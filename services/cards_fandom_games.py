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


# (subdomain fandom, nom franchise pour subtitle, categories prioritaires)
# Ordre = ordre d'import = rarete (premiers = mythic/legendary).
# Top franchises iconiques d'abord.
FRANCHISES: list[tuple[str, str, list[str]]] = [
    # === TIER S : les plus iconiques (rarete top) ===
    ("leagueoflegends",   "League of Legends",      ["Category:Champions"]),
    ("pokemon",           "Pokémon",                ["Category:Pokémon", "Category:Generation_I_Pokémon"]),
    ("mario",             "Mario",                  ["Category:Characters"]),
    ("zelda",             "The Legend of Zelda",    ["Category:Characters"]),
    ("genshin-impact",    "Genshin Impact",         ["Category:Playable_Characters", "Category:Characters"]),
    ("honkai-star-rail",  "Honkai: Star Rail",      ["Category:Playable_Characters", "Category:Characters"]),
    ("smashbros",         "Super Smash Bros.",      ["Category:Playable_characters", "Category:Fighters"]),
    ("sonic",             "Sonic the Hedgehog",     ["Category:Characters"]),
    ("finalfantasy",      "Final Fantasy",          ["Category:Playable_characters", "Category:Characters"]),
    ("kingdomhearts",     "Kingdom Hearts",         ["Category:Characters"]),
    ("overwatch",         "Overwatch",              ["Category:Heroes", "Category:Characters"]),
    ("valorant",          "Valorant",               ["Category:Agents", "Category:Characters"]),
    ("apexlegends",       "Apex Legends",           ["Category:Legends", "Category:Characters"]),
    ("dota2",             "Dota 2",                 ["Category:Heroes"]),
    ("fortnite",          "Fortnite",               ["Category:Outfits", "Category:Characters"]),
    # === TIER A : franchises majeures ===
    ("halo",              "Halo",                   ["Category:Characters"]),
    ("godofwar",          "God of War",             ["Category:Characters"]),
    ("witcher",           "The Witcher",            ["Category:Characters"]),
    ("cyberpunk",         "Cyberpunk 2077",         ["Category:Characters"]),
    ("eldenring",         "Elden Ring",             ["Category:Characters_(Elden_Ring)", "Category:Characters"]),
    ("darksouls",         "Dark Souls",             ["Category:Characters"]),
    ("bloodborne",        "Bloodborne",             ["Category:Characters"]),
    ("sekiro",            "Sekiro",                 ["Category:Characters"]),
    ("residentevil",      "Resident Evil",          ["Category:Characters"]),
    ("metalgear",         "Metal Gear",             ["Category:Characters"]),
    ("devilmaycry",       "Devil May Cry",          ["Category:Characters"]),
    ("streetfighter",     "Street Fighter",         ["Category:Playable_Characters", "Category:Characters"]),
    ("tekken",            "Tekken",                 ["Category:Characters"]),
    ("mortalkombat",      "Mortal Kombat",          ["Category:Characters"]),
    ("smt",               "Shin Megami Tensei",     ["Category:Characters"]),
    ("personaseries",     "Persona",                ["Category:Characters"]),
    # === TIER B : populaires ===
    ("masseffect",        "Mass Effect",            ["Category:Characters"]),
    ("dragonage",         "Dragon Age",             ["Category:Characters"]),
    ("baldursgate",       "Baldur's Gate",          ["Category:Characters"]),
    ("skyrim",            "The Elder Scrolls",      ["Category:Characters"]),
    ("fallout",           "Fallout",                ["Category:Characters"]),
    ("borderlands",       "Borderlands",            ["Category:Characters"]),
    ("gta",               "Grand Theft Auto",       ["Category:Characters"]),
    ("reddead",           "Red Dead Redemption",    ["Category:Characters"]),
    ("uncharted",         "Uncharted",              ["Category:Characters"]),
    ("thelastofus",       "The Last of Us",         ["Category:Characters"]),
    ("horizon",           "Horizon Zero Dawn",      ["Category:Characters"]),
    ("ghost-of-tsushima", "Ghost of Tsushima",      ["Category:Characters"]),
    ("monsterhunter",     "Monster Hunter",         ["Category:Hunters", "Category:Characters"]),
    ("destinypedia",      "Destiny",                ["Category:Characters"]),
    ("gearsofwar",        "Gears of War",           ["Category:Characters"]),
    ("metroid",           "Metroid",                ["Category:Characters"]),
    ("kirby",             "Kirby",                  ["Category:Characters"]),
    ("starfox",           "Star Fox",               ["Category:Characters"]),
    ("fireemblem",        "Fire Emblem",            ["Category:Characters"]),
    ("xenoblade",         "Xenoblade",              ["Category:Characters"]),
    ("dragonquest",       "Dragon Quest",           ["Category:Characters"]),
    ("nier",              "Nier",                   ["Category:Characters"]),
    ("tales",             "Tales of",               ["Category:Characters"]),
    # === TIER C : niche ===
    ("guiltygear",        "Guilty Gear",            ["Category:Characters"]),
    ("kof",               "King of Fighters",       ["Category:Characters"]),
    ("soulcalibur",       "Soul Calibur",           ["Category:Characters"]),
    ("silenthill",        "Silent Hill",            ["Category:Characters"]),
    ("warframe",          "Warframe",               ["Category:Characters", "Category:Warframes"]),
    ("arknights",         "Arknights",              ["Category:Operators"]),
    ("azurlane",          "Azur Lane",              ["Category:Ships"]),
    ("fategrandorder",    "Fate/Grand Order",       ["Category:Servants"]),
    ("touhou",            "Touhou Project",         ["Category:Characters"]),
    ("undertale",         "Undertale",              ["Category:Characters"]),
    ("deltarune",         "Deltarune",              ["Category:Characters"]),
    ("hollowknight",      "Hollow Knight",          ["Category:Characters"]),
    ("cuphead",           "Cuphead",                ["Category:Characters", "Category:Bosses"]),
    ("celestegame",       "Celeste",                ["Category:Characters"]),
    ("ori",               "Ori and the Blind Forest", ["Category:Characters"]),
    ("bioshock",          "BioShock",               ["Category:Characters"]),
    ("portal",            "Portal",                 ["Category:Characters"]),
    ("halflife",          "Half-Life",              ["Category:Characters"]),
    ("teamfortress",      "Team Fortress 2",        ["Category:Classes", "Category:Characters"]),
    ("counterstrike",     "Counter-Strike",         ["Category:Characters"]),
    ("minecraft",         "Minecraft",              ["Category:Mobs", "Category:Characters"]),
    ("terraria",          "Terraria",               ["Category:NPCs", "Category:Bosses"]),
    ("amongus",           "Among Us",               ["Category:Characters"]),
    ("fnaf",              "Five Nights at Freddy's", ["Category:Animatronics", "Category:Characters"]),
    ("crash-bandicoot",   "Crash Bandicoot",        ["Category:Characters"]),
    ("spyro",             "Spyro the Dragon",       ["Category:Characters"]),
    ("ratchet",           "Ratchet & Clank",        ["Category:Characters"]),
    ("jak",               "Jak and Daxter",         ["Category:Characters"]),
    ("disgaea",           "Disgaea",                ["Category:Characters"]),
    ("yu-gi-oh",          "Yu-Gi-Oh!",              ["Category:Characters"]),
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


def _list_chars_in_wiki(sub: str, categories: list[str],
                          limit: int = 200) -> list[str]:
    """Liste page titles dans categories du wiki sub. Try chacune jusqu'a hit."""
    titles: list[str] = []
    # Toujours fallback Characters/Playable_characters apres categories override
    cats = list(categories) + ["Category:Characters",
                                  "Category:Playable_characters",
                                  "Category:Major_characters"]
    seen = set()
    for cat in cats:
        if cat in seen: continue
        seen.add(cat)
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
        if len(titles) >= limit:
            break
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

    for sub, franchise_name, categories in FRANCHISES:
        try:
            titles = _list_chars_in_wiki(sub, categories, limit=per_franchise)
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
