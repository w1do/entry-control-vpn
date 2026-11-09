# R3: стек, деплой и безопасность VPN-панели

Дата среза: 2026-11-09. Версии — последние стабильные на этот день; перед фиксацией прогнать `npm outdated` и `uv lock`. Значения ADMIN/PASSWORD из вашего `.env` в документ не переношу.

## 1. Версии

| Компонент | Версия | Важное |
|---|---|---|
| React / react-dom | 19.3.1 | `createRoot`, Suspense, `startTransition` |
| Vite | 8.3.7 | Node `^20.19 || >=22.12`; Node 24 LTS |
| @vitejs/plugin-react | 6.3.0 | совместим с Vite 8 |
| @tanstack/react-router | 1.170.46 | file-based + `router-plugin` 1.169.0 |
| @tanstack/react-query | 5.107.1 | `queryOptions`, `ensureQueryData` |
| shadcn CLI / Tailwind | 4.23.0 / 4.3.5 | Tailwind v4, `@tailwindcss/vite` |
| Recharts | 3.10.1 | основа shadcn charts |
| qrcode.react | 4.2.0 | `QRCodeSVG`, `marginSize` |
| TypeScript | 7.0.2 | нативный tsc; при проблемах ecosystem — 5.9.x |
| FastAPI / Pydantic / pydantic-settings | 0.162.2 / 2.14.3 / 2.16.2 | Python 3.12.16 (EOL 2028-10); для нового проекта 3.13/3.14 |
| uvicorn | 0.54.0 | `--workers 1` для in-process jobs |
| SQLAlchemy / Alembic / asyncpg | 2.1.8 / 1.20.1 / 0.32.0 | async ORM + миграции |
| asyncssh | 2.24.1 | SSH/SFTP/port forwarding |
| APScheduler | 3.11.3 | не 4.0.0a6 (alpha) |
| sse-starlette | 3.7.0 | `EventSourceResponse` |
| cryptography / argon2-cffi | 50.0.2 / 25.1.0 | Fernet + Argon2id |
| grpcio / grpcio-tools | 1.85.1 | стабы из proto Xray |
| slowapi / structlog | 0.1.10 / 26.1.0 | rate limit + JSON-логи |
| Dokploy / Traefik | 0.31.0 / 3.7.17 | Traefik в составе Dokploy; версия может отличаться |
| Xray-core | 26.11.9 | API/proto менять вместе с версией |
| PostgreSQL / Node / nginx | 18.6 / 24.22.2 / 1.31.8 | Postgres опционален |

## 2. Dokploy

1. Project → Create Service → **Compose**; Compose Type = Docker Compose (Stack не умеет `build`).
2. General: репозиторий/ветка, Compose Path `./docker-compose.yml`; `container_name` не задавать (ломает логи/метрики Dokploy).
3. Environment: вставить `.env` в UI. Dokploy пишет его рядом с compose, **но не инжектит в контейнеры автоматически**: используйте `${VAR}` в `environment` или `env_file: .env` (второе отдаст все секреты всем сервисам — нежелательно). Shared: `${{project.VAR}}`.
4. Domains: `panel.example.com`, Path `/api` → service `api`, port 8000; Path `/` → `web`, port 8080; HTTPS on, Certificate `letsencrypt`, Strip Path off. Для compose изменения домена применяются **только после redeploy**.
5. Advanced: Isolated Deployments либо выключить (тогда сами добавляем `dokploy-network`), либо включить (тогда внешнюю сеть не добавлять). Volumes — named volumes для бэкапов; `../files` — bind mount, переживает деплой, но не бэкапится.
6. Deploy → **Preview Compose** (видно итоговые labels/сеть) → логи. История — последние 10 деплоев; откат — предыдущий commit/образ.
7. Auto Deploy: webhook из репозитория или API `compose.deploy`; проверять branch/watch paths.
8. Обновления: `docker compose up -d`, миграции — отдельным шагом/entrypoint; пиновать теги/дижесты, не `latest`; перед миграцией — бэкап.

**Env-ловушки.** `ADMIN_PASSWORD_HASH` (argon2) содержит `$` — в Dokploy `.env` compose может съесть подстановку; передавайте `ADMIN_PASSWORD_HASH_B64` и декодируйте в приложении (или экранируйте `$` как `$$`). Пароль Postgres — только буквы/цифры либо URL-encode в `DATABASE_URL`. Секреты не коммитить; `.dockerignore` — `.env`, `*.pem`, `*.key`. В репозитории `deploy/.env.example` сейчас лежат реальные значения — заменить на плейсхолдеры и ротировать. Dokploy хранит env в своей БД и `.env` на диске; для повышенной защиты — secrets provider.

