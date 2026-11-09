# INTERFACES — контракт реализации

Документ для параллельной разработки. Описывает границы владения файлами, окружение,
и точные интерфейсы между backend, compiler, agent, frontend и deploy.
Источник требований — `openspec/changes/add-vpn-control-panel/` (proposal, design, specs).

## 0. Окружение и инструменты

Рабочий каталог (WSL Ubuntu): `/home/dev/vpn`. Из Windows доступен как \\\\wsl.localhost\\Ubuntu\\home\\dev\\vpn.

- Прямого интернета из WSL **нет** (fake-IP DNS). Скачивание пакетов — только через Windows-сторону.
- Windows: `curl.exe`, `python`, `npm.cmd`, `tar.exe` работают и имеют доступ в интернет.
- Всё скачанное складывается в `C:\\Users\\pulid\\vpn-deps\\` и доступно из WSL как
  `/mnt/c/Users/pulid/vpn-deps/`.
- Готовые артефакты (после `bash .tmp/setup_tools.sh`):
  - `.tools/xray/xray` — Xray-core 26.11.9 (linux/amd64);
  - `.tools/geoip.dat`, `.tools/geosite.dat` — geodata;
  - `.tools/README.md` — откуда взято.
- Python: `/home/dev/vpn/.venv` (создаётся Lead'ом из manylinux-колёс).
  Запуск тестов: `.venv/bin/pytest`, линт: `.venv/bin/ruff`.
- Go: `go1.27.3` в WSL, сборка offline, **без внешних модулей** (только stdlib).
- Node: `node v22.22.1` в WSL; `node_modules` ставятся через Windows npm с linux-платформой
  (инструкция будет от Lead'а) либо проверяется `node --check`/tsc из WSL.
- **Не запускать `git` команды** — коммиты делает только Lead.
- Долгие bash-команды писать в файл `.tmp/*.sh` и запускать
  `wsl -d Ubuntu -- bash /home/dev/vpn/.tmp/xxx.sh` (PowerShell 5.1 ломает кавычки и `&&`).
- Секреты: `deploy/.env` не читать/не менять/не логировать; в тестах и fixtures — только фейковые значения.

## 1. Владение файлами (write scopes)

| Зона | Владелец | Пути |
|---|---|---|
| Backend core + API | Lead | `backend/**` (кроме compiler), `docs/INTERFACES.md` |
| Xray compiler | xray-compiler | `backend/app/compiler/**`, `backend/tests/compiler/**` |
| Node agent | node-agent | `agent/**` |
| Frontend | panel-ui | `frontend/**` |
| Deploy/docs | deploy-ops | `deploy/**` (кроме `deploy/.env`), `Makefile`, `README.md`, `SETUP.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `.github/**`, `.dockerignore` |

Пересечения запрещены. Если нужен файл вне зоны — сообщение Lead'у, не править самому.

## 2. Backend — структура и соглашения

```
backend/
  pyproject.toml
  alembic.ini
  app/
    main.py               # create_app(), роутеры, /healthz, статика SPA
    core/config.py        # Settings (pydantic-settings) -> get_settings()
    core/db.py            # async engine, async_sessionmaker, get_session()
    core/security.py      # argon2id, opaque-сессии, CSRF
    core/crypto.py        # Fernet (MultiFernet), encrypt/decrypt секретов
    core/logging.py       # JSON-логи в stdout, redaction
    models/*.py           # SQLAlchemy 2 (Declarative), UTC
    schemas/*.py          # Pydantic v2 DTO запросов/ответов
    api/*.py              # роутеры /api/v1, /agent/v1, /healthz, /metrics, /sub
    services/*.py         # jobs, metrics, healthcheck, links, profiles, provisioning/
    compiler/             # xray-compiler (см. §3)
  tests/
    compiler/             # xray-compiler
    test_*.py             # Lead
  alembic/versions/
```

Конвенции:
- Python 3.12+ (локально 3.14), FastAPI, Pydantic v2, SQLAlchemy 2 async + aiosqlite.
- API версии: админ `/api/v1/...`, агент `/agent/v1/...`, публично только `/healthz` и `POST /api/v1/auth/login`.
- Время в БД — UTC ISO-8601. Идентификаторы — int PK.
- Конфиг Xray генерируется детерминированно (см. §3).
- Секреты шифруются Fernet и никогда не возвращаются API/логами.
- Ошибки API: `{"error": {"code": "...", "message": "..."}}` (HTTP 4xx/5xx).

### 2.1 Переменные окружения (core/config.py)

Обязательные: `ADMIN`, `PASSWORD` (bootstrap; при непустой БД не перезаписывается), `SECRET_KEY`, `ENCRYPTION_KEY`.
Опциональные: `DATABASE_URL` (default `sqlite+aiosqlite:////data/app.db`; для локальной разработки — `sqlite+aiosqlite:///./app.db`),
`PUBLIC_BASE_URL`, `METRICS_TOKEN`, `SESSION_TTL_SECONDS`=604800, `SESSION_COOKIE_SECURE`=true, `TZ`=UTC,
`XRAY_VERSION`=26.11.9, `AGENT_VERSION`, `ARTIFACTS_DIR`, `DATA_DIR`=/data, `LOG_LEVEL`.

### 2.2 Модели БД (минимум)

- `users`: id, email unique, password_hash, must_change_password bool, created_at, last_login_at.
- `sessions`: id, user_id, token_hash unique (sha256), csrf_token, created_at, last_seen_at, expires_at, revoked_at, ip, user_agent.
- `servers`: id, name unique, host, ssh_port, ssh_user, auth_type(`password|key`), secret_enc (Fernet), host_fingerprint,
  roles(`entry|exit|both`), region, status(`new|provisioning|online|degraded|offline|delete_pending|failed`),
  agent_token_hash, agent_version, xray_version, last_seen_at, revision_id, base_dir(`/root/config/vpn` или `~/config/vpn`), created_at.
- `server_secrets`: server_id PK/FK, reality_private_key_enc, reality_public_key, short_ids_json, interconnect_uuid_enc, real_dest, real_sni.
- `exits`: id, kind(`node|external`), server_id FK nullable, label(slug unique), region, endpoint_host, endpoint_port,
  protocol(`vless_reality|http|socks5`), secret_enc nullable, expected_exit_ip, bandwidth_budget_bytes, used_bytes,
  enabled bool, health(`up|down|unknown`), last_check_at, last_delay_ms.
- `clients`: id, name unique, uuid unique, email_tag unique, enabled bool, traffic_limit_bytes nullable, sub_token unique, created_at.
- `site_rules`: id, name, match_type(`domain|wildcard|regexp|geosite`), values_json, allowed_exit_ids_json (ordered),
  preset_key nullable, enabled bool, position int, created_at.
- `routing_profile`: id=1 singleton, default_exit_ids_json (ordered), enabled bool, updated_at.
- `routing_revisions`: id, created_at, profile_json, config_hashes_json, status(`pending|applied|failed`), applied_at, error.
- `server_configs`: id, server_id, revision_id, config_json, sha256, applied_at, result, error.
- `jobs`: id, type, server_id nullable, status(`queued|running|succeeded|failed|cancelled`), progress int, payload_json,
  result_json, error, created_at, started_at, finished_at.
- `job_events`: id, job_id, seq int, ts, level, step, message, data_json.
- `metrics_buckets`: id, server_id, client_id nullable, bucket_ts, resolution(`15s|1m|5m|1h`), up_bytes, down_bytes, conns, errors_json.
- `incidents`: id, severity(`info|warning|critical`), kind, subject, status(`open|ack|resolved`), details_json, created_at, resolved_at.
- `events`: id, ts, kind, actor, ip, user_agent, payload_json (аудит; без секретов).

### 2.3 Админ-API (cookie-сессия + CSRF)

Формат успешных ответов — объекты/массивы по смыслу; ошибки — `{"error":{"code","message"}}`.

- `POST /api/v1/auth/login` `{email,password}` → `{user:{email}, csrf_token}` + cookie сессии (HttpOnly).
- `POST /api/v1/auth/logout`; `GET /api/v1/auth/me` → `{email, csrf_token, weak_password}`;
  `POST /api/v1/auth/password` `{current_password,new_password}`.
- `GET /api/v1/servers` → `[{id,name,host,roles,region,status,agent_version,xray_version,last_seen_at,revision_id,uptime_24h_pct,traffic_24h}]`.
- `POST /api/v1/servers/preflight` `{host,port,user,auth_type,password?,private_key?}` → `{ok, os, arch, sudo, port_443_free, existing_install, host_fingerprint, warnings:[]}`.
- `POST /api/v1/servers` `{name,host,port,user,auth_type,password?,private_key?,roles,region}` → `{server_id, job_id}` (статус provisioning).
- `GET /api/v1/servers/{id}`; `POST /api/v1/servers/{id}/actions` `{action:"reapply"|"restart"|"reinstall"|"diagnose"}` → `{job_id}`.
- `DELETE /api/v1/servers/{id}` `{confirm_name}` → `{job_id}`; force: `?force=true`.
- `GET|POST /api/v1/exits`, `PATCH|DELETE /api/v1/exits/{id}`, `POST /api/v1/exits/{id}/check`.
- `GET /api/v1/sites`, `POST /api/v1/sites`, `PATCH|DELETE /api/v1/sites/{id}`, `GET /api/v1/sites/presets`.
- `GET|PUT /api/v1/routing/profile`, `POST /api/v1/routing/apply` → `{revision_id, job_id}`,
  `POST /api/v1/routing/check` `{domain}` → `{kind, rule_id, exit_ids, compiled_rule, explanation}`,
  `GET /api/v1/routing/revisions`, `GET /api/v1/routing/revisions/{id}`.
- `GET|POST /api/v1/clients`, `PATCH|DELETE /api/v1/clients/{id}`,
  `GET /api/v1/clients/{id}/link?server_id=` → `{url}`, `GET /api/v1/clients/{id}/qr.svg?server_id=`,
  `GET /api/v1/clients/{id}/profile?mode=smart|vless`, `GET /api/v1/clients/{id}/stats`.
- `GET /api/v1/metrics/overview`, `GET /api/v1/metrics/traffic?range=24h|7d|30d&group_by=server|client`,
  `GET /api/v1/metrics/node/{id}?range=`, `GET /api/v1/metrics/client/{id}?range=`.
- `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/stream` (SSE: `event: progress|done|error`, id=seq), `GET /api/v1/jobs?server_id=`.
- `GET /api/v1/incidents`, `POST /api/v1/incidents/{id}/ack`, `POST /api/v1/incidents/{id}/resolve`.
- `GET /healthz` → `{status:"ok",db:"ok",version}`; `GET /metrics` (Bearer `METRICS_TOKEN`); `GET /sub/{token}` (публичный).

### 2.4 Agent API (Bearer agent-token; панель хранит sha256)

- `POST /agent/v1/register` `{agent_version,xray_version,hostname,os,arch,capabilities}[]` → `{server_id, revision:"pending"}`.
- `POST /agent/v1/heartbeat` `{revision, xray:{active,uptime_s,version,pid}, config_sha256, units:{xray:"active",agent:"active"}, degraded_reason?}`
  → `{revision, apply_pending:bool, commands:[{id,type,payload}]}` — команды: `reapply`, `restart_services`, `diagnose`, `uninstall`, `update_agent`.
- `GET /agent/v1/config?revision=N` → 200 `{revision, config, sha256}` | 204 (нет изменений).
- `POST /agent/v1/apply-result` `{revision, status:"applied"|"failed", error?, log_tail?, sha256?}`.
- `POST /agent/v1/metrics` (батч по §4) → `{ok:true, accepted:true, commands:[...]}`.
- `GET /agent/v1/artifacts/{name}` (agent token) → binary + `X-Checksum-Sha256`.
- `POST /agent/v1/diagnose` `{report:{...}}`; `POST /agent/v1/uninstall` `{status:"done"}` (до выхода агента).
- `POST /agent/v1/command-result` `{command_id, status, output}`.

## 3. Compiler (владелец: xray-compiler)

Пакет `backend/app/compiler`, публичный API через `backend/app/compiler/__init__.py`:

```python
# types.py — frozen dataclasses
class ExitOutboundSpec:      # kind: "vless_reality" | "http" | "socks5"
    kind: str
    host: str; port: int
    uuid: str | None = None
    public_key: str | None = None       # REALITY pbk
    short_id: str | None = None
    server_name: str | None = None
    fingerprint: str = "chrome"
    spider_x: str = "/"
    username: str | None = None
    password: str | None = None
    expected_exit_ip: str | None = None

class ExitSpec:
    id: int; label: str          # slug [a-z0-9-]{1,24}, уникален
    region: str
    outbound: ExitOutboundSpec
    enabled: bool = True
    health: str = "up"           # up|down|unknown

class SiteRuleSpec:
    id: int; name: str
    match_type: str              # domain|wildcard|regexp|geosite
    values: tuple[str, ...]
    allowed_exit_ids: tuple[int, ...]   # order = priority
    enabled: bool = True; position: int = 0

class ClientSpec:
    uuid: str; email: str; flow: str = "xtls-rprx-vision"

class RealityInboundSpec:
    listen: str; port: int; dest: str
    server_names: tuple[str, ...]
    private_key: str
    short_ids: tuple[str, ...]
    flow: str = "xtls-rprx-vision"

class EntryNodeSpec:
    server_id: int; public_host: str
    inbound: RealityInboundSpec
    base_dir: str = "~/config/vpn"

class EntryPeerSpec:         # кто из entry может ходить на exit
    entry_server_id: int; uuid: str; email: str

class ExitNodeSpec:
    server_id: int
    inbound: RealityInboundSpec

class RoutingSpec:
    exits: tuple[ExitSpec, ...]
    default_exit_ids: tuple[int, ...]
    site_rules: tuple[SiteRuleSpec, ...]
    enabled: bool = True
    dns_servers: tuple[str, ...] = ("https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query")

class EntryCompileInput:
    node: EntryNodeSpec; clients: tuple[ClientSpec, ...]; routing: RoutingSpec

class ExitCompileInput:
    node: ExitNodeSpec; peers: tuple[EntryPeerSpec, ...]

# функции
def compile_entry_config(inp: EntryCompileInput) -> dict: ...
def compile_exit_config(inp: ExitCompileInput) -> dict: ...
def canonical_json(obj: object) -> str    # json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)
def sha256_json(obj: object) -> str       # sha256(canonical_json(obj).encode()).hexdigest()
def match_route(domain: str, routing: RoutingSpec) -> RouteDecision
def validate_routing(routing: RoutingSpec) -> list[ProfileIssue]
```

`RouteDecision`: `kind: "ru_direct"|"site_rule"|"default"|"block"`, `rule_id: int|None`,
`exit_ids: tuple[int,...]`, `compiled_rule: dict|None`, `explanation: str`.
`ProfileIssue`: `code: str; message: str; subject: str|None`.

Требования к entry-конфигу (порядок правил фиксирован):
1. `{"type":"field","ip":["geoip:private","169.254.169.254/32"],"outboundTag":"block"}`
2. по одному правилу на каждый enabled site rule (порядок: position, затем id):
   - домены: `domain` (match_type domain → `domain:<v>`, wildcard → `domain:<v без *>`, geosite → `geosite:<v>`, regexp → `regexp:<v>`);
   - если среди разрешённых выходов есть доступные (enabled): `balancerTag: "bal-rule-<id>"`;
   - если ни одного доступного: `outboundTag: "block"` (fail-closed);
   - в правиле не должно быть geosite/AI-доменов уходящих в direct.
3. `{"type":"field","domain":["geosite:category-ru"],"outboundTag":"direct"}` (locked);
4. `{"type":"field","ip":["geoip:ru"],"outboundTag":"direct"}`;
5. catch-all: `{"type":"field","network":"tcp,udp","balancerTag":"bal-default"}` (если default exits есть) иначе `outboundTag:"block"`;
6. финальное `{"type":"field","network":"tcp,udp","outboundTag":"block"}`.

Первым outbound всегда `{"tag":"block","protocol":"blackhole"}`, затем `direct` (freedom), затем выходы:
default — тег `exit-<label>`, для правил — `rule-<rule_id>-<label>`. Балансировщики:
`bal-default` selector `["exit-"]`, `bal-rule-<id>` selector `["rule-<id>-"]`; у каждого `fallbackTag:"block"`, strategy `leastPing`.
observatory: `subjectSelector:["exit-","rule-"]`, probeUrl `https://www.gstatic.com/generate_204`, probeInterval 30s.
inbound: VLESS+REALITY (TCP, flow xtls-rprx-vision), sniffing destOverride `["http","tls","quic"]` routeOnly true.
api/stats/policy/log — по design.md §6.1; `log.access` = `<base_dir>/log/access.log`, `log.error` = `<base_dir>/log/xray-error.log`;
api.listen `127.0.0.1:10085`, services HandlerService, LoggerService, StatsService.
dns: DoH-серверы из RoutingSpec (если конфиг ядра не принимает — зафиксировать в README и тесте).
Если `routing.enabled == false`: inbound с клиентами есть, но все правила → block (нет выхода наружу).

Exit-конфиг: inbound VLESS+REALITY на порту 18443 (свой dest/ключи/shortIds), clients = peers,
outbounds `[{"tag":"direct","protocol":"freedom"}]`, routing `{"domainStrategy":"AsIs","rules":[]}`.

Детерминизм: одинаковый вход → байт-идентичный JSON (порядок списков фиксирован правилами выше).
Golden-тесты (python, pytest): RU→direct; RU-IP→direct; Anthropic→только allowlist-балансировщик;
все выходы правила down → block; нет default → block-all; private/metadata→block; порядок правил;
отсутствие direct-утечек; детерминизм; + опционально `xray run -test` (`XRAY_BIN` = `.tools/xray/xray`,
`XRAY_LOCATION_ASSET` = `.tools`) для entry и exit конфигов.

## 4. Agent (владелец: node-agent)

Go-модуль `agent/`, **только stdlib**, `go build ./...` и `go test ./...` offline.
Раскладка: `agent/cmd/vpn-agent/main.go`, `agent/internal/{config,api,apply,metrics,uninstall,runner,state}`.
Конфиг `<base>/etc/agent.json`: `{panel_url, token, server_id, base_dir, heartbeat_interval_s:30, config_poll_s:10, metrics_interval_s:15}`.
Пути состояния: `bin/xray`, `bin/vpn-agent`, `etc/xray.json`, `etc/agent.json`, `log/access.log`, `log/xray-error.log`,
`state/state.json`, `state/metrics-buffer.jsonl`, `state/apply.log`.
Флаги: `serve` (default), `uninstall --yes`, `diagnose`, `version`.
Атомарное применение: записать `xray.json.new` → `xray run -test -c` → backup → rename → `systemctl restart xray` →
проверить `is-active` + локальный health → при ошибке вернуть backup и рестарт; отчёт `/agent/v1/apply-result`.
Метрики: `xray api statsquery --server=127.0.0.1:10085 -pattern user>>>` (JSON в stdout), `statsonlineiplist`
(если поддерживается — иначе online по last_seen), `GetSysStats` через `xray api` недоступен → uptime процесса из `systemctl show`.
Кумулятивные значения → дельты по имени; `reset=true` не использовать; состояние дельт в `state/state.json`;
буфер при offline ≤24ч, после восстановления `replay:true`. accepted/blocked/rejected — parse `log/access.log` (offset).
Трафик: `user>>>EMAIL>>>traffic>>>uplink|downlink`, только они идут в квоты.
systemd unit (шаблон в `agent/packaging/vpn-agent.service`): NoNewPrivileges, ProtectSystem=strict,
ProtectHome=no, ReadWritePaths=<base>, PrivateTmp=yes, Restart=always, UMask=0077.
Uninstall: guard realpath, stop/disable units, rm unit files, daemon-reload/reset-failed, снять nft/ufw правила,
rm -rf base; вернуть 0 и отчёт.
Тесты: httptest-панель (register/heartbeat/config/apply/metrics), fake `xray` shell-скрипт для apply/rollback,
дельта-логика, парсинг access-лога, uninstall-guard. Проверить `GOOS=linux GOARCH=arm64 go build` и amd64.

## 5. Frontend (владелец: panel-ui)

`frontend/` — Vite + React 19 + TS + TanStack Router (file-based) + Query + Tailwind v4 + shadcn/ui + Recharts + qrcode.react.
Русский UI, light/dark, lazy-маршруты, начальный gzip < 250 КБ. Страницы: Login, Dashboard, Servers (+wizard SSE),
Exits, Sites, Clients, Settings. API-клиент: fetch same-origin `/api/v1`, `credentials:"include"`, `X-CSRF-Token` для мутаций,
401 → `/login`. SSE-прогресс job'ов. API ещё не готов — использовать типы из §2.3 и мок-режим только для dev.

## 6. Deploy (владелец: deploy-ops)

- `deploy/Dockerfile`: multi-stage (node build SPA → python:3.12-slim, копирует `frontend/dist` в `backend/app/static`),
  entrypoint: `alembic upgrade head` → `uvicorn app.main:app`. Один контейнер.
- `deploy/docker-compose.yml`: сервис `app`, `env_file: .env`, том `panel-data:/data`, `expose 8000`, healthcheck,
  внешняя сеть `dokploy-network` (external), опциональный `db` под profile `postgres`.
- `Makefile` (dev/test/lint/build/agent), `README.md`, `SETUP.md` (главный файл конечной настройки), `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`,
  `.github/workflows/ci.yml` (ruff+mypy+pytest, tsc+build, go test+build linux/amd64+arm64, secret-scan).
- `deploy/.env.example`: только плейсхолдеры; `deploy/.env` не трогать.

## 7. Порядок работы

1. Взять задачу из общего борда (`team_task_list`), прочитать её и `team_task_update action="claim"`.
2. Прочитать `openspec/project.md`, design.md и свой раздел.
3. Реализовать код + тесты; прогнать проверки из acceptance criteria задачи.
4. Закоммитить ничего нельзя; по завершении `team_task_update action="complete"` и отправить Lead'у
   сообщение: что сделано, команды проверки и их результат, открытые вопросы/риски.
5. Секреты не выводить в чат/логи. Реальные значения `deploy/.env` не использовать в тестах.
