#!/usr/bin/env bash
# ============================================================================
#  update_ytdlp.sh — Maintien à jour de yt-dlp (+ restart bot si version change)
# ============================================================================
#
#  YouTube change ses anti-bots / signature challenges régulièrement.
#  yt-dlp pousse des fixes presque chaque semaine. Ce script s'assure
#  que ton bot tourne toujours sur la dernière version.
#
#  Cron hebdo (lundi 4h):
#    0 4 * * 1 /home/ubuntu/discord-bot/scripts/update_ytdlp.sh >> /home/ubuntu/discord-bot/scripts/update.log 2>&1
# ============================================================================

set -euo pipefail

PM2_PROCESS="${PM2_PROCESS:-discord-bot}"

OLD=$(yt-dlp --version 2>/dev/null || echo "absent")
echo "[ytdlp] $(date -Iseconds) version actuelle: $OLD"

pip install --upgrade --quiet yt-dlp

NEW=$(yt-dlp --version 2>/dev/null || echo "absent")
echo "[ytdlp] version après upgrade: $NEW"

if [[ "$OLD" != "$NEW" ]]; then
  echo "[ytdlp] update $OLD -> $NEW, restart $PM2_PROCESS"
  pm2 restart "$PM2_PROCESS" --update-env || echo "[ytdlp] WARN pm2 restart échoué"
else
  echo "[ytdlp] déjà à jour, pas de restart"
fi
