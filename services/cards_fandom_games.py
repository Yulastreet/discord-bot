"""Bulk import personnages jeux video via Fandom wikis (MediaWiki API).

Volume cible : ~3-5k chars depuis franchises populaires.

Pour chaque franchise :
1. GET https://{sub}.fandom.com/api.php?action=query&list=categorymembers&
   cmtitle=Category:Characters&cmlimit=500 -> liste de page titles
2. Pour batch de titles (max 50/batch MediaWiki limit) :
   GET ?action=query&prop=pageimages&pithumbsize=600&titles=Title1|Title2|...
3. Extract thumbnail.source pour chaque page

Pas de rate limit officiel mais courtoisie : sleep 0.5s entre franchises.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"


# (subdomain fandom, nom franchise pour subtitle, rarity_bonus)
# rarity_bonus = boost rang pour franchises iconiques (top 10 chars
# de Mario/Zelda forcement mythic/legendary)
FRANCHISES: list[tuple[str, str, int]] = [
    ("mario",             "Mario",                  0),
    ("zelda",             "The Legend of Zelda",    0),
    ("sonic",             "Sonic the Hedgehog",     0),
    ("pokemon",           "Pokémon",                0),
    ("metroid",           "Metroid",                0),
    ("kirby",             "Kirby",                  0),
    ("starfox",           "Star Fox",               0),
    ("smashbros",         "Super Smash Bros.",      0),
    ("fireemblem",        "Fire Emblem",            0),
    ("xenoblade",         "Xenoblade",              0),
    ("finalfantasy",      "Final Fantasy",          0),
    ("kingdomhearts",     "Kingdom Hearts",         0),
    ("dragonquest",       "Dragon Quest",           0),
    ("personaseries",     "Persona",                0),
    ("smt",               "Shin Megami Tensei",     0),
    ("tales",             "Tales of",               0),
    ("nier",              "Nier",                   0),
    ("residentevil",      "Resident Evil",          0),
    ("devilmaycry",       "Devil May Cry",          0),
    ("metalgear",         "Metal Gear",             0),
    ("silenthill",        "Silent Hill",            0),
    ("monsterhunter",     "Monster Hunter",         0),
    ("streetfighter",     "Street Fighter",         0),
    ("tekken",            "Tekken",                 0),
    ("mortalkombat",      "Mortal Kombat",          0),
    ("guiltygear",        "Guilty Gear",            0),
    ("kof",               "King of Fighters",       0),
    ("soulcalibur",       "Soul Calibur",           0),
    ("godofwar",          "God of War",             0),
    ("uncharted",         "Uncharted",              0),
    ("thelastofus",       "The Last of Us",         0),
    ("horizon",           "Horizon Zero Dawn",      0),
    ("ghost-of-tsushima", "Ghost of Tsushima",      0),
    ("bloodborne",        "Bloodborne",             0),
    ("darksouls",         "Dark Souls",             0),
    ("eldenring",         "Elden Ring",             0),
    ("sekiro",            "Sekiro",                 0),
    ("witcher",           "The Witcher",            0),
    ("cyberpunk",         "Cyberpunk 2077",         0),
    ("masseffect",        "Mass Effect",            0),
    ("dragonage",         "Dragon Age",             0),
    ("baldursgate",       "Baldur's Gate",          0),
    ("skyrim",            "The Elder Scrolls",      0),
    ("fallout",           "Fallout",                0),
    ("borderlands",       "Borderlands",            0),
    ("halo",              "Halo",                   0),
    ("gearsofwar",        "Gears of War",           0),
    ("destinypedia",      "Destiny",                0),
    ("overwatch",         "Overwatch",              0),
    ("leagueoflegends",   "League of Legends",      0),
    ("dota2",             "Dota 2",                 0),
    ("valorant",          "Valorant",               0),
    ("apexlegends",       "Apex Legends",           0),
    ("fortnite",          "Fortnite",               0),
    ("warframe",          "Warframe",               0),
    ("genshin-impact",    "Genshin Impact",         0),
    ("honkai-star-rail",  "Honkai: Star Rail",      0),
    ("arknights",         "Arknights",              0),
    ("azurlane",          "Azur Lane",              0),
    ("fategrandorder",    "Fate/Grand Order",       0),
    ("fate",              "Fate series",            0),
    ("touhou",            "Touhou Project",         0),
    ("undertale",         "Undertale",              0),
    ("deltarune",         "Deltarune",              0),
    ("hollowknight",      "Hollow Knight",          0),
    ("cuphead",           "Cuphead",                0),
    ("celestegame",       "Celeste",                0),
    ("ori",               "Ori and the Blind Forest", 0),
    ("gta",               "Grand Theft Auto",       0),
    ("reddead",           "Red Dead Redemption",    0),
    ("bioshock",          "BioShock",               0),
    ("portal",            "Portal",                 0),
    ("halflife",          "Half-Life",              0),
    ("teamfortress",      "Team Fortress 2",        0),
    ("counterstrike",     "Counter-Strike",         0),
    ("minecraft",         "Minecraft",              0),
    ("terraria",          "Terraria",               0),
    ("amongus",           "Among Us",               0),
    ("fnaf",              "Five Nights at Freddy's", 0),
    ("crash-bandicoot",   "Crash Bandicoot",        0),
    ("spyro",             "Spyro the Dragon",       0),
    ("ratchet",           "Ratchet & Clank",        0),
    ("jak",               "Jak and Daxter",         0),
    ("kingdomhearts",     "Kingdom Hearts",         0),
    ("disgaea",           "Disgaea",                0),
    ("yu-gi-oh",          "Yu-Gi-Oh!",              0),
]


def _http_get_json(url: str, timeout: int = 12) -> dict | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept":     "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[fandom] HTTP {e.code} {url[:100]}")
        return None
    except Exception as e:
        print(f"[fandom] err {url[:100]}: {e}")
        return None


def _list_chars_in_wiki(sub: str, limit: int = 200) -> list[str]:
    """Liste page titles dans Category:Characters du wiki sub."""
    # Try Category:Characters d'abord, fallback Category:Playable_characters
    titles: list[str] = []
    for cat in ("Category:Characters", "Category:Playable_characters",
                  "Category:Major_characters"):
        url = (f"https://{sub}.fandom.com/api.php?action=query&format=json&"
               f"list=categorymembers&cmtitle={urllib.parse.quote(cat)}"
               f"&cmlimit={min(limit, 500)}&cmtype=page")
        data = _http_get_json(url)
        if not data:
            continue
        members = ((data.get("query") or {}).get("categorymembers") or [])
        for m in members:
            t = m.get("title")
            if t and t not in titles:
                titles.append(t)
        if titles:
            break  # cat trouvee, stop
    return titles[:limit]


def _fetch_pageimages_batch(sub: str, titles: list[str]) -> dict[str, str]:
    """Batch query pageimages pour list of titles (max 50/batch MW)."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        titles_param = "|".join(urllib.parse.quote(t) for t in batch)
        url = (f"https://{sub}.fandom.com/api.php?action=query&format=json&"
               f"prop=pageimages&pithumbsize=600&pilimit=50&"
               f"titles={titles_param}")
        data = _http_get_json(url)
        if not data:
            continue
        pages = ((data.get("query") or {}).get("pages") or {})
        for _pid, page in pages.items():
            t = page.get("title")
            thumb = (page.get("thumbnail") or {}).get("source")
            if t and thumb:
                out[t] = thumb
    return out


