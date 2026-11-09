#!/usr/bin/env bash
# =============================================================================
# acceptance.sh — приёмочный smoke-тест ЗАПУЩЕННОЙ панели VPN Control Panel.
#
# Проверяет публичные и админские сценарии по HTTP (без SSH к узлам):
# healthz, /metrics, login/CSRF/сессии, серверы (uptime_24h_pct), события,
# инциденты, метрики, выходы (external + creds), сайты (Anthropic allowlist,
# RU-locked), профиль маршрутизации, /routing/check, /routing/apply + ревизия,
# клиенты (link/profile/qr/subscription в трёх форматах), jobs, agent-auth.
#
# Использование:
#   BASE_URL=https://panel.example.com \
#   ADMIN=admin@example.com PASSWORD='...' \
#   [METRICS_TOKEN='...'] \
#   [ACCEPTANCE_CREATE_ENTRY=1] \
#   bash deploy/scripts/acceptance.sh
#
# Переменные:
#   BASE_URL               адрес панели (обязательно)
#   ADMIN, PASSWORD        учётка администратора (обязательно; не печатаются)
#   METRICS_TOKEN          если задан на панели — проверить GET /metrics
#   ACCEPTANCE_CREATE_ENTRY=1  создать временный entry-сервер (fake SSH), чтобы
#                          проверить link/profile/qr/sub/apply; по умолчанию 0 —
#                          используются уже существующие entry-узлы (если есть)
#   CURL_TIMEOUT           таймаут запроса, сек (по умолчанию 20)
#
# Скрипт создаёт временные ресурсы (external exit, site rule, клиент, при
# необходимости server) и УДАЛЯЕТ их, а профиль маршрутизации восстанавливает.
# Всё же запускайте осознанно: против рабочей панели будет кратковременно
# изменён профиль и запущен job apply.
#
# Вывод: строки PASS/FAIL/SKIP по шагам + сводка. Секреты не выводятся.
# Код возврата: 0 — все проверки PASS (SKIP допустимы), 1 — есть FAIL, 2 — нет env.
# =============================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-}"
ADMIN="${ADMIN:-}"
PASSWORD="${PASSWORD:-}"
METRICS_TOKEN="${METRICS_TOKEN:-}"
CREATE_ENTRY="${ACCEPTANCE_CREATE_ENTRY:-0}"
CURL_TIMEOUT="${CURL_TIMEOUT:-20}"

if [ -z "$BASE_URL" ] || [ -z "$ADMIN" ] || [ -z "$PASSWORD" ]; then
  echo "usage: BASE_URL=https://panel.example.com ADMIN=... PASSWORD=... bash $0" >&2
  echo "       (опционально METRICS_TOKEN=..., ACCEPTANCE_CREATE_ENTRY=1)" >&2
  exit 2
fi
BASE_URL="${BASE_URL%/}"

WORK="$(mktemp -d)"
BODY="$WORK/body"
COOKIE_JAR="$WORK/cookies.txt"
EMPTY_JAR="$WORK/empty-cookies.txt"
: > "$COOKIE_JAR"
: > "$EMPTY_JAR"
EXTRA_CURL=()
NO_CSRF=0
HTTP_STATUS="000"
RESP_CT=""
PASS=0
FAIL=0
SKIP=0
CLIENT_ID=""
SUB_TOKEN=""
SITE_ID=""
EXIT_ID=""
CREATED_SERVER_ID=""
ENTRY_ID=""
CSRF=""
CLEAN_DONE=0

ok()   { PASS=$((PASS + 1)); printf 'PASS  %-44s %s\n' "$1" "${2:-}"; }
skip() { SKIP=$((SKIP + 1)); printf 'SKIP  %-44s %s\n' "$1" "${2:-}"; }
bad()  { FAIL=$((FAIL + 1)); printf 'FAIL  %-44s %s\n' "$1" "${2:-}"; }

usage_hint() { printf '  (HTTP %s, content-type %s)\n' "${HTTP_STATUS:-?}" "${RESP_CT:-?}"; }

