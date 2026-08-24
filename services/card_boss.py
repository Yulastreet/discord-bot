"""Cooperative boss fight (automatic).

Flow:
1. The boss spawns -> recruiting phase.
2. Players click "Join" (stats come from their collection) and can
   "Choose my card" (sets the combat element).
3. The fight starts 2 min after the spawn, or 10 s once 5 players have joined.
4. AUTOMATIC turn based fight: the team hits, the boss hits back, until the boss
   dies or the team is wiped. Loot for the participants.
"""
from __future__ import annotations

import asyncio
import random

import discord

from database import (
    BOSS_TIERS, card_boss_create, card_boss_get, card_boss_set_message,
    card_boss_apply_damage, card_boss_set_status, card_boss_set_start,
    boss_participant_add, boss_participant_get, boss_participants_list,
    boss_participant_update, compute_player_combat_stats, engaged_combat_stats,
    element_matchup, BOSS_TIER_SCALE, card_boss_set_stats,
    card_pick_random_exact_rarity, card_get, card_get_by_name,
    user_card_add, user_card_count_owned, CARD_ELEMENT_LABELS, element_weaknesses,
    CARD_ELEMENTS, combat_power, boss_event_add,
)
import os as _os
import time as _t

from services.i18n import t, ti, guild_locale, locale_of


def _bloc(boss):
    """Locale for background embeds (no interaction): guild override, else EN."""
    try:
        return guild_locale((boss or {}).get("guild_id")) or "en"
    except Exception:
        return "en"


_RECRUIT_SECONDS = 120     # fight delay after the 1st player joined
_JOIN_EXPIRE = 900         # if nobody joins, the boss vanishes (15 min)
_QUICK_START_AT = 5        # player count that triggers the quick start
_MAX_PLAYERS = 5           # team capped at 5 players
# Weight of the fusion bonus INSIDE the boss scaling (0 = fusion does not inflate the
# boss at all, 1 = counts fully). The player keeps his full bonus for HIS OWN damage.
_FUSION_BOSS_SCALE_WEIGHT = 0.75
_QUICK_SECONDS = 60        # quick start delay
_TURN_DELAY = 4.8          # seconds between 2 auto turns
_MAX_TURNS = 60
_BOSS_RATIO = 0.5          # the boss hits at 50% of its atk

_RARITY_RANK = {"common": 0, "rare": 1, "epic": 2, "legendary": 3, "mythic": 4, "secret": 5}
_SORT_CYCLE = [None, "name", "rarity", "stars", "optimal"]
_SORT_EMOJI = {None: "🔃", "name": "🔤", "rarity": "🎯", "stars": "⭐", "optimal": "⚡"}
_SORT_KEY = {None: "guilds.boss.sort_none", "name": "guilds.boss.sort_name",
             "rarity": "guilds.boss.sort_rarity", "stars": "guilds.boss.sort_stars",
             "optimal": "guilds.boss.sort_optimal"}


def _card_atk_mult(c):
    """REAL ATK multiplier of the card = rarity x stars, with the special case
    secret 5★ = 999. A common 5★ can beat an epic 0★."""
    from database import CARD_RARITY_COMBAT_MULT, CARD_STAR_COMBAT_BONUS
    rar = c.get("rarity") or ""
    stars = int(c.get("stars", 0))
    if rar == "secret" and stars >= 5:
        return 999.0
    rar_mult = CARD_RARITY_COMBAT_MULT.get(rar, 1.0)
    star_mult = 1.0 + min(5, stars) * CARD_STAR_COMBAT_BONUS
    return rar_mult * star_mult


def _card_effectiveness(c, boss_element, guild_id=None):
    """Total effectiveness against THIS boss = ATK mult (rarity x stars) x element
    bonus x event card bonus (+15% while the event runs). Mirrors the real damage."""
    eff = _card_atk_mult(c) * element_matchup(c.get("element") or "", boss_element or "")
    if guild_id is not None:
        eff *= _event_boss_dmg_mult(c, guild_id)
    return eff


def _sort_cards(rows, mode, boss_element=None, guild_id=None):
    if mode == "name":
        return sorted(rows, key=lambda c: c["name"].lower())
    if mode == "rarity":
        return sorted(rows, key=lambda c: -_RARITY_RANK.get(c.get("rarity", ""), 0))
    if mode == "stars":
        return sorted(rows, key=lambda c: -int(c.get("stars", 0)))
    if mode == "optimal":
        # sort by the real effectiveness (real ATK mult x element advantage x event bonus):
        # a less rare but more starred card goes first when it hits harder.
        return sorted(rows, key=lambda c: -_card_effectiveness(c, boss_element, guild_id))
    return rows

# Rarity range of the boss "avatar" card, per tier
_TIER_RANGE = {
    1: ["common", "rare"],
    2: ["rare", "epic"],
    3: ["rare", "epic", "legendary"],
    4: ["epic", "legendary", "mythic"],
    5: ["legendary", "mythic", "secret"],
}
# Loot rarity = the top of the tier range
def _tier_loot_rarity(tier):
    return _TIER_RANGE.get(tier, ["epic"])[-1]

# Extra difficulty from the avatar rarity WITHIN the tier range.
# Multiplicative: factor = step^notch, applied to the boss HP AND ATK (on top of the
# team scaling). The higher the tier, the harsher the gap between rarities.
# T5: Legendary x1.0, Mythic x1.5, Secret x2.25. Tunable.
_TIER_RARITY_STEP = {1: 1.10, 2: 1.12, 3: 1.15, 4: 1.25, 5: 1.50}


def _avatar_difficulty(tier, idx):
    return _TIER_RARITY_STEP.get(tier, 1.12) ** idx


_RARITY_ORDER = ["common", "rare", "epic", "legendary", "mythic", "secret"]


def _avatar_idx(tier, rarity):
    """Difficulty notch of the rarity inside the tier range. Outside the range
    (rarity forced from the dashboard): clamp on the global rank."""
    rng = _TIER_RANGE.get(tier, ["epic"])
    if rarity in rng:
        return rng.index(rarity)
    try:
        rank = _RARITY_ORDER.index(rarity)
    except ValueError:
        return 0
    rng_ranks = [_RARITY_ORDER.index(r) for r in rng if r in _RARITY_ORDER]
    if rng_ranks and rank >= max(rng_ranks):
        return len(rng) - 1
    return 0


# Combat abilities (5 distinct roles, each with a trade-off).
# The dict keys are the ability ids stored in DB - never translate them.
_APT_LABEL_KEY = {
    "berserker": "guilds.boss.apt_berserker", "gardien": "guilds.boss.apt_gardien",
    "soigneur": "guilds.boss.apt_soigneur", "duelliste": "guilds.boss.apt_duelliste",
    "executeur": "guilds.boss.apt_executeur",
}
_APT_EMOJI = {
    "berserker": "🩸", "gardien": "🛡️", "soigneur": "💚",
    "duelliste": "⚔️", "executeur": "💀",
}


def _apt_label(apt, locale="en"):
    """Localized display label of an ability id (None when there is no ability)."""
    key = _APT_LABEL_KEY.get(apt or "")
    return t(key, locale) if key else None


# Berserker: +ATK / +damage taken
_BERSERK_ATK = 1.30
_BERSERK_TAKEN = 1.25
# Guardian: -damage taken / -ATK. On top: intercepts the devastating blow and
# covers the team (-10% AoE damage aura while a Guardian is alive).
_GARDIEN_TAKEN = 0.70
_GARDIEN_ATK = 0.85
_GARDIEN_AURA = 0.90   # AoE damage multiplier for the WHOLE team while a Guardian holds
# Healer: heals the most wounded / -ATK
_SOIGNEUR_ATK = 0.85
_SOIGNEUR_HEAL = 0.08     # % of max HP given back to the most wounded, per team turn
_SOIGNEUR_CRIT = 0.05     # ~5% chance the heal is doubled
# Duelist: amplified element advantage
_DUELLISTE_ADV = 1.50     # replaces the x1.25 when you have the advantage
# Executioner: +ATK while the boss is enraged (<50% HP)
_EXECUTEUR_ATK = 1.40


def _apt_atk_mult(apt, matchup, boss_enraged):
    """Offensive ATK multiplier coming from the ability (base matchup excluded)."""
    if apt == "berserker":
        return _BERSERK_ATK
    if apt == "gardien":
        return _GARDIEN_ATK
    if apt == "soigneur":
        return _SOIGNEUR_ATK
    if apt == "executeur" and boss_enraged:
        return _EXECUTEUR_ATK
    return 1.0


def _apt_matchup(apt, matchup):
    """Effective matchup: the Duelist amplifies the element advantage."""
    if apt == "duelliste" and matchup > 1.0:
        return _DUELLISTE_ADV
    return matchup


def _apt_taken_mult(apt):
    """Multiplier of the damage TAKEN coming from the ability."""
    if apt == "berserker":
        return _BERSERK_TAKEN
    if apt == "gardien":
        return _GARDIEN_TAKEN
    return 1.0


def _apt_badge(apt):
    e = _APT_EMOJI.get(apt)
    return f" {e}" if e else ""


