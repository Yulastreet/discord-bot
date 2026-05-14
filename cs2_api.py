"""HTTP clients pour CS2 : Steam Web API, Steam Market, Steam Inventory,
Faceit Data API, et taux de change USD->EUR.

Toutes les clés sont lues depuis l'environnement à chaque appel (pas de
capture à l'import) pour eviter les soucis si dotenv charge tard.

Aucune cle ne fuite dans les logs : les exceptions et messages d'erreur
n'incluent jamais la cle.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Optional
from urllib.parse import quote_plus

import aiohttp


_USER_AGENT = "TookBot-CS2/1.0 (+https://tookbot.click)"
_SESSION: Optional[aiohttp.ClientSession] = None
_LOCK = asyncio.Lock()

# Cache taux de change (refresh 1h)
_RATE_CACHE = {"rate": 0.92, "fetched": 0.0}


# --------------------------------------------------------------------------
# Session HTTP partagee
# --------------------------------------------------------------------------

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


def _steam_key() -> Optional[str]:
    return (os.getenv("STEAM_API_KEY") or "").strip() or None


def _faceit_key() -> Optional[str]:
    return (os.getenv("FACEIT_API_KEY") or "").strip() or None


# --------------------------------------------------------------------------
# Premier rank tiers (self-declared via /cs setrank)
# --------------------------------------------------------------------------

PREMIER_TIERS = [
    (0,      4999,   "grey",       "⚪ Grey",       0x808080),
    (5000,   9999,   "lightblue",  "🩵 Light Blue", 0x5DADE2),
    (10000,  14999,  "blue",       "🔷 Blue",       0x2980B9),
    (15000,  19999,  "purple",     "🟣 Purple",     0x8E44AD),
    (20000,  24999,  "pink",       "💖 Pink",       0xE91E63),
    (25000,  29999,  "red",        "🔴 Red",        0xC0392B),
    (30000,  999999, "gold",       "🟡 Gold (Top 1%)", 0xF1C40F),
]


def premier_tier(elo: int) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Retourne (code, label, color) pour un elo Premier. None si elo invalide."""
    if elo is None or elo < 0:
        return None, None, None
    for lo, hi, code, label, color in PREMIER_TIERS:
        if lo <= elo <= hi:
            return code, label, color
    return None, None, None


# --------------------------------------------------------------------------
# Steam : resolution d'ID + summary
# --------------------------------------------------------------------------

STEAMID64_RE = re.compile(r"^7656119[0-9]{10}$")
# Tolere n'importe quel path apres /profiles/<id> ou /id/<vanity> (ex: /inventory/, /home, etc.)
STEAM_URL_RE = re.compile(r"^https?://(?:www\.)?steamcommunity\.com/(?:profiles/(\d+)|id/([\w.\-]+))(?:/.*)?$")
VANITY_RE    = re.compile(r"^[\w.\-]{2,32}$")


async def steam_resolve(input_str: str) -> Optional[str]:
    """Resolve toute entree utilisateur en SteamID64 (17 chiffres).
    Accepte : SteamID64, URL profils/<id>, URL id/<vanity>, vanity seul.
    None si invalide ou vanity introuvable.
    """
    s = (input_str or "").strip()
    if not s:
        return None
    if STEAMID64_RE.match(s):
        return s
    m = STEAM_URL_RE.match(s)
    if m:
        if m.group(1):
            return m.group(1)
        return await _steam_resolve_vanity(m.group(2))
    if VANITY_RE.match(s):
        return await _steam_resolve_vanity(s)
    return None


async def _steam_resolve_vanity(vanity: str) -> Optional[str]:
    key = _steam_key()
    if not key:
        print("[cs2/steam] vanity resolve : STEAM_API_KEY absente — verifie .env + pm2 --update-env")
        return None
    url = ("https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
           f"?key={key}&vanityurl={quote_plus(vanity)}")
    s = await _get_session()
    try:
        async with s.get(url) as resp:
            body = await resp.text()
            if resp.status != 200:
                print(f"[cs2/steam] vanity resolve status={resp.status} vanity={vanity!r}")
                return None
            import json as _json
            data = _json.loads(body)
            r = data.get("response", {})
            success = r.get("success")
            if success == 1:
                return r.get("steamid")
            # 42 = no match
            print(f"[cs2/steam] vanity resolve vanity={vanity!r} success={success} msg={r.get('message')!r}")
    except Exception as e:
        print(f"[cs2/steam] vanity resolve err: {type(e).__name__}: {e}")
    return None


