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

Образ панели уже содержит всё, что нужно провижинингу: Xray 26.11.9, vpn-agent
(linux/amd64) и geodata. Dockerfile собирает агента в стадии golang:1.23-alpine и
скачивает Xray/geodata в отдельной стадии; entrypoint при первом старте копирует
четыре файла в /data/artifacts (том panel-data). Ручной make artifacts для Dokploy
не нужен.

| Файл | Что это |
|---|---|
| xray-linux-amd64 | Xray-core 26.11.9 (сверяется SHA2-256 из .dgst) |
| vpn-agent-linux-amd64 | статический vpn-agent, собран из agent/ |
| geoip.dat / geosite.dat | Loyalsoldier/v2ray-rules-dat (latest) |

Обновить версию Xray можно build-arg-ом XRAY_VERSION (по умолчанию 26.11.9).
Для офлайн-сборки или кастомных артефактов остаётся ручной путь: make artifacts,
затем скопировать файлы в /data/artifacts контейнера (make artifacts-copy);
entrypoint не перезаписывает уже существующие файлы.

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
