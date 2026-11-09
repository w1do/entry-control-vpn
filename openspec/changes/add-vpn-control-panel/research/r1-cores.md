# R1. Техническое ядро «VPN-панели»: Marzban / Marzneshin / 3x-ui / чистый Xray-core

**Срез:** 2026-11-09. Проверялся код веток по умолчанию (не README): REST-роутеры, генераторы конфига, коллекторы статистики; релизные и коммит-фиды GitHub; docker-теги. Версии: Xray-core **v26.11.9**, 3x-ui **v3.10.2**, Marzban **v0.8.4**, Marzneshin **v0.7.4**.

**Задача:** один админ; сервер добавляется по SSH → control-plane сам ставит Xray и подключает ноду; «выходы» (SG/US) — first-class объект; зарубежные домены → выбранный выход, RU → direct; клиент получает VLESS+REALITY подписку и QR; статистика трафика/коннектов/ошибок/аптайма; фронт React+TanStack+shadcn; деплой Dokploy.

## 0. Вывод

Ни одна из панелей не моделирует то, что нужно как ядро продукта — **«выход» (exit) и правило «домены → выход»**. У всех трёх маршрутизация живёт в сыром Xray-JSON (шаблон конфига), а не в API-объектах. Marzban и Marzneshin к тому же считают ноду *репликой входного инбаунда*, а не выходом, и не имеют релизов 22 и 16 месяцев соответственно. Рекомендация: **Xray-core как движок + свой control-plane**, с переиспользованием кода генерации ссылок (Marzban), дельта-алгоритма статистики (3x-ui) и встроенных механизмов Xray (routing/balancer/observatory/metrics). 3x-ui — референс и запасная база, не форк.

---

## 1. Marzban

### 1.1 Что это
Python/FastAPI + React (Chakra UI 2, react-query 3, qrcode.react), Xray дочерним процессом, SQLite/MySQL/MariaDB, Alembic. Модель: Admin → User (`proxies{}` + `inbounds{}`) → Node → Host(ProxyHost). AGPL-3.0.

### 1.2 REST API (роуты из `app/routers/*.py`)
| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/admin/token` | OAuth2 password-flow → JWT (Bearer) |
| GET/POST | `/api/users`, `/api/user` | список (offset/limit/status/search/sort) и создание |
| GET/PUT/DELETE | `/api/user/{username}` | карточка/правка/удаление |
| POST | `/api/user/{username}/reset` | сброс счётчика трафика |
| POST | `/api/user/{username}/revoke_sub` | инвалидация подписочного токена |
| POST | `/api/user/{username}/active-next` | активация next_plan |
| GET | `/api/user/{username}/usage?start=&end=` | почасовая история по нодам |
| GET | `/api/users/usage`, `/api/users/expired` | сводки |
| POST/GET | `/api/user_template` | шаблоны пользователей |
| GET | `/api/inbounds` | справочник инбаундов + reality-параметры для ссылок |
| GET/PUT | `/api/hosts` | подмена адреса/порта/SNI в подписке |
| GET | `/api/system` | CPU/RAM/bandwidth + счётчики юзеров (в т.ч. online за 24ч) |
| GET/POST | `/api/core`, `/api/core/restart`, `/api/core/config` | состояние/лог-буфер/рестарт/перезапись конфига |
| GET/POST | `/api/nodes`, `/api/node` | ноды: `{name,address,port:62050,api_port:62051,usage_coefficient,add_as_new_host}` |
| POST | `/api/node/{id}/reconnect` | реконнект (пушит полный конфиг) |
| GET | `/api/nodes/usage`, `/api/node/settings` | трафик по нодам; сертификат панели для ноды |

Создание юзера синхронно дергает `xray.operations.add_user` → gRPC HandlerService `AddUser` по каждому инбаунду (хот-адд, без рестарта ядра).

### 1.3 Трафик и статистика
Фоновый джоб `app/jobs/record_usages.py` зовёт `api.get_users_stats(reset=True)` (StatsService.QueryStats) по локальному ядру и по каждой подключённой ноде, суммирует с `usage_coefficient` и пишет в `node_user_usages`/`node_usages` **с округлением до часа**. Других метрик в API нет: коннектов, ошибок, аптайма ядра, per-exit — отсутствуют.

### 1.4 Подписка и QR
`GET /sub/{token}/` — формат по User-Agent: v2ray (base64), clash, clash-meta, sing-box, outline, v2ray-json; заголовки `subscription-userinfo`, `profile-title`, `profile-update-interval`, `support-url`, `profile-web-page-url`; при `Accept: text/html` — HTML-страница. Дополнительно `/sub/{token}/info`, `/sub/{token}/usage`, `/sub/{token}/{client_type}`. **QR-эндпоинта нет** — рисуется на фронте (qrcode.react) из share-link.

### 1.5 REALITY
В шаблоне инбаунд с `security: reality`, `realitySettings{show,dest,target,serverNames,privateKey,shortIds,spiderX}`. Панель при разборе либо берёт `publicKey`, либо считает его из `privateKey` через `xray x25519`; дальше собирает `vless://uuid@host:port?type=tcp&security=reality&pbk=…&fp=chrome&sni=…&sid=…&spx=%2F&flow=xtls-rprx-vision#remark`. Отдельных «REALITY-профилей» нет — только свой JSON через `XRAY_JSON`.

