# Архитектура VPN Control Panel

Документ описывает устройство системы: компоненты, потоки данных, модель маршрутизации,
базу данных, безопасность и деплой. Контракт между командами/слоями — [INTERFACES.md](INTERFACES.md);
требования — `openspec/changes/add-vpn-control-panel/` (proposal, design, specs).

## 1. Назначение и принципы

Персональный self-hosted control-plane для парка VPS с Xray: добавляет узлы по SSH, разворачивает
на них Xray + агент, управляет выходами и per-site маршрутизацией, выдаёт клиентам VLESS+REALITY
и показывает трафик/ошибки/аптайм. Один администратор, без мультитенантности.

Принципы, заложенные в архитектуру:

1. **Fail-closed.** Если выход недоступен или не настроен — трафик блокируется. Никаких
   деградаций «через любой доступный выход» и «напрямую». Российский IP никогда не используется
   как выход.
2. **Один источник состояния на узле.** Всё установленное и настроенное — в `~/config/vpn`
   (`bin/`, `etc/`, `log/`, `state/`); удаление узла = удаление каталога + systemd-юнитов +
   firewall-правил.
3. **Панель — единственная точка настройки.** Маршрутизация, выходы, клиенты и конфиги
   генерируются на бэкенде из БД и применяются атомарно (validate → swap → restart →
   health-check → rollback).
4. **Узел автономен.** При недоступности панели Xray продолжает работать на последней
   применённой конфигурации; агент буферизует метрики до 24 ч.
5. **RU-трафик не проксируется.** В умном профиле российские домены/IP идут напрямую с
   устройства; в режиме полного туннеля (`vless://`) — напрямую с entry, но никогда через
   выходы.
6. **Детерминизм конфигов.** Одинаковое состояние БД → байт-идентичный JSON Xray
   (для ревизий, diff и воспроизводимости).

## 2. Компоненты

```
┌──────────────────────────── Dokploy (VPS панели) ─────────────────────────────┐
│  ┌────────────────────────────── container app ────────────────────────────┐  │
│  │  Traefik (80/443, LE TLS) ──▶ FastAPI                                   │  │
│  │     /api/v1/*  админ-API (cookie + CSRF)                                │  │
│  │     /agent/v1/*  API узлов (Bearer agent-token)                         │  │
│  │     /sub/{token}  подписка клиента (публичный, токен-капабилити)        │  │
│  │     /healthz  health-check, /metrics  Prometheus (Bearer METRICS_TOKEN) │  │
│  │     /  SPA (React, статика из app/static)                               │  │
│  │  APScheduler: heartbeat-monitor, health-check выходов, свёртка метрик   │  │
│  │  SQLite /data/app.db  +  /data/artifacts (xray, vpn-agent, geodata)     │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└───────────────┬──────────────────────────────────────────────┬───────────────┘
                │ SSH (asyncssh): preflight, provisioning,     │ HTTPS исходящими
                │ apply, diagnose, uninstall                   │ (register/heartbeat/
┌───────────────▼───────────────┐              ┌────────────────▼──────────────┐
│ entry-узел                    │              │ exit-узел                     │
│  systemd: xray.service        │  VLESS+      │  systemd: xray.service        │
│           vpn-agent.service   │  REALITY     │           vpn-agent.service   │
│  ~/config/vpn/{bin,etc,log,..}│  18443/tcp   │  ~/config/vpn/...             │
│  inbound 443/tcp VLESS+REALITY│ ───────────▶ │  inbound 18443/tcp            │
│  routing/rules/balancers      │              │  allowlist по IP entry-узлов  │
└───────────────┬───────────────┘              └───────────────┬───────────────┘
                │ VLESS+REALITY 443/tcp                        │ freedom
        ┌───────▼────────┐                      ┌──────────────▼──────────────┐
        │ клиент (device)│                      │ интернет / целевой сервис    │
        │ sing-box/Clash │                      │ (Anthropic, OpenAI, ...)     │
        └────────────────┘                      └─────────────────────────────┘
```

