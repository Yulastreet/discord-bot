# TookBot — Handoff / Contexte complet

Colle ce fichier au début d'une nouvelle conversation pour donner tout le contexte.

---

## 0. Qui / Comment travailler (RÈGLES — priorité absolue)

- **Dev:** Axhel (axhel.desousa@gmail.com), dev français solo. Réponds-lui **en français**.
- **⚠️ Le PRODUIT est en anglais depuis le 2026-08-24** (bot, dashboard, landing). Tout nouveau texte visible par un utilisateur passe par i18n (voir §7). Ne réintroduis jamais de français dans le produit.
- **Mode /caveman TOUJOURS actif** (full): style télégraphique, pas de filler/politesses/hedging, fragments OK. Exception: code, commits, warnings sécurité, confirmations irréversibles, séquences multi-étapes = écrits normalement.
- **Pas d'emojis dans le code. Pas de em dashes. Pas de docs spontanées.**
- **Debug:** demander les logs `pm2` AVANT de spéculer sur un bug. Donner des commandes collables (paste-ready).

### Workflow Git (STRICT)
- **Commit + push depuis Windows uniquement.** Jamais depuis le VPS.
- **JAMAIS** de trailer `Co-Authored-By: Claude` ni aucun trailer Claude (historique déjà purgé une fois).
- **JAMAIS** push les branches `claude/*` (worktrees) ni `flyio-new-files`.
- **JAMAIS** push les chemins gitignore: `.deepsec/`, `.impeccable/`, `.claude/`, `.vscode/`, `github-profile/`, `assets/lol_emblems/`.
- Garder `landing/riot.txt` (tant que validation clé Riot pas finie).
- Push uniquement sur `origin main`.

### Après CHAQUE push → donner commande deploy VPS collable
Choisir les process pm2 selon ce qui a changé:
- Bot (`commandes/`, `services/card_boss.py`, côté bot `database.py`):
  `cd ~/discord-bot && git pull origin main && pm2 restart discord-bot`
- Dashboard (`templates/`, `web_app/`):
  `cd ~/discord-bot && git pull origin main && pm2 restart web-dashboard`
- Les deux touchés:
  `cd ~/discord-bot && git pull origin main && pm2 restart discord-bot web-dashboard`
- Landing (`landing/`, assets `static/`): pull seul, pas de restart (nginx sert le statique). Dire à Axhel **Ctrl+F5** pour vider le cache CSS.
- Sur VPS Python = `python3` (pas `python`).

---

## 1. Infra

TookBot = bot Discord + dashboard Flask + landing, sur VPS Oracle Cloud (Ubuntu, `/home/ubuntu/discord-bot`), géré par pm2.
- pm2: `discord-bot` (bot.py) + `web-dashboard` (web.py).
- nginx: `dashboard.tookbot.click` → Flask ; `tookbot.click` → statique `landing/`.
- `/static/` servi par le dashboard ; `/cards/img/` public (dans `PUBLIC_NO_AUTH_PREFIXES`).
- DB SQLite partagée `bot_database.db` (mode WAL).
- Web→bot: table `bot_commands` (`bot_command_enqueue`/`_fetch_pending`/`_finish`) + registre de hooks `services/bot_command_hooks.py` (register/get) consulté par `tasks/runtime.py` `_dispatch_bot_command`.

**Piège chemin VPS:** `__file__` de `commandes/*` et `database.py` résout un mauvais cwd en prod. Utiliser `services.card_render._ROOT` comme ancre.

**DB locale (Windows) PÉRIMÉE:** copie partielle, pas de table `borders`, pas de cartes secret/event/Wuthering-Waves. Tout script qui rend des cartes bordées doit tourner **sur le VPS**.

---

## 2. Features livrées (2026-06/07)

- **Card trade builder** (dashboard): `templates/cards_trade.html` + `web_app/routes/cards_trade.py`. Deux classeurs stylés comme la Collection, member picker (seulement joueurs possédant >=1 carte), "Envoyer" poste un embed via bot (hook `post_trade`). Vue publique `/cards/trade/<id>`, contre-offre précharge le builder via `?with=&guild=&from=`.
- **Système note raid A-F** (voir §3).
- **Promo codes**: types `roll`/`epic_roll`/`golden_roll` + génération par lot (page owner). `PROMO_REWARD_TYPES` dans database.py inclut ces types.
- **Cardshop**: achat via dropdown quantité (1-16) dans un seul ephemeral réutilisable (fini le spam).
- **Wishlist** affichée sur profil `/user/<id>` (owner-only, badge possédé/manquant).
- **Refonte landing** (lourde): dark-only (light + toggle retirés), nouvelle page `/cartes.html` (carrousel cartes random + mockup boss animé), section features homepage = "flux" éditorial (pas de boîtes, SVG custom, scroll-reveal) + spotlight Cards avec démo boss, fond = aurores boréales animées (rideaux verticaux vert+bleu, `sway`+`flick`) + starfield + parallax multi-couches. Assets: `static/topgg_banner.png`, `static/cards_showcase/`.
- **Prep top.gg** (voir §4).
- Preview owner cards affiche l'ID carte cliquable (chip copiable).

