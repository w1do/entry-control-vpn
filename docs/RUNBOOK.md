# RUNBOOK — эксплуатация VPN Control Panel

Практические процедуры для одного администратора: диагностика деградаций, откат релиза,
восстановление БД, «узел не удалился», ротация секретов. Развёртывание с нуля — в
[SETUP.md](../SETUP.md), устройство — в [ARCHITECTURE.md](ARCHITECTURE.md).

Обозначения:

```bash
PANEL=https://panel.example.com                 # PUBLIC_BASE_URL
APP=$(docker ps -qf 'name=app' -f status=running | head -n 1)   # контейнер панели
```

## 0. Общие правила

- **Секреты не выводить в терминал/логи.** Пароли и ключи — из менеджера секретов; для
  одноразовой передачи — файлы `0600`, удалять после.
- Все работы, меняющие БД/образ, — в окно обслуживания: панель на время рестарта недоступна,
  VPN при этом продолжает работать (узлы автономны, последняя конфигурация Xray остаётся).
- Источники истины: `deploy/.env` (переменные), `deploy/docker-compose.yml` (инфраструктура),
  `ENCRYPTION_KEY` (расшифровка секретов в БД). Бэкап БД без ключа бесполезен.
- Перед миграциями/обновлением — бэкап (§4) и сохранённый `ENCRYPTION_KEY`.
- Команды compose выполняются из каталога репозитория на хосте Dokploy:
  `docker compose -f deploy/docker-compose.yml --env-file deploy/.env <cmd>` (далее `$COMPOSE`).

## 1. Быстрая проверка состояния

```bash
# 1) сам сервис и БД
curl -fsS "$PANEL/healthz"        # {"status":"ok","db":"ok","version":"..."}  (503 — БД недоступна)

# 2) контейнер и его health
docker ps --filter name=app --format 'table {{.Names}}\t{{.Status}}'
docker inspect -f '{{.State.Health.Status}}' "$APP"   # healthy / starting / unhealthy

# 3) последние логи панели (JSON-строки, без секретов)
docker logs --tail=100 "$APP"

# 4) узлы: статус в UI (Servers) или API
curl -fsS -b cookies.txt "$PANEL/api/v1/servers" | python3 -m json.tool | head -60

# 5) выходы и здоровье
curl -fsS -b cookies.txt "$PANEL/api/v1/exits" | python3 -m json.tool

# 6) последние инциденты
curl -fsS -b cookies.txt "$PANEL/api/v1/incidents" | python3 -m json.tool | head -40

# 7) метрики Prometheus (если задан METRICS_TOKEN)
curl -fsS -H "Authorization: Bearer $METRICS_TOKEN" "$PANEL/metrics" | head
```

Логи входа (получить cookie/CSRF для API-команд):

```bash
curl -fsS -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"email":"'"$ADMIN"'","password":"'"$PASSWORD"'"}' "$PANEL/api/v1/auth/login" >/dev/null
CSRF=$(curl -fsS -b cookies.txt "$PANEL/api/v1/auth/me" | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
```

## 2. Деградации

### 2.1 Панель недоступна (502/504 от Traefik, `/healthz` не отвечает)

1. `docker ps -a --filter name=app` — контейнер `Restarting`/`Exited`?
2. `docker logs --tail=200 "$APP"`:
   - `migrations_failed` — ошибка Alembic (частая причина: том `/data` не записываем или
     битая миграция). См. §2.8 и §3;
   - `data_dir_not_writable` — см. §2.9;
   - `admin_bootstrapped`/`starting_uvicorn` — старт в порядке.
3. `curl -fsS "$PANEL/healthz"` → 503 `{"db":"error"}`: проверьте диск/том:
   `docker exec -u 0 "$APP" df -h /data`, права `docker exec -u 0 "$APP" ls -la /data`.