### 1.6 Мульти-нода
Marzban-node (Python-обвязка над Xray) — REST поверх HTTPS+mTLS на 62050, Xray API на 62051. Панель подключается своим TLS-сертификатом (из таблицы TLS), нода проверяет его по `SSL_CLIENT_CERT_FILE`; `SERVICE_PROTOCOL=rest` по умолчанию. Регистрация: POST `/api/node` → статус `connecting/connected/error/disabled` + версия Xray. Панель **пушит ноде тот же полный конфиг со всеми инбаундами** — нода есть второй вход, а не выход. Трафик ноды масштабируется `usage_coefficient`.

### 1.7 Живость (по фидам)
7 514★ / 1 283 форка / 96 открытых issue. **Последний релиз v0.8.4 — 2025-01-09; master заморожен на этой же дате (22 мес.).** Работа идёт в `dev` (последний коммит 2026-06-08) и в ветке `feature/migrate-chakra-to-shadcn` (2026-11-02, миграция дашборда на shadcn/Tailwind). Marzban-node: последний push 2025-03-22. Upstream жив, но **релизного канала нет**, а stable-ветка не получала фиксов ~2 года.

### 1.8 Ограничения под нашу задачу
1. **Нет per-domain маршрутизации как фичи.** `routing.rules`/`outbounds` в шаблоне сохраняются (панель лишь вставляет правило для API-инбаунда), но правятся только правкой `XRAY_JSON` + рестарт ядра; API/UI для этого нет.
2. **Выход нельзя смоделировать:** нода = копия всех инбаундов; добавление exit-сервера нодой создаст новый host в подписке (лишний профиль у клиента), а не выход.
3. **Статистика бедная:** часовые агрегаты, нет коннектов/ошибок/аптайма; ошибки — только 100 строк лог-буфера.
4. **Своя панель = борьба с чужой моделью:** подписка рендерится из inbound tags + hosts, «выходов», «политик», «серверов» нет; API admin-центричный (multi-admin WIP).
5. Релизный застой + AGPL-3.0.

### 1.9 Что переиспользовать
`app/subscription/v2ray.py` — компактный эталон сборки vless/vmess/trojan/ss-ссылок (reality `pbk/sid/spx/fp`, XHTTP `extra`); `app/xray/config.py` — валидация reality-инбаунда; идеи `revoke_sub` и заголовков подписки.

---

## 2. Marzneshin

