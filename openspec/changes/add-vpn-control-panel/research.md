# Исследование: Marzban / Marzneshin / 3x-ui / Xray-core

**Срез:** 2026-11-09. Проверялся код веток по умолчанию (роутеры, генераторы конфига, коллекторы статистики), релизы и коммит-фиды, а также живой Xray: JSON прогнаны через `xray run -test`, API-команды проверены на инстансе.

Версии: Xray-core **26.11.9** (MPL-2.0), 3x-ui **3.10.2** (2026-10, GPL-3.0), Marzban **0.8.4** (2025-01, AGPL-3.0), Marzneshin **0.7.4** (2025-07, AGPL-3.0).

## Вывод

Ни одна готовая панель не моделирует ядро продукта — **«выход» (exit) и правило «домен → выход»**: у всех маршрутизация живёт в сыром Xray-JSON, а не в API-объектах. Marzban и Marzneshin считают ноду репликой входного инбаунда, а не выходом, и не имеют релизов 22 и 16 месяцев. Рекомендация: **Xray-core как движок + свой control-plane**; переиспользуем проверенные куски (генерация ссылок, дельта-учёт трафика, observatory/balancer, provisioning-паттерны), не форкаем панели.

## Marzban

- Python/FastAPI + React (Chakra UI), Xray дочерним процессом, SQLite/MySQL/MariaDB.
- REST API: `/api/admin/token` (OAuth2), `/api/users`, `/api/user/{u}` (+`/reset`, `/revoke_sub`, `/usage`), `/api/inbounds`, `/api/hosts`, `/api/system`, `/api/core`, `/api/nodes` (+`/reconnect`); подписка `/sub/{token}/` с выбором формата по UA (v2ray/clash/sing-box/outline/json), заголовки `subscription-userinfo`, `profile-title`.
- REALITY: `realitySettings` + `pbk` (ключи `xray x25519`). QR — только на фронте.
- Ноды: **Marzban-node** (REST+mTLS, 62050/62051). Панель пушит на ноду **тот же полный конфиг со всеми инбаундами** → нода = второй ВХОД, а не выход. Per-domain routing — только сырой `XRAY_JSON` + рестарт.
- Трафик: часовые агрегаты (`record_usages`, `reset=true`); нет коннектов, ошибок, аптайма.
- Живость: master заморожен, релиз 2025-01; dev-ветка до 2026-06; 7.5k★.

## Marzneshin

- Форк-переосмысление: services вместо inbounds, **marznode** (gRPC; бэкенды xray/sing-box/hysteria).
- API: `/api/{users,services,nodes,system/stats}`, `GET|PUT /api/nodes/{id}/xray/config` (можно вкатить свой routing, но ручным JSON), подписка `/sub/{username}/{key}`.
- Релиз 0.7.4 (2025-07), коммиты до 2025-10. Та же модель «нода = агент», меньше комьюнити.

## 3x-ui

- Go-монолит (панель + Xray библиотекой), SQLite/PG, **v3.10.2 (2026-10), коммиты ежедневно, 49.6k★**. Протоколы: VLESS/VMess/Trojan/SS/WireGuard/AmneziaWG/TUIC5/Hysteria2/MTProto + XHTTP, fallbacks, REALITY (включая mldsa65/PQ).
- API `/panel/api` с контрактным тестом реестра и OpenAPI: inbound'ы, clients (traffic/onlines/links/subLinks), **nodes** (mTLS/pin, heartbeat CPU/RAM/uptime/state), server (Xray config/restart/logs/observatory/`getNewX25519Cert`/`scanRealityTarget`), hosts.
- Multi-node: нода — **полноценная 3x-ui**; панель ходит в её REST; клонирование inbound'ов; у ноды есть `OutboundTag` («per-node egress»). Routing — правила в шаблоне Xray с UI-браузером geosite/geoip, балансировщики, hot-diff outbounds/routing.
- Подписки: `/sub/:subid` (base64 v2ray), `/json/:subid` (полный клиентский JSON с routing), `/clash`, `/mihomo`; QR в UI.
- Ограничения: GPL-3.0 (форк обязывает открывать код), монолит на каждой ноде, «панельный» API (выход/политика не first-class), свой Vue-фронт.

## Xray-core 26.11.9

