"""Custom commands : dispatcher /cmd <name>.

Le builder est cote dashboard (page /custom-commands). Ce module n'expose
qu'une seule slash command runtime qui execute la commande choisie.

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

from database import (
    custom_cmds_list, custom_cmd_get, custom_cmd_increment_uses,
)


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


async def _cmd_name_autocomplete(interaction: discord.Interaction, current: str):
    if not interaction.guild:
        return []
    cur = (current or "").lower()
    rows = custom_cmds_list(interaction.guild.id, enabled_only=True)
    matches = [r for r in rows if cur in r["name"].lower()][:25]
    return [app_commands.Choice(name=f"/{r['name']} — {r.get('description') or 'sans description'}"[:100],
                                value=r["name"]) for r in matches]


def setup_custom_cmd_commands(bot: commands.Bot):

    @bot.tree.command(name="cmd", description="Exécute une commande custom de ce serveur")
    @app_commands.describe(nom="Nom de la commande custom (autocomplete)")
    @app_commands.autocomplete(nom=_cmd_name_autocomplete)
    async def cmd_run(interaction: discord.Interaction, nom: str):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Pas dispo en DM.", ephemeral=True)
            return
        row = custom_cmd_get(interaction.guild.id, nom)
        if not row or not row.get("enabled"):
            await interaction.response.send_message(
                f"❌ Commande `{nom}` introuvable ou désactivée. "
                "Va sur le dashboard pour la créer.",
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
            print(f"[custom_cmd] exec err {nom}: {type(e).__name__}: {e}")
            try:
                await interaction.response.send_message(
                    f"❌ Erreur d'exécution : `{type(e).__name__}`. Vérifie la config dans le dashboard.",
                    ephemeral=True)
            except Exception:
                pass
