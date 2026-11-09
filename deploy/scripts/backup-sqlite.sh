#!/usr/bin/env bash
# =============================================================================
# Резервная копия SQLite-БД панели (без остановки контейнера).
#
#   bash deploy/scripts/backup-sqlite.sh [путь-к-файлу-бэкапа]
#
# Делает консистентный снимок через VACUUM INTO внутри контейнера app,
# копирует его на хост и удаляет временный файл из /data.
# ENCRYPTION_KEY в бэкап не входит — сохраните его отдельно (менеджер секретов),
# иначе бэкап не даст доступа к SSH/REALITY-секретам.
# =============================================================================
set -euo pipefail

OUT="${1:-./panel-backup-$(date -u +%Y%m%dT%H%M%SZ).db}"

CONTAINER="${APP_CONTAINER:-}"
if [ -z "$CONTAINER" ]; then
  CONTAINER=$(docker ps -qf 'name=app' -f status=running | head -n 1 || true)
fi
if [ -z "$CONTAINER" ]; then
  echo "backup-sqlite: ERROR: контейнер app не найден; задайте APP_CONTAINER=<container>" >&2
  exit 1
fi

REMOTE=$(docker exec -i "$CONTAINER" python - <<'PY'
import datetime
import sqlite3

target = "/data/backup-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".db"
con = sqlite3.connect("/data/app.db")
try:
    con.execute("VACUUM INTO '" + target + "'")
finally:
    con.close()
print(target)
PY
)

docker cp "$CONTAINER:$REMOTE" "$OUT"
docker exec "$CONTAINER" rm -f "$REMOTE"
echo "backup-sqlite: OK -> $OUT"
echo "Проверка целостности: python3 -c \"import sqlite3; print(sqlite3.connect('$OUT').execute('PRAGMA integrity_check').fetchone())\""
echo "ENCRYPTION_KEY храните отдельно — без него секреты из бэкапа не расшифровать."
