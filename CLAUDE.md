# TookBot — Récap session (à jour)

> Fichier mémoire à recoller dans une nouvelle conversation pour que Claude retrouve tout le contexte du projet.

---

## 1. Profil utilisateur

- **Dev** : Axhel (axhel.desousa@gmail.com), francophone
- **Vient de** : Mammouth IA → migration vers Claude Code
- **Bot** : TookBot, ID `1499831600496640070`
- **Repo** : `Yulastreet/discord-bot`, branche `main`
- **Caveman mode** : actif (full) — réponses télégraphiques, code/commits normaux

---

## 2. Infrastructure

- **VPS** : Oracle Cloud, IP `141.253.114.142`, user `ubuntu`, Ubuntu 22.04, 956 Mo RAM + 2 Go swap
- **Domaine** : `tookbot.click` (registrar OVH)
  - `tookbot.click` + `www.tookbot.click` → landing nginx, `/var/www/tookbot.click/`
  - `dashboard.tookbot.click` → Flask, port 5000 interne, reverse proxy nginx
- **HTTPS** : Let's Encrypt + certbot auto-renew
- **Process manager** : `pm2` en user `ubuntu` (⚠️ JAMAIS sudo)
  - Process 1 : `discord-bot` (bot.py)
  - Process 2 : `web-dashboard` (web.py)

### Deploy quick
```bash
cd ~/discord-bot && git pull && pm2 restart discord-bot web-dashboard
# Pour la landing apex :
~/deploy-landing.sh   # rsync vers /var/www/tookbot.click/
```

---

## 3. Stack

### Bot
- Python 3.10
- discord.py 2.3.2 / 2.7.x
- yt-dlp + ffmpeg + libopus + PyNaCl
- Deno (résolveur JS challenge YouTube)
- bgutil-ytdlp-pot-provider (PO Token, container Docker port 4416)
- pilmoji 2.0.4 (rendu emoji couleur sur Pillow)
- Pillow 10.3.0

### Web
- Flask + Jinja2 + SQLite
- Auth Discord OAuth (scopes `identify` + `guilds`)
- Rôles : owner via `DISCORD_OWNER_ID`, mods via intersection `manage_guild`/`administrator`/`kick_members` ∩ guildes du bot
- 3 niveaux d'accès :
  - **owner** : tout
  - **mod** : pages "Ce serveur" + Modération
  - **utilisateur** (logged-in sans mod) : juste "Mon compte" (Premium / Mon Pass)

### Architecture bot↔web
- Queue de commandes en DB : table `bot_commands`
- Bot poll toutes les 1.5 s via `tasks.loop` (`process_bot_commands`)
- État bot écrit dans JSON via `_write_bot_state()`

---

## 4. Variables d'env (.env)

```
DISCORD_TOKEN=...
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
OAUTH_REDIRECT_URI=https://dashboard.tookbot.click/oauth/callback
DISCORD_OWNER_ID=222737978932461578     # Axhel
WEB_PASSWORD=...                         # fallback dev
FLASK_SECRET=...
SESSION_LIFETIME_HOURS=24
HTTPS_ENABLED=1
FLASK_DEBUG=0

# Monétisation Discord SKU
SKU_NIVEAU_PREMIUM=1502733026508144773   # achat unique 1.99 USD
SKU_PASS=1502794833322836029             # subscription 3.99 EUR/mois

# yt-dlp
BGUTIL_POT_URL=http://127.0.0.1:4416     # défaut, override possible
YT_USE_FIREFOX_COOKIES=1                 # cookies-from-browser firefox
TOPGG_TOKEN=...
```

---

## 5. Fichiers clés

