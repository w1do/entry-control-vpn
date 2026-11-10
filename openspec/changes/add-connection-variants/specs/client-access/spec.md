# Client access: transport variants

## ADDED Requirements

### Requirement: Multiple transport profiles per entry node
Система ДОЛЖНА генерировать для entry-узла несколько VLESS+REALITY inbound'ов с одинаковыми
REALITY-ключами: TCP+Vision (443), TCP без Vision (2096), XHTTP (2053), gRPC (8443).

#### Scenario: Compile entry config with variants
- **WHEN** компилируется конфиг entry-узла
- **THEN** в `inbounds` присутствуют теги `vless-in`, `vless-tcp`, `vless-xhttp`, `vless-grpc`
  с портами 443/2096/2053/8443, и только у `vless-in` клиенты имеют `flow=xtls-rprx-vision`.

### Requirement: Per-variant connection links and QR
Система ДОЛЖНА отдавать список вариантов подключения, где каждый вариант содержит готовую
VLESS-ссылку с корректным `type` (tcp/xhttp/grpc), портом и параметрами транспорта.

#### Scenario: Connection payload
- **WHEN** админ открывает подключение клиента
- **THEN** `GET /api/v1/clients/{id}/connection` возвращает 4 варианта с уникальными портами,
  и QR каждого варианта кодирует соответствующую VLESS-ссылку.

#### Scenario: Unsupported client
- **WHEN** клиент не поддерживает Vision/XHTTP
- **THEN** админ сканирует QR варианта, который поддерживается клиентом (например, gRPC или TCP без Vision),
  и подключение работает без ручного редактирования ссылки.
