# VPN Control Panel

Self-hosted панель управления VPN на **Xray-core (VLESS + XTLS-Vision + REALITY)** для одного
администратора. Панель добавляет VPS по SSH и сама разворачивает на них узлы, ведёт реестр
выходов (exit-узлов и резидентских прокси), маршрутизирует сайты по allowlist с принципом
**fail-closed**, российский трафик не проксирует и раздаёт клиентам VLESS-ссылки/QR.

Панель — единственная точка настройки: маршрутизация, выходы, клиенты и конфиги генерируются
на бэкенде из БД и применяются на узлах атомарно (validate → swap → restart → health-check →
rollback). Узлы автономны: при недоступности панели Xray продолжает работать на последней
применённой конфигурации.

> **Развёртывание «от нуля до рабочего VPN»** — в [SETUP.md](SETUP.md). Это самодостаточный
> runbook: что купить, как настроить Dokploy/TLS, сгенерировать секреты, добавить узлы,
> настроить Anthropic-allowlist, выпустить клиентам QR и проверить fail-closed.
> Эксплуатация (деградации, откаты, бэкапы) — [docs/RUNBOOK.md](docs/RUNBOOK.md).
> Устройство системы — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Возможности

| Возможность | Что делает |
|---|---|
| Добавление серверов по SSH | preflight (ОС/архитектура/sudo/порты), установка Xray и vpn-agent, systemd-юниты, firewall, регистрация в панели — одной операцией с SSE-прогрессом |
| Роли узлов | `entry` (клиентский VLESS+REALITY inbound 443/tcp), `exit` (interconnect 18443/tcp), `both` |
| Выходы | exit-узлы и внешние резидентские прокси (HTTP CONNECT / SOCKS5), health-check по фактическому exit-IP, бюджет трафика |
| Правила сайтов | домены/geosite → упорядоченный allowlist выходов; недоступность всех разрешённых = `block` (fail-closed), без «попробуем другой» и без direct |
| RU-direct | российские домены и IP всегда напрямую: в умном профиле — с устройства, в полном туннеле — с entry, но никогда через выход |
| Клиенты | VLESS-uuid, QR, `vless://`-ссылка, умный профиль (sing-box / Clash Meta) с RU-bypass, подписка `/sub/{token}`, лимиты трафика |
| Наблюдаемость | трафик по узлам/клиентам, соединения, ошибки routing_blocked/protocol_rejected/xray_error, uptime, инциденты, Prometheus `/metrics` |
| Единственный админ | bootstrap из `.env`, Argon2id, opaque cookie-сессии, CSRF, rate limit, журнал безопасности |

## Как это выглядит

```
                Браузер админа
                     │ HTTPS (Traefik, Let's Encrypt)
┌────────────────────▼─────────────────────────────────────────────┐
│ Dokploy: один контейнер app                                      │
│   FastAPI (/api/v1, /agent/v1, /sub, /healthz, /metrics)        │
│   + собранный React SPA + SQLite на томе /data                   │
└───────┬───────────────────────────────┬──────────────────────────┘
        │ SSH (провижининг)             │ HTTPS исходящими (агенты)
┌───────▼─────────┐  VLESS+REALITY  ┌────▼─────────────┐
│ entry-узел      │ ──────────────▶ │ exit-узел        │ ──▶ интернет
│ xray + agent    │  18443/tcp      │ xray + agent     │     (SG/US/…)
└───────┬─────────┘                 └──────────────────┘
        │ VLESS+REALITY 443/tcp
┌───────▼─────────┐
│ клиент (телефон)│  RU-сайты → direct с устройства (умный профиль)
└─────────────────┘
```

Подробная схема и потоки данных — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Технологический стек

| Слой | Технологии |
|---|---|
| Backend | Python 3.12, FastAPI 0.162, Pydantic v2, SQLAlchemy 2.1 (async) + Alembic, APScheduler 3.11, asyncssh 2.24, SSE (sse-starlette) |
| БД | SQLite (`sqlite+aiosqlite`, по умолчанию, том `/data`) или PostgreSQL 18 (опция, profile `postgres`) |
| Frontend | React 19, Vite 8, TypeScript 5.9, TanStack Router/Query, Tailwind v4, shadcn/ui (Radix), Recharts, qrcode.react |
| Data plane | Xray-core 26.11.9 (VLESS+REALITY, StatsService, balancers/observatory) |
| Agent | Go 1.23, только stdlib, статический бинарник linux/amd64 + linux/arm64, systemd |
| Deploy | Docker (multi-stage), docker compose, Dokploy + Traefik + Let's Encrypt |
| Тесты | pytest (+ pytest-asyncio), golden-тесты компилятора Xray, `xray run -test`, go test |