request() { # method path [json-body]
  local method="$1" path="$2" data=""
  shift 2
  if [ $# -gt 0 ]; then data="$1"; shift; fi
  local jar="${JAR_OVERRIDE:-$COOKIE_JAR}"
  local -a args=(-sS --max-time "$CURL_TIMEOUT" -b "$jar" -c "$jar" -X "$method" -o "$BODY" -w '%{http_code}|%{content_type}')
  if [ -n "$data" ]; then args+=(-H 'Content-Type: application/json' --data-binary "$data"); fi
  if [ "${NO_CSRF:-0}" != "1" ] && [ -n "${CSRF:-}" ]; then args+=(-H "X-CSRF-Token: $CSRF"); fi
  if [ "${#EXTRA_CURL[@]}" -gt 0 ]; then args+=("${EXTRA_CURL[@]}"); fi
  args+=("$BASE_URL$path")
  local meta=""
  meta=$(curl "${args[@]}" 2>/dev/null) || meta="000|"
  HTTP_STATUS="${meta%%|*}"
  RESP_CT="${meta#*|}"
  [ -n "$HTTP_STATUS" ] || HTTP_STATUS="000"
  EXTRA_CURL=()
  NO_CSRF=0
}

expect_status() { # name expected
  if [ "$HTTP_STATUS" = "$2" ]; then
    ok "$1" "HTTP $2"
  else
    bad "$1" "HTTP $HTTP_STATUS, ожидался $2"
    usage_hint
  fi
}

json_assert() { # name 'python-expression over d'
  if python3 - "$BODY" "$2" >/dev/null 2>&1 <<'PY'
import json
import sys

try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
try:
    ok = bool(eval(sys.argv[2], {
        "d": d, "len": len, "isinstance": isinstance, "any": any, "all": all,
        "str": str, "int": int, "float": float, "bool": bool, "list": list,
        "dict": dict, "min": min, "max": max, "set": set,
    }))
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PY
  then
    ok "$1" "json ok"
  else
    bad "$1" "json assert: $2"
    usage_hint
  fi
}

error_code_is() { # name expected_code
  json_assert "$1" "d.get('error', {}).get('code') == \"$2\""
}

json_value() { # dotted path -> value (empty if absent; lists/dicts as JSON)
  python3 - "$BODY" "$1" <<'PY'
import json
import sys

try:
    cur = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
for part in sys.argv[2].split("."):
    if isinstance(cur, list):
        try:
            cur = cur[int(part)]
        except (ValueError, IndexError):
            cur = None
            break
    elif isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
        break
if cur is None:
    print("")
elif isinstance(cur, (dict, list)):
    print(json.dumps(cur))
else:
    print(cur)
PY
}

body_contains() { # name needle
  if grep -qF -- "$2" "$BODY" 2>/dev/null; then
    ok "$1" "содержит: $2"
  else
    bad "$1" "нет строки: $2"
    usage_hint
  fi
}

fetch_entry_id() { # из текущего $BODY (список серверов) определить entry-узел
  python3 - "$BODY" <<'PY'
import json
import sys

try:
    servers = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
for server in servers:
    if "entry" in str(server.get("roles", "")):
        print(server.get("id", ""))
        break
else:
    print("")
PY
}

cleanup() {
  set +e
  if [ "$CLEAN_DONE" = "1" ]; then
    return 0
  fi
  CLEAN_DONE=1
  if [ -n "$CSRF" ] && [ -n "$BASE_URL" ]; then
    if [ -n "$CLIENT_ID" ]; then request DELETE "/api/v1/clients/$CLIENT_ID"; fi
    if [ -n "$SITE_ID" ]; then request DELETE "/api/v1/sites/$SITE_ID"; fi
    if [ -f "$WORK/orig-profile.json" ]; then
      local payload
      payload=$(python3 - "$WORK/orig-profile.json" <<'PY'
import json
import sys

try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
print(json.dumps({"default_exit_ids": d.get("default_exit_ids", []), "enabled": bool(d.get("enabled"))}))
PY
)
      if [ -n "$payload" ]; then request PUT "/api/v1/routing/profile" "$payload"; fi
    fi
    if [ -n "$EXIT_ID" ]; then request DELETE "/api/v1/exits/$EXIT_ID"; fi
    if [ -n "$CREATED_SERVER_ID" ]; then request DELETE "/api/v1/servers/$CREATED_SERVER_ID" '{"force":true}'; fi
  fi
  return 0
}
trap 'cleanup; rm -rf "$WORK"' EXIT

printf 'acceptance: target %s\n' "$BASE_URL"
printf 'acceptance: mode: entry=%s, metrics_token=%s\n' \
  "$([ "$CREATE_ENTRY" = "1" ] && echo create-fake || echo existing-only)" \
  "$([ -n "$METRICS_TOKEN" ] && echo set || echo skip)"

# ---------------------------------------------------------------------------
# 1. Публичные health/openapi/metrics
# ---------------------------------------------------------------------------
request GET /healthz
expect_status healthz 200
json_assert healthz "d.get('status') == 'ok' and d.get('db') == 'ok' and bool(d.get('version'))"

request GET /api/openapi.json
expect_status openapi 200
body_contains openapi "\"/healthz\""

if [ -n "$METRICS_TOKEN" ]; then
  EXTRA_CURL=(-H "Authorization: Bearer $METRICS_TOKEN")
  request GET /metrics
  expect_status metrics_authorized 200
  case "$RESP_CT" in
    text/plain*) ok metrics_content_type "text/plain" ;;
    *) bad metrics_content_type "content-type: $RESP_CT" ;;
  esac
  body_contains metrics_body "vpn_uptime_seconds"
  request GET /metrics
  expect_status metrics_unauthorized 401
