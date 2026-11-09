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

## Переменные и деплой

См. `.env.example`, `SETUP.md` (развёртывание с нуля) и `docs/RUNBOOK.md` (эксплуатация,
ротация секретов, бэкапы, откаты).
