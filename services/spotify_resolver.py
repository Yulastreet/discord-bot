"""Spotify URL resolver.

Spotify ne streame pas son audio via API publique (DRM). On utilise
l'API Spotify uniquement pour resoudre URL track/album/playlist en
metadonnees (titre + artistes), puis on cherche l'equivalent sur
YouTube via yt-dlp (cf. bot.search_youtube / get_audio_info).

Necessite env vars :
    SPOTIPY_CLIENT_ID
    SPOTIPY_CLIENT_SECRET

Creation : developer.spotify.com/dashboard -> Create app
(callback URL bidon ex http://localhost, scopes vides).
"""

import os
import re

_SPOTIFY_RE = re.compile(
    r"open\.spotify\.com/(intl-[a-z]{2}/)?(track|album|playlist)/([a-zA-Z0-9]+)"
)


def is_spotify_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    return bool(_SPOTIFY_RE.search(url))


def _spotify_client():
    """Cree client Spotipy Client Credentials. Cache module-level pour reuse."""
    global _CLIENT
    try:
        return _CLIENT
    except NameError:
        pass
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
    except ImportError as e:
        raise RuntimeError(
            "spotipy non installe. pip install spotipy"
        ) from e
    cid = os.getenv("SPOTIPY_CLIENT_ID")
    csec = os.getenv("SPOTIPY_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError(
            "Variables SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET absentes."
        )
    auth = SpotifyClientCredentials(client_id=cid, client_secret=csec)
    _CLIENT = spotipy.Spotify(auth_manager=auth, requests_timeout=10, retries=2)
    return _CLIENT


def _track_to_query(track: dict) -> str:
    """Convertit un track Spotify en query texte pour YouTube search."""
    name = (track.get("name") or "").strip()
    artists = ", ".join(a.get("name", "") for a in (track.get("artists") or []) if a)
    return f"{artists} - {name}".strip(" -")


def resolve_spotify_url(url: str, max_tracks: int = 50) -> dict:
    """Resout URL Spotify en {kind, title, tracks:[{query, duration_ms, ...}]}.

    kind : "track" | "album" | "playlist"
    tracks : liste de queries textuelles pretes pour ytsearch.
    Levee : RuntimeError si client non configure ou URL invalide.
    """
    m = _SPOTIFY_RE.search(url)
    if not m:
        raise ValueError(f"URL Spotify invalide : {url}")
    kind = m.group(2)
    spid = m.group(3)
    sp = _spotify_client()

    if kind == "track":
        t = sp.track(spid)
        return {
            "kind": "track",
            "title": _track_to_query(t),
            "tracks": [{
                "query": _track_to_query(t),
                "duration_ms": t.get("duration_ms"),
                "spotify_url": t.get("external_urls", {}).get("spotify"),
            }],
        }

    if kind == "album":
        al = sp.album(spid)
        items = al.get("tracks", {}).get("items") or []
        tracks = []
        for t in items[:max_tracks]:
            tracks.append({
                "query": _track_to_query(t),
                "duration_ms": t.get("duration_ms"),
            })
        return {
            "kind": "album",
            "title": al.get("name") or "Album Spotify",
            "tracks": tracks,
        }

    if kind == "playlist":
        pl = sp.playlist(spid, fields="name,tracks.items(track(name,artists,duration_ms))")
        items = (pl.get("tracks") or {}).get("items") or []
        tracks = []
        for it in items[:max_tracks]:
            t = (it or {}).get("track")
            if not t:
                continue
            tracks.append({
                "query": _track_to_query(t),
                "duration_ms": t.get("duration_ms"),
            })
        return {
            "kind": "playlist",
            "title": pl.get("name") or "Playlist Spotify",
            "tracks": tracks,
        }

    raise ValueError(f"Type Spotify non gere : {kind}")
