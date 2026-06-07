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


def _parse_date_input(raw: str) -> _dt.datetime | None:
    """Parse une chaine date-input flexible.

    Accepte :
    - Duree relative : '1h', '30min', '2d', '1d2h30min'
    - Date absolue : 'DD/MM/YYYY HH:MM' ou 'DD/MM HH:MM' ou 'YYYY-MM-DD HH:MM'
    - Heure du jour : 'HH:MM' (interprete comme aujourd'hui, ou demain si passe)

    Retourne datetime UTC ou None si invalide.
    """
    if not raw:
        return None
    s = raw.strip()

    # 1) Duree relative ?
    sec = parse_duration(s)
    if sec > 0 and sec <= _MAX_SECONDS:
        return _dt.datetime.utcnow() + _dt.timedelta(seconds=sec)

    # 2) Formats absolus (heure locale serveur Hetzner = UTC, on suppose UTC).
    formats = [
        "%d/%m/%Y %H:%M", "%d/%m/%Y %Hh%M", "%d/%m/%Y %H",
        "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%d/%m %H:%M", "%d-%m %H:%M",
        "%H:%M",
    ]
    now = _dt.datetime.utcnow()
    for fmt in formats:
        try:
            dt = _dt.datetime.strptime(s, fmt)
            # Si pas d'annee : annee courante
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
            # Si HH:MM uniquement : aujourd'hui ou demain
            if fmt == "%H:%M":
                dt = now.replace(hour=dt.hour, minute=dt.minute,
                                 second=0, microsecond=0)
                if dt <= now:
                    dt += _dt.timedelta(days=1)
            return dt
        except ValueError:
            continue
    return None


def _resolve_channel(raw: str, guild: discord.Guild) -> discord.TextChannel | None:
    """Parse channel input : '<#123>', '#name', 'name', '123'."""
    if not raw:
        return None
    s = raw.strip()
    # Mention <#123>
    m = re.match(r"<#(\d+)>", s)
    if m:
        return guild.get_channel(int(m.group(1)))
    # Raw ID
    if s.isdigit():
        return guild.get_channel(int(s))
    # Name (avec ou sans #)
    name = s.lstrip("#").lower()
    for ch in guild.text_channels:
        if ch.name.lower() == name:
            return ch
    return None


def setup_remind_commands(bot, deps):
    globals().update(deps)

    class RemindModal(discord.ui.Modal, title="Creer un rappel"):
        date_input = discord.ui.TextInput(
            label="Date / Duree",
            placeholder="Ex: 1h30min  •  15/06/2026 14:30  •  18:00",
            max_length=60,
            required=True,
        )
        message_input = discord.ui.TextInput(
            label="Message du rappel",
            style=discord.TextStyle.paragraph,
            placeholder="Ce que le bot affichera quand le rappel se declenche",
            max_length=500,
            required=True,
        )
        channel_input = discord.ui.TextInput(
            label="Salon (#salon, nom, ou ID) — vide = courant",
            placeholder="Ex: #general OU 1234567890",
            max_length=100,
            required=False,
        )

        async def on_submit(self, interaction: discord.Interaction):
            # 1) Parse date
            due_dt = _parse_date_input(str(self.date_input.value))
            if due_dt is None:
                await interaction.response.send_message(
                    "Date/duree invalide. Formats acceptes :\n"
                    "• Duree : `30s`, `5min`, `2h`, `1d2h30min`\n"
                    "• Date : `15/06/2026 14:30`, `2026-06-15 14:30`, `18:00`",
                    ephemeral=True,
                )
                return
            now = _dt.datetime.utcnow()
            if due_dt <= now:
                await interaction.response.send_message(
                    "La date doit etre dans le futur.", ephemeral=True,
                )
                return
            if (due_dt - now).total_seconds() > _MAX_SECONDS:
                await interaction.response.send_message(
                    "Max **30 jours** dans le futur.", ephemeral=True,
                )
                return

            # 2) Salon : input ou courant
            ch_raw = str(self.channel_input.value or "").strip()
            if ch_raw:
                ch = _resolve_channel(ch_raw, interaction.guild)
                if ch is None:
                    await interaction.response.send_message(
                        f"Salon introuvable : `{ch_raw}`. Verifie le nom/ID.",
                        ephemeral=True,
                    )
                    return
                if not isinstance(ch, (discord.TextChannel, discord.Thread)):
                    await interaction.response.send_message(
                        "Le salon doit etre textuel.", ephemeral=True,
                    )
                    return
            else:
                ch = interaction.channel

            # Verif perms d'envoi
            try:
                me = interaction.guild.me
                if not ch.permissions_for(me).send_messages:
                    await interaction.response.send_message(
                        f"Je n'ai pas la permission d'envoyer des messages dans {ch.mention}.",
                        ephemeral=True,
                    )
                    return
            except Exception:
                pass

            # 3) Crea rappel
            text = str(self.message_input.value).strip()[:500]
            due_iso = due_dt.strftime("%Y-%m-%d %H:%M:%S")
            rid = reminder_add(
                interaction.guild.id, interaction.user.id, ch.id, text, due_iso,
            )
            unix = int((due_dt - _dt.datetime(1970, 1, 1)).total_seconds())
            await interaction.response.send_message(
                f"⏰ Rappel **#{rid}** cree. Ping dans {ch.mention} <t:{unix}:R> (<t:{unix}:F>).\n"
                f"> {text[:200]}",
                ephemeral=True,
            )

    @bot.tree.command(name="remind", description="Cree un rappel via un builder")
    async def remind(interaction: discord.Interaction):
        await interaction.response.send_modal(RemindModal())


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
