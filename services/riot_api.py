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
