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
    # Accepte SPOTIPY_* (officiel spotipy) ou SPOTIFY_* (commun) ou KOFI-style
    cid = (os.getenv("SPOTIPY_CLIENT_ID")
           or os.getenv("SPOTIFY_CLIENT_ID")
           or os.getenv("SPOTIFY_ID"))
    csec = (os.getenv("SPOTIPY_CLIENT_SECRET")
            or os.getenv("SPOTIFY_CLIENT_SECRET")
            or os.getenv("SPOTIFY_SECRET"))
    if not cid or not csec:
        present = [k for k in os.environ if k.startswith("SPOTI")]
        raise RuntimeError(
            "Clefs Spotify introuvables. Cherche les noms : SPOTIPY_CLIENT_ID, "
            "SPOTIPY_CLIENT_SECRET. Vars SPOTI* presentes dans l'env du process : "
            f"{present or 'aucune'}. (Si tu viens d'editer .env, fais pm2 restart all)"
        )
    auth = SpotifyClientCredentials(client_id=cid, client_secret=csec)
    _CLIENT = spotipy.Spotify(auth_manager=auth, requests_timeout=10, retries=2)
    return _CLIENT


def search_spotify(query, limit=5):
    """Cherche tracks Spotify. Retourne liste [{title, artists, url, duration_ms, thumbnail}]."""
    sp = _spotify_client()
    res = sp.search(q=query, limit=limit, type="track")
    out = []
    for t in (res.get("tracks", {}).get("items") or []):
        thumb = None
        if t.get("album", {}).get("images"):
            thumb = t["album"]["images"][0].get("url")
        out.append({
            "title": t.get("name") or "(sans titre)",
            "artists": ", ".join(a.get("name", "") for a in (t.get("artists") or [])),
            "url": t.get("external_urls", {}).get("spotify"),
            "duration_ms": t.get("duration_ms"),
            "thumbnail": thumb,
            "query": _track_to_query(t),
        })
    return out


def _fetch_html(url: str, timeout: int = 12) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _extract_tracks_from_embed_html(html: str) -> tuple[str, list[dict]]:
    """Cherche __NEXT_DATA__ et extract entity.trackList. Limite par defaut
    ~10-50 tracks (preview embed)."""
    import json
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        html, flags=re.DOTALL,
    )
    if not m:
        raise RuntimeError("embed: __NEXT_DATA__ introuvable")
    data = json.loads(m.group(1))
    state = (((data.get("props") or {}).get("pageProps") or {})
             .get("state") or {})
    entity = ((state.get("data") or {}).get("entity") or {})
    name = entity.get("name") or "Playlist Spotify"
    raw_tracks = entity.get("trackList") or []
    out = []
    for t in raw_tracks:
        n = t.get("title") or ""
        a = t.get("subtitle") or ""
        out.append({
            "query": f"{a} - {n}".strip(" -"),
            "duration_ms": t.get("duration"),
        })
    return name, out


def _extract_tracks_from_main_page(spid: str) -> tuple[str, list[dict]]:
    """Tente de fetch open.spotify.com/playlist/<spid> (page web complete).
    Cette page contient typiquement bien plus de tracks que l'embed (full
    initial state). Le JSON est dans <script id="__NEXT_DATA__"> aussi.

    Spotify peut servir des structures differentes selon le user-agent ;
    on essaie de parser les chemins typiques.
    """
    import json
    url = f"https://open.spotify.com/playlist/{spid}"
    html = _fetch_html(url)
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        html, flags=re.DOTALL,
    )
    if not m:
        # Fallback : Spotify a aussi un JSON-LD script type=application/ld+json
        ld = re.search(
            r'<script type="application/ld\+json"[^>]*>(.+?)</script>',
            html, flags=re.DOTALL,
        )
        if not ld:
            raise RuntimeError("main page: pas de __NEXT_DATA__ ni JSON-LD")
        meta = json.loads(ld.group(1))
        name = meta.get("name") or "Playlist Spotify"
        tracks_list = meta.get("track") or []
        # JSON-LD structure : track est une list d'objets {name, byArtist}
        out = []
        for t in tracks_list:
            n = t.get("name") or ""
            artists = t.get("byArtist") or []
            if isinstance(artists, dict): artists = [artists]
            a = ", ".join(art.get("name", "") for art in artists)
            out.append({
                "query": f"{a} - {n}".strip(" -"),
                "duration_ms": None,
            })
        return name, out
    data = json.loads(m.group(1))
    # Chemins possibles pour la full page (varie selon version Spotify) :
    # 1) props.pageProps.state.data.entity.tracks.items[].track
    # 2) props.pageProps.fallback['/dynamic/playlist/...']
    # Try option 1 first.
    state = (((data.get("props") or {}).get("pageProps") or {})
             .get("state") or {})
    entity = ((state.get("data") or {}).get("entity") or {})
    name = entity.get("name") or "Playlist Spotify"

    out = []
    # Format detail page: entity.tracks.items = [{track: {name, artists}}]
    tracks_container = entity.get("tracks") or {}
    items = tracks_container.get("items") or []
    if items:
        for it in items:
            t = (it or {}).get("track") or it
            n = t.get("name") or ""
            artists_arr = t.get("artists") or []
            a = ", ".join((art or {}).get("name", "") for art in artists_arr)
            out.append({
                "query": f"{a} - {n}".strip(" -"),
                "duration_ms": t.get("duration_ms"),
            })
        return name, out

    # Format embed-style fallback (trackList)
    raw_tracks = entity.get("trackList") or []
    for t in raw_tracks:
        n = t.get("title") or ""
        a = t.get("subtitle") or ""
        out.append({
            "query": f"{a} - {n}".strip(" -"),
            "duration_ms": t.get("duration"),
        })
    return name, out