| Fichier | Rôle |
|---|---|
| `bot.py` | Bot Discord — events, commands, dispatcher bot_commands |
| `web.py` | Flask app, ~80+ routes, OAuth, role-based access |
| `database.py` | SQLite — tables + helpers (~2000 lignes) |
| `niveau_card.py` | Renderer Pillow carte /niveau premium + levelup |
| `duel_combat.py` | Stats, dégâts, effets |
| `duel_commands.py` | Commandes duel + boutique sabres + collection |
| `duel_sabres.py` | Sabres + raretés C/UC/R/SR/SSR |
| `templates/_base.html` | Layout dashboard + sidebar conditionnelle |
| `templates/_legal_base.html` | Layout pages légales |
| `landing/index.html` | Landing page (servie par nginx) |
| `assets/niveau_bg/` | 15 BGs permanents + `seasonal/<YYYY-MM>/` |
| `assets/niveau_bg/owner/<uid>.png` | BG custom owner uploadé |
| `static/favicon.{ico,png}` | Favicon dashboard |
| `landing/favicon.{ico,png}` | Favicon landing |

### Scripts utiles
- `scripts/generate_niveau_backgrounds.py` — 15 BGs permanents
- `scripts/generate_seasonal_backgrounds.py [YYYY-MM]` — 5 BGs excentriques saisonniers
- `scripts/generate_sku_banner.py` — banner /niveau Premium SKU
- `scripts/generate_sku_pass_banner.py` — banner Battle Pass SKU
- `scripts/generate_favicon.py` — favicon hexagone lime "T"

---

## 6. Schéma DB (28+ tables)

### Core
- `users` (guild_id, user_id) — XP per-guild
- `reactions` — auto-réactions sur messages user (PAS rôle-réaction)
- `welcome` — salon de bienvenue
- `guilds`, `guild_channels`, `guild_members`, `guild_roles` — caches
- `settings` — config dynamique
- `bot_commands` — queue web→bot
- `logs` — events log
- `dm_messages` — DMs entre user et bot

### Duels
- `duel_profil` (user_id) — stats globales (cross-guild)
- `duel_collection` — sabres possédés
- `duel_historique` — duels passés
- `sabres` — definition de chaque sabre (incl. saisonniers `season_<YYYY-MM>_<R|SR|SSR>`)

### Monétisation
- `entitlements` — achats Discord (auto-sync via on_entitlement_*)
- `premium_grants` (user_id, feature) — grants manuels owner
  - `feature='all'` = pack `/niveau` Premium
  - `feature='pass'` = Battle Pass (STRICT, pas inherit_all)
- `premium_settings` — préférences user (niveau_background, pass_selected_title, pass_selected_emoji)

### Battle Pass
- `pass_seasons` (month_key 'YYYY-MM') — 1 saison = 1 mois
- `pass_rewards` (season_id, tier) — 30 paliers + payload JSON
- `pass_quest_templates` — 17 templates daily/weekly
- `pass_user_quests` (user, period, slot, period_start) — quêtes actives
- `pass_progress` (user, season) — XP saison + claimed_max_tier
- `pass_unlocks` — items débloqués (BG/sabre/title/emoji/boost_xp), avec expires_at

### Reaction Roles (NEW)
- `reaction_roles` (guild, message, emoji) — mapping → role_id + mode

### Toutes les tables sont scopées par guild SAUF duels + entitlements + premium_grants/settings + pass_* (globaux).

---

## 7. Bot dispatcher (`_dispatch_bot_command`)

Commandes web→bot supportées :
- `dm_send`
- `music_play`, `music_skip`, `music_stop`, `music_pause`, `music_resume`, `music_join`, `music_leave`, `music_remove_track`, `music_clear`
- `bot_say` (poste embed/message dans salon)
- `mod_kick`, `mod_ban`, `mod_timeout`, `mod_unban`
- **`rolereaction_post`** (NEW) — poste un message rôle-réaction multi-mapping

Worker : `process_bot_commands` toutes 1.5s. Status `pending` / `done` / `error` (avec result string ≤ 300 chars).

---

## 8. Tasks loops actives (bot.py)

| Loop | Intervalle |
|---|---|
| `process_bot_commands` | 1.5 s |
| `reload_reactions` | 5 s |
| `status_writer` | 15 s |
| `rotate_presence` | 30 s |
| `anti_spam_cleanup` | 10 min |
| `pass_rotation_loop` | 6 h (BG saisonniers) |
| `daily_logs_purge` | 24 h |

---