async def steam_player_summary(steam_id: str) -> Optional[dict]:
    """Renvoie info publique (persona, avatar, country, visibility)."""
    key = _steam_key()
    if not key:
        return None
    url = ("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
           f"?key={key}&steamids={steam_id}")
    s = await _get_session()
    try:
        async with s.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            players = data.get("response", {}).get("players", [])
            return players[0] if players else None
    except Exception as e:
        print(f"[cs2/steam] summary err: {type(e).__name__}")
    return None


async def steam_cs2_stats(steam_id: str) -> Optional[dict]:
    """Stats CS2/CSGO. Retourne dict {stat_name: value} ou {'_private': True}
    si profil/jeu privé, None si erreur."""
    key = _steam_key()
    if not key:
        return None
    url = ("https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v2/"
           f"?key={key}&steamid={steam_id}&appid=730")
    s = await _get_session()
    try:
        async with s.get(url) as resp:
            if resp.status in (401, 403):
                return {"_private": True}
            if resp.status == 400:
                # Souvent : profil prive ou steamid invalide
                return {"_private": True}
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            stats_raw = data.get("playerstats", {}).get("stats", []) or []
            return {item["name"]: item["value"] for item in stats_raw}
    except Exception as e:
        print(f"[cs2/steam] stats err: {type(e).__name__}")
    return None


async def steam_owned_cs2(steam_id: str) -> Optional[dict]:
    """Renvoie le dict du jeu CS2 (730) parmi owned games (heures jouees, etc)."""
    key = _steam_key()
    if not key:
        return None
    url = ("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
           f"?key={key}&steamid={steam_id}&include_appinfo=1&appids_filter[0]=730")
    s = await _get_session()
    try:
        async with s.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            games = data.get("response", {}).get("games", []) or []
            for g in games:
                if g.get("appid") == 730:
                    return g
    except Exception as e:
        print(f"[cs2/steam] owned err: {type(e).__name__}")
    return None


async def steam_inventory(steam_id: str) -> Optional[list[dict]]:
    """Inventaire CS2 public. Retourne :
      - None  : prive OU erreur (cf logs)
      - []    : public mais vide
      - list  : items
    Utilise une session aiohttp DEDIEE (cookies vides) pour eviter que
    Steam blacklist notre fingerprint apres une serie de 400.
    """
    # count=75 est la valeur par defaut utilisee par steamcommunity.com lui-meme ;
    # demander count=5000 fait que Steam repond 400+null en blocage anti-scrape.
    url = f"https://steamcommunity.com/inventory/{steam_id}/730/2?l=french&count=75"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }
    timeout = aiohttp.ClientTimeout(total=20, connect=10)
    # Session fresh par appel : pas de cookies persistents qui poisonnent
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, cookie_jar=aiohttp.DummyCookieJar()) as s:

        body        = None
        last_status = None
        last_body   = ""
        try:
            async with s.get(url) as resp:
                last_status = resp.status
                last_body   = await resp.text()
                if resp.status == 200 and last_body.strip() not in ("", "null"):
                    body = last_body
                else:
                    print(f"[cs2/steam] inv try {url} -> status={resp.status} body={last_body[:120]!r}")
        except Exception as e:
            print(f"[cs2/steam] inv fetch exception: {type(e).__name__}: {e}")

        if body is None:
            if last_status == 400 and last_body.strip() in ("", "null"):
                print(f"[cs2/steam] inv private (400 null) steam_id={steam_id}")
            elif last_status in (401, 403):
                print(f"[cs2/steam] inv private steam_id={steam_id} status={last_status}")
            elif last_status == 429:
                print(f"[cs2/steam] inv rate-limited steam_id={steam_id}")
            else:
                print(f"[cs2/steam] inv failed steam_id={steam_id} last_status={last_status}")
            return None

    import json as _json
    try:
        data = _json.loads(body)
    except Exception as e:
        print(f"[cs2/steam] inv json parse err steam_id={steam_id}: {type(e).__name__}")
        return None
    if not data:
        return None
    if data.get("error"):
        print(f"[cs2/steam] inv error msg steam_id={steam_id} error={data.get('error')!r}")
        return None
    assets    = data.get("assets") or []
    desc_list = data.get("descriptions") or []
    if not assets and not desc_list:
        return []
    desc = {f"{d['classid']}_{d['instanceid']}": d for d in desc_list}
    items = []
    for a in assets:
        key = f"{a['classid']}_{a['instanceid']}"
        d = desc.get(key, {})
        items.append({
            "name": d.get("market_hash_name") or d.get("name") or "?",
            "icon": d.get("icon_url"),
            "tradable":   bool(d.get("tradable", 0)),
            "marketable": bool(d.get("marketable", 0)),
            "rarity": next((t.get("name") for t in d.get("tags", []) if t.get("category") == "Rarity"), None),
            "type":   next((t.get("name") for t in d.get("tags", []) if t.get("category") == "Type"), None),
        })
    print(f"[cs2/steam] inv ok steam_id={steam_id} assets={len(assets)} items={len(items)}")
    return items


