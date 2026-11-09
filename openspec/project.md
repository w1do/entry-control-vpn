# VPN Control Panel

Персональный self-hosted сервис управления VPN: добавляет серверы по SSH, сам разворачивает на них узел Xray (VLESS+REALITY), настраивает выходы (exit-серверы по регионам), per-site проксирование зарубежных сайтов, выдаёт клиентам VLESS-ссылки/QR и показывает трафик, подключения, ошибки и аптайм.

## Пользователь

Один администратор (владелец). Регистрация и многопользовательский режим отсутствуют: логин/пароль берутся из `.env` при первом запуске.

## Принципы

1. **Fail-closed.** Если выход недоступен или не настроен — трафик блокируется, а не уходит напрямую. Российский IP не должен использоваться как выход никогда.
2. **Один источник состояния на узле.** Всё, что установлено/настроено на сервере, лежит в `~/config/vpn/` (бинарники, конфиги, логи, состояние). Удаление узла = удаление этого каталога и юнитов systemd; других следов не остаётся.
3. **Панель — единственная точка настройки.** Маршрутизация, выходы, клиенты и конфиги генерируются на бэкенде из БД и применяются на узлах атомарно (validate → swap → restart → health-check → rollback).
4. **Узел автономен.** При недоступности панели узел продолжает работать на последней применённой конфигурации; панель только перезаписывает её.
5. **Лёгкость.** SQLite по умолчанию, один контейнер приложения, минимальный фронтенд (shadcn + TanStack), без тяжёлых платформенных зависимостей.
6. **RU-трафик не проксируется.** Российские домены и IP обслуживаются напрямую: в основном режиме — с устройства клиента (умный профиль), в fallback — с entry-узла, но никогда через exit-выходы.

## Технологический стек

| Слой | Выбор |
|---|---|
| Backend | FastAPI (Python 3.12), Pydantic v2, SQLAlchemy 2 + Alembic, APScheduler, asyncssh, arq/celery не используются (in-process jobs) |
| БД | SQLite через `aiosqlite` (по умолчанию, том Dokploy; URL `sqlite+aiosqlite:////data/app.db`) / PostgreSQL 16 (опция через `DATABASE_URL`) |
| Frontend | React 19 + Vite + TypeScript, TanStack Router + Query, shadcn/ui, Recharts, qrcode.react |
| Data plane | Xray-core 26.11.x (VLESS + XTLS-Vision + REALITY на entry и на interconnect к exit; StatsService; SS-2022 не используем — deprecated в 26.x) |
| Agent | Go 1.23, статический `vpn-agent` (linux/amd64, linux/arm64), systemd |
| Deploy | Dokploy → Traefik → docker-compose (api + db опционально) |
| Тесты | pytest, golden-тесты генератора конфигов, Playwright (E2E), docker-стенд с sshd+Xray |

### Версии (зафиксированы на 2026-11-09)

Python 3.12.16 · FastAPI 0.162.2 · SQLAlchemy 2.1.8 · Alembic 1.20.1 · asyncssh 2.24.1 · cryptography 50.0.2 · argon2-cffi 25.1.0 · sse-starlette 3.7.0 · APScheduler 3.11.3 · grpcio 1.85.1 · React 19.3.1 · Vite 8.3.7 · TanStack Router 1.170.46 · TanStack Query 5.107.1 · shadcn 4.23.0 · Tailwind 4.3.5 · Recharts 3.10.1 · qrcode.react 4.2.0 · Xray-core 26.11.9 · Go 1.23.

## Глоссарий

- **Server / узел** — VPS, добавленный по SSH. Хранит роль(и) entry/exit.
- **Entry** — узел, на котором слушает VLESS+REALITY inbound для клиентов (порт 443/tcp).
- **Exit** — exit-узел или внешний резидентский прокси, через который entry выпускает трафик сайтов; определяет, какой exit-IP увидит целевой сервис.
- **Site rule** — правило маршрутизации домена/geosite на список разрешённых exit'ов (fail-closed).
- **Routing profile** — полный набор правил, компилируемый в конфиг Xray.
- **Client** — админ-определённая учётка VLESS (uuid), статистика трафика привязана к `email` в Xray.
- **Умный профиль** — клиентская конфигурация (sing-box JSON / Clash Meta YAML) с RU-bypass: российские домены/IP идут напрямую с устройства, остальное — через VLESS+REALITY.
- **Agent** — демон на узле: применяет конфиги, собирает метрики, репортит в панель.
- **Base dir** — `~/config/vpn`, корень всего состояния узла.

## Структура репозитория (целевая)

```
backend/            FastAPI-приложение, миграции, тесты
frontend/           React-панель
agent/              vpn-agent (Go)
deploy/             docker-compose для Dokploy, Dockerfile, traefik labels
openspec/           спецификации (этот каталог)
research/           материалы исследования (источник design.md)
```

## Соглашения

- API версионируется: `/api/v1/...`; агент — отдельный префикс `/agent/v1/...`.
- Все секреты (SSH-пароли/ключи, interconnect-ключи REALITY, agent-токены) хранятся зашифрованными (`ENCRYPTION_KEY`, Fernet) и никогда не попадают в логи/ответы API.
- Время в БД — UTC (ISO-8601). UI показывает локальную зону.
- Логи — JSON в stdout; идентификаторы job/request в каждой записи.
- Конфиг Xray генерируется детерминированно: одинаковое состояние БД → байт-идентичный JSON (для ревизий и diff).

## OpenSpec workflow

- Источник правды по требованиям: `openspec/specs/` (после архивации изменений).
- Активные изменения: `openspec/changes/<change-id>/` (proposal.md, design.md, tasks.md, specs/<capability>/spec.md).
- Формат: `### Requirement:`, `#### Scenario:` (WHEN/THEN), дельты `## ADDED/MODIFIED/REMOVED Requirements`.
- Реализация начинается с `tasks.md`, каждая задача ссылается на capability.

## Вне скоупа

- Мультитенантность, биллинг, реклама.
- Поддержка Windows/macOS как узлов (только Linux).
- Клиентские приложения (используем штатные VLESS-клиенты по ссылке/QR).
- Обход конкретных юридических ограничений сверх fail-closed маршрутизации.