## 9. Features livrées (chronologique)

### Bot Discord
- Music YouTube via yt-dlp + bgutil PO Token + Firefox cookies (xvfb headless)
- Duel/sabres avec C/UC/R/SR/SSR + boutique TookCoins
- XP per-guild + carte levelup Pillow
- Mod (kick/ban/timeout/clear)
- Fun (8ball, blague, ship, etc.)

### Monétisation
- **`/niveau` Premium** (1.99 USD durable) :
  - Carte image HD 1024×320 avec avatar rond glow + barre XP + badge ⭐
  - 15 backgrounds permanents + 5 saisonniers (rotatifs mensuels via Pass)
  - BG custom owner uploadé (visible toi seul)
  - Cosmétiques Pass (titre + emoji prefix) appliqués si user a Pass
- **Battle Pass** (3.99 EUR/mois subscription) :
  - 30 paliers / saison mensuelle
  - 17 templates quêtes daily/weekly auto-générées (send_messages, play_duels, earn_xp, use_commands)
  - Récompenses : 9 boost_xp + 7 emojis + 6 titres + 5 BG + 3 sabres cosmétiques
  - **Anti-P2W** : sabres saisonniers stats égales aux f2p de même rareté + locked tant que user n'a pas un sabre classique de la même rareté
  - Auto-claim paliers à chaque XP credit
  - Owner ENV = pass auto + premium auto
  - Grants manuels via dashboard
- Discord SKU listeners on_entitlement_create/update/delete sync auto en DB

### Dashboard
- OAuth Discord + 3 niveaux accès
- Pages : Dashboard, Search, Reactions auto, **Rôles-Réaction (NEW)**, Music, Logs, Modération, Duels, DMs, Status, Settings, BotTalk
- Owner : Vue globale, **Recherche globale (cross-server)**, **Paramètres owner (custom BG, sabres saisonniers, console live pm2)**
- Mon compte : **Premium**, **Mon Pass** (page complète avec roadmap 30 paliers + sélecteur cosmétiques)
- Profil user : panel admin Premium / Pass / XP edit (owner ou guild-admin)
- Public : `/api/public-stats` (CORS, cache 1h, alimente landing)

### Reaction Roles (MEE6-style)
- DB `reaction_roles` (guild, msg, emoji) → role_id + mode (toggle/add_only/unique)
- Listeners `on_raw_reaction_add` + `on_raw_reaction_remove`
- Slash `/rolereaction` group :
  - `create` : **builder interactif** (ChannelSelect + Modal title/desc + Modal emoji + RoleSelect, ajout multi-mapping)
  - `add` : ajouter mapping à message existant
  - `remove [emoji]` : retirer un ou tous
  - `list` : liste avec deep-links
- Page web `/reactionroles` : builder + liste avec retrait par mapping
- Dispatcher `rolereaction_post` (queue web→bot)
- Anti-erreurs polling : alerte explicative si rôle au-dessus du bot

### Landing tookbot.click
- Refonte par user (HTML perso, manga ink theme, GSAP, Inter font)
- Stats live (`statGuilds` / `statUsers`) via `/api/public-stats`
- Favicon hexagone lime "T"

---

## 10. Bugs résolus (mémoire des pièges)

