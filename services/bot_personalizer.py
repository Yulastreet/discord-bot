"""Personnalisation du bot par serveur via Discord API native.

Discord supporte depuis 2024 un avatar/banner/nickname differents pour les
bots par serveur, via PATCH /guilds/{guild_id}/members/@me.
Pas besoin de webhooks.

Limitations :
- Nickname : rate-limit Discord ~10 par 10s par guild
- Avatar / Banner per-guild : rate-limit plus strict (~2/min par guild)
- About Me (description application) : GLOBAL uniquement, partage entre serveurs
"""

import base64
import asyncio
import aiohttp
from pathlib import Path


DISCORD_API = "https://discord.com/api/v10"


def _detect_mime(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower()
    return {
        "png":  "image/png",
        "jpg":  "image/jpeg",
        "jpeg": "image/jpeg",
        "gif":  "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/png")


def _file_to_data_uri(path: str) -> str | None:
    """Convertit un fichier local en data: URI base64. None si fichier absent."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    if p.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Fichier > 5 Mo, refuse par Discord")
    mime = _detect_mime(str(p))
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


_ACTIVITY_TYPE_MAP = {
    "playing":   0,
    "streaming": 1,
    "listening": 2,
    "watching":  3,
    "custom":    4,
    "competing": 5,
}


async def patch_server_profile(token: str, guild_id, *,
                                nick: str | None = None,
                                bio:  str | None = None,
                                avatar_path: str | None = None,
                                banner_path: str | None = None,
                                status: str | None = None,
                                activity_type: str | None = None,
                                activity_text: str | None = None,
                                clear_avatar: bool = False,
                                clear_banner: bool = False) -> tuple[int, dict]:
    """PATCH /guilds/{guild_id}/members/@me. Retourne (status_code, json_body).

    Champs Discord supportes pour bot self-member :
    - nick   : string (max 32 chars). None = pas de changement, '' = reset.
    - bio    : string (max ~190 chars). EXPERIMENTAL - Discord ne documente pas
               officiellement bio per-guild pour bots. Si l'endpoint refuse,
               on remonte l'erreur dans body.
    - avatar / banner : data URI base64.
    - clear_avatar / clear_banner : True = explicitement reset (null).
    """
    payload = {}
    if nick is not None:
        payload["nick"] = (nick or "")[:32]
    if bio is not None:
        payload["bio"] = (bio or "")[:190]
    if clear_avatar:
        payload["avatar"] = None
    elif avatar_path:
        uri = _file_to_data_uri(avatar_path)
        if uri:
            payload["avatar"] = uri
    if clear_banner:
        payload["banner"] = None
    elif banner_path:
        uri = _file_to_data_uri(banner_path)
        if uri:
            payload["banner"] = uri

    # Status + activity per-guild : EXPERIMENTAL. Discord ne documente pas
    # officiellement ces champs sur PATCH guild member. Tente quand meme.
    if status:
        payload["status"] = status  # online | idle | dnd | invisible
    if activity_type and activity_text:
        atype = _ACTIVITY_TYPE_MAP.get(activity_type.lower(), 0)
        payload["activities"] = [{"name": activity_text[:128], "type": atype}]

    if not payload:
        return 0, {"error": "rien a patcher"}

    url = f"{DISCORD_API}/guilds/{int(guild_id)}/members/@me"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type":  "application/json",
        "User-Agent":    "TookBot Customization (https://tookbot.click, 1.0)",
    }
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.patch(url, json=payload, headers=headers) as r:
            try:
                body = await r.json()
            except Exception:
                body = {"raw": await r.text()}
            return r.status, body


async def patch_about_me(token: str, description: str) -> tuple[int, dict]:
    """PATCH /applications/@me {description}. Bio bot GLOBALE (pas per-server)."""
    url = f"{DISCORD_API}/applications/@me"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type":  "application/json",
        "User-Agent":    "TookBot Customization (https://tookbot.click, 1.0)",
    }
    payload = {"description": (description or "")[:400]}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.patch(url, json=payload, headers=headers) as r:
            try:
                body = await r.json()
            except Exception:
                body = {"raw": await r.text()}
            return r.status, body


# ----- Helpers synchrones (pour Flask web)

def apply_profile_sync(token: str, guild_id, *, nick=None, bio=None,
                       avatar_path=None, banner_path=None,
                       status=None, activity_type=None, activity_text=None,
                       clear_avatar=False, clear_banner=False) -> dict:
    """Wrapper sync : lance la coro patch_server_profile dans un loop one-shot."""
    return asyncio.run(patch_server_profile(
        token, guild_id,
        nick=nick, bio=bio,
        avatar_path=avatar_path, banner_path=banner_path,
        status=status, activity_type=activity_type, activity_text=activity_text,
        clear_avatar=clear_avatar, clear_banner=clear_banner,
    ))


def apply_about_me_sync(token: str, description: str) -> dict:
    return asyncio.run(patch_about_me(token, description))
