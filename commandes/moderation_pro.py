"""Commandes de moderation enrichies : /warn, /modlogs, /clearwarns, /note.

Toutes les sanctions creees ici (warn / kick / ban / timeout / note) sont
persistees dans la table mod_actions et postees dans le salon mod-log
configure pour la guild via le dashboard.

Auto-timeout : si le nombre de warns actifs depasse autotimeout_threshold
(defini par guild dans mod_config), le membre est timeout automatiquement
pour autotimeout_duration secondes.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    mod_action_add, mod_actions_list, mod_action_get, mod_action_revoke,
    mod_action_count_active, mod_config_get,
)


ACTION_LABEL = {
    "warn":       "⚠️ Warn",
    "kick":       "👢 Kick",
    "ban":        "🔨 Ban",
    "unban":      "🔓 Unban",
    "timeout":    "⏱️ Timeout",
    "untimeout":  "✅ Timeout retiré",
    "note":       "📝 Note",
}
ACTION_COLOR = {
    "warn":      0xF1C40F,
    "kick":      0xE67E22,
    "ban":       0xC0392B,
    "unban":     0x2ECC71,
    "timeout":   0x9B59B6,
    "untimeout": 0x2ECC71,
    "note":      0x95A5A6,
}


def _err(title: str, msg: str) -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=msg, color=0xE74C3C)


def _ok(title: str, msg: str, color: int = 0x2ECC71) -> discord.Embed:
    return discord.Embed(title=title, description=msg, color=color)


def _fmt_dt(s: Optional[str]) -> str:
    if not s:
        return "—"
    return s.split(".")[0]


async def _post_modlog(guild: discord.Guild, embed: discord.Embed) -> None:
    """Poste l'embed dans le modlog_channel configure si dispo."""
    cfg = mod_config_get(guild.id)
    ch_id = cfg.get("modlog_channel_id")
    if not ch_id:
        return
    ch = guild.get_channel(int(ch_id))
    if not isinstance(ch, (discord.TextChannel, discord.Thread)):
        return
    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"[mod/modlog] send err: {type(e).__name__}: {e}")


def _build_action_embed(action: dict, *, member: Optional[discord.abc.User] = None,
                        moderator: Optional[discord.abc.User] = None,
                        title_prefix: str = "") -> discord.Embed:
    atype = action["action_type"]
    color = ACTION_COLOR.get(atype, 0x95A5A6)
    title = f"{title_prefix}{ACTION_LABEL.get(atype, atype)} · #{action['id']}"
    embed = discord.Embed(title=title, color=color, timestamp=_dt.datetime.now(_dt.timezone.utc))
    if member:
        embed.add_field(name="Membre", value=f"{member.mention} (`{member.id}`)", inline=False)
    else:
        embed.add_field(name="Membre", value=f"`{action['user_id']}`", inline=False)
    embed.add_field(name="Raison", value=action.get("reason") or "_aucune_", inline=False)
    if moderator:
        embed.add_field(name="Modérateur", value=f"{moderator.mention}", inline=True)
    elif action.get("moderator_id"):
        embed.add_field(name="Modérateur", value=f"<@{action['moderator_id']}>", inline=True)
    if action.get("duration_sec"):
        mins = action["duration_sec"] // 60
        embed.add_field(name="Durée", value=f"{mins} min", inline=True)
    if action.get("revoked_at"):
        embed.add_field(
            name="❎ Révoqué",
            value=f"par <@{action.get('revoked_by')}>\n{_fmt_dt(action.get('revoked_at'))}\n"
                  f"_{action.get('revoke_reason') or 'pas de raison'}_",
            inline=False,
        )
    return embed