def _build_battlefield(bid):
    """Compose the battlefield over the bossfightbg.png background: 5 player cards
    on top, VS in the middle, boss at the bottom. Player cards with border + stars.
    Returns the local path or None."""
    import os
    from PIL import Image
    from services.card_render import _ROOT, _load_base
    from services.card_profile import _card_image_for
    try:
        boss = card_boss_get(bid)
        parts = boss_participants_list(bid)
        bg_path = os.path.join(_ROOT, "assets", "cardrelated", "bossfightbg.png")
        if os.path.exists(bg_path):
            canvas = Image.open(bg_path).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (1672, 941), (24, 16, 32, 255))
        W, H = canvas.size

        # Uniform sizes for the players + boss
        cw, ch = 250, 375
        gap = 30

        def _place_card(img, x, y):
            r = img.convert("RGBA").resize((cw, ch), Image.LANCZOS)
            canvas.paste(r, (int(x), int(y)), r)

        # Player cards (top, max 5) with border + stars
        top_y = 25
        n = min(5, len(parts))
        if n:
            total_w = n * cw + (n - 1) * gap
            x0 = (W - total_w) // 2
            for i, p in enumerate(parts[:5]):
                img = None
                if p.get("card_id"):
                    img = _card_image_for(p["user_id"], int(p["card_id"]), allow_alt=True)
                    if img is None:
                        img = _load_base(int(p["card_id"]), None)
                if img is not None:
                    _place_card(img, x0 + i * (cw + gap), top_y)

        # Boss (bottom center, same size). LOCAL RENDER FIRST for the avatar card
        # (dead link proof): _load_base looks for card_renders/<card_id> and only
        # falls back to the http image_url when there is no local render.
        by = H - ch - 25
        bx = (W - cw) // 2
        bcid = boss.get("card_id")
        bimg = _load_base(int(bcid) if bcid else 0, boss.get("image_url"))
        if bimg is not None:
            _place_card(bimg, bx, by)

        out_dir = os.path.join(_ROOT, "static", "card_boss")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{bid}.png")
        canvas.convert("RGB").save(out, "PNG", optimize=True)
        return out
    except Exception as e:
        print(f"[boss] battlefield err: {e}")
        return None


def _cemoji(bot, name, fallback):
    """Custom emoji from the support server (resolved by name), else fallback."""
    try:
        e = discord.utils.get(bot.emojis, name=name)
        if e:
            return str(e)
    except Exception:
        pass
    return fallback