- REALITY (VLESS+XTLS-Vision), SNI-маскировка, `target`/`mldsa65` (PQ) в 26.x.
- Routing: `rules` (domain `domain:`/`full:`/`keyword:`/`regexp:`/`geosite:`, `ip:`/`geoip:`), `balancers` (`selector` — **префикс тега**, стратегии `leastPing`/`leastLoad`, `fallbackTag`), `observatory`/`burstObservatory`.
- Hot-API без рестарта: `adu`/`rmu` (users), `addrules`/`addoutbounds`, но **всё in-memory, не персистится** — после рестарта нужен replay desired-state. `SIGHUP` конфиг не перечитывает: любое изменение — `xray run -test` + `systemctl restart`.
- Stats: `StatsService` — `QueryStats` (счётчики `user>>>EMAIL>>>traffic>>>uplink|downlink`, `inbound>>>TAG>>>…`, `outbound>>>TAG>>>…`), `GetUsersStats` (online + IP + last_seen, нужен `statsUserOnline`), `GetSysStats` (uptime, goroutines, GC); `metrics.listen` → `/debug/vars` (expvar JSON) без gRPC.
- Счётчики кумулятивные; `-reset` (reset=true) возвращает старое значение и обнуляет — использовать один коллектор и считать дельты.
- Access-лог — plain text (`accepted … [inbound -> outbound] email: …`, `rejected`), error-лог — `[Warning]/[Error]`; JSON-лога нет, для агрегации нужен парсер или journald.
- Межузловой транспорт: **VLESS+REALITY** (SS-2022 в 26.x deprecated, `migrate to VLESS Encryption`).

## Целевая схема маршрутизации (проверено)

```
private/metadata (geoip:private, 169.254.169.254) -> block
site rules (например Anthropic + geosite:anthropic)  -> balancer "rule-<id>-" (fallbackTag=block)
geosite:category-ru, domain:*.ru                    -> direct
geoip:ru (нужен IPIfNonMatch)                       -> direct
default                                             -> balancer "exit-" (fallbackTag=block)
catch-all                                           -> block
```

Правила проверяются сверху вниз, первое совпадение выигрывает; первый outbound — `block`. Site rules ставятся **выше** RU-правил, иначе зарубежный домен, резолвящийся в RU-IP, уйдёт напрямую. `sniffing.destOverride=[http,tls,quic]` + `routeOnly: true` нужны для клиентов, ходящих по IP: без них доменные правила не сработают.

## Provisioning (паттерны)

- Установка: pinned release с проверкой `.dgst` (sha256) или официальный `Xray-install` с `--version`/`--no-update-service`; для воспроизводимости панель кладёт свой бинарник + unit.
- systemd: `RestartPreventExitStatus=23` (битый конфиг не уходит в цикл), `NoNewPrivileges`, `ProtectSystem=strict`, `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`, `UMask=0077`.
- Идемпотентность: `flock`, сравнение версии и sha256 конфига → рестарт только при изменении; секреты создаются один раз.
- Применение: рендер в tmp → `xray run -test -c candidate` → backup → atomic mv → restart → health → rollback.
- Uninstall: guard по `realpath` (отказ для `/`, `$HOME`, пустой переменной), затем stop/disable, удаление каталога, юнитов, firewall, `daemon-reload`, `reset-failed`.
- **Компромисс:** всё состояние в `~/config/vpn` (требование брифа) конфликтует с `ProtectHome=yes`; unit использует `ProtectHome=no` + `ProtectSystem=strict` + `ReadWritePaths` (альтернатива — `/opt/vpn` с `ProtectHome=yes`).

## vless:// URI

Обязательные: `encryption=none`, `security=reality`, `sni`, `fp`, `pbk`, `sid`, `type=tcp`, `flow=xtls-rprx-vision`, `spx=%2F` (URL-encoded). Параметры собирать через URLSearchParams; IPv6 — `@[addr]:443`; `allowInsecure` для REALITY не нужен. Длинные ссылки (>1 КБ) сканируются хуже — оставлять минимум параметров.

## Внешние резидентские прокси (рекомендации)

Для Anthropic/Claude нужен резидентский/ISP-выход: датацентровый IP (в т.ч. exit-узел) часто ловится как хостинг. Ключевой критерий — **статический IP в ISP-ASN**, а не ротация.