**Домены/HTTPS.** A-запись на IP до добавления домена, порт 80 открыт для HTTP-01. Приложение слушает `0.0.0.0`, а не `127.0.0.1`; `ports:` наружу не публиковать, только `expose:`. Для `sslip.io`: `https://203.0.113.10.sslip.io` — DNS не нужен, LE HTTP-01 работает; `traefik.me` у Dokploy — бесплатные домены только HTTP. Cloudflare сначала grey-cloud (иначе LE/SSE могут ломаться); при orange — Full (strict), доверять `CF-Connecting-IP`, для SSE — `Cache-Control: no-cache` + heartbeat, без сжатия/буферизации. HSTS включать после проверки всех поддоменов.

**Сеть/порты.** Наружу — только 22/80/443 (22 лучше ограничить по IP). Postgres/API/web — без `ports`; Traefik ходит по `dokploy-network`, БД — в `internal`. Dokploy UI (3000) закрыть; Docker socket в панель не монтировать (это root на хосте). UFW не фильтрует Docker-порты — либо не публиковать их, либо ufw-docker.

Если домен добавляется вручную (вместо UI), Dokploy-совместимые labels выглядят так:
```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.vpn-api.rule=Host(`panel.example.com`) && PathPrefix(`/api`)
  - traefik.http.routers.vpn-api.entrypoints=websecure
  - traefik.http.routers.vpn-api.tls.certresolver=letsencrypt
  - traefik.http.services.vpn-api.loadbalancer.server.port=8000
```
Для `web` — второй router с `PathPrefix('/')`; в UI-варианте эти labels генерирует Dokploy, дублировать их не нужно.

```yaml
services:
  db:
    image: postgres:18-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: vpn
      POSTGRES_USER: vpn
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set in Dokploy env}
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U vpn -d vpn"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s
    networks: [internal]

  api:
    build: {context: ./backend, dockerfile: Dockerfile}
    restart: unless-stopped
    environment:
      APP_ENV: production
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:?}
      DATABASE_URL: ${DATABASE_URL:?}
      ADMIN: ${ADMIN:?}
      PASSWORD: ${PASSWORD:-}
      ADMIN_PASSWORD_HASH_B64: ${ADMIN_PASSWORD_HASH_B64:-}
      SECRET_KEY: ${SECRET_KEY:?}
      ENCRYPTION_KEY: ${ENCRYPTION_KEY:?}
      METRICS_TOKEN: ${METRICS_TOKEN:-}
      SESSION_COOKIE_SECURE: "true"
    expose: ["8000"]
    depends_on:
      db: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 30s
    volumes: [api_data:/data]
    networks: [internal, dokploy-network]

  web:
    build: {context: ./frontend, dockerfile: Dockerfile}
    restart: unless-stopped
    expose: ["8080"]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 5
    networks: [dokploy-network]

volumes:
  pgdata:
  api_data:

networks:
  dokploy-network: {external: true}
  internal: {internal: true}
```

**SQLite вместо Postgres?** Для одного админа, 1–10 серверов и одного API-процесса — да: `DATABASE_URL=sqlite+aiosqlite:////data/app.db` (или `sqlite:////data/app.db` для sync), WAL + `busy_timeout=5000`, один uvicorn worker, бэкап — `VACUUM INTO`/копия файла + отдельно ключ шифрования. По спеке проекта v1 — **один контейнер**: FastAPI отдаёт собранный SPA через `StaticFiles` + API, `web` не нужен; раздельные `api`+`web` в примере — если нужен отдельный nginx/CDN. В `deploy/.env.example` сейчас sync-URL `sqlite:////data/app.db`; для async SQLAlchemy заменить на `sqlite+aiosqlite:////data/app.db`. Postgres нужен при 2+ воркерах/репликах, интенсивной записи статистики, PITR и внешних бэкапах (бэкап — `pg_dump` + проверка restore); для v1 проще убрать `db` и оставить SQLite.

**Health/restart.** `/healthz` — только процесс + быстрый `SELECT 1` (в split-схеме добавьте path `/healthz` → api или доверьтесь Docker healthcheck); **не** проверять SSH/Xray удалённых серверов, иначе падение exit-сервера сделает панель unhealthy и Traefik снимет маршрут. `start_period` — на миграции. `restart: unless-stopped`; `stop_grace_period: 30s`; при одном реплике краткий 502 при recreate неизбежен (zero-downtime — только 2+ реплики + общая БД/Redis, для одного админа избыточно).

