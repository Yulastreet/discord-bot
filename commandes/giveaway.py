"""Giveaways : slash command + persistent view + finalize loop.

Flux:
- /giveaway create duree gagnants prix [salon]
- bot poste un embed dans le salon cible avec bouton 🎉 Participer
- chaque click = 1 entree (toggle)
- task loop (toutes les minutes) detecte les giveaways termines
  -> pick winners aleatoires, edite le message, ping winners
- /giveaway reroll <id> et /giveaway cancel <id>

La view est enregistree avec un custom_id stable pour survivre aux restarts.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json as _json
import random
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import (
    giveaway_create, giveaway_set_message_id, giveaway_get, giveaway_get_by_message,
    giveaways_list, giveaway_entry_add, giveaway_entry_remove, giveaway_entries,
    giveaway_entries_count, giveaway_set_ended, giveaway_cancel,
    giveaways_pending_finalize,
)


_DURATION_RE = re.compile(r"^\s*(\d+)\s*(s|m|h|d|j)?\s*$", re.IGNORECASE)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "j": 86400}


def parse_duration(text: str) -> Optional[int]:
    """Parse '1h', '30m', '2d', '90' (secondes par defaut). Retourne sec ou None."""
    if not text:
        return None
    m = _DURATION_RE.match(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or "m").lower()
    return n * _UNITS.get(unit, 60)


def fmt_duration(sec: int) -> str:
    if sec <= 0:
        return "terminé"
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}j")
    if h: parts.append(f"{h}h")
    if m and not d: parts.append(f"{m}m")
    if s and not d and not h: parts.append(f"{s}s")
    return " ".join(parts) or f"{sec}s"


class GiveawayJoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎉 Participer", style=discord.ButtonStyle.success,
                       custom_id="gw:join")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        gw = giveaway_get_by_message(interaction.message.id) if interaction.message else None
        if not gw:
            try: await interaction.response.send_message("❌ Giveaway introuvable.", ephemeral=True)
            except Exception: pass
            return
        if gw.get("ended") or gw.get("cancelled"):
            try: await interaction.response.send_message("❌ Ce giveaway est terminé.", ephemeral=True)
            except Exception: pass
            return
        gid = gw["id"]
        uid = interaction.user.id
        # Toggle : si deja participant, on retire
        existing = uid in {int(x) for x in giveaway_entries(gid)}
        if existing:
            giveaway_entry_remove(gid, uid)
            try:
                await interaction.response.send_message(
                    "❎ Participation retirée.", ephemeral=True)
            except Exception: pass
        else:
            giveaway_entry_add(gid, uid)
            try:
                await interaction.response.send_message(
                    f"✅ Tu participes au giveaway pour **{gw['prize']}** ! Bonne chance.",
                    ephemeral=True)
            except Exception: pass
        # Update count in original embed
        try:
            count = giveaway_entries_count(gid)
            msg = interaction.message
            if msg and msg.embeds:
                emb = msg.embeds[0]
                # Cherche le field "Participants" ou l'ajoute
                new_fields = []
                replaced = False
                for f in emb.fields:
                    if f.name.startswith("👥"):
                        new_fields.append(("👥 Participants", str(count), True))
                        replaced = True
                    else:
                        new_fields.append((f.name, f.value, f.inline))
                if not replaced:
                    new_fields.append(("👥 Participants", str(count), True))
                emb.clear_fields()
                for n, v, i in new_fields:
                    emb.add_field(name=n, value=v, inline=i)
                await msg.edit(embed=emb, view=self)
        except Exception as e:
            print(f"[giveaway/update count] {type(e).__name__}: {e}")


def make_giveaway_embed(gw: dict, *, participants_count: int = 0,
                       finished: bool = False,
                       winners: Optional[list] = None) -> discord.Embed:
    now = _dt.datetime.now(_dt.timezone.utc)
    ends_at = _dt.datetime.fromisoformat(gw["ends_at"].replace("Z", "+00:00"))
    remaining = max(0, int((ends_at - now).total_seconds()))
    if finished or gw.get("ended"):
        color = 0x95A5A6 if not (winners) else 0x2ECC71
        title = f"🎉 Giveaway terminé · {gw['prize']}"
    else:
        color = 0xF1C40F
        title = f"🎉 GIVEAWAY · {gw['prize']}"
    embed = discord.Embed(title=title, color=color, timestamp=now)
    if not finished and not gw.get("ended"):
        embed.description = (
            f"**Récompense** : {gw['prize']}\n"
            f"**Gagnants** : {gw['winners_count']}\n"
            f"**Fin** : <t:{int(ends_at.timestamp())}:R> (<t:{int(ends_at.timestamp())}:F>)\n\n"
            f"_Clique sur **🎉 Participer** pour entrer !_"
        )
    else:
        if winners:
            mentions = ", ".join(f"<@{w}>" for w in winners)
            embed.description = (
                f"**Récompense** : {gw['prize']}\n"
                f"**Gagnants** : {mentions}\n"
                f"_Félicitations !_"
            )
        else:
            embed.description = (
                f"**Récompense** : {gw['prize']}\n"
                f"_Aucun participant — pas de gagnant._"
            )
    embed.add_field(name="👥 Participants", value=str(participants_count), inline=True)
    if gw.get("created_by"):
        embed.add_field(name="Hôte", value=f"<@{gw['created_by']}>", inline=True)
    return embed


async def _finalize_giveaway(bot: commands.Bot, gw: dict) -> Optional[list[str]]:
    """Tire les gagnants, edite le message, ping les winners. Retourne winner ids."""
    gid = gw["id"]
    entries = giveaway_entries(gid)
    n = int(gw["winners_count"])
    if not entries:
        giveaway_set_ended(gid, [])
        winners = []
    else:
        winners = random.sample(entries, min(n, len(entries)))
        giveaway_set_ended(gid, winners)

    # Update Discord message
    try:
        ch = bot.get_channel(int(gw["channel_id"]))
        if ch and gw.get("message_id"):
            msg = await ch.fetch_message(int(gw["message_id"]))
            embed = make_giveaway_embed(
                {**gw, "ended": 1},
                participants_count=len(entries),
                finished=True,
                winners=winners,
            )
            view = GiveawayJoinView()
            for child in view.children:
                child.disabled = True
            await msg.edit(embed=embed, view=view)
            if winners:
                mentions = " ".join(f"<@{w}>" for w in winners)
                await ch.send(
                    f"🎉 {mentions} a gagné **{gw['prize']}** ! "
                    f"Merci à tous d'avoir participé."
                )
            else:
                await ch.send(f"😔 Giveaway pour **{gw['prize']}** terminé sans participants.")
    except discord.NotFound:
        print(f"[giveaway/finalize] message disappeared gw={gid}")
    except Exception as e:
        print(f"[giveaway/finalize] err gw={gid}: {type(e).__name__}: {e}")
    return winners


async def giveaway_finalize_sweep(bot: commands.Bot):
    """Verifie tous les giveaways arrives a expiration."""
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    pending = giveaways_pending_finalize(now_iso)
    for gw in pending:
        try:
            await _finalize_giveaway(bot, gw)
        except Exception as e:
            print(f"[giveaway/sweep] err: {type(e).__name__}: {e}")


async def reroll_giveaway(bot: commands.Bot, giveaway_id: int) -> Optional[list[str]]:
    gw = giveaway_get(giveaway_id)
    if not gw:
        return None
    entries = giveaway_entries(giveaway_id)
    if not entries:
        return []
    # Tirage parmi participants en excluant precedents winners
    prev = []
    try:
        prev = _json.loads(gw.get("winner_ids") or "[]")
    except Exception:
        prev = []
    pool = [e for e in entries if e not in prev] or entries
    n = int(gw["winners_count"])
    winners = random.sample(pool, min(n, len(pool)))
    giveaway_set_ended(giveaway_id, winners)
    # Post nouveau message
    try:
        ch = bot.get_channel(int(gw["channel_id"]))
        if ch:
            mentions = " ".join(f"<@{w}>" for w in winners)
            await ch.send(f"🎲 **Reroll** giveaway #{giveaway_id} : {mentions} a gagné **{gw['prize']}** !")
    except Exception as e:
        print(f"[giveaway/reroll] post err: {type(e).__name__}: {e}")
    return winners


def setup_giveaway_commands(bot: commands.Bot):
    # Enregistre la view persistante pour survivre aux restarts
    bot.add_view(GiveawayJoinView())

    gw_group = app_commands.Group(name="giveaway", description="Tirages au sort")

    @gw_group.command(name="create", description="Crée un giveaway")
    @app_commands.describe(
        duree="Durée (ex: 1h, 30m, 2d). Défaut: minutes si pas d'unité.",
        gagnants="Nombre de gagnants (1-50)",
        prix="Description de la récompense",
        salon="Salon où poster (défaut: salon courant)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def gw_create(interaction: discord.Interaction,
                        duree: str,
                        gagnants: app_commands.Range[int, 1, 50],
                        prix: str,
                        salon: Optional[discord.TextChannel] = None):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Utilise cette commande dans un serveur.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ Tu as besoin de **Gérer le serveur**.", ephemeral=True)
            return
        sec = parse_duration(duree)
        if not sec or sec < 30 or sec > 30 * 86400:
            await interaction.response.send_message(
                "❌ Durée invalide. Min 30s, max 30 jours. Format : `1h`, `30m`, `2d`.",
                ephemeral=True)
            return
        if len(prix) > 200:
            await interaction.response.send_message(
                "❌ Prix trop long (max 200 caractères).", ephemeral=True)
            return
        target = salon or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Le salon cible doit être un salon texte.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        ends_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=sec)
        gid = giveaway_create(
            interaction.guild.id, target.id, prix, gagnants,
            ends_at.isoformat(), interaction.user.id,
        )
        gw = giveaway_get(gid)
        embed = make_giveaway_embed(gw, participants_count=0)
        view = GiveawayJoinView()
        try:
            msg = await target.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Permission manquante pour poster dans ce salon.", ephemeral=True)
            return
        giveaway_set_message_id(gid, msg.id)
        await interaction.followup.send(
            f"✅ Giveaway #{gid} créé dans {target.mention} ! Fin <t:{int(ends_at.timestamp())}:R>.",
            ephemeral=True,
        )

    @gw_group.command(name="list", description="Liste les giveaways actifs")
    @app_commands.default_permissions(manage_guild=True)
    async def gw_list(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Pas dispo en DM.", ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ Permission manquante.", ephemeral=True); return
        rows = giveaways_list(interaction.guild.id, only_active=True, limit=25)
        if not rows:
            await interaction.response.send_message("_Aucun giveaway actif._", ephemeral=True); return
        lines = []
        for g in rows:
            entries = giveaway_entries_count(g["id"])
            ends_ts = int(_dt.datetime.fromisoformat(g["ends_at"].replace("Z", "+00:00")).timestamp())
            lines.append(f"`#{g['id']}` **{g['prize']}** · <#{g['channel_id']}> · {entries} part. · fin <t:{ends_ts}:R>")
        embed = discord.Embed(title="🎉 Giveaways actifs", description="\n".join(lines), color=0xF1C40F)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @gw_group.command(name="reroll", description="Re-tire des gagnants pour un giveaway terminé")
    @app_commands.describe(giveaway_id="ID du giveaway (visible dans le titre du message)")
    @app_commands.default_permissions(manage_guild=True)
    async def gw_reroll(interaction: discord.Interaction, giveaway_id: int):
        if not interaction.guild:
            await interaction.response.send_message("❌ Pas dispo en DM.", ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ Permission manquante.", ephemeral=True); return
        gw = giveaway_get(giveaway_id)
        if not gw or str(gw["guild_id"]) != str(interaction.guild.id):
            await interaction.response.send_message(
                f"❌ Giveaway #{giveaway_id} introuvable.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        winners = await reroll_giveaway(bot, giveaway_id)
        if not winners:
            await interaction.followup.send(
                "❌ Aucun participant éligible pour reroll.", ephemeral=True); return
        await interaction.followup.send(
            f"✅ Reroll OK. Nouveaux gagnants : {', '.join(f'<@{w}>' for w in winners)}",
            ephemeral=True,
        )

    @gw_group.command(name="cancel", description="Annule un giveaway en cours")
    @app_commands.describe(giveaway_id="ID du giveaway")
    @app_commands.default_permissions(manage_guild=True)
    async def gw_cancel(interaction: discord.Interaction, giveaway_id: int):
        if not interaction.guild:
            await interaction.response.send_message("❌ Pas dispo en DM.", ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ Permission manquante.", ephemeral=True); return
        gw = giveaway_get(giveaway_id)
        if not gw or str(gw["guild_id"]) != str(interaction.guild.id):
            await interaction.response.send_message(
                f"❌ Giveaway #{giveaway_id} introuvable.", ephemeral=True); return
        if gw.get("ended"):
            await interaction.response.send_message(
                "❌ Déjà terminé.", ephemeral=True); return
        giveaway_cancel(giveaway_id)
        # Update message
        try:
            ch = bot.get_channel(int(gw["channel_id"]))
            if ch and gw.get("message_id"):
                msg = await ch.fetch_message(int(gw["message_id"]))
                embed = msg.embeds[0] if msg.embeds else discord.Embed()
                embed.title = f"❌ Giveaway annulé · {gw['prize']}"
                embed.color = 0xE74C3C
                view = GiveawayJoinView()
                for child in view.children:
                    child.disabled = True
                await msg.edit(embed=embed, view=view)
        except Exception:
            pass
        await interaction.response.send_message(
            f"✅ Giveaway #{giveaway_id} annulé.", ephemeral=True)

    bot.tree.add_command(gw_group)