Форк Marzban с другой моделью: **Services** (набор инбаундов, выдаваемый юзеру) вместо `inbounds` и **marznode** — форк Marzban-node на gRPC с бэкендами xray / sing-box / hysteria. Дашборд — React + **TanStack Router/Query/Table** + Radix + Tailwind (ближе всех к нашему стеку, но AGPL).

**API** (`app/routes/*.py`): `/api/users`, `/api/users/{u}/{services,usage,enable,disable,revoke_sub,reset}`; `/api/services`; `/api/nodes` + `/{id}/usage`, `/{id}/resync`, `/{id}/{backend}/stats`, `GET|PUT /{id}/{backend}/config`; `/api/system/stats/{admins,nodes,traffic,users}`; `/api/inbounds` + `/hosts`; подписка `/sub/{username}/{key}[/info|/usage|/{client_type}]`.

**Отличия:** `GET|PUT /api/nodes/{id}/xray/config` даёт прямой доступ к конфигу ядра ноды — свою маршрутизацию вкатить можно, но это «ручной JSON» без модели и валидации. Разделение user/service позволяет выдавать разные наборы инбаундов без дублирования юзеров; есть per-node `/stats` по бэкендам.

**Живость:** 708★, 125 issue, последний релиз **v0.7.4 — 2025-07-12**, коммиты master до 2025-10-03, marznode — до 2025-10-03 (релиз v0.5.7). Образ `dawsh/marzneshin:nightly` пересобирается (2026-11-09), но теги не выходят; документация тоньше, комьюнити мало́. Риск заброшенности выше, чем у Marzban.

**Итог:** архитектурно аккуратнее Marzban (services, per-backend config, stats-эндпоинты), но та же проблема — нет объектов «выход»/«правило», и темп ниже.

---

## 3. 3x-ui

### 3.1 Что это
Go-монолит (панель + Xray, Xray вкомпилен как библиотека), один бинарь/Docker, SQLite **или PostgreSQL**, GPL-3.0, 49 595★ / 12 700 форков, **v3.10.2 (2026-10-12), коммиты ежедневно (2026-11-06)**. Протоколы: VLESS/VMess/Trojan/SS/WireGuard/AmneziaWG/TUIC5/Hysteria2/MTProto + XHTTP, fallbacks, REALITY (включая mldsa65/PQ), fail2ban, IP/HWID-лимиты, Telegram+Discord боты.

### 3.2 API
Префикс `/panel/api`. Реестр эндпоинтов поддерживается вручную и **контрактно тестируется** (`frontend/src/pages/api-docs/endpoints.ts` ↔ реальный роутер), есть OpenAPI (`GET /panel/api/openapi.json`). Ключевое:
- `/panel/api/inbounds/{list,get/:id,add,update/:id,del/:id,resetTraffic,…}`, `/:id/fallbacks`;
- `/panel/api/clients/{list,add,update/:email,del/:email,traffic/:email,onlines,links/:email,subLinks/:subId,resetTraffic/:email,bulkCreate,bulkAdjust,hwids…}`;
- `/panel/api/nodes/{list,add,update/:id,del/:id,probe/:id,inbounds,mtls/ca,mtls/trustCA,history/:id/:metric/:bucket,webCert/:id}`;
- `/panel/api/server/{status,getConfigJson,restartXrayService,logs/:count,xraylogs/:count,xrayMetricsHistory/:metric,xrayObservatory,getNewX25519Cert,scanRealityTarget…}`;
- `/panel/api/hosts/*` (маппинг адресов в подписке).
Аутентификация: логин-сессия + CSRF либо API-токены со скоупами и сроком (по README).

### 3.3 Мульти-нода
Нода — **полноценная 3x-ui** на удалённом хосте; панель ходит в её REST (`/panel/api/server/status`) с `apiToken` или mTLS; режимы проверки TLS: `skip | pin | mtls`, пиннинг по SHA-256 сертификата. Heartbeat отдаёт `CpuPct/MemPct/UptimeSecs/NetUp/NetDown/XrayState/XrayError/LatencyMs/Guid` — готовый мониторинг нод. Есть «клонирование инбаундов на ноды» и **per-node egress**: у ноды есть `OutboundTag`, панель вставляет правило «трафик этой ноды → выбранный outbound/balancer».

