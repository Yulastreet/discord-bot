"""Cards collection : /cardsetup (admin), /roll, /collection, /card.

Le owner du bot a un rolls infini (skip cooldown).
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
    user_card_settings_get, user_card_settings_set_last_roll,
    roll_cooldown_get, roll_cooldown_set,
    guild_card_config_get, guild_card_config_set,
    user_card_count_owned, user_card_transfer_one,
    card_trade_create, card_trade_get, card_trade_items, card_trade_set_status,
    card_suggestion_add,
    ESSENCE_REWARDS, currency_add,
    CARD_ELEMENT_LABELS as _ELEM_LABELS,
)


# Racine repo fiable. __file__ de ce module resout vers un mauvais cwd sur le VPS
# (charge en top-level). Les modules sous services/ resolvent correctement leur
# chemin -> on reutilise celui-la comme reference fiable.
from services.card_render import _ROOT as _REPO_ROOT

ROLL_COOLDOWN_SECONDS = 3600  # 1h, par serveur

RARITY_COLORS = {
    "common":    0x9aa0a6,  # gris
    "rare":      0x4cb5f9,  # bleu
    "epic":      0xa86dff,  # violet
    "legendary": 0xffa726,  # orange
    "mythic":    0xff3d57,  # rouge
    "secret":    0x1c1c1e,  # noir profond (laisse le rainbow border briller)
}
# Couleur embed par bordure (assortie au visuel de chaque cosmetique)
BORDER_COLORS = {
    "gold":  0xFFC83D,  # or
    "leaf":  0x6AB04C,  # vert feuille
    "frost": 0x4FC3F7,  # cyan givre
    "hell":  0xE7402B,  # rouge enfer
    "void":  0x8E44AD,  # violet neant
}

RARITY_EMOJIS = {
    "common":    "⚪",
    "rare":      "🔵",
    "epic":      "🟣",
    "legendary": "🟠",
    "mythic":    "🔴",
    "secret":    "🌈",  # fallback unicode si custom emoji 'rainbow' indispo
}

# Mapping rarete -> nom emoji custom Discord pour THUMBNAIL embed
_RARITY_CUSTOM_NAME = {
    "common":    "commun",
    "rare":      "rare",
    "epic":      "epic",
    "legendary": "legendaire",
    "mythic":    "mythic",
    "secret":    "secret",  # badge thumbnail emoji custom (support server)
}

# Mapping rarete -> nom emoji custom Discord INLINE (titre carte)
_RARITY_INLINE_EMOJI_NAME = {
    "secret":    "rainbowsphere",
}
_rarity_emoji_cache: dict[str, str] = {}

def _get_inline_emoji_str(bot, emoji_name: str) -> str:
    """Cherche emoji custom par nom dans tous les guilds, retourne string
    '<:name:id>' ou '<a:name:id>' utilisable inline. '' si pas trouve."""
    if not emoji_name:
        return ""
    try:
        for e in bot.emojis:
            if e.name.lower() == emoji_name.lower():
                return str(e)
    except Exception:
        pass
    return ""


def _get_rarity_title_emoji(bot, rarity: str) -> str:
    """Pour secret : emoji custom 'rainbow' inline. Sinon : unicode par defaut."""
    inline_name = _RARITY_INLINE_EMOJI_NAME.get(rarity)
    if inline_name:
        s = _get_inline_emoji_str(bot, inline_name)
        if s:
            return s
    return RARITY_EMOJIS.get(rarity, "⚪")


def _get_element_emoji(bot, element: str) -> str:
    """Emoji custom support de l'element (par nom) sinon fallback unicode."""
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
    """Emoji custom 'goldenroll' (par nom) sinon fallback unicode arc-en-ciel."""
    s = _get_inline_emoji_str(bot, "goldenroll")
    return s or "🌈"


def _get_rarity_custom_emoji_url(bot, rarity: str) -> str:
    """Cherche emoji custom dans tous les guilds du bot (support server inclus).
    Cache CDN URL (gif si animé, png sinon). Pour usage en thumbnail embed."""
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


def _support_view(label="🎁 Rejoindre le serveur support"):
    """View avec un bouton lien vers le serveur support (perks roll/wishlist)."""
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.link,
                                  url=SUPPORT_INVITE_URL))
    return v


def _is_support_member(bot, user_id) -> bool:
    """True si le user est membre du serveur de support (perks : roll x2, wishlist 6)."""
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
    """Retourne (url_http_ou_None, discord.File_ou_None) pour set_image embed.

    PRIORITE AU RENDER LOCAL : on ne hotlink JAMAIS un hote externe tant qu'un
    render local existe (anti-liens-morts). Ordre :
      1. render local static/card_renders/<id>.(webp|png) -> servi via domaine
         (PUBLIC_BASE_URL) sinon en attachment.
      2. image_url qui pointe deja sur un /static/ local existant.
      3. dernier recours : image_url distante http (peut mourir, seulement si
         aucun render local).
    """
    root = _REPO_ROOT
    cid = card.get("id")
    img = card.get("image_url") or ""
    public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

    # 1+2. Trouve un fichier render local
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
            # Servi par ton domaine : Discord proxifie, jamais d'hote externe
            return (public_base + local_rel, None)
        # Pas de domaine public (dev) : attachment. Re-encode en PNG pour matcher
        # le 'attachment://card.png' attendu par les callers.
        try:
            import io as _io
            from PIL import Image as _PImg
            buf = _io.BytesIO()
            _PImg.open(local_path).convert("RGB").save(buf, "PNG")
            buf.seek(0)
            return (None, discord.File(buf, filename="card.png"))
        except Exception:
            pass

    # 3. Dernier recours : distante
    if isinstance(img, str) and img.startswith("http"):
        return (img, None)
    return (None, None)


def _check_channel(interaction: discord.Interaction) -> tuple[bool, str | None]:
    """Verifie que la commande est lancee dans le salon configure.
    Retourne (ok, channel_mention_si_ko)."""
    cfg = guild_card_config_get(interaction.guild.id) if interaction.guild else None
    if not cfg or not cfg.get("channel_id"):
        return (True, None)
    if str(interaction.channel.id) != str(cfg["channel_id"]):
        return (False, f"<#{cfg['channel_id']}>")
    return (True, None)


_DASHBOARD_URL = (os.getenv("DASHBOARD_URL") or "https://dashboard.tookbot.click").rstrip("/")