async def _apply_auto_timeout_if_needed(member: discord.Member, moderator: discord.abc.User) -> Optional[int]:
    """Si le membre a atteint le seuil de warns actifs, applique un timeout auto.
    Retourne la duree appliquee (en secondes) ou None."""
    cfg = mod_config_get(member.guild.id)
    threshold = int(cfg.get("autotimeout_threshold") or 0)
    if threshold <= 0:
        return None
    active_warns = mod_action_count_active(member.guild.id, member.id, "warn")
    if active_warns < threshold:
        return None
    duration_sec = int(cfg.get("autotimeout_duration") or 600)
    try:
        until = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=duration_sec)
        await member.timeout(until, reason=f"Auto-timeout : {active_warns} warns actifs (seuil {threshold})")
    except discord.Forbidden:
        print(f"[mod/auto-timeout] forbidden member={member.id}")
        return None
    except Exception as e:
        print(f"[mod/auto-timeout] err: {type(e).__name__}: {e}")
        return None
    action_id = mod_action_add(
        member.guild.id, member.id, "timeout",
        reason=f"Auto-timeout après {active_warns} warns",
        moderator_id=moderator.id,
        duration_sec=duration_sec,
    )
    auto_embed = _build_action_embed(
        mod_action_get(action_id) or {"id": action_id, "action_type": "timeout",
                                      "user_id": str(member.id), "duration_sec": duration_sec},
        member=member,
        moderator=moderator,
        title_prefix="🤖 ",
    )
    await _post_modlog(member.guild, auto_embed)
    return duration_sec