def _get_anon_access_token() -> str:
    """Recupere le token anonyme que le web player Spotify utilise.
    Permet d'appeler l'API playlists/{id}/tracks SANS OAuth user.
    Token typiquement valide ~1h."""
    import json
    url = "https://open.spotify.com/get_access_token?reason=transport&productType=web_player"
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tok = data.get("accessToken")
    if not tok:
        raise RuntimeError("anon token introuvable dans la reponse")
    return tok


def _resolve_playlist_via_anon_api(spid: str, max_tracks: int = 1000) -> tuple[str, list[dict]]:
    """Fetch la full liste de tracks via API Spotify avec le token anon
    du web player. Pagine par 100 jusqu'a max_tracks (ou epuisement)."""
    import json
    import urllib.request
    tok = _get_anon_access_token()
    headers = {
        "Authorization": f"Bearer {tok}",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    # Fetch meta + premiere page
    pl_name = "Playlist Spotify"
    try:
        req = urllib.request.Request(
            f"https://api.spotify.com/v1/playlists/{spid}?fields=name,tracks.total",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        pl_name = meta.get("name") or pl_name
        total = (meta.get("tracks") or {}).get("total") or 0
    except Exception as e:
        print(f"[spotify anon] meta fail: {type(e).__name__}: {e}")
        total = max_tracks

    tracks = []
    offset = 0
    while len(tracks) < max_tracks and offset < total:
        url = (f"https://api.spotify.com/v1/playlists/{spid}/tracks"
               f"?limit=100&offset={offset}"
               f"&fields=items(track(name,artists(name),duration_ms)),next")
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                page = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[spotify anon] page offset={offset} fail: {type(e).__name__}: {e}")
            break
        items = page.get("items") or []
        if not items:
            break
        for it in items:
            t = (it or {}).get("track")
            if not t:
                continue
            artists = ", ".join((a or {}).get("name", "") for a in (t.get("artists") or []))
            tracks.append({
                "query": f"{artists} - {t.get('name') or ''}".strip(" -"),
                "duration_ms": t.get("duration_ms"),
            })
            if len(tracks) >= max_tracks:
                break
        offset += 100
        if not page.get("next"):
            break
    print(f"[spotify anon] spid={spid} resolved {len(tracks)} tracks (total Spotify={total})", flush=True)
    return pl_name, tracks


def _resolve_playlist_via_embed(spid: str, max_tracks: int = 50) -> dict:
    """Resout une playlist Spotify sans OAuth.

    Strategie :
    1) PRIORITE : token anonyme du web player + API officielle paginee
       (permet d'obtenir jusqu'a max_tracks meme pour grosses playlists)
    2) Fallback : main page scrape + embed scrape
    3) Garde le resultat avec le PLUS de tracks
    """
    # 1) Tente anon access token + API officielle
    try:
        name_anon, tracks_anon = _resolve_playlist_via_anon_api(spid, max_tracks)
        if tracks_anon:
            return {
                "kind": "playlist",
                "title": name_anon,
                "tracks": tracks_anon[:max_tracks],
            }
    except Exception as e:
        print(f"[spotify] anon api spid={spid} fail: {type(e).__name__}: {e}", flush=True)
    pl_name_main, tracks_main = "Playlist Spotify", []
    pl_name_emb, tracks_emb = "Playlist Spotify", []
    try:
        pl_name_main, tracks_main = _extract_tracks_from_main_page(spid)
        print(f"[spotify] main page spid={spid} : {len(tracks_main)} tracks", flush=True)
    except Exception as e:
        print(f"[spotify] main page spid={spid} fail : {type(e).__name__}: {e}", flush=True)
    try:
        html = _fetch_html(f"https://open.spotify.com/embed/playlist/{spid}")
        pl_name_emb, tracks_emb = _extract_tracks_from_embed_html(html)
        print(f"[spotify] embed spid={spid} : {len(tracks_emb)} tracks", flush=True)
    except Exception as e:
        print(f"[spotify] embed spid={spid} fail : {type(e).__name__}: {e}", flush=True)

    if len(tracks_main) >= len(tracks_emb):
        chosen = tracks_main[:max_tracks]
        chosen_name = pl_name_main
    else:
        chosen = tracks_emb[:max_tracks]
        chosen_name = pl_name_emb

    if not chosen:
        raise RuntimeError("Spotify playlist: aucune piste recuperable (page protegee ?)")
    print(f"[spotify] playlist spid={spid} resolved {len(chosen)} tracks (cap {max_tracks})", flush=True)
    return {
        "kind": "playlist",
        "title": chosen_name,
        "tracks": chosen,
    }


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
        # Spotify a restreint l'acces playlist_items pour Client Credentials
        # (Nov 2024 : 401 'Valid user authentication required'). Workaround :
        # scrape la page publique embed pour extraire la liste de tracks.
        return _resolve_playlist_via_embed(spid, max_tracks=max_tracks)

    raise ValueError(f"Type Spotify non gere : {kind}")
