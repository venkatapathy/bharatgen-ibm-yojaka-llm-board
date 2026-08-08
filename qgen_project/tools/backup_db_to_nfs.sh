#!/usr/bin/env bash
# Sync app data onto the NFS project mount (survives host crash if NFS is elsewhere).
# - Postgres dumps → backups/db/
# - Media (PDFs)   → backups/media/  (rsync from Docker volume; dual-write also keeps this fresh)
#
# Run on the lab host that runs Docker (qgen_project-db-1 / qgen23).
#
#   ./tools/backup_db_to_nfs.sh
#   # cron (daily 2am) — already installable via crontab:
#   0 2 * * * /home1/pankaj/bharatgen-ibm-yojaka-llm-board/qgen_project/tools/backup_db_to_nfs.sh >> /tmp/qgen_db_backup.log 2>&1

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${BACKUP_DIR:-$ROOT/backups/db}"
MEDIA_MIRROR="${MEDIA_MIRROR_DIR:-$ROOT/backups/media}"
KEEP="${BACKUP_KEEP:-7}"
DB_CONTAINER="${DB_CONTAINER:-qgen23-db-1}"
MEDIA_CONTAINER="${MEDIA_CONTAINER:-qgen23-web-1}"
DB_USER="${DB_USER:-postgres}"
# Independent 2.3 Postgres (no longer shared with 2.1).
DATABASES=(qgen_db_dev23)

mkdir -p "$OUT_DIR" "$MEDIA_MIRROR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if ! docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  echo "ERROR: container $DB_CONTAINER is not running" >&2
  exit 1
fi

for db in "${DATABASES[@]}"; do
  dest="$OUT_DIR/${db}_${STAMP}.sql.gz"
  echo "Dumping $db -> $dest"
  docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" --no-owner --no-acl "$db" \
    | gzip -c > "$dest"
  ls -lh "$dest"
done

# Keep only the newest KEEP dumps per database
for db in "${DATABASES[@]}"; do
  mapfile -t old < <(ls -1t "$OUT_DIR/${db}_"*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) || true)
  for f in "${old[@]:-}"; do
    [[ -n "$f" ]] || continue
    echo "Removing old $f"
    rm -f "$f"
  done
done

# Full media sync Docker → NFS (covers 2.1 + 2.3 uploads; dual-write covers new 2.3 saves live)
if docker ps --format '{{.Names}}' | grep -qx "$MEDIA_CONTAINER"; then
  echo "Rsync media -> $MEDIA_MIRROR"
  docker exec "$MEDIA_CONTAINER" sh -c '
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete /var/qgen_media/ /var/qgen_media_mirror/
    else
      # Fallback if image has no rsync
      mkdir -p /var/qgen_media_mirror
      cp -a /var/qgen_media/. /var/qgen_media_mirror/ 2>/dev/null || true
    fi
  '
  du -sh "$MEDIA_MIRROR" || true
else
  echo "WARN: $MEDIA_CONTAINER not running; skipping media rsync" >&2
fi

echo "Done. NFS backups: db=$OUT_DIR media=$MEDIA_MIRROR"