## 3. Фронтенд

- **Стек:** React 19.3 + Vite 8.3 + TanStack Router 1.170 (file-based, plugin ставить до react) + Query 5.107 + shadcn/ui 4.23 на Tailwind 4.3. Node 24 LTS; сборка `vite build` → nginx `unprivileged` на 8080 (`/healthz` + SPA fallback).
- **Структура:**
```text
frontend/src/
  main.tsx
  routes/__root.tsx, routes/login.tsx
  routes/_auth.tsx                 # guard + layout
  routes/_auth/index.tsx           # dashboard
  routes/_auth/servers.$serverId.tsx
  routes/_auth/routing.tsx, settings.tsx
  components/{ui,charts,layout}/
  hooks/{use-job-events,use-stats}.ts
  lib/{api,sse,utils}.ts
  queries/{servers,jobs}.ts
  routeTree.gen.ts                 # генерируется router-plugin
```
- **Auth:** opaque-cookie session, не JWT. `fetch(..., {credentials:'include'})` на same-origin `/api/v1`; guard в `beforeLoad` через `queryClient.ensureQueryData(me)`, 401 → `redirect({to:'/login'})`; токен не в localStorage; logout чистит `queryClient`. CSRF — `Origin` + `X-CSRF-Token` для POST/PUT/DELETE.
- **Cookie vs JWT:** cookie-сессия отзывается удалением строки в БД и недоступна JS (HttpOnly); JWT stateless, но отзыв/refresh сложнее, а в localStorage — XSS-риск. Для одной панели — cookie; JWT — только для будущего CLI/внешнего API.
- **Live:** SSE, не WebSocket: поток односторонний, `EventSource` сам переподключается, работает через Traefik/HTTP2, cookie уходит автоматически. Один поток `/api/v1/jobs/{id}/events` и один `/api/v1/stats/stream`; `Last-Event-ID` для replay, throttle 500–1000 мс, закрывать на `done`, fallback-поллинг после 3 ошибок. WebSocket — только если появится интерактивный терминал.
- **Графики:** `npx shadcn@latest add chart`; `ChartContainer` + `ChartConfig` + Recharts 3.10. Для live `isAnimationActive={false}`, ring buffer 60–120 точек, `startTransition`, не перерисовывать всю страницу; тултипы и `syncId` для связанных графиков.
- **shadcn:** для нового проекта `npx shadcn@latest init -t vite`; для существующего — Tailwind v4 (`@tailwindcss/vite`), alias `@/*`, затем `init` и `add button card chart`.

## 4. Бэкенд

**Рекомендация — FastAPI (Python 3.12+).** Причины: `asyncssh` — лучший asyncio-SSH (SFTP, jump host, local port forwarding для Xray gRPC), зрелые `grpcio`, SQLAlchemy/Alembic, Pydantic-настройки, SSE. Node-альтернатива: Fastify 5.12.8 + `ssh2` 1.17 (callback-based) + `@grpc/grpc-js` 1.14.7; `paramiko` 5.1.0 — только legacy/threadpool, не для нового; Hono 4.13.17 хорош для edge/serverless, но не для долгих SSH/gRPC-сессий. Fastify выбирать, только если вся команда JS/TS и не нужен Python.

**SSH-оркестрация.** Один `asyncssh.connect` на сервер, `known_hosts`/pinned fingerprint (никогда `known_hosts=None`), ed25519, `connect_timeout=10`, `keepalive_interval=30`, `asyncio.Lock` на сервер. Команды — фиксированные, аргументы через `shlex.join`; конфиг Xray лучше заливать SFTP и проверять `xray run -test -config`, затем `systemctl restart xray`, при ошибке — откат из backup. Пароли SSH — только для bootstrap, дальше ключи; private key — Fernet, public key — в UI. `sudo` — только на узкий набор команд.

**Xray gRPC.** Включать `api.listen: 127.0.0.1:10085`, `services: [HandlerService, StatsService]`, `stats: {}`, в `policy` — `statsUserUplink/Downlink` и `statsInbound/Outbound...`:
```json
{
  "api": {"tag": "api", "listen": "127.0.0.1:10085",
          "services": ["HandlerService", "StatsService"]},
  "stats": {},
  "policy": {"levels": {"0": {"statsUserUplink": true, "statsUserDownlink": true}},
             "system": {"statsInboundUplink": true, "statsInboundDownlink": true,
                        "statsOutboundUplink": true, "statsOutboundDownlink": true}}
}
```
API **без аутентификации**: только localhost; с панели — SSH local port forward, не публиковать 10085. Стабы генерировать `grpcio-tools` из proto `app/stats/command`, `app/proxyman/command` под конкретную версию Xray (26.11.9); альтернатива — `xray api statsquery` внутри контейнера. Имена счётчиков: `user>>>email>>>traffic>>>uplink`, `inbound>>>tag...`, `outbound>>>tag...`; `reset=false` + дельты, чтобы не терять данные. Per-domain статистики в API нет — считайте по outbound-тегам (каждый exit/группа доменов = тег) или парсите access log; не обещайте «байты по домену» из gRPC.

