"""Tempvoice : salons vocaux temporaires.

Concept :
- L'admin configure un "lobby" via /tempvoice setup. C'est un salon vocal
  que les users rejoignent pour declencher la creation d'un vocal perso.
- Quand un user rejoint le lobby, le bot cree un salon vocal a son nom dans
  la categorie configuree et le deplace dedans automatiquement.
- Le salon est detruit des qu'il est vide (plus aucun membre).
- L'owner du salon temp peut le configurer via /voc (renommer, limit,
  lock, kick, transfer).

Stockage : tempvoice_config (config par guild) + tempvoice_active (track
des channels crees pour identifier l'owner + cleanup au boot).

L'event on_voice_state_update est centralise dans bot.py. Cette module
expose `tempvoice_on_voice_state_update(...)` qui doit etre appelee par le
handler central.
"""
from __future__ import annotations

import asyncio
import discord
from discord import app_commands

from database import (
    tempvoice_config_get, tempvoice_config_set, tempvoice_config_disable,
    tempvoice_track, tempvoice_untrack, tempvoice_owner_of, tempvoice_transfer,
    tempvoice_list_active,
)


_CREATION_LOCK: dict[int, asyncio.Lock] = {}


def _format_default_name(template: str, user: discord.Member) -> str:
    name = (template or "Vocal de {user}").replace("{user}", user.display_name)
    return name[:90]  # limite Discord = 100


async def _create_temp_voice(bot, member: discord.Member, cfg: dict):
    gid = member.guild.id
    lock = _CREATION_LOCK.setdefault(gid, asyncio.Lock())
    async with lock:
        # Categorie cible : config OU meme que celle du lobby
        category = None
        if cfg.get("category_id"):
            category = member.guild.get_channel(int(cfg["category_id"]))
            if not isinstance(category, discord.CategoryChannel):
                category = None
        if category is None:
            lobby = member.guild.get_channel(int(cfg["lobby_channel_id"]))
            category = lobby.category if lobby else None

        name = _format_default_name(cfg.get("default_name"), member)
        # Permissions : owner peut tout sur son salon
        overwrites = {
            member.guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            member: discord.PermissionOverwrite(
                manage_channels=True, move_members=True, mute_members=True,
                deafen_members=True, view_channel=True, connect=True, speak=True,
            ),
        }
        try:
            ch = await member.guild.create_voice_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                reason=f"Tempvoice pour {member}",
            )
            tempvoice_track(ch.id, gid, member.id)
            try:
                await member.move_to(ch, reason="Tempvoice")
            except Exception as e:
                print(f"[tempvoice] move_to err: {type(e).__name__}: {e}")
        except discord.Forbidden:
            print(f"[tempvoice] create channel forbidden guild={gid}")
        except Exception as e:
            print(f"[tempvoice] create channel err: {type(e).__name__}: {e}")


async def tempvoice_on_voice_state_update(member: discord.Member,
                                          before: discord.VoiceState,
                                          after: discord.VoiceState,
                                          bot):
    """A appeler depuis le on_voice_state_update central de bot.py.
    Gere : creation a l'entree du lobby + cleanup quand un temp est vide
    + transfert auto d'ownership si owner part avec d'autres membres."""
    if member.bot:
        return
    try:
        # Cas 1 : user rejoint un salon -> est-ce le lobby ?
        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            cfg = tempvoice_config_get(member.guild.id)
            if cfg and str(after.channel.id) == str(cfg["lobby_channel_id"]):
                await _create_temp_voice(bot, member, cfg)

        # Cas 2 : user quitte un salon -> si c'etait un temp et vide, supprime
        if before.channel and (not after.channel or before.channel.id != after.channel.id):
            owner = tempvoice_owner_of(before.channel.id)
            if owner is not None:
                if len(before.channel.members) == 0:
                    try:
                        await before.channel.delete(reason="Tempvoice vide")
                    except Exception as e:
                        print(f"[tempvoice] delete vide err: {type(e).__name__}: {e}")
                    finally:
                        tempvoice_untrack(before.channel.id)
                elif str(owner) == str(member.id):
                    # Owner part mais d'autres restent : transfere a un membre present
                    successor = next((m for m in before.channel.members if not m.bot), None)
                    if successor:
                        tempvoice_transfer(before.channel.id, successor.id)
                        try:
                            await before.channel.send(
                                f"Owner du salon a quitte, **{successor.display_name}** prend la suite."
                            )
                        except Exception:
                            pass
    except Exception as e:
        print(f"[tempvoice] on_voice_state_update err: {type(e).__name__}: {e}")