else
  request GET /metrics
  if [ "$HTTP_STATUS" = "404" ]; then
    skip metrics "METRICS_TOKEN не задан (эндпоинт выключен, HTTP 404)"
  else
    bad metrics "METRICS_TOKEN не задан, но /metrics отдал HTTP $HTTP_STATUS"
  fi
fi

# ---------------------------------------------------------------------------
# 2. Аутентификация, сессия, CSRF, rate-limit-ошибка
# ---------------------------------------------------------------------------
BAD_LOGIN_JSON=$(python3 -c 'import json,sys; print(json.dumps({"email": sys.argv[1], "password": "definitely-wrong-" + sys.argv[2][:8]}))' "$ADMIN" "x")
request POST /api/v1/auth/login "$BAD_LOGIN_JSON"
expect_status auth_login_invalid 401
error_code_is auth_login_invalid_code invalid_credentials

LOGIN_JSON=$(python3 - "$ADMIN" "$PASSWORD" <<'PY'
import json
import sys

print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))
PY
)
request POST /api/v1/auth/login "$LOGIN_JSON"
expect_status auth_login 200
CSRF=$(json_value csrf_token)
if [ -n "$CSRF" ]; then
  ok auth_csrf_token "csrf_token получен"
else
  bad auth_csrf_token "в ответе login нет csrf_token"
fi
if [ -s "$COOKIE_JAR" ]; then
  ok auth_cookie "cookie-сессия сохранена"
else
  bad auth_cookie "cookie-сессия не получена"
fi

request GET /api/v1/auth/me
expect_status auth_me 200
json_assert auth_me "d.get('email') == \"$ADMIN\""
json_assert auth_me_csrf "bool(d.get('csrf_token'))"

JAR_OVERRIDE="$EMPTY_JAR" request GET /api/v1/auth/me
expect_status auth_unauthorized 401

NO_CSRF=1 request POST /api/v1/clients '{"name":"acceptance-csrf-probe"}'
expect_status auth_csrf_required 403
error_code_is auth_csrf_required_code csrf_failed

request GET /api/v1/auth/sessions
expect_status auth_sessions 200
json_assert auth_sessions "isinstance(d, list) and any(s.get('current') for s in d)"

# ---------------------------------------------------------------------------
# 3. Серверы, события, инциденты, overview
# ---------------------------------------------------------------------------
request GET /api/v1/servers
expect_status servers_list 200
json_assert servers_list_shape "isinstance(d, list)"
json_assert servers_uptime_field "all('uptime_24h_pct' in s for s in d)"
ENTRY_ID=$(fetch_entry_id)

if [ "$CREATE_ENTRY" = "1" ]; then
  ACC_NAME="acc-entry-$(date +%s)"
  SERVER_JSON=$(python3 - "$ACC_NAME" <<'PY'
import json
import secrets
import sys

print(json.dumps({
    "name": sys.argv[1],
    "host": "127.0.0.1",
    "port": 22,
    "user": "root",
    "auth_type": "password",
    "password": secrets.token_urlsafe(16),
    "roles": "entry",
    "region": "acceptance",
}))
PY
)
  request POST /api/v1/servers "$SERVER_JSON"
  if [ "$HTTP_STATUS" = "201" ]; then
    CREATED_SERVER_ID=$(json_value server_id)
    ENTRY_ID="$CREATED_SERVER_ID"
    ok server_create_entry "server_id=$CREATED_SERVER_ID (job провижининга запущен; SSH-fail ожидаем)"
  else
    bad server_create_entry "HTTP $HTTP_STATUS"
  fi
