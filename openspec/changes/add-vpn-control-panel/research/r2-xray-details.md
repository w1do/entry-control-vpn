# R2. Xray: VLESS+REALITY, routing, межузловой транспорт, наблюдаемость, provisioning

Проверено на **Xray 26.11.9** (amd64): полные примеры прогнаны через **xray run -test**, команды API проверены на живом инстансе; фрагменты даны как вставки. Версия важна: в 26.x Shadowsocks объявлен deprecated, у REALITY появились поля **target**/**mldsa65**.

Ключевые решения: клиент→entry — VLESS+REALITY+Vision; entry→exit — тоже VLESS+REALITY (не SS-2022); маршрутизация — allowlist доменов → нужный exit, RU → direct, всё остальное → blackhole; статистика — через Stats API, а не парсинг логов.

## 1. VLESS + REALITY

### 1.1. Ключи, shortId, UUID

~~~bash
xray x25519
# PrivateKey: wIwYIPwMkN93DaXZc3FMfm1GJFoXaVQM0JeJrnbK_UU   <- server.privateKey
# Password (PublicKey): piBOCJze61fz6ef2sihb9oBBc_naxJTnJY0Bipb0WkA <- client.password / URI pbk
xray x25519 -i "$PRIVATE"      # восстановить public из private
xray uuid                     # UUID для users[].id
openssl rand -hex 8           # shortId: 16 hex-символов
~~~

**privateKey** — 32 байта в base64url без padding (43 символа). **password**/**pbk** — публичный ключ, не секрет. **shortId** — 0…16 hex-символов, длина чётная (ядро дополняет нулями); пустой shortId допустим только если в **shortIds** сервера есть пустая строка. **flow: "xtls-rprx-vision"** работает только с TCP+TLS/REALITY.

### 1.2. Минимальный серверный inbound

~~~json
{
  "log": { "loglevel": "warning" },
  "inbounds": [{
    "tag": "vless-in", "listen": "0.0.0.0",
    "port": 443, "protocol": "vless",
    "settings": { "clients": [{ "id": "UUID", "email": "u1", "flow": "xtls-rprx-vision" }], "decryption": "none" },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "show": false, "dest": "TARGET_HOST:443", "xver": 0,
        "serverNames": ["TARGET_HOST"], "privateKey": "PRIVATE_KEY",
        "shortIds": ["", "0123456789abcdef"]
      }
    },
    "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"], "routeOnly": true }
  }],
  "outbounds": [{ "tag": "direct", "protocol": "freedom" }]
}
~~~

**dest** — старый alias **target**: реальный TLS 1.3-сайт, куда REALITY релеит не прошедшие auth хендшейки. **serverNames** — SAN из сертификата target, без wildcard; клиентский **sni** обязан быть одним из них. Для target берите сайт в том же регионе/ASN, не CDN-прокси и не заблокированный; проверка: **xray tls ping TARGET_HOST**. Предупреждения ядра о non-443 и популярном target — сигнал, что IP легче заблокировать. **xver: 0**; PROXY protocol нужен только для fallback.

Клиент (SOCKS для проверки; в панели тот же outbound):