def setup_mod_commands(bot: commands.Bot):

    # --------- /warn ---------
    @bot.tree.command(name="warn", description="Avertit un membre (admin/mod uniquement)")
    @app_commands.describe(membre="Membre à avertir", raison="Raison de l'avertissement")
    @app_commands.default_permissions(kick_members=True)
    async def warn_cmd(interaction: discord.Interaction, membre: discord.Member, raison: str):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err("Pas dispo en DM", "Utilise cette commande dans un serveur."), ephemeral=True)
            return
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=_err("Permission refusée", "Tu as besoin de **Expulser des membres**."),
                ephemeral=True)
            return
        if membre.bot:
            await interaction.response.send_message(
                embed=_err("Cible invalide", "Tu ne peux pas warn un bot."), ephemeral=True)
            return
        if membre.id == interaction.user.id:
            await interaction.response.send_message(
                embed=_err("Cible invalide", "Tu ne peux pas te warn toi-même."), ephemeral=True)
            return
        if len(raison) > 500:
            await interaction.response.send_message(
                embed=_err("Raison trop longue", "Maximum 500 caractères."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        action_id = mod_action_add(
            interaction.guild.id, membre.id, "warn",
            reason=raison, moderator_id=interaction.user.id,
        )
        active_count = mod_action_count_active(interaction.guild.id, membre.id, "warn")
        action_data  = mod_action_get(action_id) or {}
        embed_log = _build_action_embed(action_data, member=membre, moderator=interaction.user)
        embed_log.set_footer(text=f"Warns actifs : {active_count}")
        await _post_modlog(interaction.guild, embed_log)

        # DM au membre
        try:
            dm_embed = discord.Embed(
                title=f"⚠️ Avertissement reçu sur **{interaction.guild.name}**",
                description=f"**Raison :** {raison}\n\nTu as **{active_count}** avertissement(s) actif(s).",
                color=0xF1C40F,
            )
            await membre.send(embed=dm_embed)
        except Exception:
            pass

        # Auto-timeout si seuil atteint
        auto_duration = await _apply_auto_timeout_if_needed(membre, interaction.user)
        msg = f"✅ **{membre.display_name}** averti (#{action_id}). Warns actifs : **{active_count}**."
        if auto_duration:
            mins = auto_duration // 60
            msg += f"\n🤖 Auto-timeout de **{mins} min** appliqué (seuil atteint)."
        await interaction.followup.send(embed=_ok("Warn enregistré", msg), ephemeral=True)

    # --------- /modlogs ---------
    @bot.tree.command(name="modlogs", description="Affiche l'historique des sanctions d'un membre")
    @app_commands.describe(membre="Membre à inspecter")
    @app_commands.default_permissions(kick_members=True)
    async def modlogs_cmd(interaction: discord.Interaction, membre: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err("Pas dispo en DM", "Utilise cette commande dans un serveur."), ephemeral=True)
            return
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=_err("Permission refusée", "Tu as besoin de **Expulser des membres**."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        actions = mod_actions_list(interaction.guild.id, user_id=membre.id, limit=20)
        active_warns = sum(1 for a in actions if a["action_type"] == "warn" and not a.get("revoked_at"))
        total = len(actions)
        embed = discord.Embed(
            title=f"📋 Modlogs · {membre.display_name}",
            description=f"**Sanctions totales** : {total} · **Warns actifs** : {active_warns}",
            color=0x3498DB,
        )
        embed.set_thumbnail(url=membre.display_avatar.url)
        if not actions:
            embed.add_field(name="​", value="_Aucune sanction enregistrée._", inline=False)
        else:
            lines = []
            for a in actions[:15]:
                tag = "~~" if a.get("revoked_at") else ""
                label = ACTION_LABEL.get(a["action_type"], a["action_type"])
                date  = _fmt_dt(a.get("created_at"))
                mod   = f"<@{a.get('moderator_id')}>" if a.get("moderator_id") else "?"
                reason = (a.get("reason") or "_sans raison_")[:80]
                lines.append(f"{tag}`#{a['id']}` {label} · {date} · par {mod}\n→ {reason}{tag}")
            embed.add_field(name="Historique (max 15)", value="\n\n".join(lines), inline=False)
        if total > 15:
            embed.set_footer(text=f"+ {total - 15} sanctions plus anciennes")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # --------- /clearwarns ---------
    @bot.tree.command(name="clearwarns", description="Révoque tous les warns actifs d'un membre")
    @app_commands.describe(membre="Membre dont les warns seront effacés", raison="Raison (optionnel)")
    @app_commands.default_permissions(manage_guild=True)
    async def clearwarns_cmd(interaction: discord.Interaction, membre: discord.Member, raison: Optional[str] = None):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err("Pas dispo en DM", "Utilise cette commande dans un serveur."), ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=_err("Permission refusée", "Tu as besoin de **Gérer le serveur**."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        active = mod_actions_list(interaction.guild.id, user_id=membre.id,
                                  action_types=["warn"], include_revoked=False, limit=200)
        n = 0
        for a in active:
            if mod_action_revoke(a["id"], interaction.user.id, raison or "clearwarns"):
                n += 1
        embed = _ok(
            "Warns révoqués",
            f"✅ **{n}** warn(s) actif(s) de **{membre.display_name}** ont été révoqués.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        log = discord.Embed(
            title=f"🧹 Clearwarns · {membre.display_name}",
            description=f"{n} warn(s) révoqués par {interaction.user.mention}.\n_{raison or 'pas de raison'}_",
            color=0x2ECC71,
            timestamp=_dt.datetime.now(_dt.timezone.utc),
        )
        await _post_modlog(interaction.guild, log)

    # --------- /note (interne, ne notif pas l'user) ---------
    @bot.tree.command(name="note", description="Note privée mod sur un membre (ne notifie pas l'user)")
    @app_commands.describe(membre="Membre concerné", texte="Contenu de la note")
    @app_commands.default_permissions(kick_members=True)
    async def note_cmd(interaction: discord.Interaction, membre: discord.Member, texte: str):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err("Pas dispo en DM", "Utilise cette commande dans un serveur."), ephemeral=True)
            return
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=_err("Permission refusée", "Tu as besoin de **Expulser des membres**."),
                ephemeral=True)
            return
        if len(texte) > 1000:
            await interaction.response.send_message(
                embed=_err("Note trop longue", "Maximum 1000 caractères."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        action_id = mod_action_add(
            interaction.guild.id, membre.id, "note",
            reason=texte, moderator_id=interaction.user.id,
        )
        embed_log = _build_action_embed(mod_action_get(action_id) or {}, member=membre,
                                        moderator=interaction.user)
        await _post_modlog(interaction.guild, embed_log)
        await interaction.followup.send(
            embed=_ok("Note enregistrée", f"📝 Note #{action_id} ajoutée sur **{membre.display_name}**."),
            ephemeral=True,
        )
