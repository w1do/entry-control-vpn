# deploy/

Файлы развёртывания VPN Control Panel (Dokploy / docker compose).

| Файл | Назначение |
|---|---|
| `Dockerfile` | multi-stage: node:24-alpine собирает SPA -> python:3.12-slim; non-root; entrypoint |
| `entrypoint.sh` | `alembic upgrade head` (ретраи) -> `uvicorn app.main:app --workers 1` |
| `docker-compose.yml` | сервис `app` (том `panel-data:/data`, healthcheck, dokploy-network) + `db` (profile postgres) |
| `.env.example` | переменные окружения с комментариями и командами генерации; реальные — в `.env` (gitignored) |
| `secret-scan.sh` | проверка репозитория на утёкшие секреты (используется в CI и `make secret-scan`) |
| `scripts/fetch-artifacts.sh` | скачивает/собирает артефакты узлов в `artifacts/` (см. ниже) |
| `scripts/backup-sqlite.sh` | консистентный бэкап SQLite через `VACUUM INTO` |
| `scripts/rotate-encryption-key.py` | перешифрование секретов при ротации `ENCRYPTION_KEY` |
| `scripts/acceptance.sh` | приёмочный smoke-тест запущенной панели (PASS/FAIL/SKIP, exit 1 при FAIL) |

## Артефакты узлов

Провижининг загружает на каждый узел четыре файла из `/data/artifacts`:

| Файл | Что это | Откуда |
|---|---|---|
| `xray-linux-amd64` | Xray-core 26.11.9 | XTLS/Xray-core release (`Xray-linux-64.zip`) |
| `vpn-agent-linux-amd64` | статический `vpn-agent` | `make agent` / `go build` |
| `geoip.dat` | geodata IP | Loyalsoldier/v2ray-rules-dat (latest) |
| `geosite.dat` | geodata доменов | Loyalsoldier/v2ray-rules-dat (latest) |

Подготовка и заливка в запущенный контейнер:

```bash
make artifacts        # -> ./artifacts/{xray-linux-amd64,vpn-agent-linux-amd64,geoip.dat,geosite.dat}
make artifacts-copy   # docker cp в /data/artifacts контейнера app + chown 10001
```

Без интернета на машине сборки: скачайте zip Xray и geodata на любой машине с сетью,
соберите агент, положите четыре файла с этими именами в `artifacts/` и выполните
`make artifacts-copy` (или залейте файлы на узел вручную — см. SETUP.md §8).

Альтернатива named-тому — bind-mount: в `docker-compose.yml` у сервиса `app` раскомментируйте
`- ../artifacts:/data/artifacts:ro` (локальный запуск) или смонтируйте свой каталог, например
`- /opt/vpn-artifacts:/data/artifacts:ro`. В Dokploy каталог репозитория может пересоздаваться
при деплое, поэтому по умолчанию используется том `panel-data`.

## Приёмка

Скрипт проверяет запущенную панель по HTTP (панель должна быть доступна):

```bash
export BASE_URL=https://panel.example.com
export ADMIN='you@example.com'
export PASSWORD='...'
# export METRICS_TOKEN='...'          # если /metrics защищён токеном
bash deploy/scripts/acceptance.sh

# временный entry-сервер (fake SSH), если entry-узлов ещё нет:
export ACCEPTANCE_CREATE_ENTRY=1
bash deploy/scripts/acceptance.sh
```

Проверяются healthz/openapi, /metrics, auth/CSRF/сессии, серверы и uptime_24h_pct,
events/incidents, выходы (external creds + check), сайты (Anthropic allowlist, RU-lock),
профиль маршрутизации, /routing/check, /routing/apply + ревизия с entry-конфигом,
клиенты (link/profile/qr), подписки /sub в форматах sing-box/clash/v2ray, agent-auth.
Временные ресурсы удаляются, профиль маршрутизации восстанавливается. Подробнее — SETUP.md §14.0.

## Переменные и деплой

См. `.env.example`, `SETUP.md` (развёртывание с нуля) и `docs/RUNBOOK.md` (эксплуатация,
ротация секретов, бэкапы, откаты).
