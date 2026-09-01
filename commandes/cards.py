"""Cards collection: /cardsetup (admin), /roll, /collection, /card.

The bot owner has infinite rolls (cooldown skipped).
"""
from __future__ import annotations

import datetime as _dt
import os
import time as _time
import discord
from discord import app_commands

from database import (
    card_count_total, card_roll_random, card_get_by_name,
    card_owners_count, card_owners_list,
    user_card_add, user_card_list, user_card_count,
    guild_card_config_get, guild_card_config_set,
    user_card_count_owned, user_card_transfer_one,
    card_trade_create, card_trade_get, card_trade_items, card_trade_set_status,
    card_suggestion_add,
    ESSENCE_REWARDS,
    CARD_ELEMENT_LABELS as _ELEM_LABELS,
)

from services.i18n import t, ti, locale_of, guild_locale, DEFAULT_LOCALE, universe_label
from services.ui_v2 import Panel, row


# Reliable repo root. __file__ of this module resolves to a wrong cwd on the VPS
# (loaded at top-level). Modules under services/ resolve their path correctly
# -> we reuse that one as the reliable reference.
from services.card_render import _ROOT as _REPO_ROOT

ROLL_COOLDOWN_SECONDS = 3600  # 1h, per server

# Rarity / border palettes. Components V2 containers are built WITHOUT an accent
# colour (deliberate product choice), so these are no longer used for messages;
# they stay as the reference palette for the renderers and the dashboard.
RARITY_COLORS = {
    "common":    0x9aa0a6,  # grey
    "rare":      0x4cb5f9,  # blue
    "epic":      0xa86dff,  # purple
    "legendary": 0xffa726,  # orange
    "mythic":    0xff3d57,  # red
    "secret":    0x1c1c1e,  # deep black (lets the rainbow border shine)
}
# Colour per border (matches each cosmetic's visual)
BORDER_COLORS = {
    "gold":  0xFFC83D,  # gold
    "leaf":  0x6AB04C,  # leaf green
    "frost": 0x4FC3F7,  # frost cyan
    "hell":  0xE7402B,  # hell red
    "void":  0x8E44AD,  # void purple
}

RARITY_EMOJIS = {
    "common":    "⚪",
    "rare":      "🔵",
    "epic":      "🟣",
    "legendary": "🟠",
    "mythic":    "🔴",
    "secret":    "🌈",  # unicode fallback if the custom 'rainbow' emoji is unavailable
}

# Rarity -> custom Discord emoji name for the panel THUMBNAIL
_RARITY_CUSTOM_NAME = {
    "common":    "commun",
    "rare":      "rare",
    "epic":      "epic",
    "legendary": "legendaire",
    "mythic":    "mythic",
    "secret":    "secret",  # custom thumbnail badge emoji (support server)
}

# Rarity -> INLINE custom Discord emoji name (card title)
_RARITY_INLINE_EMOJI_NAME = {
    "secret":    "rainbowsphere",
}

# Shared slash-command choices. The `value` of each universe is the exact string
# stored in the `cards.universe` DB column: it MUST NOT be translated. Only the
# displayed `name` is localizable (Discord requires static choice names, so they
# stay in English).
_UNIVERSE_CHOICES = [
    app_commands.Choice(name="Anime / Manga", value="Anime"),
    app_commands.Choice(name="Video game", value="Jeu Vid\u00e9o"),
    app_commands.Choice(name="Movie / Series", value="Film/S\u00e9rie"),
    app_commands.Choice(name="Comics", value="Comics"),
    app_commands.Choice(name="Other", value="Autre"),
]
# Rarity keys are DB values too (common/rare/epic/legendary/mythic/secret).
_RARITY_CHOICES = [
    app_commands.Choice(name="⚪ Common", value="common"),
    app_commands.Choice(name="🔵 Rare", value="rare"),
    app_commands.Choice(name="🟣 Epic", value="epic"),
    app_commands.Choice(name="🟠 Legendary", value="legendary"),
    app_commands.Choice(name="🔴 Mythic", value="mythic"),
]
_RARITY_CHOICES_WITH_SECRET = _RARITY_CHOICES + [
    app_commands.Choice(name="🌈 Secret", value="secret"),
]
_rarity_emoji_cache: dict[str, str] = {}

def _get_inline_emoji_str(bot, emoji_name: str) -> str:
    """Look up a custom emoji by name across every guild, returns the string
    '<:name:id>' or '<a:name:id>' usable inline. '' if not found."""
    if not emoji_name:
        return ""
    try:
        for e in bot.emojis:
            if e.name.lower() == emoji_name.lower():
                return str(e)
    except Exception:
        pass
    return ""


# In-memory cache of card names for autocompletes: avoids hitting the DB on
# every keystroke (20k+ cards -> freeze/timeout = "failed to load options").
_CARD_NAMES_CACHE = {"all": [], "obtainable": [], "universes": [], "ts": 0.0}


def _card_cache_refresh(force=False):
    """Reload the cache from the DB. Called by a BACKGROUND warmer
    (never from an autocomplete -> no DB query on the loop/interaction)."""
    import time as _t
    now = _t.time()
    if not force and _CARD_NAMES_CACHE["all"] and (now - _CARD_NAMES_CACHE["ts"] < 120):
        return
    try:
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute("SELECT name, universe, COALESCE(not_obtainable,0) AS no "
                         "FROM cards WHERE name IS NOT NULL ORDER BY name").fetchall()
        conn.close()
        _CARD_NAMES_CACHE["all"] = [r["name"] for r in rows]
        _CARD_NAMES_CACHE["obtainable"] = [r["name"] for r in rows if not r["no"]]
        _CARD_NAMES_CACHE["universes"] = sorted(
            {(r["universe"] or "").strip() for r in rows if (r["universe"] or "").strip()})
        _CARD_NAMES_CACHE["ts"] = now
    except Exception as e:
        print(f"[cards cache] refresh err: {e}")


# PURE RAM read (zero DB, zero await): safe to use inside an autocomplete.
def _card_names_cached(obtainable_only=False):
    return _CARD_NAMES_CACHE["obtainable"] if obtainable_only else _CARD_NAMES_CACHE["all"]


def _card_universes_cached():
    return _CARD_NAMES_CACHE["universes"]


def _names_to_choices(names, current, limit=25):
    """Build VALID Choices (an empty/None name makes Discord reject the WHOLE
    autocomplete response -> 'failed to load options')."""
    q = (current or "").strip().lower()
    out = []
    seen = set()
    for n in names or []:
        if not n:
            continue
        s = str(n).strip()
        if not s:
            continue
        if q and q not in s.lower():
            continue
        s = s[:100]
        if s in seen:
            continue
        seen.add(s)
        out.append(app_commands.Choice(name=s, value=s))
        if len(out) >= limit:
            break
    return out


def _get_rarity_title_emoji(bot, rarity: str) -> str:
    """For secret: inline custom 'rainbow' emoji. Otherwise: default unicode."""
    inline_name = _RARITY_INLINE_EMOJI_NAME.get(rarity)
    if inline_name:
        s = _get_inline_emoji_str(bot, inline_name)
        if s:
            return s
    return RARITY_EMOJIS.get(rarity, "⚪")


def _get_element_emoji(bot, element: str) -> str:
    """Support custom emoji for the element (by name), unicode fallback otherwise."""
    from database import CARD_ELEMENT_EMOJI, CARD_ELEMENT_EMOJI_NAME
    if not element:
        return ""
    name = CARD_ELEMENT_EMOJI_NAME.get(element)
    if name:
        s = _get_inline_emoji_str(bot, name)
        if s:
            return s
    return CARD_ELEMENT_EMOJI.get(element, "")


def _golden_emoji(bot) -> str:
    """Custom 'goldenroll' emoji (by name), unicode rainbow fallback otherwise."""
    s = _get_inline_emoji_str(bot, "goldenroll")
    return s or "🌈"


def _roll_emoji(bot) -> str:
    """Custom 'roll' emoji (support server, by name), ticket fallback otherwise."""
    s = _get_inline_emoji_str(bot, "roll")
    return s or "🎟️"


def _epic_roll_emoji(bot) -> str:
    """Custom 'epicroll' emoji (by name), purple fallback otherwise."""
    s = _get_inline_emoji_str(bot, "epicroll")
    return s or "🟣"


def _power_emoji_str(bot, n) -> str:
    """Number -> custom digit emojis from the SUPPORT SERVER (names '0_'..'9_', 'm').
    Compact format >=1M: 'XmYYY' (e.g. 1345986 -> 1m345). Lookup limited to the
    support server (short names collide). Fallback: unicode digits."""
    sg = int((os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip() or 0)
    guild = bot.get_guild(sg) if sg else None
    by_name = {}
    if guild:
        for e in guild.emojis:
            by_name[e.name.lower()] = str(e)
    n = int(n)
    s = f"{n // 1_000_000}m{(n // 1000) % 1000:03d}" if n >= 1_000_000 else str(n)
    out = []
    for ch in s:
        if ch == "m":
            out.append(by_name.get("m_", "M"))
        elif ch.isdigit():
            out.append(by_name.get(f"{ch}_", ch))
        else:
            out.append(ch)
    return "".join(out)


def _get_rarity_custom_emoji_url(bot, rarity: str) -> str:
    """Look up a custom emoji across every guild of the bot (support server included).
    Caches the CDN URL (gif if animated, png otherwise). For panel thumbnail use."""
    if rarity in _rarity_emoji_cache:
        return _rarity_emoji_cache[rarity]
    expected = _RARITY_CUSTOM_NAME.get(rarity)
    if not expected:
        _rarity_emoji_cache[rarity] = ""
        return ""
    try:
        for e in bot.emojis:
            if e.name.lower() == expected.lower():
                url = str(e.url)
                _rarity_emoji_cache[rarity] = url
                return url
    except Exception:
        pass
    return ""


def _is_owner(user_id: int | str) -> bool:
    owner = (os.getenv("DISCORD_OWNER_ID") or "").strip()
    return owner and str(user_id) == owner


SUPPORT_INVITE_URL = os.getenv("SUPPORT_INVITE_URL", "https://discord.gg/hx4KEFSGJA")


def _support_button(locale=None, label=None):
    """Link button to the support server (roll/wishlist perks)."""
    return discord.ui.Button(label=(label or t("cards.support.join_button", locale)),
                             style=discord.ButtonStyle.link,
                             url=SUPPORT_INVITE_URL)


def _support_view(locale=None, label=None):
    """Classic view holding the support link button. Only used on plain-text
    messages (no panel), which may still carry `content=`."""
    v = discord.ui.View()
    v.add_item(_support_button(locale, label))
    return v


def _is_support_member(bot, user_id) -> bool:
    """True if the user is a member of the support server (perks: roll x2, wishlist 6)."""
    try:
        sg = int((os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip() or 0)
        if not sg:
            return False
        guild = bot.get_guild(sg)
        if not guild:
            return False
        return guild.get_member(int(user_id)) is not None
    except Exception:
        return False


def _resolve_card_image(card: dict):
    """Return (http_url_or_None, discord.File_or_None) for Panel.image().

    LOCAL RENDER FIRST: we NEVER hotlink an external host as long as a local
    render exists (anti dead links). Order:
      1. local render static/card_renders/<id>.(webp|png) -> served through the
         domain (PUBLIC_BASE_URL), otherwise as an attachment.
      2. image_url already pointing at an existing local /static/ file.
      3. last resort: remote http image_url (can die, only when no local render
         exists).
    """
    root = _REPO_ROOT
    cid = card.get("id")
    img = card.get("image_url") or ""
    public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

    # 1+2. Find a local render file
    local_rel = None
    local_path = None
    if cid:
        for ext in (".webp", ".png"):
            p = os.path.join(root, "static", "card_renders", f"{cid}{ext}")
            if os.path.exists(p):
                local_rel = f"/static/card_renders/{cid}{ext}"; local_path = p
                break
    if local_rel is None and isinstance(img, str) and "/static/" in img:
        rel = "/static/" + img.split("/static/", 1)[1].split("?")[0]
        p = os.path.join(root, rel.lstrip("/").replace("/", os.sep))
        if os.path.exists(p):
            local_rel = rel; local_path = p

    if local_rel:
        if public_base:
            # Served by your domain: Discord proxies it, never an external host.
            # Cache-bust by mtime: when the render changes (approved re-crop),
            # the URL changes -> Discord re-downloads instead of serving the old cache.
            try:
                ver = int(os.path.getmtime(local_path))
                return (f"{public_base}{local_rel}?v={ver}", None)
            except Exception:
                return (public_base + local_rel, None)
        # No public domain (dev): attachment. Re-encoded to PNG to match the
        # 'attachment://card.png' expected by callers.
        try:
            import io as _io
            from PIL import Image as _PImg
            buf = _io.BytesIO()
            _PImg.open(local_path).convert("RGB").save(buf, "PNG")
            buf.seek(0)
            return (None, discord.File(buf, filename="card.png"))
        except Exception:
            pass

    # 3. Last resort: remote
    if isinstance(img, str) and img.startswith("http"):
        return (img, None)
    return (None, None)


# ===== Trade card viewer (paginated navigation, PERSISTENT) =====
# Persistent view: fixed custom_ids + state (trade_id, index) re-read from the
# message text on every click -> survives pm2 restarts and timeouts (no more
# "interaction failed" when going back).
import re as _re_trade


from services.ui_v2 import message_text as _v2_message_text
def _trade_card_entries(trade_id, sender_name, receiver_name, locale=None):
    """Flat list of the trade cards: offers then requests."""
    from database import card_trade_items
    entries = []
    for side, items in (("offer", card_trade_items(trade_id, side="offer")),
                        ("request", card_trade_items(trade_id, side="request"))):
        for it in items:
            side_lbl = (t("cards.trade.offered_by", locale, name=sender_name) if side == "offer"
                        else t("cards.trade.requested_from", locale, name=receiver_name))
            url, _f = _resolve_card_image({"id": it["card_id"], "image_url": it.get("image_url")})
            entries.append({
                "name": it["name"], "rarity": it.get("rarity"),
                "universe": it.get("universe") or it.get("subtitle") or "?",
                "qty": int(it.get("qty", 1) or 1), "side": side_lbl, "url": url,
            })
    return entries


def _trade_card_panel(trade_id, idx, entries, locale=None):
    if not entries:
        return None
    idx = idx % len(entries)
    e = entries[idx]
    emoji = RARITY_EMOJIS.get(e["rarity"], "⚪")
    qty = f" ×{e['qty']}" if e["qty"] > 1 else ""
    p = Panel(
        f"{emoji} {e['name']}{qty}"[:256],
        t("cards.trade.card_desc", locale, side=e["side"],
          rarity=(e["rarity"] or "?").upper(), universe=e["universe"]))
    if e["url"]:
        p.image(e["url"])
    # footer = persistent state (parsed on the next click). Any translation MUST
    # keep the "Trade #<id>" marker and the "<index>/<total>" counter.
    p.footer(t("cards.trade.card_footer", locale, trade_id=trade_id,
               index=idx + 1, total=len(entries)))
    return p


def _trade_entries_for(interaction, trade_id):
    """Rebuild the entries, resolving nicknames from the trade record."""
    from database import card_trade_get
    trade = card_trade_get(trade_id)
    if not trade:
        return []
    loc = locale_of(interaction)
    g = interaction.guild
    sender = g.get_member(int(trade["sender_id"])) if g and trade.get("sender_id") else None
    receiver = g.get_member(int(trade["receiver_id"])) if g and trade.get("receiver_id") else None
    sname = sender.display_name if sender else t("cards.trade.the_sender", loc)
    rname = receiver.display_name if receiver else t("cards.trade.the_recipient", loc)
    return _trade_card_entries(trade_id, sname, rname, locale=loc)


async def _trade_card_nav(interaction: discord.Interaction, direction: int):
    text = _v2_message_text(interaction.message)
    # Locale-tolerant: only the trade id and the counter are required.
    m = _re_trade.search(r"Trade #(\d+)\D+(\d+)\s*/\s*(\d+)", text)
    if not m:
        await interaction.response.defer(); return
    tid = int(m.group(1)); cur = int(m.group(2)) - 1
    entries = _trade_entries_for(interaction, tid)
    if not entries:
        await interaction.response.send_message(
            ti(interaction, "cards.trade.no_cards_left"), ephemeral=True); return
    new_idx = (cur + direction) % len(entries)
    panel = _trade_card_panel(tid, new_idx, entries, locale=locale_of(interaction))
    # content/embeds cleared: a V2 message cannot carry either, and a pre-V2
    # message still has them (they would make the edit fail).
    await interaction.response.edit_message(
        content=None, embeds=[], view=TradeCardsNavView(panel))


class _TradeCardsNavRow(discord.ui.ActionRow):
    """Navigation row. custom_ids kept identical to the pre-V2 version."""

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="trade_card_prev")
    async def prev_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await _trade_card_nav(interaction, -1)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="trade_card_next")
    async def next_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await _trade_card_nav(interaction, +1)


class TradeCardsNavView(discord.ui.LayoutView):
    """Persistent (timeout=None, fixed custom_ids). A single instance registered
    at boot via bot.add_view handles ALL trade viewers."""
    def __init__(self, panel=None):
        super().__init__(timeout=None)
        self.add_item((panel or Panel()).container())
        self.add_item(_TradeCardsNavRow())


def build_roll_view(bot, card, roller_name, roller_avatar_url=None,
                    essence_gain=0, already_owned=False, locale=None):
    """Build the panel of a roll (same format as /roll). Reused by /roll, the
    golden roll and the dashboard simulation. Returns (layout_view, img_file)."""
    rarity = card.get("rarity", "common")
    emoji = _get_rarity_title_emoji(bot, rarity)
    origin = card.get("subtitle") or "?"
    universe = universe_label(card.get("universe"), locale) or "?"
    rarity_display = "?????" if rarity == "secret" else rarity.upper()
    flavor = (card.get("flavor_subtitle") or "").strip()
    essence_line = (t("cards.roll.essences_line", locale, amount=essence_gain)
                    + (t("cards.roll.duplicate_x2", locale) if already_owned else ""))
    _elem = card.get("element")
    if _elem:
        essence_line += t("cards.roll.element_line", locale,
                          emoji=_get_element_emoji(bot, _elem),
                          label=_ELEM_LABELS.get(_elem, ''))
    desc_parts = []
    if flavor:
        desc_parts.append(f"_**{flavor}**_")
    desc_parts.append(t("cards.roll.card_desc", locale, rarity=rarity_display,
                        origin=origin, universe=universe, essence_line=essence_line))
    p = Panel(f"{emoji} {card['name']}"[:256], "\n\n".join(desc_parts))
    thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
    if thumb_url:
        p.thumbnail(thumb_url)
    img_url, img_file = _resolve_card_image(card)
    if img_url:
        p.image(img_url)
    elif img_file:
        p.image("attachment://card.png")
    p.footer(t("cards.roll.belongs_to", locale, name=roller_name))
    return OwnersView(p, card["id"], card["name"], locale=locale), img_file


