"""Providers d'alertes sociales pour le bot.

Trois plateformes :
- youtube : flux RSS public (pas de cle API requise)
- reddit  : flux RSS public (User-Agent obligatoire)
- twitch  : Helix API (necessite TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET dans .env)

Chaque provider expose une fonction async `check_<provider>(target_id, last_seen_id)`
qui retourne une liste de dicts {id, title, url, [extra]} representant les
nouveautes depuis last_seen_id (ou la 1ere detection si last_seen_id est vide).
"""
from __future__ import annotations

import os
import re
import time
import asyncio
import xml.etree.ElementTree as _ET
from typing import Optional

import aiohttp


# --------------------------------------------------------------------------
# Helpers HTTP partages
# --------------------------------------------------------------------------

_USER_AGENT = "TookBot/1.0 (+https://tookbot.click)"
_HTTP_SESSION: Optional[aiohttp.ClientSession] = None
_HTTP_LOCK = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION and not _HTTP_SESSION.closed:
        return _HTTP_SESSION
    async with _HTTP_LOCK:
        if _HTTP_SESSION and not _HTTP_SESSION.closed:
            return _HTTP_SESSION
        timeout = aiohttp.ClientTimeout(total=12, connect=6)
        _HTTP_SESSION = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        )
        return _HTTP_SESSION


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------
# Le flux RSS officiel : https://www.youtube.com/feeds/videos.xml?channel_id=UCxxx
# `target_id` doit etre l'ID de chaine YouTube (commence par UC...).
# On peut aussi accepter un @handle (ex. @LeStream) -> on resoud d'abord vers UC.

YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
_YT_NS = {"atom": "http://www.w3.org/2005/Atom",
          "yt":   "http://www.youtube.com/xml/schemas/2015"}


async def youtube_resolve_handle(handle: str) -> Optional[str]:
    """Tente de resoudre un @handle YouTube vers un channel_id (UCxxx).
    Pas d'API officielle gratuite ; on scrape la page publique. Retourne None
    si echec.
    """
    if handle.startswith("UC") and len(handle) >= 20:
        return handle
    if handle.startswith("@"):
        url = f"https://www.youtube.com/{handle}"
    else:
        url = f"https://www.youtube.com/@{handle}"
    try:
        s = await _get_session()
        async with s.get(url) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
            m = re.search(r'"channelId":"(UC[\w-]{20,})"', html)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


async def check_youtube(target_id: str, last_seen_id: Optional[str]) -> list[dict]:
    """Retourne les nouvelles videos depuis last_seen_id.
    target_id = channel_id (UCxxx) idealement. Si commence par '@', on resoud.
    """
    cid = target_id
    if not cid.startswith("UC"):
        resolved = await youtube_resolve_handle(target_id)
        if not resolved:
            return []
        cid = resolved

    url = YOUTUBE_FEED_URL.format(cid=cid)
    s = await _get_session()
    try:
        async with s.get(url) as resp:
            if resp.status != 200:
                return []
            xml = await resp.text()
    except Exception:
        return []

    try:
        root = _ET.fromstring(xml)
    except Exception:
        return []

    items = []
    for entry in root.findall("atom:entry", _YT_NS):
        vid_el = entry.find("yt:videoId", _YT_NS)
        if vid_el is None:
            continue
        vid = vid_el.text or ""
        title = (entry.find("atom:title", _YT_NS).text or "").strip()
        link_el = entry.find("atom:link", _YT_NS)
        link = link_el.attrib.get("href") if link_el is not None else f"https://youtu.be/{vid}"
        author_el = entry.find("atom:author/atom:name", _YT_NS)
        author = author_el.text if author_el is not None else ""
        items.append({
            "id":     vid,
            "title":  title,
            "url":    link,
            "author": author,
            "platform_target": cid,
        })

    if not items:
        return []
    if not last_seen_id:
        # Premiere detection : on n'envoie rien (on note juste la derniere video
        # connue pour comparer la prochaine fois)
        return []
    new_items = []
    for it in items:
        if it["id"] == last_seen_id:
            break
        new_items.append(it)
    new_items.reverse()  # plus ancienne d'abord pour poster en ordre
    return new_items


# --------------------------------------------------------------------------
# Reddit
# --------------------------------------------------------------------------
# Flux RSS de l'utilisateur : https://www.reddit.com/user/<u>/.rss
# Ou d'un subreddit : https://www.reddit.com/r/<sub>/new/.rss
# On accepte les deux : si target commence par 'r/', subreddit ; sinon user.

async def check_reddit(target_id: str, last_seen_id: Optional[str]) -> list[dict]:
    target = target_id.strip()
    if target.lower().startswith("r/"):
        url = f"https://www.reddit.com/{target}/new/.rss"
    elif target.lower().startswith("u/"):
        url = f"https://www.reddit.com/user/{target[2:]}/submitted/.rss"
    else:
        url = f"https://www.reddit.com/user/{target}/submitted/.rss"

    s = await _get_session()
    try:
        async with s.get(url) as resp:
            if resp.status != 200:
                return []
            xml = await resp.text()
    except Exception:
        return []
    try:
        root = _ET.fromstring(xml)
    except Exception:
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("atom:entry", ns):
        eid_el  = entry.find("atom:id", ns)
        ttl_el  = entry.find("atom:title", ns)
        link_el = entry.find("atom:link", ns)
        if eid_el is None or ttl_el is None:
            continue
        items.append({
            "id":    eid_el.text or "",
            "title": (ttl_el.text or "").strip(),
            "url":   link_el.attrib.get("href") if link_el is not None else "",
        })
    if not items:
        return []
    if not last_seen_id:
        return []
    new_items = []
    for it in items:
        if it["id"] == last_seen_id:
            break
        new_items.append(it)
    new_items.reverse()
    return new_items