elif [ -n "$ENTRY_ID" ]; then
  ok server_existing_entry "entry server_id=$ENTRY_ID"
else
  skip server_entry "entry-узлов нет; link/profile/qr/sub/apply будут проверены на деградацию"
fi

request GET "/api/v1/events?limit=20"
expect_status events_list 200
json_assert events_list_shape "isinstance(d, list)"
json_assert events_fields "all('kind' in e and 'ts' in e for e in d)"

request GET /api/v1/incidents
expect_status incidents_list 200
json_assert incidents_list_shape "isinstance(d, list)"

request GET /api/v1/metrics/overview
expect_status metrics_overview 200
json_assert metrics_overview_shape "d.get('nodes') is not None and 'service_uptime' in d and 'routing_enabled' in d"

request GET /api/v1/jobs?limit=10
expect_status jobs_list 200
json_assert jobs_list_shape "isinstance(d, list)"

# ---------------------------------------------------------------------------
# 4. Выходы (external, creds сохраняются), сайты, RU-lock, профиль, check
# ---------------------------------------------------------------------------
RUN_ID="$(date +%s)-$$"
EXIT_LABEL="acc-$(date +%s)-$$"
EXIT_LABEL="${EXIT_LABEL:0:24}"
EXIT_JSON=$(python3 - "$EXIT_LABEL" <<'PY'
import json
import sys

print(json.dumps({
    "kind": "external",
    "label": sys.argv[1],
    "protocol": "http",
    "endpoint_host": "127.0.0.1",
    "endpoint_port": 3128,
    "region": "US",
    "username": "acceptance-user",
    "password": "acceptance-pass",
    "expected_exit_ip": "203.0.113.99",
    "bandwidth_budget_bytes": 1048576,
}))
PY
)
request POST /api/v1/exits "$EXIT_JSON"
expect_status exit_create 201
EXIT_ID=$(json_value id)
json_assert exit_create_shape "d.get('kind') == 'external' and d.get('label') == \"$EXIT_LABEL\" and d.get('enabled') is True"

if [ -n "$EXIT_ID" ]; then
  request PATCH "/api/v1/exits/$EXIT_ID" '{"region":"EU","bandwidth_budget_bytes":2097152}'
  expect_status exit_update 200
  json_assert exit_update_shape "d.get('region') == 'EU' and d.get('bandwidth_budget_bytes') == 2097152"

  request POST "/api/v1/exits/$EXIT_ID/check"
  expect_status exit_check 200
  json_assert exit_check_job "int(d.get('job_id', 0)) > 0"
else
  bad exit_update "exit id не получен"
  bad exit_check "exit id не получен"
fi

request GET /api/v1/sites/presets
expect_status sites_presets 200
json_assert sites_presets_anthropic "isinstance(d, list) and any(p.get('key') == 'anthropic' for p in d)"

SITE_JSON=$(python3 - "$EXIT_ID" <<'PY'
import json
import sys

print(json.dumps({
    "preset_key": "anthropic",
    "name": "acceptance-anthropic",
    "allowed_exit_ids": [int(sys.argv[1])],
}))
PY
)
request POST /api/v1/sites "$SITE_JSON"
expect_status site_create 201
SITE_ID=$(json_value id)
json_assert site_create_shape "d.get('match_type') == 'domain' and 'anthropic.com' in (d.get('values') or []) and $EXIT_ID in (d.get('allowed_exit_ids') or [])"

RU_PROBE='{"name":"acceptance-ru","match_type":"domain","values":["yandex.ru"],"allowed_exit_ids":['"$EXIT_ID"']}'
request POST /api/v1/sites "$RU_PROBE"
expect_status site_ru_locked 400
error_code_is site_ru_locked_code ru_direct_locked

request GET /api/v1/routing/profile
expect_status routing_profile_get 200
json_assert routing_profile_fields "'updated_at' in d and 'default_exit_ids' in d and 'enabled' in d"
cp "$BODY" "$WORK/orig-profile.json"