4. Traefik: `docker ps --format '{{.Names}}' | grep -i traefik`, логи Traefik содержат
   ошибку маршрута/сертификата; домен должен указывать на `app:8000` (Domains в Dokploy).
5. Перезапуск: Dokploy → Redeploy/Restart либо
   `docker compose -f deploy/docker-compose.yml restart app`.
6. **Пока панель лежит — VPN работает**: узлы на последней ревизии, метрики буферизуются
   (до 24 ч). Не переустанавливайте узлы «на всякий случай».

### 2.2 Узел offline

Нет heartbeat >90 с → статус `offline`, инцидент `critical`, выходы этого узла считаются
недоступными (сайты с их allowlist блокируются — это ожидаемый fail-closed).

```bash
# на узле (SSH):
systemctl status vpn-agent xray --no-pager
journalctl -u vpn-agent -n 100 --no-pager
tail -n 100 ~/config/vpn/log/xray-error.log
curl -fsS -o /dev/null -w '%{http_code}\n' "$PANEL/healthz"     # доступен ли узел до панели

# на панели:
docker logs --tail=100 "$APP" | grep -i "server\|agent" | tail -20
```

Типичные причины и действия:

| Симптом | Причина | Действие |
|---|---|---|
| `Connection refused` к панели | узел потерял сеть/DNS | проверить сеть узла, `getent hosts panel.example.com`, firewall 443 egress |
| 401/403 в логах агента | отозван/неверный agent-token | Servers → сервер → Diagnose/Reapply; при переустановке токен выдаётся заново |
| `xray.service: failed` | невалидный конфиг/нет geodata | `~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json`, см. §2.5 |
| узел перезагружался | ребут/OVH-миграция | дождаться heartbeat 30–90 с; статус вернётся `online` |

Если узел недоступен по SSH, но VPN через него работает — не трогайте панель: дождитесь
связи, затем `Reapply`/переустановите.

### 2.3 Узел `degraded` (дрейф конфигурации)

Агент сообщает, что фактический `etc/xray.json`/ревизия не совпадает с утверждённой.

1. `~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json` — конфиг валиден?
2. Servers → сервер → **Переприменить** (Reapply). Job должен завершиться `applied`.
3. Если повторно `degraded`: `state/state.json` мог сохранить старый sha256 — проверьте
   `sha256sum ~/config/vpn/etc/xray.json`, сравните с ревизией в UI; при сомнении —
   «Переустановить» (Reinstall) с повторным провижинингом (данные узла не затрагиваются,
   ключи REALITY не перегенерируются).

### 2.4 Выход недоступен / сработал fail-closed

Признаки: сайт(ы) с allowlist не открываются, в статистике `routing_blocked`, инцидент
`exit down`, другие сайты через default-выход работают.

1. Exits в UI: `health: down`, `last_check_at`, `last_delay_ms`. Нажать **Проверить** (check).
2. Для exit-узла: SSH → `systemctl status xray`; проверить firewall (entry-IP должен иметь
   доступ к 18443); `ss -lntp | grep 18443`.
3. Для внешнего прокси: проверьте креды/баланс провайдера, фактический exit-IP
   (Exits → check): при несовпадении ожидаемого IP или стране RU выход автоматически `down`.
4. **Fail-closed работает как задумано**: пока ни один разрешённый выход не `up`, трафик сайта
   блокируется и не уходит ни на default, ни напрямую. Не добавляйте site rule на «любой
   выход» — исправьте конкретный выход.
5. Восстановление: включить резервный выход в allowlist (порядок важен: первый — основной),
   дождаться health-check (30 с) или нажать check; приложение подхватит маршрут автоматически
   после новой ревизии (Apply).
6. Проверка изнутри entry:
   ```bash
   ~/config/vpn/bin/xray api bi --server=127.0.0.1:10085 bal-default   # состояние балансировщика
   tail -f ~/config/vpn/log/access.log | grep -E '\[vless-in -> (block|exit-)'
   ```