Внешние выходы (резидентские ISP-прокси) — альтернатива exit-узлу: entry открывает
HTTP CONNECT/SOCKS5 к провайдеру, TLS браузера остаётся сквозным, провайдер выпускает трафик
из своего гео. Панель проверяет фактический exit-IP и страну; при несовпадении/RU выход
помечается `down`, сайты с ним блокируются (fail-closed).

### 2.1 Backend (FastAPI)

```
backend/
  app/
    main.py            create_app(), middleware (request-id, JSON-логи, security-заголовки),
                       lifespan (engine, bootstrap админа, scheduler), SPA-fallback
    api/               auth, servers, exits, sites, routing, clients, jobs, agent,
                       subscription, health, metrics (Prometheus), events (аудит),
                       incidents (list/ack/resolve), deps (сессия/CSRF/agent-token), errors
    core/              config (pydantic-settings), db (async engine/session),
                       security (Argon2id, opaque-сессии, CSRF, токены), crypto
                       (Fernet/MultiFernet), logging (JSON + redaction), timeutils
    models/            SQLAlchemy 2 Declarative: auth, servers, routing, ops
    schemas/           Pydantic v2 DTO запросов/ответов
    services/          auth, audit, jobs (в БД + SSE), ssh (asyncssh), provisioning
                       (bootstrap узла), runner (раскатка ревизий), reality (x25519,
                       shortId), links (vless:// и клиентские профили), presets,
                       rate_limit, metrics_agg (свёртка/retention/uptime),
                       health_monitor (инциденты), scheduler_setup (APScheduler jobs)
    compiler/          детерминированная генерация конфигов Xray (см. §4)
    static/            сюда Dockerfile кладёт собранный frontend/dist
  alembic/             миграции (entrypoint: alembic upgrade head)
  tests/               pytest + golden-тесты компилятора
```

Фоновые задачи — in-process APScheduler (`max_instances=1`, `coalesce=True`) при одном
uvicorn-воркере: состояние задач и прогресс хранятся в БД (`jobs`, `job_events`), SSE-стрим
читает события из БД, поэтому переживает переподключение клиента.

### 2.2 Agent (`vpn-agent`)

Go-демон под systemd, **без входящих портов**, только исходящий HTTPS к панели.

```
agent/
  cmd/vpn-agent/        main: флаги serve | uninstall --yes | diagnose | version
  internal/
    config/             ~/config/vpn/etc/agent.json (0600), пути состояния
    api/                HTTP-клиент панели (register, heartbeat, config, metrics, ...)
    runner/             цикл: heartbeat 30s, poll конфига 10s, метрики 15s
    apply/              атомарное применение: xray -test → backup → swap → restart →
                        is-active/health → rollback при ошибке
    metrics/            StatsService (xray api), дельты кумулятивных счётчиков,
                        парсинг access/error-логов, буфер до 24 ч
    state/              state.json (ревизия, sha256, offset логов, базлайн счётчиков)
    systemd/, uninstall/, execx/, logs/, version/
  packaging/            шаблон vpn-agent.service
```

Конфиг агента: `{panel_url, token, server_id, base_dir, heartbeat_interval_s:30,
config_poll_s:10, metrics_interval_s:15}`. Токен — per-node Bearer, на панели хранится
только SHA-256. Пути: `bin/xray`, `bin/vpn-agent`, `etc/xray.json`, `etc/agent.json`,
`log/access.log`, `log/xray-error.log`, `state/*`.

Канал команд: в ответе `POST /agent/v1/heartbeat` панель отдаёт `commands`
(`reapply`, `restart_services`, `diagnose`, `uninstall`,
`update_agent`); агент выполняет их и репортит `POST /agent/v1/command-result`,
а результат диагностики/удаления — соответствующими эндпоинтами. Команда не теряется, пока
узел offline: она выбирается из очереди при следующем heartbeat.

