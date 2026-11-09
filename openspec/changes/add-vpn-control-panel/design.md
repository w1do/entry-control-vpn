# Design: VPN Control Panel

## 1. Контекст

Персональный сервис для одного администратора: панель управляет парком VPS, на которых Xray обеспечивает VLESS+REALITY для клиентов, а трафик зарубежных сайтов уходит через выбранные exit-узлы. Российские сайты — напрямую. Удаление сервера должно полностью очищать узел.

Ключевые требования пользователя и их отображение в спеке:

| Требование | Capability / сценарий |
|---|---|
| «Добавить сервер → система сама всё поднимает» | `server-provisioning`: Провижининг узла одной операцией |
| «Выход под Сингапур/США, лёгкое подключение» | `exit-routing`: Управление выходами |
| «Удаление сносит всё, всё в ~/config/vpn» | `server-provisioning`: Удаление сервера с полной очисткой; `node-agent`: Единый корень состояния |
| «QR → получаю VLESS» | `client-access`: QR-код подключения |
| «Обязательная настройка проксирования» | `exit-routing`: Профиль с обязательным выходом по умолчанию; `panel-ui`: Гейт |
| «Русские сайты — напрямую, зарубежные — на выбранные» | `exit-routing`: RU-direct, Правила по сайтам, Fail-closed |
| «Anthropic только на указанные серверы» | `exit-routing`: Anthropic-scenario (allowlist) |
| «Статистика трафика/подключений/ошибок/аптайм» | `observability` |
| «Лёгкая панель React/TanStack/shadcn» | `panel-ui` |
| «Деплой в Dokploy, ADMIN/PASSWORD в .env» | `deployment`, `panel-auth` |

## 2. Выбор ядра (кратко)

Сравнение Marzban / Marzneshin / 3x-ui / чистый Xray и обоснование — в `research.md`. Статус на 2026-11 (проверен по коду и релизам): Marzban — master заморожен, релиз v0.8.4 от 2025-01; Marzneshin — v0.7.4 от 2025-07; 3x-ui — v3.10.2 от 2026-10, релизы и коммиты ежедневно; Xray-core — v26.11.9. Итог: **собственный control-plane + Xray-core**; из готовых проектов не берём ни один целиком, потому что ни один не даёт одновременно SSH-bootstrap, per-site allowlist-маршрутизацию и удаление по принципу «всё в одном каталоге».

## 3. Архитектура

```
┌────────────────────────────── Dokploy ──────────────────────────────┐
│  ┌─────────────┐   ┌───────────────────────────────────────────┐    │
│  │  frontend   │   │  backend (FastAPI)                        │    │
│  │  React SPA  │──▶│  API /api/v1 · jobs · SSE · compiler      │    │
│  └─────────────┘   │  auth · metrics ingest · /agent/v1        │    │
│                    └───────────┬───────────────────────────────┘    │
│                                │ SQLite/Postgres (volume /data)     │
└────────────────────────────────┼────────────────────────────────────┘
                                 │ HTTPS (исходящие от агентов)
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
 ┌─────────────┐          ┌─────────────┐           ┌─────────────┐
 │ entry-узел  │          │ entry-узел  │           │ exit-узел   │
 │ xray + agent│          │ xray + agent│           │ xray + agent│
 │ VLESS+REAL. │          │ VLESS+REAL. │           │ VLESS+REAL. │
 └──────┬──────┘          └──────┬──────┘           └──────▲──────┘
        │  per-site routing (Xray)                          │
        └──────────────► exit-sg / exit-us ─────────────────┘
                          (VLESS+REALITY interconnect, allowlist entry-IP)
```

Компоненты:

1. **Backend (control-plane)** — единственный источник правды: серверы, выходы, правила сайтов, клиенты, ревизии конфигураций, метрики, инциденты. Компилирует конфиги Xray, запускает SSH-провижининг, принимает heartbeat/метрики агентов.
2. **Agent (`vpn-agent`)** — статический Go-бинарник под systemd. Исходящие HTTPS к панели, применяет ревизии конфигов, собирает метрики, полностью автономен при потере связи.
3. **Frontend** — React 19 SPA; данные — TanStack Query, прогресс — SSE.
4. **Xray-core** — data plane: VLESS/REALITY на entry и на interconnect к exit, routing/rules/balancers, StatsService.