## Структура репозитория

```
backend/            FastAPI: api/, core/, models/, schemas/, services/, compiler/, alembic/, tests/
frontend/           React SPA: src/routes, src/pages, src/components, src/lib
agent/              vpn-agent (Go): cmd/vpn-agent, internal/, packaging/
deploy/             Dockerfile, docker-compose.yml, entrypoint.sh, .env.example, secret-scan.sh, scripts/
docs/               INTERFACES.md (контракт), ARCHITECTURE.md, RUNBOOK.md
openspec/           спецификации и проектные решения (proposal/design/specs)
SETUP.md            пошаговое развёртывание до рабочего VPN
Makefile            dev/test/lint/build/agent/up/down/secret-scan
.github/workflows/  CI (ruff+pytest, tsc+build, go test+build, secret-scan, compose config)
```

## Быстрый старт разработчика

### Предварительные требования

- Python **3.12+** (проект проверяется на 3.12; локально допускается 3.13/3.14);
- Node.js **22+** (сборка — Node 24 в CI/образе), npm;
- Go **1.23+** — для агента;
- Docker + Docker Compose v2 — для локального стенда и проверки compose;
- (опционально) бинарник Xray 26.11.9 и geodata — только для golden-тестов `xray run -test`.

В окружении разработки WSL без прямого интернета пакеты ставятся через Windows-сторону
(см. docs/INTERFACES.md §0), а сборка агента и npm выполняются там, где есть сеть.

### 1. Backend

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pip install pytest pytest-asyncio httpx ruff   # dev-инструменты

# миграции (нужны переменные окружения; для локальной разработки — фейковые)
cd backend
ADMIN=admin@localhost PASSWORD=dev-password-placeholder-1234 \
SECRET_KEY=dev-secret-key-placeholder-0123456789abcdef \
ENCRYPTION_KEY="$(../.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
DATABASE_URL="sqlite+aiosqlite:///./dev.db" ../.venv/bin/alembic upgrade head
```

Запуск с autoreload (сам подставляет dev-переменные и генерирует Fernet-ключ):

```bash
make dev          # http://127.0.0.1:8000, БД backend/dev.db
```

Альтернатива — `AUTO_CREATE_SCHEMA=true` (создать схему без Alembic; только для разработки).

### 2. Frontend

```bash
cd frontend
npm ci            # или npm install, если package-lock.json ещё не создан
npm run dev       # Vite dev server; /api проксируется на 127.0.0.1:8000
```

Production-сборка кладётся в `frontend/dist`; в Docker-образе её раздаёт FastAPI
(`backend/app/static` + SPA-fallback).

### 3. Агент

```bash
cd agent
go test ./...
go build ./cmd/vpn-agent
# или сразу оба таргета в agent/bin/:
make agent
```

### 4. Артефакты узлов (для провижининга)

Провижининг раздаёт узлам Xray, vpn-agent и geodata из каталога /data/artifacts.
Подготовка — make artifacts, проверка — make artifacts-check, заливка в контейнер —
make artifacts-copy. Подробности — deploy/README.md и SETUP.md §8.

### 5. Проверки и линт

```bash
make test          # pytest backend + go test agent
make lint          # ruff check backend + go vet agent
make secret-scan   # поиск утёкших секретов (deploy/.env не читается)
```

### 6. Локальный Docker-стенд

```bash
cp deploy/.env.example deploy/.env
# заполните ADMIN/PASSWORD/SECRET_KEY/ENCRYPTION_KEY (команды генерации — в файле)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
curl -fsS http://127.0.0.1:8000/healthz   # {"status":"ok","db":"ok","version":"0.1.0"}
```

Для локального `http://` (без TLS) поставьте `SESSION_COOKIE_SECURE=false`, иначе браузер
не сохранит cookie сессии. Порт наружу опубликован не будет — для доступа с хоста
используйте `docker compose port`/SSH-туннель или добавьте временный `ports:` только локально.

## Конфигурация

Все переменные окружения — в [deploy/.env.example](deploy/.env.example) с комментариями и
командами генерации. Обязательные: `ADMIN`, `PASSWORD`, `SECRET_KEY`, `ENCRYPTION_KEY`;
остальные имеют безопасные значения по умолчанию. Секреты проверяются при старте:
некорректный `ENCRYPTION_KEY` или плейсхолдер в `SECRET_KEY` не дадут приложению подняться.

