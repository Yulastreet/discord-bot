#!/usr/bin/env bash
# ============================================================================
#  backup_db.sh - Safe backup of the bot's SQLite database
# ============================================================================
#
#  Pratiques:
#    - utilise sqlite3 .backup pour un snapshot consistent (pas de cp pendant
#      qu'une transaction est ouverte)
#    - local rotation: keeps the last N backups
#    - compression gzip
#    - optional upload to an S3-compatible bucket if rclone is configured
#
#  Usage:
#    ./backup_db.sh                         # sauvegarde locale + rotation
#    BACKUP_REMOTE=remote:bot-backups ./backup_db.sh   # + upload rclone
#
#  Cron quotidien:
#    0 3 * * * /home/ubuntu/discord-bot/scripts/backup_db.sh >> /home/ubuntu/discord-bot/scripts/backup.log 2>&1
# ============================================================================

set -euo pipefail

# Config
DB_PATH="${DB_PATH:-/home/ubuntu/discord-bot/bot_database.db}"
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/discord-bot/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
RETENTION_LOCAL_COUNT="${RETENTION_LOCAL_COUNT:-14}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"  # e.g. "b2:bot-backups" if rclone is configured

mkdir -p "$BACKUP_DIR"

TS=$(date +'%Y-%m-%d_%H%M%S')
SNAP="$BACKUP_DIR/bot_${TS}.db"
GZ="${SNAP}.gz"

echo "[backup] $(date -Iseconds) start, source=$DB_PATH"

# 1. Atomic snapshot via sqlite3 (consistent even during a concurrent write)
sqlite3 "$DB_PATH" ".backup '$SNAP'"
echo "[backup] snapshot ok -> $SNAP"

# 2. Compression
gzip -9 "$SNAP"
echo "[backup] gzip ok -> $GZ"

# 3. Rotation locale: garde les N derniers
ls -1t "$BACKUP_DIR"/bot_*.db.gz 2>/dev/null | tail -n +$((RETENTION_LOCAL_COUNT + 1)) | xargs -r rm -f
echo "[backup] rotation locale ok (keep $RETENTION_LOCAL_COUNT)"

# 4. Remote upload via rclone if configured
if [[ -n "$BACKUP_REMOTE" ]]; then
  if command -v rclone >/dev/null 2>&1; then
    rclone copy "$GZ" "$BACKUP_REMOTE/" --quiet
    echo "[backup] rclone ok -> $BACKUP_REMOTE"
    # Purge distant > RETENTION_DAYS
    rclone delete "$BACKUP_REMOTE/" --min-age "${RETENTION_DAYS}d" --quiet || true
    echo "[backup] rclone purge >${RETENTION_DAYS}d ok"
  else
    echo "[backup] WARN BACKUP_REMOTE is set but rclone was not found" >&2
  fi
fi

SIZE=$(du -h "$GZ" | cut -f1)
echo "[backup] $(date -Iseconds) done ($SIZE)"