## 4. Топология трафика

1. Клиент подключается к entry-узлу по VLESS+REALITY (443/tcp, маскировка под TLS к реальному домену).
2. Xray на entry (sniffing: http/tls/quic) применяет правила в фиксированном порядке:
   - приватные IP и metadata (`geoip:private`, `169.254.169.254`) → `block`;
   - site rules с allowlist (например Anthropic, выше RU-правил) → только перечисленные exit-выходы через `balancer` с `fallbackTag=block`;
   - RU-домены (`geosite:category-ru`) → `direct`;
   - RU-IP (`geoip:ru`, требует `IPIfNonMatch`) → `direct`;
   - остальное → выход по умолчанию;
   - catch-all → `block`; первый outbound в конфиге — `block` (страховка fail-closed).
3. Exit-узел принимает VLESS+REALITY inbound (собственные ключи, порт 18443) с allowlist по IP entry-узлов и выпускает трафик в интернет.
4. Ответный трафик идёт обратно по той же цепочке.

**Внешние выходы (резидентские прокси).** Вместо собственного exit-узла можно указать внешний HTTP(S)/SOCKS5-прокси: entry открывает CONNECT к провайдеру, провайдер резолвит домен и выпускает трафик из своего гео; TLS браузера остаётся сквозным. Здоровье проверяется сравнением фактического exit-IP и страны с ожидаемыми; несовпадение или `RU` → выход `down` и `block` для сайтов с этим выходом. Трафик учитывается по выходу и сверяется с бюджетом провайдера.

**RU-сайты не проксируются вообще.** Основной режим — клиентский профиль с RU-bypass: правила `geosite:category-ru` + `geoip:ru → direct` применяются на устройстве, поэтому российские сайты открываются с обычного российского IP и не покидают устройство. `vless://` остаётся совместимым fallback-режимом: там RU-трафик не уходит на выходы, а выпускается напрямую с entry-узла; это ограничение явно показано в UI.

### 4.1 Клиентский профиль с RU-bypass (основной режим)

Панель генерирует персональный профиль клиента (sing-box JSON; для Clash-клиентов — Clash Meta YAML по `User-Agent` подписки). Правила выполняются на устройстве: RU-домены/IP не входят в туннель, остальное — через VLESS+REALITY.

```jsonc
{
  "inbounds": [{ "type": "tun", "tag": "tun-in", "auto_route": true, "strict_route": true }],
  "outbounds": [
    { "type": "vless", "tag": "proxy", "server": "203.0.113.5", "server_port": 443,
      "uuid": "<uuid>", "flow": "xtls-rprx-vision",
      "tls": { "enabled": true, "server_name": "www.microsoft.com",
               "utls": { "enabled": true, "fingerprint": "chrome" },
               "reality": { "enabled": true, "public_key": "<pbk>", "short_id": "<sid>" } } },
    { "type": "direct", "tag": "direct" }
  ],
  "dns": {
    "servers": [
      { "tag": "local",  "address": "local", "detour": "direct" },
      { "tag": "remote", "address": "https://1.1.1.1/dns-query", "detour": "proxy" }
    ],
    "rules": [{ "rule_set": ["geosite-category-ru"], "server": "local" }],
    "final": "remote"
  },
  "route": {
    "rules": [{ "rule_set": ["geosite-category-ru", "geoip-ru"], "outbound": "direct" }],
    "final": "proxy",
    "rule_set": [
      { "tag": "geosite-category-ru", "type": "remote", "format": "binary",
        "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs" },
      { "tag": "geoip-ru", "type": "remote", "format": "binary",
        "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-ru.srs" }
    ]
  }
}
```

Синтаксис правил подстраивается под версию sing-box клиента (1.10/1.11); эталонный профиль фиксируется в тестах. Подписка: `GET /sub/{token}` — персонализированный токен, формат выбирается по `User-Agent`, токен отзывается вместе с клиентом.

