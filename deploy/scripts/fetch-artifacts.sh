#!/usr/bin/env bash
# =============================================================================
# Артефакты узлов для провижининга (панель загружает их на узлы по SFTP).
#
# Складывает в каталог, заданный ARTIFACTS_DIR (по умолчанию <repo>/artifacts):
#   xray-linux-amd64       Xray-core заданной версии (release XTLS/Xray-core)
#   vpn-agent-linux-amd64  статический vpn-agent (go build, linux/amd64)
#   geoip.dat, geosite.dat Loyalsoldier/v2ray-rules-dat (latest release)
#
# Именно эти имена ожидает backend/app/services/provisioning.py.
#
# Запуск:
#   bash deploy/scripts/fetch-artifacts.sh            # скачать/собрать
#   bash deploy/scripts/fetch-artifacts.sh --check    # только проверить готовность
#   XRAY_VERSION=26.11.9 ARTIFACTS_DIR=artifacts ...  # переопределения
#
# Нужен интернет на машине запуска (на узлах интернет для артефактов НЕ нужен:
# панель загружает файлы по SFTP), а также curl/unzip/sha256sum и go для агента
# (либо заранее собранный agent/bin/vpn-agent-linux-amd64).
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${ARTIFACTS_DIR:-$ROOT/artifacts}"
XRAY_VERSION="${XRAY_VERSION:-26.11.9}"
XRAY_ASSET="Xray-linux-64.zip"
XRAY_URL="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/${XRAY_ASSET}"
GEODATA_BASE="https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download"
WORK="$OUT/.build"
FILES=(xray-linux-amd64 vpn-agent-linux-amd64 geoip.dat geosite.dat)

log() { printf '[artifacts] %s\n' "$*"; }
fail() { printf '[artifacts] ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "не найдено: $1"; }

check_all() {
  local missing=0 f
  for f in "${FILES[@]}"; do
    if [ -s "$OUT/$f" ]; then
      log "OK   $OUT/$f"
    else
      log "MISS $OUT/$f"
      missing=1
    fi
  done
  [ "$missing" -eq 0 ] || fail "комплект артефактов неполный: $OUT"
  ( cd "$OUT" && ls -l "${FILES[@]}" && sha256sum "${FILES[@]}" )
}

# Режим проверки: ничего не скачивает (после ручного копирования/в CI).
if [ "${1:-}" = "--check" ]; then
  check_all
  exit 0
fi

need curl
need unzip
need sha256sum
mkdir -p "$OUT" "$WORK"
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------------------
# 1. Xray-core: zip + .dgst, проверка SHA2-256, распаковка.
# ---------------------------------------------------------------------------
log "Xray ${XRAY_VERSION}: ${XRAY_ASSET}"
curl -fL --retry 3 --retry-delay 2 -o "$WORK/${XRAY_ASSET}" "$XRAY_URL"
curl -fL --retry 3 --retry-delay 2 -o "$WORK/${XRAY_ASSET}.dgst" "${XRAY_URL}.dgst"
DIGEST=$(sed -n 's/^SHA2-256= *//p' "$WORK/${XRAY_ASSET}.dgst" | head -n 1)
[ -n "$DIGEST" ] || fail "не удалось прочитать SHA2-256 из ${XRAY_ASSET}.dgst"
printf '%s  %s\n' "$DIGEST" "$WORK/${XRAY_ASSET}" | sha256sum -c -
rm -rf "$WORK/xray"
unzip -q "$WORK/${XRAY_ASSET}" -d "$WORK/xray"
install -m 0755 "$WORK/xray/xray" "$OUT/xray-linux-amd64"
"$OUT/xray-linux-amd64" version 2>/dev/null | head -n 1 || true

# ---------------------------------------------------------------------------
# 2. geodata: Loyalsoldier/v2ray-rules-dat (latest). Контрольные суммы —
#    если файл .sha256sum доступен; иначе зафиксируйте sha256 вручную.
# ---------------------------------------------------------------------------
log "geodata: Loyalsoldier/v2ray-rules-dat (latest)"
for name in geoip.dat geosite.dat; do
  curl -fL --retry 3 --retry-delay 2 -o "$OUT/$name" "$GEODATA_BASE/$name"
done
for name in geoip.dat geosite.dat; do
  if curl -fsSL --retry 2 -o "$WORK/$name.sha256sum" "$GEODATA_BASE/$name.sha256sum"; then
    ( cd "$OUT" && sha256sum -c "$WORK/$name.sha256sum" )
  else
    log "WARN: $name.sha256sum недоступен — проверьте sha256 вручную"
  fi
done

# ---------------------------------------------------------------------------
# 3. vpn-agent: готовый бинарник из agent/bin или сборка из исходников.
# ---------------------------------------------------------------------------
log "vpn-agent (linux/amd64)"
if [ -s "$ROOT/agent/bin/vpn-agent-linux-amd64" ] && [ "${REBUILD_AGENT:-0}" != "1" ]; then
  install -m 0755 "$ROOT/agent/bin/vpn-agent-linux-amd64" "$OUT/vpn-agent-linux-amd64"
else
  command -v go >/dev/null 2>&1 || fail "нужен go для сборки vpn-agent (или выполните make agent заранее)"
  ( cd "$ROOT/agent" && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
      go build -trimpath -ldflags "-s -w" -o "$OUT/vpn-agent-linux-amd64" ./cmd/vpn-agent )
fi

# ---------------------------------------------------------------------------
# 4. Итог: все четыре файла на месте, имена совпадают с ожиданиями backend.
# ---------------------------------------------------------------------------
check_all
log "Готово. Залить в панель: make artifacts-copy (docker cp в /data/artifacts)."