# --------------------------------------------------------------------------
# Steam Market : prix
# --------------------------------------------------------------------------

async def steam_market_search(query: str, count: int = 30, appid: int = 730) -> Optional[list[str]]:
    """Recherche live sur Steam Market. Retourne liste de market_hash_name
    correspondant a la query (ex: 'AK-47 Redline' -> tous les AK-47 Redline)."""
    q = (query or "").strip()
    if not q:
        return None
    url = ("https://steamcommunity.com/market/search/render/"
           f"?query={quote_plus(q)}&appid={appid}&norender=1&count={count}&start=0")
    s = await _get_session()
    try:
        async with s.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            if not data.get("success"):
                return None
            results = data.get("results") or []
            return [r.get("hash_name") for r in results if r.get("hash_name")]
    except Exception as e:
        print(f"[cs2/market] search err: {type(e).__name__}")
    return None


async def steam_market_price(market_hash_name: str, currency: int = 3) -> Optional[dict]:
    """Prix Steam Market. currency 3 = EUR. None si non trouvé."""
    name = (market_hash_name or "").strip()
    if not name:
        return None
    url = ("https://steamcommunity.com/market/priceoverview/"
           f"?appid=730&currency={currency}&market_hash_name={quote_plus(name)}")
    s = await _get_session()
    try:
        async with s.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            if not data.get("success"):
                return None
            return {
                "lowest_price": data.get("lowest_price"),
                "median_price": data.get("median_price"),
                "volume":       data.get("volume"),
            }
    except Exception as e:
        print(f"[cs2/market] price err: {type(e).__name__}")
    return None


# --------------------------------------------------------------------------
# Skinport : alternative marketplace gratuite, pas d'auth, prix EUR directs.
# Endpoint bulk : retourne TOUS les items en un seul appel (~5-8 MB).
# On cache localement pour eviter de spammer.
# --------------------------------------------------------------------------

_SKINPORT_CACHE = {"items": {}, "fetched_at": 0.0}
_SKINPORT_LOCK = asyncio.Lock()


async def _skinport_refresh_if_needed():
    """Recharge le JSON aggrégé csgotrader.app (contient les prix Skinport,
    Steam, Buff, CSMoney...). On utilise Skinport pour le 'starting_at'.
    Cache 1h. csgotrader sert un CDN static donc pas de Cloudflare bot-check."""
    now = time.time()
    if _SKINPORT_CACHE["items"] and (now - _SKINPORT_CACHE["fetched_at"] < 3600):
        return
    async with _SKINPORT_LOCK:
        if _SKINPORT_CACHE["items"] and (now - _SKINPORT_CACHE["fetched_at"] < 3600):
            return
        url = "https://prices.csgotrader.app/latest/prices_v6.json"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        print(f"[cs2/skinport] fetching bulk from csgotrader.app...")
        s = await _get_session()
        try:
            async with s.get(url, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[cs2/skinport] bulk status={resp.status} body={body[:200]!r}")
                    return
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    print(f"[cs2/skinport] unexpected payload type={type(data).__name__}")
                    return
                _SKINPORT_CACHE["items"]      = data
                _SKINPORT_CACHE["fetched_at"] = now
                print(f"[cs2/skinport] cache loaded items={len(data)} (csgotrader aggregated)")
        except Exception as e:
            print(f"[cs2/skinport] bulk err: {type(e).__name__}: {e}")


# Taux EUR -> USD pour les sources qui publient en USD (la plupart sauf Skinport)
async def skinport_lowest_price(market_hash_name: str) -> Optional[dict]:
    """Cherche le prix Skinport via le bundle csgotrader. Retourne {price_eur,
    suggested_price, quantity}. Le JSON contient un sous-dict 'skinport' par
    skin avec 'starting_at' (USD) et 'suggested_price' (USD). On convertit en EUR."""
    name = (market_hash_name or "").strip()
    if not name:
        return None
    await _skinport_refresh_if_needed()
    items = _SKINPORT_CACHE["items"]
    if not items:
        print(f"[cs2/skinport] lookup name={name!r} cache_empty=True")
        return None
    item = items.get(name)
    if not item:
        sample = [k for k in items if name.lower()[:15] in k.lower()][:3]
        print(f"[cs2/skinport] lookup name={name!r} not_found near={sample!r}")
        return None
    sp = item.get("skinport") or {}
    starting_usd = sp.get("starting_at")
    suggested_usd = sp.get("suggested_price")
    if not isinstance(starting_usd, (int, float)) or starting_usd <= 0:
        return None
    rate = await usd_to_eur_rate()
    return {
        "price_eur":       float(starting_usd) * rate,
        "suggested_price": (float(suggested_usd) * rate) if suggested_usd else None,
        "quantity":        None,
    }


async def csfloat_lowest_price(market_hash_name: str) -> Optional[dict]:
    """Cherche le listing CSFloat le moins cher pour ce skin.
    Endpoint public : GET /api/v1/listings?type=buy_now&market_hash_name=...
    Prix en cents USD -> on convertit en EUR."""
    name = (market_hash_name or "").strip()
    if not name:
        return None
    url = ("https://csfloat.com/api/v1/listings"
           f"?type=buy_now&market_hash_name={quote_plus(name)}"
           "&sort_by=lowest_price&limit=1")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0",
        "Accept": "application/json",
    }
    s = await _get_session()
    try:
        async with s.get(url, headers=headers) as resp:
            body = await resp.text()
            if resp.status != 200:
                print(f"[cs2/csfloat] status={resp.status} name={name!r} body={body[:200]!r}")
                return None
            import json as _json
            try:
                data = _json.loads(body)
            except Exception:
                print(f"[cs2/csfloat] non-JSON body for {name!r}: {body[:200]!r}")
                return None
            # CSFloat renvoie soit {"data": [...], "cursor": ...} soit liste directe
            if isinstance(data, list):
                listings = data
            else:
                listings = (data or {}).get("data") or []
            if not listings:
                print(f"[cs2/csfloat] empty result name={name!r}")
                return None
            cents_usd = listings[0].get("price")
            if not isinstance(cents_usd, (int, float)) or cents_usd <= 0:
                print(f"[cs2/csfloat] no price field name={name!r} item_keys={list(listings[0].keys())}")
                return None
            usd = cents_usd / 100.0
            rate = await usd_to_eur_rate()
            return {
                "price_usd": usd,
                "price_eur": usd * rate,
                "listings_count": len(listings),
            }
    except Exception as e:
        print(f"[cs2/csfloat] price err name={name!r}: {type(e).__name__}: {e}")
    return None