## 5. Модель данных

| Таблица | Ключевые поля |
|---|---|
| `users` | id, email, password_hash, created_at, last_login_at |
| `servers` | id, name, host, ssh_port, ssh_user, auth_type, secret_enc, role (entry/exit/both), region, status, agent_token_hash, agent_version, xray_version, last_seen_at, revision, base_dir |
| `exits` | id, kind (node/external), server_id, label, region, endpoint_host, endpoint_port, protocol (ss/ss2022/http/socks5), secret_enc, expected_exit_ip, bandwidth_budget, enabled, health, last_check_at |
| `clients` | id, name, uuid, email_tag, enabled, traffic_limit_bytes, created_at |
| `site_rules` | id, match_type (domain/wildcard/regexp/geosite), values_json, allowed_exit_ids (ordered), preset_key, enabled, position |
| `routing_revisions` | id, created_at, profile_json, config_hashes_json, status, applied_by |
| `server_configs` | server_id, revision_id, config_json, applied_at, result |
| `metrics_buckets` | server_id, client_id, bucket_ts, resolution, up_bytes, down_bytes, conns, errors_json |
| `jobs` | id, type, server_id, status, progress, log_json, created_at |
| `incidents` | id, severity, kind, subject, status (open/ack/resolved), created_at |
| `events` | id, ts, kind, actor, ip, payload_json |

Секреты (`secret_enc`, interconnect-ключи, agent-токены) — Fernet с ключом `ENCRYPTION_KEY`; в API не возвращаются.

## 6. Компиляция конфигурации Xray

### 6.1 Entry-узел

```jsonc
{
  "inbounds": [
    { "tag": "vless-in", "port": 443, "protocol": "vless",
      "settings": { "clients": [{ "id": "<uuid>", "email": "client-1@vpn", "flow": "xtls-rprx-vision" }],
                    "decryption": "none" },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true },
      "streamSettings": { "network": "tcp", "security": "reality",
        "realitySettings": { "dest": "www.microsoft.com:443", "target": "www.microsoft.com:443",
          "serverNames": ["www.microsoft.com"], "privateKey": "<key>",
          "shortIds": ["<sid>"] } } }
  ],
  "outbounds": [
    { "tag": "block", "protocol": "blackhole" },
    { "tag": "direct", "protocol": "freedom" },
    { "tag": "exit-sg1", "protocol": "vless",
      "settings": { "vnext": [{ "address": "203.0.113.10", "port": 18443,
        "users": [{ "id": "<interconnect-uuid>", "flow": "xtls-rprx-vision", "encryption": "none" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality",
        "realitySettings": { "serverName": "www.microsoft.com", "fingerprint": "chrome",
          "publicKey": "<exit-pbk>", "shortId": "<exit-sid>", "spiderX": "/" } } }
  ],
  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      { "type": "field", "ip": ["geoip:private", "169.254.169.254/32"], "outboundTag": "block" },
      { "type": "field", "domain": ["domain:anthropic.com", "domain:claude.ai", "geosite:anthropic"],
        "balancerTag": "bal-anthropic" },
      { "type": "field", "domain": ["geosite:category-ru"], "outboundTag": "direct" },
      { "type": "field", "ip": ["geoip:ru"], "outboundTag": "direct" },
      { "type": "field", "network": "tcp,udp", "balancerTag": "bal-default" },
      { "type": "field", "network": "tcp,udp", "outboundTag": "block" }
    ],
    "balancers": [
      { "tag": "bal-anthropic", "selector": ["rule-anthropic-"],
        "strategy": { "type": "leastPing" }, "fallbackTag": "block" },
      { "tag": "bal-default", "selector": ["exit-"],
        "strategy": { "type": "leastPing" }, "fallbackTag": "block" }
    ]
  },
  "observatory": { "subjectSelector": ["exit-", "rule-"],
                   "probeUrl": "https://www.gstatic.com/generate_204",
                   "probeInterval": "30s", "enableConcurrency": true },
  "policy": { "levels": { "0": { "statsUserUplink": true, "statsUserDownlink": true, "statsUserOnline": true } },
              "system": { "statsInboundUplink": true, "statsInboundDownlink": true,
                          "statsOutboundUplink": true, "statsOutboundDownlink": true } },
  "api": { "tag": "api", "listen": "127.0.0.1:10085",
           "services": ["HandlerService", "LoggerService", "StatsService"] },
  "stats": {},
  "metrics": { "listen": "127.0.0.1:11111" }
}
```