def _power_compact(n) -> str:
    """>=1M -> 'XmYYY' (millions + m + thousands, units dropped). E.g. 1345986 -> '1m345'.
    Otherwise the full number. The 'm' = custom million emoji."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n // 1_000_000}m{(n // 1000) % 1000:03d}"
    return str(n)


def _power_digits(bot, n, suffix="_") -> str:
    """Number -> custom digit emojis from the support server ONLY.
    suffix='_' -> '0_'..'9_' (players) + compact million format (emoji 'm_').
    suffix='boss' -> '0boss'..'9boss', NO million emoji -> full number.
    Restricted to the support server (short names collide)."""
    sg = int((_os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip() or 0)
    guild = bot.get_guild(sg) if sg else None
    by_name = {}
    if guild:
        for e in guild.emojis:
            by_name[e.name.lower()] = str(e)
    # boss: no million emoji -> full number. Players: compact format.
    s = str(int(n)) if suffix == "boss" else _power_compact(n)
    out = []
    for ch in s:
        if ch == "m":
            out.append(by_name.get("m_", "M"))
        elif ch.isdigit():
            out.append(by_name.get(f"{ch}{suffix}", ch))
        else:
            out.append(ch)
    return "".join(out)


def _boss_image_url(boss):
    """Displayable http URL for the boss image. Remote image_url used as-is, else
    resolve the local avatar render through the domain (PUBLIC_BASE_URL).
    Hosted cards carry a relative /static image_url that set_image refuses."""
    img = boss.get("image_url") or ""
    if isinstance(img, str) and img.startswith("http"):
        return img
    try:
        from commandes.cards import _resolve_card_image
        cid = boss.get("card_id")
        card = card_get(cid) if cid else None
        if not card and cid:
            card = {"id": cid, "image_url": img}
        if card:
            url, _file = _resolve_card_image(card)
            if url:
                return url
    except Exception:
        pass
    return None


def _bar(bot, cur, mx, enraged=False, segments=15):
    cur = max(0, cur)
    filled = min(segments, int(round(segments * cur / mx))) if mx > 0 else 0
    pct = int(round(100 * cur / mx)) if mx > 0 else 0
    # Custom emojis: full / enraged (remaining chunks) / empty (lost chunks)
    full = _cemoji(bot, "lifebardechaine" if enraged else "lifebarfull", "🟥")
    empty = _cemoji(bot, "lifebarempty", "⬛")
    return full * filled + empty * (segments - filled) + f"  **{pct}%**"


def _fit_segments(n_players: int) -> int:
    """Number of player HP bar chunks that fit under the 1024/field limit.
    Bar emojis are heavy (~39 chars), so shrink when there are many players."""
    if n_players <= 1:
        return 8
    budget = max(120, 1000 // n_players - 80)
    return max(3, min(8, budget // 39))


def _player_bar(bot, cur, mx, segments=8):
    cur = max(0, cur)
    filled = min(segments, int(round(segments * cur / mx))) if mx > 0 else 0
    full = _cemoji(bot, "playerlifebarfull", "🟩")
    empty = _cemoji(bot, "lifebarempty", "⬛")
    return full * filled + empty * (segments - filled)


def _default_card(user_id):
    """Default battle card: the profile 'middle' card, else the 1st one owned."""
    from database import card_profile_get, get_db
    prof = card_profile_get(user_id) or {}
    cid = prof.get("mid_id") or prof.get("left_id") or prof.get("right_id")
    if cid:
        card = card_get(int(cid))
        if card:
            return card
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT c.* FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                  "WHERE uc.user_id = ? LIMIT 1", (str(user_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def _elem(bot, e):
    try:
        from commandes.cards import _get_element_emoji
        return _get_element_emoji(bot, e)
    except Exception:
        return ""


def _fmt(n):
    return f"{int(n):,}".replace(",", " ")


def _default_element(user_id):
    from database import card_profile_get, get_db
    prof = card_profile_get(user_id) or {}
    cid = prof.get("mid_id") or prof.get("left_id") or prof.get("right_id")
    if cid:
        card = card_get(int(cid))
        if card and card.get("element"):
            return card["element"]
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT c.element FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                  "WHERE uc.user_id = ? AND c.element IS NOT NULL LIMIT 1",
                  (str(user_id),)).fetchone()
    conn.close()
    return r["element"] if r else "eclat"


def build_boss_embed(bot, boss, phase_text="", log=None, battle=False, locale=None):
    boss = card_boss_get(boss["id"])
    loc = locale or _bloc(boss)
    parts = boss_participants_list(boss["id"])
    color = {"defeated": 0x4ade80, "wiped": 0xff3d57}.get(boss["status"], 0x8e44ad)
    embed = discord.Embed(
        title=t("guilds.boss.title", loc,
                tier=BOSS_TIERS.get(boss['tier'], {}).get('label', ''), name=boss['name']),
        color=color)
    embed.add_field(name=t("guilds.boss.f_element", loc),
                    value=f"{_elem(bot, boss['element'])} {CARD_ELEMENT_LABELS.get(boss['element'],'?')}",
                    inline=True)
    weak = element_weaknesses(boss["element"])
    weak_txt = " ".join(f"{_elem(bot, w)} {CARD_ELEMENT_LABELS.get(w,'?')}" for w in weak) or "—"
    embed.add_field(name=t("guilds.boss.f_weak", loc), value=weak_txt, inline=True)
    embed.add_field(name=t("guilds.boss.f_atk", loc), value=f"🗡️ {_fmt(boss['atk'])}", inline=True)
    _enraged = boss["status"] == "fighting" and boss["hp"] < boss["max_hp"] * 0.5
    boss_pw = _power_digits(bot, combat_power(boss['max_hp'], boss['atk']), suffix="boss")
    embed.add_field(name=t("guilds.boss.f_hp", loc, hp=_fmt(boss['hp']), max=_fmt(boss['max_hp'])),
                    value=_bar(bot, boss['hp'], boss['max_hp'], enraged=_enraged)
                          + "\n" + t("guilds.boss.power_line", loc, power=boss_pw),
                    inline=False)
    # Info (recruiting / result) RIGHT UNDER the HP bar
    info = ""
    if phase_text:
        info = phase_text
    elif boss["status"] == "recruiting":
        if boss.get("start_at"):
            info = t("guilds.boss.recruiting", loc, ts=int(boss['start_at']),
                     seconds=_QUICK_SECONDS, players=_QUICK_START_AT)
        else:
            info = t("guilds.boss.waiting", loc)
    elif boss["status"] == "defeated":
        info = t("guilds.boss.defeated", loc)
    elif boss["status"] == "wiped":
        info = t("guilds.boss.wiped", loc)
    if info:
        embed.add_field(name="​", value=info, inline=False)
    if parts:
        plist = parts[:12]
        in_battle = bool(battle) or boss["status"] == "fighting"
        if in_battle:
            # IN FIGHT: 10 chunk HP bar + exact HP going down.
            blocks = []
            for p in plist:
                ko = " 💀" if p["hp"] <= 0 else ""
                mx = p.get("max_hp") or p["hp"]
                bar = _player_bar(bot, p["hp"], mx, 10)
                blocks.append(
                    f"{_elem(bot, p['element'])} **{p['name']}**{_apt_badge(p.get('aptitude'))}{ko} "
                    f"🗡️ {_fmt(p['atk'])}\n"
                    f"HP ❤️ {bar} **{_fmt(max(0, p['hp']))}**")
            embed.add_field(name=t("guilds.boss.f_team", loc, count=len(parts)),
                            value="​", inline=False)
            if len(blocks) <= 8:
                # 1 field per player -> uniform spacing
                for b in blocks:
                    embed.add_field(name="​", value=b, inline=False)
            else:
                # too many players: group them to stay under 25 fields / 6000 chars
                cur = ""
                for b in blocks:
                    add = ("\n" if cur else "") + b
                    if cur and len(cur) + len(add) > 950:
                        embed.add_field(name="​", value=cur, inline=False); cur = b
                    else:
                        cur += add
                if cur:
                    embed.add_field(name="​", value=cur, inline=False)
        else:
            # PREP: 3 inline columns aligned by Discord, power under the nickname.
            # Graceful degradation vs the 1024 limit: emojis -> text -> nothing (power
            # always visible even with 5 players or 7 digit power values).
            def _name_cells(power_mode):  # 'emoji' | 'plain' | None
                cells = []
                for p in plist:
                    ko = " 💀" if p["hp"] <= 0 else ""
                    line = f"{_elem(bot, p['element'])} **{p['name']}**{_apt_badge(p.get('aptitude'))}{ko}"
                    pw = combat_power(p.get('max_hp') or p['hp'], p['atk'])
                    if power_mode == "emoji":
                        line += f"\n⚡{_power_digits(bot, pw)}"
                    elif power_mode == "plain":
                        line += f"\n⚡ {_fmt(pw)}"
                    cells.append(line)
                return cells
            mode = "emoji"
            name_cells = _name_cells("emoji")
            if len("​\n" + "\n".join(name_cells)) > 1000:
                mode = "plain"
                name_cells = _name_cells("plain")
                if len("​\n" + "\n".join(name_cells)) > 1000:
                    mode = None
                    name_cells = _name_cells(None)
            pad = "\n​" if mode else ""
            hp_cells = [f"{_fmt(max(0, p['hp']))}{pad}" for p in plist]
            atk_cells = [f"{_fmt(p['atk'])}{pad}" for p in plist]
            embed.add_field(name=t("guilds.boss.f_team", loc, count=len(parts)),
                            value="​\n" + "\n".join(name_cells), inline=True)
            embed.add_field(name=t("guilds.boss.f_hp_col", loc),
                            value="​\n" + "\n".join(hp_cells), inline=True)
            embed.add_field(name=t("guilds.boss.f_atk_col", loc),
                            value="​\n" + "\n".join(atk_cells), inline=True)
        total_pw = sum(combat_power(p.get('max_hp') or p['hp'], p['atk']) for p in parts)
        embed.add_field(name=t("guilds.boss.f_party_power", loc),
                        value=_power_digits(bot, total_pw), inline=False)
    if log:
        embed.add_field(name=t("guilds.boss.f_log", loc), value="\n".join(log[-4:]), inline=False)
    # Image: battlefield during the fight, boss card while recruiting
    if battle:
        embed.set_image(url="attachment://battle.png")
    elif boss["status"] == "recruiting":
        burl = _boss_image_url(boss)
        if burl:
            embed.set_image(url=burl)
    return embed


def _boss_live_url(boss_id):
    base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/cards/boss/{int(boss_id)}" if base else None


class JoinView(discord.ui.View):
    def __init__(self, boss_id, locale="en"):
        super().__init__(timeout=None)
        self.boss_id = boss_id
        self.locale = locale or "en"
        # custom_id of the buttons must never change: already posted messages rely on it
        self.join.label = t("guilds.boss.btn_join", self.locale)
        self.card_btn.label = t("guilds.boss.btn_card", self.locale)
        self.apt_btn.label = t("guilds.boss.btn_ability", self.locale)
        # Link button -> live fight on the dashboard (real time animations)
        url = _boss_live_url(boss_id)
        if url:
            self.add_item(discord.ui.Button(label=t("guilds.boss.btn_live", self.locale),
                                            style=discord.ButtonStyle.link, url=url))

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="🛡️",
                       custom_id="boss_join")
    async def join(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.recruit_over"), ephemeral=True)
            return
        uid = interaction.user.id
        if boss_participant_get(self.boss_id, uid):
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.already_in"), ephemeral=True)
            return
        if len(boss_participants_list(self.boss_id)) >= _MAX_PLAYERS:
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.team_full", max=_MAX_PLAYERS), ephemeral=True)
            return
        dcard = _default_card(uid)
        delem = (dcard.get("element") if dcard else None) or "eclat"
        dcid = dcard["id"] if dcard else None
        stats = engaged_combat_stats(uid, dcid) if dcid else compute_player_combat_stats(uid)
        d_atk = int(stats["atk"] * (_event_boss_dmg_mult(dcard, boss.get("guild_id")) if dcard else 1.0))
        boss_participant_add(self.boss_id, uid, interaction.user.display_name,
                             delem, stats["hp"], d_atk, card_id=dcid)
        # 1st player -> starts the 2 min timer
        if not boss.get("start_at"):
            card_boss_set_start(self.boss_id, _t.time() + _RECRUIT_SECONDS)
        # Live scaling: the boss grows with the current team (preview while recruiting)
        _scale_boss_to_team(self.boss_id)
        await interaction.response.send_message(
            ti(interaction, "guilds.boss.joined"), ephemeral=True)
        try:
            await interaction.message.edit(
                embed=build_boss_embed(interaction.client, card_boss_get(self.boss_id),
                                       locale=self.locale), view=self)
        except Exception:
            pass

    def _check(self, interaction):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            return ti(interaction, "guilds.boss.recruit_over")
        if not boss_participant_get(self.boss_id, interaction.user.id):
            return ti(interaction, "guilds.boss.join_first")
        return None

    @discord.ui.button(label="Card", style=discord.ButtonStyle.secondary, emoji="🎴",
                       custom_id="boss_card")
    async def card_btn(self, interaction, btn):
        err = self._check(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        # defer: loading the whole collection can blow past the 3s window (otherwise
        # "Interaction failed"). The ephemeral is then sent as a followup.
        await interaction.response.defer(ephemeral=True, thinking=True)
        loc = locale_of(interaction)
        view = _CardPickerView(self.boss_id, interaction.user.id, interaction.client, locale=loc)
        await interaction.followup.send(content=t("guilds.boss.picker_content", loc),
                                        embed=view.build_embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Ability", style=discord.ButtonStyle.secondary, emoji="🩸",
                       custom_id="boss_apt")
    async def apt_btn(self, interaction, btn):
        err = self._check(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        p = boss_participant_get(self.boss_id, interaction.user.id)
        loc = locale_of(interaction)
        await interaction.response.send_message(
            _aptitude_text(loc, _apt_label(p.get("aptitude"), loc)),
            view=_AptitudeView(self.boss_id, loc), ephemeral=True)


def _aptitude_text(locale, cur_apt_label=None):
    base = t("guilds.boss.apt_text", locale)
    if cur_apt_label:
        base += t("guilds.boss.apt_current", locale, ability=cur_apt_label)
    return base


async def _refresh_boss_msg(client, boss_id):
    """Refresh the embed of the boss main message (called from the ephemeral)."""
    boss = card_boss_get(boss_id)
    if not boss or not boss.get("message_id"):
        return
    ch = client.get_channel(int(boss["channel_id"]))
    if not ch:
        return
    try:
        m = await ch.fetch_message(int(boss["message_id"]))
        await m.edit(embed=build_boss_embed(client, boss, locale=_bloc(boss)))
    except Exception:
        pass


class _AptitudeView(discord.ui.View):
    def __init__(self, boss_id, locale="en"):
        super().__init__(timeout=300)
        self.boss_id = boss_id
        self.locale = locale or "en"
        loc = self.locale
        # option values = ability ids stored in DB, never translated
        self.pick_apt.placeholder = t("guilds.boss.apt_placeholder", loc)
        self.pick_apt.options = [
            discord.SelectOption(label=t("guilds.boss.apt_berserker", loc), value="berserker",
                                 emoji="🩸", description=t("guilds.boss.apt_desc_berserker", loc)),
            discord.SelectOption(label=t("guilds.boss.apt_gardien", loc), value="gardien",
                                 emoji="🛡️", description=t("guilds.boss.apt_desc_gardien", loc)),
            discord.SelectOption(label=t("guilds.boss.apt_soigneur", loc), value="soigneur",
                                 emoji="💚", description=t("guilds.boss.apt_desc_soigneur", loc)),
            discord.SelectOption(label=t("guilds.boss.apt_duelliste", loc), value="duelliste",
                                 emoji="⚔️", description=t("guilds.boss.apt_desc_duelliste", loc)),
            discord.SelectOption(label=t("guilds.boss.apt_executeur", loc), value="executeur",
                                 emoji="💀", description=t("guilds.boss.apt_desc_executeur", loc)),
            discord.SelectOption(label=t("guilds.boss.apt_none", loc), value="none",
                                 emoji="➖", description=t("guilds.boss.apt_desc_none", loc)),
        ]

    @discord.ui.select(placeholder="Choose an ability…", min_values=1, max_values=1,
                       options=[discord.SelectOption(label="None", value="none", emoji="➖")])
    async def pick_apt(self, interaction, select):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.recruit_over"), ephemeral=True); return
        if not boss_participant_get(self.boss_id, interaction.user.id):
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.join_first"), ephemeral=True); return
        val = select.values[0]
        boss_participant_update(self.boss_id, interaction.user.id,
                                aptitude=("" if val == "none" else val))
        loc = self.locale
        await interaction.response.edit_message(
            content=_aptitude_text(loc, _apt_label(val, loc)), view=self)
        await _refresh_boss_msg(interaction.client, self.boss_id)


class _ElementFilterSelect(discord.ui.Select):
    def __init__(self, current, locale="en"):
        opts = [discord.SelectOption(label=t("guilds.boss.elem_all", locale), value="all",
                                     default=(current is None))]
        for e in CARD_ELEMENTS:
            opts.append(discord.SelectOption(label=CARD_ELEMENT_LABELS.get(e, e),
                                             value=e, default=(current == e)))
        super().__init__(placeholder=t("guilds.boss.elem_placeholder", locale),
                         min_values=1, max_values=1, options=opts, row=0)

    async def callback(self, interaction):
        view = self.view
        v = self.values[0]
        view.element = None if v == "all" else v
        view.page = 1
        view._load()
        view._sync_dynamic()
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class _PageCardSelect(discord.ui.Select):
    def __init__(self, page_rows, locale="en"):
        from commandes.cards import RARITY_EMOJIS
        opts = []
        for c in page_rows[:25]:
            cnt = f" x{c['count']}" if c.get("count", 1) > 1 else ""
            st = int(c.get("stars", 0))
            star_lbl = f" {st}★" if st else ""
            desc = f"{(c.get('rarity') or '?')}{(' '+st*'⭐') if st else ''} · {(c.get('universe') or '?')}"[:100]
            opts.append(discord.SelectOption(
                label=f"{c['name']}{star_lbl}{cnt}"[:100], value=str(c["card_id"]),
                emoji=RARITY_EMOJIS.get(c.get("rarity"), "⚪"), description=desc))
        if not opts:
            opts = [discord.SelectOption(label=t("guilds.boss.no_card_option", locale),
                                         value="none")]
        super().__init__(placeholder=t("guilds.boss.page_placeholder", locale),
                         min_values=1, max_values=1, options=opts, row=1)

    async def callback(self, interaction):
        if self.values[0] == "none":
            await interaction.response.defer(); return
        from database import card_get
        card = card_get(int(self.values[0]))
        if not card:
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.card_not_found"), ephemeral=True); return
        await _apply_card_choice(interaction, self.view.boss_id, card)


class _CardPickerView(discord.ui.View):
    def __init__(self, boss_id, user_id, bot, element=None, sort_mode=None, locale="en"):
        super().__init__(timeout=300)
        self.boss_id = boss_id
        self.user_id = user_id
        self.bot = bot
        self.element = element
        self.sort_mode = sort_mode
        self.locale = locale or "en"
        self.confirm.label = t("guilds.boss.btn_by_name", self.locale)
        self.page = 1
        self._load()
        self._sync_dynamic()

    def _load(self):
        from database import user_card_list, user_card_fusion_map
        cards = user_card_list(self.user_id)
        fmap = user_card_fusion_map(self.user_id)
        grouped = {}
        for c in cards:
            if self.element and (c.get("element") or "") != self.element:
                continue
            cid = c["card_id"]
            if cid not in grouped:
                grouped[cid] = {**c, "count": 0, "stars": int(fmap.get(cid, 0))}
            grouped[cid]["count"] += 1
        boss = card_boss_get(self.boss_id)
        boss_elem = boss["element"] if boss else None
        self.boss_element = boss_elem
        self.boss_guild_id = boss.get("guild_id") if boss else None
        self.rows = _sort_cards(list(grouped.values()), self.sort_mode,
                                boss_element=boss_elem, guild_id=self.boss_guild_id)
        self.total_pages = max(1, (len(self.rows) + 24) // 25)
        if self.page > self.total_pages:
            self.page = self.total_pages

    def _sync_dynamic(self):
        # rebuild the 2 selects (element filter + cards of the current page)
        for it in list(self.children):
            if isinstance(it, (_ElementFilterSelect, _PageCardSelect)):
                self.remove_item(it)
        self.add_item(_ElementFilterSelect(self.element, self.locale))
        page_rows = self.rows[(self.page - 1) * 25: self.page * 25]
        self.add_item(_PageCardSelect(page_rows, self.locale))
        self._refresh()

    def _refresh(self):
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.total_pages
        self.counter.label = f"{self.page} / {self.total_pages}"
        self.sort_btn.label = (f"{_SORT_EMOJI[self.sort_mode]} "
                               f"{t(_SORT_KEY[self.sort_mode], self.locale)}")

    def build_embed(self):
        from commandes.cards import RARITY_EMOJIS
        page_rows = self.rows[(self.page - 1) * 25: self.page * 25]
        opt_mode = self.sort_mode == "optimal" and self.boss_element
        lines = []
        for i, c in enumerate(page_rows):
            emoji = RARITY_EMOJIS.get(c["rarity"], "⚪")
            el = _elem(self.bot, c.get("element"))
            pre = f"{emoji}｜{el}" if el else emoji
            cnt = f" x{c['count']}" if c["count"] > 1 else ""
            stars = "⭐" * int(c.get("stars", 0))
            uni = c.get("universe") or "?"
            if opt_mode:
                m = element_matchup(c.get("element") or "", self.boss_element)
                match_tag = " 🔥" if m > 1 else (" 🟦" if m < 1 else "")
                # real effectiveness score = rarity x stars x element bonus
                eff = _card_effectiveness(c, self.boss_element, getattr(self, "boss_guild_id", None))
                crown = "👑 " if (self.page == 1 and i == 0) else ""
                score = f" `×{eff:.2f}`"
                lines.append(f"{crown}{pre} **{c['name']}**{stars}{cnt}{match_tag}{score} · _{uni}_")
            else:
                lines.append(f"{pre} **{c['name']}**{stars}{cnt} · _{uni}_")
        loc = self.locale
        all_lbl = t("guilds.boss.picker_all", loc)
        elem_lbl = CARD_ELEMENT_LABELS.get(self.element, all_lbl) if self.element else all_lbl
        if self.sort_mode == "optimal":
            boss_lbl = CARD_ELEMENT_LABELS.get(self.boss_element, "?") if self.boss_element else "?"
            sort_lbl = t("guilds.boss.picker_optimal", loc, element=boss_lbl)
        elif self.sort_mode:
            sort_lbl = t("guilds.boss.picker_sorted", loc,
                         mode=t(_SORT_KEY[self.sort_mode], loc))
        else:
            sort_lbl = ""
        head = t("guilds.boss.picker_head", loc, count=len(self.rows),
                 element=elem_lbl, sort=sort_lbl)
        if opt_mode:
            head += t("guilds.boss.picker_note", loc)
        embed = discord.Embed(
            title=t("guilds.boss.picker_title", loc), color=0x8e44ad,
            description=head + "\n\n" + ("\n".join(lines) if lines
                                         else t("guilds.boss.picker_empty", loc)))
        footer_extra = t("guilds.boss.picker_footer_extra", loc) if opt_mode else ""
        embed.set_footer(text=t("guilds.boss.picker_footer", loc, page=self.page,
                                total=self.total_pages, extra=footer_extra))
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=2)
    async def prev_btn(self, interaction, btn):
        if self.page > 1:
            self.page -= 1; self._sync_dynamic()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.primary, disabled=True, row=2)
    async def counter(self, interaction, btn):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_btn(self, interaction, btn):
        if self.page < self.total_pages:
            self.page += 1; self._sync_dynamic()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="🔃 Sort", style=discord.ButtonStyle.secondary, row=2)
    async def sort_btn(self, interaction, btn):
        idx = _SORT_CYCLE.index(self.sort_mode)
        self.sort_mode = _SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)]
        self._load()
        self.page = 1
        self._sync_dynamic()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Pick by name", style=discord.ButtonStyle.success, emoji="🎴", row=3)
    async def confirm(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.recruit_over"), ephemeral=True); return
        await interaction.response.send_modal(_ChooseCardModal(self.boss_id, self.locale))


class _ChooseCardModal(discord.ui.Modal):
    def __init__(self, boss_id, locale="en"):
        super().__init__(title=t("guilds.boss.modal_title", locale))
        self.boss_id = boss_id
        self.locale = locale or "en"
        self.card_name = discord.ui.TextInput(
            label=t("guilds.boss.modal_label", locale),
            placeholder=t("guilds.boss.modal_placeholder", locale),
            required=True, max_length=100)
        self.add_item(self.card_name)

    async def on_submit(self, interaction):
        card = card_get_by_name(str(self.card_name.value).strip())
        if not card:
            await interaction.response.send_message(
                ti(interaction, "guilds.boss.card_not_found"), ephemeral=True); return
        await _apply_card_choice(interaction, self.boss_id, card)


# Damage bonus of event cards against bosses (ONLY while the event runs).
_EVENT_BOSS_DMG_MULT = 1.15


def _event_boss_dmg_mult(card, guild_id) -> float:
    """1.15 when the card belongs to the active event (on this server), else 1.0."""
    try:
        ek = (card.get("event_key") or "")
        if not ek:
            return 1.0
        from database import global_event_for_guild
        ev = global_event_for_guild(guild_id)
        if ev.get("active") and ev.get("key") == ek:
            return _EVENT_BOSS_DMG_MULT
    except Exception:
        pass
    return 1.0


async def _apply_card_choice(interaction, boss_id, card):
    """Engage the `card` for the player: recompute ATK/HP, update the team, confirm."""
    uid = interaction.user.id
    loc = locale_of(interaction)
    boss = card_boss_get(boss_id)
    if not boss or boss["status"] != "recruiting":
        await interaction.response.send_message(
            t("guilds.boss.recruit_over", loc), ephemeral=True); return
    if user_card_count_owned(uid, card["id"]) <= 0:
        await interaction.response.send_message(
            t("guilds.boss.not_owned", loc, card=card['name']), ephemeral=True); return
    elem = card.get("element") or "eclat"
    stats = engaged_combat_stats(uid, card["id"])
    # Event bonus (+15% damage during the event for the event cards)
    ev_mult = _event_boss_dmg_mult(card, boss.get("guild_id"))
    atk = int(stats["atk"] * ev_mult)
    # Recompute HP/ATK from the engaged card (full HP since we are still recruiting)
    boss_participant_update(boss_id, uid, element=elem, card_id=card["id"],
                            atk=atk, max_hp=stats["hp"], hp=stats["hp"])
    m = element_matchup(elem, boss["element"]) if boss else 1.0
    if m > 1:
        match_txt = t("guilds.boss.matchup_adv", loc)
    elif m < 1:
        match_txt = t("guilds.boss.matchup_dis", loc)
    else:
        match_txt = t("guilds.boss.matchup_neutral", loc)
    # ATK breakdown: rarity + fusion stars
    if stats.get("secret_max"):
        calc = "🌈 secret 5⭐ MAX = **×999**"
    else:
        calc = f"{stats['rarity'] or '?'} ×{stats['rar_mult']:.2f}"
        if stats["stars"] > 0:
            calc += (f" · {stats['stars']}⭐ +{int(stats['stars'] * 20)}% "
                     f"(×{stats['star_mult']:.2f})")
        calc += f" = **×{stats['mult']:.2f}**"
    ev_txt = t("guilds.boss.event_bonus", loc) if ev_mult > 1 else ""
    await interaction.response.send_message(
        t("guilds.boss.engaged", loc, card=card['name'],
          element=f"{_elem(interaction.client, elem)} {CARD_ELEMENT_LABELS.get(elem,'?')}",
          atk=_fmt(atk), calc=calc, event=ev_txt, hp=_fmt(stats['hp']), matchup=match_txt),
        ephemeral=True)
    await _refresh_boss_msg(interaction.client, boss_id)


async def spawn_boss(bot, guild_id, channel_id, tier=1, element=None, rarity=None, max_rarity=None):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return None
    tier = max(1, min(5, int(tier)))
    cfg = BOSS_TIERS[tier]
    loc = guild_locale(guild_id) or "en"
    # optional element: avatar constrained to that element (else any)
    elem_filter = element if element in CARD_ELEMENT_LABELS else None
    rarity = rarity if rarity in _RARITY_ORDER else None
    avatar = None
    # forced rarity (dashboard): avatar of EXACTLY that rarity
    if rarity:
        avatar = (card_pick_random_exact_rarity(rarity, element=elem_filter)
                  or card_pick_random_exact_rarity(rarity))
    # otherwise: random rarity inside the tier range
    if not avatar:
        rng = list(_TIER_RANGE.get(tier, ["epic"]))
        # optional rarity cap (auto boss: secret stays locked until the real combat
        # power of the server is high enough, cf auto_boss_loop)
        if max_rarity in _RARITY_ORDER:
            cap = _RARITY_ORDER.index(max_rarity)
            rng = [r for r in rng if _RARITY_ORDER.index(r) <= cap] or [rng[0]]
        random.shuffle(rng)
        for _r in rng:
            avatar = card_pick_random_exact_rarity(_r, element=elem_filter)
            if avatar:
                break
        if not avatar and elem_filter:
            # no card of that element inside the range -> drop the filter
            for _r in rng:
                avatar = card_pick_random_exact_rarity(_r)
                if avatar:
                    break
    if not avatar:
        avatar = card_pick_random_exact_rarity("epic")
    name = avatar["name"] if avatar else t("guilds.boss.unknown_entity", loc)
    element = (avatar.get("element") if avatar else None) or random.choice(list(CARD_ELEMENT_LABELS.keys()))
    img = avatar.get("image_url") if avatar else None
    # Boss stats scaled by the avatar rarity: factor = step^notch
    # (multiplicative, steep at high tiers), on HP AND ATK. idx safe outside the range.
    avatar_rar = (avatar or {}).get("rarity")
    idx = _avatar_idx(tier, avatar_rar)
    diff = _avatar_difficulty(tier, idx)
    # HP: full factor (tanky = DPS check). ATK: softened (diff^0.6) so the team is
    # not one-shot before it can DPS the boss.
    boss_atk = int(cfg["atk"] * diff ** 0.6)
    boss_hp = int(cfg["hp"] * diff)
    avatar_cid = avatar["id"] if avatar else None
    # start_at not set: the timer only starts with the 1st player
    bid = card_boss_create(guild_id, channel_id, name, element, tier, boss_hp, boss_atk,
                           image_url=img, start_at=None, card_id=avatar_cid)
    card_boss_set_status(bid, "recruiting")
    boss = card_boss_get(bid)
    embed = build_boss_embed(bot, boss, locale=loc)
    view = JoinView(bid, loc)
    # Ping the "card fans" role when configured (/cardsetup role)
    from database import guild_card_config_get
    _role_id = (guild_card_config_get(guild_id) or {}).get("ping_role_id")
    content = t("guilds.boss.appeared", loc)
    allowed = discord.AllowedMentions.none()
    if _role_id:
        content = f"<@&{_role_id}> {content}"
        allowed = discord.AllowedMentions(roles=True)
    msg = await channel.send(content=content, embed=embed, view=view, allowed_mentions=allowed)
    card_boss_set_message(bid, msg.id)
    asyncio.create_task(_run_boss(bot, bid, msg, view))
    return bid


async def resume_active_bosses(bot):
    """On boot: restart the loop of the bosses left in 'recruiting'/'fighting'.
    Without it, a pm2 restart leaves the boss orphaned (the asyncio task is dead)."""
    from database import card_boss_list_active
    resumed = 0
    for boss in card_boss_list_active():
        bid = boss["id"]
        ch = bot.get_channel(int(boss["channel_id"])) if boss.get("channel_id") else None
        mid = boss.get("message_id")
        if not ch or not mid:
            card_boss_set_status(bid, "expired")
            continue
        try:
            msg = await ch.fetch_message(int(mid))
        except Exception:
            card_boss_set_status(bid, "expired")  # message deleted
            continue
        view = JoinView(bid, _bloc(boss))
        try:
            bot.add_view(view, message_id=int(mid))
        except Exception:
            pass
        asyncio.create_task(_run_boss(bot, bid, msg, view))
        resumed += 1
    if resumed:
        print(f"[boss] {resumed} fight(s) resumed at boot")


def add_dummy_participants(bid, n):
    """[Test] Add n dummy fighters to the boss (random stats + element + card)."""
    import random as _r
    rarities = ["common", "rare", "epic", "legendary", "mythic"]
    for i in range(int(n)):
        card = None
        for _ in range(3):
            card = card_pick_random_exact_rarity(_r.choice(rarities))
            if card:
                break
        elem = (card.get("element") if card else None) or _r.choice(list(CARD_ELEMENT_LABELS.keys()))
        hp = _r.randint(80000, 220000)
        atk = _r.randint(30000, 75000)
        uid = f"dummy_{i+1}"
        boss_participant_add(bid, uid, f"Bot {i+1}", elem, hp, atk,
                             card_id=(card["id"] if card else None))
        apt = _r.choice(["berserker", "gardien", "soigneur", "duelliste", "executeur", ""])
        if apt:
            boss_participant_update(bid, uid, aptitude=apt)


def _scale_boss_to_team(bid):
    """Recompute the boss HP/ATK from the BASE power of the current team.
    The boss never drops under its reference tier (floor), it only grows for
    strong teams -> anti-powercreep without any risk of a trivial fight."""
    try:
        boss = card_boss_get(bid)
        if not boss:
            return
        tier = boss["tier"]
        cfg = BOSS_TIERS.get(tier, {})
        sc = BOSS_TIER_SCALE.get(tier)
        if not sc or not cfg:
            return
        parts = boss_participants_list(bid)
        if not parts:
            return
        # sum of base ATK, average base HP of the team (collection floor, card excluded)
        sum_atk = 0
        sum_hp = 0
        for p in parts:
            uid = p["user_id"]
            if _is_dummy(uid):
                base_atk, base_hp = int(p["atk"]), int(p.get("max_hp") or p["hp"])
            else:
                st = compute_player_combat_stats(uid)
                base_atk, base_hp = st["atk"], st["hp"]
                # Reduce the impact of the fusion bonus on the boss scaling:
                # strip the full fusion mult then re-apply a fraction of it.
                bp = float(st.get("bonus_pct", 0) or 0)
                if bp > 0:
                    full = 1.0 + bp / 100.0
                    red = 1.0 + (bp / 100.0) * _FUSION_BOSS_SCALE_WEIGHT
                    base_atk = base_atk / full * red
                    base_hp = base_hp / full * red
            sum_atk += base_atk
            sum_hp += base_hp
        n = len(parts)
        # Floor = reference tier: the boss never drops under BOSS_TIERS, it only
        # grows for strong teams (anti-powercreep).
        scaled_hp = int(sc["hp"] * sum_atk)
        scaled_atk = int(sc["atk"] * (sum_hp / n))
        # Avatar rarity difficulty factor (= step^notch), recomputed from the avatar
        # rarity (card_id, immutable) -> no compounding on every join.
        avatar = card_get(boss["card_id"]) if boss.get("card_id") else None
        idx = _avatar_idx(tier, (avatar or {}).get("rarity"))
        diff = _avatar_difficulty(tier, idx)
        # HP: full factor (tanky = DPS check). ATK: softened (diff^0.6) so the team is
        # not one-shot before it can DPS.
        new_hp = int(max(cfg.get("hp", scaled_hp), scaled_hp) * diff)
        new_atk = int(max(cfg.get("atk", scaled_atk), scaled_atk) * diff ** 0.6)
        card_boss_set_stats(bid, new_hp, new_atk)
    except Exception as e:
        print(f"[boss] scale err: {e!r}")


def _is_dummy(uid):
    return str(uid).startswith("dummy_")


async def _run_boss(bot, bid, msg, view):
    try:
        _b0 = card_boss_get(bid)
        if not _b0:
            return
        loc = _bloc(_b0)
        # ── Recruiting phase ── (skipped when resuming a fight already started)
        if _b0["status"] == "recruiting":
            # The timer (start_at) is only set by the 1st player. With no player we
            # wait until _JOIN_EXPIRE then the boss vanishes.
            join_deadline = _t.time() + _JOIN_EXPIRE
            quick = False
            last_sig = None
            while True:
                await asyncio.sleep(3)
                boss = card_boss_get(bid)
                if not boss or boss["status"] != "recruiting":
                    return
                parts = boss_participants_list(bid)
                # Re-render the embed when the roster changed (includes the edits made
                # from the dashboard: card / ability / element).
                sig = [(p["user_id"], p.get("card_id"), p.get("aptitude"),
                        p.get("element"), p.get("atk"), p.get("max_hp")) for p in parts]
                if last_sig is not None and sig != last_sig:
                    try:
                        await msg.edit(embed=build_boss_embed(bot, card_boss_get(bid), locale=loc),
                                       view=view)
                    except Exception:
                        pass
                last_sig = sig
                if len(parts) >= _QUICK_START_AT:
                    quick = True
                    break
                sa = boss.get("start_at")
                if sa and _t.time() >= sa:
                    break  # 2 min timer elapsed
                if not parts and _t.time() >= join_deadline:
                    break  # nobody joined -> expiration (handled below)
            if quick:
                # quick countdown (visible timer)
                qstart = _t.time() + _QUICK_SECONDS
                card_boss_set_start(bid, qstart)
                boss = card_boss_get(bid)
                try:
                    await msg.edit(embed=build_boss_embed(bot, boss, locale=loc,
                        phase_text=t("guilds.boss.quick_start", loc,
                                     players=_QUICK_START_AT, ts=int(qstart))))
                except Exception:
                    pass
                await asyncio.sleep(_QUICK_SECONDS)

            parts = boss_participants_list(bid)
            if not parts:
                card_boss_set_status(bid, "expired")
                for ch in view.children:
                    ch.disabled = True
                try:
                    await msg.edit(content=t("guilds.boss.nobody", loc),
                                   embed=build_boss_embed(bot, card_boss_get(bid), locale=loc,
                                                          phase_text=t("guilds.boss.nobody_phase", loc)),
                                   view=view)
                except Exception:
                    pass
                return

        # ── Anti-powercreep scaling: recompute the boss HP/ATK from the team ──
        _scale_boss_to_team(bid)

        # ── Automatic fight ──
        card_boss_set_status(bid, "fighting")
        for ch in view.children:
            # keep the "watch the fight live" link button clickable during the fight
            if getattr(ch, "style", None) == discord.ButtonStyle.link:
                continue
            ch.disabled = True
        log = [t("guilds.boss.fight_start", loc)]
        boss_event_add(bid, "start", {})
        # Build the battlefield (player cards vs boss), attached once
        bf_path = _build_battlefield(bid)
        try:
            if bf_path:
                import os as _os
                await msg.edit(content=t("guilds.boss.in_progress", loc),
                               attachments=[discord.File(bf_path, filename="battle.png")],
                               embed=build_boss_embed(bot, card_boss_get(bid), log=log,
                                                      battle=True, locale=loc),
                               view=view)
            else:
                await msg.edit(content=t("guilds.boss.in_progress", loc),
                               embed=build_boss_embed(bot, card_boss_get(bid), log=log, locale=loc),
                               view=view)
        except Exception:
            pass

        # ALTERNATING turns: 1 turn = 1 action. The team starts, then the boss, etc.
        from database import card_boss_heal
        tier = boss["tier"]
        # T4+: stronger enrage. T5: devastating blow twice.
        enrage_mult = 1.65 if tier >= 4 else 1.50
        # T1-4: 1 devastating blow. T5: 2. T5 with a mythic (or secret) avatar: 3.
        _avatar_rar = (card_get(boss["card_id"]) or {}).get("rarity") if boss.get("card_id") else None
        if tier >= 5:
            max_smashes = 3 if _avatar_rar in ("mythic", "secret") else 2
        else:
            max_smashes = 1
        boss_self_heal = (tier >= 4)   # T4+: the boss heals itself once under 20% HP
        turn = 0
        actor = "party"
        smash_count = 0
        boss_healed = False
        enrage_announced = False
        def _apt(p):
            return p.get("aptitude") or ""

        def _boss_hit(p, dmg):
            """Apply the damage to a participant (after the defensive ability mult).
            Returns (new_hp, ko_bool)."""
            real = max(1, int(dmg * _apt_taken_mult(_apt(p))))
            new_hp = max(0, p["hp"] - real)
            # taken = real (post reduction); taken_raw = raw (pre reduction) for the tank grade
            boss_participant_update(bid, p["user_id"], hp=new_hp, add_taken=real,
                                    add_taken_raw=max(1, int(dmg)),
                                    died=(True if new_hp <= 0 else None))
            return new_hp, (new_hp <= 0), real

        def _apply_heals():
            """Every living Healer heals the most wounded member (+% of max HP)."""
            healers = [p for p in boss_participants_list(bid)
                       if _apt(p) == "soigneur" and p["hp"] > 0]
            for _h in healers:
                cur = boss_participants_list(bid)
                # target = living member with the lowest HP % (that is not full)
                cand = [p for p in cur if 0 < p["hp"] < (p.get("max_hp") or p["hp"])]
                if not cand:
                    continue
                tgt = min(cand, key=lambda p: p["hp"] / (p.get("max_hp") or p["hp"]))
                mx = tgt.get("max_hp") or tgt["hp"]
                heal = int(mx * _SOIGNEUR_HEAL)
                crit = random.random() < _SOIGNEUR_CRIT   # ~5%: double heal
                if crit:
                    heal *= 2
                boss_participant_update(bid, tgt["user_id"], hp=min(mx, tgt["hp"] + heal))
                boss_participant_update(bid, _h["user_id"], add_heal=heal)
                crit_txt = t("guilds.boss.heal_crit", loc) if crit else ""
                log.append(t("guilds.boss.turn_heal", loc, turn=turn, healer=_h['name'],
                             target=tgt['name'], amount=_fmt(heal), crit=crit_txt))
                boss_event_add(bid, "party_heal", {"healer": str(_h["user_id"]),
                                                   "target": str(tgt["user_id"]), "amount": heal, "crit": crit})

        while turn < _MAX_TURNS:
            turn += 1
            await asyncio.sleep(_TURN_DELAY)
            boss = card_boss_get(bid)
            if not boss or boss["status"] != "fighting":
                return
            alive = [p for p in boss_participants_list(bid) if p["hp"] > 0]
            if not alive:
                card_boss_set_status(bid, "wiped")
                break
            if actor == "party":
                boss_enraged = boss["hp"] < boss["max_hp"] * 0.5
                total = 0
                best_eff = 1.0
                for p in alive:
                    apt = _apt(p)
                    m = _apt_matchup(apt, element_matchup(p["element"], boss["element"]))
                    am = _apt_atk_mult(apt, m, boss_enraged)
                    pdmg = max(1, int(p["atk"] * m * am))
                    total += pdmg
                    best_eff = max(best_eff, m)
                    # record the contribution of each player (for the loot/MVP)
                    boss_participant_update(bid, p["user_id"], add_damage=pdmg)
                boss_hp = card_boss_apply_damage(bid, total)
                eff = " 🔥" if best_eff > 1 else ""
                log.append(t("guilds.boss.turn_party", loc, turn=turn,
                             amount=_fmt(total), eff=eff))
                boss_event_add(bid, "party_hit", {"total": total, "crit": best_eff > 1, "turn": turn})
                _apply_heals()
                # T4+: the boss heals itself once when it falls under 20% HP
                if boss_self_heal and not boss_healed and 0 < boss_hp < boss["max_hp"] * 0.20:
                    boss_healed = True
                    heal = int(boss["max_hp"] * 0.10)
                    boss_hp = card_boss_heal(bid, heal)
                    log.append(t("guilds.boss.turn_boss_heal", loc, turn=turn, amount=_fmt(heal)))
                    boss_event_add(bid, "boss_heal", {"amount": heal})
                if boss_hp <= 0:
                    card_boss_set_status(bid, "defeated")
                    break
                actor = "boss"
            elif smash_count < max_smashes and random.random() < 0.25:
                # Special blow: targets 1 player, x3 damage (T5: up to 2x per fight).
                # When a Guardian is alive it INTERCEPTS the blow (protects the squishies):
                # the healthiest one takes it instead of a random target.
                smash_count += 1
                _guards = [p for p in alive if _apt(p) == "gardien"]
                if _guards:
                    target = max(_guards, key=lambda p: p["hp"]); taunted = True
                else:
                    target = random.choice(alive); taunted = False
                cm = element_matchup(boss["element"], target["element"])
                dmg = max(1, int(boss["atk"] * cm * 3))
                new_hp, dead, real = _boss_hit(target, dmg)
                ko = t("guilds.boss.ko", loc) if dead else ""
                taunt_txt = t("guilds.boss.taunt", loc) if taunted else ""
                log.append(t("guilds.boss.turn_smash", loc, turn=turn, target=target['name'],
                             amount=_fmt(real), ko=ko, taunt=taunt_txt))
                boss_event_add(bid, "boss_smash", {"target": str(target["user_id"]),
                                                   "dmg": real, "ko": dead, "taunt": taunted, "turn": turn})
                if all(pp["hp"] <= 0 for pp in boss_participants_list(bid)):
                    card_boss_set_status(bid, "wiped")
                    break
                actor = "party"
            else:
                # The boss strikes the WHOLE team (AoE). Enraged (<50% HP).
                enraged = boss["hp"] < boss["max_hp"] * 0.5
                rage = enrage_mult if enraged else 1.0
                if enraged and not enrage_announced:
                    enrage_announced = True
                    log.append(t("guilds.boss.enrage", loc, mult=f"{enrage_mult:.2f}"))
                    boss_event_add(bid, "enrage", {"mult": enrage_mult})
                # Guardian aura: while a Guardian stands, it covers the team and
                # reduces the AoE damage everyone takes.
                guard_aura = _GARDIEN_AURA if any(_apt(p) == "gardien" for p in alive) else 1.0
                kos = []
                ko_ids = []
                total_dmg = 0
                for p in alive:
                    cm = element_matchup(boss["element"], p["element"])
                    dmg = max(1, int(boss["atk"] * cm * rage * guard_aura))
                    new_hp, dead, real = _boss_hit(p, dmg)
                    total_dmg += real
                    if dead:
                        kos.append(p["name"]); ko_ids.append(str(p["user_id"]))
                ko_txt = t("guilds.boss.kos", loc, names=", ".join(kos)) if kos else ""
                aura_txt = (t("guilds.boss.guard_aura", loc,
                              pct=int(round((1 - _GARDIEN_AURA) * 100)))
                            if guard_aura < 1.0 else "")
                log.append(t("guilds.boss.turn_aoe", loc, turn=turn,
                             amount=_fmt(total_dmg), ko=ko_txt, aura=aura_txt))
                boss_event_add(bid, "boss_aoe", {"total": total_dmg, "enraged": enraged,
                                                 "kos": ko_ids, "targets": [str(p["user_id"]) for p in alive],
                                                 "guard_aura": guard_aura < 1.0, "turn": turn})
                if all(pp["hp"] <= 0 for pp in boss_participants_list(bid)):
                    card_boss_set_status(bid, "wiped")
                    break
                actor = "party"
            try:
                await msg.edit(embed=build_boss_embed(bot, card_boss_get(bid), log=log,
                                                      battle=bool(bf_path), locale=loc),
                               view=view)
            except Exception:
                pass

        # ── End ──
        boss = card_boss_get(bid)
        if boss["status"] == "fighting":   # turn cap reached
            card_boss_set_status(bid, "wiped")
            boss = card_boss_get(bid)
        await _finish(bot, bid, msg, view, log, boss["status"] == "defeated")
    except Exception as e:
        import traceback
        print(f"[boss] run err: {e!r}")
        traceback.print_exc()
        # Safety net: never leave a "ghost" boss (status stuck on fighting/recruiting).
        # We mark the fight as over so the live link dies cleanly.
        try:
            _bx = card_boss_get(bid)
            if _bx and _bx["status"] in ("fighting", "recruiting"):
                card_boss_set_status(bid, "wiped")
                boss_event_add(bid, "end", {"victory": False})
                # Still post a Discord message so the players see a result.
                try:
                    _ch = bot.get_channel(int(_bx["channel_id"])) or await bot.fetch_channel(int(_bx["channel_id"]))
                    if _ch:
                        _loc = _bloc(_bx)
                        await _ch.send(embed=discord.Embed(
                            title=t("guilds.boss.crashed_title", _loc, boss=_bx['name']),
                            description=t("guilds.boss.crashed_desc", _loc),
                            color=0xff3d57))
                except Exception:
                    pass
        except Exception:
            pass


async def _finish(bot, bid, msg, view, log, victory):
    boss = card_boss_get(bid)
    loc = _bloc(boss)
    parts = boss_participants_list(bid)

    boss_event_add(bid, "end", {"victory": bool(victory)})
    # Show the result on the fight embed first, then leave time to read it
    log.append(t("guilds.boss.log_victory" if victory else "guilds.boss.log_defeat", loc))
    try:
        await msg.edit(content=t("guilds.boss.victory_content" if victory
                                 else "guilds.boss.defeat_content", loc),
                       embed=build_boss_embed(bot, boss, log=log, battle=True, locale=loc),
                       view=view)
    except Exception:
        pass
    await asyncio.sleep(6)

    # Resolve the channel (cache -> fetch fallback) BEFORE touching the fight embed.
    ch = bot.get_channel(int(boss["channel_id"]))
    if ch is None:
        try:
            ch = await bot.fetch_channel(int(boss["channel_id"]))
        except Exception as e:
            print(f"[boss] finish: channel {boss['channel_id']} not found: {e!r}")
    if not ch:
        # No channel to post the result: KEEP the fight embed (already edited with the
        # result) instead of deleting it and leaving a ghost behind.
        try:
            for c in view.children:
                c.disabled = True
            await msg.edit(view=view)
        except Exception:
            pass
        return

    # Delete the base fight embed (we do have a channel to post the result)
    try:
        await msg.delete()
    except Exception:
        try:
            for c in view.children:
                c.disabled = True
            await msg.edit(view=view)
        except Exception:
            pass

    def _is_dummy(uid):
        return str(uid).startswith("dummy_")
    real_parts = [p for p in parts if not _is_dummy(p["user_id"])]
    mentions = " ".join(f"<@{p['user_id']}>" for p in real_parts) or "—"

    if victory:
        from database import (essence_reward_add, card_get, user_item_add, roll_give_user)
        tier = boss["tier"]
        # Rarity of the AVATAR card fought -> drives the "card" reward
        avatar_card = card_get(boss["card_id"]) if boss.get("card_id") else None
        avatar_rar = (avatar_card or {}).get("rarity") or _tier_loot_rarity(tier)
        # Sorted by descending damage -> the 1st one is the MVP
        winners = sorted([p for p in real_parts if p["damage"] > 0],
                         key=lambda x: -x["damage"])
        # Guild XP hook: every guild present (among the winners) gains the tier XP
        # ONCE (not per member). Boss = main lever together with the roll.
        try:
            from database import get_guild_config as _ggc, guild_of_user as _gou, guild_add_xp as _gax
            _bxp = int(_ggc().get("xp", {}).get("boss", {}).get(str(tier), 0))
            if _bxp:
                _seen_g = set()
                for p in winners:
                    gg = _gou(p["user_id"])
                    if gg and gg["id"] not in _seen_g:
                        _seen_g.add(gg["id"]); _gax(gg["id"], _bxp)
        except Exception as e:
            print(f"[boss guild xp] {e}")
        # Guild quests (boss): 1 per winning participant
        try:
            from database import guild_quest_progress as _gqp
            for p in winners:
                _gqp(p["user_id"], "boss", 1)
        except Exception as e:
            print(f"[boss guild quest] {e}")
        # A-F grade per player (damage/tank/heal) -> drives every reward
        grades = _grade_map(winners)
        loot_lines = []
        web_rewards = []   # structured version for the live dashboard
        for idx, p in enumerate(winners):
            grade = grades.get(str(p["user_id"]), "C")
            base_ess = int(_ESS_BASE.get(tier, 1000) * _GRADE_ESS_MULT.get(grade, 1.0))
            # Guild loot bonus (+%) from the reward tier of his guild level
            _bpct = 0
            try:
                from database import guild_perks_for_user as _gpfu
                _bpct = int((_gpfu(p["user_id"]) or {}).get("boss_pct", 0))
            except Exception:
                _bpct = 0
            if _bpct:
                base_ess = int(base_ess * (1 + _bpct / 100))
            ess = essence_reward_add(p["user_id"], base_ess)
            gemo = _cemoji(bot, "boss" + grade.lower(), grade)
            parts_loot = [t("guilds.boss.loot_essence", loc, amount=_fmt(ess))]
            # unicode emojis for the web
            web_items = [t("guilds.boss.web_loot_essence", loc, amount=_fmt(ess))]
            # 1. Card reward from the avatar rarity (quantity from the grade)
            if avatar_rar == "secret":
                gq = _GOLDEN_BY_GRADE.get(grade, 0)
                if gq:
                    user_item_add(p["user_id"], "golden_roll", gq)
                    sfx = f" ×{gq}" if gq > 1 else ""
                    parts_loot.append(f"{_cemoji(bot, 'goldenroll', '🌈')} "
                                      + t("guilds.boss.loot_golden", loc, suffix=sfx))
                    web_items.append(t("guilds.boss.web_loot_golden", loc, suffix=sfx))
            elif avatar_rar == "mythic":
                user_item_add(p["user_id"], "mythic_fragment", 1)   # always 1
                parts_loot.append(t("guilds.boss.loot_mythic", loc))
                web_items.append(t("guilds.boss.web_loot_mythic", loc))
            elif avatar_card:
                cq = _CARD_COPIES.get(grade, 1)
                if cq:
                    for _ in range(cq):
                        user_card_add(p["user_id"], avatar_card["id"])
                    sfx = f" ×{cq}" if cq > 1 else ""
                    parts_loot.append(t("guilds.boss.loot_card", loc, card=avatar_card['name'],
                                        hint=RARITY_HINT.get(avatar_rar, ''), suffix=sfx))
                    web_items.append(t("guilds.boss.web_loot_card", loc, card=avatar_card['name'],
                                       hint=RARITY_HINT.get(avatar_rar, ''), suffix=sfx))
            # 2. DETERMINISTIC bonus rolls from tier x grade (+ guild loot bonus)
            n_rolls = _BOSS_ROLLS_BY_GRADE.get(tier, {}).get(grade, 0)
            if _bpct and n_rolls:
                n_rolls = int(round(n_rolls * (1 + _bpct / 100)))
            if n_rolls:
                roll_give_user(p["user_id"], n_rolls)
                parts_loot.append(f"{_cemoji(bot, 'roll', '🎟️')} "
                                  + t("guilds.boss.loot_rolls", loc, count=n_rolls))
                web_items.append(t("guilds.boss.web_loot_rolls", loc, count=n_rolls))
            crown = "👑 " if idx == 0 else ""
            dead = " 💀" if int(p.get("died") or 0) else ""
            loot_lines.append(f"{gemo} {crown}<@{p['user_id']}> "
                              + t("guilds.boss.loot_line", loc, grade=grade,
                                  damage=_fmt(p['damage']), died=dead)
                              + "\n　→ " + " · ".join(parts_loot))
            web_rewards.append({"uid": str(p["user_id"]), "name": p["name"],
                                "dmg": int(p["damage"]), "mvp": idx == 0, "grade": grade,
                                "died": bool(int(p.get("died") or 0)), "items": web_items})
        reward_hdr = {
            "secret": f"{_cemoji(bot, 'goldenroll', '🌈')} " + t("guilds.boss.hdr_secret", loc),
            "mythic": t("guilds.boss.hdr_mythic", loc),
        }.get(avatar_rar, t("guilds.boss.hdr_card", loc,
                            card=(avatar_card or {}).get('name', '?'),
                            hint=RARITY_HINT.get(avatar_rar, '')))
        # Rewards for the live dashboard (header without markdown / custom emojis)
        web_hdr = {
            "secret": t("guilds.boss.web_hdr_secret", loc),
            "mythic": t("guilds.boss.web_hdr_mythic", loc),
        }.get(avatar_rar, t("guilds.boss.web_hdr_card", loc,
                            card=(avatar_card or {}).get('name', '?'),
                            hint=RARITY_HINT.get(avatar_rar, '')))
        boss_event_add(bid, "rewards", {"header": web_hdr, "boss": boss["name"],
                                        "tier": tier, "players": web_rewards})
        embed = discord.Embed(
            title=t("guilds.boss.victory_title", loc, boss=boss['name'], tier=tier),
            description=t("guilds.boss.victory_desc", loc, mentions=mentions,
                          header=reward_hdr, lines=("\n".join(loot_lines) or "—")),
            color=0x4ade80)
        await ch.send(content=mentions, embed=embed,
                       allowed_mentions=discord.AllowedMentions(users=True))
    else:
        boss_event_add(bid, "defeat", {"boss": boss["name"], "tier": boss["tier"]})
        embed = discord.Embed(
            title=t("guilds.boss.defeat_title", loc, boss=boss['name']),
            description=t("guilds.boss.defeat_desc", loc, mentions=mentions),
            color=0xff3d57)
        await ch.send(content=mentions, embed=embed,
                       allowed_mentions=discord.AllowedMentions(users=True))


RARITY_HINT = {"epic": "🟣", "legendary": "🟠", "mythic": "🔴", "secret": "🌈"}


# ===== RAID REWARD GRADING SYSTEM (A-F) =====
# Every player is graded on his ABSOLUTE share of a boss-tied pool:
#   DPS   = damage / total team damage
#   Tank  = raw damage taken (Guardian) / total raw damage taken by the team
#   Heal  = healing done / real HP lost by the team
# score = best share + 0.25 * 2nd share. A at 35% (~2x the fair share) -> hard.
_ESS_BASE = {1: 800, 2: 1500, 3: 2500, 4: 3500, 5: 5000}
_GRADE_ESS_MULT = {"A": 1.5, "B": 1.25, "C": 1.0, "D": 0.85, "E": 0.65, "F": 0.4}
_BOSS_ROLLS_BY_GRADE = {
    1: {"A": 1, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0},
    2: {"A": 1, "B": 1, "C": 0, "D": 0, "E": 0, "F": 0},
    3: {"A": 2, "B": 1, "C": 1, "D": 0, "E": 0, "F": 0},
    4: {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1, "F": 0},
    5: {"A": 12, "B": 9, "C": 6, "D": 4, "E": 2, "F": 1},
}
_CARD_COPIES = {"A": 2, "B": 1, "C": 1, "D": 1, "E": 1, "F": 0}
_GOLDEN_BY_GRADE = {"A": 2, "B": 1, "C": 1, "D": 1, "E": 0, "F": 0}
_GRADE_LADDER = ["F", "E", "D", "C", "B", "A"]
# Healing is worth more "per point" than raw damage taken: a healer never offsets
# 35% of ALL the damage, so without this weight he would cap at C.
_HEAL_WEIGHT = 2.5


def _grade_band(score):
    if score >= 0.35: return "A"
    if score >= 0.25: return "B"
    if score >= 0.15: return "C"
    if score >= 0.08: return "D"
    if score >= 0.03: return "E"
    return "F"


def _grade_map(winners):
    """{user_id: 'A'..'F'} for every winner. Solo -> A. Died -> C max."""
    if not winners:
        return {}
    n = len(winners)
    dmg_pool  = sum(max(0, int(p.get("damage") or 0)) for p in winners) or 1
    tank_pool = sum(max(0, int(p.get("taken_raw") or 0)) for p in winners) or 1
    heal_pool = sum(max(0, int(p.get("taken") or 0)) for p in winners) or 1
    out = {}
    for p in winners:
        is_gardien = (p.get("aptitude") or "") == "gardien"
        dmg_frac  = int(p.get("damage") or 0) / dmg_pool
        tank_frac = (int(p.get("taken_raw") or 0) / tank_pool) if is_gardien else 0.0
        heal_frac = min(1.0, _HEAL_WEIGHT * int(p.get("heal") or 0) / heal_pool)
        axes = sorted([dmg_frac, tank_frac, heal_frac], reverse=True)
        score = axes[0] + 0.25 * axes[1]
        g = "A" if n == 1 else _grade_band(score)
        if int(p.get("died") or 0) and _GRADE_LADDER.index(g) > _GRADE_LADDER.index("C"):
            g = "C"   # died -> capped at C
        out[str(p["user_id"])] = g
    return out