def _rarity_weighted(rank: int) -> str:
    if rank <= 30:    return "mythic"
    if rank <= 120:   return "legendary"
    if rank <= 350:   return "epic"
    if rank <= 800:   return "rare"
    return "common"


def bulk_import_fandom_games(per_franchise: int = 200,
                                total_target: int | None = None,
                                sleep_between: float = 0.5,
                                skip_existing: bool = True,
                                wipe_first: bool = False) -> dict:
    """Iter chaque franchise, list chars, fetch pageimages, insert.
    total_target : si set, stop des qu'on atteint ce nb d'insertions.
    per_franchise : cap max par wiki (eviter qu'un seul wiki domine)."""
    from database import get_db, card_add

    conn = get_db(); c = conn.cursor()
    if wipe_first:
        c.execute("DELETE FROM user_cards")
        c.execute("DELETE FROM cards")
        conn.commit()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0,
              "franchises_done": 0, "franchises_empty": 0}
    rank = 0

    for sub, franchise_name, _bonus in FRANCHISES:
        try:
            titles = _list_chars_in_wiki(sub, limit=per_franchise)
            if not titles:
                print(f"[fandom] {sub} : aucun char trouve")
                stats["franchises_empty"] += 1
                time.sleep(sleep_between)
                continue
            img_map = _fetch_pageimages_batch(sub, titles)
            inserted_this = 0
            for title in titles:
                stats["total_seen"] += 1
                rank += 1
                name = title.strip()
                if not name:
                    stats["failed"] += 1; continue
                # Skip pages bizarres (List of, Category, etc)
                if name.lower().startswith(("list of", "category:", "template:",
                                              "file:", "user:")):
                    stats["failed"] += 1; continue
                if skip_existing and name.lower() in existing:
                    stats["skipped"] += 1; continue
                img = img_map.get(title)
                if not img:
                    stats["failed"] += 1; continue
                rarity = _rarity_weighted(rank)
                desc = f"Personnage de {franchise_name}."
                try:
                    card_add(name=name, universe="Jeu Vidéo",
                              subtitle=franchise_name[:80],
                              rarity=rarity, image_url=img,
                              description=desc)
                    existing.add(name.lower())
                    stats["inserted"] += 1
                    inserted_this += 1
                except Exception as e:
                    print(f"[fandom] insert err {name}: {e}")
                    stats["failed"] += 1
            stats["franchises_done"] += 1
            print(f"[fandom] {sub} : +{inserted_this} chars (sur {len(titles)} titles)")
            time.sleep(sleep_between)
            if total_target and stats["inserted"] >= total_target:
                print(f"[fandom] total_target {total_target} atteint, stop")
                break
        except Exception as e:
            print(f"[fandom] franchise {sub} fatal: {e}")
            stats["franchises_empty"] += 1
            time.sleep(sleep_between)

    print(f"[fandom] final stats={stats}")
    return stats