PROFILE_JSON=$(python3 - "$EXIT_ID" <<'PY'
import json
import sys

print(json.dumps({"default_exit_ids": [int(sys.argv[1])], "enabled": True}))
PY
)
request PUT /api/v1/routing/profile "$PROFILE_JSON"
expect_status routing_profile_put 200
json_assert routing_profile_put_shape "d.get('enabled') is True and [int(x) for x in d.get('default_exit_ids', [])] == [$EXIT_ID]"

request POST /api/v1/routing/check '{"domain":"anthropic.com"}'
expect_status routing_check_site_rule 200
json_assert routing_check_site_rule_shape "d.get('kind') == 'site_rule' and $EXIT_ID in d.get('exit_ids', [])"

request POST /api/v1/routing/check '{"domain":"example.com"}'
expect_status routing_check_default 200
json_assert routing_check_default_shape "d.get('kind') == 'default' and d.get('exit_ids') == [$EXIT_ID]"

request POST /api/v1/routing/check '{"domain":"yandex.ru"}'
expect_status routing_check_ru 200
json_assert routing_check_ru_shape "d.get('kind') == 'ru_direct' and d.get('exit_ids') == []"

# ---------------------------------------------------------------------------
# 5. Клиенты: создание, список (sub_token/last_seen_at), link/profile/qr
# ---------------------------------------------------------------------------
CLIENT_NAME="acc-client-$(date +%s)-$$"
CLIENT_JSON=$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1]}))' "$CLIENT_NAME")
request POST /api/v1/clients "$CLIENT_JSON"
expect_status client_create 201
CLIENT_ID=$(json_value id)
SUB_TOKEN=$(json_value sub_token)
json_assert client_create_shape "d.get('name') == \"$CLIENT_NAME\" and bool(d.get('uuid')) and bool(d.get('sub_token'))"

request GET /api/v1/clients
expect_status clients_list 200
json_assert clients_list_shape "isinstance(d, list)"

if [ -n "$CLIENT_ID" ]; then
  CLIENT_ROW=$(python3 - "$BODY" "$CLIENT_ID" "$SUB_TOKEN" <<'PY'
import json
import sys

try:
    clients = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
cid = int(sys.argv[2])
token = sys.argv[3]
for client in clients:
    if client.get("id") == cid:
        ok = (client.get("sub_token") == token and "last_seen_at" in client
              and "traffic_bytes" in client)
        print("ok" if ok else "bad")
        break
else:
    print("missing")
PY
)
  case "$CLIENT_ROW" in
    ok) ok clients_row_fields "sub_token/last_seen_at/traffic_bytes на месте" ;;
    missing) bad clients_row_fields "созданный клиент не найден в списке" ;;
    *) bad clients_row_fields "поля sub_token/last_seen_at отсутствуют" ;;
  esac

  request GET "/api/v1/clients/$CLIENT_ID/stats"
  expect_status client_stats 200
  json_assert client_stats_shape "'24h' in (d.get('traffic') or {}) and '30d' in (d.get('traffic') or {})"
else
  bad client_stats "клиент не создан"
fi

if [ -n "$ENTRY_ID" ] && [ -n "$CLIENT_ID" ]; then
  request GET "/api/v1/clients/$CLIENT_ID/link"
  expect_status client_link 200
  json_assert client_link_url "str(d.get('url', '')).startswith('vless://') and 'security=reality' in d.get('url', '')"

  request GET "/api/v1/clients/$CLIENT_ID/profile?mode=smart"
  expect_status client_profile_smart 200
  body_contains client_profile_ru "geosite-category-ru"
  body_contains client_profile_direct "geoip-ru"

  request GET "/api/v1/clients/$CLIENT_ID/profile?mode=vless"
  expect_status client_profile_vless 200
  body_contains client_profile_vless_body "vless://"

  request GET "/api/v1/clients/$CLIENT_ID/qr.svg?mode=smart"
  expect_status client_qr 200
  case "$RESP_CT" in
    image/svg+xml*) ok client_qr_type "image/svg+xml" ;;
    *) bad client_qr_type "content-type: $RESP_CT" ;;
  esac
  body_contains client_qr_svg "<svg"
else
  skip client_link "нет entry-узла"
  skip client_profile "нет entry-узла"
  skip client_qr "нет entry-узла"
fi