# --------------------------------------------------------------------------
# Twitch
# --------------------------------------------------------------------------
# Necessite TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET dans .env.
# Pour eviter les notifications repetees, last_seen_id contient :
#   - 'live'    -> stream actif au dernier check
#   - 'offline' -> stream pas actif au dernier check

_TWITCH_CLIENT_ID     = os.getenv("TWITCH_CLIENT_ID", "").strip() or None
_TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip() or None
_TWITCH_TOKEN_CACHE   = {"access_token": None, "expires_at": 0}


async def _twitch_get_token() -> Optional[str]:
    if not (_TWITCH_CLIENT_ID and _TWITCH_CLIENT_SECRET):
        return None
    now = time.time()
    if _TWITCH_TOKEN_CACHE["access_token"] and _TWITCH_TOKEN_CACHE["expires_at"] > now + 60:
        return _TWITCH_TOKEN_CACHE["access_token"]
    s = await _get_session()
    try:
        async with s.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id":     _TWITCH_CLIENT_ID,
                "client_secret": _TWITCH_CLIENT_SECRET,
                "grant_type":    "client_credentials",
            },
        ) as resp:
            if resp.status != 200:
                return None
            j = await resp.json()
            tok = j.get("access_token")
            ttl = int(j.get("expires_in") or 0)
            if tok:
                _TWITCH_TOKEN_CACHE["access_token"] = tok
                _TWITCH_TOKEN_CACHE["expires_at"]   = now + ttl
                return tok
    except Exception:
        return None
    return None


async def check_twitch(target_id: str, last_seen_id: Optional[str]) -> list[dict]:
    """target_id = login Twitch (ex: 'pokimane'). last_seen_id 'live' ou 'offline'."""
    if not (_TWITCH_CLIENT_ID and _TWITCH_CLIENT_SECRET):
        return []
    token = await _twitch_get_token()
    if not token:
        return []
    s = await _get_session()
    headers = {
        "Authorization": f"Bearer {token}",
        "Client-Id":     _TWITCH_CLIENT_ID,
    }
    try:
        async with s.get(
            f"https://api.twitch.tv/helix/streams?user_login={target_id}",
            headers=headers,
        ) as resp:
            if resp.status != 200:
                return []
            j = await resp.json()
            data = j.get("data") or []
    except Exception:
        return []

    is_live = bool(data)
    if is_live and last_seen_id != "live":
        # Transition offline -> live : notifier
        stream = data[0]
        return [{
            "id":       "live",
            "title":    stream.get("title") or f"{target_id} est en live !",
            "url":      f"https://twitch.tv/{target_id}",
            "game":     stream.get("game_name") or "",
            "viewers":  stream.get("viewer_count") or 0,
            "started":  stream.get("started_at") or "",
            "thumb":    (stream.get("thumbnail_url") or "").replace("{width}", "1280").replace("{height}", "720"),
        }]
    if not is_live and last_seen_id != "offline":
        # Transition live -> offline : pas de notification, juste mettre a jour
        return [{"id": "offline", "_silent": True}]
    return []


# --------------------------------------------------------------------------
# URL parser
# --------------------------------------------------------------------------
# Accepte une URL de chaine/profil et extrait l'identifiant que les providers
# attendent. Retourne (target_id, display_label) ou None si invalide.

def parse_social_url(platform: str, raw: str) -> Optional[tuple[str, str]]:
    s = (raw or "").strip()
    if not s:
        return None

    if platform == "twitch":
        m = re.search(r'twitch\.tv/([A-Za-z0-9_]{3,30})', s, re.IGNORECASE)
        if m:
            login = m.group(1).lower()
            return (login, login)
        if re.fullmatch(r'[A-Za-z0-9_]{3,30}', s):
            return (s.lower(), s.lower())
        return None

    if platform == "youtube":
        m = re.search(r'youtube\.com/channel/(UC[\w-]{20,})', s)
        if m:
            return (m.group(1), m.group(1))
        m = re.search(r'youtube\.com/(@[\w.\-]+)', s)
        if m:
            return (m.group(1), m.group(1))
        m = re.search(r'youtube\.com/(?:c|user)/([\w.\-]+)', s)
        if m:
            return ("@" + m.group(1), "@" + m.group(1))
        m = re.search(r'youtu\.be/(@[\w.\-]+)', s)
        if m:
            return (m.group(1), m.group(1))
        if s.startswith("UC") and len(s) >= 20:
            return (s, s)
        if s.startswith("@"):
            return (s, s)
        return None

    if platform == "reddit":
        m = re.search(r'reddit\.com/r/([A-Za-z0-9_]+)', s, re.IGNORECASE)
        if m:
            return (f"r/{m.group(1)}", f"r/{m.group(1)}")
        m = re.search(r'reddit\.com/(?:u|user)/([A-Za-z0-9_-]+)', s, re.IGNORECASE)
        if m:
            return (m.group(1), f"u/{m.group(1)}")
        low = s.lower()
        if low.startswith("r/") or low.startswith("u/"):
            return (s, s)
        return None

    return None


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

PROVIDERS = {
    "youtube": check_youtube,
    "reddit":  check_reddit,
    "twitch":  check_twitch,
}


async def check_platform(platform: str, target_id: str, last_seen_id: Optional[str]) -> list[dict]:
    fn = PROVIDERS.get(platform)
    if not fn:
        return []
    try:
        return await fn(target_id, last_seen_id)
    except Exception as e:
        print(f"[social] check {platform}/{target_id} error: {e!r}")
        return []
