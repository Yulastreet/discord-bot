"""HTTP client for Riot Games API (League of Legends).

Endpoints used :
- ACCOUNT-V1 (regional) : riot id <-> puuid
- SUMMONER-V4 (platform) : puuid -> summonerId + level + profile icon
- LEAGUE-V4  (platform) : ranks (solo / flex)
- CHAMPION-MASTERY-V4 (platform) : top mastery champions

Region routing :
- platform routes (summoner/league/mastery) : euw1, na1, kr, jp1, eun1...
- regional routes (account/match-v5)        : europe, americas, asia, sea
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

import aiohttp
from urllib.parse import quote


_USER_AGENT = "TookBot-LoL/1.0 (+https://tookbot.click)"
_SESSION: Optional[aiohttp.ClientSession] = None
_LOCK = asyncio.Lock()


# Platform region -> regional route
PLATFORM_TO_REGIONAL = {
    "euw1":  "europe",
    "eun1":  "europe",
    "tr1":   "europe",
    "ru":    "europe",
    "na1":   "americas",
    "br1":   "americas",
    "la1":   "americas",
    "la2":   "americas",
    "kr":    "asia",
    "jp1":   "asia",
    "oc1":   "sea",
    "ph2":   "sea",
    "sg2":   "sea",
    "th2":   "sea",
    "tw2":   "sea",
    "vn2":   "sea",
}

# Display label per platform
PLATFORM_LABEL = {
    "euw1": "EUW", "eun1": "EUNE", "tr1": "TR", "ru": "RU",
    "na1": "NA", "br1": "BR", "la1": "LAN", "la2": "LAS",
    "kr": "KR", "jp1": "JP", "oc1": "OCE",
    "ph2": "PH", "sg2": "SG", "th2": "TH", "tw2": "TW", "vn2": "VN",
}

# Tier metadata : color (Discord embed) + emblem image
TIER_COLOR = {
    "IRON":        0x5B5B5C,
    "BRONZE":      0x8C5230,
    "SILVER":      0x95A3A5,
    "GOLD":        0xC8A45C,
    "PLATINUM":    0x28A29F,
    "EMERALD":     0x50C878,
    "DIAMOND":     0x4E8FD8,
    "MASTER":      0x9B59B6,
    "GRANDMASTER": 0xC0392B,
    "CHALLENGER":  0xF1C40F,
    "UNRANKED":    0x747F8D,
}

def tier_emblem_url(tier: str) -> str:
    """Community Dragon hoste les emblems ranked. URL stable."""
    t = (tier or "UNRANKED").upper()
    if t == "UNRANKED":
        return "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-iron.png"
    return f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-{t.lower()}.png"


# Cache local des emblems croppes et redimensionnes
import os as _os
_EMBLEM_CACHE_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "assets", "lol_emblems",
)

async def tier_emblem_file_path(tier: str) -> Optional[str]:
    """Retourne le path local d'un emblem cropped+redim (256x256).
    Telecharge depuis CommunityDragon a la premiere demande, puis cache.
    Renvoie None si echec (fallback : set_thumbnail avec URL distante)."""
    t = (tier or "UNRANKED").upper()
    if t == "UNRANKED":
        return None
    _os.makedirs(_EMBLEM_CACHE_DIR, exist_ok=True)
    target = _os.path.join(_EMBLEM_CACHE_DIR, f"{t.lower()}.png")
    if _os.path.exists(target) and _os.path.getsize(target) > 0:
        return target
    # Telecharge + crop bbox + resize
    s = await _get_session()
    try:
        async with s.get(tier_emblem_url(t)) as resp:
            if resp.status != 200:
                return None
            raw = await resp.read()
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        # Resize a 256x256 (max display thumbnail Discord ~80x80, image ~400)
        im.thumbnail((256, 256), Image.LANCZOS)
        im.save(target, format="PNG", optimize=True)
        print(f"[riot/emblem] cached {t} -> {target} size={im.size}")
        return target
    except Exception as e:
        print(f"[riot/emblem] cache err {t}: {type(e).__name__}: {e}")
        return None


async def _get_session() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION and not _SESSION.closed:
        return _SESSION
    async with _LOCK:
        if _SESSION and not _SESSION.closed:
            return _SESSION
        _SESSION = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15, connect=8),
            headers={"User-Agent": _USER_AGENT},
        )
        return _SESSION


def _key() -> Optional[str]:
    return (os.getenv("RIOT_API_KEY") or "").strip() or None


def regional_route(platform: str) -> str:
    return PLATFORM_TO_REGIONAL.get(platform.lower(), "europe")


async def _get(host_prefix: str, path: str) -> Optional[dict]:
    """GET helper qui ajoute la cle et logge non-200."""
    key = _key()
    if not key:
        print("[riot] missing RIOT_API_KEY — verifie .env + pm2 --update-env")
        return None
    url = f"https://{host_prefix}.api.riotgames.com{path}"
    headers = {"X-Riot-Token": key, "Accept": "application/json"}
    s = await _get_session()
    try:
        async with s.get(url, headers=headers) as resp:
            body = await resp.text()
            if resp.status == 404:
                return None
            if resp.status == 401:
                print(f"[riot] 401 unauthorized — cle invalide ou expiree (dev key 24h)")
                return None
            if resp.status == 403:
                print(f"[riot] 403 forbidden — cle revoquee ou endpoint hors scope")
                return None
            if resp.status == 429:
                print(f"[riot] 429 rate-limited — retry after {resp.headers.get('Retry-After')}")
                return None
            if resp.status >= 500:
                print(f"[riot] {resp.status} upstream error path={path}")
                return None
            if resp.status != 200:
                print(f"[riot] status={resp.status} path={path} body={body[:200]!r}")
                return None
            import json as _json
            return _json.loads(body)
    except Exception as e:
        print(f"[riot] err path={path}: {type(e).__name__}: {e}")
        return None


# ===== Account API (regional) =====
async def account_by_riot_id(platform: str, game_name: str, tag_line: str) -> Optional[dict]:
    """Resoud Riot ID 'name#tag' -> {puuid, gameName, tagLine}.
    URL-encode les params : pseudos peuvent contenir espaces et caracteres
    Unicode (accents, etc.)."""
    regional = regional_route(platform)
    return await _get(regional,
                       f"/riot/account/v1/accounts/by-riot-id/{quote(game_name, safe='')}/{quote(tag_line, safe='')}")


async def account_by_puuid(platform: str, puuid: str) -> Optional[dict]:
    regional = regional_route(platform)
    return await _get(regional, f"/riot/account/v1/accounts/by-puuid/{puuid}")


# ===== Summoner API (platform) =====
async def summoner_by_puuid(platform: str, puuid: str) -> Optional[dict]:
    """Renvoie {id (summonerId), accountId, puuid, profileIconId, summonerLevel}."""
    return await _get(platform.lower(),
                       f"/lol/summoner/v4/summoners/by-puuid/{puuid}")


# ===== League API (platform) =====
async def league_entries_by_summoner(platform: str, summoner_id: str) -> Optional[list[dict]]:
    """Renvoie liste de LeagueEntryDTO pour ce summoner :
    {queueType, tier, rank, leaguePoints, wins, losses, ...}
    queueType : 'RANKED_SOLO_5x5' | 'RANKED_FLEX_SR' | 'RANKED_FLEX_TT'..."""
    data = await _get(platform.lower(),
                       f"/lol/league/v4/entries/by-summoner/{summoner_id}")
    if isinstance(data, list):
        return data
    return None


async def league_entries_by_puuid(platform: str, puuid: str) -> Optional[list[dict]]:
    """Variante by-puuid (plus fiable, endpoint moderne)."""
    data = await _get(platform.lower(),
                       f"/lol/league/v4/entries/by-puuid/{puuid}")
    if isinstance(data, list):
        return data
    return None


# ===== Match API (regional) =====
async def match_ids_by_puuid(platform: str, puuid: str,
                              count: int = 10, queue: Optional[int] = None) -> Optional[list[str]]:
    """N derniers matchIds. queue : 420=SoloQ, 440=Flex, 450=ARAM, 400=Normals."""
    regional = regional_route(platform)
    qstr = f"&queue={int(queue)}" if queue else ""
    path = f"/lol/match/v5/matches/by-puuid/{puuid}/ids?count={int(count)}{qstr}"
    data = await _get(regional, path)
    if isinstance(data, list):
        return data
    return None


async def match_details(platform: str, match_id: str) -> Optional[dict]:
    """Detail complet d'un match : metadata + info (participants, teams, etc.)."""
    regional = regional_route(platform)
    return await _get(regional, f"/lol/match/v5/matches/{match_id}")