# ---------------------------------------------------------------------------
# 6. Apply ревизии и получение конфигов
# ---------------------------------------------------------------------------
request POST /api/v1/routing/apply
if [ -n "$ENTRY_ID" ]; then
  if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "202" ]; then
    REVISION_ID=$(json_value revision_id)
    if [ -n "$REVISION_ID" ]; then
      ok routing_apply "revision_id=$REVISION_ID"
      request GET "/api/v1/routing/revisions/$REVISION_ID"
      expect_status revision_get 200
      json_assert revision_fields "'applied_at' in d and 'configs' in d and 'status' in d"
      REVISION_ENTRY=$(python3 - "$BODY" "$ENTRY_ID" <<'PY'
import json
import sys

try:
    revision = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("bad")
    raise SystemExit(0)
configs = revision.get("configs") or {}
config = configs.get(str(sys.argv[2]))
if not config:
    print("bad")
elif isinstance(config.get("inbounds"), list) and config.get("inbounds"):
    print("ok")
else:
    print("bad")
PY
)
      case "$REVISION_ENTRY" in
        ok) ok revision_entry_config "configs содержит entry-конфиг" ;;
        *) bad revision_entry_config "нет конфига entry-сервера $ENTRY_ID в ревизии" ;;
      esac
    else
      bad routing_apply "в ответе нет revision_id"
    fi
  else
    bad routing_apply "HTTP $HTTP_STATUS (ожидался 200/202, entry есть)"
    usage_hint
  fi
else
  if [ "$HTTP_STATUS" = "409" ]; then
    ok routing_apply "ожидаемая деградация без entry-узлов: HTTP 409"
    error_code_is routing_apply_code no_entry_servers
  else
    bad routing_apply "HTTP $HTTP_STATUS (без entry-узлов ожидался 409 no_entry_servers)"
    usage_hint
  fi
fi

request GET /api/v1/routing/revisions
expect_status revisions_list 200
json_assert revisions_fields "isinstance(d, list) and all('applied_at' in r and 'status' in r for r in d)"

# ---------------------------------------------------------------------------
# 7. Подписка /sub/{token}: sing-box, clash, v2ray, невалидный токен
# ---------------------------------------------------------------------------
if [ -n "$SUB_TOKEN" ] && [ -n "$ENTRY_ID" ]; then
  EXTRA_CURL=(-H "User-Agent: sing-box/1.11.0")
  request GET "/sub/$SUB_TOKEN"
  expect_status sub_singbox 200
  body_contains sub_singbox_ru "geosite-category-ru"

  EXTRA_CURL=(-H "User-Agent: clash-verge/2.0")
  request GET "/sub/$SUB_TOKEN"
  expect_status sub_clash 200
  body_contains sub_clash_body "proxies:"

  EXTRA_CURL=(-H "User-Agent: v2rayN/6.0")
  request GET "/sub/$SUB_TOKEN"
  expect_status sub_v2ray 200
  if python3 - "$BODY" >/dev/null 2>&1 <<'PYV2'
import base64
import sys

raw = open(sys.argv[1], "rb").read().strip()
try:
    decoded = base64.b64decode(raw, validate=True).decode("utf-8")
except Exception:
    sys.exit(1)
sys.exit(0 if decoded.startswith("vless://") else 1)
PYV2
  then
    ok sub_v2ray_body "base64 от vless://"
  else
    bad sub_v2ray_body "тело не является base64-подпиской vless://"
  fi

  request GET "/sub/invalid-$RUN_ID"
  expect_status sub_invalid 404
  error_code_is sub_invalid_code subscription_not_found
else
  skip sub_singbox "нет entry-узла/клиента"
  skip sub_clash "нет entry-узла/клиента"
  skip sub_v2ray "нет entry-узла/клиента"
fi

# ---------------------------------------------------------------------------
# 8. Agent API без токена + logout
# ---------------------------------------------------------------------------
request POST /agent/v1/heartbeat '{}'
expect_status agent_heartbeat_unauthorized 401
request GET /agent/v1/config
expect_status agent_config_unauthorized 401

# Явная уборка временных ресурсов до logout (пока сессия/CSRF валидны).
cleanup
ok cleanup "временные ресурсы удалены"

request POST /api/v1/auth/logout
expect_status auth_logout 204
request GET /api/v1/auth/me
expect_status auth_logout_me 401

# ---------------------------------------------------------------------------
# Итог
# ---------------------------------------------------------------------------
printf '\nacceptance: PASS=%s FAIL=%s SKIP=%s\n' "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
  printf 'acceptance: FAILED\n' >&2
  exit 1
fi
printf 'acceptance: OK\n'
exit 0
