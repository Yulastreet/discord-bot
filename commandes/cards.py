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
)


ROLL_COOLDOWN_SECONDS = 3600  # 1h, par serveur

RARITY_COLORS = {
    "common":    0x9aa0a6,  # gris
    "rare":      0x4cb5f9,  # bleu
    "epic":      0xa86dff,  # violet
    "legendary": 0xffa726,  # orange
    "mythic":    0xff3d57,  # rouge
    "secret":    0x1c1c1e,  # noir profond (laisse le rainbow border briller)
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

        # Cooldown 1h par (user, guild) - skip pour owner
        uid = interaction.user.id
        gid = interaction.guild.id if interaction.guild else None
        if not _is_owner(uid) and gid:
            last = roll_cooldown_get(uid, gid)
            if last:
                try:
                    # last stocke en UTC naive, parse comme UTC-aware
                    last_dt = _dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    last_dt = last_dt.replace(tzinfo=_dt.timezone.utc)
                    now_ts = _time.time()
                    last_ts = last_dt.timestamp()
                    elapsed = now_ts - last_ts
                    remain = ROLL_COOLDOWN_SECONDS - elapsed
                    if remain > 0:
                        rh = int(remain // 3600)
                        rm = int((remain % 3600) // 60)
                        rs = int(remain % 60)
                        wait = f"{rh}h {rm}min" if rh > 0 else f"{rm}min {rs}s"
                        # Discord timestamp absolu epoch (cohérent avec wait)
                        ready_at = int(now_ts + remain)
                        await interaction.response.send_message(
                            f"⏰ Cooldown actif. Prochain roll <t:{ready_at}:R> (dans {wait}).",
                            ephemeral=True,
                        )
                        return
                except ValueError:
                    pass

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
        user_card_add(uid, card["id"])
        if not _is_owner(uid) and gid:
            now_iso = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            roll_cooldown_set(uid, gid, now_iso)

        # Embed minimaliste
        rarity = card.get("rarity", "common")
        color = RARITY_COLORS.get(rarity, 0x9aa0a6)
        emoji = _get_rarity_title_emoji(bot, rarity)
        origine = card.get("subtitle") or "?"
        univers = card.get("universe") or "?"
        rarity_display = "?????" if rarity == "secret" else rarity.upper()
        flavor = (card.get("flavor_subtitle") or "").strip()
        desc_parts = []
        if flavor:
            desc_parts.append(f"_**{flavor}**_")
        desc_parts.append(f"**Rareté :** {rarity_display}\n**Origine :** {origine}\n**Univers :** {univers}")
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
        img = card.get("image_url")
        if img and isinstance(img, str) and img.startswith("http"):
            embed.set_image(url=img)
        avatar_url = str(interaction.user.display_avatar.url) if interaction.user.display_avatar else None
        embed.set_footer(text=f"Appartient à {interaction.user.display_name}",
                          icon_url=avatar_url)
        view = OwnersView(card["id"], card["name"])
        await interaction.response.send_message(embed=embed, view=view)

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
    @app_commands.describe(membre="Membre dont voir la collection (defaut : toi)",
                            rarete="Filtre par rarete")
    @app_commands.choices(rarete=[
        app_commands.Choice(name="common", value="common"),
        app_commands.Choice(name="rare", value="rare"),
        app_commands.Choice(name="epic", value="epic"),
        app_commands.Choice(name="legendary", value="legendary"),
        app_commands.Choice(name="mythic", value="mythic"),
    ])
    async def collection(interaction: discord.Interaction,
                          membre: discord.Member = None,
                          rarete: app_commands.Choice[str] = None):
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    f"Les commandes cartes sont reservees au salon {target}.",
                    ephemeral=True,
                )
                return
        target_user = membre or interaction.user
        rar_val = rarete.value if rarete else None
        cards = user_card_list(target_user.id, rarity=rar_val)
        total = user_card_count(target_user.id)
        if not cards:
            msg = f"**{target_user.display_name}** n'a pas encore de cartes"
            if rar_val:
                msg += f" {rar_val}"
            msg += "."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        # Regroupe par carte (count duplicates)
        grouped: dict[int, dict] = {}
        for c in cards:
            cid = c["card_id"]
            if cid not in grouped:
                grouped[cid] = {**c, "count": 0, "nt_count": 0}
            grouped[cid]["count"] += 1
            if c.get("not_tradeable"):
                grouped[cid]["nt_count"] += 1
        rows = list(grouped.values())

        # Pagine (25 max par embed)
        PAGE_SIZE = 25
        page = 1
        page_rows = rows[:PAGE_SIZE]
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)

        embed = discord.Embed(
            title=f"🃏 Collection de {target_user.display_name}",
            description=f"**{total}** cartes ({len(rows)} uniques)" + (f" • filtre **{rar_val}**" if rar_val else ""),
            color=0xB9F23A,
        )
        lines = []
        for c in page_rows:
            emoji = RARITY_EMOJIS.get(c["rarity"], "⚪")
            count = f" x{c['count']}" if c["count"] > 1 else ""
            nt = c.get("nt_count", 0)
            nt_tag = f" 🔒{nt}" if nt > 0 else ""
            lines.append(f"{emoji} **{c['name']}**{count}{nt_tag} · _{c.get('universe') or '?'}_")
        embed.description += "\n\n" + "\n".join(lines)
        embed.set_footer(text=f"Page {page}/{total_pages} • Pour plus de pages utiliser bouton (a venir)")
        if target_user.display_avatar:
            embed.set_thumbnail(url=str(target_user.display_avatar.url))
        await interaction.response.send_message(embed=embed)


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
            desc_parts = []
            if flavor:
                desc_parts.append(f"_**{flavor}**_")
            desc_parts.append(f"**Rareté :** {rarity_display}\n**Origine :** {origine}\n**Univers :** {univers}")
            desc = "\n\n".join(desc_parts)
            embed = discord.Embed(
                title=f"{emoji} {card['name']}"[:256],
                description=desc,
                color=color,
            )
            thumb_url = _get_rarity_custom_emoji_url(bot, rarity)
            if thumb_url:
                embed.set_thumbnail(url=thumb_url)
            img = card.get("image_url")
            if img and isinstance(img, str) and img.startswith("http"):
                embed.set_image(url=img)
            owners = card_owners_count(card["id"])
            if owners > 0:
                embed.set_footer(text=f"Possédée par {owners} joueur{'s' if owners > 1 else ''}")
            # View toujours present (au moins le bouton Modifier link)
            view = OwnersView(card["id"], card["name"])
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
            label="Tes cartes (separees par virgule)",
            placeholder="Naruto Uzumaki, Goku x2, Vegeta",
            required=True, max_length=400, style=discord.TextStyle.paragraph,
        )
        request_field = discord.ui.TextInput(
            label="Cartes voulues",
            placeholder="Gojo Satoru, Itadori Yuji",
            required=True, max_length=400, style=discord.TextStyle.paragraph,
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
                offer_parsed = _parse_card_list(str(self.offer_field.value))
                request_parsed = _parse_card_list(str(self.request_field.value))
                if not offer_parsed or not request_parsed:
                    await interaction.response.send_message(
                        "Tu dois proposer au moins 1 carte de chaque cote.",
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
