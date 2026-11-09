# R2. Xray: VLESS+REALITY, routing, межузловой транспорт, наблюдаемость, provisioning

Проверено на **Xray 26.11.9** (amd64): полные примеры прогнаны через **xray run -test**, API проверен на живом инстансе. В 26.x Shadowsocks deprecated, у REALITY есть **target**/**mldsa65**. Решения: клиент→entry — VLESS+REALITY+Vision; entry→exit — VLESS+REALITY; allowlist доменов → exit, RU → direct, остальное → blackhole; статистика — Stats API.

## 1. VLESS + REALITY

### 1.1. Ключи и shortId

~~~bash
xray x25519
# PrivateKey: wIwYIPwMkN93DaXZc3FMfm1GJFoXaVQM0JeJrnbK_UU   <- server.privateKey
# Password (PublicKey): piBOCJze61fz6ef2sihb9oBBc_naxJTnJY0Bipb0WkA <- client.password / URI pbk
xray x25519 -i "$PRIVATE"      # public из private
xray uuid                     # users[].id
openssl rand -hex 8           # shortId
~~~

**privateKey** — 32 байта base64url без padding (43 символа). **password/pbk** — публичный ключ, не секрет. **shortId** — 0…16 hex, чётная длина (ядро дополняет нулями); пустой допустим только если в **shortIds** есть пустая строка. **flow: "xtls-rprx-vision"** — только TCP+TLS/REALITY.

### 1.2. Серверный inbound

~~~json
{
  "log": { "loglevel": "warning", "access": "/var/log/xray/access.log", "error": "/var/log/xray/error.log" },
  "inbounds": [{
    "tag": "vless-in", "listen": "0.0.0.0",
    "port": 443, "protocol": "vless",
    "settings": { "clients": [{ "id": "UUID", "email": "u1", "flow": "xtls-rprx-vision" }], "decryption": "none" },
    "streamSettings": {
      "network": "tcp", "security": "reality",
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

**dest** — старый alias **target**: реальный TLS 1.3/H2-сайт, куда REALITY релеит не прошедшие auth хендшейки. **serverNames** — SAN сертификата target, без wildcard; клиентский **sni** — один из них. Target — в том же регионе/ASN, не CDN и не заблокирован; проверка **xray tls ping TARGET_HOST**. Предупреждения о non-443/популярном target — сигнал риска блокировки IP. **xver: 0**; PROXY protocol — только для fallback.

### 1.3. Клиент

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

**encryption: "none"** в vnext обязателен. **fingerprint** — chrome/firefox/safari (гарантирован MLKEM); **spiderX** уникален для клиента.

### 1.4. Fallbacks

~~~json
"fallbacks": [{ "dest": "127.0.0.1:8080", "xver": 0 }]
~~~

Fallback — после REALITY, когда TLS расшифрован, а VLESS-auth не прошёл; dest — plaintext-локальный сервис, не TLS-target. Для чистого REALITY обычно не нужен: не прошедший REALITY-хендшейк и так уходит на dest. При fallback неверный UUID не даёт rejected в access-логе — важно для мониторинга.

## 2. Routing в Xray

Правила — сверху вниз, первое совпадение. Если не совпало — первый outbound; поэтому fail-closed: первый outbound = block, последнее правило = catch-all block, balancer **fallbackTag: "block"**.

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

Порядок: block private/metadata (SSRF, LAN, 169.254.169.254) → явный allowlist → RU-домены → RU-IP → общий balancer → block. **geoip:ru** до Anthropic завернул бы зарубежный домен с RU-IP напрямую. **geoip:ru** требует **IPIfNonMatch** (DNS-второй проход) или IP-литерала; **AsIs** быстрее, если все адреса — домены.

Формы domain: **domain:example.com** (apex+поддомены), **full:**, **keyword:**, **regexp:**, **geosite:category-ru**, **geosite:anthropic@ru**, **ext:file:tag**. Для TUN/клиента с IP включите **sniffing.routeOnly: true**: домен из SNI/Host только для routing, destination не меняется. Без sniffing per-domain правила не видят домен у IP-соединений.

Balancer: **selector** — префикс тегов (exit- → exit-sg/exit-us); **leastPing** требует observatory, outbounds вне observatory исключаются; при отказе всех — **fallbackTag** (blackhole, не direct). **outboundTag** + **balancerTag** — побеждает outboundTag. Живость: **xray api bi --server=127.0.0.1:10085 balancer-exit** (нужен RoutingService). exit-us/exit-sg — outbound'ы из §1.3, direct — freedom, block — blackhole.

Геоданные: в релизе **geoip.dat** (lite: cn/ir/ru) и **geosite.dat** (standard); путь — **XRAY_LOCATION_ASSET** или /usr/local/share/xray (поиск: ./, /usr/local/share/xray, /usr/share/xray). Обновление:

~~~bash
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ update-geodata
# или geosite-expand.dat.gz / geoip-full.dat.gz + .sha256sum из XTLS/Xray-rules-dat
install -m 644 -o xray -g xray geosite.dat geoip.dat /usr/local/share/xray/
xray run -test -c /usr/local/etc/xray/config.json && systemctl restart xray
~~~

Обновление дат меняет маршрутизацию: храните sha256 и прошлую копию, после — **-test** (проверяет категории) и канареечный домен.

## 3. Межузловой транспорт entry→exit

Предпочтительно **VLESS+REALITY** (тот же inbound, отдельные UUID/ключи, firewall allowlist): маскируется под TLS, уже отлажен, не зависит от SS. **SS-2022** в 26.x работает, но ядро пишет **Shadowsocks ... deprecated ... migrate to VLESS Encryption**; только для совместимости.

~~~jsonc
// exit: inbound
{ "tag": "interconnect-in", "listen": "0.0.0.0", "port": 18443, "protocol": "shadowsocks",
  "settings": { "method": "2022-blake3-aes-128-gcm", "password": "BASE64_16B", "network": "tcp,udp", "email": "entry-sg" } }

// entry: outbound
{ "tag": "exit-ss", "protocol": "shadowsocks",
  "settings": { "address": "EXIT_IP", "port": 18443, "method": "2022-blake3-aes-128-gcm", "password": "BASE64_16B" } }
~~~

PSK: **openssl rand -base64 16** для aes-128, 32 байта для aes-256/chacha. Multi-user SS-2022: верхний password — server PSK, users[].password — user PSK, клиентский пароль — **SERVER_PSK:USER_PSK**. Легко перепутать; для entry→exit проще single-user.

Публичный порт exit — allowlist на entry IP, SSH — только с IP панели:

~~~bash
ufw default deny incoming
ufw allow from PANEL_IP to any port 22 proto tcp
ufw allow from ENTRY_IP to any port 18443 proto tcp
ufw enable
~~~

Для UDP-трафика добавьте **ufw allow from ENTRY_IP to any port 18443 proto udp**.

SOCKS/HTTP без TLS — только через WireGuard/private network; на публичной сети это открытый прокси. Exit лучше слушает private-IP WireGuard, не 0.0.0.0. VLESS Encryption (**xray vlessenc**) — альтернатива без TLS, но для интернета надёжнее REALITY.

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

С **listen** (Xray ≥1.8.12) отдельный inbound/route не нужен; на старых — tunnel/dokodemo-door inbound с тегом api + routing на outbound api. Без **policy.system.*** счётчики inbound/outbound не создаются; без **levels.*.statsUser*** — нет per-user. Уровень по умолчанию 0.

~~~bash
xray api statsquery --server=127.0.0.1:10085 -pattern 'user>>>'
xray api statsquery --server=127.0.0.1:10085 -pattern 'inbound>>>vless-in>>>'
xray api stats --server=127.0.0.1:10085 -name 'user>>>u1@example.com>>>traffic>>>uplink'
xray api statsquery --server=127.0.0.1:10085 -pattern 'user>>>' -reset
xray api statsonline --server=127.0.0.1:10085 -email 'u1@example.com'
xray api statsonlineiplist --server=127.0.0.1:10085 -all -include-traffic
~~~

Имена: **user>>>EMAIL>>>traffic>>>uplink|downlink**, **inbound>>>TAG>>>traffic>>>...**, **outbound>>>TAG>>>traffic>>>...**. **-pattern** — подстрока (не regex, регистрозависима); пустая = все. Ответ **{"stat":[{"name":"...","value":458}]}**; **value** может отсутствовать при нуле (protobuf JSON) — это 0, не ошибка. int64 в JSON — число; в JS >2^53 теряется точность, парсите BigInt/строкой. Счётчики ленивы: **GetStats** по несуществующему имени — NotFound, **QueryStats** просто не покажет. **-reset** возвращает старое и обнуляет; один коллектор, иначе гонка.

### 4.2. Клиенты без рестарта

~~~bash
# add.json: полный inbound (tag/listen/port/decryption), не только user
cat >/root/add.json <<'EOF'
{"inbounds":[{"tag":"vless-in","listen":"0.0.0.0","port":443,"protocol":"vless","settings":{"decryption":"none","clients":[{"id":"UUID","email":"u2@example.com","flow":"xtls-rprx-vision"}]}}]}
EOF
xray api adu --server=127.0.0.1:10085 /root/add.json
xray api rmu --server=127.0.0.1:10085 -tag=vless-in u2@example.com
~~~

**adu/rmu** меняют только память: после рестарта пользователи исчезнут, если их нет в config.json. Панель хранит desired-state и либо рендерит config, либо повторно применяет adu. Смена UUID/flow — rmu+adu; shortId/ключи REALITY — только рестарт. rmu не рвёт установленные соединения, новые отклоняет. API — только loopback/SSH-туннель: HandlerService без аутентификации.

### 4.3. Логи, подключения, ошибки

~~~text
2026/11/09 21:24:33.267479 from 127.0.0.1:63403 accepted tcp:example.com:443 [vless-in -> direct] email: u1@example.com
~~~

**accepted** — inbound принял соединение, не «сайт доступен»: при block будет **accepted ... [vless-in -> block]** — блокировка, не ошибка. **rejected** — ошибка протокола/auth; при fallback неверный UUID может не дать rejected. Счёт: connections = accepted по email/времени; blocked = accepted с outbound block; errors = rejected + **[Error]** из error-log. Error-log: **2026/11/09 21:24:25.481457 [Warning] [connID] component: message**; уровни debug|info|warning|error|none, default warning.

Нативного JSON-лога нет. JSON: Vector/Fluent Bit с regex по access-логу, либо **journalctl -u xray -o json** (JSON — обёртка journald, MESSAGE — тот же текст). **log.maskAddress: "quarter"|"half"|"full"** маскирует IP. Ротация: logrotate copytruncate + **xray api restartlogger** (LoggerService) или официальный **--logrotate 00:00:00**.

### 4.4. Reload

SIGHUP не перечитывает конфиг: main/run.go слушает только SIGINT/SIGTERM. **systemctl reload** не поддержан. Любое изменение config.json — **xray run -test** → **systemctl restart xray** (разрыв соединений). adu/rmu/adrules — in-memory, не персистентны. restartlogger переоткрывает логи, конфиг не трогает.

## 5. vless:// URI и QR

~~~text
vless://UUID@SERVER_IP:443?encryption=none&security=reality&sni=TARGET_HOST&fp=chrome&pbk=PUBLIC_KEY&sid=0123456789abcdef&spx=%2F&type=tcp&flow=xtls-rprx-vision#vpn-us-1
~~~

| Параметр | Значение |
|---|---|
| type | tcp (REALITY+Vision), ws, http, grpc, httpupgrade, xhttp, kcp, quic |
| security | reality / tls / none |
| encryption | none (обязателен для VLESS) |
| flow | xtls-rprx-vision только TCP+TLS/REALITY |
| sni | REALITY serverName / TLS SNI |
| fp | uTLS: chrome, firefox, safari, ios, android, edge, 360, qq, random, randomized |
| pbk | REALITY public key, base64url без = |
| sid | shortId, hex, чётная длина ≤16 |
| spx | spiderX, URL-encoded path, обычно %2F |
| alpn | h2,http/1.1 (кодировать h2%2Chttp%2F1.1) |
| host, path, serviceName | WS Host, WS/XHTTP path, gRPC service |
| mode, headerType, seed | gRPC/XHTTP mode, TCP header none|http, mKCP seed |
| #... | имя узла, URL-encode (# → %23, пробел → %20) |

**allowInsecure=1** для REALITY не нужен и опасен. Ссылку собирайте через URLSearchParams: spx=%2F, sni с точкой безопасны, # — только фрагмент. IPv6 — **@[2001:db8::1]:443**.

QR: **qrencode -t ANSIUTF8 "$URI"** или **qrencode -t PNG -s 6 -m 4 -o vless.png "$URI"**; в вебе — qrcode/segno, кодировать URI целиком. Длинные ссылки (>1 КБ) сканируются плохо: убирайте лишние параметры.

## 6. Provisioning, systemd, ~/config/vpn, uninstall

### 6.1. Неинтерактивная установка

~~~bash
V=26.11.9
curl -fL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/v$V/Xray-linux-64.zip"
curl -fL -o /tmp/xray.zip.dgst "https://github.com/XTLS/Xray-core/releases/download/v$V/Xray-linux-64.zip.dgst"
echo "$(awk -F '= ' '/256=/ {print $2}' /tmp/xray.zip.dgst)  /tmp/xray.zip" | sha256sum -c -
unzip -q /tmp/xray.zip -d /tmp/xray && install -m 755 /tmp/xray/xray /usr/local/bin/xray
~~~

Официальный скрипт (root, без вопросов): **bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install --version 26.11.9 --no-update-service**. Создаёт xray user, config в /usr/local/etc/xray/config.json (не перезаписывает), dat — в /usr/local/share/xray, unit — в /etc/systemd/system. Для воспроизводимости панель сама кладёт pinned binary+config и свой unit.

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

**RestartPreventExitStatus=23** не даёт цикл при битом конфиге; **ProtectHome=yes** — не запускать Xray из ~/. **CAP_NET_BIND_SERVICE** достаточно для 443; CAP_NET_ADMIN — только TUN; если панель читает логи не от root — UMask=0027 и группа xray.

### 6.3. Структура ~/config/vpn

~~~text
~/config/vpn/                  # control-plane панели, 0700
  inventory/<node>.json        # host, role=entry|exit, region, ssh alias
  secrets/<node>.json          # private/public, shortIds, UUIDs; 0600
  render/<node>/config.json    # сгенерированный config
  state/<node>.json            # schema, xray_version, config_sha256, last_apply
  templates/node.json
~~~

На узле runtime — FHS: /usr/local/bin/xray, /usr/local/etc/xray/config.json (0640 root:xray), /usr/local/share/xray/*.dat, /var/log/xray/*.log, /var/lib/vpn-node/state.json. Домашний каталог — staging/секреты панели, не WorkingDirectory сервиса; пользователь xray — system/nologin, каталоги /var/log/xray и /usr/local/etc/xray — 0750 xray:xray.

### 6.4. Идемпотентность и удаление

- **flock -n /var/lib/vpn-node/lock**; сравнить xray version и sha256 конфига — совпало → не рестартовать.
- Секреты создавать только при отсутствии: повторный provision не меняет REALITY-ключи/UUID/shortId.
- Рендер в tmp → **xray run -test** → backup → atomic install/mv → **systemctl restart** при изменении → health-check → rollback.
- Пользователи — в desired-state; adu — hot-path, но config при рестарте должен их содержать (или replay).
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

Официальный путь: **install-release.sh @ remove --purge** (binary, units, dat, config, logs). 3x-ui: stop/disable unit, drop-ins, daemon-reload, reset-failed, затем /etc/x-ui + /usr/local/x-ui; Marzban: контейнеры/образы, /opt/marzban, и только по подтверждению /var/lib/marzban. Не копируйте rm -rf без проверки пути.

## Ссылки

- REALITY/VLESS/fallbacks: https://xtls.github.io/en/config/transports/reality.html · https://xtls.github.io/en/config/inbounds/vless.html · https://xtls.github.io/en/config/features/fallback.html
- Routing/observatory/API/policy/log: https://xtls.github.io/en/config/routing.html · https://xtls.github.io/en/config/observatory.html · https://xtls.github.io/en/config/api.html · https://xtls.github.io/en/config/policy.html · https://xtls.github.io/en/config/log.html
- SS-2022/asset/install/geodata/URI: https://xtls.github.io/en/config/outbounds/shadowsocks.html · https://xtls.github.io/en/config/env.html · https://github.com/XTLS/Xray-install · https://github.com/XTLS/Xray-rules-dat · https://github.com/XTLS/Xray-core/discussions/716
- Панели: https://github.com/Gozargah/Marzban-scripts · https://github.com/MHSanaei/3x-ui
