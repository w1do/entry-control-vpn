# VPN Control Panel — Frontend

SPA панели управления Xray VLESS+REALITY: React 19 + Vite + TypeScript + TanStack Router (file-based)
+ TanStack Query + Tailwind CSS v4 + локальные shadcn-подобные компоненты (Radix) + Recharts + qrcode.react.

Русский интерфейс, светлая/тёмная тема, ленивые маршруты (auto code-splitting TanStack Router).

## Структура

```
src/
  routes/            файловые маршруты TanStack Router (routeTree.gen.ts генерируется автоматически)
  pages/             страницы: login, dashboard, servers (list/detail/wizard), exits, sites, clients, settings
  components/ui/     button, card, input, select, dialog, table, badge, switch, tabs, tooltip, toast...
  components/common/ job-progress (SSE), routing-gate-banner, traffic-chart, stat-card, incidents-list
  components/layout/ сайдбар, шапка (статус маршрутизации, инциденты, тема, выход), app shell
  components/sites/  allowlist-picker (упорядоченный multi-select выходов)
  components/clients/ connect-dialog (QR/ссылка/профиль), client-form-dialog
  lib/               api (fetch + CSRF + 401 + error envelope), api-types, queries, use-job-stream (SSE),
                     mock-api (VITE_USE_MOCKS=1), theme, toast, utils
```

## Команды

```bash
npm run dev        # Vite dev-server, proxy /api -> http://127.0.0.1:8000
npm run build      # production-сборка в dist/
npm run typecheck  # tsc --noEmit
```

Мок-режим (работа без backend, демо-данные в памяти):

```bash
VITE_USE_MOCKS=1 npm run dev
# вход: admin@vpn.local / password12345
```

## API-клиент

- same-origin `/api/v1`, `credentials: "include"`;
- `X-CSRF-Token` из `GET /auth/me` (и обновляется после `POST /auth/password`, backend возвращает новый токен);
- `401` → редирект на `/login` (кроме запроса `/auth/me`, чтобы не было цикла);
- ошибки — envelope `{"error":{"code","message"}}` → `ApiError` + toast;
- SSE-прогресс job'ов: `GET /jobs/{id}/stream` (`progress`/`done`/`error`). События `progress` не содержат
  `progress`, поэтому хук параллельно опрашивает `GET /jobs/{id}` и показывает прогресс-бар; при обрыве SSE
  остаётся поллинг;
- QR-коды подключения отдаёт backend (`GET /clients/{id}/qr.svg?mode=smart|vless&server_id=`):
  smart кодирует absolute URL подписки, vless — `vless://`. Фронт показывает SVG и скачивает его;
  при ошибке backend для vless есть локальный fallback через `qrcode.react`.

## Установка зависимостей в этом окружении (WSL без интернета)

WSL не имеет прямого интернета, а `npm` в WSL PATH может резолвиться в Windows-install (тогда Vite
запускается Windows-Node и падает на нативных биндингах). Рабочий способ:

1. Windows: `C:\Users\pulid\vpn-deps\frontend-install` — скопировать `package.json`;
2. `npm.cmd install --no-audit --no-fund --os=linux --cpu=x64 --ignore-scripts`;
3. `tar.exe -czf node_modules.tgz node_modules package-lock.json`, распаковать в `frontend/`;
4. npm не ставит linux-опциональные нативные биндинги при установке на Windows — доставить вручную
   (`@rolldown/binding-linux-x64-gnu`, `@tailwindcss/oxide-linux-x64-gnu`, `lightningcss-linux-x64-gnu`)
   из tarball'ов registry и `chmod +x node_modules/.bin/*`;
5. запускать npm через WSL-обёртку:
   `exec /usr/bin/node "/mnt/c/Program Files/nodejs/node_modules/npm/bin/npm-cli.js" "$@"`.

Для чистой Linux-среды (Docker/CI) достаточно обычного `npm ci && npm run build`.

## Бюджет бандла

Начальный JS (`dist/assets/index-*.js`) ≈ **83 КБ gzip** при лимите 250 КБ; Recharts/страницы/QR
вынесены в ленивые чанки (Dashboard ≈ 113 КБ gzip, Clients/QR ≈ 11 КБ gzip и т.д.).

## Интеграция с backend проверена вживую

Локальный прогон (backend `uvicorn app.main:app` на 8000 + `npm run dev` на 5176 с прокси
`/api`, `/healthz`, `/sub`) с реальными API-вызовами и headless Chrome: вход, дашборд
(маршрутизация активна), серверы, карточка сервера, выходы (диалог удаления с `used_by_sites`),
сайты (ревизия применена, проверка маршрута возвращает скомпилированное правило), клиенты
(connect-модалка: backend-QR smart/vless, URL подписки с копированием), настройки (журнал аудита
из `GET /events`, ревизии с `applied_at`, детали ревизии). JS-исключений и console.error — 0;
единственный 4xx/5xx — ожидаемый 501 у `/incidents` (в работе, task-5).

## Известные расхождения с backend

| Что | Состояние |
|---|---|
| `GET /api/v1/incidents` + ack/resolve | ещё не реализовано (stub 501, task-5); дашборд/колокольчик показывают пустое состояние с ошибкой, UI готов |
| `GET /api/v1/clients/{id}` | нет эндпоинта — карточка клиента берёт данные из `GET /clients` (список) |
| `PATCH /api/v1/exits/{id}` | меняет enabled/region/expected_exit_ip/bandwidth_budget_bytes/username/password; label/endpoint/protocol неизменяемы — в UI при редактировании disabled |
| SSE `progress` | события без поля `progress` — фронт параллельно опрашивает `GET /jobs/{id}` для прогресс-бара |
| Аудит | `GET /events` отдаёт поле `payload` — нормализуется в `payload_json`; fallback `/audit` оставлен на случай смены пути |

Исправлено backend'ом и учтено фронтом: `uptime_24h_pct` в `/servers`, `sub_token`/`last_seen_at`
в `/clients`, credentials внешних выходов (шифруются; PATCH принимает username/password),
`applied_at` в ревизиях и `updated_at` в профиле. Health-семантика выхода: пригоден, если
`enabled && health !== "down"` (как в backend), поэтому «не проверен» не блокирует маршрутизацию.

Все страницы используют реальные пути/поля из `docs/INTERFACES.md §2.3` и ответов `backend/app/api/*`.

