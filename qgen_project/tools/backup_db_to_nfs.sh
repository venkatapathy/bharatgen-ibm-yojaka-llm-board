#!/usr/bin/env bash
# Dump Postgres DBs onto the NFS project mount (survives host crash if NFS is elsewhere).
# Run on the lab host that runs Docker (qgen_project-db-1).
#
#   ./tools/backup_db_to_nfs.sh
#   # cron example (daily 2am):
#   0 2 * * * /home1/pankaj/bharatgen-ibm-yojaka-llm-board/qgen_project/tools/backup_db_to_nfs.sh >> /tmp/qgen_db_backup.log 2>&1

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${BACKUP_DIR:-$ROOT/backups/db}"
KEEP="${BACKUP_KEEP:-7}"
DB_CONTAINER="${DB_CONTAINER:-qgen_project-db-1}"
DB_USER="${DB_USER:-postgres}"
# Both stacks share this Postgres container.
DATABASES=(qgen_db_dev23 qgen_db)

mkdir -p "$OUT_DIR"
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

echo "Done. Backups on NFS mount: $OUT_DIR"
