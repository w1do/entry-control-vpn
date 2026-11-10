# syntax=docker/dockerfile:1.7
# =============================================================================
# VPN Control Panel — production-образ.
#
# Сборка (context — корень репозитория):
#   docker compose -f deploy/docker-compose.yml build
# Или напрямую:
#   docker build -f deploy/Dockerfile -t vpn-control-panel .
#
# Один контейнер: собранный React SPA + FastAPI. Dokploy собирает этот же
# Dockerfile при каждом redeploy.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1. Frontend: React 19 + Vite -> статический dist/
# -----------------------------------------------------------------------------
FROM node:24-alpine AS frontend-build

# libc6-compat нужен нативным бинарникам (rollup/oxide) в musl-окружении.
RUN apk add --no-cache libc6-compat

WORKDIR /build/frontend

# Копируем frontend целиком: package-lock.json может отсутствовать на
# промежуточных итерациях, поэтому установка ниже условная.
COPY frontend/ /build/frontend/

RUN set -eux; \
    if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund; \
    else \
        npm install --no-audit --no-fund; \
    fi; \
    npm run build; \
    test -f dist/index.html

# -----------------------------------------------------------------------------
# Stage 2. vpn-agent: статический бинарник (linux/amd64), нужен провижинингу.
# -----------------------------------------------------------------------------
FROM golang:1.23-alpine AS agent-build

WORKDIR /src
COPY agent/ /src/
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64     go build -trimpath -ldflags "-s -w" -o /out/vpn-agent-linux-amd64 ./cmd/vpn-agent

# -----------------------------------------------------------------------------
# Stage 3. Артефакты узлов: Xray-core + geodata. Кладутся в образ, entrypoint
# заполняет ими пустой том /data/artifacts — ручной make artifacts не нужен.
# -----------------------------------------------------------------------------
FROM alpine:3.21 AS node-artifacts

ARG XRAY_VERSION=26.11.9

RUN apk add --no-cache ca-certificates curl unzip

WORKDIR /out
RUN set -eux;     curl -fL --retry 3 --retry-delay 2 -o xray.zip       "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip";     curl -fL --retry 3 --retry-delay 2 -o xray.zip.dgst       "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip.dgst";     digest="$(sed -n 's/^SHA2-256= *//p' xray.zip.dgst | head -n 1)";     [ -n "$digest" ];     printf '%s  %s\n' "$digest" xray.zip | sha256sum -c -;     unzip -q xray.zip -d xray;     install -m 0755 xray/xray /out/xray-linux-amd64;     rm -rf xray xray.zip xray.zip.dgst

RUN set -eux;     base="https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download";     for f in geoip.dat geosite.dat; do       curl -fL --retry 3 --retry-delay 2 -o "/out/$f" "$base/$f";     done

COPY --from=agent-build /out/vpn-agent-linux-amd64 /out/vpn-agent-linux-amd64

# -----------------------------------------------------------------------------
# Stage 4. Runtime: Python 3.12 + FastAPI + статика SPA
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# ENV, а не ARG: значения должны быть в контейнере по умолчанию.
# DATABASE_URL можно переопределить в deploy/.env (env_file перекрывает ENV).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    APP_ENV=production \
    DATA_DIR=/data \
    NODE_ARTIFACTS_DIR=/opt/node-artifacts \
    DATABASE_URL=sqlite+aiosqlite:////data/app.db \
    TZ=UTC

# Non-root пользователь (UID/GID 10001). /data принадлежит ему же, поэтому
# named volume при первом монтировании наследует права из образа.
RUN groupadd --system --gid 10001 panel \
    && useradd --system --uid 10001 --gid panel --home-dir /app --shell /usr/sbin/nologin panel

WORKDIR /app

# Зависимости отдельным слоем — кэш не сбрасывается при изменении кода.
# requirements.txt — источник истины (синхронизирован с pyproject.toml).
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && (python -c "import asyncpg" || pip install --no-cache-dir "asyncpg==0.32.0")

# Приложение целиком: app/, alembic.ini, alembic/ (миграции); тесты
# отсекаются .dockerignore.
COPY backend/ /app/

# Собранный SPA. backend/app/main.py монтирует static из каталога пакета
# (STATIC_DIR по умолчанию — app/static) и делает SPA-fallback.
COPY --from=frontend-build /build/frontend/dist /app/app/static

# Артефакты узлов (Xray, vpn-agent, geodata) уже в образе: entrypoint
# копирует их в /data/artifacts при первом старте/пустом томе.
COPY --from=node-artifacts /out/ /opt/node-artifacts/

# Точка входа: alembic upgrade head -> uvicorn app.main:app (workers=1).
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && mkdir -p /data/artifacts \
    && chown -R panel:panel /app /data

USER panel

# Наружу порт не публикуется (только expose в compose; снаружи — Traefik).
EXPOSE 8000

# Тот же healthcheck продублирован в compose (Dokploy показывает статус по нему).
# /healthz проверяет процесс и БД, но не удалённые узлы (иначе падение exit-узла
# снимало бы маршрут с самой панели).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).read()"]

# PID 1 — entrypoint; uvicorn запускается через exec и получает SIGTERM напрямую.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