---

## 3. Système de note raid A-F (validé avant code)

Récompenses raid boss notées A-F par joueur, dans `services/card_boss.py` (`_grade_map`, `_grade_band`).

**Axes** (chacun = fraction ABSOLUE du joueur sur un pool scalé au boss):
- DPS = tes dégâts / dégâts totaux équipe.
- Tank = ton `taken_raw` (BRUT, avant réduction Gardien) / total équipe. Compte seulement si aptitude==gardien.
- Heal = heal donné / total HP réelles perdues équipe (`taken`), × `_HEAL_WEIGHT`=2.5 (sans poids, un healer plafonne à C).

`score = best_frac + 0.25*second_frac`. Bandes: A>=.35, B>=.25, C>=.15, D>=.08, E>=.03, sinon F.
Solo (n==1) → A auto. Mort (`died`) → plafonné à C.

**Tables récompenses (pilotées par grade):**
- Essence = `_ESS_BASE[tier]` × `_GRADE_ESS_MULT` (A1.5 B1.25 C1.0 D.85 E.65 F.4). Base = {1:800,2:1500,3:2500,4:3500,5:5000}.
- Rolls déterministes `_BOSS_ROLLS_BY_GRADE[tier][grade]` (fini le RNG). T5: A12 B9 C6 D4 E2 F1.
- Carte avatar: `_CARD_COPIES` A2 sinon1 F0. Avatar Mythic → Fragment Mythic toujours 1 (indép. du grade, Axhel a insisté). Avatar Secret → Golden Roll `_GOLDEN_BY_GRADE` A2 B1 C1 D1 E0 F0.

DB: `card_boss_participant` cols `taken_raw`+`died`. `_apply_dmg`/`_boss_hit` les remplissent.
Lettres de note affichées via assets bossweb + `_cemoji(bot,'boss'+grade.lower())`.

**Pourquoi:** récompenses non-random, au mérite, durables, A vraiment dur (~2× la part équitable), pas de A auto pour solo tank/healer.

---

## 4. Prep top.gg (HISTORIQUE — banni le 2026-08-24, ne plus travailler dessus)

**Guidelines qui comptent:** aucune commande ne doit exiger `administrator`; chaque commande demande juste la perm nécessaire; entrée d'aide claire (`/commandes`); prefix `/`; pas de NSFW.

**Corrigé:** retiré `administrator` de `/reaction_add`, `/reaction_remove`, `/setwelcome` (→ `manage_guild`). Docs alignées au vrai nombre de commandes (~134 → on annonce "130+"; retiré `/xp` fantôme; ajouté `/jump`, `/eventfight`, `/eventshop`, `/guild apply|applications`, `/presentation`, liste LoL complète).

**Description top.gg = HTML mais fortement sanitizé:** pas de `<style>`/`<script>`/anims CSS, `<details>` ne se replie PAS (affiche tout). Ce qui rend: `<div align="center">`, badges shields.io (utilisés comme boutons/titres lime, `labelColor=0d0f0a`, color `B9F23A`), `<table><tr><td>` pour images côte-à-côte (cellules `&nbsp;` pour espacer), `<img>` (proxifié — garder petit; webp animé <=~1.5MB). Bannière `static/topgg_banner.png`; 4 cartes showcase (voir §5).

**À FAIRE:**
- Lien invite en `permissions=8` (Administrator) sur la nav + `/invite` — **RED FLAG top.gg**. Remplacer par un masque de perms scopé.
- Ajouter social proof (compteurs serveurs/membres près du hero, témoignages, badge uptime).
- CTA mobile sticky "Ajouter à Discord".
- Notes reviewer: features premium → whitelist Verification Center server ID `333949691962195969` ou fournir clé premium redeem.

---

## 5. Générateur images showcase cartes

`scripts/gen_card_showcase.py` régénère les images dans `static/cards_showcase/` (servi, PAS commité).

