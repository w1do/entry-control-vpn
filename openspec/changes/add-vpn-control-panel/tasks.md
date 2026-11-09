# Tasks: add-vpn-control-panel

Легенда: `[ ]` — не начато, `[x]` — готово. Каждая задача ссылается на capability в `specs/`.

## 1. Фундамент

- [ ] 1.1 Скелет репозитория: `backend/`, `frontend/`, `agent/`, `deploy/`, `Makefile` (`deployment`)
- [ ] 1.2 Настройки приложения через `.env` (Pydantic Settings), валидация обязательных переменных, `.env.example` (`deployment`, `panel-auth`)
- [ ] 1.3 CI: ruff + mypy + pytest (backend), eslint + tsc + vitest (frontend), `go test` (agent), сборка образов (`deployment`)
- [ ] 1.4 Базовый Dockerfile (multi-stage: frontend build → static, backend), docker-compose для Dokploy (`deployment`)

## 2. Backend core (`panel-auth`, `observability`)

- [ ] 2.1 FastAPI-приложение, роутеры `/api/v1`, `/agent/v1`, `/healthz`; middleware: request-id, логирование JSON
- [ ] 2.2 SQLAlchemy 2 модели + первая миграция Alembic: `users`, `servers`, `exits`, `clients`, `site_rules`, `routing_revisions`, `jobs`, `events`, `metrics`, `incidents`
- [ ] 2.3 Аутентификация: bootstrap админа из `ADMIN/PASSWORD`, argon2id, серверные сессии, cookie, CSRF, rate limit, смена пароля (`panel-auth`)
- [ ] 2.4 Фреймворк фоновых jobs (in-process + БД-статусы): создание, прогресс, логи, отмена, ретраи
- [ ] 2.5 SSE-стрим прогресса job'а (`GET /api/v1/jobs/{id}/stream`)
- [ ] 2.6 Журнал безопасности и аудит действий (`panel-auth`)

## 3. SSH-провижининг (`server-provisioning`)

- [ ] 3.1 SSH-клиент (asyncssh): пароль/ключ, keepalive, таймауты, шифрование кредов (Fernet) в БД
- [ ] 3.2 Preflight: ОС/архитектура, sudo, порты, существующая установка, свободное место
- [ ] 3.3 Шаги bootstrap: создание `~/config/vpn`, загрузка Xray и `vpn-agent`, юниты systemd, начальный конфиг, firewall (nft/ufw), запуск и проверка `is-active`
- [ ] 3.4 Идемпотентность шагов, повтор с backoff, частичный прогресс
- [ ] 3.5 Регистрация узла: выпуск токена, проверка первого heartbeat, статусы online/degraded/offline
- [ ] 3.6 Удаление: uninstall-скрипт/агентская команда, проверка «каталог удалён, юниты сняты», статус `delete_pending`, force-delete (`server-provisioning`)
- [ ] 3.7 Действия: переприменить конфиг, перезапустить сервисы, переустановить, диагностика

## 4. Агент `vpn-agent` (`node-agent`)

- [ ] 4.1 Каркас Go: конфиг `~/config/vpn/etc/agent.json`, JSON-логи, флаги `serve`/`uninstall`/`diagnose`
- [ ] 4.2 Цикл: register → heartbeat (30 с) → poll конфигурации (10 с) → apply при новой ревизии
- [ ] 4.3 Атомарное применение: `xray -test` → swap → restart → health → rollback (`node-agent`)
- [ ] 4.4 Сбор метрик: `QueryStats`/`GetUsersStats` (online+IP)/`GetSysStats` (uptime) через StatsService, парсинг access/error-логов для accepted/blocked/rejected, delta-базлайн счётчиков, буфер и отправка батчей
- [ ] 4.5 Самообновление с SHA-256 и откатом
- [ ] 4.6 Uninstall (юниты, каталог, firewall) и диагностический отчёт
- [ ] 4.7 systemd-юнит с hardening; сборка linux/amd64 + linux/arm64

## 5. Движок конфигурации Xray (`exit-routing`, `client-access`)

- [ ] 5.1 Генератор entry-конфига: VLESS+REALITY inbound + sniffing (destOverride/routeOnly), API/StatsService, outbounds (block первым/direct/exits), DNS-политика
- [ ] 5.2 Генератор exit-конфига: VLESS+REALITY interconnect inbound (свои ключи, порт 18443), firewall allowlist entry-IP, egress
- [ ] 5.3 Компилятор правил в порядке: private/metadata block → site rules (allowlist) → RU-direct (locked) → RU-IP (IPIfNonMatch) → default exit → catch-all block; префиксные теги `rule-<id>-…` для балансировщиков
- [ ] 5.4 Детерминированная сериализация + SHA-256 ревизии
- [ ] 5.5 Golden-тесты: RU → direct; Anthropic → только allowlist; недоступность allowlist → block; отсутствие default → block all; зарубежный домен, резолвящийся в RU-IP, не уходит в direct; отсутствие direct-утечек (`exit-routing`)
- [ ] 5.6 Пайплайн валидации `xray -test` на целевых узлах и атомарная раскатка с откатом
- [ ] 5.7 Обновление geosite/geoip-датасетов (фиксированные источники, ручной триггер + расписание)
- [ ] 5.8 Golden-тест клиентского профиля: RU-домены → `direct`, остальное → `proxy`; проверка DNS-правил
- [ ] 5.9 Пиннинг Xray 26.11.x и geodata: sha256, предыдущая копия датасета, `-test`, канареечный домен после обновления
- [ ] 5.10 Подбор REALITY-dest (TLS 1.3/H2, та же юрисдикция) со сканированием и канареечным подключением

## 6. Выходы и маршрутизация — API/UX (`exit-routing`)

