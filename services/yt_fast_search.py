"""Search YouTube via HTML scrape direct.

Pas de yt-dlp (qui declenche bgutil-pot-provider plugin asyncio.run en
parallel context). Pas d'import de bot.py (qui aurait des side-effects).

Usage :
    from services.yt_fast_search import yt_search_fast
    info = await yt_search_fast("Daft Punk - One More Time")
    # -> {title, url, source_url, duration, thumbnail}
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import urllib.request


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _walk_for_video(obj):
    if isinstance(obj, dict):
        if "videoRenderer" in obj:
            return obj["videoRenderer"]
        for v in obj.values():
            r = _walk_for_video(v)
            if r:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = _walk_for_video(it)
            if r:
                return r
    return None


def _yt_search_sync(query: str) -> dict:
    """Recherche YouTube via /results?search_query=... et parse
    ytInitialData JSON pour extraire le 1er videoRenderer."""
    if query.startswith("http"):
        return {"title": query, "url": query, "source_url": query}
    q = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.DOTALL)
    if not m:
        raise RuntimeError("yt search: ytInitialData introuvable")
    data = json.loads(m.group(1))
    vr = _walk_for_video(data)
    if not vr:
        raise RuntimeError("yt search: aucun videoRenderer trouve")
    vid = vr.get("videoId")
    if not vid:
        raise RuntimeError("yt search: pas de videoId")
    title = ""
    title_runs = (vr.get("title") or {}).get("runs") or []
    if title_runs:
        title = title_runs[0].get("text") or ""
    duration = None
    duration_text = (vr.get("lengthText") or {}).get("simpleText") or ""
    if duration_text and ":" in duration_text:
        try:
            parts = [int(x) for x in duration_text.split(":")]
            if len(parts) == 2:
                duration = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                duration = parts[0] * 3600 + parts[1] * 60 + parts[2]
        except ValueError:
            pass
    thumb = None
    thumbs = (vr.get("thumbnail") or {}).get("thumbnails") or []
    if thumbs:
        thumb = thumbs[-1].get("url")
    # Fallback : construit URL depuis video_id (toujours dispo via ytimg)
    if not thumb and vid:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    yt_url = f"https://www.youtube.com/watch?v={vid}"
    return {
        "title": title or query,
        "url": yt_url,
        "source_url": yt_url,
        "duration": duration,
        "thumbnail": thumb,
    }


async def yt_search_fast(query: str) -> dict:
    """Version async (lance en thread pool)."""
    return await asyncio.to_thread(_yt_search_sync, query)