**DOIT tourner sur le VPS** (`cd ~/discord-bot && python3 scripts/gen_card_showcase.py`), pas en local (DB Windows périmée, pas de borders/secret/event/WuWa). Utilise le vrai render `/show` (`services.card_render.compose_card_image` + `border_get()`), donc cartes bordées positionnées comme sur Discord.

Sorties:
- Rangée 4 cartes top.gg: `toji_alt.png` (42682 alt art), `goku_secret.webp` (38651 animé, ré-encodé <=1.5MB), `augusta_hell.png` (40078 + border hell), `byakuya_void.png` (22185 + border void). IDs hardcodés en haut du script.
- Carrousel `/cartes.html`: `show1..show24.png` (cartes random, ~40% avec border random).

`_shrink_animated()` redimensionne/ré-encode les cartes secret animées (proxy top.gg a rejeté un webp 5MB).

---

---

## 6. i18n / Anglais (depuis 2026-08-24)

Après un 4e refus top.gg suivi d'un **ban**, le produit est passé du français à l'anglais avec une infra i18n
prête pour d'autres langues. **Anglais uniquement** pour l'instant.

**Système:** `services/i18n.py`
- Catalogues = `locales/<lang>/<namespace>.json`. Le nom du fichier est le préfixe de clé:
  `locales/en/moderation.json` `{"kick": {"forbidden": "..."}}` -> clé `moderation.kick.forbidden`.
- `t(key, locale, **params)` — params en `str.format` (`{member}`). Clé absente = log `[i18n] MISSING` + retourne la clé (jamais de crash).
- `ti(interaction, key, **params)` — raccourci côté bot.
- Résolution de langue: réglage serveur `locale` > langue du client Discord > `en`.
- `guild_locale(guild_id)` pour les tâches de fond / events sans interaction.
- Flask: `t()` est un global Jinja déclaré dans `web.py`.

**Outils (à lancer avant tout déploiement):**
```
python scripts/i18n_check.py [chemins]   # cles manquantes + restes de francais
python scripts/i18n_smoke.py             # compile, imports, resolution des cles, templates, commandes
```

**Règles pour tout nouveau texte:**
- Texte utilisateur -> `ti(interaction, "ns.key")` (bot) ou `{{ t('ns.key') }}` (Jinja). Jamais en dur.
- Noms/descriptions/**paramètres** de commandes slash -> anglais en dur (Discord les enregistre au démarrage; les noms de paramètres sont visibles).
- Logs `print()` -> anglais en clair, sans `t()`.
- **Ne jamais toucher:** `custom_id` des vues persistantes (casse les boutons des messages déjà postés), clés DB/settings,
  codes d'erreur machine lus par le JS du dashboard (`owner_only`, `not_logged_in`...), chemins de routes API, valeurs d'API externes (rangs Riot...).
- Une valeur de catalogue contenant du HTML doit être rendue avec `| safe`.

**Commandes renommées:** `/commandes`->`/commands`, `/niveau`->`/level`, `/blague`->`/joke`, `/choix`->`/choice`,
`/qui`->`/who`, `/citation`->`/quote`, `/dé`->`/dice`, `/zgeg`->`/pp`, `/profil`->`/profile`, `/sabre`->`/saber`, `/historique`->`/history`.

⚠️ **Piège:** `services/feature_guard.py` `COMMAND_FEATURE_MAP` compare ses clés à `interaction.data["name"]`.
Renommer une commande sans mettre à jour cette table lui fait **contourner silencieusement son toggle de feature**.

⚠️ Le sync global du tree Discord peut mettre **jusqu'à 1h** à se propager: les anciens noms subsistent le temps de la propagation.

**Décision ouverte:** `universe` est stocké en base (`Film/Série` 10335 cartes, `Anime` 2467, `Jeu Vidéo` 419).
Les valeurs sont restées en français pour ne pas scinder les collections. Soit migration SQL + importeurs alignés,
soit traduction à l'affichage seulement.

---

## 7. Ouvert / prochaines étapes

- **Finir/valider la migration anglaise**: lancer `python scripts/i18n_smoke.py` (doit être 100% vert) avant tout déploiement.
- Décider du sort des valeurs `universe` stockées en base (cf §6).
- Validation clé Riot production (garder `landing/riot.txt`).
- Équilibrage des nombres de récompenses boss après vrais raids live.
- Croissance hors top.gg (banni): autres listings de bots, SEO de la landing, bouche-à-oreille.

**Ne pas** refactorer ou ajouter des features non demandées.