### 2.3 Frontend (SPA)

React 19 + Vite + TypeScript + TanStack Router/Query + Tailwind v4 + shadcn/ui + Recharts +
qrcode.react. Русский UI, light/dark, ленивые маршруты. API-клиент: same-origin `/api/v1`,
`credentials: "include"`, `X-CSRF-Token` для мутаций, 401 → `/login`; прогресс
провижининга/применения — SSE (`/api/v1/jobs/{id}/stream`); графики — Query с инвалидацией.

Маршруты: `login`, `dashboard`, `servers` (+wizard, карточка), `exits`, `sites`,
`clients` (+карточка), `settings`.

### 2.4 Data plane (Xray-core)

Xray 26.11.9 на узлах, конфиг генерируется компилятором (`backend/app/compiler`):

- entry: inbound VLESS+REALITY (443/tcp, flow `xtls-rprx-vision`, sniffing
  `http/tls/quic` с `routeOnly: true`), outbounds `block` (первым) → `direct` → выходы,
  routing по §4, balancers `leastPing`, observatory (`generate_204` каждые 30 с),
  API/StatsService на `127.0.0.1:10085`, access/error-логи в `<base>/log/`.
- exit: inbound VLESS+REALITY на 18443/tcp (свои ключи/dest/shortIds), clients = entry-узлы,
  outbound `freedom`, firewall allowlist по IP entry-узлов.
- REALITY dest выбирается «живой» TLS 1.3/H2-домен в той же AS/юрисдикции; ключи
  (x25519), shortId и interconnect-UUID генерирует бэкенд, приватные части шифруются Fernet.
- Внешние выходы (HTTP CONNECT/SOCKS5) хранят креды (username/password) только в
  `exits.secret_enc` (Fernet); в API/logs не возвращаются. Health-монитор сверяет
  фактический exit-IP/страну с `expected_exit_ip`, при несовпадении/RU выход → `down`.

## 3. Потоки данных

### 3.1 Браузер администратора
`Traefik:443 → app:8000`. Сессия — HttpOnly cookie `__Host-vpn_session` (Secure,
SameSite=Lax), сервер хранит SHA-256 токена. Мутации требуют `X-CSRF-Token` из
`GET /api/v1/auth/me`. SSE-поток job'ов — через тот же origin; Traefik не буферизует
(heartbeat/keep-alive).

### 3.2 Провижининг узла
`POST /api/v1/servers/preflight` (SSH, ОС/arch/sudo/порты/наличие установки) →
`POST /api/v1/servers` создаёт запись и job → runner по SSH: каталоги `~/config/vpn`,
SFTP-загрузка `xray`, `vpn-agent`, `geoip.dat`/`geosite.dat` из `/data/artifacts`,
systemd-юниты (`Environment=XRAY_LOCATION_ASSET=<base>/etc`), firewall, запуск, регистрация.
`job_events`, UI читает SSE. Повторный провижининг идемпотентен; при ошибке — статус
`failed` с шагом и stderr без секретов.

### 3.3 Применение конфигурации (ревизии)
`PUT /api/v1/routing/profile` → `POST /api/v1/routing/apply`:
1. компиляция профиля для каждого entry-узла (детерминированно, SHA-256);
2. ревизия `routing_revisions` → `pending`, `server_configs` по узлам;
3. агенты при следующем poll (`GET /agent/v1/config?revision=N`) получают конфиг;
4. агент: `xray run -test -c candidate` → backup → atomic rename → `systemctl restart xray`
   → `is-active` + локальный health → `POST /agent/v1/apply-result`;
5. при провале — rollback на предыдущий файл и конфиг, ревизия `failed`, панель показывает
   причину; успешная ревизия — `applied`.