### 2.5 Ревизия `failed` / `apply failed`

1. Jobs в UI: какой узел и шаг упал, текст ошибки.
2. На узле: `~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json` — если файл
   откатился, конфиг текущий валиден; причина в предыдущем файле (смотрите `state/apply.log`).
3. Устраните причину (частые: отсутствуют `geoip.dat`/`geosite.dat`, несовместимая версия
   Xray, битый shortId/UUID, занят порт).
4. Сделайте Apply ещё раз (создаст новую ревизию). Агент сам откатывается на предыдущий
   конфиг, поэтому VPN не должен лечь целиком; если лёг — верните предыдущую ревизию из UI
   (история ревизий) и Apply.

### 2.6 RU-трафик пошёл через VPN

1. Клиент должен использовать **умный профиль** (QR подписки `/sub/{token}`), а не только
   `vless://` — в умном профиле RU-домены/IP → `direct` на устройстве.
2. В режиме полного туннеля RU идёт напрямую с entry-узла (это ожидаемо и подписано в UI).
3. Проверка маршрута на панели: Sites → «Проверить маршрут» для `yandex.ru` → должно быть
   `ru_direct`. Если нет — проверьте, что клиент/компилятор не перекрыли RU-правило
   (API отклоняет такие site rules с `ru_direct_locked`).
4. На клиенте убедитесь, что включён режим «умный профиль» (sing-box/Clash), а не «весь трафик
   через прокси».

### 2.7 Клиент не подключается

1. Активен ли клиент в UI (не `limit reached`/disabled), не удалён ли.
2. Ссылка/QR выпущены для entry-узла; у узла роль `entry`/`both` и статус `online`.
3. На entry: `systemctl status xray`, `ss -lntp | grep :443`; в access-логе — `rejected`
   (не тот UUID/shortId) или ничего (трафик не доходит — сеть/провайдер/порт).
4. Проверьте `xray run -test` и совпадение REALITY `pbk`/`sid`/`sni` в ссылке с конфигом
   (Servers → сервер → REALITY).
5. После смены REALITY-ключей/домена ссылки пересчитываются — раздайте клиенту новую.

### 2.8 Контейнер не поднялся из-за миграций

1. `docker logs "$APP"` содержит `migrations_failed`/`alembic`-ошибку.
2. Том `/data` записываем (см. §2.9), место есть (`df -h`).
3. Попробуйте `alembic current`/`upgrade head` вручную:
   ```bash
   docker compose -f deploy/docker-compose.yml run --rm --no-deps app alembic current
   docker compose -f deploy/docker-compose.yml run --rm --no-deps app alembic upgrade head
   ```
4. Если миграция сломана — откат на предыдущий образ (см. §3) и восстановление БД из бэкапа
   (§4). Не редактируйте таблицы вручную без бэкапа.

### 2.9 `data_dir_not_writable` / permission denied

Том создан root-ом, а контейнер работает под UID 10001:

```bash
VOL=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$APP")
docker run --rm -u 0 -v "$VOL":/data busybox chown -R 10001:10001 /data
docker restart "$APP"
```

## 3. Откат релиза

**Важно:** миграции Alembic применяются вперёд; автоматический `downgrade` при откате не
выполняется. Если новая версия добавила миграцию, старый код может не прочитать схему —
сначала сделайте бэкап (§4) и предпочтите восстановление БД.

Dokploy:

1. Сервис → **Deployments** (история последних деплоев).
2. Выберите предыдущий успешный деплой → **Redeploy** (или переключите branch/commit на
   предыдущий и Deploy).
3. Дождитесь `healthy` и `curl -fsS "$PANEL/healthz"`.
4. Проверьте: вход в панель, Servers (статусы), Exits, Apply новой ревизии не требуется —
   конфиги узлов не меняются при откате панели.

Самостоятельный compose (без Dokploy):