| Провайдер | Тип | Практика |
|---|---|---|
| Bright Data | ISP static + rotating | премиум-пул, дорого, KYC, лучшее качество |
| Oxylabs | ISP + residential | премиум, стабильно, дороже среднего |
| Decodo (ex-Smartproxy) | ISP + residential | средний сегмент, гибкие sticky-сессии |
| IPRoyal | Royal ISP + residential | недорогие static-IP, средний пул |
| Webshare | ISP static + residential | дешёвые static-IP, удобная замена при бане |
| Proxy-Seller | static residential | бюджетно, пул средний |
| Mobile (Bright Data, Airproxy) | LTE | максимальное доверие, дорого, обычно CGNAT-ротация |

Рекомендация: **2 статических ISP-IP разных провайдеров/ASN** (US основной + EU резерв), HTTP CONNECT, ожидаемый IP зафиксирован в панели, бюджет трафика. Альтернатива без аренды — собственный домашний узел (Raspberry Pi/роутер с WireGuard) в нужной стране: самый «чистый» IP, но нужен физический доступ и стабильный канал.

Ограничения: провайдер видит метаданные; общие пулы бывают в фрод-листах; Anthropic дополнительно смотрит платёж/телефон/поведение — сеть решает не всё.

## Как получить резидентский IP (легально)

| Путь | Как | Плюсы | Минусы |
|---|---|---|---|
| Аренда ISP/static | Bright Data, Oxylabs, Decodo, IPRoyal, Webshare | быстро, статичный IP, поддержка | платно, KYC, общий пул |
| Аренда rotating residential | те же + SOAX/NetNut | гео на выбор | IP меняется, цена за ГБ |
| Mobile LTE | Bright Data mobile, Airproxy | максимальное доверие | дорого, CGNAT-ротация |
| Свой узел | Raspberry Pi/OpenWrt-роутер дома + WireGuard до entry | бесплатно, «чистый» IP, полный контроль | нужен стабильный канал и питание, обслуживание |
| У доверенных людей | мини-ПК у родственников/друзей с их разрешением | IP нужной страны | согласие, доступ, ToS провайдера |
| Мобильный роутер с eSIM другой страны | 4G/5G-роутер с локальной SIM | резидентский IP страны | CGNAT, цена трафика, нестабильность |

Схема своего узла: WireGuard между entry-VPS и Pi/роутером (инициирует домашняя сторона, NAT не мешает), Pi слушает VLESS+REALITY на wg-адресе, entry ходит на него как на обычный exit — панель добавляет такой узел по SSH через wg-IP. Если IP динамический, health-check проверяет страну/ASN, а не точный IP.

Чужие роутеры и устройства без разрешения — несанкционированный доступ и ботнет; «прокси-фермы» и бесплатные списки — юридический риск, утечка трафика и IP, уже помеченные как фрод. Не используем.

## Риски

| Риск | Митигация |
|---|---|
| Marzban/Marzneshin заброшены | не выбирать базой; следить как за референсом |
| GPL при форке 3x-ui | использовать как референс, не линковать код |
| Клиент ходит по IP без SNI | `sniffing.destOverride` + `routeOnly`, тесты на клиентах |
| DNS-утечки | раздельные DNS-правила: RU — локально, остальное — через выход |
| Двойной учёт трафика | считать только `user>>>`; outbound — диагностика |
| `reset=true` при нескольких коллекторах | дельты по именам счётчиков, один коллектор |
| Ошибка генерации конфига | `xray -test`, канареечный деплой, авто-откат |
| REALITY-dest подобран неверно | dest из той же AS/юрисдикции, скан, ротация |
| SSH-секреты в БД | шифрование at-rest, отдельный ключ, аудит |
| Dokploy в контейнере, ноды снаружи | SSH/агент только через HTTPS, firewall, healthcheck |

## Источники

- https://github.com/Gozargah/Marzban · https://github.com/Gozargah/Marzban-node
- https://github.com/Marzneshin/Marzneshin · https://github.com/Marzneshin/marznode
- https://github.com/MHSanaei/3x-ui · https://docs.sanaei.dev
- https://github.com/XTLS/Xray-core · https://xtls.github.io/en/config/{routing,api,policy,observatory,metrics,log}.html
- https://xtls.github.io/en/config/transports/reality.html · https://github.com/XTLS/Xray-install · https://github.com/XTLS/Xray-rules-dat
- https://github.com/XTLS/Xray-core/discussions/716 (VLESS URI)