Порядок правил фиксирован: private/metadata → site rules (allowlist; выше RU, чтобы зарубежный домен, резолвящийся в RU-IP, не ушёл напрямую) → RU-direct (locked) → RU-IP (`geoip:ru`, требует `IPIfNonMatch`) → default exit → catch-all block. Первый outbound — `block`. Балансировщики используют префиксные селекторы, поэтому выходы внутри site rule получают теги `rule-<id>-…`, а выходы по умолчанию — `exit-…`; `fallbackTag` — `block`. Это проверяет golden-тест.

### 6.2 Exit-узел

```jsonc
{
  "inbounds": [{ "tag": "interconnect-in", "listen": "0.0.0.0", "port": 18443, "protocol": "vless",
    "settings": { "clients": [{ "id": "<interconnect-uuid>", "email": "entry-1", "flow": "xtls-rprx-vision" }],
                  "decryption": "none" },
    "streamSettings": { "network": "tcp", "security": "reality",
      "realitySettings": { "dest": "www.apple.com:443", "target": "www.apple.com:443",
        "serverNames": ["www.apple.com"], "privateKey": "<exit-key>", "shortIds": ["<exit-sid>"] } } }],
  "outbounds": [{ "tag": "direct", "protocol": "freedom" }],
  "routing": { "rules": [], "domainStrategy": "AsIs" }
}
```

Firewall: 18443/tcp открыт только на IP, входящие в кластер entry-узлов; 22/tcp — по политике; 443/tcp — только если узел имеет роль entry. SS-2022 не используем: в Xray 26.x Shadowsocks объявлен deprecated (migrate to VLESS Encryption), а VLESS+REALITY уже обкатан на клиентском inbound.

### 6.3 Ревизии и применение

- Профиль маршрутизации → `profile_json` → компилятор → `{server_id: config_json}`. Сериализация детерминированная (сортировка ключей, фиксированный порядок правил), SHA-256 каждого конфига.
- Пайплайн: сохранить ревизию (`pending`) → разослать агентам (`apply`) → каждый прогоняет `xray run -test -c <candidate>` (валидация до замены файла; альтернатива — конфиг через stdin) → атомарная замена → restart → health-check → панель собирает результаты.
- Любой провал → ревизия `failed`, всем узлам уходит команда отката на предыдущую. UI показывает diff и причину.
- Версия Xray пиннится (26.11.x) и проверяется на узле; geodata-датасет обновляется с сохранением sha256 и предыдущей копии, после обновления — `-test` и канареечный домен.

### 6.4 Fail-closed

- `fallbackTag: "block"` у балансировщиков; никаких «попробуем другой выход, потом direct».
- Если у site rule пустой/невалидный allowlist — правило не компилируется, запрос отклоняется валидацией.
- Если профиль не активирован — entry-конфиг содержит только `block`-маршрут и inbound, трафик клиентов не выпускается.

## 7. Агент и протокол

Агент работает исходящими HTTPS-запросами, входящих портов не открывает.

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/agent/v1/register` | регистрация, версии, capabilities |
| POST | `/agent/v1/heartbeat` | живость каждые 30 с, ревизия, состояние юнитов |
| GET | `/agent/v1/config?revision=N` | получить новую ревизию конфигурации |
| POST | `/agent/v1/apply-result` | результат применения |
| POST | `/agent/v1/metrics` | батч метрик (15 с) |
| POST | `/agent/v1/diagnose` | диагностический отчёт |
| POST | `/agent/v1/uninstall` | полная очистка |

Авторизация — per-node bearer-токен (хранится на узле `0600`), панель хранит только хеш. Payload метрик:

```json
{ "ts": "2026-01-01T00:00:00Z", "revision": 42,
  "xray": { "active": true, "uptime_s": 123456, "version": "26.11.9", "goroutines": 42 },
  "traffic_delta": { "up": 1234, "down": 5678 },
  "clients": [{ "email": "client-1@vpn", "up": 10, "down": 20, "online": true, "ips": ["203.0.113.7"] }],
  "connections": { "accepted_15s": 12, "blocked_15s": 2, "online": 7 },
  "errors": { "rejected_15s": 3, "xray_error_15s": 0 } }