| Symptôme | Cause | Fix |
|---|---|---|
| `/statpoint` n'apparaît pas | emoji + double espace dans `app_commands.Choice` casse `tree.sync()` | retirer emojis, single space |
| Port 5000 already in use | Flask `debug=True` + pm2 fork = double process | `debug=False`, `use_reloader=False` |
| libopus `'davey library needed'` | pm2 lancé en sudo ≠ env ubuntu | pm2 sans sudo |
| YouTube `Sign in to confirm you're not a bot` | bot detection sur IP datacenter | Firefox VNC + cookies-from-browser + bgutil PO Token |
| Macro Jinja `heatmap` shadowée | collision import vs variable | alias `heatmap_chart` |
| OAuth redirect_uri non valide | `.env` ≠ Dev Portal | aligner les deux |
| 403 sur `tookbot.click` | `/var/www/tookbot.click/` vide | `git pull` + `cp` côté serveur |
| Initiative dés cosmétique | `ordre` non utilisé phases 1/2 | `ordre[0]`/`ordre[1]` |
| **Owner /premium loop infini** | `_current_user_id()` lisait `session.get("user")` au lieu de `session["discord"]["user_id"]` | fix helper |
| **Régulars ne peuvent pas login** | OAuth callback HTTP 403 si pas de guild commune | autoriser tous logged-in users |
| **Sidebar montrait "MODÉRATEUR" pour user lambda** | label hardcodé | label conditionnel Owner/Mod/Utilisateur |
| **Sabres saisonniers crash /sabre** | rareté `'rare'`/`'epique'`/`'legendaire'` invalide | utiliser C/UC/R/SR/SSR + migration cleanup |
| **Sabres saisonniers P2W** | effets différents des f2p de même rareté | mêmes effets (overcharge / reflect_100 / ultimate) |
| **Sabres saisonniers équipables sans avoir le f2p** | pas de check possession | `_can_equip_seasonal` gate |
| **/niveau lent** | aiohttp session par appel + Pillow event loop | sync session + asyncio.to_thread + cache BG mtime-aware |
| **Custom BG owner update pas visible bot** | _BG_CACHE séparé entre processus pm2 | mtime-aware cache (reload si fichier plus récent) |
| **Emojis Pass affichés en carrés** | Pillow ne rend pas les emojis couleur | pilmoji + emoji_scale=0.75 |
| **/my-pass infini "Chargement..."** | escape JS cassé `\\'` au lieu de `\'` | switch `"…l'XP…"` double-quoted |
| **TookCoins en récompenses Pass** | P2W (TookCoins achètent sabres duels) | retiré, remplacé par cosmétiques only |
| **Pass auto-grant pour holders 'all'** | `has_premium_grant` fallbackait sur 'all' | `inherit_all=False` pour Pass strict |
| **Reaction `Unknown Emoji 10014` web flow** | strip de U+200D ZWJ cassait emojis composés (🧗‍♂️) | garder ZWJ, virer seulement ZWSP/ZWNJ/WJ/BOM |
| **Reactions non ajoutées web flow** | `_try_add` retournait l'exception au lieu de la lever | raise après variants épuisées |

---

## 11. Mécaniques duel (état actuel)

- Stats : `combat_level`, sabre rareté, force/agilité/défense/endurance/chance
  - hp_max = 250 + lvl×10 + endurance×25
  - attaque = 15 + lvl×2 + bonus×5 + force×5
  - defense = 5 + lvl + bonus + def_stat×3
  - esquive = min(agilité×0.04, 0.40)
  - crit = min(chance×0.05, 0.50)
- Effets : `absorb_next`, `lifesteal_50/75`, `rage_next`, `double_attaque`, `overcharge`, `paralyze`, `reflect_100`, `ultimate`, `buff_degats`
- Parade decay + vs special
- `NextTurnView` : bouton "Tour suivant ⚔️"

---

## 12. Battle Pass — config (constants)

- `PASS_TIERS = 30`
- `PASS_XP_PER_TIER = 250`
- `PASS_XP_TOTAL = 7500`
- Quêtes daily : 50 XP × 3 = 150/jour
- Quêtes weekly : 250 XP × 3 = 750/semaine

### Tier reward map (30 paliers, anti-P2W, cosmétiques only)
- T1 : boost_xp 30min ×2
- T2 : title "Initié"
- T3 : boost_xp 1h ×2
- **T4 : BG saisonnier #1**
- T5 : emoji 🌱
- T6 : title "Adepte"
- T7 : boost_xp 1h ×2
- T8 : emoji 🔥
- **T9 : BG saisonnier #2**
- **T10 : sabre R cosmétique** (overcharge, comme Cyan)
- T11 : emoji ⚡
- T12 : boost_xp 2h ×2
- T13 : title "Vétéran"
- T14 : boost_xp 2h ×2
- **T15 : BG saisonnier #3**
- T16 : emoji 💎
- T17 : title "Élu"
- T18 : boost_xp 2h ×2
- T19 : boost_xp 2h ×2
- **T20 : sabre SR cosmétique** (reflect_100, comme Argent)
- T21 : emoji 🌊
- T22 : boost_xp 3h ×2
- **T23 : BG saisonnier #4**
- T24 : emoji 🎯
- T25 : title "Maître"
- T26 : boost_xp 3h ×2
- T27 : title "Légende"
- T28 : emoji 🌟
- **T29 : BG saisonnier #5**
- **T30 : sabre SSR cosmétique** (ultimate, comme Arc-en-Ciel)

