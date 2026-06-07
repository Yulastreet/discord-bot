"""Slash commands /remind <duree> <texte>, /reminders, /unremind.

Durees supportees : `1h`, `30min`, `2d`, `1d2h30min`, `45s`. Max 30 jours.
"""
from __future__ import annotations

import datetime as _dt
import re
import discord
from discord import app_commands

from database import (
    reminder_add, reminders_list_user, reminder_delete,
)


_DURATION_RE = re.compile(
    r"(?:(?P<d>\d+)\s*[dj])?\s*"   # 2d / 2j
    r"(?:(?P<h>\d+)\s*h)?\s*"      # 2h
    r"(?:(?P<m>\d+)\s*(?:min|m))?\s*"
    r"(?:(?P<s>\d+)\s*s)?",
    re.IGNORECASE,
)
_MAX_SECONDS = 30 * 24 * 3600  # 30 jours


def parse_duration(s: str) -> int:
    """Parse 'XdYhZminWs' -> total secondes. Retourne 0 si invalide."""
    if not s:
        return 0
    s = s.strip().lower().replace(" ", "")
    m = _DURATION_RE.fullmatch(s)
    if not m:
        return 0
    parts = m.groupdict(default="0")
    total = (int(parts["d"]) * 86400
             + int(parts["h"]) * 3600
             + int(parts["m"]) * 60
             + int(parts["s"]))
    return total


def _fmt_due(due_dt: _dt.datetime) -> str:
    """Format human : 'le 7 juin 2026 a 14:32' ou 'dans 1h30min'."""
    months = ["janvier", "fevrier", "mars", "avril", "mai", "juin",
              "juillet", "aout", "septembre", "octobre", "novembre", "decembre"]
    return f"{due_dt.day} {months[due_dt.month - 1]} {due_dt.year} a {due_dt.hour:02d}:{due_dt.minute:02d}"


def setup_remind_commands(bot, deps):
    globals().update(deps)

    @bot.tree.command(name="remind", description="Cree un rappel a une date future")
    @app_commands.describe(
        duree="Duree (ex: 1h, 30min, 2d, 1d2h30min)",
        texte="Texte du rappel (ce que le bot te ping)",
    )
    async def remind(interaction: discord.Interaction, duree: str, texte: str):
        sec = parse_duration(duree)
        if sec <= 0:
            await interaction.response.send_message(
                "Duree invalide. Formats acceptes : `30s`, `5min`, `2h`, `1d`, `1d2h30min`.",
                ephemeral=True,
            )
            return
        if sec > _MAX_SECONDS:
            await interaction.response.send_message(
                "Duree max : **30 jours**.", ephemeral=True,
            )
            return
        if not texte.strip():
            await interaction.response.send_message("Texte vide.", ephemeral=True)
            return

        due_dt = _dt.datetime.utcnow() + _dt.timedelta(seconds=sec)
        due_iso = due_dt.strftime("%Y-%m-%d %H:%M:%S")
        rid = reminder_add(
            interaction.guild.id, interaction.user.id, interaction.channel.id,
            texte.strip()[:500], due_iso,
        )

        # Timestamp Discord relatif : <t:UNIX:R> -> "dans 2 heures"
        unix = int((due_dt - _dt.datetime(1970, 1, 1)).total_seconds())
        await interaction.response.send_message(
            f"⏰ Rappel **#{rid}** cree. Je te ping <t:{unix}:R> (<t:{unix}:F>).\n"
            f"> {texte.strip()[:200]}",
            ephemeral=True,
        )


    @bot.tree.command(name="reminders", description="Liste tes rappels actifs")
    async def reminders_list_cmd(interaction: discord.Interaction):
        rows = reminders_list_user(interaction.user.id, include_fired=False, limit=20)
        if not rows:
            await interaction.response.send_message(
                "Aucun rappel actif. Utilise `/remind` pour en creer un.",
                ephemeral=True,
            )
            return
        lines = []
        for r in rows:
            try:
                due_dt = _dt.datetime.strptime(r["due_at"], "%Y-%m-%d %H:%M:%S")
                unix = int((due_dt - _dt.datetime(1970, 1, 1)).total_seconds())
                lines.append(f"**#{r['id']}** • <t:{unix}:R> • {r['text'][:80]}")
            except Exception:
                lines.append(f"**#{r['id']}** • {r['due_at']} • {r['text'][:80]}")
        await interaction.response.send_message(
            "**Tes rappels actifs**\n" + "\n".join(lines)
            + "\n\nSupprime avec `/unremind id:<numero>`.",
            ephemeral=True,
        )


    @bot.tree.command(name="unremind", description="Supprime un de tes rappels")
    @app_commands.describe(id="Numero du rappel (vu via /reminders)")
    async def unremind(interaction: discord.Interaction, id: int):
        ok = reminder_delete(id, interaction.user.id)
        if ok:
            await interaction.response.send_message(
                f"Rappel **#{id}** supprime.", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Rappel **#{id}** introuvable ou pas a toi.", ephemeral=True,
            )
