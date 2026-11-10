# Design: connection transport variants

## Decisions

1. **Один источник правды**: `backend/app/compiler/transports.py` содержит `TransportVariant`
   (id, label, network, port, flow, path/serviceName). Компилятор строит из него inbounds,
   links — VLESS-ссылки, API — список вариантов. Никаких дублирующихся портов в UI.
2. **Inbounds**: основной `vless-in` (443, TCP, Vision) не меняется. Дополнительные:
   - `vless-tcp` — TCP REALITY, клиенты без `flow`;
   - `vless-xhttp` — `network=xhttp`, `xhttpSettings={path:"/vpn-xhttp",mode:"auto"}`;
   - `vless-grpc` — `network=grpc`, `grpcSettings={serviceName:"vpn-grpc",multiMode:false}`.
   Все используют те же REALITY-ключи и shortIds, поэтому ссылки отличаются только транспортом/портом.
3. **Flow**: Vision только на основном inbound. XHTTP/gRPC/TCP-variant получают клиентов без flow.
4. **API**: `GET /clients/{id}/connection?server_id=` → `{variants:[{id,label,type,port,flow,url}], smart:{subscription_url}}`.
5. **QR**: frontend рендерит QR локально (`qrcode.react`) из `url` каждого варианта —
   исключает рассинхронизацию backend-QR и позволяет выбрать нужный транспорт.
6. **Откат**: удаление variants из компилятора автоматически убирает inbounds при следующем apply;
   firewall-правила остаются безопасными (allow только нужных портов).

## Alternatives

- Отдельные entry-серверы под каждый транспорт — дороже и не нужно: Xray слушает несколько портов.
- Хранить варианты в БД — лишняя миграция; набор фиксирован версией Xray.