async def _persist_attachment(att) -> str | None:
    """Download an image attachment and store it locally in a STABLE way (Discord
    ephemeral attachment URLs EXPIRE -> unusable at approval time).
    Returns the URL (domain + /static/card_suggestions/...) or None."""
    try:
        import time as _t
        data = await att.read()
        ext = os.path.splitext(att.filename or "")[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
        name = f"submit_{int(_t.time()*1000)}{ext}"
        d = os.path.join(_REPO_ROOT, "static", "card_suggestions")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
        rel = f"/static/card_suggestions/{name}"
        pub = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        return (pub + rel) if pub else rel
    except Exception as e:
        print(f"[persist attachment] {e}")
        return None


def _resolve_owned_card(uid, query):
    """Find a card owned by the user from a typed name (fuzzy match).
    Priority: exact > prefix > contains. Returns dict {card_id, name} or None."""
    from database import get_db
    q = (query or "").strip()
    if not q:
        return None
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT DISTINCT uc.card_id AS card_id, ca.name AS name FROM user_cards uc "
        "JOIN cards ca ON ca.id = uc.card_id WHERE uc.user_id = ? AND LOWER(ca.name) LIKE ? "
        "ORDER BY ca.name LIMIT 50",
        (str(uid), f"%{q.lower()}%")).fetchall()
    conn.close()
    if not rows:
        return None
    ql = q.lower()
    exact = [r for r in rows if r["name"].lower() == ql]
    if exact:
        return dict(exact[0])
    pref = [r for r in rows if r["name"].lower().startswith(ql)]
    if pref:
        return dict(pref[0])
    return dict(rows[0])


class _CardsModal(discord.ui.Modal, title="Your 3 featured cards"):
    """Free text entry of the 3 cards by name (fuzzy search in the inventory)."""
    def __init__(self, uid, cur_names, locale=None):
        super().__init__(title=t("cards.profile.modal_title", locale))
        self.uid = uid
        self.locale = locale
        self.left_in = discord.ui.TextInput(label=t("cards.profile.left_card", locale),
                                            required=True, max_length=100,
                                            default=cur_names.get("left") or "")
        self.mid_in = discord.ui.TextInput(label=t("cards.profile.mid_card", locale),
                                           required=True, max_length=100,
                                           default=cur_names.get("mid") or "")
        self.right_in = discord.ui.TextInput(label=t("cards.profile.right_card", locale),
                                             required=True, max_length=100,
                                             default=cur_names.get("right") or "")
        self.add_item(self.left_in); self.add_item(self.mid_in); self.add_item(self.right_in)

    async def on_submit(self, interaction):
        from database import card_profile_set
        loc = locale_of(interaction)
        slots = [(t("cards.profile.slot_left", loc), self.left_in.value),
                 (t("cards.profile.slot_mid", loc), self.mid_in.value),
                 (t("cards.profile.slot_right", loc), self.right_in.value)]
        resolved = []; missing = []
        for lbl, raw in slots:
            cd = _resolve_owned_card(self.uid, raw)
            if cd:
                resolved.append(cd)
            else:
                missing.append(f"**{lbl}** (`{(raw or '').strip()}`)")
        if missing:
            await interaction.response.send_message(
                ti(interaction, "cards.profile.cards_not_found", list=", ".join(missing)),
                ephemeral=True)
            return
        card_profile_set(self.uid, resolved[0]["card_id"], resolved[1]["card_id"], resolved[2]["card_id"])
        await interaction.response.send_message(
            ti(interaction, "cards.profile.featured_set",
               list=" · ".join(f"**{r['name']}**" for r in resolved)), ephemeral=True)


def _profile_color_name(col, locale=None):
    """Localized name of a PROFILE_COLORS entry (falls back to the DB label)."""
    key = f"cards.profile.color.{col['key']}"
    label = t(key, locale)
    return col["name"] if label == key else label


class _ProfileCustomView(discord.ui.View):
    """Profile editor: cards modal button (free choice) + color dropdown (guild tier)."""
    def __init__(self, uid, prof, guild_level, cur_names, locale=None):
        super().__init__(timeout=300)
        self.uid = uid; self.guild_level = guild_level; self.cur_names = cur_names
        self.locale = locale
        self.color = (prof or {}).get("color")
        self.pick_cards.label = t("cards.profile.pick_cards_btn", locale)
        self.add_item(self._ColorSelect(self))

    class _ColorSelect(discord.ui.Select):
        def __init__(self, parent):
            from database import PROFILE_COLORS
            self.pv = parent
            opts = []
            for col in PROFILE_COLORS:
                locked = parent.guild_level < col["lvl"]
                name = _profile_color_name(col, parent.locale)
                if locked:
                    name += t("cards.profile.color_locked_suffix", parent.locale, level=col["lvl"])
                opts.append(discord.SelectOption(label=name[:100], value=col["key"],
                                                 default=(col["key"] == parent.color)))
            super().__init__(placeholder=t("cards.profile.color_placeholder", parent.locale),
                             options=opts, min_values=1, max_values=1)

        async def callback(self, interaction):
            from database import PROFILE_COLORS, card_profile_set_color
            if interaction.user.id != self.pv.uid:
                await interaction.response.send_message(
                    ti(interaction, "cards.profile.not_yours"), ephemeral=True); return
            key = self.values[0]
            col = next((c for c in PROFILE_COLORS if c["key"] == key), None)
            loc = locale_of(interaction)
            if col and self.pv.guild_level < col["lvl"]:
                await interaction.response.send_message(
                    t("cards.profile.color_locked", loc,
                      color=_profile_color_name(col, loc), level=col["lvl"]),
                    ephemeral=True); return
            card_profile_set_color(self.pv.uid, key)
            self.pv.color = key
            await interaction.response.send_message(
                t("cards.profile.color_applied", loc, color=_profile_color_name(col, loc)),
                ephemeral=True)

    @discord.ui.button(label="Pick my 3 cards", emoji="🎴",
                       style=discord.ButtonStyle.success, row=1)
    async def pick_cards(self, interaction, btn):
        if interaction.user.id != self.uid:
            await interaction.response.send_message(
                ti(interaction, "cards.profile.not_yours"), ephemeral=True); return
        await interaction.response.send_modal(
            _CardsModal(self.uid, self.cur_names, locale=locale_of(interaction)))


async def _open_profile_customizer(bot, interaction):
    uid = interaction.user.id
    from database import get_db, card_profile_get, guild_of_user
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ?", (str(uid),)).fetchone()["n"]
    if not n:
        conn.close()
        await interaction.response.send_message(
            ti(interaction, "cards.profile.no_cards"), ephemeral=True)
        return
    prof = card_profile_get(uid) or {}
    # Current names (to pre-fill the modal)
    cur_names = {}
    for key, col in (("left", "left_id"), ("mid", "mid_id"), ("right", "right_id")):
        cid = prof.get(col)
        if cid:
            r = c.execute("SELECT name FROM cards WHERE id = ?", (cid,)).fetchone()
            if r:
                cur_names[key] = r["name"]
    conn.close()
    glvl = (guild_of_user(uid) or {}).get("level", 0)
    loc = locale_of(interaction)
    view = _ProfileCustomView(uid, prof, glvl, cur_names, locale=loc)
    await interaction.response.send_message(
        t("cards.profile.customizer_intro", loc), view=view, ephemeral=True)


def _check_channel(interaction: discord.Interaction) -> tuple[bool, str | None]:
    """Check that the command is run in the configured channel.
    Returns (ok, channel_mention_if_ko)."""
    cfg = guild_card_config_get(interaction.guild.id) if interaction.guild else None
    if not cfg or not cfg.get("channel_id"):
        return (True, None)
    if str(interaction.channel.id) != str(cfg["channel_id"]):
        return (False, f"<#{cfg['channel_id']}>")
    return (True, None)


_DASHBOARD_URL = (os.getenv("DASHBOARD_URL") or "https://dashboard.tookbot.click").rstrip("/")