### 3.4 Маршрутизация и статистика
Routing — правила в шаблоне Xray-конфига, редактируются в UI с браузером geosite/geoip; есть балансировщики с fallback, outbound-подписки, hot-diff перестройка outbounds/routing без рестарта. Статистика: per-client в БД (`client_traffics`), per inbound/outbound, online-клиенты через `StatsService.GetUsersStats` (email + IP + last_seen), исторические метрики Xray и observatory. Ошибки — логи Xray; отдельного API метрик ошибок нет.

### 3.5 Подписки, QR, бот
`/sub/:subid` (base64 v2ray), `/json/:subid` (полный клиентский JSON с routing — можно отдать клиентский сплит), `/clash/:subid`, `/mihomo/:subid`, `/:subid/hwid-status`; автоопределение по UA. QR — в UI. Есть правила маршрутизации, «запекаемые» в подписку (Happ/Clash/JSON, в т.ч. по внешнему URL правил).

### 3.6 Ограничения
1. **GPL-3.0** — форк/производная обязывает открывать код при распространении.
2. **Монолит на каждой ноде:** полная панель с БД и веб-мордой = лишняя поверхность атаки и вес на exit-серверах.
3. **API панельный, не доменный:** «выход», «политика маршрутизации», «сервер» — не first-class; правила = JSON в настройках. Свой UI всё равно строится поверх чужих сущностей.
4. Собственный Vue-фронт (не React) — дублирование половины UI.

---

## 4. Чистый Xray-core + своя оркестрация

### 4.1 Что писать самим
1. **Модель:** `servers` (entry/exit), `exits` (адрес, протокол, ключи, вес, статус), `routing_policies` (geosite/regex → exit), `users`, `subscriptions`, `health`, `traffic_samples`.
2. **Генератор конфига entry:** инбаунд VLESS+REALITY(vision), `sniffing{destOverride:[http,tls,quic]}`, `outbounds` (по одному на exit + direct + block), `routing.rules` (api → api, ru → direct, policy → balancer), `dns` (ru локально, остальное через exit), `api`+stats+policy, `observatory`, `metrics`.
3. **Provisioning по SSH:** установка Xray (XTLS/Xray-install), systemd-юнит, `xray x25519` для ключей, `openssl rand -hex 8` для shortIds, ufw/nftables, запись конфига, health-check.
4. **Транспорт управления:** v1 — SSH-exec (`xray api …`, чтение логов); v2 — WireGuard-меш + агент на ноде.
5. **Статистика:** `QueryStats`/`GetUsersStats`/`GetSysStats` по всем нодам с дельта-базлайном в Postgres; health — observatory + TCP/TLS-пробы; ошибки — парсинг access/error логов.
6. **Подписка/QR:** рендер vless-ссылок, base64, заголовки, страница подписки; QR на фронте.
7. **Мелочи:** RBAC (один админ), аудит, ротация ключей, бэкапы, API-токены.

### 4.2 Плюсы
- Маршрутизация — ровно наша: rules → outbound/balancer, hot-add через RoutingService (`xray api addrules/addoutbounds`) без рестарта (не персистится — повторять при старте).
- Тонкие ноды: только `xray` + systemd, минимум поверхности атаки.
- Нет GPL-наследования: Xray-core — **MPL-2.0**, запускается отдельным процессом.
- «Выход», «политика», «сервер» — сущности нашей модели, а не JSON в чужом шаблоне.
- Единый источник статистики: per-user, per-inbound, per-outbound, per-exit.

