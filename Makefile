# =============================================================================
# VPN Control Panel — команды разработчика.
#
#   make help            список целей
#   make dev             backend с autoreload (http://127.0.0.1:8000)
#   make test            pytest backend + go test agent
#   make lint            ruff check + go vet
#   make build           frontend dist + бинарники агента
#   make agent           vpn-agent linux/amd64 + linux/arm64
#   make artifacts       Xray 26.11.9 + vpn-agent + geodata -> ./artifacts (для узлов)
#   make artifacts-copy  залить артефакты в /data/artifacts контейнера app
#   make up / down       docker compose (deploy/.env, Dokploy-совместимо)
#   make secret-scan     проверка репозитория на утёкшие секреты
#
# Python-окружение: .venv (создаётся Lead'ом/разработчиком, см. README.md).
# Прямого интернета из WSL нет — npm/go/pip и make artifacts выполняются там, где
# есть сеть (Windows-сторона/CI); см. docs/INTERFACES.md §0.
# =============================================================================
SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- инструменты (переопределяемы: make PYTHON=python3 test) -----------------
PYTHON      ?= .venv/bin/python
PYTEST      ?= .venv/bin/pytest
RUFF        ?= .venv/bin/ruff
GO          ?= go

# --- deploy ------------------------------------------------------------------
COMPOSE     ?= docker compose -f deploy/docker-compose.yml
COMPOSE_ENV ?= deploy/.env

# --- agent -------------------------------------------------------------------
AGENT_LDFLAGS ?= -s -w

# --- артефакты узлов ---------------------------------------------------------
ARTIFACTS_DIR ?= artifacts
XRAY_VERSION  ?= 26.11.9

.PHONY: help dev dev-ui test lint build agent agent-amd64 agent-arm64 \
        artifacts artifacts-copy up down secret-scan e2e-node e2e-provisioning

# =============================================================================
help: ## Показать этот список целей
	@printf 'VPN Control Panel — команды разработки\n\n'
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\nПодробнее: README.md (быстрый старт), SETUP.md (развёртывание), docs/RUNBOOK.md (эксплуатация).\n'

