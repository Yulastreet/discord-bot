# Setup environnement DEV local

## 1. Cree un second bot Discord (DEV)

1. <https://discord.com/developers/applications> -> **New Application** -> "TookBot Dev"
2. Onglet **Bot** -> **Reset Token** -> copie
3. Onglet **OAuth2** -> note Client ID + Client Secret
4. Onglet **OAuth2 -> Redirects** -> ajoute `http://localhost:5001/oauth/callback`
5. Onglet **Bot -> Privileged Gateway Intents** -> active `MESSAGE CONTENT` + `SERVER MEMBERS`
6. Invite TookBot Dev sur ton serveur test via :
   `https://discord.com/oauth2/authorize?client_id=<CLIENT_ID>&permissions=1101952052310&scope=bot+applications.commands`

## 2. Cree `.env.dev`

```bash
cp .env.dev.example .env.dev
# Edit .env.dev et remplis DISCORD_TOKEN, DISCORD_CLIENT_ID, etc.
```

Notes :
- `DB_PATH=bot_database_dev.db` -> DB separee, jamais touche la prod
- `WEB_PORT=5001` -> port different de Hetzner (5000 prod)
- `STRIPE_SECRET_KEY=sk_test_...` -> utilise tes clefs Stripe **TEST**

## 3. Installer dependencies dev

```bash
pip install -r requirements.txt
pip install pyinstaller  # optionnel, pour build .exe
```

## 4. Lancer le bot + dashboard via GUI

```bash
python dev_launcher.py
```

GUI Tkinter s'ouvre avec boutons :
- Demarrer / Arreter / Redemarrer **Bot Discord (DEV)**
- Demarrer / Arreter / Redemarrer **Dashboard Web (DEV)**
- Ouvrir Dashboard (browser -> localhost:5001)
- Git Pull (recup derniers commits)
- Tout demarrer / Tout arreter

## 5. (Optionnel) Compiler en .exe single-file

```bash
build_exe.bat
```

Genere `dist\TookBot Dev Launcher.exe` que tu peux double-cliquer sans Python.

---

## Workflow de dev recommande

1. Modifie le code Windows local
2. Click **Redemarrer Bot** dans le launcher
3. Test sur ton serveur Discord
4. Quand validé : `git push`
5. SSH Hetzner -> `git pull && pm2 restart discord-bot`

La prod n'est jamais touchee tant que tu n'as pas push + restart Hetzner.

## Detection auto env (.env.dev vs .env)

`bot.py`, `web.py`, `database.py` chargent automatiquement `.env.dev` si present,
sinon `.env`. Sur Hetzner il n'y a pas de `.env.dev` -> charge `.env` normal.

```python
_env_file = ".env.dev" if os.path.exists(".env.dev") else ".env"
load_dotenv(_env_file)
```

`DB_PATH` env override le chemin DB. Defaut `bot_database.db`. Dev = `bot_database_dev.db`.
