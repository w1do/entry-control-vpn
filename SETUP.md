# SETUP — от нуля до рабочего VPN

Самодостаточный runbook конечной настройки: что купить, как развернуть панель, добавить
entry/exit-узлы, настроить выходы и allowlist для Anthropic, выдать клиентам QR и проверить,
что всё работает fail-closed. Выполняйте по порядку; все команды — рабочие, подставьте свои
значения вместо `panel.example.com`, `203.0.113.10` и `<...>`.

**Результат:** панель `https://panel.example.com` с TLS, два узла (`entry` + `exit`) в статусе
`online`, выход по умолчанию, правило Anthropic только через резидентский ISP-прокси, клиент
с QR/подпиской. RU-сайты идут напрямую, Anthropic — только через разрешённый выход, при
остановке выхода сайт блокируется (никакого fallback «напрямую»).

Ориентировочное время: 1–2 часа (без учёта ожидания DNS/выдачи VPS). Бюджет: ~$15–35/мес
(2 VPS + панель + резидентский прокси).

---

## Оглавление

1. [Что понадобится](#1-что-понадобится)
2. [Покупка инфраструктуры](#2-покупка-инфраструктуры)
3. [Подготовка серверов](#3-подготовка-серверов)
4. [Домен и DNS](#4-домен-и-dns)
5. [Секреты и deploy/.env](#5-секреты-и-deployenv)
6. [Развёртывание панели в Dokploy](#6-развёртывание-панели-в-dokploy)
7. [Первый вход и смена пароля](#7-первый-вход-и-смена-пароля)
8. [Артефакты узлов (Xray, vpn-agent, geodata)](#8-артефакты-узлов-xray-vpn-agent-geodata)
9. [Добавление entry-узла](#9-добавление-entry-узла)
10. [Добавление exit-узла](#10-добавление-exit-узла)
11. [Выходы и выход по умолчанию](#11-выходы-и-выход-по-умолчанию)
12. [Правила сайтов и allowlist для Anthropic](#12-правила-сайтов-и-allowlist-для-anthropic)
13. [Клиенты: QR и умный профиль](#13-клиенты-qr-и-умный-профиль)
14. [Проверка результата](#14-проверка-результата)
15. [Мониторинг](#15-мониторинг)
16. [Бэкап и восстановление](#16-бэкап-и-восстановление)
17. [Обновление панели и узлов](#17-обновление-панели-и-узлов)
18. [Troubleshooting](#18-troubleshooting)
19. [Ротация секретов](#19-ротация-секретов)
20. [Чек-лист готовности](#20-чек-лист-готовности)

---

## 1. Что понадобится

| # | Компонент | Зачем | Минимум |
|---|---|---|---|
| 1 | **VPS entry** | принимает клиентов: VLESS+REALITY на 443/tcp, генерирует трафик к exit | 1 vCPU, 1 GB RAM, 10 GB, Ubuntu 24.04, IPv4, root |
| 2 | **VPS exit** | выпускает трафик в интернет из нужной страны; interconnect 18443/tcp | 1 vCPU, 1 GB RAM, 10 GB, Ubuntu 24.04, IPv4, root |
| 3 | **VPS панели (Dokploy)** | Docker + Dokploy + Traefik, домен панели | 1 vCPU, 2 GB RAM, 20 GB, 80/443, root |
| 4 | **Резидентский ISP-прокси** | Anthropic и чувствительные сервисы: статический ISP-IP в US (основной) + EU (резерв) | HTTP CONNECT или SOCKS5, статический IP, sticky-сессии |
| 5 | **Домен** | панель и TLS Let's Encrypt | например `panel.example.com` |
| 6 | **Менеджер секретов** | `ENCRYPTION_KEY`, SSH-креды, ключи REALITY | любой (KeePass/Bitwarden/1Password) |

Варианты размещения панели:
- **Рекомендуется:** отдельный маленький VPS под Dokploy. 443/tcp панели и 443/tcp entry
  не конфликтуют, т.к. это разные машины.
- **Можно совместить панель с exit-узлом** (exit нужен только 18443 наружу, 80/443 свободны):
  дешевле, но при блокировке exit-IP панель тоже станет недоступна — только для пилота.
- **Нельзя** совмещать панель с entry: entry занимает 443/tcp под REALITY, а Traefik панели
  тоже требует 443. (Обходные схемы с несколькими IP не рассматриваем.)

### 1.1 Почему именно так

- **Entry** — точка входа для клиентов. Его IP виден провайдеру/сайтам только как IP
  VPN-сервера; выбирайте площадку с низким RTT до ваших устройств (RU/Финляндия/Казахстан…).
- **Exit** — через него сайты видят ваш «выходной» IP. Он **не должен быть российским**:
  требование fail-closed — российский IP никогда не используется как выход. Для Anthropic
  нужен именно резидентский ISP-IP (US): датацентровые IP часто блокируются антифродом.
- **Резидентский прокси** — внешний выход, который панель подключает к entry как обычный exit.
  Anthropic видит домашний/ISP-адрес США, а не IP вашего VPS.

---

## 2. Покупка инфраструктуры

### 2.1 VPS

Покупайте KVM (не OpenVZ/LXC), Ubuntu **24.04 LTS**, полный root, 1 IPv4, без «заблокированных»
портов. Для entry/exit подойдут недорогие провайдеры с почасовой оплатой (удобно тестировать).

Требования по ролям:

| Параметр | entry | exit | панель |
|---|---|---|---|
| ОС | Ubuntu 24.04/22.04 | Ubuntu 24.04/22.04 | Ubuntu 24.04 (Dokploy сам ставит Docker) |
| CPU/RAM/диск | 1 vCPU / 1 GB / 10 GB | 1 vCPU / 1 GB / 10 GB | 1 vCPU / 2 GB / 20 GB |
| Входящие порты | 22 (SSH), 443 (VLESS) | 22 (SSH), 18443 (от entry) | 22, 80, 443 |
| Исходящие | 443 к панели, 18443 к exit | 443 к панели, интернет | 80/443 (LE), Docker Hub/GitHub, SSH к узлам |
| Трафик | ваш объём (медиа — десятки ГБ) | аналогично | небольшой |
| IPv6 | не обязателен | не обязателен | не обязателен |

Проверка после получения VPS (консоль провайдера, не панель):

```bash
lsb_release -a                  # Ubuntu 24.04
uname -m                        # x86_64 или aarch64 (важно для артефактов!)
free -h; df -h /
ip -4 addr show scope global    # публичный IPv4
curl -s -m 5 https://api.ipify.org; echo   # исходящий интернет есть
```

### 2.2 Резидентский ISP-прокси для Anthropic

Что нужно от провайдера:

- **статический ISP (residential) IP**, а не ротирующийся пул; US — основной, EU — резерв;
- протокол **HTTP CONNECT** (предпочтительно) или SOCKS5, с логином/паролем;
- **sticky-сессия** (один и тот же IP на время сессии), если провайдер поддерживает;
- лимит трафика/месячный бюджет — панель считает расход по выходу и предупреждает;
- возможность указать **ожидаемый exit-IP** (панель сверяет фактический IP и страну).

Возьмите 1 IP в US (Anthropic) и, при бюджете, 1 в EU как резерв. Цена ориентировочно
$2–15 за статический ISP-IP в месяц. Не покупайте «датацентровые» прокси под видом
резидентских — антифрод Anthropic их видит.

Проверьте прокси до покупки (подставьте выданные host/port/user/pass):

```bash
# HTTP CONNECT
curl -sS -x "http://USER:PASS@HOST:PORT" https://api.ipify.org; echo
curl -sS -x "http://USER:PASS@HOST:PORT" https://ipinfo.io/json | head -20   # страна/ASN

# SOCKS5
curl -sS --socks5-hostname "USER:PASS@HOST:PORT" https://api.ipify.org; echo
```

Убедитесь, что IP стабилен (повторите запрос 2–3 раза) и страна = `US`.

### 2.3 Домен

Купите/используйте домен, к которому у вас есть доступ к DNS. Нужна одна A-запись:
`panel.example.com → <IP панели>`. Поддомен панели не должен быть занят.

---

## 3. Подготовка серверов

Все команды ниже выполняются по SSH на соответствующем сервере от `root` (или через
`sudo -i`). Сначала обновите систему и проверьте базовые вещи.

### 3.1 Общее для entry и exit

```bash
apt update && apt -y upgrade
timedatectl set-timezone UTC && timedatectl             # корректное время (TLS!)
apt -y install curl ca-certificates unzip ufw
sysctl -w net.core.somaxconn=4096

# firewall: наружу только SSH + сервисные порты (UFW не фильтрует Docker, но на узлах Docker нет)
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw enable
```

**entry** дополнительно откройте 443/tcp, **exit** — 18443/tcp (лучше — только с IP entry
после того, как узнаете его IP; провижининг панели добавит правила сам, но базовый порядок):

```bash
# на entry:
ufw allow 443/tcp
# на exit (после того как узнали IP entry; до этого не открывать 18443 всему интернету):
# ufw allow from <ENTRY_IP> to any port 18443 proto tcp
```

Опционально (рекомендуется) — вход только по SSH-ключам:

```bash
# на вашей машине: ssh-copy-id root@<IP>
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

Не устанавливайте на entry/exit Xray вручную: панель сама развернёт `~/config/vpn` и юниты.
Если на VPS уже есть панели (3x-ui/Marzban) — переустановите ОС.

### 3.2 Панель (Dokploy)

Ubuntu 24.04, root. Установите Dokploy официальным скриптом (актуальная команда —
на странице установки dokploy.com; на 2026-11: `curl -sSL https://dokploy.com/install.sh | sh`):

```bash
curl -sSL https://dokploy.com/install.sh | sh
# дождитесь завершения; Dokploy поднимет Traefik и панель
docker ps --format 'table {{.Names}}\t{{.Status}}' | head
# панель Dokploy слушает :3000 — не открывайте 3000 всему интернету:
ufw default deny incoming; ufw allow 22/tcp; ufw allow 80/tcp; ufw allow 443/tcp; ufw enable
```

Откройте Dokploy как в его инструкции (по умолчанию через SSH-туннель
`ssh -L 3000:localhost:3000 root@<PANEL_IP>` → `http://localhost:3000`), создайте
администратора Dokploy и **сразу** смените пароль/включите 2FA, если доступно.

> Если Dokploy у вас уже развёрнут — используйте существующий, новый VPS не обязателен.

Проверка, что узлы «увидят» панель (выполните на entry/exit после §6):

```bash
curl -fsS https://panel.example.com/healthz; echo
```

---

## 4. Домен и DNS

1. В DNS-провайдере создайте A-запись:
   `panel.example.com. 300 IN A <PANEL_IP>` (TTL 300 на время настройки).
2. Порт 80 на панели должен быть открыт — Let's Encrypt проверяет HTTP-01.
3. Проверьте распространение DNS (с вашей машины):

```bash
dig +short panel.example.com            # должен вернуть IP панели
curl -sS -o /dev/null -w '%{http_code}\n' http://panel.example.com/.well-known/acme-challenge/x  # 404 = Traefik отвечает
```

Замечания:
- **Cloudflare**: до получения сертификата держите запись в режиме «grey cloud» (DNS only),
  иначе HTTP-01 может не пройти; после — можно включить proxy (Full strict), но SSE и
  Let's Encrypt через прокси периодически ломаются — проще оставить DNS-only.
- AAAA-запись не создавайте, если у панели нет рабочего IPv6.
- Домен/поддомен привязывается к сервису Dokploy в §6.4; изменения домена применяются
  только после redeploy.

---

## 5. Секреты и deploy/.env

Реальные секреты существуют **только** в `deploy/.env` на диске (и в Dokploy Environment).
Файл в git не попадает; в репозитории — `deploy/.env.example` с плейсхолдерами.

### 5.1 Сгенерируйте ключи

На любой машине с `openssl`/`python3` (можно на панели):

```bash
# SECRET_KEY: 64 hex-символа
openssl rand -hex 32

# ENCRYPTION_KEY: Fernet (44 символа base64url) — нужен cryptography
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# если cryptography нет:
openssl rand -base64 32 | tr '+/' '-_'

# METRICS_TOKEN (опционально): 64 hex
openssl rand -hex 32

# Пароль администратора: минимум 12 символов, лучше 20+
python3 -c "import secrets, string; a=string.ascii_letters+string.digits; print(''.join(secrets.choice(a) for _ in range(24)))"
```

**Сразу** сохраните `ENCRYPTION_KEY` в менеджер секретов. Без него любые бэкапы БД не дадут
расшифровать SSH/REALITY-секреты, и восстановление будет невозможно.

### 5.2 Заполните .env

Скопируйте шаблон и откройте редактором:

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

Итоговый файл (пример структуры; значения — ваши, из шага 5.1):

```bash
ADMIN=you@example.com
PASSWORD=<сгенерированный пароль 12+ символов>

SECRET_KEY=<64 hex>
ENCRYPTION_KEY=<44-символьный Fernet-ключ>
METRICS_TOKEN=<64 hex или пусто>

PUBLIC_BASE_URL=https://panel.example.com
DATABASE_URL=sqlite+aiosqlite:////data/app.db
SESSION_COOKIE_SECURE=true
TZ=UTC
```

Правила и частые ошибки:
- `ADMIN` — email для входа; латиница, без пробелов.
- `PASSWORD` используется **только при первом старте на пустой БД**; после первого входа
  пароль меняется в панели (Настройки → Сменить пароль), а `.env` на него больше не влияет.
- `PUBLIC_BASE_URL` — без завершающего `/`; должен совпадать с доменом панели (агенты и
  подписки строят URL от него).
- `DATABASE_URL` — оставьте SQLite (четыре слэша = абсолютный путь `/data/app.db`).
- `ENCRYPTION_KEY` — ровно один ключ; не меняйте его после первого запуска без процедуры
  ротации (docs/RUNBOOK.md §6.3).
- Не копируйте `.env` в git/мессенджеры; для передачи между машинами — менеджер секретов.

Проверка, что плейсхолдеры заменены (ключи не выводятся):

```bash
grep -E '^(ADMIN|PUBLIC_BASE_URL|DATABASE_URL|SESSION_COOKIE_SECURE|TZ)=' deploy/.env
grep -c 'CHANGE_ME' deploy/.env   # должно быть 0
```

### 5.3 Проверьте compose локально

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env config -q && echo "compose OK"
make secret-scan    # не должно быть находок
```

> Если `make secret-scan` ругается на ваш `.env` — вы случайно создали его в неверном месте
> или закоммитили; файл `deploy/.env` сканер не читает.

---

## 6. Развёртывание панели в Dokploy

### 6.1 Создайте Compose-сервис

1. Dokploy → **Project** → Create Project (например `vpn`).
2. В проекте → **Create Service** → **Compose**.
3. General:
   - Name: `panel` (или любое);
   - Provider: Git (GitHub/GitLab) или Raw (если без git) — репозиторий с этим кодом;
   - Branch: `main`;
   - **Compose Path: `deploy/docker-compose.yml`**.
4. Advanced: **Isolated Deployments = OFF** — compose использует внешнюю сеть
   `dokploy-network`, иначе Dokploy её не создаст. (Если включите isolated — уберите
   `dokploy-network` из `networks` app и оставьте только `internal`.)

### 6.2 Environment

Вставьте в **Environment** содержимое `deploy/.env` (весь файл, значения уже реальные).
Dokploy сохранит их рядом с compose, но **не пробросит** в контейнер сам — контейнер получает
переменные через `env_file: .env` в `docker-compose.yml`. После изменения переменных нужен
**Redeploy**.

### 6.3 Domains и TLS

Domains → **Add Domain**:

| Поле | Значение |
|---|---|
| Host | `panel.example.com` |
| Path | `/` |
| Service | `app` |
| Container Port | `8000` (порт из `expose`) |
| HTTPS | включить |
| Certificate | `letsencrypt` |

Strip Path — выключен. Если Dokploy пишет «service not found» — проверьте, что в compose
сервис называется `app` и публикует `expose: 8000` (не `ports`).

### 6.4 Deploy и проверка

1. **Deploy**. Первый build может занять 3–10 минут (npm-сборка SPA + pip).
2. Смотрите **Deployments / Logs**: должны появиться JSON-строки entrypoint
   `migrations_applied` → `starting_uvicorn`.
3. Проверка с любой машины:

```bash
curl -fsS https://panel.example.com/healthz; echo
# ожидаемо: {"status":"ok","db":"ok","version":"0.1.0"}
```

4. Если 502/504: дождитесь окончания миграций (healthcheck `start_period` 90 с); затем
   проверьте логи контейнера и раздел «Панель недоступна» в docs/RUNBOOK.md.
5. Проверьте, что панель не опубликована без TLS: `curl -sSI http://<PANEL_IP>:8000` должен
   **не** отвечать (порт не публикуется), `http://panel.example.com` — редирект на HTTPS.

### 6.5 Вариант без Dokploy (если он не используется)

```bash
cd /opt && git clone <repo-url> vpn-control-panel && cd vpn-control-panel
cp deploy/.env.example deploy/.env   # заполнить (§5)
docker network create dokploy-network   # внешняя сеть из compose
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
curl -fsS http://127.0.0.1:8000/healthz
```

Далее поставьте перед контейнером любой reverse-proxy с TLS. Пример Caddy
(`/etc/caddy/Caddyfile`), он сам получит сертификат:

```caddyfile
panel.example.com {
    reverse_proxy app:8000
}
```

(для этого добавьте Caddy в сеть `dokploy-network` и опубликуйте 80/443.) SSE-эндпоинты
работают через Caddy без буферизации «из коробки».

---

## 7. Первый вход и смена пароля

1. Откройте `https://panel.example.com` → страница входа.
2. `ADMIN` / `PASSWORD` из `.env` (bootstrap создан при первом старте на пустой БД).
3. Сразу: Настройки → **Сменить пароль** (минимум 12 символов). Все прочие сессии будут
   инвалидированы; `.env` менять не нужно.
4. Проверьте cookie: DevTools → Application → Cookies: `__Host-vpn_session`, `Secure`,
   `HttpOnly`, `SameSite=Lax`. Если вместо неё `vpn_session` — у вас
   `SESSION_COOKIE_SECURE=false` (не для продакшена).
5. Если пароль утерян — процедура сброса в docs/RUNBOOK.md §6.1 (без переустановки).

---

## 8. Артефакты узлов (Xray, vpn-agent, geodata)

Провижининг загружает на каждый узел четыре файла из каталога `/data/artifacts`
контейнера панели:

| Файл | Что это | Откуда |
|---|---|---|
| `xray-linux-amd64` | Xray-core **26.11.9** | XTLS/Xray-core release `Xray-linux-64.zip` |
| `vpn-agent-linux-amd64` | статический `vpn-agent` | `make agent` / `go build` |
| `geoip.dat` | geodata IP | Loyalsoldier/v2ray-rules-dat (latest) |
| `geosite.dat` | geodata доменов | Loyalsoldier/v2ray-rules-dat (latest) |

Если хотя бы одного файла нет, job провижининга падает с понятным сообщением
(«Артефакты узла не найдены...»). Узлам интернет для получения артефактов не нужен:
панель загружает их по SFTP, geoip/geosite кладутся в `~/config/vpn/etc/`, а в юните
Xray задаётся `Environment=XRAY_LOCATION_ASSET`.

### 8.1 Быстрый путь: make artifacts

На машине, где есть интернет (dev-машина, CI или сам хост панели), из каталога репозитория:

```bash
make artifacts        # Xray 26.11.9 + geodata (latest) + сборка vpn-agent -> ./artifacts
make artifacts-check  # проверить наличие и sha256 всех четырёх файлов
ls -l artifacts/
# xray-linux-amd64  vpn-agent-linux-amd64  geoip.dat  geosite.dat
```

Требуется `curl`, `unzip`, `sha256sum` и `go` (для агента). Если Go на машине нет —
соберите агент там, где Go есть (`make agent`), скопируйте
`agent/bin/vpn-agent-linux-amd64` в `artifacts/` и запустите `make artifacts-check`.

### 8.2 Заливка в контейнер панели

```bash
# репозиторий на том же хосте, что и контейнер app:
make artifacts-copy
# docker cp 4 файлов в /data/artifacts + chown 10001:10001 + sha256
```

Если панель на другом хосте — перенесите каталог и повторите копирование вручную:

```bash
scp artifacts/* root@<PANEL_IP>:/opt/vpn-artifacts/
ssh root@<PANEL_IP>
APP=$(docker ps -qf 'name=app' -f status=running | head -n 1)
for f in xray-linux-amd64 vpn-agent-linux-amd64 geoip.dat geosite.dat; do
  docker cp "/opt/vpn-artifacts/$f" "$APP":/data/artifacts/$f
done
docker exec -u 0 "$APP" chown -R 10001:10001 /data/artifacts
docker exec "$APP" ls -l /data/artifacts
```

Альтернатива named-тому — bind-mount: раскомментируйте в `deploy/docker-compose.yml`
строку `- /opt/vpn-artifacts:/data/artifacts:ro` (каталог на хосте, не в git-репозитории)
и положите четыре файла туда. При деплое Dokploy каталог репозитория может пересоздаваться,
поэтому named volume + `make artifacts-copy` надёжнее.

### 8.3 Ручная подготовка без make artifacts

Если `make artifacts` недоступен (нет go/скрипта), скачайте файлы вручную на машине с сетью:

```bash
mkdir -p /opt/vpn-artifacts && cd /opt/vpn-artifacts
V=26.11.9

# Xray (amd64) + проверка SHA2-256 из .dgst
curl -fL -o xray.zip "https://github.com/XTLS/Xray-core/releases/download/v$V/Xray-linux-64.zip"
curl -fL -o xray.zip.dgst "https://github.com/XTLS/Xray-core/releases/download/v$V/Xray-linux-64.zip.dgst"
DGST=$(sed -n 's/^SHA2-256= *//p' xray.zip.dgst | head -n1)
echo "$DGST  xray.zip" | sha256sum -c -
unzip -q xray.zip -d xray && install -m 0755 xray/xray xray-linux-amd64

# geodata (Loyalsoldier/v2ray-rules-dat, latest)
curl -fL -o geoip.dat   https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat
curl -fL -o geosite.dat https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat
sha256sum geoip.dat geosite.dat      # зафиксируйте значения

# агент (на машине с Go 1.23+)
make agent                            # -> agent/bin/vpn-agent-linux-amd64
cp agent/bin/vpn-agent-linux-amd64 /opt/vpn-artifacts/vpn-agent-linux-amd64

ls -l /opt/vpn-artifacts/             # ровно 4 файла с нужными именами
```

Дальше — копирование в контейнер по §8.2.

### 8.4 Проверка

```bash
APP=$(docker ps -qf 'name=app' -f status=running | head -n 1)
docker exec "$APP" sha256sum /data/artifacts/xray-linux-amd64 /data/artifacts/vpn-agent-linux-amd64   /data/artifacts/geoip.dat /data/artifacts/geosite.dat
```

Сверьте хэши с тем, что вы скачали/собрали. После этого можно провижинить узлы (§9, §10).

---

## 9. Добавление entry-узла

Панель → **Серверы** → **Добавить сервер**. Мастер проведёт по шагам.

### Шаг 1. SSH-доступ

| Поле | Значение |
|---|---|
| Имя | `entry-ru-1` (уникальное, латиница) |
| Host | публичный IP или DNS entry-узла |
| SSH-порт | `22` (или ваш) |
| SSH-пользователь | `root` (или пользователь с sudo) |
| Аутентификация | пароль root **или** приватный ключ (OpenSSH, целиком) |

Креды шифруются на панели (Fernet) и не выводятся в UI/логи. Для постоянной работы лучше
ключ, чем пароль.

### Шаг 2. Проверка

Нажмите **Проверить SSH**. Preflight вернёт: ОС, архитектуру, наличие sudo, свободен ли
443/tcp, есть ли уже установка VPN, отпечаток host-key. Если ошибка — см. §18 (auth_failed,
host_unreachable, sudo_unavailable).

### Шаг 3. Роли и регион

- Роль: **entry** (принимает клиентов).
- Регион: `RU` / `Finland` / … — метка для UI (на маршрутизацию не влияет, кроме
  идентификации; RU-регион допустим для entry, т.к. выходом он не является).

### Шаг 4. Провижининг

Нажмите **Начать провижининг** и наблюдайте SSE-шаги: `ssh_connect` → `dirs` → `upload_xray` →
`upload_agent` → `upload_geodata` → `config` → `units` → `firewall` → `start` → `register`. Панель создаёт на узле
`~/config/vpn/{bin,etc,log,state}`, ставит `xray.service` и `vpn-agent.service`, включает
автозапуск, открывает порты и ждёт первый heartbeat.

Частые ошибки шага и что делать:

| Ошибка | Причина | Действие |
|---|---|---|
| `Артефакты узла не найдены` | в `/data/artifacts` нет одного из 4 файлов | §8.1–8.2, `make artifacts-check` |
| `auth_failed` | неверный пароль/ключ | исправьте на шаге 1 |
| `sudo_unavailable` | пользователь без sudo | добавьте sudo или используйте root |
| порт 443 занят | на VPS уже есть веб-сервер | освободите порт/переустановите ОС |
| job `failed` на `config` | узел не поддерживает что-то из ОС | пришлите stderr из job в UI |

### Шаг 5. Результат

Дождитесь статуса `online`, версии Xray (26.11.9) и агента. Проверьте на узле:

```bash
ssh root@<ENTRY_IP>
systemctl status xray vpn-agent --no-pager          # active (running)
ss -lntp | grep ':443'                              # xray слушает 443
~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json && echo "xray config OK"
ls -la ~/config/vpn/etc ~/config/vpn/log
tail -n 20 ~/config/vpn/log/xray-error.log
```

Провижининг сам кладёт geodata в `~/config/vpn/etc/` и выставляет `XRAY_LOCATION_ASSET`.
Если `xray -test` ругается на geosite/geoip — проверьте, что job дошёл до шага
`upload_geodata` (иначе в `/data/artifacts` не хватает файлов, §8) и переустановите узел.

---

## 10. Добавление exit-узла

Серверы → **Добавить сервер**, шаги те же:

- Имя: `exit-sg-1`; Host: IP exit-узла; роль: **exit**; регион: `SG` (или `US`, `NL`…).
- Провижининг ставит Xray с interconnect-inbound на **18443/tcp** и client-доступом для
  entry-узлов; firewall на exit должен пускать 18443 только с IP entry (панель настраивает
  allowlist исходя из известных entry-узлов — проверьте `ufw status`).
- Регион exit определяет, какой IP увидят сайты: выбирайте не-RU (требование fail-closed) и
  страну, нужную сервисам.

Проверка на exit:

```bash
ssh root@<EXIT_IP>
systemctl status xray vpn-agent --no-pager
ss -lntp | grep ':18443'
~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json && echo "xray config OK"
ufw status | grep 18443       # только IP entry-узлов
```

После добавления exit появится в реестре **Выходов** (шаг 11).

---

## 11. Выходы и выход по умолчанию

### 11.1 Реестр выходов

Панель → **Выходы**. Для exit-узла:

1. **Добавить выход** → тип `node` → выбрать сервер с ролью exit (`exit-sg-1`).
2. Метка `sg-1` (slug, `[a-z0-9-]`), регион `SG`; бюджет трафика — опционально.
3. **Проверить** — панель делает контрольный запрос через цепочку, записывает фактический
   exit-IP, задержку и статус `up`.

Для резидентского ISP-прокси (несколько IP — несколько выходов):

1. **Добавить выход** → тип `external`:
   - label: `isp-us-1`, регион `US`;
   - protocol: `http` (HTTP CONNECT) или `socks5`;
   - endpoint_host / endpoint_port: из договора провайдера;
   - username/password: креды прокси (шифруются);
   - **expected_exit_ip**: тот статический IP, который обещал провайдер;
   - бюджет трафика (байты) — по тарифу.
2. **Проверить**: фактический IP должен совпасть с `expected_exit_ip`, страна — `US`.
   При несовпадении/стране RU выход станет `down`, и сайты с ним — блокироваться (так задумано).

Если IP датацентрового exit-узла заблокирован сервисом — используйте ISP-прокси как выход для
этого сайта (allowlist), а не как default.

### 11.2 Выход по умолчанию (обязательно)

Панель → **Сайты** → блок **«Выход по умолчанию»**:

- Выберите основной выход (например, `sg-1`), затем добавьте резервный (например, `us-1`) —
  порядок = приоритет, балансировщик `leastPing` с `fallbackTag=block`.
- Пока выход не выбран, профиль нельзя включить: трафик клиентов блокируется (fail-closed) —
  на всех страницах висит баннер «Проксирование не настроено».
- Резервный выход — не «любой», а явно перечисленный: при недоступности всех выходов
  соединение блокируется, а не уходит напрямую.

> Пока default exit не задан, можно подключаться к панели и настраивать всё остальное, но
> клиентский трафик наружу не пойдёт — это штатный режим.

---

## 12. Правила сайтов и allowlist для Anthropic

Панель → **Сайты и маршрутизация**. Секции: (1) RU — locked, (2) выход по умолчанию,
(3) список правил. Порядок правил в конфиге Xray фиксирован: private/metadata → site rules
(allowlist) → RU-direct → RU-IP → default → block.

### 12.1 Добавьте Anthropic с allowlist

Anthropic (Claude) блокирует российские IP и капризен к датацентровым адресам — направьте его
**только** через резидентский ISP-прокси, с резервом на US exit.

1. **Добавить сайт** → пресет **Anthropic** (домены `anthropic.com`, `claude.ai`,
   `claude.com`, `console.anthropic.com`, `api.anthropic.com` и связанные).
2. В модалке allowlist выберите выходы **в порядке приоритета**:
   - 1) `isp-us-1` (резидентский US);
   - 2) `us-1` (exit-узел US), если нужен резерв;
   - НЕ добавляйте сюда `sg-1`/default и не оставляйте пусто — правило с пустым allowlist
     отклоняется валидацией.
3. Сохраните. Предупреждение о fail-closed означает: если оба выхода недоступны, Claude
   перестанет открываться, но не пойдёт ни на default, ни напрямую.

Аналогично добавьте другие сервисы при необходимости: OpenAI, Google/Gemini, Telegram,
GitHub (пресеты) — каждому свой allowlist, если хотите закрепить регион.

### 12.2 RU-трафик — locked

Секция «Российские сайты — как есть (не проксируются)» с замком: `geosite:category-ru` →
`direct` и `geoip:ru` → `direct` жёстко зашиты в компилятор; API отклоняет site rule,
перекрывающее RU-диапазон (`ru_direct_locked`). Менять/удалять это правило нельзя — так
выполняется требование «RU никогда не через выход».

### 12.3 Примените профиль

1. Нажмите **Применить** — создаётся ревизия, конфиг валидируется на каждом entry (`xray -test`),
   применяется атомарно, при провале — откат.
2. Дождитесь статуса ревизии `applied` (Настройки → Ревизии, или бейдж на странице).
3. Проверьте маршруты через **«Проверить маршрут»** (Sites): 
   - `anthropic.com` → тип `site_rule`, выходы `isp-us-1, us-1` (только они);
   - `yandex.ru` → `ru_direct` (выходов нет);
   - `example.com` → `default` с вашим основным выходом.
4. Если ревизия `failed` — смотрите ошибку (какой узел, что не прошло) и docs/RUNBOOK.md §2.5.

---

## 13. Клиенты: QR и умный профиль

Панель → **Клиенты** → **Добавить клиента**: имя (например `iPhone`, `MacBook`), опционально
лимит трафика. UUID и email-тег для статистики генерируются автоматически.

### 13.1 Выдача подключения

В карточке клиента → **Подключиться**:

| Режим | Что выдаётся | Когда использовать |
|---|---|---|
| **Умный профиль (RU напрямую)** | QR на URL подписки `/sub/<token>` + файл `.json` (sing-box/Clash Meta по User-Agent) | **Основной режим.** RU-сайты идут напрямую с устройства, остальное — через entry |
| **Полный туннель (vless)** | QR и `vless://`-ссылка | Fallback: простой клиент без подписок. RU-сайты пойдут через entry (не через выход), UI предупреждает |

Рекомендуемые клиенты (штатные VLESS+REALITY+Vision):
- iOS: **Hiddify**, **Streisand**, **v2rayTun**;
- Android: **Hiddify**, **v2rayNG**, **v2rayTun**;
- macOS/Windows/Linux: **Hiddify**, **v2rayN**, **Clash Meta (FlClash)**, **sing-box**.

Импорт: отсканировать QR (камера/в клиенте «Добавить из QR») или вставить URL подписки.
Для sing-box/Clash используйте именно подписку (умный профиль), а не `vless://`.

### 13.2 Что важно знать

- Ссылка/токен подписки — **секрет**: не публикуйте QR в открытых чатах. Токен отзывается
  вместе с клиентом (удалить/пересоздать).
- При смене REALITY-ключей/маскировочного домена узла ссылки пересчитываются — раздайте новую.
- Лимит трафика: при превышении клиент автоматически отключается на всех узлах
  (`limit reached`), поднимите лимит/сбросьте счётчик в UI.
- Статистика по клиенту — в карточке (24 ч / 7 д / 30 д, последняя активность, узлы).

---

## 14. Проверка результата

Выполняйте на устройстве, подключённом к VPN (умный профиль), и на серверах. Все проверки —
обязательная часть приёмки.

### 14.0 Автоматическая приёмка (acceptance.sh)

**Локальный E2E узла (без VPS):** make e2e-node — поднимает панель на свободном порту и гоняет реальный vpn-agent через fake Xray/systemd (register → heartbeat → config poll → apply → apply-result → metrics → diagnose). Ожидаемо: e2e-node: PASS=11 FAIL=0. Требуется Go и собранный .venv.

**Локальный E2E провижининга (без VPS):** make e2e-provisioning — панель через реальный asyncssh-клиент разворачивает fake-узла в sandbox (загрузка Xray/vpn-agent/geodata, bootstrap-конфиг, systemd-юниты, xray -test), после чего загруженный агент регистрируется в панели. Ожидаемо: e2e-provisioning: PASS=20 FAIL=0.

**E2E реального data plane без VPS:** make e2e-dataplane — локально поднимаются настоящие Xray entry/exit/client, проверяется фактический трафик и маршрутизация (default, site rule, RU-direct, fail-closed). Ожидаемо: e2e-dataplane: PASS=10 FAIL=0.

Один самодостаточный smoke-скрипт проверяет публичные и админские сценарии по HTTP и печатает
PASS/FAIL/SKIP по шагам (секреты не выводит):

```bash
export BASE_URL=https://panel.example.com
export ADMIN='you@example.com'
export PASSWORD='<пароль>'
export METRICS_TOKEN='<токен, если задан>'   # можно не задавать
export ACCEPTANCE_CREATE_ENTRY=1             # 1 — создать временный entry, если его нет
bash deploy/scripts/acceptance.sh
```

Что проверяется: healthz/openapi; /metrics с Bearer и без; login/rate-limit/CSRF/cookie-сессии;
/servers (поле uptime_24h_pct); /events (аудит); /incidents (200, пустой список допустим);
/metrics/overview; создание external exit + сохранение creds + check; пресеты и
Anthropic-allowlist; RU-lock (ru_direct_locked); PUT /routing/profile (updated_at);
/routing/check для anthropic.com (site_rule), example.com (default), yandex.ru (ru_direct);
/routing/apply -> ревизия и entry-конфиг в configs; клиент (sub_token/last_seen_at/stats),
link vless://, smart-профиль с geosite-category-ru, qr.svg; /sub/{token} в форматах
sing-box/clash/v2ray; unauthorized agent API; logout.

Скрипт создаёт временные ресурсы и удаляет их, а профиль маршрутизации восстанавливает.
ACCEPTANCE_CREATE_ENTRY=1 создаёт временный entry-сервер с fake SSH (нужен, если entry-узлов
ещё нет; иначе link/profile/qr/sub будут SKIP, а apply вернёт ожидаемый 409 no_entry_servers).
Ожидаемый результат: acceptance: PASS=... FAIL=0 SKIP=... и acceptance: OK (exit 0).


### 14.1 Панель и API

```bash
curl -fsS https://panel.example.com/healthz; echo
# {"status":"ok","db":"ok","version":"..."}

# метрики (если задан METRICS_TOKEN)
curl -fsS -H "Authorization: Bearer $METRICS_TOKEN" https://panel.example.com/metrics | head -20
```

Вход в браузере работает, cookie `__Host-vpn_session` (Secure/HttpOnly).

### 14.2 Exit-IP: трафик реально идёт через выход

На устройстве с включённым VPN:

```
Откройте https://api.ipify.org      -> должен показать IP вашего default-выхода (exit-sg-1 и т.п.)
Откройте https://ipinfo.io/json     -> страна/ASN соответствуют выходу (SG/US), не RU
```

Сверьте с панелью: Выходы → `sg-1` → фактический exit-IP (поле в карточке/после «Проверить»).

### 14.3 Anthropic идёт только через allowlist

1. С панели проверьте, что `anthropic.com` → `site_rule`, выходы только `isp-us-1, us-1`
   («Проверить маршрут»).
2. На устройстве откройте `https://claude.ai` — должен открыться; IP для Anthropic —
   резидентский US (провайдер подтверждает: зайдите в кабинет провайдера или проверьте
   exit-IP выхода в панели).
3. В статистике панели (Sites/графики) трафик правило anthropic идёт через allowlist, не через
   default.

### 14.4 RU-сайты — напрямую (не через VPN)

На устройстве с умным профилем:

```
Откройте https://2ip.ru        -> должен показать ваш ДОМАШНИЙ российский IP (RU-провайдер)
Откройте https://yandex.ru     -> открывается быстро, как без VPN
Откройте https://api.ipify.org -> IP выхода (проверено в 14.2)
```

Если 2ip.ru показывает exit-IP — клиент использует «полный туннель»/`vless://`, а не умный
профиль; переключите режим на подписку.

### 14.5 Fail-closed: остановка выхода блокирует сайт

Проверка «не утечёт ли трафик» — самая важная:

1. На устройстве убедитесь, что `claude.ai` открывается (через allowlist).
2. Остановите **все** выходы из allowlist Anthropic:
   - панель: Выходы → у `isp-us-1` и `us-1` выключить (disabled), либо
   - на exit-узле: `systemctl stop xray` (для node-выхода), либо
   - у провайдера прокси — приостановить доступ.
3. Подождите 30–60 с (health-check) или нажмите «Проверить» у выходов.
4. **Ожидаемо:** `claude.ai` не открывается (соединение блокируется), в панели — инцидент
   `exit down`/ошибки `routing_blocked`; при этом другие сайты работают через default-выход.
   Никакого «попробовало напрямую» быть не должно.
5. Верните выходы: `systemctl start xray` / включите в UI → маршрут восстановится
   автоматически после новой ревизии (или Apply).
6. Дополнительно проверьте лог на entry:
   ```bash
   ssh root@<ENTRY_IP> 'tail -n 50 ~/config/vpn/log/access.log'
   # при блокировке: "accepted tcp:claude.ai:443 [vless-in -> block]" — это работает fail-closed
   ```

### 14.6 Конфиги Xray валидны на узлах

```bash
ssh root@<ENTRY_IP> '~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json' && echo "entry OK"
ssh root@<EXIT_IP>  '~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json' && echo "exit OK"
```

### 14.7 Узлы онлайн и автономны

- Серверы: статусы `online`, версии Xray/агента, last_seen свежий.
- Остановите панель на 1–2 минуты (`docker stop "$APP"`): VPN продолжает работать на
  последней конфигурации (проверьте claude.ai/2ip.ru), метрики копятся в буфере; после старта
  панели догоняются.
- Перезапустите контейнер (`docker restart "$APP"`): после `healthy` данные (серверы, правила,
  клиенты) на месте — SQLite на томе.

### 14.8 Подписка доступна внешне

```bash
# на машине без VPN (подставьте токен из карточки клиента; это секрет)
curl -fsS -A sing-box "https://panel.example.com/sub/<token>" | head -20
```

Должен вернуться профиль (JSON/YAML по User-Agent), без админских данных.

### 14.9 Инциденты, события и uptime

С авторизованной cookie и CSRF (как получить — docs/RUNBOOK.md §1):

```bash
curl -fsS -b cookies.txt "$PANEL/api/v1/incidents" | python3 -m json.tool | head -40
curl -fsS -b cookies.txt -H "X-CSRF-Token: $CSRF" -X POST "$PANEL/api/v1/incidents/<id>/ack"
curl -fsS -b cookies.txt -H "X-CSRF-Token: $CSRF" -X POST "$PANEL/api/v1/incidents/<id>/resolve"
curl -fsS -b cookies.txt "$PANEL/api/v1/events?limit=20" | python3 -m json.tool | head -60
curl -fsS -b cookies.txt "$PANEL/api/v1/servers" | python3 -c 'import json,sys; print([(s["name"], s["uptime_24h_pct"]) for s in json.load(sys.stdin)])'
```

- Инциденты создаются автоматически: узел offline (>90 с без heartbeat), выход down (если он
  входит в allowlist сайта), сбой применения конфигурации. После проверки fail-closed (§14.5)
  в списке появится инцидент про выход — подтвердите (ack) и закройте (resolve) после ремонта.
- События — журнал безопасности/аудита: входы, изменения маршрутизации, действия с серверами
  (без секретов).
- uptime_24h_pct в списке серверов — покрытие 15-секундных метрик за 24 ч; service_uptime —
  в /api/v1/metrics/overview.


---

## 15. Мониторинг

- **Дашборд**: узлы онлайн/всего, трафик за сегодня, активные соединения, ошибки за час,
  доступность маршрутизации, последние инциденты.
- **Инциденты**: узел offline (>90 с), выход down (особенно входящий в allowlist), apply failed.
  Подтверждайте (ack) и разбирайте по docs/RUNBOOK.md.
- **Prometheus**: `GET /metrics` с `Authorization: Bearer $METRICS_TOKEN`. Пример scrape-конфига:

```yaml
scrape_configs:
  - job_name: vpn-panel
    scheme: https
    metrics_path: /metrics
    bearer_token: "<METRICS_TOKEN>"
    static_configs:
      - targets: ["panel.example.com"]
```

Метрики: `node_up`, `xray_up`, `traffic_bytes_total`, `connections_active`, `errors_total`,
`uptime_seconds`.
- Внешний аптайм-монитор на `/healthz` (не проверяет узлы — можно мониторить и панель, и
  отдельно exit-узлы через их собственные проверки).
- Логи: `docker logs -f "$APP"` (JSON, без секретов). Ротация json-file 10 MB × 3.

---

## 16. Бэкап и восстановление

### 16.1 Бэкап

```bash
mkdir -p /root/panel-backups && chmod 700 /root/panel-backups
bash deploy/scripts/backup-sqlite.sh "/root/panel-backups/app-$(date -u +%Y%m%dT%H%M%SZ).db"
python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone())" /root/panel-backups/app-*.db
```

- Ежедневный cron на хосте панели:
  ```bash
  cat >/etc/cron.daily/panel-backup <<'EOF'
  #!/usr/bin/env bash
  cd /opt/vpn-control-panel && /bin/bash deploy/scripts/backup-sqlite.sh "/root/panel-backups/app-$(date -u +%Y%m%d).db"
  find /root/panel-backups -name 'app-*.db' -mtime +30 -delete
  EOF
  chmod +x /etc/cron.daily/panel-backup
  ```
- **Обязательно** храните `ENCRYPTION_KEY` отдельно (менеджер секретов). Бэкап БД без ключа
  не позволяет расшифровать SSH/REALITY-секреты.
- Копируйте бэкапы вне сервера (S3/rsync/локально), иначе потеря VPS = потеря всего.
- Раз в квартал проверяйте восстановление (см. ниже) на тестовом стенде.

### 16.2 Восстановление

```bash
APP=$(docker ps -qf 'name=app' -f status=running | head -n 1)
VOL=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$APP")
IMG=$(docker inspect -f '{{.Config.Image}}' "$APP")
docker stop "$APP"
# положите файл бэкапа на хост, затем восстановите его в том и верните владельца:
docker run --rm -u 0 --entrypoint sh -v "$VOL":/data -v "$PWD/panel-backup.db":/backup/app.db:ro "$IMG" \
  -c 'cp /backup/app.db /data/app.db && rm -f /data/app.db-wal /data/app.db-shm && chown 10001:10001 /data/app.db'
docker start "$APP"
docker logs --tail=50 "$APP"      # migrations_applied + starting_uvicorn
curl -fsS https://panel.example.com/healthz
```

БД должна быть от той же версии схемы (entrypoint применит недостающие миграции вперёд).
Если `/healthz` 503 — проверьте логи и права тома (docs/RUNBOOK.md §2.9).

---

## 17. Обновление панели и узлов

### 17.1 Панель

1. Сделайте бэкап (§16) и сохраните `ENCRYPTION_KEY`.
2. Dokploy → Deploy (новая версия из репозитория/ветки). Миграции применит entrypoint.
3. Убедитесь: `/healthz` 200, логи `migrations_applied` + `starting_uvicorn`, вход работает.
4. При проблеме — откат на предыдущий деплой в Dokploy (docs/RUNBOOK.md §3); схему БД при
   необходимости восстановите из бэкапа.

### 17.2 Узлы (Xray/agent/geodata)

1. Обновите артефакты в `/data/artifacts` (§8.1–8.2).
2. Переустановите/перепримените узлы **по одному** (Серверы → сервер → Reapply/Reinstall),
   проверяя после каждого: heartbeat, `xray -test`, канареечный домен.
3. Обновляйте geodata с сохранением sha256 и предыдущей копии; после — проверка RU-direct и
   allowlist через «Проверить маршрут».
4. Версии видны в UI; несовпадение Xray-версии на узле — повод reinstall.

---

## 18. Troubleshooting

| Симптом | Что проверить | Что делать |
|---|---|---|
| `/healthz` не отвечает / 502 | `docker ps -a`, `docker logs "$APP"`, Traefik | ждать миграции; логи; docs/RUNBOOK §2.1/§2.8 |
| `/healthz` 503, `db:error` | диск/права тома, логи миграций | §2.9, восстановление из бэкапа |
| Вход не сохраняется, снова login | `SESSION_COOKIE_SECURE`, HTTPS | true только за TLS; проверьте домен/сертификат |
| `SECRET_KEY must be a strong...` при старте | плейсхолдер в .env | замените на 64 hex |
| `ENCRYPTION_KEY must be a valid Fernet key` | плейсхолдер/не тот формат | сгенерируйте заново (§5.1) |
| Провижининг: `Артефакты узла не найдены` | `/data/artifacts` | §8.1–8.2, `make artifacts-check` |
| Провижининг: auth_failed/host_unreachable | SSH-креды, firewall, порт | исправить; `ssh -v` с панели вручную |
| Узел `offline` после провижининга | агент не достучался до панели | `journalctl -u vpn-agent`; исходящий 443; `PUBLIC_BASE_URL` |
| `xray -test` падает: geosite/geoip | провижининг не загрузил geodata / юнит без `XRAY_LOCATION_ASSET` | `make artifacts-check`, переустановить узел (§8) |
| Выход `down`, сайт не открывается | Exits → check: IP/страна/доступность | fail-closed; исправить выход, добавить резерв |
| Anthropic не открывается, остальное работает | allowlist-выходы down | включить ISP/US выход; см. RUNBOOK §2.4 |
| Все сайты не открываются, RU — работают | default exit не задан/все выходы down | Сайты → default exit, Apply |
| RU-сайты через exit-IP | клиент в режиме «полный туннель» | переключить на умный профиль/подписку |
| Клиент `rejected` в access-логе | устаревшая ссылка/QR | перевыпустить ссылку в карточке клиента |
| Не удаляется сервер | SSH недоступен, статус `delete_pending` | docs/RUNBOOK §5 (ручная очистка + force) |
| Диск панели заполнен | бэкапы/логи/образы | `docker system prune`, чистите старые бэкапы |
| Медленно через выход | задержка узла/провайдера | «Проверить» задержку, сменить приоритет выходов |

---

## 19. Ротация секретов

Кратко (полные процедуры — docs/RUNBOOK.md §6):

- **PASSWORD**: Настройки → Сменить пароль (в `.env` менять бессмысленно после первого старта).
- **SECRET_KEY**: новый `openssl rand -hex 32` в Dokploy Environment → redeploy; все сессии
  инвалидируются, узлы не затронуты.
- **ENCRYPTION_KEY**: только через перешифрование БД (иначе секреты станут нечитаемыми):

  ```bash
  # 1. новый ключ -> deploy/.env (и Dokploy Environment)
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

  # 2. старый ключ -> файл (chmod 600), приложение остановить
  mkdir -p deploy/.tmp && chmod 700 deploy/.tmp
  # сохраните в deploy/.tmp/old.key старый ключ из менеджера секретов

  docker compose -f deploy/docker-compose.yml --env-file deploy/.env stop app

  # 3. перешифровать секреты (servers, server_secrets, exits)
  docker compose -f deploy/docker-compose.yml --env-file deploy/.env run --rm --no-deps \
    --entrypoint python \
    -v "$PWD/deploy/scripts:/scripts:ro" \
    -v "$PWD/deploy/.tmp/old.key:/run/secrets/old.key:ro" \
    app /scripts/rotate-encryption-key.py

  # 4. удалить old.key, запустить панель, проверить вход и карточку сервера
  rm -f deploy/.tmp/old.key
  docker compose -f deploy/docker-compose.yml --env-file deploy/.env start app
  ```
- **METRICS_TOKEN**: новый токен → redeploy → обновить Prometheus.
- **SSH-креды/agent-token**: «Переустановить» узел; токен агента выдаётся заново.
- **Токены подписок**: удалить/пересоздать клиента (новые UUID и sub-token).

При подозрении на утечку: сначала ротировать, потом разбираться; зафиксировать в журнале
событий панели и проверить историю git на предмет секретов (`make secret-scan`).

---

## 20. Чек-лист готовности

- [ ] `https://panel.example.com/healthz` → `{"status":"ok","db":"ok","version":"..."}` по HTTPS.
- [ ] Вход работает, cookie `__Host-vpn_session` Secure/HttpOnly; пароль сменён после bootstrap.
- [ ] `ENCRYPTION_KEY` сохранён в менеджере секретов **отдельно** от бэкапов БД.
- [ ] entry-узел: `online`, 443 слушает, `xray -test` OK, geodata на месте.
- [ ] exit-узел: `online`, 18443 слушает, firewall пускает только entry-IP, `xray -test` OK.
- [ ] Выходы: node и ISP-прокси проверены (`up`, фактический exit-IP/страна совпадают).
- [ ] Выход по умолчанию выбран (+резерв); профиль применён (ревизия `applied`).
- [ ] Anthropic: пресет с allowlist [ISP-US, US-exit], «Проверить маршрут» → только allowlist.
- [ ] Клиент создан; QR умного профиля сканируется; `claude.ai` открывается.
- [ ] На устройстве: `api.ipify.org` → exit IP; `2ip.ru` → домашний RU IP.
- [ ] Fail-closed проверен (§14.5): остановка выходов блокирует Anthropic, утечки нет.
- [ ] Панель пережила рестарт контейнера без потери данных; узлы работают без панели.
- [ ] Бэкап БД создан и проверен (`PRAGMA integrity_check`), вне сервера есть копия.
- [ ] `make secret-scan` чист; `.env` не в git; в логах панели нет секретов.
- [ ] Мониторинг: `/metrics` с токеном отвечает, инциденты видны на дашборде.

**Финальная проверка «как пользователь»:** включите VPN на телефоне → `claude.ai` работает →
`2ip.ru` показывает домашний IP → выключите выход в панели → `claude.ai` не открывается, в
панели инцидент → включите выход обратно → всё восстановилось. Это и есть fail-closed.