- [ ] 6.1 CRUD выходов: добавление из сервера, регион/метка, enable/disable, health-история
- [ ] 6.2 Health-check выходов (30 с) с фактическим exit-IP и задержкой
- [ ] 6.3 CRUD site rules + пресеты (Anthropic, OpenAI, Google, Telegram, GitHub, custom)
- [ ] 6.4 Валидация профиля и activation gate (`exit-routing`)
- [ ] 6.5 Apply job: компиляция → валидация → раскатка на entry → health → rollback при провале
- [ ] 6.6 Эндпоинт «Проверить маршрут» (домен → правило/выход/block)
- [ ] 6.7 История ревизий и diff
- [ ] 6.8 CRUD внешних выходов (HTTP CONNECT/SOCKS5): креды, регион, ожидаемый IP, sticky-сессия
- [ ] 6.9 Health-check фактического exit-IP/страны для внешних выходов + блокировка при несовпадении
- [ ] 6.10 Учёт трафика по внешнему выходу и бюджет провайдера с инцидентами

## 7. Клиенты и QR (`client-access`)

- [ ] 7.1 CRUD клиентов: uuid, email-тег, статус, лимит, привязка ко всем entry
- [ ] 7.2 Добавление/удаление пользователя через Xray API без рестарта + replay desired-state клиентов после рестарта/применения конфига (`adu` не персистентен)
- [ ] 7.3 Генератор `vless://` ссылки (reality-параметры)
- [ ] 7.4 QR: SVG/PNG-эндпоинт + модалка в UI
- [ ] 7.5 Учёт и лимит трафика клиента, автоотключение при превышении
- [ ] 7.6 Генератор умного профиля: sing-box JSON (rule-set `geosite-category-ru`/`geoip-ru` → direct) и Clash Meta YAML
- [ ] 7.7 Подписка `GET /sub/{token}` с выбором формата по `User-Agent`, персональный отзываемый токен, rate limit
- [ ] 7.8 QR-коды двух режимов (подписка/умный профиль и `vless://`) в модалке подключения

## 8. Наблюдаемость — backend (`observability`)

- [ ] 8.1 Приём метрик `POST /agent/v1/metrics` с валидацией токена
- [ ] 8.2 Агрегация (15s → 1m → 5m → 1h), ретенция, фоновый job свёртки
- [ ] 8.3 Расчёт uptime по heartbeat-окнам, список downtime-интервалов
- [ ] 8.4 Агрегация ошибок по категориям и топ-домены отказов
- [ ] 8.5 Инциденты: создание/ack/resolve, связь с узлами и выходами
- [ ] 8.6 `GET /metrics` (Prometheus) с `METRICS_TOKEN`
- [ ] 8.7 API дашборда: сводка, таймсерии (range, group_by)
- [ ] 8.8 Корректность счётчиков: кумулятивные значения → дельты, защита от рестарта/сброса, int64 без потери точности, один коллектор на узел

## 9. Frontend (`panel-ui`)

- [ ] 9.1 Скаффолд: Vite + React 19 + TS + TanStack Router/Query + shadcn/ui + Recharts + qrcode.react
- [ ] 9.2 Login-страница и сессионный guard
- [ ] 9.3 Layout: сайдбар, шапка со статусом маршрутизации, тема light/dark
- [ ] 9.4 Dashboard: карточки, график трафика, инциденты
- [ ] 9.5 Servers: список, карточка, мастер добавления (SSH → preflight → роли → прогресс SSE → результат)
- [ ] 9.6 Sites: RU-locked секция, default exit, site rules с «шестерёнкой» allowlist, пресеты, баннер-гейт
- [ ] 9.7 Exits: реестр выходов, здоровье, enable/disable
- [ ] 9.8 Clients: список, карточка, QR/ссылка/копирование, лимит
- [ ] 9.9 Settings: ревизии, диагностика, аудит, смена пароля
- [ ] 9.10 Playwright: вход, добавление сервера (fake SSH), правило Anthropic, удаление сервера

## 10. Деплой (`deployment`)

- [ ] 10.1 Compose для Dokploy: `app`, опционально `db`, том `/data`, labels Traefik, healthcheck
- [ ] 10.2 Entrypoint: миграции + статика SPA с fallback
- [ ] 10.3 Раздача артефактов агента/Xray с checksum и версией
- [ ] 10.4 Бэкап/восстановление SQLite, инструкция
- [ ] 10.5 Документация развёртывания (Dokploy, домен, TLS, переменные)
- [ ] 10.6 Гигиена секретов: `.env` в `.gitignore`, `.env.example` только с плейсхолдерами, secret-scan в CI, процедура ротации пароля/`SECRET_KEY`/`ENCRYPTION_KEY`

## 11. Качество и приёмка

- [ ] 11.1 Тесты backend: auth, provisioning (mock SSH), routing compiler golden, metrics aggregation
- [ ] 11.2 Стенд: docker-контейнер с sshd + Xray, интеграционный прогон провижининга и раскатки
- [ ] 11.3 E2E-сценарий приёмки из proposal (7 критериев успеха)
- [ ] 11.4 Проверка безопасности: секреты в логах, CSRF, rate limit, SSH-ключи
- [ ] 11.5 Soak-тест: 3 узла, 10 клиентов, 24 ч, стабильность метрик и памяти
- [ ] 11.6 Runbook: деградации, откат релиза, восстановление БД, «узел не удалился»

## 12. Документация и архивация

- [ ] 12.1 README: быстрый старт (Dokploy → админ → сервер → выход → правило → клиент)
- [ ] 12.2 docs/ARCHITECTURE.md и docs/RUNBOOK.md
- [ ] 12.3 Архивация change: дельты → `openspec/specs/`, `changes/archive/`
