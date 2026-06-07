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
    card_owners_count,
    user_card_add, user_card_list, user_card_count,
    user_card_settings_get, user_card_settings_set_last_roll,
    guild_card_config_get, guild_card_config_set,
)


ROLL_COOLDOWN_SECONDS = 3 * 3600  # 3h (TookBot+ plus tard : 1h)

RARITY_COLORS = {
    "common":    0x9aa0a6,  # gris
    "rare":      0x4cb5f9,  # bleu
    "epic":      0xa86dff,  # violet
    "legendary": 0xffa726,  # orange
    "mythic":    0xff3d57,  # rouge
}
RARITY_EMOJIS = {
    "common":    "⚪",
    "rare":      "🔵",
    "epic":      "🟣",
    "legendary": "🟠",
    "mythic":    "🔴",
}

# Mapping rarete -> nom emoji custom Discord (support server)
_RARITY_CUSTOM_NAME = {
    "rare":      "rare",
    "epic":      "epic",
    "legendary": "legendaire",
    "mythic":    "mythic",
}
_rarity_emoji_cache: dict[str, str] = {}

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
            f"Les commandes `/roll`, `/mycards`, `/card` ne marcheront que dans ce salon.",
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

    # === /roll ===
    @bot.tree.command(name="roll", description="Tire une carte aleatoire de la collection")
    async def roll(interaction: discord.Interaction):
        if interaction.guild:
            ok, target = _check_channel(interaction)
            if not ok:
                await interaction.response.send_message(
                    f"Les commandes cartes sont reservees au salon {target}. Utilise `/cardsetup` (admin) pour changer.",
                    ephemeral=True,
                )
                return

        # Cooldown (skip pour owner)
        uid = interaction.user.id
        if not _is_owner(uid):
            settings = user_card_settings_get(uid)
            last = settings.get("last_roll_at")
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

        # Pioche + add
        card = card_roll_random()
        if not card:
            await interaction.response.send_message("Erreur pioche carte.", ephemeral=True)
            return
        user_card_add(uid, card["id"])
        if not _is_owner(uid):
            now_iso = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            user_card_settings_set_last_roll(uid, now_iso)

        # Embed minimaliste
        rarity = card.get("rarity", "common")
        color = RARITY_COLORS.get(rarity, 0x9aa0a6)
        emoji = RARITY_EMOJIS.get(rarity, "⚪")
        origine = card.get("subtitle") or "?"
        univers = card.get("universe") or "?"
        desc = f"**Rareté :** {rarity.upper()}\n**Origine :** {origine}\n**Univers :** {univers}"
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
        await interaction.response.send_message(embed=embed)

    # === /collection ===
    @bot.tree.command(name="mycards", description="Voir ta collection de cartes (ou celle de quelqu'un)")
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
                grouped[cid] = {**c, "count": 0}
            grouped[cid]["count"] += 1
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
            lines.append(f"{emoji} **{c['name']}**{count} · _{c.get('universe') or '?'}_")
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
            emoji = RARITY_EMOJIS.get(rarity, "⚪")
            origine = card.get("subtitle") or "?"
            desc = f"**Rareté :** {rarity.upper()}\n**Origine :** {origine}"
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
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = f"Erreur /card : `{type(e).__name__}: {e}`"
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
