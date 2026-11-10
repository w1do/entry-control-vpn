# Tasks: add-connection-variants

## 1. Compiler
- [x] 1.1 `app/compiler/transports.py`: `TransportVariant`, `VARIANTS`, `EXTRA_VARIANTS`
- [x] 1.2 `compiler/inbounds.py`: `transport_inbound()` (xhttp/grpc/tcp без flow)
- [x] 1.3 `compiler/entry.py`: добавить extra-inbounds в конфиг
- [x] 1.4 Тест: entry-конфиг содержит 4 inbound'а с нужными портами/flow

## 2. Links/API
- [x] 2.1 `build_vless_url` с вариантом (type/path/serviceName/flow)
- [x] 2.2 `GET /clients/{id}/connection` со списком вариантов
- [x] 2.3 Тест API/ссылок: уникальные порты, type/path/serviceName/flow

## 3. Provisioning
- [x] 3.1 ufw allow 2053/tcp, 2096/tcp, 8443/tcp

## 4. Frontend
- [x] 4.1 api-types/api: `ClientConnection`
- [x] 4.2 connect-dialog: список вариантов, per-variant QR, copy/download
- [x] 4.3 typecheck + build

## 5. Rollout
- [x] 5.1 pytest/ruff, frontend build
- [x] 5.2 применить ревизию, проверить 4 транспорта с ПК
- [x] 5.3 commit + push, CI green
