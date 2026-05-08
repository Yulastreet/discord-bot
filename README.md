# Bot Console — Discord bot + Web dashboard

Personal multi-feature Discord bot with an integrated admin dashboard.

- **Bot** (Python · discord.py 2.7) : XP per-guild, auto-reactions, music
  (YouTube via yt-dlp + Deno), duels with custom sabre catalog, and more.
- **Dashboard** (Flask + Jinja) : per-guild + cross-server views to monitor
  and edit everything stored in the SQLite DB. Theme dark/light, parallax,
  Vercel-inspired Restrained palette with a lime acid accent.
- **Architecture** : the bot and the web run as **two separate pm2 processes**
  on the VPS; web pushes commands to the bot via a lightweight DB-backed
  command queue (~1.5s polling).

---

## Stack

| Layer | Tech |
|---|---|
| Bot   | Python 3.10+ · discord.py 2.7 · yt-dlp · ffmpeg · Deno (JS challenge solver for YouTube) |
| Web   | Flask · Jinja · Geist font · OKLCH tokens |
| DB    | SQLite (single file `bot_database.db`) |
| Run   | pm2 (user-level, **never** `sudo pm2`) + systemd |
| Optional | nginx + certbot for HTTPS, rclone for backup offsite |

---

## Setup local

```bash
git clone <repo>
cd discord-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edite .env (au minimum DISCORD_TOKEN + WEB_PASSWORD)
python bot.py     # dans un terminal
python web.py     # dans un autre
```

Dashboard accessible sur `http://localhost:5000`.

---

## Setup VPS (production)

```bash
# 1. Dépendances système
sudo apt update
sudo apt install -y python3-pip ffmpeg libopus0 libopus-dev libffi-dev unzip

# 2. Node.js 20 + Deno (pour yt-dlp JS challenges)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
curl -fsSL https://deno.land/install.sh | sh
echo 'export DENO_INSTALL="$HOME/.deno"' >> ~/.bashrc
echo 'export PATH="$DENO_INSTALL/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# 3. Pip packages
pip install -r requirements.txt

# 4. Cookies YouTube (anti-bot bypass) — voir section dédiée

# 5. PM2 — IMPORTANT : jamais `sudo pm2`, sinon libopus introuvable
pm2 start bot.py --name discord-bot --interpreter python3
pm2 start web.py --name web-dashboard --interpreter python3
pm2 save
pm2 startup    # exécute la commande qu'il imprime, puis :
pm2 save
```

### Cookies YouTube (obligatoire)

YouTube bloque yt-dlp avec un challenge anti-bot. Solution : exporter les
cookies depuis un navigateur logué.

1. Sur ton PC, installe l'extension Chrome/Firefox **"Get cookies.txt LOCALLY"**
2. Va sur `youtube.com` (compte connecté)
3. Clique sur l'extension → Export → tu obtiens `cookies.txt`
4. Pousse vers le VPS :
   ```bash
   scp cookies.txt ubuntu@<IP_VPS>:~/discord-bot/cookies.txt
   ```
5. Restart : `pm2 restart discord-bot`

Les cookies expirent au bout de quelques semaines/mois. Si la musique
recommence à planter avec "rotated cookies", refais l'export.

### HTTPS via nginx + Let's Encrypt (fortement recommandé)

Sans HTTPS le mot de passe du dashboard transite en clair.

```bash
# 1. Installer nginx + certbot
sudo apt install -y nginx certbot python3-certbot-nginx

# 2. Configurer nginx (remplace dashboard.tonnomdedomaine.fr)
sudo nano /etc/nginx/sites-available/bot-dashboard
```
Contenu :
```nginx
server {
    listen 80;
    server_name dashboard.tonnomdedomaine.fr;

    client_max_body_size 5m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_read_timeout 60s;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/bot-dashboard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 3. Certificat Let's Encrypt (auto-renew)
sudo certbot --nginx -d dashboard.tonnomdedomaine.fr

# 4. Active le flag HTTPS dans .env
echo "HTTPS_ENABLED=1" >> ~/discord-bot/.env
pm2 restart web-dashboard --update-env

# 5. Ferme le port 5000 au monde extérieur (uniquement nginx)
sudo ufw allow 'Nginx Full'
sudo ufw deny 5000
```

