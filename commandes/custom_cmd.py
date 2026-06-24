"""Custom commands : enregistrement dynamique par guild.

Chaque commande custom devient une vraie slash command `/<nom>` propre au serveur
(guild-scoped). Le builder est cote dashboard (page /custom-commands).

Variables supportees dans le texte ou les champs d'embed :
  {user}        -> @mention de l'utilisateur
  {user.name}   -> display name
  {user.id}     -> ID
  {server}      -> nom du serveur
  {channel}     -> #salon mention
  {date}        -> date du jour (YYYY-MM-DD)
  {time}        -> heure HH:MM
"""
from __future__ import annotations

import datetime as _dt
import json as _json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import os as _os

from database import (
    custom_cmds_list, custom_cmd_get, custom_cmd_increment_uses,
    has_premium_grant, user_has_active_entitlement,
)


_SKU_TOOKBOT_PLUS  = _os.getenv("SKU_TOOKBOT_PLUS", "").strip() or None
_DISCORD_OWNER_ID  = _os.getenv("DISCORD_OWNER_ID", "").strip() or None


def _owner_has_tookbot_plus(guild) -> bool:
    """True si l'owner du serveur a TookBot+ (grant manuel OU SKU OR owner global)."""
    if not guild:
        return False
    owner_id = getattr(guild, "owner_id", None)
    if not owner_id:
        return False
    oid = str(owner_id)
    if has_premium_grant(oid, feature="tookbot_plus", inherit_all=False):
        return True
    if _SKU_TOOKBOT_PLUS and user_has_active_entitlement(oid, sku_id=_SKU_TOOKBOT_PLUS):
        return True
    if _DISCORD_OWNER_ID and oid == _DISCORD_OWNER_ID:
        return True
    return False


def _interpolate(s: str, *, user: discord.abc.User, guild: discord.Guild,
                 channel: discord.abc.GuildChannel) -> str:
    if not s:
        return s
    now = _dt.datetime.now()
    return (s
            .replace("{user.mention}", getattr(user, "mention", "?"))
            .replace("{user.name}",    getattr(user, "display_name", "?"))
            .replace("{user.id}",      str(user.id))
            .replace("{user}",         getattr(user, "mention", "?"))
            .replace("{server}",       guild.name if guild else "?")
            .replace("{channel}",      getattr(channel, "mention", "?"))
            .replace("{date}",         now.strftime("%Y-%m-%d"))
            .replace("{time}",         now.strftime("%H:%M")))


def _build_embed_from_json(raw: str, *, user, guild, channel) -> Optional[discord.Embed]:
    try:
        data = _json.loads(raw or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    title = _interpolate(data.get("title") or "", user=user, guild=guild, channel=channel)
    desc  = _interpolate(data.get("description") or "", user=user, guild=guild, channel=channel)
    color_raw = data.get("color")
    try:
        color_int = int(color_raw, 16) if isinstance(color_raw, str) else int(color_raw or 0x3498DB)
    except Exception:
        color_int = 0x3498DB
    embed = discord.Embed(title=title or None, description=desc or None, color=color_int)
    if data.get("image"):
        embed.set_image(url=data["image"])
    if data.get("thumbnail"):
        embed.set_thumbnail(url=data["thumbnail"])
    if data.get("footer"):
        embed.set_footer(text=_interpolate(data["footer"], user=user, guild=guild, channel=channel))
    for f in (data.get("fields") or []):
        if not isinstance(f, dict):
            continue
        embed.add_field(
            name=_interpolate(f.get("name") or "​", user=user, guild=guild, channel=channel)[:256],
            value=_interpolate(f.get("value") or "​", user=user, guild=guild, channel=channel)[:1024],
            inline=bool(f.get("inline", False)),
        )
    return embed


async def _execute_custom(interaction: discord.Interaction, name: str):
    """Logique d'execution partagee : recupere la commande en DB et repond."""
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ Indisponible en message privé, utilise cette commande dans un serveur.", ephemeral=True)
        return
    # Gate TookBot+ : les commandes custom sont payantes. Owner du serveur doit avoir TookBot+.
    if not _owner_has_tookbot_plus(interaction.guild):
        embed = discord.Embed(
            title="✦ Feature TookBot+",
            description=(
                "Les commandes custom (`/" + name + "`) sont reservees aux serveurs "
                "dont le proprietaire a un abonnement **TookBot+**.\n\n"
                "Le proprietaire peut s'abonner ici :\n"
                "**https://dashboard.tookbot.click/subscription**"
            ),
            color=0xffa726,
        )
        embed.set_footer(text="TookBot+ : Bot Customization + Commandes custom + Soutien direct")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    row = custom_cmd_get(interaction.guild.id, name)
    if not row or not row.get("enabled"):
        await interaction.response.send_message(
            f"❌ Commande `/{name}` introuvable ou désactivée.",
            ephemeral=True)
        return
    try:
        if row.get("use_embed"):
            embed = _build_embed_from_json(
                row.get("response_embed") or "{}",
                user=interaction.user, guild=interaction.guild,
                channel=interaction.channel,
            )
            if not embed:
                raise ValueError("embed invalide")
            await interaction.response.send_message(embed=embed)
        else:
            text = _interpolate(
                row.get("response_text") or "",
                user=interaction.user, guild=interaction.guild,
                channel=interaction.channel,
            )
            if not text.strip():
                text = "_(réponse vide)_"
            await interaction.response.send_message(text)
        custom_cmd_increment_uses(row["id"])
    except Exception as e:
        print(f"[custom_cmd] exec err {name}: {type(e).__name__}: {e}")
        try:
            await interaction.response.send_message(
                "❌ Cette commande n'a pas pu s'exécuter. Réessaie plus tard.",
                ephemeral=True)
        except Exception:
            pass


def _make_callback(name: str):
    """Cree un callback async qui exec la commande custom 'name'."""
    async def _cb(interaction: discord.Interaction):
        await _execute_custom(interaction, name)
    return _cb


def _build_command(name: str, description: Optional[str]) -> app_commands.Command:
    desc = (description or "Commande personnalisée du serveur")[:100]
    return app_commands.Command(
        name=name,
        description=desc,
        callback=_make_callback(name),
    )


async def sync_custom_commands_for_guild(bot: commands.Bot, guild_id) -> int:
    """Recharge les slash commands custom d'un serveur (utilise au boot + apres
    save/delete depuis le dashboard). Retourne le nb de commandes enregistrees."""
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return 0
    try:
        bot.tree.clear_commands(guild=guild)
    except Exception as e:
        print(f"[custom_cmd] clear {guild_id} fail : {e}")
    rows = custom_cmds_list(guild.id, enabled_only=True)
    added = 0
    for r in rows:
        try:
            cmd = _build_command(r["name"], r.get("description"))
            bot.tree.add_command(cmd, guild=guild, override=True)
            added += 1
        except Exception as e:
            print(f"[custom_cmd] add err {r['name']} ({guild_id}): {e}")
    try:
        await bot.tree.sync(guild=guild)
    except Exception as e:
        print(f"[custom_cmd] sync {guild_id} fail : {e}")
    return added


def setup_custom_cmd_commands(bot: commands.Bot):
    """Conserve pour compat. Aucune commande globale `/cmd` n'est plus enregistree :
    les commandes custom sont ajoutees dynamiquement par guild via
    sync_custom_commands_for_guild() au demarrage du bot et apres chaque
    modification depuis le dashboard."""
    return