### 3.4 Метрики
Агент каждые 15 с собирает кумулятивные `user>>>email>>>traffic>>>uplink|downlink` через
StatsService, онлайн-клиентов/IP (`statsUserOnline`), uptime процесса, accepted/blocked из
access-лога, ошибки из error-лога. Считает дельты относительно прошлого среза (рестарт ядра
не даёт отрицательных значений), буферизует при offline до 24 ч и отправляет
`POST /agent/v1/metrics` (`replay:true` после восстановления). Панель нормализует время в
UTC, пишет `metrics_buckets` и сворачивает: 15s → 1m (24 ч) → 5m (30 д) → 1h (365 д).

Фоновые задачи APScheduler (in-process, один воркер):
`mark_offline` (узел без heartbeat >90 с → `offline` + инцидент),
`metrics_aggregate` (раз в 60 с: 15s → 1m → 5m → 1h), `metrics_purge`
(ретенция), `health_monitor` (каждые 30 с: инциденты `server_offline` /
`exit_down` с учётом allowlist сайтов; восстановление закрывает инцидент).
`uptime_24h_pct` в `GET /api/v1/servers` — покрытие 15-секундных бакетов за
24 ч; `service_uptime` в `/api/v1/metrics/overview` — по истории heartbeat.
API инцидентов: `GET /api/v1/incidents`, `POST .../{id}/ack|resolve`.
Журнал аудита: `GET /api/v1/events` (без секретов).

### 3.5 Клиентский трафик
1. Клиент → entry:443 VLESS+REALITY (маскировка под TLS к выбранному домену).
2. Entry: sniffing определяет домен (SNI/Host) даже у IP-соединений (`routeOnly`).
3. Правила (§4): private/metadata → block; site rule allowlist → balancer только разрешённых
   выходов; RU-домены/IP → direct; остальное → default exit; catch-all → block.
4. Exit-узел или внешний прокси выпускает трафик в интернет; ответ идёт обратно по цепочке.
5. RU-сайты в умном профиле вообще не входят в туннель (правила на устройстве клиента).

### 3.6 Подписка и QR
`GET /api/v1/clients/{id}/profile?mode=smart|vless` → sing-box JSON / Clash Meta YAML /
`vless://`; QR — `/qr.svg`. Публичная подписка `GET /sub/{token}`: персональный
неугадываемый токен (только SHA-256 в БД), формат выбирается по `User-Agent`, заголовки
`Subscription-Userinfo`/Profile-Title`, rate limit, токен отзывается вместе с клиентом.

## 4. Маршрутизация и компилятор Xray

Компилятор (`backend/app/compiler`, контракт — INTERFACES §3) — чистые функции без БД:

```
compile_entry_config(EntryCompileInput) -> dict
compile_exit_config(ExitCompileInput) -> dict
canonical_json(obj) -> str            # sort_keys, compact separators
sha256_json(obj) -> str
match_route(domain, RoutingSpec) -> RouteDecision   # та же логика, что в конфиге
validate_routing(routing) -> list[ProfileIssue]
```

Порядок правил entry зафиксирован:

1. `ip: ["geoip:private", "169.254.169.254/32"] → block` (защита LAN/метаданных);
2. по правилу на каждый enabled site rule (по `position`, затем `id`):
   allowlist-выходы → `balancerTag: bal-rule-<id>`; нет доступных выходов → `block`;
3. `domain: ["geosite:category-ru"] → direct` (locked: API отклоняет правила, перекрывающие RU);
4. `ip: ["geoip:ru"] → direct` (нужен `domainStrategy: IPIfNonMatch`);
5. catch-all `network: "tcp,udp" → balancer bal-default` (если default exits заданы), иначе block;
6. финальное `network: "tcp,udp" → block`.

Outbounds: первым всегда `block` (blackhole), затем `direct` (freedom), затем выходы.
Теги: `exit-<label>` для default, `rule-<rule_id>-<label>` внутри правил; балансировщики
`bal-default` (selector `exit-`) и `bal-rule-<id>` (selector `rule-<id>-`) со стратегией
`leastPing` и `fallbackTag: "block"`. Observatory проверяет `https://www.gstatic.com/generate_204`
каждые 30 с. Если routing выключен — inbound есть, но весь трафик блокируется.