### BG saisonniers
Stockés dans `assets/niveau_bg/seasonal/<YYYY-MM>/<name>.png` :
- crystal_cave, liquid_chrome, neon_tokyo, stained_glass, cosmic_vortex
- Expirent fin du **mois suivant** le déblocage (~30j garantis)
- Auto-générés par `pass_rotation_loop` (toutes 6h, pre-genere J+25 du mois)

---

## 13. Discord & SKUs

### Bot
- ID : `1499831600496640070`
- App ID : idem

### SKUs
- **`/niveau Premium`** ID `1502733026508144773` — Durable 1.99 USD
- **`Battle Pass`** ID `1502794833322836029` — Subscription 3.99 EUR/mois

### Owner Discord
- ID : `222737978932461578`
- Auto-premium + auto-pass via DISCORD_OWNER_ID env

### Server support
- Invite : `https://discord.gg/pM43xjuRAb`

---

## 14. Conventions / règles projet

- pm2 toujours en `ubuntu`, jamais sudo
- DB unique `bot_database.db`, scope par guild sauf duels/monétisation/pass
- Slash commands : pas d'emoji ni double espace dans `Choice.name`
- Flask : `debug=False` en prod, secret fixe
- nginx : apex statique, sous-domaine reverse proxy
- Caveman mode actif (full) sauf code/commits/PRs
- Tous les commits avec `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` retirés (user pref)
- ⚠️ **Bash agent saute parfois dans `.claude/worktrees/keen-benz-bba36e/`** au lieu de la racine repo. Toujours `cd /c/Users/axhel/Desktop/discord-bot &&` avant les git commit / push si bizarre.
- ⚠️ **Emoji parsing** : ne JAMAIS strip U+200D ZWJ (essentiel pour emojis composés). Strip seulement U+200B/U+200C/U+2060/U+FEFF. Toujours faire NFC normalize avant.

---

## 15. Endpoints owner-only majeurs

| Endpoint | Action |
|---|---|
| `/owner/settings` | page (custom BG upload, sabres saisonniers list, console live pm2) |
| `/api/owner/niveau-bg` POST/DELETE | upload/remove custom BG |
| `/api/owner/seasonal-sabres` GET | tous sabres `season_*` |
| `/api/owner/logs?proc=bot|web&stream=out|err&offset=N` | tail pm2 |
| `/search-global` + `/api/search-global` | recherche cross-serveur |
| `/api/user/<id>/premium` GET/POST/DELETE | grant/revoke /niveau Premium |
| `/api/user/<id>/pass` GET/POST/DELETE/PATCH | grant/revoke + edit XP saison |
| `/api/user/<id>/pass/quests` GET/DELETE | re-roll quêtes |
| `/api/user/<id>/xp` POST | edit XP (owner OU admin guild) |

---

## 16. À retenir si bug futur sur emoji/reactions

1. **Toujours logger les codepoints** avant `add_reaction` :
   ```python
   cps = " ".join(f"U+{ord(c):04X}" for c in ek)
   print(f"emoji={ek!r} codepoints=[{cps}]")
   ```
2. **NFC normalize** d'abord (macOS envoie NFD)
3. **NE PAS strip U+200D** (ZWJ, structurel pour 🧗‍♂️ etc.)
4. **VS-16 (U+FE0F)** : essayer avec et sans en variants
5. **Custom emojis** d'autres serveurs : bot peut les rendre dans embed mais pas réagir avec
6. `_try_add` doit RAISE en fin si tout échoue, pas RETURN