**Фоновые задачи/SSE.** APScheduler 3.11 (`AsyncIOScheduler`, `max_instances=1`, `coalesce=True`) — ровно один процесс/leader — для poll статистики и health-check; провижининг — запись шагов в `job_events`, SSE-эндпоинт читает события из БД и отдаёт `EventSourceResponse` (`ping=15`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`). Формат: `id: 42\nevent: progress\ndata: {"step":"install_xray","pct":40}\n\n`; терминальное `event: done`. Это переживает реконнект и работает при нескольких воркерах; шаги идемпотентны, после рестарта — resume/rollback; Redis/очередь — только когда появятся реальные параллельные джобы.

**БД.** Таблицы: `users`, `sessions`, `servers`, `ssh_credentials` (ciphertext), `exits`, `domain_rules`, `clients`, `jobs`, `job_events`, `stats_samples`, `audit_log`. SQLAlchemy async + Alembic; миграции — с advisory lock (Postgres) или file lock (SQLite). Ключи/пароли — `SecretStr`, в API не возвращать; `ENCRYPTION_KEY` — Fernet `MultiFernet` для ротации; `SECRET_KEY` — CSRF/подпись.

## 5. Безопасность

- **Один админ.** `ADMIN` — email единственного пользователя, регистрации нет. `PASSWORD` из `.env` — только bootstrap: при старте Argon2id-хешировать и хранить хеш, затем убрать plaintext из env. Предпочтительно сразу `ADMIN_PASSWORD_HASH_B64` (Argon2id, `time_cost=3, memory_cost=64 MiB, parallelism=4`). Указанный пароль — 6 цифр; он слабый и уже засветился в переписке — сменить до деплоя. Сравнение — `verify`, для несуществующего пользователя — dummy-хеш (анти-энумерация).
- **Сессии.** 32 байта `secrets.token_urlsafe`, в БД — только SHA-256; cookie `__Host-vpn_session`: `Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=43200`, без Domain. Ротация при login, revoke при logout/смене пароля; idle-timeout. CSRF: проверка `Origin` для мутаций + double-submit `X-CSRF-Token`. JWT не нужен; если появится CLI — короткий access + HttpOnly refresh, не localStorage.
- **Rate limit.** Login: 5/мин на IP и 10/час на аккаунт, затем 429 + `Retry-After`; глобально 100/мин; провижининг 10/мин и 1 активный job на сервер. `slowapi` (in-memory, 1 worker) + `Traefik rateLimit`; за Cloudflare — `trustedIPs`/реальные IP, иначе все клиенты = один IP.
- **HTTPS/периметр.** LE + redirect 80→443, TLS 1.2+, HSTS, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, CSP `default-src 'self'; style-src 'self' 'unsafe-inline'`. Только 80/443 наружу; Dokploy 3000, Postgres 5432, Xray 10085 — закрыты; SSH — key-only, root login off, fail2ban/UFW; Docker socket не монтировать.
- **Ключи/секреты.** SSH private keys и пароли exit-серверов — Fernet (AES-CBC+HMAC) или libnacl SecretBox; ключ `ENCRYPTION_KEY` (Fernet) и `SECRET_KEY` (CSRF/подпись) — в Dokploy env, не в Git; `ENCRYPTION_KEY` бэкапить отдельно (потеря = потеря доступа). Расшифровка только в памяти; private key никогда не отдавать API/UI; host key pinning; least-privilege sudo; валидировать host/port/domain/UUID (SSRF, инъекции в JSON Xray, metadata IP 169.254.169.254).
- **Логи.** `structlog` JSON + redaction по ключам `password, passwd, secret, token, authorization, cookie, private_key, api_key`; `SecretStr`; не логировать `model_dump()`, тела auth-запросов и полный Xray config; `asyncssh`/`paramiko`/`grpc` — WARNING; Sentry `send_default_pii=False` + `before_send` scrub; Docker `json-file` с `max-size`/`max-file`; audit log — кто/что/когда, без секретов. Токены/ключи не передавать в query string (иначе окажутся в access log); `/metrics` — только с bearer `METRICS_TOKEN`.

## 6. QR и vless://

Ссылку собирает **бэкенд** (у него UUID, Reality pubkey, sni), QR рисует **фронт** — без PNG по сети и без дублирования логики. Эндпоинт `GET /api/v1/servers/{id}/clients/{cid}/link` → `{ "url": "vless://..." }`, `Cache-Control: no-store`; ссылка — секрет, не логировать.

```python
from urllib.parse import quote, urlencode
from pydantic import BaseModel

class Vless(BaseModel):
    uuid: str; host: str; sni: str
    port: int = 443; security: str = "reality"
    pbk: str | None = None; sid: str | None = None; label: str = "VPN"

def vless_url(c: Vless) -> str:
    q = {"encryption": "none", "type": "tcp", "security": c.security,
         "sni": c.sni, "fp": "chrome"}
    if c.security == "reality":
        q |= {"pbk": c.pbk or "", "sid": c.sid or "", "spx": "/",
              "flow": "xtls-rprx-vision"}
    else:
        q |= {"alpn": "h2,http/1.1"}
    return f"vless://{c.uuid}@{c.host}:{c.port}?{urlencode(q, quote_via=quote)}#{quote(c.label, safe='')}"
```

```tsx
import { QRCodeSVG } from "qrcode.react";
export function VlessQr({ url, label }: { url: string; label: string }) {
  return <div className="flex flex-col items-center gap-3">
    <QRCodeSVG value={url} size={240} level="M" marginSize={2} title={label}
      bgColor="#fff" fgColor="#000" />
    <code className="max-w-full truncate text-xs">{url}</code>
  </div>;
}
```

Reality: `vless://UUID@host:443?encryption=none&security=reality&sni=...&fp=chrome&pbk=...&sid=...&spx=%2F&type=tcp&flow=xtls-rprx-vision#Name`. WS+TLS: `security=tls&type=ws&host=...&path=%2Fws&sni=...`. UUID/ключи — `xray uuid` / `xray x25519`; `pbk` — публичный ключ, private не попадает в ссылку. QR печатать на белом фоне (level M, margin 2–4), проверять сканирование iOS/Android; для PNG-скачивания — `QRCodeCanvas.toDataURL()` или Python `qrcode`, но для панели достаточно SVG + copy.

## 7. Ссылки

- Dokploy: [compose](https://docs.dokploy.com/docs/core/docker-compose), [domains](https://docs.dokploy.com/docs/core/docker-compose/domains), [env](https://docs.dokploy.com/docs/core/variables), [hardening](https://docs.dokploy.com/docs/core/guides/production-hardening), [troubleshooting](https://docs.dokploy.com/docs/core/troubleshooting/domains), [releases](https://github.com/Dokploy/dokploy/releases).
- Frontend: [React](https://react.dev/), [Vite](https://vite.dev/), [TanStack Router](https://tanstack.com/router/latest/docs/framework/react/quick-start), [Query](https://tanstack.com/query/latest/docs/framework/react/overview), [shadcn Vite](https://ui.shadcn.com/docs/installation/vite), [shadcn chart](https://ui.shadcn.com/docs/components/chart), [Recharts](https://recharts.org/), [qrcode.react](https://github.com/zpao/qrcode.react).
- Backend: [FastAPI](https://fastapi.tiangolo.com/), [asyncssh](https://asyncssh.readthedocs.io/en/latest/), [SQLAlchemy](https://docs.sqlalchemy.org/), [Alembic](https://alembic.sqlalchemy.org/), [APScheduler](https://apscheduler.readthedocs.io/en/3.x/), [sse-starlette](https://github.com/sysid/sse-starlette), [Fernet](https://cryptography.io/en/latest/fernet/), [argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/), [slowapi](https://slowapi.readthedocs.io/).
- Xray: [API](https://xtls.github.io/en/config/api.html), [stats](https://xtls.github.io/en/config/stats.html), [policy](https://xtls.github.io/en/config/policy.html), [VLESS share link](https://github.com/XTLS/Xray-core/discussions/716), [releases](https://github.com/XTLS/Xray-core/releases).
- Docker: [compose healthcheck](https://docs.docker.com/reference/compose-file/services/#healthcheck), [startup order](https://docs.docker.com/compose/how-tos/startup-order/), [Traefik Docker](https://doc.traefik.io/traefik/providers/docker/), [rate limit](https://doc.traefik.io/traefik/middlewares/http/ratelimit/).
