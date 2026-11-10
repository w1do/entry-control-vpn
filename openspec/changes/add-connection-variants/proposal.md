# Change: connection transport variants with per-variant QR

## Why

Сейчас панель выдаёт один профиль — VLESS TCP + REALITY + XTLS Vision. Клиенты вроде Incy
не умеют Vision (а иногда и TCP+REALITY), зато умеют XHTTP/gRPC. Из-за этого QR из панели
добавляет нерабочий профиль, хотя те же параметры вручную импортируются.

Нужно, чтобы панель генерировала несколько транспортов на одном entry-узле и показывала
отдельный QR для каждого, чтобы пользователь сканировал именно тот профиль, который
поддерживает его клиент.

## What Changes

1. **Compiler**: entry-конфиг дополнительно содержит inbound'ы XHTTP (2053), gRPC (8443) и
   TCP без Vision (2096) с теми же REALITY-ключами и клиентами (без `flow`).
2. **Links**: генерация VLESS-ссылки для каждого транспорта; новый эндпоинт
   `GET /api/v1/clients/{id}/connection`, возвращающий список вариантов.
3. **UI**: в модалке подключения — список вариантов, у каждого свой QR, подпись транспорта,
   кнопка копирования и скачивания; «умный профиль» остаётся отдельной вкладкой.
4. **Provisioning**: ufw открывает порты вариантов (2053/tcp, 2096/tcp, 8443/tcp).

## Capabilities

| Capability | Что покрывает |
|---|---|
| `client-access` | Мульти-транспортные профили, ссылки, per-variant QR |

## Impact

- Backend: `app/compiler/`, `app/services/links.py`, `app/api/clients.py`, `provisioning.py`.
- Frontend: `connect-dialog.tsx`, `api.ts`, `api-types.ts`.
- Совместимость: старые `/link`, `/qr.svg`, `/profile`, `/sub` продолжают работать и отдают TCP+Vision.
- Без миграций БД: набор вариантов — константы компилятора.