Exit-конфиг: inbound 18443/tcp VLESS+REALITY (свои ключи), clients = entry-узлы,
outbounds `[direct]`, routing `AsIs`, пустые rules.

## 5. Модель данных

SQLite (`sqlite+aiosqlite:////data/app.db`, WAL, foreign_keys, busy_timeout=5000) или
PostgreSQL через `DATABASE_URL=postgresql+asyncpg://...`. Время — UTC.

| Таблица | Назначение |
|---|---|
| `users`, `sessions` | единственный админ (Argon2id) и серверные сессии (sha256 токена, CSRF, TTL, revoke) |
| `servers` | узлы: host/ssh, auth_type, secret_enc, host_fingerprint, roles (`entry/exit/both`), region, status, agent_token_hash, версии, last_seen, revision_id, base_dir |
| `server_secrets` | REALITY private/public, shortIds, interconnect UUID, dest/sni |
| `exits` | exit-узлы и внешние прокси: kind, label, region, endpoint, protocol, secret_enc, expected_exit_ip, budget/used, enabled, health, last_check |
| `clients` | VLESS-клиенты: uuid, email_tag (статистика Xray), sub_token, лимит, enabled |
| `site_rules` | match_type (domain/wildcard/regexp/geosite), values_json, allowed_exit_ids_json (ordered), preset_key, position |
| `routing_profile` | singleton id=1: default_exit_ids_json (ordered), enabled |
| `routing_revisions`, `server_configs` | ревизии профиля, скомпилированные конфиги по узлам, sha256, статус, ошибки |
| `jobs`, `job_events` | фоновые операции (провижининг, apply, uninstall, diagnose), прогресс/логи/SSE |
| `metrics_buckets` | трафик/соединения/ошибки по узлам и клиентам с разрешениями 15s/1m/5m/1h |
| `incidents`, `events` | инциденты (offline/exit down/apply failed) и аудит действий без секретов |

Секреты в БД — только Fernet-шифротекст (`secret_enc`, `reality_private_key_enc`,
`interconnect_uuid_enc`); в API/логи не попадают.

## 6. Безопасность

**Аутентификация и сессии.** Единственный админ создаётся при пустой БД из `ADMIN/PASSWORD`
(после этого `.env` на пароль не влияет). Argon2id, сравнение с dummy-хешем для
несуществующего пользователя. Opaque-токен 32 байта, в БД — SHA-256; cookie
`__Host-vpn_session; Secure; HttpOnly; SameSite=Lax; Path=/` (без Domain), ротация при входе,
idle/TTL, revoke при logout/смене пароля. CSRF: проверка Origin + double-submit
`X-CSRF-Token`. Rate limit login: 5/5 мин на IP с блокировкой 15 мин.

**Секреты.** SSH-пароли/ключи, REALITY private keys, interconnect UUID, токены агентов и
внешних прокси шифруются `ENCRYPTION_KEY` (Fernet/MultiFernet, поддерживает ротацию);
расшифровка — только в памяти на время операции. Redaction в логах по ключам
password/secret/token/authorization/cookie/private_key. `/metrics` защищён `METRICS_TOKEN`,
`/sub/{token}` — капабилити-токен с rate limit.

**Сеть.** Наружу только 80/443 (Traefik, LE TLS, HSTS при secure-cookie). Порт 8000 не
публикуется, БД — во внутренней сети `internal`, Docker socket в контейнер не монтируется.
CSP/security-заголовки ставит middleware FastAPI.