### 4.3 Минусы и цена
- 8–12 инженер-недель до MVP (+2–4 на мониторинг/полировку) против 4–6 у 3x-ui.
- Ошибка в сгенерированном JSON = простой; нужны `xray -test -config` и канареечный деплой.
- Свой рендер подписок (v2ray/clash/sing-box), совместимость клиентов тестировать самому.
- Нет «из коробки» UI для ручных экспериментов — админ-режим «править JSON» делать осознанно.

---

## 5. Готовые куски — что не писать с нуля

| Кусок | Готовое решение | Где |
|---|---|---|
| `vless://` ссылки (reality, xhttp, flow) | `V2rayShareLink.vless()` — эталон параметров | [v2ray.py](https://github.com/Gozargah/Marzban/blob/master/app/subscription/v2ray.py) |
| QR | `qrcode.react` / `qrcode` на фронте, сервер не нужен | npm |
| statsquery | `StatsService.QueryStats{pattern,reset}`; CLI `xray api statsquery --server=127.0.0.1:8080 -pattern "user>>>" [-reset] [-json]` | [command.proto](https://github.com/XTLS/Xray-core/blob/main/app/stats/command/command.proto), [api.go](https://github.com/XTLS/Xray-core/blob/main/main/commands/all/api/api.go) |
| online/коннекты | `GetUsersStats{include_traffic}` → email + IP + last_seen + up/down (нужен `statsUserOnline`) | тот же proto |
| аптайм ядра | `GetSysStats` → `Uptime`, goroutines, GC; + systemd/проба ноды | тот же proto |
| метрики без gRPC | `metrics.listen` → `/debug/vars` (expvar JSON: stats + observatory) | [metrics](https://xtls.github.io/en/config/metrics.html) |
| health выходов | `observatory`/`burstObservatory` (alive, delay) + `balancer` с fallback | [observatory](https://xtls.github.io/en/config/observatory.html) |
| REALITY-шаблон | `security: reality` + `realitySettings`; ключи `xray x25519`; mldsa65 для PQ | [reality](https://xtls.github.io/en/config/transports/reality.html) |
| дельта-учёт трафика | базлайн по именам счётчиков, защита от рестарта ядра и «сброса назад» | [xray/api.go](https://github.com/MHSanaei/3x-ui/blob/main/internal/xray/api.go) |

---

## 6. Сравнение

| Критерий | Marzban | Marzneshin | 3x-ui v3 | Xray + своё |
|---|---|---|---|---|
| Многосерверность | да, Marzban-node (REST+mTLS), нода = реплика входа | да, marznode (gRPC), бэкенды xray/sing-box/hysteria | да, нода = полная 3x-ui, mTLS/pin, heartbeat CPU/RAM/uptime/state | своё: SSH/агент, ноды тонкие |
| Per-domain routing | только сырой `routing` в `XRAY_JSON` + рестарт | сырой конфиг ноды через `PUT /nodes/{id}/xray/config` | правила в шаблоне + UI geosite/geoip, hot-diff, балансировщики | first-class: policies → balancer/outbound, hot-add правил |
| «Выход» как объект | нет | нет | частично (`OutboundTag` у ноды, outbound-подписки) | да, ядро продукта |
| Трафик по клиентам | да, часовые агрегаты | да, + `/nodes/{id}/usage` | да, подробно + online IP | да, per user/inbound/outbound/exit |
| Коннекты/онлайн | только «online за 24ч» в `/api/system` | есть online-счётчики | `GetUsersStats`: email+IP+last_seen | `GetUsersStats` + парсинг логов |
| Ошибки/аптайм | нет (лог-буфер 100 строк) | нет | состояние ядра, логи, observatory-история, uptime ноды | observatory + логи + свои пробы |
| API | REST+JWT, admin-центричный | REST, services/nodes-центричный | REST (`/panel/api`), OpenAPI, токены | свой |
| Подписка/QR | v2ray/clash/sing-box/outline/json, QR на фронте | то же | raw/json/clash/mihomo + HWID, QR в UI | свой рендер + QR |
| Вес/стек | Python+React; SQLite/MySQL | Python+React(TanStack)/gRPC; SQLite/PG | Go-монолит; SQLite/PG | xray (~30 МБ) + свой сервис |
| Поддержка | релиз 2025-01, master заморожен; dev жив | релиз 2025-07, коммиты до 2025-10 | релиз 2026-10, коммиты ежедневно, 49.6k★ | upstream Xray v26.11.9, MPL-2.0 |
| Лицензия | AGPL-3.0 | AGPL-3.0 | GPL-3.0 | MPL-2.0 (ядро) |
| Срок внедрения (MVP) | 6–8 нед (с хаком routing) | 8–10 нед | 4–6 нед | 8–12 нед |
| Главный риск | заброшенность + чужая модель | заброшенность + мало комьюнити | GPL + монолит на нодах + «панельный» API | объём работ |

---

## 7. Рекомендация

**Взять за основу Xray-core и написать control-plane самим. Не форкать Marzban/Marzneshin. 3x-ui — референс и, при жёстком дедлайне, запасная база.**

Почему: единственный дифференциатор продукта — «выход по домену», а он у всех панелей реализуется правкой сырого JSON, то есть всё равно пишется руками, но внутри чужой модели и чужого релизного цикла. Marzban даёт 22-месячный застой stable-ветки и архитектуру «нода = второй вход»; Marzneshin — то же плюс меньший темп; 3x-ui технически ближе всех (routing UI, per-node egress, heartbeat, Postgres), но GPL и панель на каждом сервере.

**Что переиспользовать (по слоям):**
- ссылки/подписка — `v2ray.py` как спецификация (не как зависимость);
- статистика — дельта-базлайн из 3x-ui `GetTraffic` (не `reset=true`, иначе гонки при нескольких коллекторах);
- health выходов — `observatory`/`burstObservatory` + `balancer` (не свой пингер);
- метрики — `metrics.listen` (`/debug/vars`) как дешёвый срез без gRPC-клиента;
- REALITY — генерация ключей `xray x25519`, shortIds, проверка target сканом (идея из 3x-ui `scanRealityTarget`);
- provisioning — XTLS/Xray-install + systemd, конфиг через `xray run -config stdin:` (как в Marzban `XRayCore.start`).

**Целевая схема маршрутизации (entry):**
```
api_in(127.0.0.1:10085) -> outbound "api"
domain geosite:category-ru, regexp:.*\.ru$, ip geoip:ru -> direct
domain <policy.foreign_domains> -> balancer "exit-sg" | "exit-us"
default -> balancer "exit-default"  (fallback: direct)
```
Со `sniffing.destOverride=[http,tls,quic]` — иначе клиенты, ходящие по IP, попадут в default-выход. DNS: RU-зона — локальный резолвер входа, остальное — через выбранный exit (иначе утечка/подмена). Выход — Xray с VLESS+REALITY-инбаундом, единственный клиент — UUID входа; квоты считать только по `user>>>…>>>uplink/downlink`, outbound-счётчики — диагностика.

**Запасной вариант (если срок критичен):** 3x-ui как движок, свой React-фронт поверх `/panel/api` (4–6 недель), принять GPL и «панельный» API; выходы — outbounds + balancer + `OutboundTag` у ноды, per-domain — правила в шаблоне. Быстрее, но фиксирует в чужой модели и чужой лицензии.

---

## 8. Риски

| Риск | Влияние | Митигация |
|---|---|---|
| Marzban/Marzneshin заброшены | нет security-фиксов, устаревший Xray | не выбирать базой; следить только как за референсом |
| 3x-ui GPL-3.0 | обязательство открыть производную при распространении | использовать как референс, не линковать код |
| Клиент ходит по IP, а не по домену | правило по домену не сработает | `sniffing.destOverride`, fallback по `geoip`, тесты на реальных клиентах |
| Утечка DNS / RU-домены через выход | RU-сервисы тормозят/банят | раздельные DNS по доменам, RU → direct с локальным резолвером |
| Двойной учёт трафика | завышенные квоты | считать только `user>>>`; outbound — только диагностика |
| `reset=true` в StatsService | потеря/гонка счётчиков при двух коллекторах | дельты по именам счётчиков + один коллектор на ноду |
| Отказ выхода | обрыв у всех юзеров политики | balancer + observatory, health-проба, fallback на direct |
| Ошибка генерации конфига | простой ноды | `xray -test -config`, канареечный деплой, авто-откат по heartbeat |
| REALITY-dest подобран неверно | детект/бан IP | dest из той же AS/юрисдикции, скан `scanRealityTarget`, ротация |
| Секреты provisioning (SSH) в БД | компрометация всех нод | шифрование at-rest (age/libsodium), ключи, аудит, отдельный ключ на ноду |
| Dokploy + ноды | панель в контейнере, ноды снаружи | панель в Docker Compose, SSH/агент-порты в файрволе, healthcheck |

---

## 9. Ссылки

- Marzban: [repo](https://github.com/Gozargah/Marzban) · [routers/user.py](https://github.com/Gozargah/Marzban/blob/master/app/routers/user.py) · [routers/subscription.py](https://github.com/Gozargah/Marzban/blob/master/app/routers/subscription.py) · [routers/node.py](https://github.com/Gozargah/Marzban/blob/master/app/routers/node.py) · [xray/config.py](https://github.com/Gozargah/Marzban/blob/master/app/xray/config.py) · [jobs/record_usages.py](https://github.com/Gozargah/Marzban/blob/master/app/jobs/record_usages.py) · [subscription/v2ray.py](https://github.com/Gozargah/Marzban/blob/master/app/subscription/v2ray.py) · [releases](https://github.com/Gozargah/Marzban/releases) · [docs](https://gozargah.github.io/marzban/)
- Marzban-node: [repo](https://github.com/Gozargah/Marzban-node) · [config.py](https://github.com/Gozargah/Marzban-node/blob/master/config.py) · [xray/node.py](https://github.com/Gozargah/Marzban/blob/master/app/xray/node.py)
- Marzneshin: [repo](https://github.com/Marzneshin/Marzneshin) · [app/routes](https://github.com/Marzneshin/Marzneshin/tree/master/app/routes) · [marznode](https://github.com/Marzneshin/marznode) · [docs](https://docs.marzneshin.org) · [docker tags](https://hub.docker.com/r/dawsh/marzneshin/tags)
- 3x-ui: [repo](https://github.com/MHSanaei/3x-ui) · [API registry](https://github.com/MHSanaei/3x-ui/blob/main/frontend/src/pages/api-docs/endpoints.ts) · [service/node.go](https://github.com/MHSanaei/3x-ui/blob/main/internal/web/service/node.go) · [xray/api.go](https://github.com/MHSanaei/3x-ui/blob/main/internal/xray/api.go) · [sub/controller.go](https://github.com/MHSanaei/3x-ui/blob/main/internal/sub/controller.go) · [docs](https://docs.sanaei.dev) · [releases](https://github.com/MHSanaei/3x-ui/releases)
- Xray-core: [repo](https://github.com/XTLS/Xray-core) · [API](https://xtls.github.io/en/config/api.html) · [Stats](https://xtls.github.io/en/config/stats.html) · [Policy](https://xtls.github.io/en/config/policy.html) · [Routing](https://xtls.github.io/en/config/routing.html) · [Metrics](https://xtls.github.io/en/config/metrics.html) · [Observatory](https://xtls.github.io/en/config/observatory.html) · [REALITY](https://xtls.github.io/en/config/transports/reality.html) · [stats command.proto](https://github.com/XTLS/Xray-core/blob/main/app/stats/command/command.proto) · [VLESS URL scheme (#716)](https://github.com/XTLS/Xray-core/discussions/716) · [Xray-install](https://github.com/XTLS/Xray-install)