```bash
# на хосте репозитория: переключитесь на предыдущий тег/коммит (git — на вашей машине/хосте)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Откат **конфигурации узлов** (ревизии маршрутизации) — отдельно: Routing → история ревизий →
выбрать рабочую → Apply. Это не требует отката образа.

## 4. Бэкап и восстановление БД

### 4.1 SQLite (по умолчанию)

Скрипт делает консистентный снимок через `VACUUM INTO` без остановки контейнера:

```bash
mkdir -p /root/panel-backups && chmod 700 /root/panel-backups
bash deploy/scripts/backup-sqlite.sh "/root/panel-backups/app-$(date -u +%Y%m%dT%H%M%SZ).db"
# проверка целостности на хосте:
python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone())" \
  /root/panel-backups/app-*.db
```

Храните отдельно: **`ENCRYPTION_KEY`** (без него секреты не расшифровать) и сам бэкап.
Рекомендуемая частота: ежедневно + перед каждым обновлением; хранить 14–30 дней вне сервера.

Восстановление:

```bash
APP=$(docker ps -qf 'name=app' -f status=running | head -n 1)
VOL=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$APP")
IMG=$(docker inspect -f '{{.Config.Image}}' "$APP")
docker stop "$APP"
# архив кладём в /data как app.db, сбрасываем WAL/SHM, возвращаем владельца 10001
docker run --rm -u 0 --entrypoint sh -v "$VOL":/data -v "$PWD/panel-backup.db":/backup/app.db:ro "$IMG" \
  -c 'cp /backup/app.db /data/app.db && rm -f /data/app.db-wal /data/app.db-shm && chown 10001:10001 /data/app.db'
docker start "$APP"
docker logs --tail=50 "$APP"          # migrations_applied + starting_uvicorn
curl -fsS "$PANEL/healthz"
```

БД должна быть от той же версии схемы (entrypoint применит недостающие миграции вперёд).

### 4.2 PostgreSQL (profile `postgres`)

```bash
# бэкап
docker exec -i $(docker ps -q -f name=db | head -1) pg_dump -U vpn -d vpn -Fc > /root/panel-backups/vpn-$(date -u +%Y%m%d).dump
# восстановление (app остановлен)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env stop app
cat /root/panel-backups/vpn-YYYYMMDD.dump | docker exec -i $(docker ps -q -f name=db | head -1) pg_restore -U vpn -d vpn --clean --if-exists
docker compose -f deploy/docker-compose.yml --env-file deploy/.env start app
```

## 5. «Узел не удалился» (`delete_pending`)

Удаление требует SSH: если узел недоступен, запись остаётся в `delete_pending`, и это
правильно — нельзя «забыть» настроенный узел. Порядок:

1. Проверьте SSH вручную: `ssh -p <port> <user>@<host> 'echo ok'`.
2. Если SSH восстановился — повторите удаление в UI (кнопка «Повторить»).
3. Если узел недоступен навсегда (провайдер удалил VPS) — **принудительное удаление записи**:
   UI «Удалить запись (force)» с предупреждением либо API:
   ```bash
   curl -X DELETE -b cookies.txt -H "X-CSRF-Token: $CSRF" \
     "$PANEL/api/v1/servers/<id>?force=true"
   ```
4. Если VPS жив, но панель его не видит — очистите узел вручную (после этого force-delete):
   ```bash
   ssh <user>@<host> 'bash -s' <<'EOF'
   set -e
   systemctl disable --now vpn-agent 2>/dev/null || true
   systemctl disable --now xray 2>/dev/null || true
   rm -f /etc/systemd/system/vpn-agent.service /etc/systemd/system/xray.service
   systemctl daemon-reload || true
   systemctl reset-failed || true
   rm -rf "$HOME/config/vpn"
   # снять правила, добавленные провижинингом (ufw):
   for p in 443/tcp 18443/tcp; do ufw delete allow "$p" 2>/dev/null || true; done
   echo "cleanup done"
   EOF
   ```
5. Проверка «чисто»: `$HOME/config/vpn` отсутствует, `systemctl status xray` — `not-found`,
   правила ufw для 443/18443 удалены.
6. После force-delete **обязательно** почистите узел вручную, если он ещё существует:
   иначе на нём останется рабочий Xray с конфигом и ключами.

## 6. Ротация секретов

### 6.1 Пароль администратора

- Штатно: Настройки → Сменить пароль (минимум 12 символов; остальные сессии инвалидируются).
- `.env`/Dokploy `PASSWORD` после первого запуска **ни на что не влияет** (bootstrap только
  на пустой БД) — менять его там бессмысленно и небезопасно.
- Забыли пароль / нет доступа — сброс одноразовым скриптом (приложение остановлено):

```bash
read -rsp "Новый пароль: " NEW_ADMIN_PASSWORD; echo
export NEW_ADMIN_PASSWORD
docker compose -f deploy/docker-compose.yml run --rm --no-deps --entrypoint python app -c '
import asyncio, os
from sqlalchemy import select
from app.core.config import get_settings
from app.core.db import init_engine, get_session_factory
from app.core.security import hash_password
from app.models.auth import User