```

Счётчики Xray кумулятивные: агент хранит последние значения по имени и отправляет дельты (защита от рестарта ядра и двойного учёта), `reset=true` не используется. Атомарное применение: `xray run -test -c candidate` → rename `xray.json.new → xray.json` → `systemctl restart xray` → проверка `is-active` + локальный health → при ошибке вернуть старый файл и рестартовать. Агент буферизует метрики до 24 ч при недоступности панели.

## 8. API панели (эскиз)

- `POST /api/v1/auth/login|logout`, `GET /api/v1/auth/me`, `POST /api/v1/auth/password`
- `GET|POST /api/v1/servers`, `GET|DELETE /api/v1/servers/{id}`, `POST /api/v1/servers/{id}/actions`
- `GET|POST /api/v1/exits`, `PATCH|DELETE /api/v1/exits/{id}`
- `GET|POST /api/v1/sites`, `PATCH|DELETE /api/v1/sites/{id}`, `GET /api/v1/sites/presets`
- `GET|PUT /api/v1/routing/profile`, `POST /api/v1/routing/apply`, `POST /api/v1/routing/check`, `GET /api/v1/routing/revisions`
- `GET|POST /api/v1/clients`, `DELETE /api/v1/clients/{id}`, `GET /api/v1/clients/{id}/link|qr.svg`
- `GET /sub/{token}` — подписка клиента (sing-box JSON / Clash Meta YAML), `GET /api/v1/clients/{id}/profile?mode=smart|vless`
- `GET /api/v1/metrics/overview|traffic|node/{id}|client/{id}`
- `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/stream` (SSE), `GET /api/v1/incidents`
- `GET /healthz`, `GET /metrics` (Prometheus, `METRICS_TOKEN`)

## 9. Frontend

- Стек: React 19, Vite, TypeScript, TanStack Router, TanStack Query, shadcn/ui (Radix), Recharts, qrcode.react.
- Сессия — cookie; guarded routes; все мутации с `X-CSRF-Token`.
- Прогресс провижининга/применения — SSE (`EventSource`), остальное — Query с инвалидацией.
- Страницы: Login, Dashboard, Servers (+wizard), Exits, Sites, Clients, Settings.
- Тема light/dark, ленивые маршруты, бюджет начального бандла ≤250 КБ gzip.

## 10. Безопасность

- Один админ из `.env` (argon2id), серверные сессии, CSRF, rate limit.
- SSH-ключи/PSK — шифрование Fernet, не логируются, не отдаются в API.
- Жёсткие ограничения на узлах: exit-порт только для entry-IP; агент без входящих портов; systemd hardening.
- HTTPS через Traefik; `Secure` cookies; CSP на SPA; `/metrics` за токеном.

## 11. Деплой (Dokploy)

```yaml
services:
  app:
    build: .
    env_file: .env
    environment:
      DATABASE_URL: sqlite+aiosqlite:////data/app.db
    volumes: [ "panel-data:/data" ]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
      interval: 30s
      timeout: 5s
      retries: 3
volumes:
  panel-data:
```

Dokploy подключает домен/TLS через Traefik; том `panel-data` хранит SQLite и артефакты. Миграции — в entrypoint. Внешний Postgres — опционально через `DATABASE_URL` для многосерверных сценариев. Dokploy не инжектит `.env` автоматически — переменные попадают в контейнер через `env_file`/`${VAR}` рядом с compose; смена домена применяется только после redeploy; наружу открыты только 80/443, БД и внутренние порты — во внутренней сети (`dokploy-network` + internal).

## 12. Альтернативы и почему отвергнуты

| Альтернатива | Причина отказа |
|---|---|
| Marzban как ядро | Нет SSH-провижининга (только marzban-node), нет per-site exit routing; тяжёлый стек MySQL/Redis |
| Marzneshin как ядро | То же; меньше экосистема |
| 3x-ui как ядро | Технически ближе всех (v3.10.2, routing UI, per-node egress, heartbeat), но GPL-3.0, монолит на каждой ноде и «панельный» API; свой UI всё равно писать |
| sing-box вместо Xray | Меньше зрелость REALITY-экосистемы для панелей, но подходит; Xray выбран из-за зрелости REALITY и stats API |
| WireGuard-меш для exit-связки | Нет встроенного per-domain routing на entry; Xray-нативный VLESS+REALITY проще и без deprecation-рисков |
| SS-2022 для interconnect | В Xray 26.x Shadowsocks объявлен deprecated (migrate to VLESS Encryption); используем VLESS+REALITY |
| Клиентский split tunneling для RU | Принят как основной режим: профиль sing-box с `geosite:category-ru`/`geoip:ru → direct`; `vless://` остаётся fallback |

## 13. Риски и компромиссы

- **REALITY-домен** нужно выбирать «живой» и близкий к узлу; смена домена = новая ревизия ссылок.
- **Межузловой транспорт** — VLESS+REALITY на отдельном порту с allowlist по IP entry; SS-2022 не используем: в Xray 26.x Shadowsocks объявлен deprecated (migrate to VLESS Encryption).
- **Учёт подключений:** онлайн-клиенты и их IP берутся из `GetUsersStats` (`statsUserOnline`), accepted/blocked — из access-лога (plain text, парсится regex-ом); `reset=true` не используется — только дельты по кумулятивным счётчикам, один коллектор на узел.
- **RU-трафик:** в умном профиле RU-сайты идут напрямую с устройства (российский IP); в режиме полного туннеля — через entry (не через выходы), это явно помечено в UI.
- **Один админ** — нет ролей; при росте потребуется отдельный change.
- **Резидентский провайдер** видит метаданные соединений и может ограничивать использование; статический ISP-IP лучше sticky-пула, но и он может попасть в фрод-листы — нужен health-check по фактическому IP и запасной выход.
- **REALITY-dest** подбирается из той же AS/юрисдикции, что и узел, проверяется сканом TLS 1.3/H2 и канареечным подключением; неудачный dest = детект/бан.
- **Клиенты по IP без SNI:** правила по домену не увидят домен — включаем `sniffing.destOverride` + `routeOnly` и покрываем тестами.
- **`adu`/`rmu` не персистентны:** клиенты хранятся в desired-state и повторно применяются при старте/переприменении конфига.
- **Base dir в домашнем каталоге конфликтует с `ProtectHome`:** unit использует `ProtectHome=no` + `ProtectSystem=strict` + `ReadWritePaths=<base>/` (альтернатива — `/opt/vpn` с `ProtectHome=yes`).
- **SQLite** — окно записи при провижининге; для одного админа достаточно, Postgres — опция.

## 14. Открытые вопросы

1. ~~Нужен ли клиентский профиль с RU-bypass?~~ Решено: клиентский профиль — часть MVP (`4.1, capability `client-access`); открытым остаётся список поддерживаемых клиентов и версий формата подписки.
2. Какой провайдер резидентских ISP-прокси взять для пилота (US основной + EU резерв) и сколько IP держать; сравнение — в `research.md`.
3. Политика ретенции метрик и пороги алертов (сейчас: 24 ч raw / 30 д 5м / 365 д 1ч).
4. Нужна ли автогенерация REALITY-домена по региону узла.
5. Поддержать «свой домашний exit» (Pi/OpenWrt + WireGuard) как отдельный тип выхода — в roadmap: панель уже умеет добавлять узел по SSH, нужен только WG-меш и health по стране/ASN вместо фиксированного IP.