**Узлы.** Агент без входящих портов, исходящий HTTPS; systemd-hardening
(`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=no` + `ReadWritePaths=<base>`,
`PrivateTmp`, `UMask=0077`); секреты `0600`; firewall: 22 — по политике, 443 — только entry,
18443 — только IP entry-узлов; host-key pinning в SSH; uninstall-guard по realpath.

**Цепочка поставки.** Версии запинены (Xray 26.11.9, Python/npm/go lock-файлы); артефакты
узлов раздаются с SHA-256; CI выполняет secret-scan; `.env` в `.gitignore`, в
`.env.example` — только плейсхолдеры.

## 7. Деплой

Один контейнер `app` (multi-stage: node:24-alpine собирает SPA → python:3.12-slim),
non-root UID 10001, entrypoint: `alembic upgrade head` (с ретраями) → `uvicorn app.main:app
--workers 1`. Том `panel-data:/data` (SQLite + артефакты + бэкапы). Healthcheck —
`GET /healthz` (процесс + `SELECT 1`, без проверки удалённых узлов). Внешняя сеть
`dokploy-network` — для Traefik; опциональный `db` (PostgreSQL 18) под profile
`postgres` во внутренней сети. Лимиты: `mem_limit 512m`, `cpus 1.0`, `no-new-privileges`,
`stop_grace_period 30s`, ротация json-file логов.

Почему один воркер: in-process jobs/APScheduler и SQLite рассчитаны на единственный процесс;
SSE работает через БД-события. Горизонтальное масштабирование потребует PostgreSQL + внешнюю
очередь — вне MVP.

## 8. Наблюдаемость и отказоустойчивость

- Логи — JSON в stdout: `ts`, `level`, `request_id`/`job_id`, `event`, длительность; секреты
  маскируются. Docker пишет json-file с ротацией 10 MB × 3.
- `GET /healthz` → `{status, db, version}` (200/503) — только локальная БД.
- `GET /metrics` (Bearer `METRICS_TOKEN`) → Prometheus:
  `vpn_node_up`, `vpn_xray_up`, `vpn_traffic_bytes_total`,
  `vpn_connections_active`, `vpn_incidents_open`, `vpn_uptime_seconds`.
- Аудит и инциденты: `GET /api/v1/events` (кто/что/когда, без секретов),
  `GET /api/v1/incidents` + `ack/resolve`; инциденты создаёт health-monitor
  (offline/exit_down) и пайплайн применения конфигураций (apply failed).
- Приёмочный smoke: `deploy/scripts/acceptance.sh` против запущенной панели проверяет
  публичные и админские сценарии (healthz, auth/CSRF, servers/uptime, events/incidents,
  exits/sites/routing, apply+ревизия, clients/QR/sub, agent-auth), PASS/FAIL/SKIP и exit 1
  при FAIL; временные ресурсы удаляет, профиль восстанавливает.
- Инциденты: узел offline (>90 с без heartbeat), выход down (особенно входящий в allowlist),
  apply failed → severity critical/warning с ack/resolve.
- Агент автономен; панель может быть недоступна — VPN продолжает работать, метрики
  буферизуются. Падение exit-узла блокирует только сайты с его allowlist (fail-closed),
  default-трафик уходит на оставшиеся default-выходы, если они есть.
- Резервное копирование — SQLite-файл + `ENCRYPTION_KEY` (см. RUNBOOK); без ключа бэкап БД
  не даёт доступа к SSH/REALITY-секретам.

## 9. Границы и развитие

Вне MVP: мультитенантность/роли, биллинг, клиентские приложения,не-Linux узлы, автопокупка
VPS/прокси, тонкая политика по IP/портам. Точки расширения: PostgreSQL (2+ воркера, PITR),
несколько entry в разных регионах (компилятор уже работает по списку узлов), домашний
exit (WireGuard-меш + health по ASN вместо фиксированного IP), обновление geodata по расписанию
с канареечным доменом.