class OwnersView(discord.ui.View):
    """Boutons 'Voir possesseurs' + 'Modifier' sous embed carte."""
    def __init__(self, card_id: int, card_name: str):
        super().__init__(timeout=600)
        self.card_id = card_id
        self.card_name = card_name
        # Bouton link 'Modifier' -> dashboard /cards?edit=<id>
        edit_url = f"{_DASHBOARD_URL}/cards?edit={card_id}"
        self.add_item(discord.ui.Button(
            label="Modifier", style=discord.ButtonStyle.link,
            emoji="✏", url=edit_url,
        ))

    @discord.ui.button(label="Voir possesseurs", style=discord.ButtonStyle.secondary,
                        emoji="👥")
    async def _show_owners(self, interaction: discord.Interaction, button: discord.ui.Button):
        owners = card_owners_list(self.card_id, limit=50)
        if not owners:
            await interaction.response.send_message(
                "Personne ne possède cette carte.", ephemeral=True)
            return
        lines = []
        for o in owners:
            uid = o["user_id"]
            qty = o["qty"]
            suffix = f" ×{qty}" if qty > 1 else ""
            lines.append(f"<@{uid}>{suffix}")
        embed = discord.Embed(
            title=f"👥 Possesseurs de {self.card_name}",
            description="\n".join(lines)[:4000],
            color=0xB9F23A,
        )
        if len(owners) >= 50:
            embed.set_footer(text="50 premiers affichés.")
        await interaction.response.send_message(
            embed=embed, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def setup_cards_commands(bot, deps):
    globals().update(deps)

    cards_grp = app_commands.Group(name="cards", description="Collection de cartes pop culture")

    # === /cardsetup admin (alias top-level pour clarte) ===
    @bot.tree.command(name="cardsetup", description="Definir le salon ou les commandes cartes sont autorisees (admin)")
    @app_commands.describe(salon="Salon textuel ou les commandes cartes seront limitees")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cardsetup(interaction: discord.Interaction, salon: discord.TextChannel):
        guild_card_config_set(interaction.guild.id, channel_id=salon.id, enabled=True)
        await interaction.response.send_message(
            f"✅ Salon des cartes configure sur {salon.mention}. "
            f"Les commandes `/roll`, `/cardcollec`, `/card` ne marcheront que dans ce salon.",
            ephemeral=True,
        )

    @bot.tree.command(name="cardsetup_disable", description="Desactive la restriction de salon cartes (admin)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cardsetup_disable(interaction: discord.Interaction):
        guild_card_config_set(interaction.guild.id, channel_id=None, enabled=True)
        await interaction.response.send_message(
            "✅ Restriction de salon retiree. Les commandes cartes sont disponibles partout.",
            ephemeral=True,
        )

    # === /roll [univers] ===
    @bot.tree.command(name="roll",
                       description="Tire une carte aleatoire (optionnel : filtre par univers)")
    @app_commands.describe(univers="Filtrer par categorie (sinon toutes)")
    async def roll(interaction: discord.Interaction, univers: str = None):
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    f"Les commandes cartes sont reservees au salon {target}. Utilise `/cardsetup` (admin) pour changer.",
                    ephemeral=True,
                )
                return

        # Cooldown PAR SERVEUR (un timer par guild) - skip pour owner.
        # Membres serveur support : 2 charges/h. Autres : 1/h. Chaque charge
        # recharge 1h apres SA propre utilisation. Les rolls bonus (offerts par
        # owner) sont consommes en priorite et ne se rechargent pas.
        from database import (roll_bonus_available, roll_bonus_consume,
                               roll_events_count, roll_events_oldest_ts, roll_events_add)
        uid = interaction.user.id
        gid = interaction.guild.id if interaction.guild else None
        is_support = _is_support_member(bot, uid)
        max_charges = 2 if is_support else 1
        use_bonus = False
        if not _is_owner(uid) and gid:
            if roll_bonus_available(uid) > 0:
                use_bonus = True
            else:
                recent = roll_events_count(uid, gid, 3600)
                if recent >= max_charges:
                    now_ts = _time.time()
                    oldest = roll_events_oldest_ts(uid, gid, 3600)
                    remain = (3600 - (now_ts - oldest)) if oldest else 3600
                    if remain < 0:
                        remain = 0
                    ready_at = int(now_ts + remain)
                    if is_support:
                        await interaction.response.send_message(
                            f"⏰ Tu as utilisé tes **2 rolls** de l'heure. "
                            f"Prochain roll <t:{ready_at}:R>.",
                            ephemeral=True)
                    else:
                        await interaction.response.send_message(
                            f"⏰ Cooldown actif. Prochain roll <t:{ready_at}:R>.\n"
                            f"💡 Si tu es sur le **serveur support** tu as **2 rolls/h** au lieu de 1. "
                            f"Rejoins-le :",
                            view=_support_view(), ephemeral=True)
                    return

        # Verifie qu'il y a des cartes
        if card_count_total() == 0:
            await interaction.response.send_message(
                "Aucune carte dans le catalogue. Demande au owner d'en ajouter via le dashboard.",
                ephemeral=True,
            )
            return

        # Pioche + add (avec filtre univers si fourni)
        univers_filter = (univers or "").strip() or None
        card = card_roll_random(universe=univers_filter)
        if not card:
            label = f" dans l'univers `{univers_filter}`" if univers_filter else ""
            await interaction.response.send_message(
                f"Aucune carte disponible{label}.", ephemeral=True)
            return
        # Doublon ? (avant l'ajout) -> essences x2
        already_owned = user_card_count_owned(uid, card["id"]) > 0
        user_card_add(uid, card["id"])
        bonus_left = None
        if not _is_owner(uid) and gid:
            if use_bonus:
                roll_bonus_consume(uid)
                bonus_left = roll_bonus_available(uid)
            else:
                roll_events_add(uid, gid)

        # Gain d'essences selon rarete (doublon = x2)
        rarity_for_reward = card.get("rarity", "common")
        essence_base = ESSENCE_REWARDS.get(rarity_for_reward, 12)
        essence_gain = essence_base * 2 if already_owned else essence_base
        try:
            from database import essence_reward_add
            essence_gain = essence_reward_add(uid, essence_gain)  # applique bonus roue du jour
        except Exception as e:
            print(f"[roll essence] err: {e}")

        # Embed minimaliste
        rarity = card.get("rarity", "common")
        color = RARITY_COLORS.get(rarity, 0x9aa0a6)
        emoji = _get_rarity_title_emoji(bot, rarity)
        origine = card.get("subtitle") or "?"
        univers = card.get("universe") or "?"
        rarity_display = "?????" if rarity == "secret" else rarity.upper()
        flavor = (card.get("flavor_subtitle") or "").strip()
        essence_line = f"**Essences :** +{essence_gain} ✨" + (" _(doublon x2)_" if already_owned else "")
        _elem = card.get("element")
        if _elem:
            essence_line += f"\n**Élément :** {_get_element_emoji(bot, _elem)} {_ELEM_LABELS.get(_elem, '')}"
        if bonus_left is not None:
            essence_line += f"\n🎟️ _Roll bonus utilisé — il t'en reste **{bonus_left}**_"
        desc_parts = []
        if flavor:
            desc_parts.append(f"_**{flavor}**_")
        desc_parts.append(f"**Rareté :** {rarity_display}\n**Origine :** {origine}\n**Univers :** {univers}\n{essence_line}")
        desc = "\n\n".join(desc_parts)
        embed = discord.Embed(
            title=f"{emoji} {card['name']}"[:256],
            description=desc,
            color=color,
        )
        # Thumbnail = emoji custom anime (rareté) si dispo
        thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
        if thumb_url:
            embed.set_thumbnail(url=thumb_url)
        img_url, img_file = _resolve_card_image(card)
        if img_url:
            embed.set_image(url=img_url)
        elif img_file:
            embed.set_image(url="attachment://card.png")
        avatar_url = str(interaction.user.display_avatar.url) if interaction.user.display_avatar else None
        embed.set_footer(text=f"Appartient à {interaction.user.display_name}",
                          icon_url=avatar_url)
        view = OwnersView(card["id"], card["name"])
        if img_file:
            await interaction.response.send_message(embed=embed, file=img_file, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)

        # Notif wishlist : ping les gens qui veulent cette carte (hors roller)
        try:
            from database import wishlist_users_for_card
            wishers = wishlist_users_for_card(card["id"], exclude_user=uid)
            if wishers and interaction.guild:
                mentions = []
                for wid in wishers[:5]:
                    m = interaction.guild.get_member(int(wid))
                    if m:
                        mentions.append(m.mention)
                if mentions:
                    await interaction.channel.send(
                        f"🔔 {' '.join(mentions)} — **{interaction.user.display_name}** "
                        f"vient d'obtenir **{card['name']}** de votre wishlist ! "
                        f"Proposez un échange avec `/cardtrade`.")
        except Exception as e:
            print(f"[wishlist notif] {e}")

    @roll.autocomplete("univers")
    async def roll_univers_autocomplete(interaction: discord.Interaction, current: str):
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            if q:
                rows = c.execute(
                    "SELECT DISTINCT universe FROM cards "
                    "WHERE universe IS NOT NULL AND universe != '' "
                    "AND LOWER(universe) LIKE ? "
                    "ORDER BY universe LIMIT 25", (f"%{q}%",)).fetchall()
            else:
                rows = c.execute(
                    "SELECT DISTINCT universe FROM cards "
                    "WHERE universe IS NOT NULL AND universe != '' "
                    "ORDER BY universe LIMIT 25").fetchall()
            conn.close()
            return [app_commands.Choice(name=r["universe"][:100], value=r["universe"][:100])
                     for r in rows]
        except Exception:
            return []

    # === /collection ===
    @bot.tree.command(name="cardcollec", description="Voir ta collection de cartes (ou celle de quelqu'un)")
    @app_commands.describe(membre="Membre dont voir la collection (defaut : toi)")
    async def collection(interaction: discord.Interaction,
                          membre: discord.Member = None):
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    f"Les commandes cartes sont reservees au salon {target}.",
                    ephemeral=True,
                )
                return
        target_user = membre or interaction.user
        rar_val = None
        cat_val = None
        from database import (user_card_customizations_map, user_card_fusion_map,
                               user_collection_origins, all_card_origins)
        custom_map = user_card_customizations_map(target_user.id)
        fusion_map = user_card_fusion_map(target_user.id)
        total = user_card_count(target_user.id)
        owner_id = interaction.user.id
        PAGE_SIZE = 25

        def _grouped_rows(cat):
            cards = user_card_list(target_user.id, rarity=rar_val, categorie=cat)
            grouped: dict[int, dict] = {}
            for c in cards:
                cid = c["card_id"]
                if cid not in grouped:
                    grouped[cid] = {**c, "count": 0, "nt_count": 0}
                grouped[cid]["count"] += 1
                if c.get("not_tradeable"):
                    grouped[cid]["nt_count"] += 1
            return list(grouped.values())

        def _build_embed(rows, cat, page, total_pages):
            page_rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
            desc = f"**{total}** cartes ({len(rows)} uniques)"
            if rar_val:
                desc += f" • rareté **{rar_val}**"
            if cat:
                desc += f" • **{cat}**"
            embed = discord.Embed(
                title=f"🃏 Collection de {target_user.display_name}",
                description=desc, color=0xB9F23A,
            )
            lines = []
            for c in page_rows:
                emoji = RARITY_EMOJIS.get(c["rarity"], "⚪")
                elem = _get_element_emoji(bot, c.get("element"))
                pre = f"{emoji}｜{elem}" if elem else emoji
                uni = c.get("universe") or "?"
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
            embed.description += "\n\n" + ("\n".join(lines) if lines else "_(vide)_")
            embed.set_footer(text=f"Page {page}/{total_pages}")
            if target_user.display_avatar:
                embed.set_thumbnail(url=str(target_user.display_avatar.url))
            return embed

        class _CollecView(discord.ui.View):
            def __init__(self, rows, cat):
                super().__init__(timeout=300)
                self.rows = rows
                self.cat = cat
                self.page = 1
                self.total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
                self._refresh()

            def _refresh(self):
                self.prev_btn.disabled = (self.page <= 1)
                self.next_btn.disabled = (self.page >= self.total_pages)
                self.counter.label = f"{self.page} / {self.total_pages}"

            async def _guard(self, interaction):
                if interaction.user.id != owner_id:
                    await interaction.response.send_message(
                        "Ce menu n'est pas pour toi. Fais ta propre `/cardcollec`.",
                        ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="◀ Précédent", style=discord.ButtonStyle.secondary, row=0)
            async def prev_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                if not await self._guard(interaction): return
                if self.page > 1:
                    self.page -= 1; self._refresh()
                    await interaction.response.edit_message(
                        embed=_build_embed(self.rows, self.cat, self.page, self.total_pages), view=self)

            @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.primary, disabled=True, row=0)
            async def counter(self, interaction: discord.Interaction, btn: discord.ui.Button):
                pass

            @discord.ui.button(label="Suivant ▶", style=discord.ButtonStyle.secondary, row=0)
            async def next_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                if not await self._guard(interaction): return
                if self.page < self.total_pages:
                    self.page += 1; self._refresh()
                    await interaction.response.edit_message(
                        embed=_build_embed(self.rows, self.cat, self.page, self.total_pages), view=self)

            @discord.ui.button(label="📚 Parcourir les origines", style=discord.ButtonStyle.success, row=1)
            async def browse_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                if not await self._guard(interaction): return
                view = _OriginsView()
                await interaction.response.edit_message(
                    embed=view.build_embed(), view=view)

            @discord.ui.button(label="🔍 Rechercher", style=discord.ButtonStyle.secondary, row=1)
            async def search_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
                if not await self._guard(interaction): return
                await interaction.response.send_modal(_SearchOriginModal())

        def _make_collec_view(cat):
            rows = _grouped_rows(cat)
            view = _CollecView(rows, cat)
            return _build_embed(rows, cat, 1, view.total_pages), view

        # Navigateur d'origines (style "Browse Series")
        class _OriginsView(discord.ui.View):
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
                self._build_select()

            def build_embed(self):
                tp = max(1, (len(self.origins) + self.per - 1) // self.per)
                chunk = self.origins[self.page * self.per:(self.page + 1) * self.per]
                lines = "\n".join(
                    f"**{o}** · {self.owned.get(o, 0)}/{n}" for o, n in chunk) or "_(aucun résultat)_"
                q_txt = f" · recherche : **{self.query}**" if self.query else ""
                return discord.Embed(
                    title=f"📚 Origines — collection de {target_user.display_name}",
                    description=(f"_{len(self.origins)} origines · page {self.page + 1}/{tp}{q_txt}_\n"
                                  f"{lines}\n\n"
                                  f"_possédées/total._ Navigue avec ◀ ▶ ou choisis dans le menu."),
                    color=0xB9F23A,
                )

            def _build_select(self):
                self.clear_items()
                chunk = self.origins[self.page * self.per:(self.page + 1) * self.per]
                opts = [discord.SelectOption(
                            label=o[:100],
                            description=f"{self.owned.get(o, 0)}/{n} possédée(s)")
                        for o, n in chunk]
                sel = discord.ui.Select(placeholder="Sélectionne une origine…",
                                          options=opts or [discord.SelectOption(label="—")], row=0)
                async def _on_select(inter: discord.Interaction):
                    if inter.user.id != owner_id:
                        await inter.response.send_message("Pas ton menu.", ephemeral=True); return
                    chosen = sel.values[0]
                    emb, v = _make_collec_view(chosen)
                    await inter.response.edit_message(embed=emb, view=v)
                sel.callback = _on_select
                self.add_item(sel)
                # boutons page + retour
                tp = max(1, (len(self.origins) + self.per - 1) // self.per)
                prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary,
                                          row=1, disabled=self.page <= 0)
                nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary,
                                         row=1, disabled=self.page >= tp - 1)
                back = discord.ui.Button(label="↩ Retour", style=discord.ButtonStyle.danger, row=1)
                async def _prev(i):
                    if i.user.id != owner_id:
                        await i.response.send_message("Pas ton menu.", ephemeral=True); return
                    self.page -= 1; self._build_select()
                    await i.response.edit_message(embed=self.build_embed(), view=self)
                async def _nxt(i):
                    if i.user.id != owner_id:
                        await i.response.send_message("Pas ton menu.", ephemeral=True); return
                    self.page += 1; self._build_select()
                    await i.response.edit_message(embed=self.build_embed(), view=self)
                async def _back(i):
                    if i.user.id != owner_id:
                        await i.response.send_message("Pas ton menu.", ephemeral=True); return
                    emb, v = _make_collec_view(None)
                    await i.response.edit_message(embed=emb, view=v)
                prev.callback = _prev; nxt.callback = _nxt; back.callback = _back
                self.add_item(prev); self.add_item(nxt); self.add_item(back)

        class _SearchOriginModal(discord.ui.Modal, title="Rechercher une origine"):
            q = discord.ui.TextInput(label="Nom de l'origine / série",
                                      placeholder="ex: Genshin, Naruto…", required=True, max_length=100)
            async def on_submit(self, inter: discord.Interaction):
                view = _OriginsView(query=str(self.q.value))
                await inter.response.edit_message(embed=view.build_embed(), view=view)

        # Envoi initial
        first_rows = _grouped_rows(cat_val)
        if not first_rows:
            msg = f"**{target_user.display_name}** n'a pas de cartes"
            if rar_val:
                msg += f" {rar_val}"
            if cat_val:
                msg += f" pour **{cat_val}**"
            await interaction.response.send_message(msg + ".", ephemeral=True)
            return
        view = _CollecView(first_rows, cat_val)
        await interaction.response.send_message(
            embed=_build_embed(first_rows, cat_val, 1, view.total_pages), view=view)


    # === /card <nom> ===
    @bot.tree.command(name="card", description="Voir les details d'une carte par son nom")
    @app_commands.describe(nom="Nom de la carte (autocomplete)")
    async def card_cmd(interaction: discord.Interaction, nom: str):
        try:
            if interaction.guild:
                ok, target = _check_channel(interaction)
                if not ok:
                    await interaction.response.send_message(
                        f"Les commandes cartes sont reservees au salon {target}.",
                        ephemeral=True,
                    )
                    return
            card = card_get_by_name(nom.strip())
            if not card:
                await interaction.response.send_message(
                    f"Carte introuvable : `{nom}`. Utilise l'autocomplete.",
                    ephemeral=True,
                )
                return
            rarity = card.get("rarity", "common")
            color = RARITY_COLORS.get(rarity, 0x9aa0a6)
            emoji = _get_rarity_title_emoji(bot, rarity)
            origine = card.get("subtitle") or "?"
            univers = card.get("universe") or "?"
            rarity_display = "?????" if rarity == "secret" else rarity.upper()
            flavor = (card.get("flavor_subtitle") or "").strip()
            elem = card.get("element")
            elem_line = f"\n**Élément :** {_get_element_emoji(bot, elem)} {_ELEM_LABELS.get(elem, '')}" if elem else ""
            desc_parts = []
            if flavor:
                desc_parts.append(f"_**{flavor}**_")
            desc_parts.append(f"**Rareté :** {rarity_display}\n**Origine :** {origine}\n**Univers :** {univers}{elem_line}")
            desc = "\n\n".join(desc_parts)
            embed = discord.Embed(
                title=f"{emoji} {card['name']}"[:256],
                description=desc,
                color=color,
            )
            thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
            if thumb_url:
                embed.set_thumbnail(url=thumb_url)
            img_url, img_file = _resolve_card_image(card)
            if img_url:
                embed.set_image(url=img_url)
            elif img_file:
                embed.set_image(url="attachment://card.png")
            owners = card_owners_count(card["id"])
            if owners > 0:
                embed.set_footer(text=f"Possédée par {owners} joueur{'s' if owners > 1 else ''}")
            # View toujours present (au moins le bouton Modifier link)
            view = OwnersView(card["id"], card["name"])
            if img_file:
                await interaction.response.send_message(embed=embed, file=img_file, view=view)
            else:
                await interaction.response.send_message(embed=embed, view=view)
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = (f"❌ Erreur sur `/card` : `{type(e).__name__}`. "
                        f"Vérifie le nom (autocomplete recommandé). "
                        f"Si le bug persiste, signale-le au support TookBot.")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(err_msg[:1900], ephemeral=True)
                else:
                    await interaction.response.send_message(err_msg[:1900], ephemeral=True)
            except Exception:
                pass

    @card_cmd.autocomplete("nom")
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


    # === /essences : solde de monnaie ===
    @bot.tree.command(name="essences", description="Voir ton solde d'Essences ✨")
    @app_commands.describe(membre="Voir le solde de quelqu'un d'autre (defaut : toi)")
    async def essences_cmd(interaction: discord.Interaction, membre: discord.Member = None):
        from database import currency_get
        target = membre or interaction.user
        bal = currency_get(target.id)
        embed = discord.Embed(
            title="✨ Essences",
            description=f"**{target.display_name}** possède **{bal:,}** ✨".replace(",", " "),
            color=0xB9F23A,
        )
        if target.display_avatar:
            embed.set_thumbnail(url=str(target.display_avatar.url))
        await interaction.response.send_message(embed=embed, ephemeral=(membre is None))


    # === /show <carte> : montre une carte (avec bordure custom si appliquee) ===
    @bot.tree.command(name="show", description="Montre une de tes cartes (avec sa bordure custom)")
    @app_commands.describe(nom="Nom de la carte que tu possèdes")
    async def show_cmd(interaction: discord.Interaction, nom: str):
        from database import (card_get_by_name, user_card_count_owned,
                                card_customization_get, border_get, card_fusion_get)
        from services.card_render import render_user_card
        await interaction.response.defer()
        card = card_get_by_name(nom.strip())
        if not card:
            await interaction.followup.send(f"Carte introuvable : `{nom}`.", ephemeral=True)
            return
        uid = interaction.user.id
        if user_card_count_owned(uid, card["id"]) <= 0 and not _is_owner(uid):
            await interaction.followup.send(
                f"Tu ne possèdes pas **{card['name']}**. Fais `/roll` pour l'obtenir.",
                ephemeral=True)
            return
        rarity = card.get("rarity", "common")
        border_key = card_customization_get(uid, card["id"])
        fusion_level = card_fusion_get(uid, card["id"])
        # Couleur : bordure si equipee, sinon rareté
        color = BORDER_COLORS.get(border_key) if border_key else None
        if color is None:
            color = RARITY_COLORS.get(rarity, 0x9aa0a6)
        # Titre : ✨ devant si cosmetique + nom + espace + etoiles fusion
        title = ("✨ " if border_key else "") + card['name'] + (" " + "⭐" * fusion_level if fusion_level > 0 else "")
        embed = discord.Embed(title=title[:256], color=color)
        embed.set_footer(text=f"Carte de {interaction.user.display_name}",
                          icon_url=str(interaction.user.display_avatar.url) if interaction.user.display_avatar else None)
        file = None
        rendered_url = None
        if border_key or fusion_level > 0:
            border = border_get(border_key) if border_key else None
            rendered_url = render_user_card(uid, card["id"], border,
                                             fusion_level=fusion_level,
                                             fallback_url=card.get("image_url"))
        if rendered_url:
            # Sert le fichier local en attachment (pas besoin URL publique)
            import os as _os
            local_path = _os.path.join(
                _REPO_ROOT, rendered_url.lstrip("/").replace("/", _os.sep))
            if _os.path.exists(local_path):
                file = discord.File(local_path, filename="card.png")
                embed.set_image(url="attachment://card.png")
            else:
                print(f"[show] render introuvable: {local_path}")
        else:
            print(f"[show] render_user_card a retourne None (card={card['id']} "
                  f"border={border_key} fusion={fusion_level})")
        if file is None:
            img = card.get("image_url")
            if img and isinstance(img, str) and img.startswith("http"):
                embed.set_image(url=img)
        if file:
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(embed=embed)

    @show_cmd.autocomplete("nom")
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


    # === /cardcustom <carte> <bordure> : applique une bordure possedee ===
    @bot.tree.command(name="cardcustom", description="Applique une bordure que tu possèdes à une de tes cartes")
    @app_commands.describe(nom="Nom de la carte", bordure="Bordure à appliquer (ou 'aucune' pour retirer)")
    async def cardcustom_cmd(interaction: discord.Interaction, nom: str, bordure: str):
        from database import (card_get_by_name, user_card_count_owned,
                                user_border_has, user_border_consume, border_get,
                                card_customization_get, card_customization_set,
                                card_fusion_get)
        from services.card_render import render_user_card
        await interaction.response.defer(ephemeral=True)
        card = card_get_by_name(nom.strip())
        if not card:
            await interaction.followup.send(f"Carte introuvable : `{nom}`.", ephemeral=True)
            return
        uid = interaction.user.id
        if user_card_count_owned(uid, card["id"]) <= 0 and not _is_owner(uid):
            await interaction.followup.send(
                f"Tu ne possèdes pas **{card['name']}**.", ephemeral=True)
            return
        if card.get("rarity") == "secret":
            await interaction.followup.send(
                "Les cartes **secrètes** ne peuvent pas être customisées.", ephemeral=True)
            return
        bkey = (bordure or "").strip().lower()
        if bkey in ("aucune", "none", "retirer", "remove"):
            card_customization_set(uid, card["id"], None)
            await interaction.followup.send(
                f"Bordure retirée de **{card['name']}** (la bordure est consommée, pas rendue).",
                ephemeral=True)
            return
        # Deja equipee de cette bordure ? -> no-op, pas de consommation
        if card_customization_get(uid, card["id"]) == bkey:
            await interaction.followup.send(
                f"**{card['name']}** a déjà cette bordure équipée.", ephemeral=True)
            return
        border = border_get(bkey)
        if not border:
            await interaction.followup.send("Bordure introuvable.", ephemeral=True)
            return
        # Consomme 1 copie du stock (owner exempté)
        if _is_owner(uid):
            user_border_consume(uid, bkey)  # best-effort, pas bloquant
        elif not user_border_consume(uid, bkey):
            await interaction.followup.send(
                f"Tu n'as pas **{border['name']}** en stock. Achète-la via `/cardshop`.",
                ephemeral=True)
            return
        card_customization_set(uid, card["id"], bkey)
        render_user_card(uid, card["id"], border,
                          fusion_level=card_fusion_get(uid, card["id"]),
                          fallback_url=card.get("image_url"))
        await interaction.followup.send(
            f"✅ Bordure **{border['name']}** appliquée à **{card['name']}** ! "
            f"Utilise `/show {card['name']}` pour la montrer.", ephemeral=True)

    @cardcustom_cmd.autocomplete("nom")
    async def cardcustom_nom_autocomplete(interaction: discord.Interaction, current: str):
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

    @cardcustom_cmd.autocomplete("bordure")
    async def cardcustom_bordure_autocomplete(interaction: discord.Interaction, current: str):
        from database import user_borders_list
        try:
            uid = interaction.user.id
            owned = user_borders_list(uid)
            choices = [app_commands.Choice(
                name=f"{b['name']} (x{b['qty']})", value=b["border_key"]) for b in owned]
            choices.append(app_commands.Choice(name="Aucune (retirer)", value="aucune"))
            q = (current or "").strip().lower()
            if q:
                choices = [ch for ch in choices if q in ch.name.lower()]
            return choices[:25]
        except Exception:
            return []


    # === /cardinventory : items & cosmetiques en stock ===
    _FRAGMENTS_PER_MYTHIC = 5

    def _card_result_display(card, owner, essence_gain, already_owned):
        """Construit l'embed/fichier/vue d'une carte obtenue, MEME FORMAT que /roll.
        Retourne (embed, img_file_ou_None, view)."""
        rarity = card.get("rarity", "common")
        color = RARITY_COLORS.get(rarity, 0x9aa0a6)
        emoji = _get_rarity_title_emoji(bot, rarity)
        origine = card.get("subtitle") or "?"
        univers = card.get("universe") or "?"
        rarity_display = "?????" if rarity == "secret" else rarity.upper()
        flavor = (card.get("flavor_subtitle") or "").strip()
        essence_line = f"**Essences :** +{essence_gain} ✨" + (" _(doublon x2)_" if already_owned else "")
        _elem = card.get("element")
        if _elem:
            essence_line += f"\n**Élément :** {_get_element_emoji(bot, _elem)} {_ELEM_LABELS.get(_elem, '')}"
        desc_parts = []
        if flavor:
            desc_parts.append(f"_**{flavor}**_")
        desc_parts.append(f"**Rareté :** {rarity_display}\n**Origine :** {origine}\n"
                          f"**Univers :** {univers}\n{essence_line}")
        embed = discord.Embed(title=f"{emoji} {card['name']}"[:256],
                              description="\n\n".join(desc_parts), color=color)
        thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
        if thumb_url:
            embed.set_thumbnail(url=thumb_url)
        img_url, img_file = _resolve_card_image(card)
        if img_url:
            embed.set_image(url=img_url)
        elif img_file:
            embed.set_image(url="attachment://card.png")
        avatar_url = str(owner.display_avatar.url) if owner.display_avatar else None
        embed.set_footer(text=f"Appartient à {owner.display_name}", icon_url=avatar_url)
        return embed, img_file, OwnersView(card["id"], card["name"])

    def _inv_embed(target):
        from database import (user_borders_list, user_item_get, roll_bonus_available)
        frags = user_item_get(target.id, "mythic_fragment")
        golden = user_item_get(target.id, "golden_roll")
        rolls = roll_bonus_available(target.id)
        borders = user_borders_list(target.id)
        embed = discord.Embed(title=f"🎒 Inventaire — {target.display_name}", color=0xB9F23A)
        if target.display_avatar:
            embed.set_thumbnail(url=str(target.display_avatar.url))
        lines = [
            f"🎟️ **Rolls bonus** : {rolls}  _(utilisables au_ `/roll`_)_",
            f"🔴 **Fragments Mythic** : {frags} / {_FRAGMENTS_PER_MYTHIC}  _(→ 1 mythic)_",
            f"{_golden_emoji(bot)} **Golden Rolls** : {golden}  _(→ 1 légendaire garanti)_",
        ]
        embed.add_field(name="Objets", value="\n".join(lines), inline=False)
        if borders:
            bl = [f"🖼 **{b['name']}** × {b['qty']}" for b in borders]
            embed.add_field(name="Bordures (non utilisées)",
                            value="\n".join(bl) + "\n_Applique via_ `/cardcustom`.", inline=False)
        return embed, frags, golden

    class _InventoryView(discord.ui.View):
        def __init__(self, owner_id):
            super().__init__(timeout=180)
            self.owner_id = owner_id
            ge = discord.utils.get(bot.emojis, name="goldenroll")
            if ge:
                self.use_golden.emoji = ge

        async def _guard(self, interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Ce n'est pas ton inventaire.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Utiliser Golden Roll", style=discord.ButtonStyle.success, emoji="🌈")
        async def use_golden(self, interaction, btn):
            if not await self._guard(interaction):
                return
            from database import (user_item_consume, card_pick_random_exact_rarity, user_card_add,
                                  user_card_count_owned, essence_reward_add, ESSENCE_REWARDS)
            uid = interaction.user.id
            if not user_item_consume(uid, "golden_roll", 1):
                await interaction.response.send_message("Tu n'as pas de Golden Roll.", ephemeral=True)
                return
            card = card_pick_random_exact_rarity("legendary")
            if not card:
                from database import user_item_add
                user_item_add(uid, "golden_roll", 1)  # remboursé
                await interaction.response.send_message("Aucune légendaire dispo, Golden Roll rendu.", ephemeral=True)
                return
            already = user_card_count_owned(uid, card["id"]) > 0
            user_card_add(uid, card["id"])
            base = ESSENCE_REWARDS.get("legendary", 220) * (2 if already else 1)
            ess = essence_reward_add(uid, base)
            embed, img_file, view = _card_result_display(card, interaction.user, ess, already)
            # PUBLIC, meme forme qu'un /roll, avec mention du coupon hors embed
            content = f"{_golden_emoji(bot)} **Roll effectué avec un coupon Golden Roll** (légendaire garanti)"
            if img_file:
                await interaction.response.send_message(content=content, embed=embed, file=img_file, view=view)
            else:
                await interaction.response.send_message(content=content, embed=embed, view=view)

        @discord.ui.button(label="Craft Mythic (5 fragments)", style=discord.ButtonStyle.danger, emoji="🔴")
        async def craft_mythic(self, interaction, btn):
            if not await self._guard(interaction):
                return
            from database import user_item_consume, card_pick_random_exact_rarity, user_card_add
            uid = interaction.user.id
            if not user_item_consume(uid, "mythic_fragment", _FRAGMENTS_PER_MYTHIC):
                await interaction.response.send_message(
                    f"Il te faut {_FRAGMENTS_PER_MYTHIC} Fragments Mythic.", ephemeral=True)
                return
            card = card_pick_random_exact_rarity("mythic")
            if not card:
                from database import user_item_add
                user_item_add(uid, "mythic_fragment", _FRAGMENTS_PER_MYTHIC)  # remboursé
                await interaction.response.send_message("Aucune mythic dispo, fragments rendus.", ephemeral=True)
                return
            user_card_add(uid, card["id"])
            embed, *_ = _inv_embed(interaction.user)
            await interaction.response.edit_message(embed=embed, view=_InventoryView(uid))
            await interaction.followup.send(
                f"🔴 **Craft !** Tu obtiens **{card['name']}** 🔴 (mythic) !", ephemeral=True)

    @bot.tree.command(name="cardinventory", description="Tes objets : rolls, fragments mythic, golden rolls, bordures")
    @app_commands.describe(membre="Voir l'inventaire de quelqu'un d'autre (defaut : toi)")
    async def cardinventory_cmd(interaction: discord.Interaction, membre: discord.Member = None):
        target = membre or interaction.user
        embed, frags, golden = _inv_embed(target)
        view = None
        if membre is None and (golden > 0 or frags >= _FRAGMENTS_PER_MYTHIC):
            view = _InventoryView(interaction.user.id)
            for ch in view.children:
                if "Golden" in ch.label:
                    ch.disabled = golden <= 0
                if "Craft" in ch.label:
                    ch.disabled = frags < _FRAGMENTS_PER_MYTHIC
        await interaction.response.send_message(
            embed=embed, view=(view or discord.utils.MISSING), ephemeral=(membre is None))


    # Autocomplete partage : cartes dont le user a des DOUBLONS (>1 copie)
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


    # === /cardrecycle : doublons -> essences ===
    @bot.tree.command(name="cardrecycle", description="Recycle tes doublons en Essences ✨")
    @app_commands.describe(nom="Carte à recycler (tu gardes toujours 1 exemplaire)",
                            quantite="Nombre de doublons à recycler (defaut : tous)")
    async def cardrecycle_cmd(interaction: discord.Interaction, nom: str, quantite: int = None):
        from database import (card_get_by_name, user_card_count_owned,
                                user_card_remove_copies, currency_add, ESSENCE_RECYCLE)
        card = card_get_by_name(nom.strip())
        if not card:
            await interaction.response.send_message(f"Carte introuvable : `{nom}`.", ephemeral=True)
            return
        uid = interaction.user.id
        owned = user_card_count_owned(uid, card["id"])
        dupes = max(0, owned - 1)  # garde toujours 1
        if dupes <= 0:
            await interaction.response.send_message(
                f"Tu n'as pas de doublon de **{card['name']}** à recycler.", ephemeral=True)
            return
        qty = dupes if quantite is None else max(1, min(int(quantite), dupes))
        rarity = card.get("rarity", "common")
        per = ESSENCE_RECYCLE.get(rarity, 6)
        removed = user_card_remove_copies(uid, card["id"], qty)
        from database import essence_reward_add, currency_get
        gain = essence_reward_add(uid, per * removed)  # applique bonus roue du jour
        new_bal = currency_get(uid)
        await interaction.response.send_message(
            f"♻️ {removed} doublon(s) de **{card['name']}** recyclé(s) → **+{gain}** ✨\n"
            f"Solde : **{new_bal}** ✨", ephemeral=True)

    @cardrecycle_cmd.autocomplete("nom")
    async def cardrecycle_autocomplete(interaction: discord.Interaction, current: str):
        return await _dup_cards_autocomplete(interaction, current)


    # === /cardfuse : monte le niveau d'etoiles d'une carte via ses doublons ===
    @bot.tree.command(name="cardfuse", description="Fusionne tes doublons pour ajouter une étoile à une carte (max 5)")
    @app_commands.describe(nom="Carte à faire monter en étoile")
    async def cardfuse_cmd(interaction: discord.Interaction, nom: str):
        from database import (card_get_by_name, user_card_count_owned,
                                user_card_remove_copies, card_fusion_get, card_fusion_set,
                                card_customization_get, border_get,
                                user_card_lock_one,
                                FUSION_STAR_COSTS, FUSION_MAX_STARS)
        from services.card_render import render_user_card
        await interaction.response.defer(ephemeral=True)
        card = card_get_by_name(nom.strip())
        if not card:
            await interaction.followup.send(f"Carte introuvable : `{nom}`.", ephemeral=True)
            return
        uid = interaction.user.id
        if card.get("rarity") == "secret":
            await interaction.followup.send(
                "Les cartes **secrètes** ne peuvent pas être fusionnées.", ephemeral=True)
            return
        level = card_fusion_get(uid, card["id"])
        if level >= FUSION_MAX_STARS:
            await interaction.followup.send(
                f"**{card['name']}** est déjà au niveau max ({'⭐' * FUSION_MAX_STARS}).",
                ephemeral=True)
            return
        # Cout = nombre d'exemplaires requis (inclut la carte qui garde les etoiles)
        cost = FUSION_STAR_COSTS[level]
        owned = user_card_count_owned(uid, card["id"])
        if owned < cost:
            await interaction.followup.send(
                f"Il te faut **{cost}** exemplaires de **{card['name']}** pour passer à "
                f"{'⭐' * (level + 1)} (tu en as {owned}). "
                f"L'un d'eux garde les étoiles, les {cost - 1} autres sont consommés.",
                ephemeral=True)
            return
        # Consomme cost-1 copies, la derniere garde les etoiles
        removed = user_card_remove_copies(uid, card["id"], cost - 1)
        new_level = level + 1
        card_fusion_set(uid, card["id"], new_level)
        # Verrouille UNE copie (celle qui porte les etoiles). Les doublons en trop
        # restent echangeables et recyclables.
        user_card_lock_one(uid, card["id"])
        # Regenere le rendu (garde bordure si equipee)
        border_key = card_customization_get(uid, card["id"])
        border = border_get(border_key) if border_key else None
        render_user_card(uid, card["id"], border, fusion_level=new_level,
                          fallback_url=card.get("image_url"))
        nxt = (f"\nProchaine étoile : **{FUSION_STAR_COSTS[new_level]}** exemplaires."
               if new_level < FUSION_MAX_STARS else "\nNiveau **max** atteint !")
        await interaction.followup.send(
            f"✨ **{card['name']}** fusionnée ! {removed} exemplaires consommés → {'⭐' * new_level}{nxt}\n"
            f"🔒 L'exemplaire étoilé devient non échangeable (tes doublons en trop restent libres).\n"
            f"Vois-la avec `/show {card['name']}`.", ephemeral=True)

    @cardfuse_cmd.autocomplete("nom")
    async def cardfuse_autocomplete(interaction: discord.Interaction, current: str):
        # Comme _dup_cards mais EXCLUT les cartes deja maxées (5⭐) et les secret
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
                "WHERE uc.user_id = ? AND c.rarity != 'secret' AND LOWER(c.name) LIKE ? "
                "GROUP BY uc.card_id HAVING n > 1 AND lvl < 5 ORDER BY c.name LIMIT 25",
                (uid, f"%{q}%")).fetchall()
            conn.close()
            return [app_commands.Choice(name=f"{r['name']} (x{r['n']}{' ' + '⭐'*r['lvl'] if r['lvl'] else ''})"[:100],
                                          value=r["name"][:100]) for r in rows]
        except Exception:
            return []


    # === /cardup : tier-up (doublons d'une rareté -> 1 carte rareté au-dessus) ===
    @bot.tree.command(name="cardup",
                       description="Sacrifie les doublons de tes cartes 5⭐ pour 1 carte aléatoire de la rareté au-dessus")
    @app_commands.describe(rarete="Rareté des doublons (de cartes 5⭐) à sacrifier")
    @app_commands.choices(rarete=[
        app_commands.Choice(name="Common → Rare", value="common"),
        app_commands.Choice(name="Rare → Epic", value="rare"),
        app_commands.Choice(name="Epic → Legendary", value="epic"),
        app_commands.Choice(name="Legendary → Mythic", value="legendary"),
    ])
    async def cardup_cmd(interaction: discord.Interaction, rarete: app_commands.Choice[str]):
        from database import (CARDUP_NEXT, CARDUP_COST, user_duplicate_count_by_rarity,
                               user_consume_duplicates_by_rarity, card_pick_random_exact_rarity,
                               user_card_add)
        await interaction.response.defer()
        src = rarete.value
        nxt = CARDUP_NEXT.get(src)
        cost = CARDUP_COST.get(src)
        if not nxt or not cost:
            await interaction.followup.send("Rareté invalide.", ephemeral=True)
            return
        uid = interaction.user.id
        avail = user_duplicate_count_by_rarity(uid, src)
        if avail < cost:
            await interaction.followup.send(
                f"Il te faut **{cost}** doublons **{src}** de cartes **déjà 5⭐** "
                f"(copies en trop au-delà de la carte étoilée). Tu en as **{avail}**.\n"
                f"_Maxe une carte {src} à 5⭐ avec `/cardfuse`, ses doublons en trop deviennent utilisables ici._",
                ephemeral=True)
            return
        removed = user_consume_duplicates_by_rarity(uid, src, cost)
        reward = card_pick_random_exact_rarity(nxt)
        if not reward:
            await interaction.followup.send(
                f"Aucune carte **{nxt}** disponible pour la récompense (réessaie plus tard).",
                ephemeral=True)
            return
        user_card_add(uid, reward["id"])
        emoji = _get_rarity_title_emoji(bot, nxt)
        color = RARITY_COLORS.get(nxt, 0x9aa0a6)
        embed = discord.Embed(
            title=f"⬆️ Tier-up réussi !",
            description=(f"{removed} doublons **{src}** (cartes 5⭐) sacrifiés →\n"
                          f"# {emoji} {reward['name']}\n"
                          f"**Rareté :** {nxt.upper()} · **Origine :** {reward.get('subtitle') or '?'}"),
            color=color,
        )
        img_url, img_file = _resolve_card_image(reward)
        if img_url:
            embed.set_image(url=img_url)
        elif img_file:
            embed.set_image(url="attachment://card.png")
        if img_file:
            await interaction.followup.send(embed=embed, file=img_file)
        else:
            await interaction.followup.send(embed=embed)


    # === /cardprofile : voir un profil OU setup (params optionnels) ===
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
                       description="Voir un profil de cartes (ou définir tes 3 cartes vedettes via setup)")
    @app_commands.describe(
        membre="Profil à afficher (defaut : toi)",
        setup_gauche="(setup) carte de gauche",
        setup_milieu="(setup) carte du milieu (mise en avant)",
        setup_droite="(setup) carte de droite")
    async def cardprofile_cmd(interaction: discord.Interaction,
                               membre: discord.Member = None,
                               setup_gauche: str = None,
                               setup_milieu: str = None,
                               setup_droite: str = None):
        from database import (card_get_by_name, user_card_count_owned, card_profile_set,
                               card_profile_get, user_card_count, user_card_rarity_breakdown,
                               currency_get, user_card_fusion_map, user_borders_list)
        from services.card_profile import build_profile_image
        import os as _os

        # --- Mode SETUP : au moins un des 3 champs carte fourni ---
        if setup_gauche or setup_milieu or setup_droite:
            if not (setup_gauche and setup_milieu and setup_droite):
                await interaction.response.send_message(
                    "Pour configurer ton profil, remplis les **3** cartes "
                    "(setup_gauche, setup_milieu, setup_droite).", ephemeral=True)
                return
            uid = interaction.user.id
            resolved = []
            for label, nm in (("gauche", setup_gauche), ("milieu", setup_milieu),
                               ("droite", setup_droite)):
                card = card_get_by_name(nm.strip())
                if not card:
                    await interaction.response.send_message(
                        f"Carte introuvable ({label}) : `{nm}`.", ephemeral=True)
                    return
                if user_card_count_owned(uid, card["id"]) <= 0 and not _is_owner(uid):
                    await interaction.response.send_message(
                        f"Tu ne possèdes pas **{card['name']}** ({label}).", ephemeral=True)
                    return
                resolved.append(card["id"])
            card_profile_set(uid, resolved[0], resolved[1], resolved[2])
            await interaction.response.send_message(
                "✅ Profil de cartes mis à jour ! Tape `/cardprofile` pour le voir.",
                ephemeral=True)
            return

        # --- Mode VOIR ---
        await interaction.response.defer()
        target = membre or interaction.user
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
        # Indice de chance : moyenne ponderee par rareté vs moyenne attendue (50% = moyen)
        _pts = {"common": 1, "rare": 2, "epic": 5, "legendary": 25, "mythic": 100, "secret": 200}
        _total_pts = sum(_pts.get(r, 1) * n for r, n in breakdown.items())
        _avg = (_total_pts / total) if total else 0
        _expected = 3.85  # esperance de points/roll selon les poids de tirage
        luck = max(0, min(100, round(_avg / _expected * 50))) if total else 0
        from database import compute_player_combat_stats
        cs = compute_player_combat_stats(uid)
        def _fmt(n):
            return f"{int(n):,}".replace(",", " ")
        DIV = "══════════════════════════════"
        bonus_txt = f"\n_bonus fusion +{min(50, cs['stars'])}%_" if cs['stars'] else ""

        embed = discord.Embed(
            title=f"🃏 Profil de cartes ｜ {target.display_name}",
            color=0xB9F23A,
        )
        # Haut : 3 colonnes (champs inline)
        embed.add_field(name="📦 Collection",
                        value=f"{_fmt(total)} cartes\n{_fmt(uniq)} uniques", inline=True)
        embed.add_field(name="✨ Essences", value=f"{_fmt(essences)}", inline=True)
        embed.add_field(name="🍀 Chance", value=f"{luck}%", inline=True)
        # Reste : un seul bloc. Le 1er separateur = NOM du champ (evite la ligne vide).
        block = (
            f"⚔️ **Stats de combat**\n"
            f"❤️ PV **{_fmt(cs['hp'])}**　　🗡️ ATK **{_fmt(cs['atk'])}**{bonus_txt}\n"
            f"{DIV}\n"
            f"⭐ **Fusionnées** ｜ {_fmt(fused)}　　🖼️ **Bordures** ｜ {_fmt(borders_stock)}\n"
            f"{DIV}\n"
            f"🎴 **Raretés**\n"
            f"{rar_line or '—'}"
        )
        embed.add_field(name=DIV, value=block, inline=False)

        if target.display_avatar:
            embed.set_thumbnail(url=str(target.display_avatar.url))
        embed.set_footer(text=f"Profil de {target.display_name}",
                          icon_url=str(target.display_avatar.url) if target.display_avatar else None)
        file = None
        if profile:
            rel = build_profile_image(uid, profile)
            if rel:
                local_path = _os.path.join(_REPO_ROOT, rel.lstrip("/").replace("/", _os.sep))
                if _os.path.exists(local_path):
                    file = discord.File(local_path, filename="profile.png")
                    embed.set_image(url="attachment://profile.png")
        if not profile:
            note = ("Aucune carte vedette définie. " if target == interaction.user
                    else f"{target.display_name} n'a pas défini de cartes vedettes. ")
            if target == interaction.user:
                note += "Configure-les avec `/cardprofile setup_gauche: … setup_milieu: … setup_droite: …`."
            embed.description = note
        if file:
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(embed=embed)

    for _p in ("setup_gauche", "setup_milieu", "setup_droite"):
        cardprofile_cmd.autocomplete(_p)(_owned_cards_autocomplete)


    # === /cardwish <carte> : ajoute/retire de la wishlist ===
    # Cap : 3 par defaut, 6 pour les membres du serveur support.
    def _wishlist_max(user_id):
        return 6 if _is_support_member(bot, user_id) else 3
    @bot.tree.command(name="cardwish", description="Ajoute ou retire une carte de ta wishlist")
    @app_commands.describe(nom="Carte à ajouter/retirer de ta wishlist")
    async def cardwish_cmd(interaction: discord.Interaction, nom: str):
        from database import card_get_by_name, wishlist_toggle, wishlist_has, wishlist_list
        card = card_get_by_name(nom.strip())
        if not card:
            await interaction.response.send_message(f"Carte introuvable : `{nom}`.", ephemeral=True)
            return
        wl_max = _wishlist_max(interaction.user.id)
        # Cap : seulement si on AJOUTE (toggle off toujours autorisé)
        if not wishlist_has(interaction.user.id, card["id"]):
            if len(wishlist_list(interaction.user.id)) >= wl_max:
                base = (f"Wishlist pleine ({wl_max} max). Retire une carte avec "
                        f"`/cardwishlist` (boutons) avant d'en ajouter une autre.")
                if wl_max >= 6:
                    await interaction.response.send_message(base, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        base + "\n💡 Sur le **serveur support** tu as **6 emplacements** "
                               "au lieu de 3. Rejoins-le :",
                        view=_support_view(), ephemeral=True)
                return
        added = wishlist_toggle(interaction.user.id, card["id"])
        count = len(wishlist_list(interaction.user.id))
        emoji = RARITY_EMOJIS.get(card.get("rarity"), "⚪")
        if added:
            msg = (f"💖 **{card['name']}** {emoji} ajoutée à ta wishlist ({count}/{wl_max}). "
                   f"Tu seras ping si quelqu'un la tire.")
        else:
            msg = f"💔 **{card['name']}** {emoji} retirée de ta wishlist ({count}/{wl_max})."
        await interaction.response.send_message(msg, ephemeral=True)

    @cardwish_cmd.autocomplete("nom")
    async def cardwish_autocomplete(interaction: discord.Interaction, current: str):
        from database import get_db
        try:
            conn = get_db(); c = conn.cursor()
            q = (current or "").strip().lower()
            rows = c.execute("SELECT name FROM cards WHERE LOWER(name) LIKE ? "
                             "AND COALESCE(not_obtainable,0)=0 ORDER BY name LIMIT 25",
                             (f"%{q}%",)).fetchall()
            conn.close()
            return [app_commands.Choice(name=r["name"][:100], value=r["name"][:100]) for r in rows]
        except Exception:
            return []

    # === /cardwishlist [membre] : voir la wishlist ===
    @bot.tree.command(name="cardwishlist", description="Voir ta wishlist (ou celle d'un membre)")
    @app_commands.describe(membre="Membre dont voir la wishlist (defaut : toi)")
    async def cardwishlist_cmd(interaction: discord.Interaction, membre: discord.Member = None):
        from database import wishlist_list, wishlist_toggle
        target = membre or interaction.user
        is_self = (target.id == interaction.user.id)
        items = wishlist_list(target.id)

        def _build_wl_embed():
            its = wishlist_list(target.id)
            emb = discord.Embed(
                title=f"💖 Wishlist — {target.display_name} ({len(its)}/{_wishlist_max(target.id)})",
                color=0xff5fa2)
            if target.display_avatar:
                emb.set_thumbnail(url=str(target.display_avatar.url))
            if not its:
                emb.description = ("Wishlist vide." + (" Ajoute des cartes avec `/cardwish`."
                                    if is_self else ""))
            else:
                emb.description = "\n".join(
                    f"{RARITY_EMOJIS.get(i['rarity'],'⚪')} **{i['name']}** · _{i.get('universe') or '?'}_"
                    for i in its[:40])
            return emb, its

        embed, items = _build_wl_embed()

        # Boutons de suppression (seulement sur sa propre wishlist)
        class _WishlistView(discord.ui.View):
            def __init__(self, wl_items):
                super().__init__(timeout=120)
                for it in wl_items[:5]:
                    btn = discord.ui.Button(
                        label=f"🗑 {it['name'][:70]}",
                        style=discord.ButtonStyle.danger)
                    btn.callback = self._make_cb(it["card_id"])
                    self.add_item(btn)

            def _make_cb(self, card_id):
                async def _cb(inter: discord.Interaction):
                    if inter.user.id != interaction.user.id:
                        await inter.response.send_message("Pas ta wishlist.", ephemeral=True)
                        return
                    wishlist_toggle(interaction.user.id, card_id)  # retire
                    new_embed, new_items = _build_wl_embed()
                    new_view = _WishlistView(new_items) if new_items else None
                    await inter.response.edit_message(embed=new_embed, view=new_view)
                return _cb

        view = _WishlistView(items) if (is_self and items) else None
        await interaction.response.send_message(embed=embed, view=view)

    # === /cardtop <categorie> : classements ===
    @bot.tree.command(name="cardtop", description="Classements cartes (collection, mythiques, essences, fusions, chance)")
    @app_commands.describe(categorie="Type de classement")
    @app_commands.choices(categorie=[
        app_commands.Choice(name="Valeur de collection", value="value"),
        app_commands.Choice(name="Mythiques", value="mythic"),
        app_commands.Choice(name="Essences", value="essences"),
        app_commands.Choice(name="Fusions (étoiles)", value="fusions"),
        app_commands.Choice(name="Indice de chance", value="luck"),
    ])
    async def cardtop_cmd(interaction: discord.Interaction,
                           categorie: app_commands.Choice[str] = None):
        from database import (leaderboard_card_aggregates, leaderboard_essences,
                               leaderboard_fusions)
        await interaction.response.defer()
        cat = categorie.value if categorie else "value"

        def _name(uid):
            try:
                m = interaction.guild.get_member(int(uid)) if interaction.guild else None
                if m:
                    return m.display_name
                u = bot.get_user(int(uid))
                return u.name if u else f"Joueur {str(uid)[:6]}"
            except Exception:
                return f"Joueur {str(uid)[:6]}"

        rows = []   # (uid, value_str, sort_key)
        title = "🏆 Classement"
        if cat == "essences":
            title = "🏆 Top Essences ✨"
            for uid, e in leaderboard_essences(10):
                rows.append((uid, f"{e} ✨"))
        elif cat == "fusions":
            title = "🏆 Top Fusions ⭐"
            for uid, cards, stars in leaderboard_fusions(10):
                rows.append((uid, f"{stars} ⭐ ({cards} carte(s))"))
        else:
            agg = leaderboard_card_aggregates()
            if cat == "mythic":
                title = "🏆 Top Mythiques 🔴"
                ranked = sorted(((u, d) for u, d in agg.items() if d["mythic"] > 0),
                                key=lambda x: x[1]["mythic"], reverse=True)[:10]
                for u, d in ranked:
                    rows.append((u, f"{d['mythic']} mythique(s)"))
            elif cat == "luck":
                title = "🏆 Top Indice de chance 🍀"
                cand = []
                for u, d in agg.items():
                    if d["total"] >= 10:  # min 10 cartes pour etre classe
                        luck = max(0, min(100, round(d["pts"] / d["total"] / 3.85 * 50)))
                        cand.append((u, luck, d["total"]))
                cand.sort(key=lambda x: x[1], reverse=True)
                for u, luck, tot in cand[:10]:
                    rows.append((u, f"{luck}% _( {tot} cartes )_"))
            else:  # value
                title = "🏆 Top Valeur de collection 💎"
                ranked = sorted(agg.items(), key=lambda x: x[1]["pts"], reverse=True)[:10]
                for u, d in ranked:
                    rows.append((u, f"{d['pts']} pts ({d['total']} cartes)"))

        medals = ["🥇", "🥈", "🥉"] + [f"`#{i}`" for i in range(4, 11)]
        if not rows:
            desc = "Pas encore de données pour ce classement."
        else:
            desc = "\n".join(f"{medals[i]} **{_name(uid)}** — {val}"
                             for i, (uid, val) in enumerate(rows))
        embed = discord.Embed(title=title, description=desc, color=0xF1C40F)
        embed.set_footer(text="Classement global (tous serveurs)")
        await interaction.followup.send(embed=embed)


    # === /bossspawn : fait apparaitre un boss (owner, test) ===
    @bot.tree.command(name="bossspawn", description="[Owner] Fait apparaître un boss à combattre dans ce salon")
    @app_commands.describe(tier="Difficulté du boss (1 à 5)",
                            dummies="[Test] Nb de combattants factices à ajouter (0-4)")
    @app_commands.choices(tier=[
        app_commands.Choice(name="Tier 1 (facile)", value=1),
        app_commands.Choice(name="Tier 2", value=2),
        app_commands.Choice(name="Tier 3", value=3),
        app_commands.Choice(name="Tier 4", value=4),
        app_commands.Choice(name="Tier 5 (raid)", value=5),
    ])
    async def bossspawn_cmd(interaction: discord.Interaction,
                             tier: app_commands.Choice[int] = None,
                             dummies: int = 0):
        if not _is_owner(interaction.user.id):
            await interaction.response.send_message("Owner uniquement.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("À utiliser dans un serveur.", ephemeral=True)
            return
        from services.card_boss import spawn_boss, add_dummy_participants
        t = tier.value if tier else 1
        await interaction.response.send_message(f"🐲 Invocation d'un boss Tier {t}…", ephemeral=True)
        bid = await spawn_boss(bot, interaction.guild.id, interaction.channel.id, tier=t)
        if not bid:
            await interaction.followup.send("Échec de l'invocation (salon/carte introuvable).", ephemeral=True)
            return
        d = max(0, min(4, int(dummies or 0)))
        if d:
            add_dummy_participants(bid, d)
            await interaction.followup.send(
                f"🤖 {d} combattant(s) factice(s) ajouté(s). Rejoins pour compléter l'équipe.",
                ephemeral=True)


    # === /cardhelp : guide complet du système de cartes ===
    @bot.tree.command(name="cardhelp", description="Guide complet du système de cartes TookBot")
    async def cardhelp_cmd(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🃏 Guide des cartes TookBot",
            description=("Collectionne, fusionne, customise et échange des cartes de tes "
                         "persos préférés ! Voici tout ce que tu peux faire."),
            color=0xB9F23A,
        )
        embed.add_field(
            name="🎴 Obtenir des cartes",
            value=("**/roll `[univers]`** — tire une carte aléatoire (cooldown **1h**, "
                   "**30 min sur le serveur support**). Chaque roll donne des **Essences ✨** "
                   "(plus la carte est rare, plus tu en gagnes ; doublon = ×2).\n"
                   "**/daily** — récompense quotidienne (Essences + TookCoins + streak).\n"
                   "**Drop Events** — des cartes apparaissent dans certains salons : "
                   "tape le **code affiché sur l'image** pour la gagner (1er servi)."),
            inline=False,
        )
        embed.add_field(
            name="✨ Essences & boutique",
            value=("**/essences** — ton solde. **/cardshop** — achète cartes & cosmétiques.\n"
                   "**/cardrecycle `<carte>`** — transforme tes doublons en Essences "
                   "(tu gardes toujours 1 exemplaire)."),
            inline=False,
        )
        embed.add_field(
            name="⭐ Fusion (prestige)",
            value=("**/cardfuse `<carte>`** — consomme des exemplaires d'une même carte pour "
                   "lui ajouter une **étoile** (jusqu'à 5). Coût croissant : 2 → 3 → 4 → 5 → 6 "
                   "exemplaires. Une carte fusionnée devient **non-échangeable** "
                   "(recyclage uniquement)."),
            inline=False,
        )
        embed.add_field(
            name="🖼 Cosmétiques",
            value=("**/cardcustom `<carte>` `<bordure>`** — applique une bordure (achetée au shop, "
                   "consommée à l'usage). **/cardinventory** — tes cosmétiques en stock.\n"
                   "**/show `<carte>`** — montre une carte avec sa bordure et ses étoiles."),
            inline=False,
        )
        embed.add_field(
            name="🪪 Profil & classements",
            value=("**/cardprofile `[membre]`** — stats + image de tes 3 cartes vedettes "
                   "(`setup_*` pour les choisir). **/cardtop `<catégorie>`** — classements globaux."),
            inline=False,
        )
        embed.add_field(
            name="💖 Wishlist & échange",
            value=("**/cardwish `<carte>`** — wishlist (3 max, **6 sur le support**) : tu es ping "
                   "quand quelqu'un la tire. **/cardwishlist** — voir/retirer. "
                   "**/cardtrade `<joueur>`** — échange multi-cartes."),
            inline=False,
        )
        embed.set_footer(text="Astuce : rejoins le serveur support pour 2 rolls/h et 6 wishes !")
        await interaction.response.send_message(embed=embed, view=_support_view(), ephemeral=True)


    # === /cardshop : boutique hebdo (6 slots) ===
    @bot.tree.command(name="cardshop", description="Boutique de cartes et cosmétiques (Essences ✨)")
    async def cardshop_cmd(interaction: discord.Interaction):
        from database import card_shop_get_slots, currency_get
        from services.card_shop import build_shop_image, purchase_slot
        import os as _os
        await interaction.response.defer()
        slots = card_shop_get_slots()
        active = [s for s in slots if s.get("enabled") and s.get("item_type") and s.get("item_ref")]
        if not active:
            await interaction.followup.send(
                "La boutique est vide pour le moment. Reviens plus tard !", ephemeral=True)
            return
        try:
            rel = build_shop_image()
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[cardshop] build_shop_image err: {e!r}")
            rel = None
        bal = currency_get(interaction.user.id)

        class _ShopView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=300)
                for s in slots:
                    n = int(s["slot"])
                    enabled = bool(s.get("enabled") and s.get("item_type") and s.get("item_ref"))
                    label = (s.get("label") or f"Slot {n}")[:40]
                    price = int(s.get("price") or 0)
                    btn = discord.ui.Button(
                        label=f"{label} · {price} ✨" if enabled else f"Slot {n}",
                        style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
                        disabled=not enabled,
                        row=0 if n <= 3 else 1,
                        custom_id=f"shop_buy_{n}",
                    )
                    btn.callback = self._make_cb(n)
                    self.add_item(btn)

            def _make_cb(self, slot_n):
                async def _cb(inter: discord.Interaction):
                    res = purchase_slot(inter.user.id, slot_n)
                    if res.get("ok"):
                        await inter.response.send_message(
                            f"✅ **{res['item_name']}** acheté pour {res['price']} ✨ !\n"
                            f"Nouveau solde : **{res['new_balance']}** ✨"
                            + (f"\nApplique-la avec `/cardcustom`." if res['item_type'] == 'border' else ""),
                            ephemeral=True)
                    else:
                        await inter.response.send_message(
                            f"❌ {res.get('error', 'Achat échoué.')}", ephemeral=True)
                return _cb

        embed = discord.Embed(
            title="🛒 Card Shop",
            description=f"Ton solde : **{bal}** ✨\nClique sur un bouton pour acheter.",
            color=0xB9F23A,
        )
        file = None
        if rel:
            local_path = _os.path.join(
                _REPO_ROOT, rel.lstrip("/").replace("/", _os.sep))
            if _os.path.exists(local_path):
                file = discord.File(local_path, filename="cardshop.png")
                embed.set_image(url="attachment://cardshop.png")
            else:
                print(f"[cardshop] image generee mais introuvable: {local_path}")
        else:
            print("[cardshop] build_shop_image a retourne None")
        view = _ShopView()
        if file:
            await interaction.followup.send(embed=embed, file=file, view=view)
        else:
            await interaction.followup.send(embed=embed, view=view)


    # === /cardtrade <user> ===
    def _parse_card_list(s: str) -> list[tuple[str, int]]:
        """Parse 'Nom1, Nom2 x2, Nom3' -> [(name, qty), ...]. Cap qty 1-99."""
        out = []
        if not s: return out
        for part in s.split(","):
            p = part.strip()
            if not p: continue
            qty = 1
            # Suffix 'xN' ou ' xN'
            import re as _re
            m = _re.match(r"^(.*?)\s*[xX]\s*(\d{1,2})\s*$", p)
            if m:
                p = m.group(1).strip()
                qty = max(1, min(int(m.group(2)), 99))
            if p:
                out.append((p, qty))
        return out

    def _resolve_card_names(items: list[tuple[str, int]]) -> tuple[list, list]:
        """Resolve names -> [(card_id, qty)]. Retourne (ok, errors)."""
        resolved = []; errors = []
        for name, qty in items:
            card = card_get_by_name(name)
            if not card:
                errors.append(f"`{name}`")
                continue
            resolved.append((card["id"], qty))
        return resolved, errors

    def _verify_ownership(user_id, items: list[tuple[int, int]]) -> list[str]:
        """Retourne liste d'erreurs si user ne possede pas la qty demandee."""
        errs = []
        # Aggregate par card_id (au cas ou meme carte 2x dans liste)
        agg = {}
        for cid, qty in items:
            agg[cid] = agg.get(cid, 0) + qty
        for cid, qty in agg.items():
            owned = user_card_count_owned(user_id, cid, only_tradeable=True)
            if owned < qty:
                card = card_get_by_name("")  # placeholder
                from database import get_db
                conn = get_db(); cc = conn.cursor()
                r = cc.execute("SELECT name FROM cards WHERE id = ?", (cid,)).fetchone()
                conn.close()
                nm = r["name"] if r else f"#{cid}"
                errs.append(f"`{nm}` (possede {owned}/{qty})")
        return errs

    def _build_trade_embed(trade_id: int, sender: discord.Member,
                             receiver: discord.Member, status: str = "pending") -> discord.Embed:
        offer = card_trade_items(trade_id, side="offer")
        request = card_trade_items(trade_id, side="request")

        def _fmt(items):
            if not items:
                return "_(rien)_"
            lines = []
            for it in items:
                em = RARITY_EMOJIS.get(it["rarity"], "⚪")
                qty = f" ×{it['qty']}" if it["qty"] > 1 else ""
                lines.append(f"{em} **{it['name']}**{qty}")
            return "\n".join(lines)

        status_color = {
            "pending":   0xC8F050,
            "accepted":  0x4ade80,
            "refused":   0xff3d57,
            "cancelled": 0x9aa0a6,
            "countered": 0xfbbf24,
        }.get(status, 0xC8F050)
        status_label = {
            "pending":   "⏳ En attente",
            "accepted":  "✅ Acceptée",
            "refused":   "❌ Refusée",
            "cancelled": "⊘ Annulée",
            "countered": "🔄 Contre-offre",
        }.get(status, status)

        embed = discord.Embed(
            title=f"🔄 Trade #{trade_id} · {status_label}",
            color=status_color,
        )
        embed.add_field(name=f"📤 {sender.display_name} propose",
                         value=_fmt(offer)[:1024], inline=True)
        embed.add_field(name=f"📥 {receiver.display_name} donnerait",
                         value=_fmt(request)[:1024], inline=True)
        embed.set_footer(text=f"{sender} ↔ {receiver}")
        return embed


    class TradeView(discord.ui.View):
        def __init__(self, trade_id: int, sender_id: int, receiver_id: int):
            super().__init__(timeout=24 * 3600)
            self.trade_id = trade_id
            self.sender_id = int(sender_id)
            self.receiver_id = int(receiver_id)

        async def _disable_all(self, interaction: discord.Interaction):
            for child in self.children:
                child.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

        @discord.ui.button(label="Accepter", style=discord.ButtonStyle.success, emoji="✅")
        async def accept_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
            if interaction.user.id != self.receiver_id:
                await interaction.response.send_message(
                    "Seul le destinataire peut accepter ce trade.", ephemeral=True)
                return
            trade = card_trade_get(self.trade_id)
            if not trade or trade["status"] != "pending":
                await interaction.response.send_message(
                    "Ce trade n'est plus actif.", ephemeral=True)
                return
            # Re-verify ownership
            offer = card_trade_items(self.trade_id, side="offer")
            request = card_trade_items(self.trade_id, side="request")
            sender_items = [(it["card_id"], it["qty"]) for it in offer]
            recv_items = [(it["card_id"], it["qty"]) for it in request]
            err_s = _verify_ownership(self.sender_id, sender_items)
            err_r = _verify_ownership(self.receiver_id, recv_items)
            if err_s or err_r:
                msg = "Trade impossible : cartes manquantes.\n"
                if err_s: msg += f"<@{self.sender_id}> : {', '.join(err_s)}\n"
                if err_r: msg += f"<@{self.receiver_id}> : {', '.join(err_r)}"
                card_trade_set_status(self.trade_id, "cancelled")
                await interaction.response.send_message(msg, ephemeral=False,
                    allowed_mentions=discord.AllowedMentions.none())
                await self._disable_all(interaction)
                return
            # Transfer atomically
            for cid, qty in sender_items:
                for _ in range(qty):
                    user_card_transfer_one(self.sender_id, self.receiver_id, cid)
            for cid, qty in recv_items:
                for _ in range(qty):
                    user_card_transfer_one(self.receiver_id, self.sender_id, cid)
            card_trade_set_status(self.trade_id, "accepted")
            sender = interaction.guild.get_member(self.sender_id) or interaction.user
            receiver = interaction.guild.get_member(self.receiver_id) or interaction.user
            new_embed = _build_trade_embed(self.trade_id, sender, receiver, "accepted")
            await interaction.response.edit_message(embed=new_embed, view=None)

        @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger, emoji="❌")
        async def refuse_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
            if interaction.user.id not in (self.sender_id, self.receiver_id):
                await interaction.response.send_message(
                    "Tu n'es pas concerne par ce trade.", ephemeral=True)
                return
            trade = card_trade_get(self.trade_id)
            if not trade or trade["status"] != "pending":
                await interaction.response.send_message(
                    "Ce trade n'est plus actif.", ephemeral=True)
                return
            new_status = "cancelled" if interaction.user.id == self.sender_id else "refused"
            card_trade_set_status(self.trade_id, new_status)
            sender = interaction.guild.get_member(self.sender_id) or interaction.user
            receiver = interaction.guild.get_member(self.receiver_id) or interaction.user
            new_embed = _build_trade_embed(self.trade_id, sender, receiver, new_status)
            await interaction.response.edit_message(embed=new_embed, view=None)

        @discord.ui.button(label="Contre-offre", style=discord.ButtonStyle.secondary, emoji="🔄")
        async def counter_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
            if interaction.user.id != self.receiver_id:
                await interaction.response.send_message(
                    "Seul le destinataire peut faire une contre-offre.", ephemeral=True)
                return
            trade = card_trade_get(self.trade_id)
            if not trade or trade["status"] != "pending":
                await interaction.response.send_message(
                    "Ce trade n'est plus actif.", ephemeral=True)
                return
            # Open counter modal. La contre-offre inverse roles : receiver
            # devient sender, sender devient receiver.
            modal = TradeModal(target_user_id=self.sender_id,
                                 is_counter=True, original_trade_id=self.trade_id,
                                 view_to_disable=self)
            await interaction.response.send_modal(modal)


    class TradeModal(discord.ui.Modal, title="Proposer un trade"):
        offer_field = discord.ui.TextInput(
            label="Tes cartes (laisse vide pour ne rien donner)",
            placeholder="Naruto Uzumaki, Goku x2, Vegeta",
            required=False, max_length=400, style=discord.TextStyle.paragraph,
        )
        request_field = discord.ui.TextInput(
            label="Cartes voulues (laisse vide pour un don)",
            placeholder="Gojo Satoru, Itadori Yuji",
            required=False, max_length=400, style=discord.TextStyle.paragraph,
        )

        def __init__(self, target_user_id: int, is_counter: bool = False,
                      original_trade_id: int | None = None,
                      view_to_disable=None):
            super().__init__()
            self.target_user_id = int(target_user_id)
            self.is_counter = is_counter
            self.original_trade_id = original_trade_id
            self.view_to_disable = view_to_disable
            # Pre-fill avec contenu trade original (roles inverses pour counter)
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
                # Counter sender = original receiver. Son 'offer' (ce qu'il
                # donne) = ce qu'on lui demandait avant = orig_request.
                # Son 'request' (ce qu'il veut) = ce qu'on lui offrait avant
                # = orig_offer.
                self.offer_field.default = _fmt(orig_request)
                self.request_field.default = _fmt(orig_offer)
                self.title = f"Contre-offre (trade #{original_trade_id})"

        async def on_submit(self, interaction: discord.Interaction):
            try:
                offer_parsed = _parse_card_list(str(self.offer_field.value or ""))
                request_parsed = _parse_card_list(str(self.request_field.value or ""))
                # Au moins UN des deux cotes doit avoir une carte (don unilateral OK)
                if not offer_parsed and not request_parsed:
                    await interaction.response.send_message(
                        "Indique au moins 1 carte d'un des deux côtés.",
                        ephemeral=True)
                    return

                # Resolve names -> ids
                offer_items, errs1 = _resolve_card_names(offer_parsed)
                request_items, errs2 = _resolve_card_names(request_parsed)
                if errs1 or errs2:
                    msg = "Cartes introuvables : " + ", ".join(errs1 + errs2)
                    await interaction.response.send_message(msg, ephemeral=True)
                    return

                # Verify ownership
                sender_id = interaction.user.id
                receiver_id = self.target_user_id
                err_s = _verify_ownership(sender_id, offer_items)
                err_r = _verify_ownership(receiver_id, request_items)
                if err_s:
                    await interaction.response.send_message(
                        f"Tu ne possedes pas : {', '.join(err_s)}", ephemeral=True)
                    return
                if err_r:
                    await interaction.response.send_message(
                        f"Le destinataire ne possede pas : {', '.join(err_r)}",
                        ephemeral=True)
                    return

                # Si contre-offre : marque ancien trade
                if self.is_counter and self.original_trade_id:
                    card_trade_set_status(self.original_trade_id, "countered")
                    if self.view_to_disable:
                        try:
                            for child in self.view_to_disable.children:
                                child.disabled = True
                            if interaction.message:
                                await interaction.message.edit(view=self.view_to_disable)
                        except Exception:
                            pass

                gid = interaction.guild.id if interaction.guild else None
                cid = interaction.channel.id if interaction.channel else None
                tid = card_trade_create(sender_id, receiver_id, gid, cid,
                                          offer_items, request_items)

                receiver_member = interaction.guild.get_member(receiver_id) if interaction.guild else None
                if not receiver_member:
                    await interaction.response.send_message(
                        "Destinataire introuvable sur ce serveur.", ephemeral=True)
                    card_trade_set_status(tid, "cancelled")
                    return

                embed = _build_trade_embed(tid, interaction.user, receiver_member, "pending")
                view = TradeView(tid, sender_id, receiver_id)
                await interaction.response.send_message(
                    content=f"{receiver_member.mention}",
                    embed=embed, view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
                msg = await interaction.original_response()
                card_trade_set_status(tid, "pending", message_id=msg.id)
            except Exception as e:
                import traceback; traceback.print_exc()
                try:
                    await interaction.response.send_message(
                        f"❌ Erreur création trade : `{type(e).__name__}`. "
                        f"Vérifie que les noms de cartes existent (format : "
                        f"`Nom1, Nom2 x3, Nom3`). Re-essaie ou contacte le support.",
                        ephemeral=True)
                except Exception:
                    pass


    # === /cardsuggest : suggestion communaute (support guild only) ===
    SUGGEST_CHANNEL_ID = 1513592894265757716
    SUPPORT_GUILD_ID = int((os.getenv("SUPPORT_GUILD_ID") or "0").strip() or 0)

    @bot.tree.command(name="cardsuggest",
                       description="Suggerer un personnage a ajouter au catalogue (serveur support)")
    @app_commands.describe(
        nom="Nom du personnage",
        univers="Categorie",
        origine="Anime/jeu/film d'origine (ex : Naruto, Genshin Impact)",
        rarete="Rarete suggeree (optionnel)",
        image_url="URL d'image (optionnel si tu joins une image en piece jointe)",
        image="Piece jointe image (optionnel si URL fournie)",
    )
    @app_commands.choices(univers=[
        app_commands.Choice(name="Anime / Manga",  value="Anime"),
        app_commands.Choice(name="Jeu Vidéo",      value="Jeu Vidéo"),
        app_commands.Choice(name="Film / Série",   value="Film/Série"),
        app_commands.Choice(name="Comics",          value="Comics"),
        app_commands.Choice(name="Autre",           value="Autre"),
    ])
    @app_commands.choices(rarete=[
        app_commands.Choice(name="⚪ Common",      value="common"),
        app_commands.Choice(name="🔵 Rare",         value="rare"),
        app_commands.Choice(name="🟣 Epic",         value="epic"),
        app_commands.Choice(name="🟠 Legendary",    value="legendary"),
        app_commands.Choice(name="🔴 Mythic",       value="mythic"),
    ])
    async def cardsuggest(interaction: discord.Interaction,
                            nom: str,
                            univers: app_commands.Choice[str],
                            origine: str = None,
                            rarete: app_commands.Choice[str] = None,
                            image_url: str = None,
                            image: discord.Attachment = None):
        # /cardsuggest dispo partout (tous serveurs + DM)

        # Resolve image
        final_url = None
        source_type = None
        if image:
            ct = (image.content_type or "").lower()
            if not ct.startswith("image/"):
                await interaction.response.send_message(
                    "La piece jointe doit etre une image (PNG/JPG/WEBP/GIF).",
                    ephemeral=True)
                return
            if image.size > 8 * 1024 * 1024:
                await interaction.response.send_message(
                    "Image trop lourde (max 8 Mo).", ephemeral=True)
                return
            final_url = image.url   # Discord CDN, stable
            source_type = "attachment"
        elif image_url:
            url = image_url.strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                await interaction.response.send_message(
                    "L'URL doit commencer par http:// ou https://.", ephemeral=True)
                return
            final_url = url
            source_type = "url"
        else:
            await interaction.response.send_message(
                "Tu dois fournir soit une URL (`image_url`) soit une piece jointe (`image`).",
                ephemeral=True)
            return

        nom_clean = nom.strip()[:100]
        if not nom_clean:
            await interaction.response.send_message("Nom invalide.", ephemeral=True)
            return

        try:
            sid = card_suggestion_add(
                suggester_id=interaction.user.id,
                suggester_name=str(interaction.user),
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel.id if interaction.channel else None,
                name=nom_clean,
                universe=univers.value,
                subtitle=(origine or "").strip()[:80] or None,
                image_url=final_url,
                source_type=source_type,
                proposed_rarity=rarete.value if rarete else None,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Erreur enregistrement suggestion : `{type(e).__name__}`. "
                f"Re-essaie. Si le bug persiste, contacte le support TookBot.",
                ephemeral=True)
            return

        # Embed pour forward vers salon support
        embed = discord.Embed(
            title=f"💠 Suggestion #{sid} reçue",
            description=f"**{nom_clean}**\n_{univers.value}{' · ' + origine if origine else ''}_",
            color=0xB9F23A,
        )
        embed.set_image(url=final_url)
        avatar_url = str(interaction.user.display_avatar.url) if interaction.user.display_avatar else None
        embed.set_footer(text=f"Suggérée par {interaction.user.display_name}", icon_url=avatar_url)

        # Forward vers le salon support
        support_channel = bot.get_channel(SUGGEST_CHANNEL_ID)
        forward_ok = False
        if support_channel:
            try:
                await support_channel.send(embed=embed)
                forward_ok = True
            except Exception as e:
                print(f"[cardsuggest] forward err: {e}")

        # Reponse ephemerale au user (rien de visible publiquement)
        msg = ("✅ **Suggestion envoyée à l'équipe.**\n"
                f"Numéro #{sid}. Tu seras prévenu si elle est approuvée.")
        if not forward_ok:
            msg += "\n_(Le forward salon support a échoué mais ta suggestion est bien enregistrée.)_"
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="cardtrade", description="Proposer un echange de cartes a un autre joueur")
    @app_commands.describe(joueur="Joueur a qui proposer l'echange")
    async def cardtrade(interaction: discord.Interaction, joueur: discord.Member):
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    f"Les commandes cartes sont reservees au salon {target}.",
                    ephemeral=True)
                return
        if joueur.id == interaction.user.id:
            await interaction.response.send_message(
                "Tu ne peux pas trader avec toi-meme.", ephemeral=True)
            return
        if joueur.bot:
            await interaction.response.send_message(
                "Impossible de trader avec un bot.", ephemeral=True)
            return
        await interaction.response.send_modal(TradeModal(target_user_id=joueur.id))