def setup_tempvoice(bot, deps):
    globals().update(deps)

    # ===== Slash commands admin : /tempvoice setup/disable/info =====
    tempvoice_grp = app_commands.Group(name="tempvoice",
                                       description="Configure les salons vocaux temporaires (admin)")

    @tempvoice_grp.command(name="setup", description="Configure le salon lobby (admin)")
    @app_commands.describe(
        lobby="Salon vocal a rejoindre pour declencher la creation",
        categorie="Categorie ou poser les salons crees (defaut : meme que le lobby)",
        nom_par_defaut="Template pour le nom (use {user} pour le pseudo)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def tempvoice_setup(interaction: discord.Interaction,
                               lobby: discord.VoiceChannel,
                               categorie: discord.CategoryChannel = None,
                               nom_par_defaut: str = None):
        tempvoice_config_set(
            interaction.guild.id,
            lobby_channel_id=lobby.id,
            category_id=categorie.id if categorie else None,
            default_name=nom_par_defaut,
        )
        await interaction.response.send_message(
            f"Tempvoice configure. Lobby : <#{lobby.id}>. "
            + (f"Categorie : **{categorie.name}**. " if categorie else "Categorie : meme que le lobby. ")
            + f"Template nom : `{nom_par_defaut or 'Vocal de {user}'}`",
            ephemeral=True,
        )

    @tempvoice_grp.command(name="disable", description="Desactive les salons vocaux temporaires (admin)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def tempvoice_disable(interaction: discord.Interaction):
        tempvoice_config_disable(interaction.guild.id)
        await interaction.response.send_message("Tempvoice desactive sur ce serveur.", ephemeral=True)

    @tempvoice_grp.command(name="info", description="Affiche la config tempvoice")
    async def tempvoice_info(interaction: discord.Interaction):
        cfg = tempvoice_config_get(interaction.guild.id)
        if not cfg:
            await interaction.response.send_message(
                "Tempvoice n'est pas configure. Utilise `/tempvoice setup`.",
                ephemeral=True,
            )
            return
        active = tempvoice_list_active(interaction.guild.id)
        await interaction.response.send_message(
            f"**Lobby** : <#{cfg['lobby_channel_id']}>\n"
            f"**Categorie** : {'<#' + cfg['category_id'] + '>' if cfg.get('category_id') else 'meme que le lobby'}\n"
            f"**Template nom** : `{cfg['default_name']}`\n"
            f"**Salons actifs** : {len(active)}",
            ephemeral=True,
        )

    bot.tree.add_command(tempvoice_grp)


    # ===== Slash commands user : /voc rename/limit/lock/unlock/kick/transfer =====
    voc_grp = app_commands.Group(name="voc",
                                  description="Gere ton salon vocal temporaire")

    async def _require_owner(interaction: discord.Interaction):
        """Retourne le channel temp si l'user est owner, sinon repond + None."""
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "Tu dois etre dans ton salon temp pour utiliser cette commande.",
                ephemeral=True,
            )
            return None
        ch = interaction.user.voice.channel
        owner = tempvoice_owner_of(ch.id)
        if owner is None:
            await interaction.response.send_message(
                "Ce salon n'est pas un salon temp.", ephemeral=True,
            )
            return None
        if str(owner) != str(interaction.user.id):
            await interaction.response.send_message(
                "Seul l'owner du salon peut faire ca.", ephemeral=True,
            )
            return None
        return ch

    @voc_grp.command(name="rename", description="Renomme ton salon temp")
    @app_commands.describe(nom="Nouveau nom (max 90 chars)")
    async def voc_rename(interaction: discord.Interaction, nom: str):
        ch = await _require_owner(interaction)
        if not ch: return
        try:
            await ch.edit(name=nom[:90], reason=f"Rename par {interaction.user}")
            await interaction.response.send_message(f"Salon renommé en **{nom[:90]}**.", ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] rename err: {e!r}")
            await interaction.response.send_message(
                "Impossible de renommer le salon (réessaie dans quelques secondes).", ephemeral=True)

    @voc_grp.command(name="limit", description="Limite le nombre de membres (0 = illimite)")
    @app_commands.describe(nombre="Nombre max de membres (0 a 99)")
    async def voc_limit(interaction: discord.Interaction, nombre: int):
        ch = await _require_owner(interaction)
        if not ch: return
        nombre = max(0, min(99, nombre))
        try:
            await ch.edit(user_limit=nombre, reason=f"Limit par {interaction.user}")
            label = "illimité" if nombre == 0 else str(nombre)
            await interaction.response.send_message(f"Limite de membres : **{label}**.", ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] limit err: {e!r}")
            await interaction.response.send_message(
                "Impossible de changer la limite du salon (vérifie les permissions du bot).", ephemeral=True)

    @voc_grp.command(name="lock", description="Empeche les nouveaux membres de rejoindre")
    async def voc_lock(interaction: discord.Interaction):
        ch = await _require_owner(interaction)
        if not ch: return
        try:
            await ch.set_permissions(interaction.guild.default_role, connect=False)
            await interaction.response.send_message("🔒 Salon verrouillé.", ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] lock err: {e!r}")
            await interaction.response.send_message(
                "Impossible de verrouiller le salon (vérifie les permissions du bot).", ephemeral=True)

    @voc_grp.command(name="unlock", description="Re-ouvre ton salon a tout le monde")
    async def voc_unlock(interaction: discord.Interaction):
        ch = await _require_owner(interaction)
        if not ch: return
        try:
            await ch.set_permissions(interaction.guild.default_role, connect=None)
            await interaction.response.send_message("🔓 Salon rouvert à tout le monde.", ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] unlock err: {e!r}")
            await interaction.response.send_message(
                "Impossible de rouvrir le salon (vérifie les permissions du bot).", ephemeral=True)

    @voc_grp.command(name="kick", description="Vire un membre de ton salon")
    @app_commands.describe(membre="Membre a virer")
    async def voc_kick(interaction: discord.Interaction, membre: discord.Member):
        ch = await _require_owner(interaction)
        if not ch: return
        if membre.voice is None or membre.voice.channel is None or membre.voice.channel.id != ch.id:
            await interaction.response.send_message("Ce membre n'est pas dans ton salon.", ephemeral=True)
            return
        try:
            await membre.move_to(None, reason=f"Kick par {interaction.user} (tempvoice)")
            await interaction.response.send_message(f"{membre.mention} a été expulsé du salon.", ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] kick err: {e!r}")
            await interaction.response.send_message(
                "Impossible d'expulser ce membre (vérifie les permissions du bot).", ephemeral=True)

    @voc_grp.command(name="transfer", description="Donne la propriete du salon a un autre membre")
    @app_commands.describe(membre="Nouveau owner (doit etre dans le salon)")
    async def voc_transfer(interaction: discord.Interaction, membre: discord.Member):
        ch = await _require_owner(interaction)
        if not ch: return
        if membre.bot:
            await interaction.response.send_message(
                "Action impossible sur un bot.", ephemeral=True); return
        if membre.voice is None or membre.voice.channel is None or membre.voice.channel.id != ch.id:
            await interaction.response.send_message("Le nouveau owner doit etre dans le salon.", ephemeral=True)
            return
        tempvoice_transfer(ch.id, membre.id)
        # Mise a jour des permissions : nouvel owner peut manage, ancien revient au defaut
        try:
            await ch.set_permissions(interaction.user, overwrite=None)
            await ch.set_permissions(membre, manage_channels=True, move_members=True,
                                      mute_members=True, deafen_members=True,
                                      view_channel=True, connect=True, speak=True)
        except Exception:
            pass
        await interaction.response.send_message(
            f"{membre.mention} est maintenant le owner du salon.", ephemeral=True,
        )

    bot.tree.add_command(voc_grp)