async def main():
    init_engine(get_settings())
    async with get_session_factory()() as db:
        user = (await db.scalars(select(User))).first()
        if user is None:
            raise SystemExit("no admin user in database")
        user.password_hash = hash_password(os.environ["NEW_ADMIN_PASSWORD"])
        user.must_change_password = True
        await db.commit()
        print("password reset for", user.email)

asyncio.run(main())
'
unset NEW_ADMIN_PASSWORD
```

### 6.2 `SECRET_KEY`

Инвалидирует все сессии (самый простой способ «выкинуть всех»):

1. `openssl rand -hex 32` → новое значение в `deploy/.env` / Dokploy Environment.
2. Redeploy/перезапуск app. Все браузеры — на login; узлы не затронуты.
3. Причина смены (компрометация) — зафиксировать в журнале событий панели.

### 6.3 `ENCRYPTION_KEY` (шифрует SSH/REALITY-секреты в БД)

Ключ нельзя просто заменить — старые значения перестанут расшифровываться. Процедура:

1. Сгенерируйте новый ключ:
   `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Положите старый ключ в файл `deploy/.tmp/old.key` (`chmod 700 deploy/.tmp; chmod 600`),
   новый — в `deploy/.env` / Dokploy Environment.
3. Остановите панель:
   `docker compose -f deploy/docker-compose.yml --env-file deploy/.env stop app`.
4. Перешифруйте БД (одноразовый контейнер с новым ключом из env):
   ```bash
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env run --rm --no-deps \
     --entrypoint python \
     -v "$PWD/deploy/scripts:/scripts:ro" \
     -v "$PWD/deploy/.tmp/old.key:/run/secrets/old.key:ro" \
     app /scripts/rotate-encryption-key.py
   # ожидаемый вывод: rotate-encryption-key: OK, re-encrypted fields: N
   ```
5. Удалите `deploy/.tmp/old.key`; запустите app.
6. Проверьте: вход, Servers (карточки без ошибок расшифровки), Exits, Apply новой ревизии,
   провижининг/переприменение на одном узле.
7. Старый ключ уничтожьте в менеджере секретов; в истории git/логах его быть не должно.

### 6.4 `METRICS_TOKEN`

Новый токен в `.env` → redeploy; обновите конфиг Prometheus/Grafana. Пустое значение =
`/metrics` закрыт.

### 6.5 SSH-креды узлов и agent-токены

- SSH-пароль/ключ узла: Servers → сервер → «Переустановить» не нужен; при смене кредов
  обновите запись (через провижининг/повторное добавление) либо смените пароль на узле и
  обновите секрет в панели штатным действием.
