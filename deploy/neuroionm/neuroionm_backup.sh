#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/var/www/sites/neuroionm"
ENV_FILE="$APP_ROOT/shared/.env.production"
BACKUP_ROOT="/var/backups/neuroionm"
RETENTION_DAYS="${NEUROIONM_BACKUP_RETENTION_DAYS:-21}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_ROOT/$TIMESTAMP"
LOCK_FILE="/run/neuroionm-backup.lock"

cleanup_on_error() {
  local exit_code=$?
  if (( exit_code != 0 )) && [[ -d "${DEST:-}" && ! -f "${DEST:-}/SHA256SUMS" ]]; then
    rm -rf "$DEST"
  fi
  exit "$exit_code"
}
trap cleanup_on_error EXIT

strip_cr() {
  printf '%s' "$1" | tr -d '\r'
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  logger -t neuroionm-backup "backup already running"
  exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a

MONGO_URI="$(strip_cr "${IONM_MONGO_URI:?IONM_MONGO_URI is required}")"
MONGO_DB="$(strip_cr "${IONM_MONGO_DB:-neuroionm_platform}")"
RETENTION_DAYS="$(strip_cr "$RETENTION_DAYS")"
CURRENT_RELEASE="$(readlink -f "$APP_ROOT/current")"

install -d -m 700 -o root -g root "$BACKUP_ROOT" "$DEST"

mongodump --uri "$MONGO_URI" --db "$MONGO_DB" --archive="$DEST/mongo-$MONGO_DB.archive.gz" --gzip >/dev/null

tar -C "$CURRENT_RELEASE" -czf "$DEST/current-release.tar.gz" .

tar -czf "$DEST/server-config.tar.gz" \
  /etc/systemd/system/neuroionm.service \
  /etc/nginx/sites-enabled/neuroionm \
  "$ENV_FILE" \
  2>/dev/null

cat > "$DEST/RESTORE_NOTES.txt" <<NOTES
NeuroIONM backup created at $TIMESTAMP UTC

Mongo restore:
  mongorestore --uri '<IONM_MONGO_URI>' --archive=$DEST/mongo-$MONGO_DB.archive.gz --gzip --nsInclude '$MONGO_DB.*'

Release restore:
  mkdir -p /var/www/sites/neuroionm/releases/<release-id>
  tar -xzf $DEST/current-release.tar.gz -C /var/www/sites/neuroionm/releases/<release-id>
  ln -sfn /var/www/sites/neuroionm/releases/<release-id> /var/www/sites/neuroionm/current
  chown -R www-data:www-data /var/www/sites/neuroionm/releases/<release-id>
  systemctl restart neuroionm.service

Config archive:
  $DEST/server-config.tar.gz

This backup is local to the server and contains secrets from .env.production.
Keep /var/backups/neuroionm root-readable only.
NOTES

(
  cd "$DEST"
  sha256sum mongo-"$MONGO_DB".archive.gz current-release.tar.gz server-config.tar.gz RESTORE_NOTES.txt > SHA256SUMS
)

chmod -R go-rwx "$DEST"
ln -sfn "$DEST" "$BACKUP_ROOT/latest"

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*Z' -mtime +"$RETENTION_DAYS" -exec rm -rf {} +

logger -t neuroionm-backup "backup completed at $DEST"
echo "backup completed: $DEST"