class _OwnersRow(discord.ui.ActionRow):
    """'View owners' + 'Edit' buttons under a card panel."""
    def __init__(self, card_id: int, card_name: str, locale=None):
        super().__init__()
        self.card_id = card_id
        self.card_name = card_name
        self._show_owners.label = t("cards.owners.btn_view", locale)
        # 'Edit' link button -> dashboard /cards?edit=<id>
        edit_url = f"{_DASHBOARD_URL}/cards?edit={card_id}"
        self.add_item(discord.ui.Button(
            label=t("cards.owners.btn_edit", locale), style=discord.ButtonStyle.link,
            emoji="✏", url=edit_url,
        ))

    @discord.ui.button(label="View owners", style=discord.ButtonStyle.secondary,
                        emoji="👥")
    async def _show_owners(self, interaction: discord.Interaction, button: discord.ui.Button):
        owners = card_owners_list(self.card_id, limit=50)
        if not owners:
            await interaction.response.send_message(
                ti(interaction, "cards.owners.nobody"), ephemeral=True)
            return
        lines = []
        for o in owners:
            uid = o["user_id"]
            qty = o["qty"]
            suffix = f" ×{qty}" if qty > 1 else ""
            lines.append(f"<@{uid}>{suffix}")
        p = Panel(ti(interaction, "cards.owners.title", name=self.card_name),
                  "\n".join(lines)[:4000])
        if len(owners) >= 50:
            p.footer(ti(interaction, "cards.owners.footer_limit"))
        await interaction.response.send_message(
            view=p.view(), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class OwnersView(discord.ui.LayoutView):
    """Card panel + the 'View owners' / 'Edit' buttons."""
    def __init__(self, panel: Panel, card_id: int, card_name: str, locale=None):
        super().__init__(timeout=600)
        self.add_item(panel.container())
        self.add_item(_OwnersRow(card_id, card_name, locale=locale))


def setup_cards_commands(bot, deps):
    globals().update(deps)

    # Persistent view of the trade card viewer: survives pm2 restarts.
    try:
        bot.add_view(TradeCardsNavView())
    except Exception as _e:
        print(f"[cards] add_view TradeCardsNavView: {_e}")

    # Warmer for the names/universes cache (background thread): the autocomplete
    # never reads the DB again -> no loop blocking and no "failed to load options".
    from discord.ext import tasks as _tasks

    @_tasks.loop(seconds=120)
    async def _cards_cache_loop():
        import asyncio as _aio
        try:
            await _aio.to_thread(_card_cache_refresh, True)
        except Exception as e:
            print(f"[cards cache] loop err: {e}")

    @_cards_cache_loop.before_loop
    async def _before_cards_cache():
        await bot.wait_until_ready()

    # Start the loop once the async loop is running (setup runs before the loop).
    @bot.listen("on_ready")
    async def _start_cards_cache():
        try:
            if not _cards_cache_loop.is_running():
                _cards_cache_loop.start()
        except Exception as e:
            print(f"[cards cache] start err: {e}")

    # === /cardsetup admin (top-level alias for clarity) ===
    async def _resolve_or_create_role(interaction, role_str):
        """Resolve a role from text: mention <@&id>, id, or exact name.
        If no role matches the name, create one. Returns (role, created)
        or (None, False) on creation failure."""
        guild = interaction.guild
        raw = (role_str or "").strip()
        if not raw:
            return (None, False)
        # mention <@&123> or raw id
        digits = "".join(ch for ch in raw if ch.isdigit())
        if (raw.startswith("<@&") or raw.isdigit()) and digits:
            r = guild.get_role(int(digits))
            if r:
                return (r, False)
        # exact name (case insensitive)
        low = raw.lower()
        for r in guild.roles:
            if r.name.lower() == low:
                return (r, False)
        # not found -> create a role with that name
        try:
            r = await guild.create_role(name=raw, mentionable=True,
                                        reason="cardsetup: card fans role")
            return (r, True)
        except Exception as e:
            print(f"[cardsetup] create_role err: {e}")
            return (None, False)

    @bot.tree.command(name="cardsetup", description="Set the cards channel + role to ping on drops/bosses (admin)")
    @app_commands.describe(
        channel="Text channel the card commands will be limited to",
        role="(Optional) Role to ping (mention, or a name: creates the role if it doesn't exist)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cardsetup(interaction: discord.Interaction, channel: discord.TextChannel,
                        role: str = None):
        role_obj = None
        created = False
        role_err = False
        if role and role.strip():
            role_obj, created = await _resolve_or_create_role(interaction, role)
            role_err = role_obj is None
        guild_card_config_set(interaction.guild.id, channel_id=channel.id, enabled=True,
                              ping_role_id=(role_obj.id if role_obj else ...))
        msg = ti(interaction, "cards.setup.channel_set", channel=channel.mention)
        if role_obj:
            key = "cards.setup.role_created_set" if created else "cards.setup.role_set"
            msg += ti(interaction, key, role=role_obj.mention)
        elif role_err:
            msg += ti(interaction, "cards.setup.role_error")
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="cardsetup_disable", description="Disable the cards channel restriction (admin)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cardsetup_disable(interaction: discord.Interaction):
        guild_card_config_set(interaction.guild.id, channel_id=None, enabled=True)
        await interaction.response.send_message(
            ti(interaction, "cards.setup.disabled"), ephemeral=True,
        )

    # === /roll [universe] ===
    @bot.tree.command(name="roll",
                       description="Pull a random card (optional: filter by universe)")
    @app_commands.describe(universe="Filter by category (all of them otherwise)")
    async def roll(interaction: discord.Interaction, universe: str = None):
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    ti(interaction, "cards.channel.restricted", channel=target),
                    ephemeral=True,
                )
                return

        # Anti-abuse: /roll blocked in servers that are too RECENT. The server age
        # comes from the Discord snowflake (guild.created_at), which can't be faked.
        # Kills farming through mass throwaway server creation. Owner + support
        # server are exempt.
        if interaction.guild and not _is_owner(interaction.user.id):
            try:
                from database import get_setting
                _min_days = int(os.getenv("ROLL_MIN_GUILD_AGE_DAYS")
                                or get_setting("roll_min_guild_age_days", "7"))
            except (ValueError, TypeError):
                _min_days = 7
            _sg = int((os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip() or 0)
            if _min_days > 0 and interaction.guild.id != _sg:
                age = _dt.datetime.now(_dt.timezone.utc) - interaction.guild.created_at
                if age < _dt.timedelta(days=_min_days):
                    ready_at = int((interaction.guild.created_at
                                    + _dt.timedelta(days=_min_days)).timestamp())
                    await interaction.response.send_message(
                        ti(interaction, "cards.roll.guild_too_young",
                           days=_min_days, ready_at=ready_at),
                        ephemeral=True)
                    return

            # Anti-abuse #2: cap on "solo" servers (user alone with the bot, only
            # bots besides them). An account can only roll in N solo servers max.
            if interaction.guild.id != _sg:
                try:
                    from database import (get_setting, roll_solo_guild_has,
                                          roll_solo_guild_count, roll_solo_guild_add)
                    _max_solo = int(get_setting("roll_max_solo_guilds", "2"))
                except (ValueError, TypeError):
                    _max_solo = 2
                if _max_solo > 0:
                    humans = sum(1 for m in interaction.guild.members if not m.bot)
                    if humans <= 1:  # solo server: just the roller (+ some bots)
                        uid_s = interaction.user.id
                        gid_s = interaction.guild.id
                        if not roll_solo_guild_has(uid_s, gid_s):
                            if roll_solo_guild_count(uid_s) >= _max_solo:
                                await interaction.response.send_message(
                                    ti(interaction, "cards.roll.solo_guild_cap", max=_max_solo),
                                    ephemeral=True)
                                return
                            roll_solo_guild_add(uid_s, gid_s)

        # PER-SERVER cooldown (one timer per guild) - skipped for the owner.
        # Support server members: 2 charges/h. Others: 1/h. Each charge recharges
        # 1h after ITS own use. Bonus rolls (granted by the owner) are consumed
        # first and never recharge.
        from database import (roll_bonus_available, roll_bonus_consume,
                               roll_events_count, roll_events_oldest_ts, roll_events_add)
        uid = interaction.user.id
        gid = interaction.guild.id if interaction.guild else None
        is_support = _is_support_member(bot, uid)
        # Guild passives (level tier): +charges/h, -roll cooldown
        from database import guild_perks_for_user
        _gperk = guild_perks_for_user(uid) or {}
        max_charges = (2 if is_support else 1) + int(_gperk.get("charges", 0))
        _cd_window = max(60, 3600 - int(_gperk.get("roll_cd_min", 0)) * 60)
        use_bonus = False
        if not _is_owner(uid) and gid:
            if roll_bonus_available(uid) > 0:
                use_bonus = True
            else:
                recent = roll_events_count(uid, gid, _cd_window)
                if recent >= max_charges:
                    now_ts = _time.time()
                    oldest = roll_events_oldest_ts(uid, gid, _cd_window)
                    remain = (_cd_window - (now_ts - oldest)) if oldest else _cd_window
                    if remain < 0:
                        remain = 0
                    ready_at = int(now_ts + remain)
                    if is_support:
                        await interaction.response.send_message(
                            ti(interaction, "cards.roll.cooldown_support", ready_at=ready_at),
                            ephemeral=True)
                    else:
                        await interaction.response.send_message(
                            ti(interaction, "cards.roll.cooldown", ready_at=ready_at),
                            view=_support_view(locale_of(interaction)), ephemeral=True)
                    return

        # Make sure there are cards
        if card_count_total() == 0:
            await interaction.response.send_message(
                ti(interaction, "cards.roll.catalog_empty"), ephemeral=True,
            )
            return

        # Draw + add (with universe filter when provided)
        universe_filter = (universe or "").strip() or None
        # Owner cheat: forced card. Applied ONLY if it matches the roll's universe
        # filter (otherwise normal roll, and the forced card stays pending).
        from database import forced_roll_get, forced_roll_clear, card_get
        card = None
        _forced_id = forced_roll_get(uid)
        if _forced_id:
            fc = card_get(_forced_id)
            if fc and (not universe_filter
                       or (fc.get("universe") or "").lower() == universe_filter.lower()):
                card = fc
                forced_roll_clear(uid)   # consumed only when it matches
        if not card:
            card = card_roll_random(universe=universe_filter, user_id=uid, guild_id=gid)
        if not card:
            label = (ti(interaction, "cards.roll.no_card_universe_suffix",
                        universe=universe_filter) if universe_filter else "")
            await interaction.response.send_message(
                ti(interaction, "cards.roll.no_card_available", universe=label), ephemeral=True)
            return
        # Duplicate? (before adding) -> essences x2
        already_owned = user_card_count_owned(uid, card["id"]) > 0
        user_card_add(uid, card["id"])
        bonus_left = None
        if not _is_owner(uid) and gid:
            if use_bonus:
                roll_bonus_consume(uid)
                bonus_left = roll_bonus_available(uid)
            else:
                roll_events_add(uid, gid)

        # Essence gain based on rarity (duplicate = x2)
        rarity_for_reward = card.get("rarity", "common")
        essence_base = ESSENCE_REWARDS.get(rarity_for_reward, 12)
        essence_gain = essence_base * 2 if already_owned else essence_base
        # Guild passive bonus on essences
        _ess_pct = int(_gperk.get("essence_pct", 0))
        if _ess_pct:
            essence_gain = int(essence_gain * (1 + _ess_pct / 100))
        try:
            from database import essence_reward_add
            essence_gain = essence_reward_add(uid, essence_gain)  # applies the daily wheel bonus
        except Exception as e:
            print(f"[roll essence] err: {e}")
        # Guild XP hook (roll = main lever), capped per day/member
        try:
            from database import get_guild_config, guild_member_action_xp, guild_quest_progress
            _xpr = int(get_guild_config().get("xp", {}).get("roll", 0))
            if _xpr:
                guild_member_action_xp(uid, _xpr, source="roll")
            guild_quest_progress(uid, "roll", 1)
        except Exception as e:
            print(f"[roll guild xp] err: {e}")
        try:
            from database import roll_total_inc
            roll_total_inc(uid, 1)
        except Exception as e:
            print(f"[roll total] err: {e}")

        # Minimalist panel
        loc = locale_of(interaction)
        rarity = card.get("rarity", "common")
        emoji = _get_rarity_title_emoji(bot, rarity)
        origin = card.get("subtitle") or "?"
        universe_name = universe_label(card.get("universe"), loc) or "?"
        rarity_display = "?????" if rarity == "secret" else rarity.upper()
        flavor = (card.get("flavor_subtitle") or "").strip()
        essence_line = (t("cards.roll.essences_line", loc, amount=essence_gain)
                        + (t("cards.roll.duplicate_x2", loc) if already_owned else ""))
        _elem = card.get("element")
        if _elem:
            essence_line += t("cards.roll.element_line", loc,
                              emoji=_get_element_emoji(bot, _elem),
                              label=_ELEM_LABELS.get(_elem, ''))
        if bonus_left is not None:
            essence_line += t("cards.roll.bonus_roll_used", loc,
                              emoji=_roll_emoji(bot), left=bonus_left)
        desc_parts = []
        if flavor:
            desc_parts.append(f"_**{flavor}**_")
        desc_parts.append(t("cards.roll.card_desc", loc, rarity=rarity_display,
                            origin=origin, universe=universe_name,
                            essence_line=essence_line))
        desc = "\n\n".join(desc_parts)
        p = Panel(f"{emoji} {card['name']}"[:256], desc)
        # Thumbnail = animated custom emoji (rarity) when available
        thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
        if thumb_url:
            p.thumbnail(thumb_url)
        img_url, img_file = _resolve_card_image(card)
        if img_url:
            p.image(img_url)
        elif img_file:
            p.image("attachment://card.png")
        p.footer(t("cards.roll.belongs_to", loc, name=interaction.user.display_name))
        view = OwnersView(p, card["id"], card["name"], locale=loc)
        if img_file:
            await interaction.response.send_message(view=view, file=img_file)
        else:
            await interaction.response.send_message(view=view)

        # Wishlist notification: ping the people who want this card (roller excluded)
        try:
            from database import wishlist_users_for_card
            wishers = wishlist_users_for_card(card["id"], exclude_user=uid)
            if wishers and interaction.guild:
                mentions = []
                for wid in wishers[:50]:
                    m = interaction.guild.get_member(int(wid))
                    if m:
                        mentions.append(m.mention)
                if mentions:
                    # mentions in a spoiler: still pings, but no huge block
                    await interaction.channel.send(
                        t("cards.roll.wishlist_ping", loc, mentions=" ".join(mentions),
                          roller=interaction.user.display_name, card=card["name"]))
        except Exception as e:
            print(f"[wishlist notif] {e}")

    @roll.autocomplete("universe")
    async def roll_univers_autocomplete(interaction: discord.Interaction, current: str):
        try:
            return _names_to_choices(_card_universes_cached(), current)
        except Exception as e:
            print(f"[roll univers ac] {type(e).__name__}: {e}")
            return []

    # === /collection ===
    @bot.tree.command(name="cardcollec", description="View your card collection (or someone else's)")
    @app_commands.describe(member="Member whose collection to view (default: you)")
    async def collection(interaction: discord.Interaction,
                          member: discord.Member = None):
        loc = locale_of(interaction)
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    t("cards.channel.restricted_short", loc, channel=target),
                    ephemeral=True,
                )
                return
        target_user = member or interaction.user
        rar_val = None
        cat_val = None
        from database import (user_card_customizations_map, user_card_fusion_map,
                               user_collection_origins, all_card_origins)
        custom_map = user_card_customizations_map(target_user.id)
        fusion_map = user_card_fusion_map(target_user.id)
        total = user_card_count(target_user.id)
        owner_id = interaction.user.id
        PAGE_SIZE = 25

        _RARITY_RANK = {"common": 0, "rare": 1, "epic": 2, "legendary": 3, "mythic": 4, "secret": 5}
        # Internal sort ids (never displayed as-is, see cards.collection.sort.*)
        _SORT_CYCLE = [None, "name", "rarity", "stars"]

        def _sort_btn_label(sort_mode):
            return t("cards.collection.sort." + (sort_mode or "none"), loc)

        def _grouped_rows(cat, name_q=None):
            cards = user_card_list(target_user.id, rarity=rar_val, categorie=cat)
            grouped: dict[int, dict] = {}
            for c in cards:
                cid = c["card_id"]
                if cid not in grouped:
                    grouped[cid] = {**c, "count": 0, "nt_count": 0}
                grouped[cid]["count"] += 1
                if c.get("not_tradeable"):
                    grouped[cid]["nt_count"] += 1
            rows = list(grouped.values())
            if name_q:
                ql = name_q.lower()
                rows = [r for r in rows if ql in r["name"].lower()
                        or ql in (r.get("subtitle") or "").lower()
                        or ql in (r.get("universe") or "").lower()]
            return rows

        def _sorted_rows(rows, sort_mode):
            if sort_mode == "name":
                return sorted(rows, key=lambda c: c["name"].lower())
            if sort_mode == "rarity":
                return sorted(rows, key=lambda c: -_RARITY_RANK.get(c.get("rarity", ""), 0))
            if sort_mode == "stars":
                return sorted(rows, key=lambda c: -fusion_map.get(c["card_id"], 0))
            return rows

        def _build_panel(rows, cat, page, total_pages, name_q=None, sort_mode=None):
            page_rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
            desc = t("cards.collection.count", loc, total=total, unique=len(rows))
            if rar_val:
                desc += t("cards.collection.filter_rarity", loc, rarity=rar_val)
            if cat:
                desc += t("cards.collection.filter_category", loc, category=cat)
            if name_q:
                desc += t("cards.collection.filter_search", loc, query=name_q)
            if sort_mode:
                desc += t("cards.collection.sorted_by", loc,
                          sort=t("cards.collection.sort_name." + sort_mode, loc))
            lines = []
            for c in page_rows:
                emoji = RARITY_EMOJIS.get(c["rarity"], "⚪")
                elem = _get_element_emoji(bot, c.get("element"))
                pre = f"{emoji}｜{elem}" if elem else emoji
                uni = universe_label(c.get("universe"), loc) or "?"
                fusion = fusion_map.get(c["card_id"], 0)
                cosmetic_tag = " ✨" if custom_map.get(c["card_id"]) else ""
                total_n = c["count"]
                if fusion > 0:
                    lines.append(f"{pre} **{c['name']}**{cosmetic_tag}{'⭐' * fusion} 🔒 · _{uni}_")
                    extra = total_n - 1
                    if extra > 0:
                        cnt = f" x{extra}" if extra > 1 else ""
                        lines.append(f"{pre} **{c['name']}**{cnt} · _{uni}_")
                else:
                    count = f" x{total_n}" if total_n > 1 else ""
                    nt = c.get("nt_count", 0)
                    nt_tag = f" 🔒{nt}" if nt > 0 else ""
                    lines.append(f"{pre} **{c['name']}**{cosmetic_tag}{count}{nt_tag} · _{uni}_")
            body = desc + "\n\n" + ("\n".join(lines) if lines
                                    else t("cards.collection.empty", loc))
            p = Panel(t("cards.collection.title", loc, name=target_user.display_name),
                      body)
            p.footer(t("cards.collection.footer", loc, page=page, total=total_pages))
            if target_user.display_avatar:
                p.thumbnail(str(target_user.display_avatar.url))
            return p

        class _CollecNavRow(discord.ui.ActionRow):
            @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                v = self.view
                if not await v._guard(interaction): return
                if v.page > 1:
                    v.page -= 1; v._rebuild()
                    await interaction.response.edit_message(view=v)

            @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.primary, disabled=True)
            async def counter(self, interaction: discord.Interaction, btn: discord.ui.Button):
                pass

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                v = self.view
                if not await v._guard(interaction): return
                if v.page < v.total_pages:
                    v.page += 1; v._rebuild()
                    await interaction.response.edit_message(view=v)

            @discord.ui.button(label="🔃 Sort", style=discord.ButtonStyle.secondary)
            async def trier_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                v = self.view
                if not await v._guard(interaction): return
                idx = _SORT_CYCLE.index(v.sort_mode)
                v.sort_mode = _SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)]
                v.rows = _sorted_rows(v.base_rows, v.sort_mode)
                v.page = 1
                v.total_pages = max(1, (len(v.rows) + PAGE_SIZE - 1) // PAGE_SIZE)
                v._rebuild()
                await interaction.response.edit_message(view=v)

        class _CollecToolsRow(discord.ui.ActionRow):
            @discord.ui.button(label="📚 Browse origins", style=discord.ButtonStyle.success)
            async def browse_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                v = self.view
                if not await v._guard(interaction): return
                await interaction.response.edit_message(view=_OriginsView())

            @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.secondary)
            async def search_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                v = self.view
                if not await v._guard(interaction): return
                await interaction.response.send_modal(_SearchCardModal(v.cat, v.sort_mode))

        class _CollecView(discord.ui.LayoutView):
            def __init__(self, rows, cat, name_q=None, sort_mode=None):
                super().__init__(timeout=300)
                self.base_rows = rows
                self.cat = cat
                self.name_q = name_q
                self.sort_mode = sort_mode
                self.rows = _sorted_rows(rows, sort_mode)
                self.page = 1
                self.total_pages = max(1, (len(self.rows) + PAGE_SIZE - 1) // PAGE_SIZE)
                self.nav = _CollecNavRow()
                self.tools = _CollecToolsRow()
                self.nav.prev_btn.label = t("cards.collection.btn.prev", loc)
                self.nav.next_btn.label = t("cards.collection.btn.next", loc)
                self.tools.browse_btn.label = t("cards.collection.btn.browse", loc)
                self.tools.search_btn.label = t("cards.collection.btn.search", loc)
                # Link button to the target user's dashboard binder
                _dash = os.getenv("DASHBOARD_URL", "https://dashboard.tookbot.click").rstrip("/")
                self.binder = row(discord.ui.Button(
                    label=t("cards.collection.btn.binder", loc), style=discord.ButtonStyle.link,
                    url=f"{_dash}/cards/collection/{target_user.id}"))
                self._rebuild()

            def _rebuild(self):
                """Swap the container for the current page and refresh the nav state."""
                self.clear_items()
                self.nav.prev_btn.disabled = (self.page <= 1)
                self.nav.next_btn.disabled = (self.page >= self.total_pages)
                self.nav.counter.label = f"{self.page} / {self.total_pages}"
                self.nav.trier_btn.label = _sort_btn_label(self.sort_mode)
                self.add_item(self._panel().container())
                self.add_item(self.nav)
                self.add_item(self.tools)
                self.add_item(self.binder)

            def _panel(self):
                return _build_panel(self.rows, self.cat, self.page, self.total_pages,
                                    name_q=self.name_q, sort_mode=self.sort_mode)

            async def _guard(self, interaction):
                if interaction.user.id != owner_id:
                    await interaction.response.send_message(
                        ti(interaction, "cards.collection.not_yours"), ephemeral=True)
                    return False
                return True

        def _make_collec_view(cat, name_q=None, sort_mode=None):
            rows = _grouped_rows(cat, name_q)
            return _CollecView(rows, cat, name_q=name_q, sort_mode=sort_mode)

        # Origins browser ("Browse Series" style)
        class _OriginsView(discord.ui.LayoutView):
            def __init__(self, query=None):
                super().__init__(timeout=300)
                self.query = (query or "").strip()
                all_o = all_card_origins()
                if self.query:
                    ql = self.query.lower()
                    all_o = [(o, n) for o, n in all_o if ql in o.lower()]
                self.origins = all_o
                self.owned = dict(user_collection_origins(target_user.id))
                self.page = 0
                self.per = 25
                self._rebuild()

            def build_panel(self):
                tp = max(1, (len(self.origins) + self.per - 1) // self.per)
                chunk = self.origins[self.page * self.per:(self.page + 1) * self.per]
                lines = "\n".join(
                    f"**{o}** · {self.owned.get(o, 0)}/{n}" for o, n in chunk
                ) or t("cards.collection.origins.no_results", loc)
                q_txt = (t("cards.collection.origins.query_suffix", loc, query=self.query)
                         if self.query else "")
                return Panel(
                    t("cards.collection.origins.title", loc,
                      name=target_user.display_name),
                    t("cards.collection.origins.body", loc,
                      count=len(self.origins), page=self.page + 1,
                      total=tp, query=q_txt, lines=lines),
                )

            def _rebuild(self):
                self.clear_items()
                self.add_item(self.build_panel().container())
                chunk = self.origins[self.page * self.per:(self.page + 1) * self.per]
                opts = [discord.SelectOption(
                            label=o[:100],
                            description=t("cards.collection.origins.option_desc", loc,
                                          owned=self.owned.get(o, 0), total=n))
                        for o, n in chunk]
                sel = discord.ui.Select(
                    placeholder=t("cards.collection.origins.placeholder", loc),
                    options=opts or [discord.SelectOption(label="—")])
                async def _on_select(inter: discord.Interaction):
                    if inter.user.id != owner_id:
                        await inter.response.send_message(
                            ti(inter, "cards.collection.origins.not_yours"), ephemeral=True); return
                    chosen = sel.values[0]
                    await inter.response.edit_message(view=_make_collec_view(chosen))
                sel.callback = _on_select
                self.add_item(row(sel))
                # page buttons + back
                tp = max(1, (len(self.origins) + self.per - 1) // self.per)
                prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary,
                                          disabled=self.page <= 0)
                nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary,
                                         disabled=self.page >= tp - 1)
                back = discord.ui.Button(label=t("cards.collection.origins.back", loc),
                                         style=discord.ButtonStyle.danger)
                async def _prev(i):
                    if i.user.id != owner_id:
                        await i.response.send_message(
                            ti(i, "cards.collection.origins.not_yours"), ephemeral=True); return
                    self.page -= 1; self._rebuild()
                    await i.response.edit_message(view=self)
                async def _nxt(i):
                    if i.user.id != owner_id:
                        await i.response.send_message(
                            ti(i, "cards.collection.origins.not_yours"), ephemeral=True); return
                    self.page += 1; self._rebuild()
                    await i.response.edit_message(view=self)
                async def _back(i):
                    if i.user.id != owner_id:
                        await i.response.send_message(
                            ti(i, "cards.collection.origins.not_yours"), ephemeral=True); return
                    await i.response.edit_message(view=_make_collec_view(None))
                prev.callback = _prev; nxt.callback = _nxt; back.callback = _back
                self.add_item(row(prev, nxt, back))

        class _SearchCardModal(discord.ui.Modal, title="Search for a card"):
            def __init__(self, cat, sort_mode):
                super().__init__(title=t("cards.collection.search.card_title", loc))
                self._cat = cat
                self._sort = sort_mode
                self.q = discord.ui.TextInput(
                    label=t("cards.collection.search.card_label", loc),
                    placeholder=t("cards.collection.search.card_placeholder", loc),
                    required=True, max_length=100)
                self.add_item(self.q)

            async def on_submit(self, inter: discord.Interaction):
                v = _make_collec_view(self._cat, name_q=str(self.q.value).strip(), sort_mode=self._sort)
                if not v.rows:
                    await inter.response.send_message(
                        ti(inter, "cards.collection.search.no_result", query=self.q.value),
                        ephemeral=True)
                    return
                await inter.response.edit_message(view=v)

        class _SearchOriginModal(discord.ui.Modal, title="Search for an origin"):
            def __init__(self):
                super().__init__(title=t("cards.collection.search.origin_title", loc))
                self.q = discord.ui.TextInput(
                    label=t("cards.collection.search.origin_label", loc),
                    placeholder=t("cards.collection.search.origin_placeholder", loc),
                    required=True, max_length=100)
                self.add_item(self.q)

            async def on_submit(self, inter: discord.Interaction):
                await inter.response.edit_message(view=_OriginsView(query=str(self.q.value)))

        # Initial send
        first_rows = _grouped_rows(cat_val)
        if not first_rows:
            msg = t("cards.collection.no_cards", loc, name=target_user.display_name)
            if rar_val:
                msg += t("cards.collection.no_cards_rarity", loc, rarity=rar_val)
            if cat_val:
                msg += t("cards.collection.no_cards_category", loc, category=cat_val)
            await interaction.response.send_message(msg + ".", ephemeral=True)
            return
        await interaction.response.send_message(view=_CollecView(first_rows, cat_val))


    # === /card <card> ===
    @bot.tree.command(name="card", description="View the details of a card by its name")
    @app_commands.describe(card="Card name (autocomplete)")
    async def card_cmd(interaction: discord.Interaction, card: str):
        loc = locale_of(interaction)
        try:
            if interaction.guild:
                ok, target = _check_channel(interaction)
                if not ok:
                    await interaction.response.send_message(
                        t("cards.channel.restricted_short", loc, channel=target),
                        ephemeral=True,
                    )
                    return
            data = card_get_by_name(card.strip())
            if not data:
                await interaction.response.send_message(
                    t("cards.card.not_found", loc, name=card),
                    ephemeral=True,
                )
                return
            rarity = data.get("rarity", "common")
            emoji = _get_rarity_title_emoji(bot, rarity)
            origin = data.get("subtitle") or "?"
            universe = universe_label(data.get("universe"), loc) or "?"
            rarity_display = "?????" if rarity == "secret" else rarity.upper()
            flavor = (data.get("flavor_subtitle") or "").strip()
            elem = data.get("element")
            elem_line = (t("cards.roll.element_line", loc,
                           emoji=_get_element_emoji(bot, elem),
                           label=_ELEM_LABELS.get(elem, '')) if elem else "")
            desc_parts = []
            if flavor:
                desc_parts.append(f"_**{flavor}**_")
            desc_parts.append(t("cards.card.desc", loc, rarity=rarity_display,
                                origin=origin, universe=universe,
                                element_line=elem_line))
            desc = "\n\n".join(desc_parts)
            p = Panel(f"{emoji} {data['name']}"[:256], desc)
            thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
            if thumb_url:
                p.thumbnail(thumb_url)
            img_url, img_file = _resolve_card_image(data)
            if img_url:
                p.image(img_url)
            elif img_file:
                p.image("attachment://card.png")
            owners = card_owners_count(data["id"])
            if owners > 0:
                p.footer(t("cards.card.owned_by_one", loc) if owners == 1
                         else t("cards.card.owned_by_many", loc, count=owners))
            # View always present (at least the 'Edit' link button)
            view = OwnersView(p, data["id"], data["name"], locale=loc)
            if img_file:
                await interaction.response.send_message(view=view, file=img_file)
            else:
                await interaction.response.send_message(view=view)
        except Exception:
            import traceback
            traceback.print_exc()
            err_msg = t("cards.card.display_error", loc)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(err_msg[:1900], ephemeral=True)
                else:
                    await interaction.response.send_message(err_msg[:1900], ephemeral=True)
            except Exception:
                pass

    @card_cmd.autocomplete("card")
    async def card_autocomplete(interaction: discord.Interaction, current: str):
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            if q:
                rows = c.execute(
                    "SELECT name FROM cards WHERE LOWER(name) LIKE ? "
                    "ORDER BY name LIMIT 25", (f"%{q}%",)).fetchall()
            else:
                rows = c.execute(
                    "SELECT name FROM cards ORDER BY RANDOM() LIMIT 25").fetchall()
            conn.close()
            return [app_commands.Choice(name=r["name"][:100], value=r["name"][:100])
                     for r in rows]
        except Exception:
            return []


    # === /essences: currency balance ===
    @bot.tree.command(name="essences", description="View your Essences balance ✨")
    @app_commands.describe(member="View someone else's balance (default: you)")
    async def essences_cmd(interaction: discord.Interaction, member: discord.Member = None):
        from database import currency_get
        loc = locale_of(interaction)
        target = member or interaction.user
        bal = currency_get(target.id)
        p = Panel(
            t("cards.essences.title", loc),
            t("cards.essences.balance", loc, name=target.display_name,
              amount=f"{bal:,}".replace(",", " ")),
        )
        if target.display_avatar:
            p.thumbnail(str(target.display_avatar.url))
        await interaction.response.send_message(view=p.view(), ephemeral=(member is None))


    # === /show <card>: show a card (with its custom border if applied) ===
    @bot.tree.command(name="show", description="Show one of your cards (with its custom border)")
    @app_commands.describe(card="Name of a card you own")
    async def show_cmd(interaction: discord.Interaction, card: str):
        from database import (card_get_by_name, user_card_count_owned,
                                card_customization_get, border_get, card_fusion_get)
        from services.card_render import render_user_card
        loc = locale_of(interaction)
        await interaction.response.defer()
        data = card_get_by_name(card.strip())
        if not data:
            await interaction.followup.send(
                t("cards.show.not_found", loc, name=card), ephemeral=True)
            return
        uid = interaction.user.id
        if user_card_count_owned(uid, data["id"]) <= 0 and not _is_owner(uid):
            await interaction.followup.send(
                t("cards.show.not_owned", loc, name=data['name']), ephemeral=True)
            return
        border_key = card_customization_get(uid, data["id"])
        fusion_level = card_fusion_get(uid, data["id"])
        # Title: cosmetic marker before the name, then the fusion stars
        title = ("✨ " if border_key else "") + data['name'] + (" " + "⭐" * fusion_level if fusion_level > 0 else "")
        # Unlocked alternate skin (event): takes priority, replaces the normal render.
        # Fusion stars are composited ON the alt art (no border).
        from database import event_skin_has
        import os as _os_alt
        if event_skin_has(uid, data["id"]):
            alt_url = render_user_card(uid, data["id"], None,
                                       fusion_level=fusion_level, alt=True)
            alt_path = (_os_alt.path.join(_REPO_ROOT, alt_url.lstrip("/").replace("/", _os_alt.sep))
                        if alt_url else None)
            if alt_path and _os_alt.path.exists(alt_path):
                alt_panel = Panel(
                    ("🎨 " + data['name'] + (" " + "⭐" * fusion_level if fusion_level > 0 else ""))[:256])
                alt_panel.image("attachment://card.png")
                alt_panel.footer(t("cards.show.alt_skin_footer", loc,
                                   name=interaction.user.display_name))
                await interaction.followup.send(
                    view=alt_panel.view(),
                    file=discord.File(alt_path, filename="card.png"))
                return

        p = Panel(title[:256])
        file = None
        rendered_url = None
        if border_key or fusion_level > 0:
            border = border_get(border_key) if border_key else None
            rendered_url = render_user_card(uid, data["id"], border,
                                             fusion_level=fusion_level,
                                             fallback_url=data.get("image_url"))
        if rendered_url:
            # Serve the local file as an attachment (no public URL needed)
            import os as _os
            local_path = _os.path.join(
                _REPO_ROOT, rendered_url.lstrip("/").replace("/", _os.sep))
            if _os.path.exists(local_path):
                file = discord.File(local_path, filename="card.png")
                p.image("attachment://card.png")
            else:
                print(f"[show] render not found: {local_path}")
        else:
            print(f"[show] render_user_card returned None (card={data['id']} "
                  f"border={border_key} fusion={fusion_level})")
        if file is None:
            # No border/fusion: local render first (reliable), remote as a last resort
            img_url, img_file = _resolve_card_image(data)
            if img_url:
                p.image(img_url)
            elif img_file:
                file = img_file
                p.image("attachment://card.png")
        p.footer(t("cards.show.footer", loc, name=interaction.user.display_name))
        if file:
            await interaction.followup.send(view=p.view(), file=file)
        else:
            await interaction.followup.send(view=p.view())

    @show_cmd.autocomplete("card")
    async def show_autocomplete(interaction: discord.Interaction, current: str):
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            uid = str(interaction.user.id)
            rows = c.execute(
                "SELECT DISTINCT c.name FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                "WHERE uc.user_id = ? AND LOWER(c.name) LIKE ? ORDER BY c.name LIMIT 25",
                (uid, f"%{q}%")).fetchall()
            conn.close()
            return [app_commands.Choice(name=r["name"][:100], value=r["name"][:100])
                     for r in rows]
        except Exception:
            return []


    # === /cardcustom <card> <border>: apply an owned border ===
    @bot.tree.command(name="cardcustom", description="Apply a border you own to one of your cards")
    @app_commands.describe(card="Card name", border="Border to apply (or 'none' to remove)")
    async def cardcustom_cmd(interaction: discord.Interaction, card: str, border: str):
        from database import (card_get_by_name, user_card_count_owned,
                                user_border_consume, border_get,
                                card_customization_get, card_customization_set,
                                card_fusion_get)
        from services.card_render import render_user_card
        loc = locale_of(interaction)
        await interaction.response.defer(ephemeral=True)
        data = card_get_by_name(card.strip())
        if not data:
            await interaction.followup.send(
                t("cards.custom.not_found", loc, name=card), ephemeral=True)
            return
        uid = interaction.user.id
        if user_card_count_owned(uid, data["id"]) <= 0 and not _is_owner(uid):
            await interaction.followup.send(
                t("cards.custom.not_owned", loc, name=data['name']), ephemeral=True)
            return
        if data.get("rarity") == "secret":
            await interaction.followup.send(
                t("cards.custom.secret_forbidden", loc), ephemeral=True)
            return
        if data.get("event_key"):
            await interaction.followup.send(
                t("cards.custom.event_forbidden", loc), ephemeral=True)
            return
        bkey = (border or "").strip().lower()
        # "aucune"/"retirer" kept as accepted aliases: users who typed the old
        # French sentinel by hand keep working after the English migration.
        if bkey in ("none", "remove", "aucune", "retirer"):
            card_customization_set(uid, data["id"], None)
            await interaction.followup.send(
                t("cards.custom.removed", loc, name=data['name']), ephemeral=True)
            return
        # Already wearing this border? -> no-op, nothing consumed
        if card_customization_get(uid, data["id"]) == bkey:
            await interaction.followup.send(
                t("cards.custom.already_equipped", loc, name=data['name']), ephemeral=True)
            return
        border_obj = border_get(bkey)
        if not border_obj:
            await interaction.followup.send(
                t("cards.custom.border_not_found", loc), ephemeral=True)
            return
        # Consume 1 copy from the stock (owner exempt)
        if _is_owner(uid):
            user_border_consume(uid, bkey)  # best-effort, non blocking
        elif not user_border_consume(uid, bkey):
            await interaction.followup.send(
                t("cards.custom.no_stock", loc, border=border_obj['name']), ephemeral=True)
            return
        card_customization_set(uid, data["id"], bkey)
        render_user_card(uid, data["id"], border_obj,
                          fusion_level=card_fusion_get(uid, data["id"]),
                          fallback_url=data.get("image_url"))
        await interaction.followup.send(
            t("cards.custom.applied", loc, border=border_obj['name'], name=data['name']),
            ephemeral=True)

    @cardcustom_cmd.autocomplete("card")
    async def cardcustom_card_autocomplete(interaction: discord.Interaction, current: str):
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            uid = str(interaction.user.id)
            rows = c.execute(
                "SELECT DISTINCT c.name FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                "WHERE uc.user_id = ? AND c.rarity != 'secret' AND LOWER(c.name) LIKE ? "
                "ORDER BY c.name LIMIT 25", (uid, f"%{q}%")).fetchall()
            conn.close()
            return [app_commands.Choice(name=r["name"][:100], value=r["name"][:100])
                     for r in rows]
        except Exception:
            return []

    @cardcustom_cmd.autocomplete("border")
    async def cardcustom_border_autocomplete(interaction: discord.Interaction, current: str):
        from database import user_borders_list
        try:
            uid = interaction.user.id
            owned = user_borders_list(uid)
            choices = [app_commands.Choice(
                name=f"{b['name']} (x{b['qty']})", value=b["border_key"]) for b in owned]
            choices.append(app_commands.Choice(
                name=ti(interaction, "cards.custom.autocomplete_none"), value="none"))
            q = (current or "").strip().lower()
            if q:
                choices = [ch for ch in choices if q in ch.name.lower()]
            return choices[:25]
        except Exception:
            return []


    # === /cardinventory: items & cosmetics in stock ===
    _FRAGMENTS_PER_MYTHIC = 5

    def _card_result_display(card, owner, essence_gain, already_owned, locale=None,
                             lead_text=None):
        """Build the view/file of an obtained card, SAME FORMAT as /roll.
        `lead_text` is what used to live in `content=` (a V2 message has none).
        Returns (layout_view, img_file_or_None)."""
        rarity = card.get("rarity", "common")
        emoji = _get_rarity_title_emoji(bot, rarity)
        origin = card.get("subtitle") or "?"
        universe = universe_label(card.get("universe"), locale) or "?"
        rarity_display = "?????" if rarity == "secret" else rarity.upper()
        flavor = (card.get("flavor_subtitle") or "").strip()
        essence_line = (t("cards.roll.essences_line", locale, amount=essence_gain)
                        + (t("cards.roll.duplicate_x2", locale) if already_owned else ""))
        _elem = card.get("element")
        if _elem:
            essence_line += t("cards.roll.element_line", locale,
                              emoji=_get_element_emoji(bot, _elem),
                              label=_ELEM_LABELS.get(_elem, ''))
        desc_parts = []
        if flavor:
            desc_parts.append(f"_**{flavor}**_")
        desc_parts.append(t("cards.roll.card_desc", locale, rarity=rarity_display,
                            origin=origin, universe=universe, essence_line=essence_line))
        p = Panel(f"{emoji} {card['name']}"[:256], "\n\n".join(desc_parts))
        if lead_text:
            p.text(lead_text)
        thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
        if thumb_url:
            p.thumbnail(thumb_url)
        img_url, img_file = _resolve_card_image(card)
        if img_url:
            p.image(img_url)
        elif img_file:
            p.image("attachment://card.png")
        p.footer(t("cards.roll.belongs_to", locale, name=owner.display_name))
        return OwnersView(p, card["id"], card["name"], locale=locale), img_file

    def _inv_panel(target, locale=None):
        from database import (user_borders_list, user_item_get, roll_bonus_available)
        frags = user_item_get(target.id, "mythic_fragment")
        golden = user_item_get(target.id, "golden_roll")
        epic = user_item_get(target.id, "epic_roll")
        rolls = roll_bonus_available(target.id)
        borders = user_borders_list(target.id)
        p = Panel(t("cards.inventory.title", locale, name=target.display_name))
        if target.display_avatar:
            p.thumbnail(str(target.display_avatar.url))
        lines = [
            t("cards.inventory.bonus_rolls", locale, emoji=_roll_emoji(bot), count=rolls),
            t("cards.inventory.epic_rolls", locale, emoji=_epic_roll_emoji(bot), count=epic),
            t("cards.inventory.mythic_fragments", locale, count=frags,
              needed=_FRAGMENTS_PER_MYTHIC),
            t("cards.inventory.golden_rolls", locale, emoji=_golden_emoji(bot), count=golden),
        ]
        p.field(t("cards.inventory.field_items", locale), "\n".join(lines))
        if borders:
            bl = [t("cards.inventory.border_line", locale, name=b['name'], qty=b['qty'])
                  for b in borders]
            p.field(t("cards.inventory.field_borders", locale),
                    "\n".join(bl) + t("cards.inventory.borders_hint", locale))
        return p, frags, golden, epic

    class _InventoryRow(discord.ui.ActionRow):
        async def _guard(self, interaction):
            if interaction.user.id != self.view.owner_id:
                await interaction.response.send_message(
                    ti(interaction, "cards.inventory.not_yours"), ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Use Epic Roll", style=discord.ButtonStyle.primary, emoji="🟣")
        async def use_epic(self, interaction, btn):
            if not await self._guard(interaction):
                return
            from database import (user_item_consume, card_pick_random_exact_rarity, user_card_add,
                                  user_card_count_owned, essence_reward_add, ESSENCE_REWARDS, user_item_add)
            loc = locale_of(interaction)
            uid = interaction.user.id
            if not user_item_consume(uid, "epic_roll", 1):
                await interaction.response.send_message(
                    t("cards.inventory.no_epic", loc), ephemeral=True)
                return
            card = card_pick_random_exact_rarity("epic")
            if not card:
                user_item_add(uid, "epic_roll", 1)  # refunded
                await interaction.response.send_message(
                    t("cards.inventory.no_epic_card", loc), ephemeral=True)
                return
            already = user_card_count_owned(uid, card["id"]) > 0
            user_card_add(uid, card["id"])
            base = ESSENCE_REWARDS.get("epic", 80) * (2 if already else 1)
            ess = essence_reward_add(uid, base)
            view, img_file = _card_result_display(
                card, interaction.user, ess, already, locale=loc,
                lead_text=t("cards.inventory.epic_content", loc, emoji=_epic_roll_emoji(bot)))
            if img_file:
                await interaction.response.send_message(view=view, file=img_file)
            else:
                await interaction.response.send_message(view=view)

        @discord.ui.button(label="Use Golden Roll", style=discord.ButtonStyle.success, emoji="🌈")
        async def use_golden(self, interaction, btn):
            if not await self._guard(interaction):
                return
            from database import (user_item_consume, card_pick_random_exact_rarity, user_card_add,
                                  user_card_count_owned, essence_reward_add, ESSENCE_REWARDS)
            loc = locale_of(interaction)
            uid = interaction.user.id
            if not user_item_consume(uid, "golden_roll", 1):
                await interaction.response.send_message(
                    t("cards.inventory.no_golden", loc), ephemeral=True)
                return
            card = card_pick_random_exact_rarity("legendary")
            if not card:
                from database import user_item_add
                user_item_add(uid, "golden_roll", 1)  # refunded
                await interaction.response.send_message(
                    t("cards.inventory.no_golden_card", loc), ephemeral=True)
                return
            already = user_card_count_owned(uid, card["id"]) > 0
            user_card_add(uid, card["id"])
            base = ESSENCE_REWARDS.get("legendary", 220) * (2 if already else 1)
            ess = essence_reward_add(uid, base)
            # PUBLIC, same shape as a /roll, with the coupon mentioned in the panel
            view, img_file = _card_result_display(
                card, interaction.user, ess, already, locale=loc,
                lead_text=t("cards.inventory.golden_content", loc, emoji=_golden_emoji(bot)))
            if img_file:
                await interaction.response.send_message(view=view, file=img_file)
            else:
                await interaction.response.send_message(view=view)

        @discord.ui.button(label="Craft Mythic", style=discord.ButtonStyle.danger, emoji="🔴")
        async def craft_mythic(self, interaction, btn):
            if not await self._guard(interaction):
                return
            from database import (user_item_consume, card_pick_random_exact_rarity, user_card_add,
                                  user_card_count_owned, essence_reward_add, ESSENCE_REWARDS)
            loc = locale_of(interaction)
            uid = interaction.user.id
            if not user_item_consume(uid, "mythic_fragment", _FRAGMENTS_PER_MYTHIC):
                await interaction.response.send_message(
                    t("cards.inventory.need_fragments", loc, count=_FRAGMENTS_PER_MYTHIC),
                    ephemeral=True)
                return
            card = card_pick_random_exact_rarity("mythic")
            if not card:
                from database import user_item_add
                user_item_add(uid, "mythic_fragment", _FRAGMENTS_PER_MYTHIC)  # refunded
                await interaction.response.send_message(
                    t("cards.inventory.no_mythic_card", loc), ephemeral=True)
                return
            already = user_card_count_owned(uid, card["id"]) > 0
            user_card_add(uid, card["id"])
            ess = essence_reward_add(uid, ESSENCE_REWARDS.get("mythic", 650) * (2 if already else 1))
            # refresh the inventory (fragments consumed) then post the card in a clean panel
            inv_panel, frags, golden, epic = _inv_panel(interaction.user, locale=loc)
            await interaction.response.edit_message(
                view=_InventoryView(inv_panel, uid, frags, golden, epic, locale=loc))
            view, img_file = _card_result_display(
                card, interaction.user, ess, already, locale=loc,
                lead_text=t("cards.inventory.craft_content", loc, count=_FRAGMENTS_PER_MYTHIC))
            if img_file:
                await interaction.followup.send(view=view, file=img_file)
            else:
                await interaction.followup.send(view=view)

    class _InventoryView(discord.ui.LayoutView):
        """Inventory panel + the action row (only when the viewer owns items)."""
        def __init__(self, panel, owner_id, frags, golden, epic, locale=None,
                     with_buttons=True):
            super().__init__(timeout=180)
            self.owner_id = owner_id
            self.locale = locale
            self.add_item(panel.container())
            if not with_buttons:
                return
            r = _InventoryRow()
            r.use_epic.label = t("cards.inventory.btn_epic", locale)
            r.use_golden.label = t("cards.inventory.btn_golden", locale)
            r.craft_mythic.label = t("cards.inventory.btn_craft", locale,
                                     count=_FRAGMENTS_PER_MYTHIC)
            ge = discord.utils.get(bot.emojis, name="goldenroll")
            if ge:
                r.use_golden.emoji = ge
            ee = discord.utils.get(bot.emojis, name="epicroll")
            if ee:
                r.use_epic.emoji = ee
            r.use_epic.disabled = epic <= 0
            r.use_golden.disabled = golden <= 0
            r.craft_mythic.disabled = frags < _FRAGMENTS_PER_MYTHIC
            self.add_item(r)

    @bot.tree.command(name="cardinventory", description="Your items: rolls, mythic fragments, golden rolls, borders")
    @app_commands.describe(member="View someone else's inventory (default: you)")
    async def cardinventory_cmd(interaction: discord.Interaction, member: discord.Member = None):
        loc = locale_of(interaction)
        target = member or interaction.user
        is_self = target.id == interaction.user.id
        panel, frags, golden, epic = _inv_panel(target, locale=loc)
        with_buttons = is_self and (epic > 0 or golden > 0 or frags >= _FRAGMENTS_PER_MYTHIC)
        view = _InventoryView(panel, interaction.user.id, frags, golden, epic,
                              locale=loc, with_buttons=with_buttons)
        await interaction.response.send_message(view=view, ephemeral=is_self)


    # Shared autocomplete: cards the user has DUPLICATES of (>1 copy)
    async def _dup_cards_autocomplete(interaction: discord.Interaction, current: str):
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            uid = str(interaction.user.id)
            rows = c.execute(
                "SELECT c.name, COUNT(*) AS n FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                "WHERE uc.user_id = ? AND LOWER(c.name) LIKE ? "
                "GROUP BY uc.card_id HAVING n > 1 ORDER BY c.name LIMIT 25",
                (uid, f"%{q}%")).fetchall()
            conn.close()
            return [app_commands.Choice(name=f"{r['name']} (x{r['n']})"[:100], value=r["name"][:100])
                     for r in rows]
        except Exception:
            return []


    # === /cardrecycle: duplicates -> essences ===
    @bot.tree.command(name="cardrecycle", description="Recycle your duplicates into Essences ✨")
    @app_commands.describe(card="Card to recycle (you always keep 1 copy)",
                            amount="Number of duplicates to recycle (default: all)")
    async def cardrecycle_cmd(interaction: discord.Interaction, card: str, amount: int = None):
        from database import (card_get_by_name, user_card_count_owned,
                                user_card_remove_copies, ESSENCE_RECYCLE)
        loc = locale_of(interaction)
        data = card_get_by_name(card.strip())
        if not data:
            await interaction.response.send_message(
                t("cards.recycle.not_found", loc, name=card), ephemeral=True)
            return
        uid = interaction.user.id
        owned = user_card_count_owned(uid, data["id"])
        dupes = max(0, owned - 1)  # always keep 1
        if dupes <= 0:
            await interaction.response.send_message(
                t("cards.recycle.no_duplicates", loc, name=data['name']), ephemeral=True)
            return
        qty = dupes if amount is None else max(1, min(int(amount), dupes))
        rarity = data.get("rarity", "common")
        per = ESSENCE_RECYCLE.get(rarity, 6)
        removed = user_card_remove_copies(uid, data["id"], qty)
        from database import essence_reward_add, currency_get
        gain = essence_reward_add(uid, per * removed)  # applies the daily wheel bonus
        new_bal = currency_get(uid)
        await interaction.response.send_message(
            t("cards.recycle.success", loc, count=removed, name=data['name'],
              gain=gain, balance=new_bal), ephemeral=True)

    @cardrecycle_cmd.autocomplete("card")
    async def cardrecycle_autocomplete(interaction: discord.Interaction, current: str):
        return await _dup_cards_autocomplete(interaction, current)


    # === /cardfuse: raise a card's star level using its duplicates ===
    @bot.tree.command(name="cardfuse", description="Fuse your duplicates to add a star to a card (max 5)")
    @app_commands.describe(card="Card to raise a star on")
    async def cardfuse_cmd(interaction: discord.Interaction, card: str):
        from database import (card_get_by_name, user_card_count_owned,
                                user_card_remove_copies, card_fusion_get, card_fusion_set,
                                card_customization_get, border_get,
                                user_card_lock_one,
                                FUSION_STAR_COSTS, FUSION_MAX_STARS)
        from services.card_render import render_user_card
        loc = locale_of(interaction)
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id

        # === /cardfuse all: max out EVERY card that has duplicates ===
        if card.strip().lower() == "all":
            from database import get_db
            conn = get_db(); c = conn.cursor()
            rows = c.execute(
                "SELECT uc.card_id, c.name, c.image_url, COUNT(*) AS n, "
                "  COALESCE(cc.fusion_level, 0) AS lvl "
                "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                "LEFT JOIN card_customizations cc ON cc.user_id = uc.user_id AND cc.card_id = uc.card_id "
                "WHERE uc.user_id = ? "
                "GROUP BY uc.card_id HAVING n > 1 AND lvl < ?",
                (str(uid), FUSION_MAX_STARS)).fetchall()
            conn.close()
            cards_fused = 0
            stars_gained = 0
            consumed_total = 0
            for r in rows:
                cid = r["card_id"]
                owned = int(r["n"])
                lvl = int(r["lvl"])
                gained_here = 0
                while lvl < FUSION_MAX_STARS and owned >= FUSION_STAR_COSTS[lvl]:
                    cost = FUSION_STAR_COSTS[lvl]
                    user_card_remove_copies(uid, cid, cost - 1)
                    lvl += 1
                    card_fusion_set(uid, cid, lvl)
                    user_card_lock_one(uid, cid)
                    owned -= (cost - 1)
                    consumed_total += (cost - 1)
                    gained_here += 1
                if gained_here:
                    cards_fused += 1
                    stars_gained += gained_here
                    border_key = card_customization_get(uid, cid)
                    border = border_get(border_key) if border_key else None
                    render_user_card(uid, cid, border, fusion_level=lvl,
                                     fallback_url=r["image_url"])
            if stars_gained == 0:
                await interaction.followup.send(
                    t("cards.fuse.all_none", loc), ephemeral=True)
                return
            # Guild XP/quest hook: 1 fusion = 1 star gained
            try:
                from database import get_guild_config, guild_member_action_xp, guild_quest_progress
                _xpf = int(get_guild_config().get("xp", {}).get("fusion", 0))
                if _xpf:
                    guild_member_action_xp(uid, _xpf * stars_gained, source="fusion (all)")
                guild_quest_progress(uid, "fusion", stars_gained)
            except Exception as e:
                print(f"[fusion all guild xp] err: {e}")
            await interaction.followup.send(
                t("cards.fuse.all_success", loc, cards=cards_fused, stars=stars_gained,
                  consumed=consumed_total), ephemeral=True)
            return

        data = card_get_by_name(card.strip())
        if not data:
            await interaction.followup.send(
                t("cards.fuse.not_found", loc, name=card), ephemeral=True)
            return
        level = card_fusion_get(uid, data["id"])
        if level >= FUSION_MAX_STARS:
            await interaction.followup.send(
                t("cards.fuse.max_level", loc, name=data['name'],
                  stars="⭐" * FUSION_MAX_STARS), ephemeral=True)
            return
        # Cost = number of copies required (includes the card that keeps the stars)
        cost = FUSION_STAR_COSTS[level]
        owned = user_card_count_owned(uid, data["id"])
        if owned < cost:
            await interaction.followup.send(
                t("cards.fuse.not_enough", loc, cost=cost, name=data['name'],
                  stars="⭐" * (level + 1), owned=owned, consumed=cost - 1),
                ephemeral=True)
            return
        # Consume cost-1 copies, the last one keeps the stars
        removed = user_card_remove_copies(uid, data["id"], cost - 1)
        new_level = level + 1
        card_fusion_set(uid, data["id"], new_level)
        # Guild XP hook (one star fused)
        try:
            from database import get_guild_config, guild_member_action_xp, guild_quest_progress
            _xpf = int(get_guild_config().get("xp", {}).get("fusion", 0))
            if _xpf:
                guild_member_action_xp(uid, _xpf, source="fusion")
            guild_quest_progress(uid, "fusion", 1)
        except Exception as e:
            print(f"[fusion guild xp] err: {e}")
        # Lock ONE copy (the one carrying the stars). Extra duplicates stay
        # tradeable and recyclable.
        user_card_lock_one(uid, data["id"])
        # Regenerate the render (keeps the border if equipped)
        border_key = card_customization_get(uid, data["id"])
        border = border_get(border_key) if border_key else None
        render_user_card(uid, data["id"], border, fusion_level=new_level,
                          fallback_url=data.get("image_url"))
        nxt = (t("cards.fuse.next_star", loc, cost=FUSION_STAR_COSTS[new_level])
               if new_level < FUSION_MAX_STARS else t("cards.fuse.max_reached", loc))
        await interaction.followup.send(
            t("cards.fuse.success", loc, name=data['name'], removed=removed,
              stars="⭐" * new_level, next=nxt), ephemeral=True)

    @cardfuse_cmd.autocomplete("card")
    async def cardfuse_autocomplete(interaction: discord.Interaction, current: str):
        # Like _dup_cards but EXCLUDES cards already maxed out (5⭐)
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            uid = str(interaction.user.id)
            rows = c.execute(
                "SELECT c.name, COUNT(*) AS n, "
                "  COALESCE(cc.fusion_level, 0) AS lvl "
                "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                "LEFT JOIN card_customizations cc ON cc.user_id = uc.user_id AND cc.card_id = uc.card_id "
                "WHERE uc.user_id = ? AND LOWER(c.name) LIKE ? "
                "GROUP BY uc.card_id HAVING n > 1 AND lvl < 5 ORDER BY c.name LIMIT 24",
                (uid, f"%{q}%")).fetchall()
            conn.close()
            out = []
            # "all" option first (fuses every duplicate at once)
            if not q or "all" in q or "tout" in q:
                out.append(app_commands.Choice(
                    name=ti(interaction, "cards.fuse.autocomplete_all"), value="all"))
            out += [app_commands.Choice(
                name=f"{r['name']} (x{r['n']}{' ' + '⭐'*r['lvl'] if r['lvl'] else ''})"[:100],
                value=r["name"][:100]) for r in rows]
            return out[:25]
        except Exception:
            return []


    # === /eventfight: high-stakes event fight (card power vs monster HP) ===
    # Monster tiers: (HP in mult units, token reward). Element advantage
    # multiplies your damage x1.25 (disadvantage x0.8). You win IF damage >= HP.
    _EFIGHT_TIERS = {
        "faible":  {"label_key": "cards.eventfight.tier_weak",   "emoji": "🟢", "hp": 1.0, "coins": 3},
        "moyen":   {"label_key": "cards.eventfight.tier_medium", "emoji": "🟡", "hp": 1.7, "coins": 5},
        "costaud": {"label_key": "cards.eventfight.tier_tough",  "emoji": "🔴", "hp": 2.6, "coins": 8},
    }
    _EFIGHT_FAIL_COINS = 1

    @bot.tree.command(name="eventfight",
                       description="Event fight: send your best card for the element (3/day)")
    async def eventfight_cmd(interaction: discord.Interaction):
        from database import (global_event_for_guild, event_fight_used, event_fight_inc,
                               event_coins_add, EVENT_FIGHT_MAX_PER_DAY,
                               CARD_ELEMENTS, CARD_ELEMENT_LABELS, CARD_ELEMENT_EMOJI,
                               element_matchup, CARD_RARITY_COMBAT_MULT,
                               CARD_STAR_COMBAT_BONUS, get_db)
        import random as _r
        loc = locale_of(interaction)
        ev = global_event_for_guild(interaction.guild.id if interaction.guild else None)
        if not ev.get("active"):
            await interaction.response.send_message(
                t("cards.eventfight.no_event", loc), ephemeral=True)
            return
        uid = interaction.user.id
        ek = ev["key"]
        if event_fight_used(uid, ek) >= EVENT_FIGHT_MAX_PER_DAY and not _is_owner(uid):
            await interaction.response.send_message(
                t("cards.eventfight.daily_limit", loc, max=EVENT_FIGHT_MAX_PER_DAY),
                ephemeral=True)
            return

        # Player's best card (power) per element
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT c.element, c.name, c.rarity, COALESCE(cc.fusion_level,0) AS lvl "
            "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
            "LEFT JOIN card_customizations cc ON cc.user_id = uc.user_id AND cc.card_id = uc.card_id "
            "WHERE uc.user_id = ?", (str(uid),)).fetchall()
        conn.close()

        def _mult(rar, stars):
            if rar == "secret" and stars >= 5:
                return 999.0
            return CARD_RARITY_COMBAT_MULT.get(rar, 1.0) * (1.0 + min(5, stars) * CARD_STAR_COMBAT_BONUS)

        best = {}  # element -> (power, name)
        for r in rows:
            el = r["element"] or "eclat"
            p = _mult(r["rarity"], int(r["lvl"]))
            if el not in best or p > best[el][0]:
                best[el] = (p, r["name"])
        if not best:
            await interaction.response.send_message(
                t("cards.eventfight.no_cards", loc), ephemeral=True)
            return

        left = EVENT_FIGHT_MAX_PER_DAY - event_fight_used(uid, ek)

        def _tier_label(tr):
            return t(tr["label_key"], loc)

        def _tier_panel():
            lines = [t("cards.eventfight.tier_line", loc, emoji=tr["emoji"],
                       label=_tier_label(tr), hp=f"{tr['hp']:.1f}", coins=tr["coins"],
                       coin_emoji=ev["coin_emoji"])
                     for tr in _EFIGHT_TIERS.values()]
            return Panel(
                t("cards.eventfight.title", loc, emoji=ev["emoji"], event=ev["name"]),
                t("cards.eventfight.intro", loc, lines="\n".join(lines), left=left))

        async def _resolve(inter, tier_key, monster_elem, el):
            if event_fight_used(uid, ek) >= EVENT_FIGHT_MAX_PER_DAY and not _is_owner(uid):
                await inter.response.edit_message(
                    view=Panel(description=t("cards.eventfight.no_fight_left", loc)).view())
                return
            tr = _EFIGHT_TIERS[tier_key]
            power, cname = best[el]
            m = element_matchup(el, monster_elem)
            dmg = power * m
            event_fight_inc(uid, ek)
            win = dmg >= tr["hp"]
            coins = tr["coins"] if win else _EFIGHT_FAIL_COINS
            bal = event_coins_add(uid, ek, coins)
            adv_tag = (t("cards.eventfight.advantage", loc) if m > 1
                       else (t("cards.eventfight.disadvantage", loc) if m < 1
                             else t("cards.eventfight.neutral", loc)))
            head = t("cards.eventfight.win", loc) if win else t("cards.eventfight.lose", loc)
            res = t("cards.eventfight.result", loc, head=head,
                    card_emoji=CARD_ELEMENT_EMOJI.get(el, ''), card=cname,
                    power=f"{power:.2f}", tier_emoji=tr["emoji"], tier=_tier_label(tr),
                    hp=f"{tr['hp']:.1f}",
                    monster_emoji=CARD_ELEMENT_EMOJI.get(monster_elem, ''),
                    monster_element=CARD_ELEMENT_LABELS.get(monster_elem),
                    damage=f"{dmg:.2f}", advantage=adv_tag, coins=coins,
                    coin=ev['coin'], coin_emoji=ev['coin_emoji'], balance=bal)
            await inter.response.edit_message(
                view=Panel(t("cards.eventfight.result_title", loc, emoji=ev['emoji']),
                           res).view())

        class _ElemView(discord.ui.LayoutView):
            def __init__(self, panel, tier_key, monster_elem):
                super().__init__(timeout=120)
                self.tier_key = tier_key
                self.monster_elem = monster_elem
                self.add_item(panel.container())
                self.add_item(row(*(self._mk(el) for el in CARD_ELEMENTS)))

            def _mk(self, el):
                has = el in best
                lbl = CARD_ELEMENT_LABELS.get(el, el)
                if has:
                    lbl += f" ({best[el][0]:.2f})"
                btn = discord.ui.Button(label=lbl[:80], emoji=CARD_ELEMENT_EMOJI.get(el),
                                        style=discord.ButtonStyle.secondary, disabled=not has)
                async def _cb(inter):
                    if inter.user.id != uid:
                        await inter.response.send_message(
                            t("cards.eventfight.not_yours", loc), ephemeral=True); return
                    await _resolve(inter, self.tier_key, self.monster_elem, el)
                btn.callback = _cb
                return btn

        class _TierView(discord.ui.LayoutView):
            def __init__(self):
                super().__init__(timeout=120)
                self.add_item(_tier_panel().container())
                self.add_item(row(*(self._mk(k, tr) for k, tr in _EFIGHT_TIERS.items())))

            def _mk(self, k, tr):
                btn = discord.ui.Button(
                    label=t("cards.eventfight.tier_btn", loc, label=_tier_label(tr),
                            coins=tr["coins"])[:80],
                    emoji=tr["emoji"], style=discord.ButtonStyle.primary)
                async def _cb(inter):
                    if inter.user.id != uid:
                        await inter.response.send_message(
                            t("cards.eventfight.not_yours", loc), ephemeral=True); return
                    monster_elem = _r.choice(CARD_ELEMENTS)
                    weak = element_matchup  # local ref
                    # elements that beat the monster (hint)
                    counters = [e for e in CARD_ELEMENTS if weak(e, monster_elem) > 1]
                    counter_txt = " ".join(
                        f"{CARD_ELEMENT_EMOJI.get(e,'')}{CARD_ELEMENT_LABELS.get(e)}" for e in counters) or "—"
                    mp = Panel(
                        t("cards.eventfight.monster_title", loc, emoji=ev['emoji'],
                          tier_emoji=tr['emoji'], tier=_tier_label(tr)),
                        t("cards.eventfight.monster_desc", loc,
                          monster_emoji=CARD_ELEMENT_EMOJI.get(monster_elem, ''),
                          monster_element=CARD_ELEMENT_LABELS.get(monster_elem),
                          hp=f"{tr['hp']:.1f}", counters=counter_txt))
                    await inter.response.edit_message(view=_ElemView(mp, k, monster_elem))
                btn.callback = _cb
                return btn

        await interaction.response.send_message(view=_TierView())


    # === /eventshop: event shop (tokens) ===
    @bot.tree.command(name="eventshop",
                       description="Event shop: spend your tokens (rolls, essence bonus)")
    async def eventshop_cmd(interaction: discord.Interaction):
        from database import (global_event_for_guild, event_coins_get, event_coins_spend,
                               essence_bonus_add, roll_give_user, event_shop_skins,
                               event_skin_grant, user_item_add,
                               EVENT_SHOP_ROLL_COST, EVENT_SHOP_GOLDEN_COST,
                               EVENT_SHOP_ESS10_COST,
                               EVENT_SHOP_ESS10_PCT, EVENT_SHOP_SKIN_COST)
        import os as _os_shop, glob as _glob_shop
        loc = locale_of(interaction)
        ev = global_event_for_guild(interaction.guild.id if interaction.guild else None)
        if not ev.get("active"):
            await interaction.response.send_message(
                t("cards.eventshop.no_event", loc), ephemeral=True)
            return
        uid = interaction.user.id
        ek = ev["key"]

        # Shop visual: assets/cardrelated/global event/<event>_*/shop.png.
        # We serve an OPTIMIZED version (resize + compression) because the raw image
        # can exceed the payload limit of an interaction response (413).
        _shop_img = None
        try:
            _matches = _glob_shop.glob(_os_shop.path.join(
                _REPO_ROOT, "assets", "cardrelated", "global event", f"{ek}_*", "shop.png"))
            if _matches:
                _src = _matches[0]
                _opt_dir = _os_shop.path.join(_REPO_ROOT, "static", "event_shop")
                _os_shop.makedirs(_opt_dir, exist_ok=True)
                _opt = _os_shop.path.join(_opt_dir, f"{ek}.jpg")
                if (not _os_shop.path.exists(_opt)
                        or _os_shop.path.getmtime(_opt) < _os_shop.path.getmtime(_src)):
                    from PIL import Image as _PImg
                    _im = _PImg.open(_src).convert("RGB")
                    if _im.width > 1000:
                        _im = _im.resize((1000, int(_im.height * 1000 / _im.width)), _PImg.LANCZOS)
                    _im.save(_opt, "JPEG", quality=85, optimize=True)
                _shop_img = _opt
        except Exception as _e:
            print(f"[eventshop] opt image err: {_e}")
            _shop_img = None

        def _panel():
            bal = event_coins_get(uid, ek)
            skins = event_shop_skins(uid, ek)
            buyable = [s for s in skins if not s["owned_skin"]]
            owned = [s for s in skins if s["owned_skin"]]
            desc = t("cards.eventshop.desc", loc, coin=ev['coin'], balance=bal,
                     coin_emoji=ev['coin_emoji'], roll_emoji=_roll_emoji(bot),
                     roll_cost=EVENT_SHOP_ROLL_COST, golden_emoji=_golden_emoji(bot),
                     golden_cost=EVENT_SHOP_GOLDEN_COST, ess_pct=EVENT_SHOP_ESS10_PCT,
                     ess_cost=EVENT_SHOP_ESS10_COST, skin_cost=EVENT_SHOP_SKIN_COST)
            if buyable:
                desc += "\n" + "\n".join(
                    t("cards.eventshop.skin_line", loc,
                      emoji=RARITY_EMOJIS.get(s['rarity'], '⚪'), name=s['name'])
                    for s in buyable[:10])
            else:
                desc += t("cards.eventshop.no_skins", loc)
            if owned:
                desc += t("cards.eventshop.owned_skins", loc,
                          list=", ".join(s["name"] for s in owned[:10]))
            p = Panel(t("cards.eventshop.title", loc, emoji=ev['emoji'], event=ev['name']),
                      desc)
            if _shop_img:
                p.image("attachment://shop.jpg")
            return p

        def _skin_select():
            skins = [s for s in event_shop_skins(uid, ek) if not s["owned_skin"]]
            if not skins:
                return None
            opts = [discord.SelectOption(
                label=s["name"][:100], value=str(s["id"]),
                description=t("cards.eventshop.skin_option_desc", loc, rarity=s['rarity'],
                              cost=EVENT_SHOP_SKIN_COST, coin=ev['coin']),
                emoji=RARITY_EMOJIS.get(s["rarity"], "⚪")) for s in skins[:25]]
            sel = discord.ui.Select(placeholder=t("cards.eventshop.skin_placeholder", loc),
                                    options=opts)
            async def _on(inter):
                if inter.user.id != uid:
                    await inter.response.send_message(
                        t("cards.eventshop.not_yours", loc), ephemeral=True); return
                cid = int(sel.values[0])
                if not event_coins_spend(uid, ek, EVENT_SHOP_SKIN_COST):
                    await inter.response.send_message(
                        t("cards.eventshop.not_enough", loc, coin=ev['coin']),
                        ephemeral=True); return
                event_skin_grant(uid, cid)
                await inter.response.edit_message(view=_ShopView())
                await inter.followup.send(
                    t("cards.eventshop.skin_unlocked", loc), ephemeral=True)
            sel.callback = _on
            return sel

        class _ShopRow(discord.ui.ActionRow):
            async def _guard(self, inter):
                if inter.user.id != uid:
                    await inter.response.send_message(
                        t("cards.eventshop.not_yours", loc), ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="Buy a roll", emoji="🎲", style=discord.ButtonStyle.success)
            async def buy_roll(self, inter, _b):
                if not await self._guard(inter): return
                if not event_coins_spend(uid, ek, EVENT_SHOP_ROLL_COST):
                    await inter.response.send_message(
                        t("cards.eventshop.not_enough", loc, coin=ev['coin']),
                        ephemeral=True); return
                roll_give_user(uid, 1)
                await inter.response.edit_message(view=_ShopView())
                await inter.followup.send(t("cards.eventshop.roll_bought", loc), ephemeral=True)

            @discord.ui.button(label="Golden Roll", emoji="🌈", style=discord.ButtonStyle.success)
            async def buy_golden(self, inter, _b):
                if not await self._guard(inter): return
                if not event_coins_spend(uid, ek, EVENT_SHOP_GOLDEN_COST):
                    await inter.response.send_message(
                        t("cards.eventshop.not_enough", loc, coin=ev['coin']),
                        ephemeral=True); return
                user_item_add(uid, "golden_roll", 1)
                await inter.response.edit_message(view=_ShopView())
                await inter.followup.send(
                    t("cards.eventshop.golden_bought", loc), ephemeral=True)

            @discord.ui.button(label="+10% essences (1d)", emoji="✨", style=discord.ButtonStyle.primary)
            async def buy_ess(self, inter, _b):
                if not await self._guard(inter): return
                if not event_coins_spend(uid, ek, EVENT_SHOP_ESS10_COST):
                    await inter.response.send_message(
                        t("cards.eventshop.not_enough", loc, coin=ev['coin']),
                        ephemeral=True); return
                total = essence_bonus_add(uid, EVENT_SHOP_ESS10_PCT)
                await inter.response.edit_message(view=_ShopView())
                await inter.followup.send(
                    t("cards.eventshop.essences_bought", loc, total=total), ephemeral=True)

        class _ShopView(discord.ui.LayoutView):
            def __init__(self):
                super().__init__(timeout=180)
                self.add_item(_panel().container())
                # support-server custom emojis on the roll / golden buttons
                _re = discord.utils.get(bot.emojis, name="roll")
                _ge = discord.utils.get(bot.emojis, name="goldenroll")
                r = _ShopRow()
                r.buy_roll.label = t("cards.eventshop.btn_roll", loc)
                r.buy_golden.label = t("cards.eventshop.btn_golden", loc)
                r.buy_ess.label = t("cards.eventshop.btn_essences", loc)
                if _re:
                    r.buy_roll.emoji = _re
                if _ge:
                    r.buy_golden.emoji = _ge
                self.add_item(r)
                s = _skin_select()
                if s:
                    self.add_item(row(s))

        try:
            if _shop_img:
                await interaction.response.send_message(
                    view=_ShopView(),
                    file=discord.File(_shop_img, filename="shop.jpg"), ephemeral=True)
            else:
                await interaction.response.send_message(view=_ShopView(), ephemeral=True)
        except Exception as _e:
            import traceback as _tb
            print(f"[eventshop] {type(_e).__name__}: {_e}")
            _tb.print_exc()
            try:
                await interaction.response.send_message(
                    t("cards.eventshop.error", loc, error=type(_e).__name__), ephemeral=True)
            except Exception:
                pass


    # === /cardup: tier-up (duplicates of a rarity -> 1 card of the rarity above) ===
    @bot.tree.command(name="cardup",
                       description="Sacrifice duplicates of your 5⭐ cards for 1 random card of the rarity above")
    @app_commands.describe(rarity="Rarity of the duplicates (from 5⭐ cards) to sacrifice")
    @app_commands.choices(rarity=[
        app_commands.Choice(name="Common → Rare", value="common"),
        app_commands.Choice(name="Rare → Epic", value="rare"),
        app_commands.Choice(name="Epic → Legendary", value="epic"),
        app_commands.Choice(name="Legendary → Mythic", value="legendary"),
    ])
    async def cardup_cmd(interaction: discord.Interaction, rarity: app_commands.Choice[str]):
        from database import (CARDUP_NEXT, CARDUP_COST, user_duplicate_count_by_rarity,
                               user_consume_duplicates_by_rarity, card_pick_random_exact_rarity,
                               user_card_add)
        loc = locale_of(interaction)
        await interaction.response.defer()
        src = rarity.value
        nxt = CARDUP_NEXT.get(src)
        cost = CARDUP_COST.get(src)
        if not nxt or not cost:
            await interaction.followup.send(t("cards.cardup.invalid_rarity", loc), ephemeral=True)
            return
        uid = interaction.user.id
        avail = user_duplicate_count_by_rarity(uid, src)
        if avail < cost:
            await interaction.followup.send(
                t("cards.cardup.not_enough", loc, cost=cost, rarity=src, owned=avail),
                ephemeral=True)
            return
        removed = user_consume_duplicates_by_rarity(uid, src, cost)
        reward = card_pick_random_exact_rarity(nxt)
        if not reward:
            await interaction.followup.send(
                t("cards.cardup.no_reward", loc, rarity=nxt), ephemeral=True)
            return
        user_card_add(uid, reward["id"])
        emoji = _get_rarity_title_emoji(bot, nxt)
        p = Panel(
            t("cards.cardup.title", loc),
            t("cards.cardup.desc", loc, removed=removed, rarity=src,
              emoji=emoji, name=reward['name'], new_rarity=nxt.upper(),
              origin=reward.get('subtitle') or '?'),
        )
        img_url, img_file = _resolve_card_image(reward)
        if img_url:
            p.image(img_url)
        elif img_file:
            p.image("attachment://card.png")
        if img_file:
            await interaction.followup.send(view=p.view(), file=img_file)
        else:
            await interaction.followup.send(view=p.view())


    # === /cardprofile: view a profile OR set it up (optional params) ===
    async def _owned_cards_autocomplete(interaction: discord.Interaction, current: str):
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            uid = str(interaction.user.id)
            rows = c.execute(
                "SELECT DISTINCT c.name FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                "WHERE uc.user_id = ? AND LOWER(c.name) LIKE ? ORDER BY c.name LIMIT 25",
                (uid, f"%{q}%")).fetchall()
            conn.close()
            return [app_commands.Choice(name=r["name"][:100], value=r["name"][:100])
                     for r in rows]
        except Exception:
            return []

    @bot.tree.command(name="cardprofile",
                       description="View your card profile (or customize it with custom)")
    @app_commands.describe(
        member="Profile to display (default: you)",
        custom="Customize: pick your 3 featured cards + your color")
    async def cardprofile_cmd(interaction: discord.Interaction,
                               member: discord.Member = None,
                               custom: bool = False):
        from database import (card_profile_get, user_card_count, user_card_rarity_breakdown,
                               currency_get, user_card_fusion_map, user_borders_list)
        from services.card_profile import build_profile_image
        import os as _os

        # --- CUSTOMIZE mode: card dropdowns + color ---
        if custom:
            await _open_profile_customizer(bot, interaction)
            return

        # --- VIEW mode ---
        loc = locale_of(interaction)
        await interaction.response.defer()
        target = member or interaction.user
        uid = target.id
        profile = card_profile_get(uid)
        # Stats
        total = user_card_count(uid)
        breakdown = user_card_rarity_breakdown(uid)
        from database import get_db
        conn = get_db(); c = conn.cursor()
        uniq = c.execute("SELECT COUNT(DISTINCT card_id) AS n FROM user_cards WHERE user_id = ?",
                          (str(uid),)).fetchone()["n"]
        conn.close()
        essences = currency_get(uid)
        fused = len(user_card_fusion_map(uid))
        borders_stock = sum(b["qty"] for b in user_borders_list(uid))
        rar_line = "　　".join(
            f"{RARITY_EMOJIS.get(r, '⚪')} **{breakdown.get(r, 0)}**"
            for r in ("common", "rare", "epic", "legendary", "mythic"))
        # Luck index: rarity-weighted average vs the expected average (50% = average)
        _pts = {"common": 1, "rare": 2, "epic": 5, "legendary": 25, "mythic": 100, "secret": 200}
        _total_pts = sum(_pts.get(r, 1) * n for r, n in breakdown.items())
        _avg = (_total_pts / total) if total else 0
        _expected = 3.85  # expected points/roll given the draw weights
        luck = max(0, min(100, round(_avg / _expected * 50))) if total else 0
        from database import compute_player_combat_stats, combat_power
        cs = compute_player_combat_stats(uid)
        def _fmt(n):
            return f"{int(n):,}".replace(",", " ")
        DIV = "══════════════════════════════"
        bonus_inline = (t("cards.profile.fusion_bonus", loc,
                          pct=round(cs.get('bonus_pct', 0))) if cs['stars'] else "")
        power = combat_power(cs['hp'], cs['atk'])
        power_emojis = _power_emoji_str(bot, power)

        # Chosen profile color + guild emblem
        _emblem = ""
        try:
            from database import guild_of_user as _gou2
            _gg = _gou2(uid)
            if _gg and _gg.get("emblem"):
                _emblem = _gg["emblem"] + " "
        except Exception:
            pass
        p = Panel(t("cards.profile.title", loc, emblem=_emblem, name=target.display_name))
        # Top: 3 stats merged onto a single line (inline fields)
        p.field(t("cards.profile.field_collection", loc),
                t("cards.profile.field_collection_value", loc,
                  total=_fmt(total), unique=_fmt(uniq)), inline=True)
        p.field(t("cards.profile.field_essences", loc), f"{_fmt(essences)}", inline=True)
        p.field(t("cards.profile.field_luck", loc), f"{luck}%", inline=True)
        # Rest: a single block. The 1st separator = field NAME (avoids the empty line).
        block = t("cards.profile.block", loc, bonus=bonus_inline, hp=_fmt(cs['hp']),
                  atk=_fmt(cs['atk']), power=power_emojis, div=DIV, fused=_fmt(fused),
                  borders=_fmt(borders_stock), rarities=(rar_line or "—"))
        # Guild (if a member)
        try:
            from database import guild_of_user
            _g = guild_of_user(uid)
            if _g:
                _tag = f" [{_g['tag']}]" if _g.get("tag") else ""
                block = t("cards.profile.guild_line", loc, name=_g['name'], tag=_tag,
                          level=_g['level'], div=DIV) + block
        except Exception:
            pass
        p.field(DIV, block)

        if target.display_avatar:
            p.thumbnail(str(target.display_avatar.url))
        file = None
        has_cards = bool(profile and profile.get("left_id") and profile.get("mid_id") and profile.get("right_id"))
        if has_cards:
            rel = build_profile_image(uid, profile)
            if rel:
                local_path = _os.path.join(_REPO_ROOT, rel.lstrip("/").replace("/", _os.sep))
                if _os.path.exists(local_path):
                    file = discord.File(local_path, filename="profile.png")
                    p.image("attachment://profile.png")
        if not has_cards:
            note = (t("cards.profile.no_featured_self", loc) if target == interaction.user
                    else t("cards.profile.no_featured_other", loc, name=target.display_name))
            if target == interaction.user:
                note += t("cards.profile.no_featured_hint", loc)
            p.text(note)
        try:
            from database import roll_total_get
            _rt = roll_total_get(uid)
        except Exception:
            _rt = 0
        p.footer(t("cards.profile.footer", loc, name=target.display_name, rolls=_fmt(_rt)))
        if file:
            await interaction.followup.send(view=p.view(), file=file)
        else:
            await interaction.followup.send(view=p.view())



    # === /cardwish <card>: add/remove from the wishlist ===
    # Cap: 3 by default, 6 for support-server members.
    def _wishlist_max(user_id):
        base = 6 if _is_support_member(bot, user_id) else 3
        try:
            from database import guild_perks_for_user
            base += int((guild_perks_for_user(user_id) or {}).get("wishlist", 0))
        except Exception:
            pass
        return base
    @bot.tree.command(name="cardwish", description="Add or remove a card from your wishlist")
    @app_commands.describe(card="Card to add/remove from your wishlist")
    async def cardwish_cmd(interaction: discord.Interaction, card: str):
        from database import card_get_by_name, wishlist_toggle, wishlist_has, wishlist_list
        loc = locale_of(interaction)
        data = card_get_by_name(card.strip())
        if not data:
            await interaction.response.send_message(
                t("cards.wish.not_found", loc, name=card), ephemeral=True)
            return
        wl_max = _wishlist_max(interaction.user.id)
        # Cap: only when ADDING (toggling off is always allowed)
        if not wishlist_has(interaction.user.id, data["id"]):
            if len(wishlist_list(interaction.user.id)) >= wl_max:
                base = t("cards.wish.full", loc, max=wl_max)
                if wl_max >= 6:
                    await interaction.response.send_message(base, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        base + t("cards.wish.full_hint", loc),
                        view=_support_view(loc), ephemeral=True)
                return
        added = wishlist_toggle(interaction.user.id, data["id"])
        count = len(wishlist_list(interaction.user.id))
        emoji = RARITY_EMOJIS.get(data.get("rarity"), "⚪")
        key = "cards.wish.added" if added else "cards.wish.removed"
        msg = t(key, loc, name=data['name'], emoji=emoji, count=count, max=wl_max)
        await interaction.response.send_message(msg, ephemeral=True)

    @cardwish_cmd.autocomplete("card")
    async def cardwish_autocomplete(interaction: discord.Interaction, current: str):
        try:
            return _names_to_choices(_card_names_cached(True), current)
        except Exception as e:
            print(f"[cardwish ac] {type(e).__name__}: {e}")
            return []

    # === /cardwishlist [member]: view the wishlist ===
    @bot.tree.command(name="cardwishlist", description="View your wishlist (or a member's)")
    @app_commands.describe(member="Member whose wishlist to view (default: you)")
    async def cardwishlist_cmd(interaction: discord.Interaction, member: discord.Member = None):
        from database import wishlist_list, wishlist_toggle
        loc = locale_of(interaction)
        target = member or interaction.user
        is_self = (target.id == interaction.user.id)

        def _build_wl_panel():
            its = wishlist_list(target.id)
            body = ((t("cards.wishlist.empty", loc)
                     + (t("cards.wishlist.empty_hint", loc) if is_self else ""))
                    if not its else
                    "\n".join(
                        t("cards.wishlist.line", loc,
                          emoji=RARITY_EMOJIS.get(i['rarity'], '⚪'), name=i['name'],
                          universe=universe_label(i.get('universe'), loc) or '?')
                        for i in its[:40]))
            p = Panel(t("cards.wishlist.title", loc, name=target.display_name,
                        count=len(its), max=_wishlist_max(target.id)), body)
            if target.display_avatar:
                p.thumbnail(str(target.display_avatar.url))
            return p, its

        # Delete buttons (only on your own wishlist)
        class _WishlistView(discord.ui.LayoutView):
            def __init__(self, panel, wl_items, with_buttons=True):
                super().__init__(timeout=120)
                self.add_item(panel.container())
                if not with_buttons:
                    return
                # 25 buttons max = 5 ActionRows of 5 (V2 has no automatic wrapping)
                buttons = []
                for it in wl_items[:25]:
                    btn = discord.ui.Button(
                        label=t("cards.wishlist.remove_btn", loc, name=it['name'][:70])[:80],
                        style=discord.ButtonStyle.danger)
                    btn.callback = self._make_cb(it["card_id"])
                    buttons.append(btn)
                for i in range(0, len(buttons), 5):
                    self.add_item(row(*buttons[i:i + 5]))

            def _make_cb(self, card_id):
                async def _cb(inter: discord.Interaction):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message(
                            ti(inter, "cards.wishlist.not_yours"), ephemeral=True)
                        return
                    wishlist_toggle(interaction.user.id, card_id)  # remove
                    new_panel, new_items = _build_wl_panel()
                    await inter.response.edit_message(
                        view=_WishlistView(new_panel, new_items,
                                           with_buttons=bool(new_items)))
                return _cb

        panel, items = _build_wl_panel()
        await interaction.response.send_message(
            view=_WishlistView(panel, items, with_buttons=bool(is_self and items)))

    # === /cardtop <category>: leaderboards ===
    @bot.tree.command(name="cardtop", description="Card leaderboards (collection, mythics, essences, fusions, luck)")
    @app_commands.describe(category="Leaderboard type")
    @app_commands.choices(category=[
        app_commands.Choice(name="Collection value", value="value"),
        app_commands.Choice(name="Mythics", value="mythic"),
        app_commands.Choice(name="Essences", value="essences"),
        app_commands.Choice(name="Fusions (stars)", value="fusions"),
        app_commands.Choice(name="Luck index", value="luck"),
    ])
    async def cardtop_cmd(interaction: discord.Interaction,
                           category: app_commands.Choice[str] = None):
        from database import (leaderboard_card_aggregates, leaderboard_essences,
                               leaderboard_fusions)
        loc = locale_of(interaction)
        await interaction.response.defer()
        cat = category.value if category else "value"

        def _name(uid):
            try:
                m = interaction.guild.get_member(int(uid)) if interaction.guild else None
                if m:
                    return m.display_name
                u = bot.get_user(int(uid))
                return u.name if u else t("cards.top.unknown_player", loc, id=str(uid)[:6])
            except Exception:
                return t("cards.top.unknown_player", loc, id=str(uid)[:6])

        rows = []   # (uid, value_str, sort_key)
        title = t("cards.top.title", loc)
        if cat == "essences":
            title = t("cards.top.essences_title", loc)
            for uid, e in leaderboard_essences(10):
                rows.append((uid, t("cards.top.essences_value", loc, amount=e)))
        elif cat == "fusions":
            title = t("cards.top.fusions_title", loc)
            for uid, cards, stars in leaderboard_fusions(10):
                rows.append((uid, t("cards.top.fusions_value", loc, stars=stars, cards=cards)))
        else:
            agg = leaderboard_card_aggregates()
            if cat == "mythic":
                title = t("cards.top.mythic_title", loc)
                ranked = sorted(((u, d) for u, d in agg.items() if d["mythic"] > 0),
                                key=lambda x: x[1]["mythic"], reverse=True)[:10]
                for u, d in ranked:
                    rows.append((u, t("cards.top.mythic_value", loc, count=d['mythic'])))
            elif cat == "luck":
                title = t("cards.top.luck_title", loc)
                cand = []
                for u, d in agg.items():
                    if d["total"] >= 10:  # min 10 cards to be ranked
                        luck = max(0, min(100, round(d["pts"] / d["total"] / 3.85 * 50)))
                        cand.append((u, luck, d["total"]))
                cand.sort(key=lambda x: x[1], reverse=True)
                for u, luck, tot in cand[:10]:
                    rows.append((u, t("cards.top.luck_value", loc, luck=luck, total=tot)))
            else:  # value
                title = t("cards.top.value_title", loc)
                ranked = sorted(agg.items(), key=lambda x: x[1]["pts"], reverse=True)[:10]
                for u, d in ranked:
                    rows.append((u, t("cards.top.value_value", loc, points=d['pts'],
                                      total=d['total'])))

        medals = ["🥇", "🥈", "🥉"] + [f"`#{i}`" for i in range(4, 11)]
        if not rows:
            desc = t("cards.top.empty", loc)
        else:
            desc = "\n".join(t("cards.top.line", loc, medal=medals[i],
                                name=_name(uid), value=val)
                             for i, (uid, val) in enumerate(rows))
        p = Panel(title, desc)
        p.footer(t("cards.top.footer", loc))
        await interaction.followup.send(view=p.view())


    # === /bossspawn: spawn a boss (owner, test) ===
    @bot.tree.command(name="bossspawn", description="[Owner] Spawn a boss to fight in this channel")
    @app_commands.describe(tier="Boss difficulty (1 to 5)",
                            dummies="[Test] Number of dummy fighters to add (0-4)")
    @app_commands.choices(tier=[
        app_commands.Choice(name="Tier 1 (easy)", value=1),
        app_commands.Choice(name="Tier 2", value=2),
        app_commands.Choice(name="Tier 3", value=3),
        app_commands.Choice(name="Tier 4", value=4),
        app_commands.Choice(name="Tier 5 (raid)", value=5),
    ])
    async def bossspawn_cmd(interaction: discord.Interaction,
                             tier: app_commands.Choice[int] = None,
                             dummies: int = 0):
        loc = locale_of(interaction)
        if not _is_owner(interaction.user.id):
            await interaction.response.send_message(
                t("cards.boss.owner_only", loc), ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message(
                t("cards.boss.guild_only", loc), ephemeral=True)
            return
        from services.card_boss import spawn_boss, add_dummy_participants
        tv = tier.value if tier else 1
        await interaction.response.send_message(
            t("cards.boss.summoning", loc, tier=tv), ephemeral=True)
        bid = await spawn_boss(bot, interaction.guild.id, interaction.channel.id, tier=tv)
        if not bid:
            await interaction.followup.send(t("cards.boss.spawn_failed", loc), ephemeral=True)
            return
        d = max(0, min(4, int(dummies or 0)))
        if d:
            add_dummy_participants(bid, d)
            await interaction.followup.send(
                t("cards.boss.dummies_added", loc, count=d), ephemeral=True)


    # === /cardhelp: full guide of the card system ===
    @bot.tree.command(name="cardhelp", description="Full guide of the TookBot card system")
    async def cardhelp_cmd(interaction: discord.Interaction):
        loc = locale_of(interaction)
        p = Panel(t("cards.help.title", loc), t("cards.help.intro", loc))
        for _name_key, _val_key in (
            ("get_name", "get_value"),
            ("essences_name", "essences_value"),
            ("fusion_name", "fusion_value"),
            ("cosmetics_name", "cosmetics_value"),
            ("profile_name", "profile_value"),
            ("combat_name", "combat_value"),
            ("trade_name", "trade_value"),
            ("guild_name", "guild_value"),
        ):
            p.field(t("cards.help." + _name_key, loc), t("cards.help." + _val_key, loc))
        p.footer(t("cards.help.footer", loc))
        await interaction.response.send_message(
            view=p.view(row(_support_button(loc))), ephemeral=True)


    # === /cardshop: weekly shop (6 slots) ===
    @bot.tree.command(name="cardshop", description="Card and cosmetics shop (Essences ✨)")
    async def cardshop_cmd(interaction: discord.Interaction):
        from database import card_shop_get_slots, currency_get
        from services.card_shop import build_shop_image, purchase_slot
        import os as _os
        loc = locale_of(interaction)
        await interaction.response.defer()
        slots = card_shop_get_slots()
        active = [s for s in slots if s.get("enabled") and s.get("item_type") and s.get("item_ref")]
        if not active:
            await interaction.followup.send(
                t("cards.shop.empty", loc), ephemeral=True)
            return
        try:
            rel = build_shop_image()
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[cardshop] build_shop_image err: {e!r}")
            rel = None
        bal = currency_get(interaction.user.id)
        _slot_by_n = {int(s["slot"]): s for s in slots}
        # one ephemeral panel per player (avoids spam when slots are re-clicked)
        _panels = {}

        def _do_purchase(user_id, slot_n, qty):
            """Buy the slot qty times. Returns (bought, last_res, err)."""
            bought = 0; last = None; err = None
            for _ in range(qty):
                res = purchase_slot(user_id, slot_n)
                if res.get("ok"):
                    bought += 1; last = res
                else:
                    err = res.get("error"); break
            return bought, last, err

        def _purchase_msg(bought, last, err):
            if not bought:
                return t("cards.shop.purchase_failed", loc,
                         error=err or t("cards.shop.purchase_failed_default", loc))
            total = last["price"] * bought
            qtxt = f"{bought}× " if bought > 1 else ""
            msg = t("cards.shop.purchased", loc, qty=qtxt, name=last['item_name'],
                    total=total, balance=last['new_balance'])
            if last["item_type"] == "border":
                msg += t("cards.shop.border_hint", loc)
            if err:
                msg += t("cards.shop.partial", loc, count=bought, error=err)
            return msg

        class _QtySelect(discord.ui.Select):
            def __init__(self, slot_n):
                unit = int((_slot_by_n.get(slot_n) or {}).get("price") or 0)
                opts = [discord.SelectOption(
                            label=t("cards.shop.qty_option", loc, qty=i, total=i * unit),
                            value=str(i))
                        for i in range(1, 17)]
                super().__init__(placeholder=t("cards.shop.qty_placeholder", loc), options=opts,
                                 min_values=1, max_values=1, custom_id="shop_qty")
                self.slot_n = slot_n

            async def callback(self, inter: discord.Interaction):
                qty = int(self.values[0])
                bought, last, err = _do_purchase(inter.user.id, self.slot_n, qty)
                # edit the SAME ephemeral message (no spam)
                await inter.response.edit_message(content=_purchase_msg(bought, last, err),
                                                  view=self.view)

        class _QtyView(discord.ui.View):
            def __init__(self, slot_n):
                super().__init__(timeout=300)
                self.add_item(_QtySelect(slot_n))

        class _ShopView(discord.ui.LayoutView):
            def __init__(self, panel):
                super().__init__(timeout=300)
                self.add_item(panel.container())
                row0, row1 = [], []
                for s in slots:
                    n = int(s["slot"])
                    enabled = bool(s.get("enabled") and s.get("item_type") and s.get("item_ref"))
                    _slot_lbl = t("cards.shop.slot_label", loc, n=n)
                    label = (s.get("label") or _slot_lbl)[:40]
                    price = int(s.get("price") or 0)
                    btn = discord.ui.Button(
                        label=f"{label} · {price} ✨" if enabled else _slot_lbl,
                        style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
                        disabled=not enabled,
                        custom_id=f"shop_buy_{n}",
                    )
                    btn.callback = self._make_cb(n)
                    (row0 if n <= 3 else row1).append(btn)
                for chunk in (row0, row1):
                    if chunk:
                        self.add_item(row(*chunk))

            def _make_cb(self, slot_n):
                async def _cb(inter: discord.Interaction):
                    s = _slot_by_n.get(slot_n) or {}
                    name = (s.get("label") or t("cards.shop.slot_label", loc, n=slot_n))[:60]
                    price = int(s.get("price") or 0)
                    content = t("cards.shop.buy_prompt", loc, name=name, price=price)
                    view = _QtyView(slot_n)
                    # reuse the player's existing ephemeral panel (no spam)
                    prev = _panels.get(inter.user.id)
                    if prev is not None:
                        try:
                            await prev.edit_original_response(content=content, view=view)
                            await inter.response.defer()  # silent ack, no new message
                            return
                        except (discord.NotFound, discord.HTTPException):
                            _panels.pop(inter.user.id, None)
                    await inter.response.send_message(content, view=view, ephemeral=True)
                    _panels[inter.user.id] = inter
                return _cb

        p = Panel(t("cards.shop.title", loc), t("cards.shop.desc", loc, balance=bal))
        file = None
        if rel:
            local_path = _os.path.join(
                _REPO_ROOT, rel.lstrip("/").replace("/", _os.sep))
            if _os.path.exists(local_path):
                file = discord.File(local_path, filename="cardshop.png")
                p.image("attachment://cardshop.png")
            else:
                print(f"[cardshop] image generated but not found: {local_path}")
        else:
            print("[cardshop] build_shop_image returned None")
        view = _ShopView(p)
        if file:
            await interaction.followup.send(view=view, file=file)
        else:
            await interaction.followup.send(view=view)


    # === /cardtrade <user> ===
    def _parse_card_list(s: str) -> list[tuple[str, int]]:
        """Parse 'Name1, Name2 x2, Name3' -> [(name, qty), ...]. Cap qty 1-99."""
        out = []
        if not s: return out
        for part in s.split(","):
            p = part.strip()
            if not p: continue
            qty = 1
            # Suffix 'xN' or ' xN'
            import re as _re
            m = _re.match(r"^(.*?)\s*[xX]\s*(\d{1,2})\s*$", p)
            if m:
                p = m.group(1).strip()
                qty = max(1, min(int(m.group(2)), 99))
            if p:
                out.append((p, qty))
        return out

    def _resolve_card_names(items: list[tuple[str, int]]) -> tuple[list, list]:
        """Resolve names -> [(card_id, qty)]. Returns (ok, errors)."""
        resolved = []; errors = []
        for name, qty in items:
            card = card_get_by_name(name)
            if not card:
                errors.append(f"`{name}`")
                continue
            resolved.append((card["id"], qty))
        return resolved, errors

    def _verify_ownership(user_id, items: list[tuple[int, int]], locale=None) -> list[str]:
        """Returns a list of errors if the user doesn't own the requested qty."""
        errs = []
        # Aggregate per card_id (in case the same card appears twice in the list)
        agg = {}
        for cid, qty in items:
            agg[cid] = agg.get(cid, 0) + qty
        for cid, qty in agg.items():
            owned = user_card_count_owned(user_id, cid, only_tradeable=True)
            if owned < qty:
                from database import get_db
                conn = get_db(); cc = conn.cursor()
                r = cc.execute("SELECT name FROM cards WHERE id = ?", (cid,)).fetchone()
                conn.close()
                nm = r["name"] if r else f"#{cid}"
                errs.append(t("cards.trade.owned_ratio", locale, name=nm,
                              owned=owned, qty=qty))
        return errs

    def _build_trade_panel(trade_id: int, sender: discord.Member,
                             receiver: discord.Member, status: str = "pending",
                             locale=None, ping: str | None = None) -> Panel:
        offer = card_trade_items(trade_id, side="offer")
        request = card_trade_items(trade_id, side="request")

        def _fmt(items):
            if not items:
                return t("cards.trade.nothing", locale)
            lines = []
            for it in items:
                em = RARITY_EMOJIS.get(it["rarity"], "⚪")
                qty = f" ×{it['qty']}" if it["qty"] > 1 else ""
                lines.append(f"{em} **{it['name']}**{qty}")
            return "\n".join(lines)

        _status_key = {
            "pending":   "cards.trade.status_pending",
            "accepted":  "cards.trade.status_accepted",
            "refused":   "cards.trade.status_refused",
            "cancelled": "cards.trade.status_cancelled",
            "countered": "cards.trade.status_countered",
        }.get(status)
        status_label = t(_status_key, locale) if _status_key else status

        # The title MUST keep the "Trade #<id>" marker: the persistent view
        # parses it back on every click.
        p = Panel(t("cards.trade.embed_title", locale, trade_id=trade_id, status=status_label))
        # A V2 message has no `content=`: the receiver ping becomes a panel block.
        if ping:
            p.text(ping)
        p.field(t("cards.trade.field_offer", locale, name=sender.display_name),
                _fmt(offer)[:1024])
        p.field(t("cards.trade.field_request", locale, name=receiver.display_name),
                _fmt(request)[:1024])
        p.footer(f"{sender} ↔ {receiver}")
        return p

    def _trade_ctx(interaction):
        """(trade_id, trade, sender_id, receiver_id) or None if not found.
        The trade id is re-read from the panel text (marker "Trade #N")."""
        import re as _re_t
        m = _re_t.search(r"Trade #(\d+)", _v2_message_text(interaction.message))
        if not m:
            return None
        tid = int(m.group(1))
        trade = card_trade_get(tid)
        if not trade:
            return None
        return tid, trade, int(trade["sender_id"]), int(trade["receiver_id"])

    def _trade_members(interaction, sender_id, receiver_id):
        """Resolve both sides, falling back to the clicker when out of cache."""
        g = interaction.guild
        sender = (g.get_member(sender_id) if g else None) or interaction.user
        receiver = (g.get_member(receiver_id) if g else None) or interaction.user
        return sender, receiver

    class _TradeRow(discord.ui.ActionRow):
        """Decision row. custom_ids kept identical to the pre-V2 version."""

        @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅",
                           custom_id="trade_accept")
        async def accept_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
            loc = locale_of(interaction)
            ctx = _trade_ctx(interaction)
            if not ctx:
                await interaction.response.send_message(
                    t("cards.trade.gone", loc), ephemeral=True); return
            tid, trade, sender_id, receiver_id = ctx
            if interaction.user.id != receiver_id:
                await interaction.response.send_message(
                    t("cards.trade.only_receiver_accept", loc), ephemeral=True)
                return
            if trade["status"] != "pending":
                await interaction.response.send_message(
                    t("cards.trade.not_active", loc), ephemeral=True)
                return
            offer = card_trade_items(tid, side="offer")
            request = card_trade_items(tid, side="request")
            sender_items = [(it["card_id"], it["qty"]) for it in offer]
            recv_items = [(it["card_id"], it["qty"]) for it in request]
            err_s = _verify_ownership(sender_id, sender_items, locale=loc)
            err_r = _verify_ownership(receiver_id, recv_items, locale=loc)
            sender, receiver = _trade_members(interaction, sender_id, receiver_id)
            if err_s or err_r:
                msg = t("cards.trade.missing_cards", loc)
                if err_s:
                    msg += t("cards.trade.missing_line", loc, user_id=sender_id,
                             list=", ".join(err_s))
                if err_r:
                    msg += t("cards.trade.missing_line", loc, user_id=receiver_id,
                             list=", ".join(err_r))
                card_trade_set_status(tid, "cancelled")
                # A V2 message cannot fall back to `content`: replace the panel.
                # content/embeds cleared so a pre-V2 message can be edited too.
                await interaction.response.edit_message(
                    content=None, embeds=[],
                    view=_build_trade_panel(tid, sender, receiver, "cancelled",
                                            locale=loc).view(timeout=None))
                await interaction.followup.send(msg,
                    allowed_mentions=discord.AllowedMentions.none())
                return
            for cid, qty in sender_items:
                for _ in range(qty):
                    user_card_transfer_one(sender_id, receiver_id, cid)
            for cid, qty in recv_items:
                for _ in range(qty):
                    user_card_transfer_one(receiver_id, sender_id, cid)
            card_trade_set_status(tid, "accepted")
            await interaction.response.edit_message(
                content=None, embeds=[],
                view=_build_trade_panel(tid, sender, receiver, "accepted",
                                        locale=loc).view(timeout=None))

        @discord.ui.button(label="Refuse", style=discord.ButtonStyle.danger, emoji="❌",
                           custom_id="trade_refuse")
        async def refuse_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
            loc = locale_of(interaction)
            ctx = _trade_ctx(interaction)
            if not ctx:
                await interaction.response.send_message(
                    t("cards.trade.gone", loc), ephemeral=True); return
            tid, trade, sender_id, receiver_id = ctx
            if interaction.user.id not in (sender_id, receiver_id):
                await interaction.response.send_message(
                    t("cards.trade.not_involved", loc), ephemeral=True)
                return
            if trade["status"] != "pending":
                await interaction.response.send_message(
                    t("cards.trade.not_active", loc), ephemeral=True)
                return
            new_status = "cancelled" if interaction.user.id == sender_id else "refused"
            card_trade_set_status(tid, new_status)
            sender, receiver = _trade_members(interaction, sender_id, receiver_id)
            await interaction.response.edit_message(
                content=None, embeds=[],
                view=_build_trade_panel(tid, sender, receiver, new_status,
                                        locale=loc).view(timeout=None))

        @discord.ui.button(label="Counter-offer", style=discord.ButtonStyle.secondary, emoji="🔄",
                           custom_id="trade_counter")
        async def counter_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
            loc = locale_of(interaction)
            ctx = _trade_ctx(interaction)
            if not ctx:
                await interaction.response.send_message(
                    t("cards.trade.gone", loc), ephemeral=True); return
            tid, trade, sender_id, receiver_id = ctx
            if interaction.user.id != receiver_id:
                await interaction.response.send_message(
                    t("cards.trade.only_receiver_counter", loc), ephemeral=True)
                return
            if trade["status"] != "pending":
                await interaction.response.send_message(
                    t("cards.trade.not_active", loc), ephemeral=True)
                return
            modal = TradeModal(target_user_id=sender_id,
                                 is_counter=True, original_trade_id=tid,
                                 view_to_disable=None, locale=loc)
            await interaction.response.send_modal(modal)

    class _TradeCardsRow(discord.ui.ActionRow):
        """Second row: 'View cards' + the dashboard link.
        custom_id kept identical to the pre-V2 version."""

        @discord.ui.button(label="View cards", style=discord.ButtonStyle.secondary, emoji="🃏",
                           custom_id="trade_view_cards")
        async def view_cards_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
            loc = locale_of(interaction)
            ctx = _trade_ctx(interaction)
            if not ctx:
                await interaction.response.send_message(
                    t("cards.trade.gone", loc), ephemeral=True); return
            tid, trade, sender_id, receiver_id = ctx
            sender = interaction.guild.get_member(sender_id) if interaction.guild else None
            receiver = interaction.guild.get_member(receiver_id) if interaction.guild else None
            sname = sender.display_name if sender else t("cards.trade.the_sender", loc)
            rname = receiver.display_name if receiver else t("cards.trade.the_recipient", loc)
            entries = _trade_card_entries(tid, sname, rname, locale=loc)
            if not entries:
                await interaction.response.send_message(
                    t("cards.trade.no_cards_to_show", loc), ephemeral=True)
                return
            await interaction.response.send_message(
                view=TradeCardsNavView(_trade_card_panel(tid, 0, entries, locale=loc)),
                ephemeral=True)

    _TRADE_DASHBOARD_URL = "https://dashboard.tookbot.click"

    class TradeView(discord.ui.LayoutView):
        """Persistent (timeout=None, fixed custom_ids). A single instance registered
        at boot handles ALL trades. The state (trade_id) is re-read from the panel
        title (Trade #N), sender/receiver from the DB -> survives restarts."""
        def __init__(self, panel=None, locale=None, trade_id=None):
            super().__init__(timeout=None)
            self.add_item((panel or Panel()).container())
            r1 = _TradeRow()
            r1.accept_btn.label = t("cards.trade.btn_accept", locale)
            r1.refuse_btn.label = t("cards.trade.btn_refuse", locale)
            r1.counter_btn.label = t("cards.trade.btn_counter", locale)
            self.add_item(r1)
            r2 = _TradeCardsRow()
            r2.view_cards_btn.label = t("cards.trade.btn_view_cards", locale)
            if trade_id is not None:
                # 'Open on the dashboard' link button (page /cards/trade/<id>)
                r2.add_item(discord.ui.Button(
                    label=t("cards.trade.btn_dashboard", locale),
                    style=discord.ButtonStyle.link,
                    url=f"{_TRADE_DASHBOARD_URL}/cards/trade/{trade_id}", emoji="🎛️"))
            self.add_item(r2)

    # Register the persistent trade view (survives pm2 restarts)
    try:
        bot.add_view(TradeView())
    except Exception as _e:
        print(f"[cards] add_view TradeView: {_e}")

    def _trade_view(panel, tid, locale=None):
        """Trade panel + decision buttons + 'Open on the dashboard' link button."""
        return TradeView(panel, locale=locale, trade_id=tid)

    # web -> bot hook: the dashboard "Send" button posts the trade panel in the
    # cards channel (same panel + same TradeView as /cardtrade).
    async def _hook_post_trade(bot_, gid, payload):
        tid = int(payload.get("trade_id") or 0)
        trade = card_trade_get(tid)
        if not trade:
            raise RuntimeError(f"trade #{tid} not found")
        guild = bot_.get_guild(int(gid))
        if not guild:
            raise RuntimeError(f"guild {gid} not found")
        cfg = guild_card_config_get(gid) or {}
        ch_id = cfg.get("channel_id")
        channel = guild.get_channel(int(ch_id)) if ch_id else None
        if not channel:
            raise RuntimeError("cards channel not configured (/cardsetup)")
        sender = guild.get_member(int(trade["sender_id"])) or await bot_.fetch_user(int(trade["sender_id"]))
        receiver = guild.get_member(int(trade["receiver_id"])) or await bot_.fetch_user(int(trade["receiver_id"]))
        _loc = guild_locale(gid) or DEFAULT_LOCALE
        panel = _build_trade_panel(tid, sender, receiver, "pending", locale=_loc,
                                   ping=f"<@{trade['receiver_id']}>")
        msg = await channel.send(
            view=_trade_view(panel, tid, locale=_loc),
            allowed_mentions=discord.AllowedMentions(users=True))
        card_trade_set_status(tid, "pending", message_id=msg.id)

    try:
        from services.bot_command_hooks import register as _reg_hook
        _reg_hook("post_trade", _hook_post_trade)
    except Exception as _e:
        print(f"[cards] register post_trade hook: {_e}")


    class TradeModal(discord.ui.Modal, title="Propose a trade"):
        offer_field = discord.ui.TextInput(
            label="Your cards (leave empty to give nothing)",
            placeholder="Naruto Uzumaki, Goku x2, Vegeta",
            required=False, max_length=400, style=discord.TextStyle.paragraph,
        )
        request_field = discord.ui.TextInput(
            label="Cards you want (leave empty for a gift)",
            placeholder="Gojo Satoru, Itadori Yuji",
            required=False, max_length=400, style=discord.TextStyle.paragraph,
        )

        def __init__(self, target_user_id: int, is_counter: bool = False,
                      original_trade_id: int | None = None,
                      view_to_disable=None, locale=None):
            super().__init__()
            self.target_user_id = int(target_user_id)
            self.is_counter = is_counter
            self.original_trade_id = original_trade_id
            self.view_to_disable = view_to_disable
            self.locale = locale
            self.title = t("cards.trade.modal_title", locale)
            self.offer_field.label = t("cards.trade.offer_label", locale)
            self.offer_field.placeholder = t("cards.trade.offer_placeholder", locale)
            self.request_field.label = t("cards.trade.request_label", locale)
            self.request_field.placeholder = t("cards.trade.request_placeholder", locale)
            # Pre-fill with the original trade content (roles swapped for a counter)
            if is_counter and original_trade_id:
                def _fmt(items):
                    parts = []
                    for it in items:
                        if it["qty"] > 1:
                            parts.append(f"{it['name']} x{it['qty']}")
                        else:
                            parts.append(it["name"])
                    return ", ".join(parts)
                orig_offer = card_trade_items(original_trade_id, side="offer")
                orig_request = card_trade_items(original_trade_id, side="request")
                # Counter sender = original receiver. Their 'offer' (what they
                # give) = what was requested from them = orig_request.
                # Their 'request' (what they want) = what was offered to them
                # = orig_offer.
                self.offer_field.default = _fmt(orig_request)
                self.request_field.default = _fmt(orig_offer)
                self.title = t("cards.trade.modal_counter_title", locale,
                               trade_id=original_trade_id)

        async def on_submit(self, interaction: discord.Interaction):
            loc = locale_of(interaction)
            try:
                offer_parsed = _parse_card_list(str(self.offer_field.value or ""))
                request_parsed = _parse_card_list(str(self.request_field.value or ""))
                # At least ONE of the two sides must have a card (one-way gift is OK)
                if not offer_parsed and not request_parsed:
                    await interaction.response.send_message(
                        t("cards.trade.need_one_card", loc), ephemeral=True)
                    return

                # Resolve names -> ids
                offer_items, errs1 = _resolve_card_names(offer_parsed)
                request_items, errs2 = _resolve_card_names(request_parsed)
                if errs1 or errs2:
                    msg = t("cards.trade.cards_not_found", loc,
                            list=", ".join(errs1 + errs2))
                    await interaction.response.send_message(msg, ephemeral=True)
                    return

                # Verify ownership
                sender_id = interaction.user.id
                receiver_id = self.target_user_id
                err_s = _verify_ownership(sender_id, offer_items, locale=loc)
                err_r = _verify_ownership(receiver_id, request_items, locale=loc)
                if err_s:
                    await interaction.response.send_message(
                        t("cards.trade.you_dont_own", loc, list=", ".join(err_s)),
                        ephemeral=True)
                    return
                if err_r:
                    await interaction.response.send_message(
                        t("cards.trade.receiver_doesnt_own", loc, list=", ".join(err_r)),
                        ephemeral=True)
                    return

                # Counter-offer: mark the old trade + strip the buttons off the
                # original message. A V2 message cannot go back to `content`, so
                # the whole view is replaced by the panel alone.
                if self.is_counter and self.original_trade_id:
                    card_trade_set_status(self.original_trade_id, "countered")
                    try:
                        if interaction.message:
                            _orig = card_trade_get(self.original_trade_id) or {}
                            _os_id = int(_orig.get("sender_id") or 0)
                            _or_id = int(_orig.get("receiver_id") or 0)
                            _osender, _oreceiver = _trade_members(interaction, _os_id, _or_id)
                            await interaction.message.edit(
                                content=None, embeds=[],
                                view=_build_trade_panel(
                                    self.original_trade_id, _osender, _oreceiver,
                                    "countered", locale=loc,
                                    ping=f"<@{_or_id}>" if _or_id else None,
                                ).view(timeout=None))
                    except Exception:
                        pass

                gid = interaction.guild.id if interaction.guild else None
                cid = interaction.channel.id if interaction.channel else None
                tid = card_trade_create(sender_id, receiver_id, gid, cid,
                                          offer_items, request_items)

                receiver_member = interaction.guild.get_member(receiver_id) if interaction.guild else None
                if not receiver_member:
                    await interaction.response.send_message(
                        t("cards.trade.receiver_not_found", loc), ephemeral=True)
                    card_trade_set_status(tid, "cancelled")
                    return

                panel = _build_trade_panel(tid, interaction.user, receiver_member, "pending",
                                           locale=loc, ping=receiver_member.mention)
                await interaction.response.send_message(
                    view=_trade_view(panel, tid, locale=loc),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
                msg = await interaction.original_response()
                card_trade_set_status(tid, "pending", message_id=msg.id)
            except Exception as e:
                import traceback; traceback.print_exc()
                try:
                    await interaction.response.send_message(
                        t("cards.trade.create_failed", loc), ephemeral=True)
                except Exception:
                    pass


    # === /cardsuggest: community suggestion (support guild only) ===
    SUGGEST_CHANNEL_ID = 1513592894265757716
    VOTE_UP = "🔼"; VOTE_DOWN = "🔽"

    async def _add_vote_reactions(msg):
        try:
            await msg.add_reaction(VOTE_UP)
            await msg.add_reaction(VOTE_DOWN)
        except Exception as e:
            print(f"[suggest votes] add react err: {e}")
    SUPPORT_GUILD_ID = int((os.getenv("SUPPORT_GUILD_ID") or "0").strip() or 0)

    @bot.tree.command(name="cardsuggest",
                       description="Suggest a character to add to the catalog (support server)")
    @app_commands.describe(
        name="Character name",
        universe="Category",
        origin="Source anime/game/movie (e.g. Naruto, Genshin Impact)",
        rarity="Suggested rarity (optional)",
        image_url="Image URL (optional if you attach an image)",
        image="Image attachment (optional if a URL is provided)",
    )
    @app_commands.choices(universe=list(_UNIVERSE_CHOICES))
    @app_commands.choices(rarity=list(_RARITY_CHOICES))
    async def cardsuggest(interaction: discord.Interaction,
                            name: str,
                            universe: app_commands.Choice[str],
                            origin: str = None,
                            rarity: app_commands.Choice[str] = None,
                            image_url: str = None,
                            image: discord.Attachment = None):
        # /cardsuggest is available everywhere (all servers + DMs)
        loc = locale_of(interaction)

        # Resolve image
        final_url = None
        source_type = None
        if image:
            ct = (image.content_type or "").lower()
            if not ct.startswith("image/"):
                await interaction.response.send_message(
                    t("cards.suggest.attachment_not_image", loc), ephemeral=True)
                return
            if image.size > 8 * 1024 * 1024:
                await interaction.response.send_message(
                    t("cards.suggest.image_too_big", loc), ephemeral=True)
                return
            final_url = await _persist_attachment(image)  # ephemeral URLs expire -> we host it
            if not final_url:
                await interaction.response.send_message(
                    t("cards.suggest.upload_failed", loc), ephemeral=True)
                return
            source_type = "upload"
        elif image_url:
            url = image_url.strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                await interaction.response.send_message(
                    t("cards.suggest.bad_url", loc), ephemeral=True)
                return
            final_url = url
            source_type = "url"
        else:
            await interaction.response.send_message(
                t("cards.suggest.need_image", loc), ephemeral=True)
            return

        name_clean = name.strip()[:100]
        if not name_clean:
            await interaction.response.send_message(
                t("cards.suggest.bad_name", loc), ephemeral=True)
            return

        try:
            sid = card_suggestion_add(
                suggester_id=interaction.user.id,
                suggester_name=str(interaction.user),
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel.id if interaction.channel else None,
                name=name_clean,
                universe=universe.value,
                subtitle=(origin or "").strip()[:80] or None,
                image_url=final_url,
                source_type=source_type,
                proposed_rarity=rarity.value if rarity else None,
            )
        except Exception as e:
            print(f"[cardsuggest] save err: {e!r}")
            await interaction.response.send_message(
                t("cards.suggest.save_failed", loc), ephemeral=True)
            return

        # Panel forwarded to the support channel
        _rar_line = (t("cards.suggest.rarity_line", loc, rarity=rarity.value)
                     if rarity else "")
        p = Panel(
            t("cards.suggest.title", loc, id=sid),
            t("cards.suggest.desc", loc, name=name_clean,
              universe=universe.value,
              origin=(" · " + origin) if origin else "",
              rarity_line=_rar_line),
        )
        p.image(final_url)
        p.footer(t("cards.suggest.footer", loc, name=interaction.user.display_name))

        # Forward to the support channel
        support_channel = bot.get_channel(SUGGEST_CHANNEL_ID)
        forward_ok = False
        if support_channel:
            try:
                fmsg = await support_channel.send(view=p.view(timeout=None))
                from database import card_suggestion_set_forward
                card_suggestion_set_forward(sid, fmsg.id)
                await _add_vote_reactions(fmsg)
                forward_ok = True
            except Exception as e:
                print(f"[cardsuggest] forward err: {e}")

        # Ephemeral reply to the user (nothing publicly visible)
        msg = t("cards.suggest.sent", loc, id=sid)
        if not forward_ok:
            msg += t("cards.suggest.forward_failed", loc)
        await interaction.response.send_message(msg, ephemeral=True)

    # === /cardmodify: suggest an edit to an EXISTING card ===
    @bot.tree.command(name="cardmodify",
                       description="Suggest an edit to an existing card (rarity, universe, image…)")
    @app_commands.describe(
        card="EXACT name of the existing card to edit",
        rarity="New rarity (optional)",
        universe="New universe (optional)",
        origin="New origin (optional)",
        new_name="New name (optional)",
        image_url="New image by URL (optional)",
        image="New image as an attachment (optional)",
    )
    @app_commands.choices(universe=list(_UNIVERSE_CHOICES))
    @app_commands.choices(rarity=list(_RARITY_CHOICES_WITH_SECRET))
    async def cardmodify(interaction: discord.Interaction,
                          card: str,
                          rarity: app_commands.Choice[str] = None,
                          universe: app_commands.Choice[str] = None,
                          origin: str = None,
                          new_name: str = None,
                          image_url: str = None,
                          image: discord.Attachment = None):
        from database import card_get_by_name, card_suggestion_add
        loc = locale_of(interaction)
        data = card_get_by_name(card.strip())
        if not data:
            await interaction.response.send_message(
                t("cards.modify.not_found", loc, name=card), ephemeral=True)
            return
        # Optional image (URL or attachment)
        final_url = None
        source_type = None
        if image:
            ct = (image.content_type or "").lower()
            if not ct.startswith("image/"):
                await interaction.response.send_message(
                    t("cards.modify.attachment_not_image", loc), ephemeral=True)
                return
            if image.size > 8 * 1024 * 1024:
                await interaction.response.send_message(
                    t("cards.modify.image_too_big", loc), ephemeral=True)
                return
            final_url = await _persist_attachment(image)  # ephemeral URLs expire -> we host it
            if not final_url:
                await interaction.response.send_message(
                    t("cards.modify.upload_failed", loc), ephemeral=True)
                return
            source_type = "upload"
        elif image_url:
            u = image_url.strip()
            if not (u.startswith("http://") or u.startswith("https://")):
                await interaction.response.send_message(
                    t("cards.modify.bad_url", loc), ephemeral=True)
                return
            final_url = u
            source_type = "url"
        new_rar = rarity.value if rarity else None
        new_uni = universe.value if universe else None
        new_origin = (origin or "").strip() or None
        new_name = (new_name or "").strip()[:100] or None
        if not any([new_rar, new_uni, new_origin, new_name, final_url]):
            await interaction.response.send_message(
                t("cards.modify.need_change", loc), ephemeral=True)
            return
        try:
            sid = card_suggestion_add(
                suggester_id=interaction.user.id,
                suggester_name=str(interaction.user),
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel.id if interaction.channel else None,
                name=new_name or data["name"],   # NOT NULL: new name, else the current one
                universe=new_uni,
                subtitle=new_origin,
                image_url=final_url,
                source_type=source_type or "url",
                suggestion_type="edit",
                target_card_id=data["id"],
                proposed_rarity=new_rar,
            )
        except Exception as e:
            print(f"[cardmodify] save err: {e!r}")
            await interaction.response.send_message(
                t("cards.modify.save_failed", loc), ephemeral=True)
            return
        # Summary of the proposed changes
        changes = []
        if new_name:
            changes.append(t("cards.modify.change_name", loc, value=new_name))
        if new_rar:
            changes.append(t("cards.modify.change_rarity", loc, value=new_rar))
        if new_uni:
            changes.append(t("cards.modify.change_universe", loc, value=new_uni))
        if new_origin:
            changes.append(t("cards.modify.change_origin", loc, value=new_origin))
        if final_url:
            changes.append(t("cards.modify.change_image", loc))
        p = Panel(
            t("cards.modify.title", loc, id=sid, name=data['name']),
            t("cards.modify.desc", loc, card_id=data['id'],
              rarity=data.get('rarity', '?'),
              changes="\n".join(f"• {c}" for c in changes)))
        # Image: the new one if provided, otherwise the card's CURRENT image
        img_for_panel = final_url
        panel_file = None
        if not img_for_panel:
            _u, _f = _resolve_card_image(data)
            if _u:
                img_for_panel = _u
            elif _f:
                panel_file = _f
        if img_for_panel:
            p.image(img_for_panel)
        elif panel_file:
            p.image("attachment://card.png")
        p.footer(t("cards.modify.footer", loc, name=interaction.user.display_name))
        support_channel = bot.get_channel(SUGGEST_CHANNEL_ID)
        forward_ok = False
        if support_channel:
            try:
                if panel_file:
                    fmsg = await support_channel.send(view=p.view(timeout=None), file=panel_file)
                else:
                    fmsg = await support_channel.send(view=p.view(timeout=None))
                from database import card_suggestion_set_forward
                card_suggestion_set_forward(sid, fmsg.id)
                await _add_vote_reactions(fmsg)
                forward_ok = True
            except Exception as e:
                print(f"[cardmodify] forward err: {e}")
        msg = t("cards.modify.sent", loc, id=sid)
        if not forward_ok:
            msg += t("cards.modify.forward_failed", loc)
        await interaction.response.send_message(msg, ephemeral=True)

    @cardmodify.autocomplete("card")
    async def cardmodify_autocomplete(interaction: discord.Interaction, current: str):
        try:
            return _names_to_choices(_card_names_cached(), current)
        except Exception as e:
            print(f"[cardmodify ac] {type(e).__name__}: {e}")
            return []

    @bot.tree.command(name="cardtrade", description="Propose a card trade to another player")
    @app_commands.describe(player="Player to propose the trade to")
    async def cardtrade(interaction: discord.Interaction, player: discord.Member):
        loc = locale_of(interaction)
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    t("cards.channel.restricted_short", loc, channel=target),
                    ephemeral=True)
                return
        if player.id == interaction.user.id:
            await interaction.response.send_message(
                t("cards.trade.self_trade", loc), ephemeral=True)
            return
        if player.bot:
            await interaction.response.send_message(
                t("cards.trade.bot_trade", loc), ephemeral=True)
            return
        # Before building: show both players' binders so the offer can be prepared.
        _dash = os.getenv("DASHBOARD_URL", "https://dashboard.tookbot.click").rstrip("/")
        view = discord.ui.View(timeout=300)
        _build_btn = discord.ui.Button(label=t("cards.trade.btn_build", loc),
                                       style=discord.ButtonStyle.success, row=0)
        async def _open_builder(inter: discord.Interaction):
            if inter.user.id != interaction.user.id:
                await inter.response.send_message(
                    ti(inter, "cards.trade.not_your_trade"), ephemeral=True); return
            await inter.response.send_modal(
                TradeModal(target_user_id=player.id, locale=locale_of(inter)))
        _build_btn.callback = _open_builder
        view.add_item(_build_btn)
        _gid = interaction.guild.id if interaction.guild else ""
        view.add_item(discord.ui.Button(
            label=t("cards.trade.btn_open_dashboard", loc), style=discord.ButtonStyle.link,
            url=f"{_dash}/cards/trade?with={player.id}&guild={_gid}", row=0))
        view.add_item(discord.ui.Button(
            label=t("cards.trade.btn_their_binder", loc, name=player.display_name)[:80],
            style=discord.ButtonStyle.link,
            url=f"{_dash}/cards/collection/{player.id}", row=1))
        view.add_item(discord.ui.Button(
            label=t("cards.trade.btn_my_binder", loc), style=discord.ButtonStyle.link,
            url=f"{_dash}/cards/collection/{interaction.user.id}", row=1))
        await interaction.response.send_message(
            t("cards.trade.intro", loc, name=player.display_name),
            view=view, ephemeral=True)
