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

# curl_cffi : optional dep, mimic TLS Chrome pour bypass Cloudflare
# Si pas installe, on tombe sur aiohttp (souvent bloque par Cloudflare).
try:
    from curl_cffi import requests as _curl_requests  # type: ignore
    _HAS_CURL_CFFI = True
except Exception as _e:
    _curl_requests = None
    _HAS_CURL_CFFI = False
    print(f"[riot/init] curl_cffi import FAILED: {type(_e).__name__}: {_e}")

print(f"[riot/init] curl_cffi available: {_HAS_CURL_CFFI}")


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


async def _fetch_html_cffi(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch HTML via curl_cffi (impersonate Chrome TLS) pour bypasser
    Cloudflare/anti-bot des sites comme OP.GG / Mobalytics / U.GG.
    Renvoie None si echec ou lib non installee."""
    if not _HAS_CURL_CFFI or _curl_requests is None:
        return None
    # curl_cffi est sync, on l'execute dans un thread pour ne pas bloquer
    # l'event loop.
    try:
        return await asyncio.to_thread(
            lambda: _curl_requests.get(
                url,
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            ).text
        )
    except Exception as e:
        print(f"[riot/cffi] err {url}: {type(e).__name__}: {e}")
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


# ===== Data Dragon : items cache (id -> name) =====
_DD_ITEMS_CACHE = {"version": None, "items": {}, "fetched": 0.0}


async def _dd_items_refresh():
    now = time.time()
    if _DD_ITEMS_CACHE["items"] and (now - _DD_ITEMS_CACHE["fetched"]) < _DD_TTL_SEC:
        return
    await _dd_refresh()
    ver = _DD_CACHE.get("version")
    if not ver:
        return
    s = await _get_session()
    try:
        async with s.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/item.json") as resp:
            if resp.status != 200:
                return
            data = await resp.json(content_type=None)
        items = {}
        for iid, info in (data.get("data") or {}).items():
            items[int(iid)] = {"name": info.get("name"), "image": (info.get("image") or {}).get("full")}
        _DD_ITEMS_CACHE["version"] = ver
        _DD_ITEMS_CACHE["items"]   = items
        _DD_ITEMS_CACHE["fetched"] = now
        print(f"[riot/dd-items] cached items={len(items)} ver={ver}")
    except Exception as e:
        print(f"[riot/dd-items] err: {type(e).__name__}: {e}")


# ===== Runes Reforged cache (resolution id -> nom + icon) =====
_DD_RUNES_CACHE = {"version": None, "by_id": {}, "fetched": 0.0}


async def _dd_runes_refresh():
    now = time.time()
    if _DD_RUNES_CACHE["by_id"] and (now - _DD_RUNES_CACHE["fetched"]) < _DD_TTL_SEC:
        return
    await _dd_refresh()
    ver = _DD_CACHE.get("version")
    if not ver:
        return
    s = await _get_session()
    try:
        async with s.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/runesReforged.json") as resp:
            if resp.status != 200:
                return
            data = await resp.json(content_type=None)
        by_id = {}
        for tree in data or []:
            tid = tree.get("id")
            tname = tree.get("name")
            ticon = tree.get("icon")
            by_id[int(tid)] = {"name": tname, "icon": ticon, "is_tree": True}
            for slot in tree.get("slots") or []:
                for rune in slot.get("runes") or []:
                    rid = rune.get("id")
                    rname = rune.get("name")
                    ricon = rune.get("icon")
                    by_id[int(rid)] = {"name": rname, "icon": ricon, "tree_id": tid}
        _DD_RUNES_CACHE["version"] = ver
        _DD_RUNES_CACHE["by_id"]   = by_id
        _DD_RUNES_CACHE["fetched"] = now
        print(f"[riot/dd-runes] cached count={len(by_id)} ver={ver}")
    except Exception as e:
        print(f"[riot/dd-runes] err: {type(e).__name__}: {e}")


async def rune_name(rune_id: int) -> str:
    await _dd_runes_refresh()
    info = _DD_RUNES_CACHE["by_id"].get(int(rune_id))
    if info:
        return info["name"]
    return STAT_SHARDS.get(int(rune_id)) or f"Rune #{rune_id}"


async def rune_icon_url(rune_id: int) -> Optional[str]:
    await _dd_runes_refresh()
    info = _DD_RUNES_CACHE["by_id"].get(int(rune_id))
    if not info:
        return None
    icon = info.get("icon")
    if not icon:
        return None
    return f"https://ddragon.leagueoflegends.com/cdn/img/{icon}"


async def item_name(item_id: int) -> str:
    await _dd_items_refresh()
    info = _DD_ITEMS_CACHE["items"].get(int(item_id))
    if info:
        return info["name"]
    return f"Item #{item_id}"


# Summoner spells (small static map, ne change presque jamais)
SUMMONER_SPELL_NAMES = {
    1:  "Cleanse", 3: "Exhaust", 4: "Flash", 6: "Ghost",
    7:  "Heal",    11: "Smite",  12: "Teleport", 13: "Clarity",
    14: "Ignite",  21: "Barrier", 32: "Snowball",
}
# id Riot -> nom asset Data Dragon (pour icon)
SUMMONER_SPELL_ASSETS = {
    1:  "SummonerBoost",  3: "SummonerExhaust", 4: "SummonerFlash",  6: "SummonerHaste",
    7:  "SummonerHeal",  11: "SummonerSmite",  12: "SummonerTeleport", 13: "SummonerMana",
    14: "SummonerDot",   21: "SummonerBarrier", 32: "SummonerSnowball",
}


# ===== Composite image : icones items + sorts en une seule PNG =====
async def compose_build_image(item_ids: list[int], spell_ids: list[int]) -> Optional[bytes]:
    """Telecharge icones DataDragon, compose une image horizontale :
    Row 1 : items (64x64 chacun)
    Row 2 : summoner spells (48x48 chacun)
    Renvoie bytes PNG ou None si echec."""
    if not item_ids and not spell_ids:
        return None
    await _dd_refresh()
    ver = _DD_CACHE.get("version")
    if not ver:
        return None
    try:
        from PIL import Image
        import io
    except Exception:
        return None

    s = await _get_session()

    async def _fetch(url):
        try:
            async with s.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
        except Exception:
            return None

    # Telecharge tous les assets en parallele
    item_tasks = [_fetch(f"https://ddragon.leagueoflegends.com/cdn/{ver}/img/item/{i}.png") for i in item_ids]
    spell_tasks = []
    for sid in spell_ids:
        asset = SUMMONER_SPELL_ASSETS.get(int(sid))
        if asset:
            spell_tasks.append(_fetch(f"https://ddragon.leagueoflegends.com/cdn/{ver}/img/spell/{asset}.png"))
        else:
            spell_tasks.append(asyncio.sleep(0, result=None))

    item_bufs = await asyncio.gather(*item_tasks) if item_tasks else []
    spell_bufs = await asyncio.gather(*spell_tasks) if spell_tasks else []

    ITEM_SIZE = 64
    SPELL_SIZE = 48
    PAD = 8
    item_count = len(item_bufs)
    spell_count = len(spell_bufs)
    row1_w = item_count * ITEM_SIZE + max(0, item_count - 1) * PAD if item_count else 0
    row2_w = spell_count * SPELL_SIZE + max(0, spell_count - 1) * PAD if spell_count else 0
    canvas_w = max(row1_w, row2_w) + 2 * PAD
    row1_h = ITEM_SIZE if item_count else 0
    row2_h = SPELL_SIZE if spell_count else 0
    canvas_h = row1_h + (PAD if item_count and spell_count else 0) + row2_h + 2 * PAD
    if canvas_w <= 0 or canvas_h <= 0:
        return None

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    y = PAD
    if item_count:
        x = PAD + max(0, (canvas_w - 2 * PAD - row1_w) // 2)
        for buf in item_bufs:
            if buf:
                try:
                    icon = Image.open(io.BytesIO(buf)).convert("RGBA").resize(
                        (ITEM_SIZE, ITEM_SIZE), Image.LANCZOS)
                    canvas.paste(icon, (x, y), icon)
                except Exception:
                    pass
            x += ITEM_SIZE + PAD
        y += ITEM_SIZE + PAD
    if spell_count:
        x = PAD + max(0, (canvas_w - 2 * PAD - row2_w) // 2)
        for buf in spell_bufs:
            if buf:
                try:
                    icon = Image.open(io.BytesIO(buf)).convert("RGBA").resize(
                        (SPELL_SIZE, SPELL_SIZE), Image.LANCZOS)
                    canvas.paste(icon, (x, y), icon)
                except Exception:
                    pass
            x += SPELL_SIZE + PAD

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


# ===== Data Dragon recommended builds (officiel Riot, toujours dispo) =====
_DD_CHAMP_DETAIL_CACHE: dict = {}


async def ddragon_recommended(slug: str) -> Optional[list[dict]]:
    """Renvoie les builds 'recommended' officiels Riot pour ce champion.
    Format : liste de {name, items_by_phase, source_url, summoner_spells=[],
    keystone_id=None, primary_rune_tree=None, secondary_rune_tree=None,
    skill_order=''}. Toujours dispo (CDN public, pas d'anti-bot)."""
    await _dd_refresh()
    ver = _DD_CACHE.get("version")
    if not ver:
        return None
    # Le slug pour DD est le 'id' (PascalCase), pas le 'slug' lowercase
    # On a stocke 'slug' (PascalCase original) dans _DD_CACHE deja
    asset_id = None
    for cid, info in _DD_CACHE["champions"].items():
        if info["slug"].lower() == slug.lower() or info["name"].lower() == slug.lower():
            asset_id = info["slug"]
            break
    if not asset_id:
        return None

    cache_key = f"{ver}:{asset_id}"
    if cache_key in _DD_CHAMP_DETAIL_CACHE:
        rec = _DD_CHAMP_DETAIL_CACHE[cache_key]
    else:
        s = await _get_session()
        url = f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion/{asset_id}.json"
        try:
            async with s.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as e:
            print(f"[riot/dd-champ] err {asset_id}: {type(e).__name__}: {e}")
            return None
        champ_data = (data.get("data") or {}).get(asset_id) or {}
        rec = champ_data.get("recommended") or []
        _DD_CHAMP_DETAIL_CACHE[cache_key] = rec

    if not rec:
        return None

    # Filtre les builds Summoner's Rift (map=any ou map=SR) et mode classique
    builds_out = []
    for r in rec:
        rmap = (r.get("map") or "").lower()
        rmode = (r.get("mode") or "").lower()
        if rmap not in ("any", "sr", "summonersrift", "summoner's rift"):
            continue
        if rmode not in ("any", "classic"):
            continue
        title = r.get("title") or r.get("type") or "Standard"
        blocks = r.get("blocks") or []
        phases = []
        for b in blocks:
            btype = b.get("type") or "?"
            items = []
            for it in (b.get("items") or []):
                try:
                    items.append(int(it.get("id")))
                except (TypeError, ValueError):
                    continue
            if items:
                phases.append({"type": btype, "items": items})
        if phases:
            builds_out.append({
                "name":               title,
                "items_by_phase":     phases,
                "summoner_spells":    [],
                "keystone_id":        None,
                "primary_rune_tree":  None,
                "secondary_rune_tree": None,
                "skill_order":        "",
                "source_url":         f"https://www.leagueoflegends.com/en-us/champions/{asset_id.lower()}/",
            })
    return builds_out or None


# ===== Build scrapers (multi-source : Mobalytics, OP.GG, U.GG, DPM) =====
# Mobalytics : labels des types de build
MOBA_BUILD_LABELS = {
    "MOST_POPULAR":     "Most Popular",
    "HIGHEST_WIN_RATE": "Highest WR",
    "HIGH_WIN_RATE":    "High WR",
    "ALTERNATIVE":      "Alternative",
    "OFF_META":         "Off-Meta",
    "STANDARD":         "Standard",
    "PRO":              "Pro Build",
}

# Skill order : 1=Q, 2=W, 3=E, 4=R
SKILL_LETTERS = {1: "Q", 2: "W", 3: "E", 4: "R"}

# Stat shards (5001-5013). IDs ne changent presque jamais.
STAT_SHARDS = {
    5001: "+15-90 HP (au niv.)",
    5002: "+6 Armure",
    5003: "+8 RM",
    5005: "+10% Vitesse d'Attaque",
    5007: "+8 Hate",
    5008: "+9 Force Adaptative",
    5010: "+1% Vitesse",
    5011: "+65 HP",
    5013: "+10% Tenacite + Reduc. Ralenti.",
}


async def mobalytics_builds_all(slug: str, role: Optional[str] = None) -> Optional[list[dict]]:
    """Scrape Mobalytics, renvoie une LISTE de builds avec donnees completes.
    Chaque build : {name, type, items_by_phase, summoner_spells, perk_ids (9),
    primary_style, sub_style, skill_order (lettres), skill_max_order, wr,
    matches, source_url}."""
    slug_clean = (slug or "").strip().lower().replace(" ", "").replace("'", "").replace(".", "")
    if not slug_clean:
        return None
    url = f"https://mobalytics.gg/lol/champions/{slug_clean}/build"
    if role:
        url += f"?role={role.lower()}"

    # Tente d'abord curl_cffi (TLS Chrome -> passe Cloudflare). Fallback aiohttp.
    html = await _fetch_html_cffi(url)
    if not html or len(html) < 5000:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.5",
        }
        s = await _get_session()
        try:
            async with s.get(url, headers=headers, allow_redirects=True,
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    print(f"[riot/moba] aiohttp status={resp.status} url={url}")
                    return None
                html = await resp.text()
        except Exception as e:
            print(f"[riot/moba] aiohttp fetch err: {type(e).__name__}: {e}")
            return None
    if not html or len(html) < 5000:
        print(f"[riot/moba] empty html url={url} len={len(html or '')}")
        return None

    import re as _re

    # Mobalytics expose chaque build via cache key Apollo :
    # "LolChampionBuild:{\"id\":...,\"type\":...}":{...data...}
    # Le meme pattern apparait dans __ref. On cherche tout puis on filtre :
    # garde uniquement ceux suivis par ":{\"__typename\":\"LolChampionBuild\""
    raw_positions = []
    idx = 0
    needle = '"LolChampionBuild:{'
    while True:
        pos = html.find(needle, idx)
        if pos == -1:
            break
        raw_positions.append(pos)
        idx = pos + 1

    # Filtre : ne garde que les positions suivies d'un data block
    build_starts = []
    data_marker = '":{"__typename":"LolChampionBuild"'
    for pos in raw_positions:
        # Cherche la fin de la cle (jusqu'a 400 chars apres)
        slice_end = pos + 400
        if data_marker in html[pos:slice_end]:
            build_starts.append(pos)

    print(f"[riot/moba] raw markers={len(raw_positions)} data blocks={len(build_starts)}")

    if not build_starts:
        print(f"[riot/moba] no LolChampionBuild data block url={url}")
        return None

    build_starts.append(len(html))
    print(f"[riot/moba] LolChampionBuild markers={len(build_starts) - 1}")

    builds = []
    for i in range(len(build_starts) - 1):
        chunk = html[build_starts[i]:build_starts[i + 1]]

        # Type (label du build)
        t = _re.search(r'"type":"([A-Z_]+)"', chunk)
        btype = t.group(1) if t else f"BUILD_{i + 1}"

        # Items par phase
        phases = []
        for m in _re.finditer(r'"__typename":"LolChampionBuildItemsList","type":"([^"]+)","items":\[([^\]]*)\]', chunk):
            ptype = m.group(1)
            ids = [int(x) for x in _re.findall(r'\d+', m.group(2))]
            phases.append({"type": ptype, "items": ids})

        # Runes : perks.IDs (9 ids), perks.style, perks.subStyle
        perk_ids = []
        primary_style = None
        sub_style = None
        perk_block = _re.search(r'"perks":\{[^}]*"IDs":\[([^\]]+)\][^}]*"style":(\d+)[^}]*"subStyle":(\d+)', chunk)
        if perk_block:
            try:
                perk_ids = [int(x) for x in _re.findall(r'\d+', perk_block.group(1))]
                primary_style = int(perk_block.group(2))
                sub_style = int(perk_block.group(3))
            except Exception:
                pass

        # Summoner spells : "spells":[4,14]
        spells = []
        sp = _re.search(r'"spells":\[(\d+),(\d+)\]', chunk)
        if sp:
            spells = [int(sp.group(1)), int(sp.group(2))]

        # Skill order : "skillOrder":[3,1,2,2,...]
        skill_order = []
        so = _re.search(r'"skillOrder":\[([^\]]+)\]', chunk)
        if so:
            skill_order = [int(x) for x in _re.findall(r'\d+', so.group(1))]

        # Skill max order : "skillMaxOrder":[2,1,3]
        skill_max = []
        sm = _re.search(r'"skillMaxOrder":\[([^\]]+)\]', chunk)
        if sm:
            skill_max = [int(x) for x in _re.findall(r'\d+', sm.group(1))]

        # Stats : matchCount + wins
        wins = None
        matches = None
        wr = None
        ws = _re.search(r'"matchCount":(\d+),"wins":(\d+)', chunk)
        if ws:
            try:
                matches = int(ws.group(1))
                wins = int(ws.group(2))
                if matches > 0:
                    wr = wins / matches * 100.0
            except Exception:
                pass

        # Ne garde que si on a au moins items_by_phase
        if not phases:
            continue

        builds.append({
            "type":             btype,
            "name":             MOBA_BUILD_LABELS.get(btype, btype.replace("_", " ").title()),
            "items_by_phase":   phases,
            "summoner_spells":  spells,
            "perk_ids":         perk_ids,         # 9 ids : keystone + 3 primary + 2 secondary + 3 shards
            "primary_style":    primary_style,    # 8000..8400
            "sub_style":        sub_style,
            "keystone_id":      perk_ids[0] if perk_ids else None,
            "primary_rune_tree": primary_style,
            "secondary_rune_tree": sub_style,
            "skill_order":      skill_order,
            "skill_max_order":  skill_max,
            "wr":               wr,
            "matches":          matches,
            "source_url":       url,
        })

    # Dedup : meme type apparait parfois plusieurs fois, on garde 1er
    seen = set()
    unique = []
    for b in builds:
        if b["type"] in seen:
            continue
        seen.add(b["type"])
        unique.append(b)

    print(f"[riot/moba] builds parsed={len(unique)} types={[b['type'] for b in unique]}")
    return unique[:6] if unique else None


async def mobalytics_build(slug: str, role: Optional[str] = None) -> Optional[dict]:
    """Compat : renvoie le premier build seulement."""
    builds = await mobalytics_builds_all(slug, role)
    if not builds:
        return None
    return builds[0]


# Mapping rune tree ID -> nom (Riot Communities Dragon)
RUNE_TREES = {
    8000: "Precision",
    8100: "Domination",
    8200: "Sorcery",
    8300: "Inspiration",
    8400: "Resolve",
}

# Keystone runes principales (id -> name)
RUNE_KEYSTONES = {
    8005: "Press the Attack", 8008: "Lethal Tempo", 8021: "Fleet Footwork", 8010: "Conqueror",
    8112: "Electrocute", 8124: "Predator", 8128: "Dark Harvest", 9923: "Hail of Blades",
    8214: "Summon Aery", 8229: "Arcane Comet", 8230: "Phase Rush",
    8351: "Glacial Augment", 8360: "Unsealed Spellbook", 8369: "First Strike",
    8437: "Grasp of the Undying", 8439: "Aftershock", 8465: "Guardian",
}


async def opgg_build(slug: str, role: str) -> Optional[dict]:
    """Scrape OP.GG pour suggestions de build. Tente d'extraire __NEXT_DATA__ JSON.
    Retourne dict avec core_items, runes, summoner_spells, skill_order,
    winrate, pickrate, ou None si echec."""
    slug = (slug or "").strip().lower().replace(" ", "").replace("'", "")
    role = (role or "").strip().lower()
    if role not in ("top", "jungle", "mid", "adc", "support"):
        return None
    url = f"https://www.op.gg/lol/champions/{slug}/build/{role}"

    # Tente curl_cffi en priorite (passe Cloudflare). Fallback aiohttp.
    html = await _fetch_html_cffi(url)
    if not html or len(html) < 5000:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        s = await _get_session()
        try:
            async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    print(f"[riot/opgg] aiohttp status={resp.status} url={url}")
                    return None
                html = await resp.text()
        except Exception as e:
            print(f"[riot/opgg] aiohttp fetch err: {type(e).__name__}: {e}")
            return None
    if not html or len(html) < 5000:
        print(f"[riot/opgg] empty html url={url}")
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