# =============================================================================
# Разработка
# =============================================================================
dev: ## Backend с autoreload на 127.0.0.1:8000 (dev SQLite backend/dev.db)
	cd backend && \
	  ADMIN=admin@localhost \
	  PASSWORD=dev-password-placeholder-1234 \
	  SECRET_KEY=dev-secret-key-placeholder-0123456789abcdef \
	  ENCRYPTION_KEY="$$(../$(PYTHON) -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
	  DATABASE_URL="sqlite+aiosqlite:///./dev.db" \
	  PYTHONPATH=. \
	  ../$(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
	@echo ""
	@echo "UI в dev-режиме: отдельный терминал -> make dev-ui (Vite проксирует /api на :8000)"

dev-ui: ## Frontend dev server (Vite)
	cd frontend && npm run dev

# =============================================================================
# Проверки
# =============================================================================
test: ## Тесты: pytest (backend) + go test (agent)
	cd backend && ../$(PYTEST)
	@if [ -d agent ] && command -v $(GO) >/dev/null 2>&1; then \
	  cd agent && $(GO) test ./...; \
	else \
	  echo "agent: go не найден или каталог agent/ отсутствует — go test пропущен"; \
	fi

lint: ## Линт: ruff check (backend) + go vet (agent)
	$(RUFF) check backend
	@if [ -d agent ] && command -v $(GO) >/dev/null 2>&1; then \
	  cd agent && $(GO) vet ./...; \
	else \
	  echo "agent: go не найден или каталог agent/ отсутствует — go vet пропущен"; \
	fi

# =============================================================================
# Сборка
# =============================================================================
build: ## Production-сборка: frontend/dist + agent binaries
	cd frontend && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
	cd frontend && npm run build
	$(MAKE) agent

agent: agent-amd64 agent-arm64 ## Собрать vpn-agent для linux/amd64 и linux/arm64 в agent/bin/
	@ls -lh agent/bin/

agent-amd64:
	cd agent && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 $(GO) build -trimpath -ldflags "$(AGENT_LDFLAGS)" -o bin/vpn-agent-linux-amd64 ./cmd/vpn-agent

agent-arm64:
	cd agent && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 $(GO) build -trimpath -ldflags "$(AGENT_LDFLAGS)" -o bin/vpn-agent-linux-arm64 ./cmd/vpn-agent

# =============================================================================
# Артефакты узлов: Xray + vpn-agent + geoip.dat/geosite.dat
# Имена файлов жёстко ожидаются backend/app/services/provisioning.py.
# =============================================================================
artifacts: ## Скачать/собрать артефакты узлов в ./artifacts (Xray 26.11.9, geodata, vpn-agent)
	ARTIFACTS_DIR="$(ARTIFACTS_DIR)" XRAY_VERSION="$(XRAY_VERSION)" bash deploy/scripts/fetch-artifacts.sh

artifacts-check: ## Проверить наличие и контрольные суммы артефактов в ./artifacts
	ARTIFACTS_DIR="$(ARTIFACTS_DIR)" bash deploy/scripts/fetch-artifacts.sh --check

artifacts-copy: ## Скопировать ./artifacts в /data/artifacts запущенного контейнера app
	@set -e; \
	APP=$$(docker ps -qf 'name=app' -f status=running | head -n 1); \
	if [ -z "$$APP" ]; then echo "ERROR: контейнер app не запущен (сначала make up)"; exit 1; fi; \
	for f in xray-linux-amd64 vpn-agent-linux-amd64 geoip.dat geosite.dat; do \
	  if [ ! -s "$(ARTIFACTS_DIR)/$$f" ]; then echo "ERROR: нет $(ARTIFACTS_DIR)/$$f — выполните make artifacts"; exit 1; fi; \
	  docker cp "$(ARTIFACTS_DIR)/$$f" "$$APP":/data/artifacts/$$f; \
	done; \
	docker exec -u 0 "$$APP" chown -R 10001:10001 /data/artifacts; \
	docker exec "$$APP" sh -c 'ls -l /data/artifacts && sha256sum /data/artifacts/xray-linux-amd64 /data/artifacts/vpn-agent-linux-amd64 /data/artifacts/geoip.dat /data/artifacts/geosite.dat'

# =============================================================================
# Deploy (Dokploy-совместимый compose)
# =============================================================================
up: ## docker compose up -d --build по deploy/.env
	@test -f $(COMPOSE_ENV) || { echo "ERROR: $(COMPOSE_ENV) не найден. Скопируйте deploy/.env.example -> deploy/.env и заполните секреты (см. SETUP.md §5)." >&2; exit 1; }
	$(COMPOSE) --env-file $(COMPOSE_ENV) up -d --build
	@echo "Готово. Проверьте https://<PUBLIC_BASE_URL>/healthz и логи: docker compose -f deploy/docker-compose.yml logs -f app"

down: ## docker compose down (том panel-data сохраняется)
	$(COMPOSE) down --remove-orphans
	@echo "Тома не удалены: данные SQLite и артефакты сохранены. Полное удаление — см. docs/RUNBOOK.md."

# =============================================================================
# Безопасность
# =============================================================================
e2e-node: ## E2E: vpn-agent ↔ панель на локальном стенде (fake xray/systemd, без VPS)
	$(PYTHON) deploy/scripts/e2e_node_stand.py

e2e-provisioning: ## E2E: SSH-провижининг панелью локального fake-узла (asyncssh-эмулятор, без VPS)
	$(PYTHON) deploy/scripts/e2e_provisioning_stand.py

secret-scan: ## Проверить репозиторий на утёкшие секреты (без чтения deploy/.env)
	@bash deploy/secret-scan.sh
