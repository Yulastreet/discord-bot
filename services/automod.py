"""Automod : filtres moderation auto par serveur (TookBot+).

4 regles toggleables :
- banned_words : liste CSV de mots interdits, match case-insensitive
- discord_invites : detecte invites Discord (discord.gg/, discord.com/invite/)
- mention_spam : msg avec >= N mentions
- raid_protection : >= N joins/minute sur une fenetre glissante

Action : DELETE le message (sauf raid_protection qui log juste pour l'instant).
Toutes les actions sont aussi logguees dans le log_channel_id si defini.

L'event handler central appelle :
- automod_on_message(message, bot) dans on_message
- automod_on_member_join(member, bot) dans on_member_join
"""
from __future__ import annotations

import re
import time
import discord

from database import automod_config_get


# Cache des configs (TTL 30s) pour eviter de hit la DB sur chaque message
_CFG_CACHE: dict[str, tuple[float, dict]] = {}
_CFG_TTL = 30.0


def _get_cfg(guild_id):
    key = str(guild_id)
    now = time.time()
    cached = _CFG_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]
    cfg = automod_config_get(guild_id)
    _CFG_CACHE[key] = (now + _CFG_TTL, cfg)
    return cfg


def invalidate_automod_cache(guild_id=None):
    """A appeler apres modification de la config (web ou slash)."""
    if guild_id is None:
        _CFG_CACHE.clear()
    else:
        _CFG_CACHE.pop(str(guild_id), None)


# Fenetre joins par guild (deque-like : list de timestamps trim)
_JOIN_WINDOW: dict[str, list[float]] = {}
_JOIN_WINDOW_SEC = 60


_INVITE_RE = re.compile(
    r"(?:discord(?:app)?\.com\/invite|discord\.gg)\/[a-zA-Z0-9-]+",
    re.IGNORECASE,
)


def _word_match(content: str, words_csv: str) -> str | None:
    if not words_csv:
        return None
    words = [w.strip().lower() for w in words_csv.split(",") if w.strip()]
    if not words:
        return None
    low = content.lower()
    for w in words:
        # Match whole word (frontiere) si pas d'espace dans w, sinon substring
        if " " in w:
            if w in low:
                return w
        else:
            if re.search(rf"\b{re.escape(w)}\b", low):
                return w
    return None


async def _log_action(bot, guild, log_ch_id, title: str, description: str,
                       color=discord.Color.red(), fields=None):
    if not log_ch_id:
        return
    try:
        ch = guild.get_channel(int(log_ch_id))
        if ch is None or not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return
        em = discord.Embed(title=title, description=description, color=color,
                            timestamp=discord.utils.utcnow())
        if fields:
            for n, v in fields:
                em.add_field(name=n, value=v, inline=False)
        em.set_footer(text="TookBot Automod")
        await ch.send(embed=em)
    except Exception as e:
        print(f"[automod log] err: {type(e).__name__}: {e}")


async def automod_on_message(message: discord.Message, bot):
    """Hook on_message : scan banned_words / discord invites / mention spam."""
    if message.author.bot or not message.guild:
        return
    # Ignore admin/manage_messages
    perms = message.author.guild_permissions
    if perms.administrator or perms.manage_messages:
        return

    cfg = _get_cfg(message.guild.id)
    if not cfg.get("enabled"):
        return

    log_ch = cfg.get("log_channel_id")

    # 1) Banned words
    if cfg.get("banned_words_enabled"):
        matched = _word_match(message.content or "", cfg.get("banned_words") or "")
        if matched:
            try:
                await message.delete()
            except Exception:
                pass
            await _log_action(
                bot, message.guild, log_ch,
                "Message supprime (mot interdit)",
                f"Auteur : {message.author.mention} ({message.author.id})\n"
                f"Salon : {message.channel.mention}",
                fields=[("Mot detecte", f"`{matched}`"),
                        ("Contenu (extrait)", (message.content or '')[:1000] or '*vide*')],
            )
            return

    # 2) Discord invites
    if cfg.get("discord_invites_enabled"):
        if _INVITE_RE.search(message.content or ""):
            try:
                await message.delete()
            except Exception:
                pass
            await _log_action(
                bot, message.guild, log_ch,
                "Message supprime (invite Discord)",
                f"Auteur : {message.author.mention} ({message.author.id})\n"
                f"Salon : {message.channel.mention}",
                fields=[("Contenu", (message.content or '')[:1000])],
            )
            return

    # 3) Mention spam
    if cfg.get("mention_spam_enabled"):
        threshold = max(2, int(cfg.get("mention_spam_threshold") or 5))
        nb_mentions = len(message.mentions) + len(message.role_mentions)
        if nb_mentions >= threshold:
            try:
                await message.delete()
            except Exception:
                pass
            await _log_action(
                bot, message.guild, log_ch,
                "Message supprime (spam mentions)",
                f"Auteur : {message.author.mention} ({message.author.id})\n"
                f"Salon : {message.channel.mention}\n"
                f"Mentions : **{nb_mentions}** (seuil : {threshold})",
            )
            return


async def automod_on_member_join(member: discord.Member, bot):
    """Hook on_member_join : raid protection (compte joins/minute)."""
    if not member.guild:
        return
    cfg = _get_cfg(member.guild.id)
    if not cfg.get("enabled") or not cfg.get("raid_protection_enabled"):
        return
    threshold = max(2, int(cfg.get("raid_threshold") or 5))
    now = time.time()
    gkey = str(member.guild.id)
    w = _JOIN_WINDOW.setdefault(gkey, [])
    # Trim
    cutoff = now - _JOIN_WINDOW_SEC
    while w and w[0] < cutoff:
        w.pop(0)
    w.append(now)
    if len(w) >= threshold:
        # Raid suspect : log (pas de kick/ban auto pour eviter faux positifs)
        await _log_action(
            bot, member.guild, cfg.get("log_channel_id"),
            "Raid suspect detecte",
            f"**{len(w)}** joins en moins de {_JOIN_WINDOW_SEC}s "
            f"(seuil : {threshold}/min).\n"
            f"Dernier joiner : {member.mention} ({member.id})",
            color=discord.Color.orange(),
            fields=[("Recommandation",
                     "Active le verrouillage du serveur ou augmente le seuil "
                     "de verification Discord si confirme.")],
        )
        # Reset le compteur pour pas spam le log
        _JOIN_WINDOW[gkey] = []