### Backup automatique

Le script `scripts/backup_db.sh` crée un snapshot consistent + gzip + rotation,
avec upload optionnel vers un bucket via rclone.

```bash
chmod +x ~/discord-bot/scripts/backup_db.sh

# Cron quotidien 3h
crontab -e
# Ajoute :
0 3 * * * /home/ubuntu/discord-bot/scripts/backup_db.sh >> /home/ubuntu/discord-bot/scripts/backup.log 2>&1
```

Pour activer l'upload offsite (Backblaze B2 gratuit jusqu'à 10 GB) :

```bash
sudo apt install -y rclone
rclone config    # configure un remote nommé 'b2' par exemple
# puis dans le cron, prefix la commande avec :
#   BACKUP_REMOTE=b2:bot-backups /home/ubuntu/discord-bot/scripts/backup_db.sh
```

### Auto-update yt-dlp (recommandé)

```bash
chmod +x ~/discord-bot/scripts/update_ytdlp.sh
crontab -e
# Tous les lundis à 4h :
0 4 * * 1 /home/ubuntu/discord-bot/scripts/update_ytdlp.sh >> /home/ubuntu/discord-bot/scripts/update.log 2>&1
```

---

## Variables d'environnement (.env)

Voir `.env.example`. Au minimum :

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Token bot (Discord Developer Portal → Bot → Token) |
| `WEB_PASSWORD`  | Mot de passe d'accès au dashboard |
| `FLASK_SECRET`  | Clé de session Flask (32+ chars random) |

Optionnel :

| Variable | Défaut | Description |
|---|---|---|
| `HTTPS_ENABLED` | 0 | Mettre 1 derrière nginx HTTPS pour activer Secure cookies |
| `SESSION_LIFETIME_HOURS` | 24 | Durée de validité d'une session web |
| `FLASK_DEBUG`   | 0 | 1 pour Flask debug mode (jamais en prod) |

---

## Structure

```
discord-bot/
├── bot.py                  # Bot Discord (entrée principale)
├── web.py                  # Web dashboard Flask
├── database.py             # SQLite : init + helpers
├── duel_commands.py        # Slash commands duel + sabre
├── duel_combat.py          # Logique combat (HP, dégâts, effets)
├── duel_minigames.py       # Mini-jeux entre tours de duel
├── duel_sabres.py          # Catalogue sabres (seed initial)
├── rank_card.py            # Génération images de level-up (Pillow)
├── templates/              # Jinja templates dashboard
│   ├── _base.html          # Shell sidebar + topbar + theme
│   ├── _styles.html        # Tokens CSS OKLCH (1 fichier source)
│   ├── dashboard.html
│   ├── duels.html
│   ├── music.html
│   ├── reactions.html
│   ├── logs.html
│   ├── bottalk.html
│   ├── dms.html
│   ├── status.html
│   └── ...
├── scripts/
│   ├── backup_db.sh        # Snapshot + gzip + rclone offsite
│   └── update_ytdlp.sh     # Cron hebdo update yt-dlp
└── PRODUCT.md              # Strategic design context (impeccable plugin)
```

---

## Troubleshooting

| Symptôme | Cause probable | Fix |
|---|---|---|
| `/play` : "Sign in to confirm you're not a bot" | Cookies YouTube manquants ou expirés | Re-export cookies via extension navigateur |
| `/play` : "Requested format is not available" | Deno absent ou pas dans PATH du process pm2 | `pm2 restart --update-env` après install Deno |
| `/join` : "davey library needed" | libopus introuvable côté process | `apt install libopus0` + jamais `sudo pm2` |
| Dashboard inaccessible | pm2 lance avec `sudo` (root env différent de ubuntu) | Toujours `pm2 start ...` sans sudo |
| `Address already in use` port 5000 | Ancien Flask zombie (debug mode + reloader) | `fuser -k 5000/tcp` puis restart |
| Sessions web sautent à chaque restart | `FLASK_SECRET` non défini | Génère une clé : `echo "FLASK_SECRET=$(openssl rand -hex 32)" >> .env` |

---

## Licence

Personnel — pas de licence publique.