- Agent-token: отзывается/выдаётся заново при «Переустановить» (Reinstall); старый токен
  после этого недействителен. Токен хранится на узле в `~/config/vpn/etc/agent.json` (0600).
- Подписки клиентов: токен `/sub/{token}` отзывается вместе с клиентом; при подозрении на
  утечку удалите клиента и создайте нового (UUID и токен новые).

## 7. Обновление Xray, geodata и агента

- Версия Xray пинится (`XRAY_VERSION`, сейчас 26.11.9) и проверяется на узле. Порядок:
  бэкап БД → обновить артефакты в `/data/artifacts` → «Переустановить» узлы по одному →
  `xray run -test` на узле → канареечный домен → остальные узлы.
- geodata (`geoip.dat`, `geosite.dat`) обновляйте с сохранением sha256 и предыдущей копии;
  после обновления — `xray run -test` и проверка RU-direct/allowlist через «Проверить маршрут».
- Агент: новый бинарник `vpn-agent-linux-amd64` в `/data/artifacts` → «Переустановить»
  (или команда `update_agent` в heartbeat-командах). Несовпадение версий видно в UI.
- После обновления любого узла панель должна получить heartbeat и `applied`-ревизию.

## 8. Сбор диагностики для разбора

```bash
# панель
docker logs --tail=500 "$APP" > /root/diag-panel.log 2>&1
curl -fsS "$PANEL/healthz" >> /root/diag-panel.log
docker inspect "$APP" > /root/diag-container.json

# узел (SSH): версии, юниты, конфиг-тест, последние логи
ssh <user>@<host> 'bash -s' <<'EOF' > /root/diag-node.log 2>&1
set +e
hostname; uname -a; uptime
systemctl status vpn-agent xray --no-pager
~/config/vpn/bin/vpn-agent diagnose || true
~/config/vpn/bin/xray run -test -c ~/config/vpn/etc/xray.json
tail -n 200 ~/config/vpn/log/xray-error.log
ls -la ~/config/vpn/etc ~/config/vpn/state
EOF
```

Секреты не попадают в эти логи (конфиг Xray не содержит приватных ключей клиентов, но
содержит privateKey REALITY — не публикуйте `diag-node.log` без редактирования; `etc/xray.json`
лучше передавать только доверенным лицам).

## 9. Таблица «симптом → первые действия»

| Симптом | Первые действия |
|---|---|
| 502/504 снаружи, контейнер healthy | Traefik/libels: Domains в Dokploy, сеть `dokploy-network`, логи Traefik |
| `/healthz` 503 | БД: диск/права тома, логи миграций, восстановление из бэкапа |
| Панель пускает, интерфейс пустой | API 401: истекла сессия → login; иначе `docker logs` 500-ошибки |
| Узел offline | §2.2: SSH, agent, xray, сеть до панели |
| Узел degraded | §2.3: reapply, xray -test |
| Сайт Anthropic не открывается | §2.4: health выходов allowlist, fail-closed — это норма при down |
| Остальные сайты работают, RU — нет | Проверить geoip/geosite на entry, xray -test, ревизию |
| Клиент не подключается | §2.7: ссылка/QR, entry online, access-лог |
| Рост ошибок routing_blocked | Ожидаемо при down выхода; проверить инциденты и allowlist |
| Диск панели заполнен | `du -sh /var/lib/docker/volumes/*`, старые бэкапы/логи, ротация |

## 10. Чек-лист после инцидента

1. `/healthz` = 200, статусы узлов `online`, выходы `up`, инциденты resolved/ack.
2. Пройдена проверка из SETUP.md §14 (exit-IP, RU-direct, fail-closed, xray -test).
3. Бэкап свежий, `ENCRYPTION_KEY` на месте, логи без секретов.
4. Зафиксировано событие в журнале панели; при компрометации — секреты ротированы (§6).