Ключи для production:

```bash
openssl rand -hex 32                                                     # SECRET_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY
openssl rand -hex 32                                                     # METRICS_TOKEN
```

`ENCRYPTION_KEY` — единственный ключ, без которого нельзя расшифровать сохранённые
SSH/REALITY-секреты. Храните его отдельно от резервных копий БД.

## Тесты и качество

- `backend/tests` — auth/bootstrap, healthz, agent API, компилятор Xray (golden: порядок правил,
  RU-direct, allowlist, fail-closed, детерминизм).
- `agent/internal/*` — протокол агента, дельта-метрики, атомарное применение с rollback,
  uninstall-guard.
- CI (.github/workflows/ci.yml): backend (ruff + pytest, mypy информационно), frontend
  (typecheck + build), agent (go vet/test + сборка linux/amd64 и linux/arm64), secret-scan,
  `docker compose config`.
- `deploy/secret-scan.sh` — регулярные выражения (PEM, AWS/GitHub/OpenAI/Slack-токены,
  присвоения секретных переменных) + проверка `.gitignore`. Значения в лог не выводятся.
- `deploy/scripts/acceptance.sh` — приёмочный smoke-тест **запущенной** панели
- make e2e-node — E2E связки «реальная панель ↔ реальный vpn-agent» на локальном стенде (Xray и systemd заменены fake-скриптами, VPS не нужен): register → heartbeat → config poll → атомарное применение → apply-result → метрики-дельты → diagnose-команда → command-result. 11 проверок.
- make e2e-provisioning — E2E SSH-провижининга: панель через реальный asyncssh разворачивает узла в sandbox (артефакты, конфиг, юниты, xray -test), затем загруженный агент регистрируется. 20 проверок.
  (`BASE_URL`/`ADMIN`/`PASSWORD`, опционально `METRICS_TOKEN`): healthz/openapi/metrics,
  auth+CSRF, серверы и uptime_24h_pct, events/incidents, выходы/сайты/routing
  (check + apply + ревизия), клиенты/QR/подписки в форматах sing-box/clash/v2ray,
  agent-auth. Печатает PASS/FAIL/SKIP, exit 1 при FAIL; временные ресурсы удаляет, профиль
  маршрутизации восстанавливает. Подробности — SETUP.md §14.0.

## Безопасность (кратко)

- Один админ, регистрации нет; bootstrap-пароль действует только при пустой БД.
- Пароли — Argon2id; сессии — opaque-токены в HttpOnly-cookie `__Host-vpn_session`,
  CSRF-заголовок `X-CSRF-Token` для мутаций; rate limit на login.
- SSH-креды, REALITY-ключи и agent-токены шифруются Fernet в БД и не отдаются API/логам.
- Наружу только 80/443 через Traefik; БД и порт 8000 не публикуются, внутренняя сеть
  `internal`; контейнер — non-root, `no-new-privileges`, без Docker socket.
- Узлы: exit-порт 18443 доступен только IP entry-узлов; агент не открывает входящих портов;
  systemd-hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths`).
- Маршрутизация fail-closed: первый outbound — blackhole, у балансировщиков
  `fallbackTag: block`, финальное правило — block.

## Ограничения (MVP)

- Один администратор, без ролей и мультитенантности.
- Узлы только Linux (systemd, root/sudo).
- Клиентские приложения не входят в поставку — используются штатные VLESS-клиенты
  (sing-box/Hiddify/Clash Meta/v2rayTun и т.п.) по ссылке/QR/подписке.
- Автоматическая покупка VPS/прокси не поддерживается.

## Документация

| Документ | Назначение |
|---|---|
| [SETUP.md](SETUP.md) | пошагово: покупка серверов → DNS → Dokploy → секреты → узлы → выходы → правила → клиенты → проверки |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | компоненты, потоки данных, маршрутизация, БД, безопасность |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | деградации, откат релиза, бэкап/восстановление, «узел не удалился», ротация ключей |
| [docs/INTERFACES.md](docs/INTERFACES.md) | контракт между backend/compiler/agent/frontend/deploy (зона Lead) |
| [openspec/](openspec/) | proposal, design, capability-спеки, research |

## Лицензия

Приватный проект для личного использования; публикация и перераспределение — по решению владельца.
