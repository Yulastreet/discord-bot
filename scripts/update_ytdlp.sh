#!/usr/bin/env bash
# ============================================================================
#  update_ytdlp.sh - Keep yt-dlp up to date (+ restart the bot if the version changed)
# ============================================================================
#
#  YouTube changes its anti-bot / signature challenges regularly.
#  yt-dlp pousse des fixes presque chaque semaine. Ce script s'assure
#  so your bot always runs the latest version.
#
#  Cron hebdo (lundi 4h):
#    0 4 * * 1 /home/ubuntu/discord-bot/scripts/update_ytdlp.sh >> /home/ubuntu/discord-bot/scripts/update.log 2>&1
# ============================================================================

set -euo pipefail

PM2_PROCESS="${PM2_PROCESS:-discord-bot}"

OLD=$(yt-dlp --version 2>/dev/null || echo "absent")
echo "[ytdlp] $(date -Iseconds) version actuelle: $OLD"

pip install --upgrade --quiet yt-dlp bgutil-ytdlp-pot-provider

NEW=$(yt-dlp --version 2>/dev/null || echo "absent")
echo "[ytdlp] version after upgrade: $NEW"

if [[ "$OLD" != "$NEW" ]]; then
  echo "[ytdlp] update $OLD -> $NEW, restart $PM2_PROCESS"
  pm2 restart "$PM2_PROCESS" --update-env || echo "[ytdlp] WARN pm2 restart failed"
else
  echo "[ytdlp] already up to date, no restart"
fi