# ===== Spectator API (platform) =====
async def active_game_by_puuid(platform: str, puuid: str) -> Optional[dict]:
    """Renvoie le current game si user en partie, None si pas en jeu."""
    return await _get(platform.lower(),
                       f"/lol/spectator/v5/active-games/by-summoner/{puuid}")


# ===== Champion Mastery extra =====
async def mastery_all(platform: str, puuid: str) -> Optional[list[dict]]:
    """Toutes les maitrises (sorted par points desc cote API)."""
    data = await _get(platform.lower(),
                       f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}")
    if isinstance(data, list):
        return data
    return None


async def mastery_by_champion(platform: str, puuid: str, champion_id: int) -> Optional[dict]:
    """Maitrise sur un champion specifique."""
    return await _get(platform.lower(),
                       f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/by-champion/{int(champion_id)}")


# Mapping queue id -> label affichage (pour history / live)
QUEUE_LABELS = {
    400: "Normal Draft",
    420: "Solo/Duo",
    430: "Normal Blind",
    440: "Flex 5v5",
    450: "ARAM",
    700: "Clash",
    830: "Co-op vs AI Intro",
    840: "Co-op vs AI Beginner",
    850: "Co-op vs AI Intermediate",
    900: "URF",
    1700: "Arena",
    1900: "URF (revisited)",
}


def queue_label(queue_id: Optional[int]) -> str:
    return QUEUE_LABELS.get(int(queue_id or 0), f"Queue #{queue_id}")


# ===== Meraki Analytics : prix skins + metadonnees champion =====
_MERAKI_CACHE: dict = {}
_MERAKI_TTL_SEC = 21600  # 6h


async def meraki_champion(slug_or_name: str) -> Optional[dict]:
    """Renvoie le JSON champion Meraki Analytics (contient prix skins,
    historique, etc.). Cache 6h par slug."""
    slug = (slug_or_name or "").strip().lower().replace(" ", "").replace("'", "")
    if not slug:
        return None
    now = time.time()
    cached = _MERAKI_CACHE.get(slug)
    if cached and (now - cached["ts"]) < _MERAKI_TTL_SEC:
        return cached["data"]
    s = await _get_session()
    url = f"https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions/{slug}.json"
    try:
        async with s.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            _MERAKI_CACHE[slug] = {"data": data, "ts": now}
            return data
    except Exception as e:
        print(f"[riot/meraki] err slug={slug}: {type(e).__name__}: {e}")
        return None


# ===== OP.GG scrape (build suggestions) =====
async def opgg_build(slug: str, role: str) -> Optional[dict]:
    """Scrape OP.GG pour suggestions de build. Tente d'extraire __NEXT_DATA__ JSON.
    Retourne dict avec core_items, runes, summoner_spells, skill_order,
    winrate, pickrate, ou None si echec."""
    slug = (slug or "").strip().lower().replace(" ", "").replace("'", "")
    role = (role or "").strip().lower()
    if role not in ("top", "jungle", "mid", "adc", "support"):
        return None
    url = f"https://www.op.gg/lol/champions/{slug}/build/{role}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    s = await _get_session()
    try:
        async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200:
                print(f"[riot/opgg] status={resp.status} url={url}")
                return None
            html = await resp.text()
    except Exception as e:
        print(f"[riot/opgg] fetch err: {type(e).__name__}: {e}")
        return None

    # Cherche le bloc __NEXT_DATA__
    marker = '<script id="__NEXT_DATA__" type="application/json">'
    idx = html.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = html.find("</script>", start)
    if end == -1:
        return None
    raw = html[start:end].strip()
    try:
        import json as _j
        next_data = _j.loads(raw)
    except Exception as e:
        print(f"[riot/opgg] json parse err: {type(e).__name__}")
        return None

    # Naviguer dans props.pageProps pour trouver les builds. Structure variable,
    # on extrait au mieux. Renvoie un dict minimal + URL OP.GG en fallback.
    try:
        page_props = (next_data.get("props") or {}).get("pageProps") or {}
        data = page_props.get("data") or page_props
        # Garde l'URL pour fallback
        return {
            "url": url,
            "raw_keys": list(data.keys()) if isinstance(data, dict) else None,
            "data": data,
        }
    except Exception:
        return {"url": url, "data": None}


# ===== Champion Mastery API (platform) =====
async def mastery_top(platform: str, puuid: str, count: int = 3) -> Optional[list[dict]]:
    """Top N masteries du joueur.
    Renvoie liste : {championId, championLevel, championPoints, ...}"""
    data = await _get(platform.lower(),
                       f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count={int(count)}")
    if isinstance(data, list):
        return data
    return None


# ===== Data Dragon : champion id -> name (statique, cache 24h) =====
_DD_CACHE = {"version": None, "champions": {}, "fetched": 0.0}
_DD_TTL_SEC = 86400


async def _dd_refresh():
    now = time.time()
    if _DD_CACHE["champions"] and (now - _DD_CACHE["fetched"]) < _DD_TTL_SEC:
        return
    s = await _get_session()
    try:
        async with s.get("https://ddragon.leagueoflegends.com/api/versions.json") as resp:
            if resp.status != 200:
                return
            versions = await resp.json(content_type=None)
            if not versions:
                return
            ver = versions[0]
        async with s.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json") as resp:
            if resp.status != 200:
                return
            data = await resp.json(content_type=None)
        champs = {}
        for slug, info in (data.get("data") or {}).items():
            champs[int(info["key"])] = {"name": info["name"], "slug": slug}
        _DD_CACHE["version"] = ver
        _DD_CACHE["champions"] = champs
        _DD_CACHE["fetched"] = now
        print(f"[riot/dd] cache loaded version={ver} champions={len(champs)}")
    except Exception as e:
        print(f"[riot/dd] refresh err: {type(e).__name__}: {e}")


async def champion_name(champion_id: int) -> str:
    """Resoud championId -> nom affichage. Fallback : 'Champ #<id>'."""
    await _dd_refresh()
    c = _DD_CACHE["champions"].get(int(champion_id))
    if c:
        return c["name"]
    return f"Champ #{champion_id}"


async def champion_icon_url(champion_id: int) -> Optional[str]:
    await _dd_refresh()
    ver = _DD_CACHE.get("version")
    c = _DD_CACHE["champions"].get(int(champion_id))
    if not c or not ver:
        return None
    return f"https://ddragon.leagueoflegends.com/cdn/{ver}/img/champion/{c['slug']}.png"


def rank_label_fr(tier: str, rank: str) -> str:
    """'GOLD' + 'IV' -> 'Gold IV'."""
    t = (tier or "UNRANKED").title()
    r = (rank or "").upper()
    if t == "Unranked":
        return "Unranked"
    if t in ("Master", "Grandmaster", "Challenger"):
        return t
    return f"{t} {r}".strip()