~~~json
{
  "log": { "loglevel": "warning" },
  "inbounds": [{ "tag": "socks-in", "listen": "127.0.0.1", "port": 1080, "protocol": "socks", "settings": { "auth": "noauth", "udp": true } }],
  "outbounds": [{
    "tag": "proxy", "protocol": "vless",
    "settings": { "vnext": [{ "address": "SERVER_IP", "port": 443, "users": [{ "id": "UUID", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
    "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "serverName": "TARGET_HOST", "fingerprint": "chrome", "password": "PUBLIC_KEY", "shortId": "0123456789abcdef", "spiderX": "/" } }
  }]
}
~~~

**encryption: "none"** в vnext обязательно. **fingerprint** — chrome/firefox/safari (у них гарантирован MLKEM); **spiderX** лучше уникальный для клиента, например / + случайный сегмент.

### 1.3. Fallbacks

~~~json
"fallbacks": [{ "dest": "127.0.0.1:8080", "xver": 0 }]
~~~

Fallback срабатывает после REALITY, когда TLS уже расшифрован, а VLESS-auth не прошёл; dest — plaintext-локальный сервис (nginx/декой), не TLS-target. Для чистого REALITY обычно не нужен: не прошедший REALITY-хендшейк трафик и так уходит на dest. Если fallback включён, неверный UUID не даёт строки rejected в access-логе — это важно для мониторинга.

## 2. Routing в Xray

Правила проверяются сверху вниз, первое совпадение выигрывает. Если не совпало ничего — используется первый outbound, поэтому fail-closed делается так: первый outbound = block, последнее правило = catch-all block, у balancer **fallbackTag: "block"**.

~~~json
{
  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      { "type": "field", "ip": ["geoip:private", "169.254.169.254/32"], "outboundTag": "block" },
      { "type": "field", "domain": ["domain:anthropic.com", "domain:claude.ai", "domain:claude.com", "geosite:anthropic"], "outboundTag": "exit-us" },
      { "type": "field", "domain": ["geosite:category-ru"], "outboundTag": "direct" },
      { "type": "field", "ip": ["geoip:ru"], "outboundTag": "direct" },
      { "type": "field", "domain": ["geosite:openai", "geosite:google"], "balancerTag": "balancer-exit" },
      { "type": "field", "network": "tcp,udp", "outboundTag": "block" }
    ],
    "balancers": [{ "tag": "balancer-exit", "selector": ["exit-"], "fallbackTag": "block", "strategy": { "type": "leastPing" } }]
  },
  "observatory": { "subjectSelector": ["exit-"], "probeUrl": "https://www.gstatic.com/generate_204", "probeInterval": "30s", "enableConcurrency": true }
}
~~~

Порядок принципиален: сначала блок приватных/метаданных (иначе клиент достанет 169.254.169.254 и LAN узла), затем явный allowlist (Anthropic), затем RU-домены, затем RU-IP, затем общий balancer, затем block. Если поставить geoip:ru раньше Anthropic, зарубежный домен, резолвящийся в RU-IP, уйдёт напрямую. geoip:ru требует IPIfNonMatch (второй проход с DNS) либо IP-литерала; AsIs быстрее, если все адреса приходят доменами.

Формы domain: **domain:example.com** (сам домен + поддомены), **full:** (точное), **keyword:** (подстрока), **regexp:** (regex, регистрозависимый), **geosite:category-ru**, **geosite:anthropic@ru** (атрибут), **ext:file:tag**. Для sniffed-трафика (TUN/клиент прислал IP) включите **sniffing.routeOnly: true**: домен берётся из SNI/Host только для routing, destination не подменяется. Без sniffing per-domain правила не увидят домен у IP-соединений.

Balancer: **selector** — префикс тегов (exit- → exit-sg, exit-us); **leastPing** обязателен observatory, outbounds вне observatory исключаются; если все мертвы, fallbackTag (blackhole, не direct). **outboundTag** и **balancerTag** вместе — побеждает outboundTag. Проверка живости — **xray api bi --server=127.0.0.1:10085 balancer-exit**; exit-us/exit-sg — те же outbound'ы, что в §1.2 (REALITY-клиент к exit), direct — freedom, block — blackhole.

Геоданные: в релизе Xray лежат geoip.dat (lite: cn/ir/ru) и geosite.dat (standard); путь — **XRAY_LOCATION_ASSET** или /usr/local/share/xray (поиск: ./, /usr/local/share/xray, /usr/share/xray). Обновление full/expand:

~~~bash
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ update-geodata
# или вручную: geosite-expand.dat.gz / geoip-full.dat.gz + .sha256sum из XTLS/Xray-rules-dat
install -m 644 -o xray -g xray geosite.dat geoip.dat /usr/local/share/xray/
xray run -test -c /usr/local/etc/xray/config.json && systemctl restart xray
~~~

Обновление дат может изменить маршрутизацию: храните sha256 и предыдущую копию, после обновления прогоняйте -test (проверяет наличие категорий) и канареечный домен.

## 3. Межузловой транспорт entry→exit

Предпочтительно **VLESS+REALITY** (тот же inbound, что у клиента, но отдельный UUID/ключи и firewall allowlist). Он маскируется под TLS, уже отлажен и не зависит от SS. **SS-2022** в Xray 26.x работает, но ядро печатает **Shadowsocks ... is deprecated ... migrate to VLESS Encryption**; используйте только для совместимости.

~~~jsonc
// exit: inbound
{ "tag": "interconnect-in", "listen": "0.0.0.0", "port": 18443, "protocol": "shadowsocks",
  "settings": { "method": "2022-blake3-aes-128-gcm", "password": "BASE64_16B", "network": "tcp,udp", "email": "entry-sg" } }

// entry: outbound
{ "tag": "exit-ss", "protocol": "shadowsocks",
  "settings": { "address": "EXIT_IP", "port": 18443, "method": "2022-blake3-aes-128-gcm", "password": "BASE64_16B" } }
~~~

PSK: **openssl rand -base64 16** для aes-128, 32 байта для aes-256/chacha. Multi-user SS-2022: верхний password — server PSK, в users[].password — user PSK, а клиентский пароль — **SERVER_PSK:USER_PSK** (одной строкой). Это легко перепутать; для entry→exit проще single-user.

Публичный порт exit закрывайте allowlist’ом на entry IP (ufw/nftables/security group), SSH — только с IP панели:

~~~bash
ufw default deny incoming
ufw allow from PANEL_IP to any port 22 proto tcp
ufw allow from ENTRY_IP to any port 18443 proto tcp
ufw enable
~~~

SOCKS/HTTP без TLS — только через WireGuard/private network; на публичной сети это открытый прокси. Для exit лучше listen на private-IP WireGuard, а не 0.0.0.0. VLESS Encryption (**xray vlessenc**) — новая альтернатива без TLS, но для интернета надёжнее проверенный REALITY.

## 4. Наблюдаемость

### 4.1. API, policy, stats

~~~json
"api": { "tag": "api", "listen": "127.0.0.1:10085", "services": ["HandlerService", "LoggerService", "StatsService", "RoutingService"] },
"stats": {},
"policy": {
  "levels": { "0": { "statsUserUplink": true, "statsUserDownlink": true, "statsUserOnline": true } },
  "system": { "statsInboundUplink": true, "statsInboundDownlink": true, "statsOutboundUplink": true, "statsOutboundDownlink": true }
}
~~~

С **listen** (Xray ≥1.8.12) отдельный inbound/route не нужен; на старых — tunnel/dokodemo-door inbound с тегом api + routing на outbound api. Без **policy.system.*** счётчики inbound/outbound не создаются; без **levels.*.statsUser*** — нет per-user. Уровень пользователя по умолчанию 0.

~~~bash
xray api statsquery --server=127.0.0.1:10085 -pattern 'user>>>'
xray api statsquery --server=127.0.0.1:10085 -pattern 'inbound>>>vless-in>>>'
xray api stats --server=127.0.0.1:10085 -name 'user>>>u1@example.com>>>traffic>>>uplink'
xray api statsquery --server=127.0.0.1:10085 -pattern 'user>>>' -reset
xray api statsonline --server=127.0.0.1:10085 -email 'u1@example.com'
xray api statsonlineiplist --server=127.0.0.1:10085 -all -include-traffic
~~~

Имена: **user>>>EMAIL>>>traffic>>>uplink|downlink**, **inbound>>>TAG>>>traffic>>>...**, **outbound>>>TAG>>>traffic>>>...**. **-pattern** — подстрока (не regex, регистрозависима); пустая строка = все. Ответ — **{"stat":[{"name":"...","value":458}]}**; value может отсутствовать при нуле (protobuf JSON), это 0, а не ошибка. int64 в JSON — число; в JS выше 2^53 теряется точность, парсите BigInt/строкой. Счётчики создаются лениво: GetStats по несуществующему имени возвращает NotFound, QueryStats просто не покажет его. **-reset** возвращает старое значение и обнуляет; делайте это одним коллектором, иначе гонка.

### 4.2. Добавление/удаление клиента без рестарта

~~~bash
# add.json: обязателен полный inbound (tag/listen/port/decryption), не только user
cat >/root/add.json <<'EOF'
{"inbounds":[{"tag":"vless-in","listen":"0.0.0.0","port":443,"protocol":"vless","settings":{"decryption":"none","clients":[{"id":"UUID","email":"u2@example.com","flow":"xtls-rprx-vision"}]}}]}
EOF
xray api adu --server=127.0.0.1:10085 /root/add.json
xray api rmu --server=127.0.0.1:10085 -tag=vless-in u2@example.com
~~~

**adu/rmu** меняют только память: после рестарта пользователи исчезнут, если их нет в config.json. Панель должна хранить desired-state и либо рендерить config при изменении, либо повторно применять adu после старта. Смена UUID/flow — rmu + adu; смена shortId/ключей REALITY — только рестарт. rmu не рвёт уже установленные соединения, новые — отклоняет. API слушайте только на loopback/через SSH-туннель: HandlerService не имеет аутентификации.

### 4.3. Логи, подключения, ошибки

Access-log (plain text, не JSON):

~~~
2026/11/09 21:24:33.267479 from 127.0.0.1:63403 accepted tcp:example.com:443 [vless-in -> direct] email: u1@example.com
~~~

accepted означает «inbound принял соединение», а не «сайт доступен»: при политике block строка будет **accepted ... [vless-in -> block]** — это блокировка, не ошибка. rejected — ошибка протокола/auth; при включённых fallbacks неверный UUID уходит в fallback и может не дать rejected. Считайте: connections = accepted по email:/времени; blocked = accepted с outbound block; errors = rejected + строки [Error] из error-log. Формат error-log: **2026/11/09 21:24:25.481457 [Warning] [connID] component: message**; уровни debug|info|warning|error|none, по умолчанию warning.

Нативного JSON-лога у Xray нет. Для JSON: Vector/Fluent Bit с regex-парсером по access-логу, либо **journalctl -u xray -o json** (JSON только обёртка journald, поле MESSAGE — тот же текст). **log.maskAddress: "quarter"|"half"|"full"** маскирует IP. Ротация: logrotate с copytruncate + **xray api restartlogger** (LoggerService) либо официальный **--logrotate 00:00:00**.

### 4.4. Reload

SIGHUP не перечитывает конфиг: main/run.go слушает только SIGINT/SIGTERM. **systemctl reload** не поддержан. Любое изменение config.json — **xray run -test** → **systemctl restart xray** (кратковременный разрыв соединений). adu/rmu/adrules — только in-memory hot-path, не персистентны. restartlogger переоткрывает логи, конфиг не трогает.

## 5. vless:// URI и QR

~~~
vless://UUID@SERVER_IP:443?encryption=none&security=reality&sni=TARGET_HOST&fp=chrome&pbk=PUBLIC_KEY&sid=0123456789abcdef&spx=%2F&type=tcp&flow=xtls-rprx-vision#vpn-us-1
~~~

| Параметр | Значение |
|---|---|
| type | tcp (REALITY+Vision), ws, http, grpc, httpupgrade, xhttp, kcp, quic |
| security | reality / tls / none |
| encryption | none (обязателен для VLESS) |
| flow | xtls-rprx-vision только для TCP+TLS/REALITY |
| sni | REALITY serverName / TLS SNI |
| fp | uTLS: chrome, firefox, safari, ios, android, edge, 360, qq, random, randomized |
| pbk | REALITY public key, base64url без = |
| sid | shortId, hex, чётная длина ≤16 |
| spx | spiderX, URL-encoded path, обычно %2F |
| alpn | h2,http/1.1 (кодировать как h2%2Chttp%2F1.1) |
| host, path, serviceName | WS Host, WS/XHTTP path, gRPC service |
| mode, headerType, seed | gRPC/XHTTP mode, TCP header none|http, mKCP seed |
| #... | имя узла, URL-encode (# → %23, пробел → %20) |

**allowInsecure=1** для REALITY не нужен и опасен. Ссылку собирайте через URLSearchParams, а не конкатенацию: spx=%2F, sni с точкой безопасны, # — только фрагмент. Для IPv6 — **@[2001:db8::1]:443**.

QR: **qrencode -t ANSIUTF8 "$URI"** (терминал) или **qrencode -t PNG -s 6 -m 4 -o vless.png "$URI"**; в веб-панели — qrcode/segno, кодировать весь URI целиком. Длинные ссылки (>1 КБ) плохо сканируются: убирайте лишние параметры, оставляя encryption/security/sni/fp/pbk/sid/type/flow.

## 6. Provisioning, systemd, ~/config/vpn, uninstall

### 6.1. Неинтерактивная установка

~~~bash
V=26.11.9
curl -fL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/v$V/Xray-linux-64.zip"
curl -fL -o /tmp/xray.zip.dgst "https://github.com/XTLS/Xray-core/releases/download/v$V/Xray-linux-64.zip.dgst"
echo "$(awk -F '= ' '/256=/ {print $2}' /tmp/xray.zip.dgst)  /tmp/xray.zip" | sha256sum -c -
unzip -q /tmp/xray.zip -d /tmp/xray && install -m 755 /tmp/xray/xray /usr/local/bin/xray
~~~

Официальный скрипт (root, без вопросов): **bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install --version 26.11.9 --no-update-service**. Он создаёт xray user, кладёт config в /usr/local/etc/xray/config.json (не перезаписывает существующий), dat — в /usr/local/share/xray, unit — в /etc/systemd/system. Для воспроизводимости панель лучше сама кладёт pinned binary+config и свой unit.

### 6.2. systemd unit

~~~ini
[Unit]
Description=VPN Xray node
After=network-online.target nss-lookup.target
Wants=network-online.target
[Service]
User=xray
ExecStart=/usr/local/bin/xray run -c /usr/local/etc/xray/config.json
Restart=on-failure
RestartPreventExitStatus=23
RestartSec=2
LimitNOFILE=1000000
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/log/xray
UMask=0077
[Install]
WantedBy=multi-user.target
~~~

**RestartPreventExitStatus=23** не даёт цикл при битом конфиге; **ProtectHome=yes** — причина не запускать Xray из ~/. **CAP_NET_BIND_SERVICE** достаточно для 443; CAP_NET_ADMIN нужен только TUN.

### 6.3. Структура ~/config/vpn

~~~
~/config/vpn/                  # control-plane панели, 0700
  inventory/<node>.json        # host, role=entry|exit, region, ssh alias
  secrets/<node>.json          # private/public, shortIds, UUIDs; 0600
  render/<node>/config.json    # сгенерированный config для загрузки
  state/<node>.json            # schema, xray_version, config_sha256, last_apply
  templates/node.json
~~~

На узле runtime — только FHS: /usr/local/bin/xray, /usr/local/etc/xray/config.json (0640 root:xray), /usr/local/share/xray/*.dat, /var/log/xray/*.log, /var/lib/vpn-node/state.json. Домашний каталог — staging/секреты панели, не WorkingDirectory сервиса; пользователь xray — system/nologin.

### 6.4. Идемпотентность и удаление

- **flock -n /var/lib/vpn-node/lock**; сравнить xray version и sha256 конфига — если совпало, не рестартовать.
- Секреты создавать только при отсутствии: повторный provision не должен менять REALITY-ключи/UUID/shortId.
- Рендер в tmp → **xray run -test** → backup старого → atomic install/mv → **systemctl restart** только при изменении → health-check (statsquery/TCP) → rollback при провале.
- Пользователей хранить в desired-state; adu — быстрый hot-path, но config при следующем рестарте должен содержать их (или replay).
- Unit менять только при diff, затем **systemctl daemon-reload**.

Uninstall (guard: realpath, отказ для /, $HOME, пустой переменной):

~~~bash
systemctl disable --now vpn-xray
rm -f /etc/systemd/system/vpn-xray.service
rm -rf /etc/systemd/system/vpn-xray.service.d
systemctl daemon-reload && systemctl reset-failed
rm -rf /usr/local/etc/xray /usr/local/share/xray /var/log/xray /var/lib/vpn-node
rm -f /usr/local/bin/xray; userdel xray 2>/dev/null || true
~~~

Официальный путь: **install-release.sh @ remove --purge** (binary, units, dat, config, logs). 3x-ui: stop/disable unit, удалить drop-ins, daemon-reload, reset-failed, затем /etc/x-ui + /usr/local/x-ui; Marzban: контейнеры/образы, /opt/marzban, и только по подтверждению /var/lib/marzban. Не копируйте rm -rf без проверки пути.

## Ссылки

- REALITY/VLESS/fallbacks: https://xtls.github.io/en/config/transports/reality.html · https://xtls.github.io/en/config/inbounds/vless.html · https://xtls.github.io/en/config/features/fallback.html
- Routing/observatory/API/policy/log: https://xtls.github.io/en/config/routing.html · https://xtls.github.io/en/config/observatory.html · https://xtls.github.io/en/config/api.html · https://xtls.github.io/en/config/policy.html · https://xtls.github.io/en/config/log.html
- SS-2022/asset/install/geodata/URI: https://xtls.github.io/en/config/outbounds/shadowsocks.html · https://xtls.github.io/en/config/env.html · https://github.com/XTLS/Xray-install · https://github.com/XTLS/Xray-rules-dat · https://github.com/XTLS/Xray-core/discussions/716
- Панели: https://github.com/Gozargah/Marzban-scripts · https://github.com/MHSanaei/3x-ui