#!/usr/bin/env bash
# =============================================================================
# Entrypoint контейнера VPN Control Panel.
#
# 1. Проверяет данные/том и наличие alembic.ini.
# 2. Применяет миграции: alembic upgrade head (с ретраями — на случай, если
#    PostgreSQL ещё стартует; для SQLite ретраи просто не срабатывают).
# 3. Запускает uvicorn app.main:app --workers 1.
#
# workers=1 обязателен: фоновые задачи (APScheduler, in-process jobs, SSE)
# рассчитаны на один процесс и одну SQLite-БД.
# =============================================================================
set -euo pipefail

readonly PORT="${PORT:-8000}"
readonly DATA_DIR="${DATA_DIR:-/data}"
readonly MIGRATION_ATTEMPTS="${MIGRATION_ATTEMPTS:-30}"
readonly MIGRATION_RETRY_DELAY="${MIGRATION_RETRY_DELAY:-2}"

log() {
  local level="$1" event="$2" message="${3:-}"
  printf '{"ts":"%s","level":"%s","component":"entrypoint","event":"%s","message":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$event" "$message"
}

if [ ! -f /app/alembic.ini ]; then
  log error migration_config_missing "alembic.ini not found in /app; образ собран не из backend/"
  exit 1
fi

# Постоянный том должен быть записываем для UID 10001 (см. deploy/RUNBOOK.md,
# если том создан вручную root-ом: chown -R 10001:10001 <volume>).
mkdir -p "$DATA_DIR" 2>/dev/null || true
if [ ! -w "$DATA_DIR" ]; then
  log error data_dir_not_writable "DATA_DIR=$DATA_DIR not writable by $(id -un) (uid=$(id -u)); fix volume ownership: chown -R 10001:10001 <volume>"
  exit 1
fi
mkdir -p "${ARTIFACTS_DIR:-$DATA_DIR/artifacts}"

attempt=0
while true; do
  attempt=$((attempt + 1))
  if alembic upgrade head; then
    log info migrations_applied "alembic upgrade head: ok (attempt $attempt)"
    break
  fi
  if [ "$attempt" -ge "$MIGRATION_ATTEMPTS" ]; then
    log error migrations_failed "alembic upgrade head failed after $attempt attempts; previous image can be restored by rollback (deploy/RUNBOOK.md)"
    exit 1
  fi
  log warning migrations_retry "alembic upgrade head failed (attempt $attempt/$MIGRATION_ATTEMPTS), retry in ${MIGRATION_RETRY_DELAY}s — waiting for database"
  sleep "$MIGRATION_RETRY_DELAY"
done

log info starting_uvicorn "uvicorn app.main:app on 0.0.0.0:$PORT (workers=1)"
# exec: uvicorn становится PID 1 и корректно получает SIGTERM/SIGINT.
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --timeout-keep-alive 65 \
  --no-access-log \
  --log-level "${UVICORN_LOG_LEVEL:-info}"
