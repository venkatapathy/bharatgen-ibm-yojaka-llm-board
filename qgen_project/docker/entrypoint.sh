#!/bin/sh
set -e

echo "[entrypoint] Waiting for postgres..."
until python -c "
import psycopg2, os
psycopg2.connect(
    dbname=os.environ.get('DB_NAME','qgen_db'),
    user=os.environ.get('DB_USER','postgres'),
    password=os.environ.get('DB_PASSWORD','postgres'),
    host=os.environ.get('DB_HOST','db'),
    port=os.environ.get('DB_PORT','5432'),
)
" 2>/dev/null; do
  echo "[entrypoint]   postgres not ready, retrying in 2s..."
  sleep 2
done
echo "[entrypoint] Postgres is up."

echo "[entrypoint] Making migrations..."
python manage.py makemigrations --settings="${DJANGO_SETTINGS_MODULE:-qgen.settings.development}"

echo "[entrypoint] Running migrations..."
python manage.py migrate --settings="${DJANGO_SETTINGS_MODULE:-qgen.settings.development}"

exec "$@"
