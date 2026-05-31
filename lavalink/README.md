# Lavalink — moteur audio TookBot

Remplace yt-dlp + ffmpeg par Lavalink (serveur audio Java) + le plugin
`youtube-source`. Resout le blocage YouTube "Sign in to confirm you're not a bot"
grace a OAuth + clients multiples, comme les gros bots (Jockie, Hydra...).

## Pourquoi

- yt-dlp depuis une IP datacenter (Oracle/Hetzner/...) se fait flag par YouTube.
- Lavalink + youtube-source bascule entre plusieurs clients YouTube (MUSIC, WEB,
  ANDROID, IOS...) et utilise un refresh token OAuth d'un compte Google.
- Lavalink tourne a cote du bot (port 2333, localhost). Le bot s'y connecte via wavelink.

## Prerequis VPS

```bash
# Java 17+ (Lavalink 4 exige Java 17 minimum)
sudo apt update
sudo apt install -y openjdk-17-jre-headless
java -version   # doit afficher 17 ou plus
```

## Installation

```bash
cd ~/discord-bot/lavalink

# Telecharge Lavalink.jar (verifie la derniere release)
# https://github.com/lavalink-devs/Lavalink/releases
wget -O Lavalink.jar https://github.com/lavalink-devs/Lavalink/releases/latest/download/Lavalink.jar

# application.yml est deja fourni dans ce dossier.
# Le plugin youtube-source se telecharge AUTOMATIQUEMENT au 1er lancement
# (declare dans application.yml > lavalink.plugins).
```

## Mot de passe

Dans le `.env` du bot (`~/discord-bot/.env`), ajoute :

```
LAVALINK_PASSWORD=un_mot_de_passe_solide
LAVALINK_HOST=127.0.0.1
LAVALINK_PORT=2333
```

Le meme mot de passe est lu par `application.yml` via `${LAVALINK_PASSWORD}`.
Lance Lavalink avec la variable d'env disponible (voir pm2 ci-dessous).

## Activer OAuth YouTube (le fix anti-blocage)

`oauth.enabled: true` est deja dans application.yml.

1. Lance Lavalink une 1ere fois (voir pm2).
2. Dans les logs, cherche une ligne du type :
   `OAUTH INTEGRATION: To authorise, visit https://www.google.com/device and enter code XXXX-XXXX`
3. Ouvre l'URL sur ton PC, entre le code, connecte-toi avec un **compte Google jetable**
   (PAS ton perso : risque de flag/ban du compte).
4. Lavalink logge alors un `refreshToken`. Copie-le.
5. Colle-le dans `application.yml` sous `plugins.youtube.oauth.refreshToken: "..."`.
6. Mets `skipInitialization: true` (evite de redemander le login a chaque boot).
7. Restart Lavalink.

A partir de la, YouTube te prend pour un humain connecte. Plus de "Sign in to confirm".

## Lancer avec pm2

```bash
cd ~/discord-bot/lavalink

# Charge le .env du bot pour avoir LAVALINK_PASSWORD, puis lance le jar.
pm2 start "java -jar Lavalink.jar" --name lavalink --cwd ~/discord-bot/lavalink

# Verifie que ca demarre (cherche "Lavalink is ready to accept connections")
pm2 logs lavalink --lines 40

pm2 save
```

Si `LAVALINK_PASSWORD` n'est pas pris en compte, exporte-le avant :

```bash
export LAVALINK_PASSWORD=ton_mdp
pm2 start "java -jar Lavalink.jar" --name lavalink --cwd ~/discord-bot/lavalink --update-env
pm2 save
```

## Ordre de boot

Lavalink doit etre UP avant le bot (le bot se connecte au node au demarrage,
avec retry). Lance dans l'ordre :

```bash
pm2 restart lavalink
pm2 restart discord-bot --update-env
```

## Verification

```bash
# Lavalink ecoute ?
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:2333/version   # 200 ou 401 = up

# Cote bot, au boot, cherche :
pm2 logs discord-bot --lines 30 | grep -i wavelink
# -> "[wavelink] node connecte" attendu
```

## Desinstaller l'ancien moteur (optionnel, plus tard)

Une fois Lavalink valide, yt-dlp / bgutil / ffmpeg ne sont plus utilises pour
la musique. On peut les retirer du requirements.txt, mais yt-dlp reste utilise
ailleurs (verifier avant). Ne PAS supprimer a la legere.