def _parse_price_eur(price_str: Optional[str]) -> Optional[float]:
    """'1,23€' / '12,45€' / '1.234,56€' -> float."""
    if not price_str:
        return None
    s = price_str.replace("€", "").replace("\xa0", " ").strip()
    s = s.replace(" ", "")
    # Format europeen : 1.234,56 -> 1234.56
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Faceit
# --------------------------------------------------------------------------

async def faceit_player_by_nickname(nickname: str) -> Optional[dict]:
    key = _faceit_key()
    if not key:
        return None
    nick = (nickname or "").strip()
    if not nick:
        return None
    url = f"https://open.faceit.com/data/v4/players?nickname={quote_plus(nick)}"
    headers = {"Authorization": f"Bearer {key}"}
    s = await _get_session()
    try:
        async with s.get(url, headers=headers) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except Exception as e:
        print(f"[cs2/faceit] lookup err: {type(e).__name__}")
    return None


async def faceit_player_stats(player_id: str, game: str = "cs2") -> Optional[dict]:
    key = _faceit_key()
    if not key:
        return None
    url = f"https://open.faceit.com/data/v4/players/{player_id}/stats/{game}"
    headers = {"Authorization": f"Bearer {key}"}
    s = await _get_session()
    try:
        async with s.get(url, headers=headers) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except Exception as e:
        print(f"[cs2/faceit] stats err: {type(e).__name__}")
    return None


# --------------------------------------------------------------------------
# Taux de change USD -> EUR
# --------------------------------------------------------------------------

async def usd_to_eur_rate() -> float:
    """Taux courant, cache 1h, fallback 0.92."""
    now = time.time()
    if now - _RATE_CACHE["fetched"] < 3600 and _RATE_CACHE["rate"]:
        return _RATE_CACHE["rate"]
    s = await _get_session()
    try:
        async with s.get("https://api.frankfurter.app/latest?from=USD&to=EUR") as resp:
            if resp.status != 200:
                return _RATE_CACHE["rate"]
            data = await resp.json(content_type=None)
            rate = data.get("rates", {}).get("EUR")
            if rate:
                _RATE_CACHE["rate"]    = float(rate)
                _RATE_CACHE["fetched"] = now
                return _RATE_CACHE["rate"]
    except Exception as e:
        print(f"[cs2/fx] rate err: {type(e).__name__}")
    return _RATE_CACHE["rate"]
