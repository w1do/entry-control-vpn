# vpn-agent — агент узла VPN Control Panel

Go-демон, который живёт на каждом entry/exit-узле под systemd. Он применяет
ревизии конфигурации Xray из панели, собирает телеметрию, выполняет команды
панели и полностью удаляет узел по команде. Контракт поведения —
`docs/INTERFACES.md` §4, `design.md` §7 и `specs/node-agent/spec.md`.

Агент делает **только исходящие HTTPS-запросы** к панели (`/agent/v1/...`)
и не открывает входящих портов. Всё, что он устанавливает и настраивает,
лежит в одном каталоге (base dir): по умолчанию `/root/config/vpn` для root
и `~/config/vpn` для обычного пользователя.

## 1. Состав

| Пакет | Назначение |
|---|---|
| `cmd/vpn-agent` | CLI: `serve` (по умолчанию), `uninstall --yes`, `diagnose`, `version` |
| `internal/config` | загрузка/валидация `<base>/etc/agent.json`, раскладка путей, каталоги |
| `internal/api` | HTTP-клиент панели (Bearer, повторы с backoff, артефакты с SHA-256) |
| `internal/apply` | атомарное применение: validate → swap → restart → health → rollback |
| `internal/metrics` | `xray api statsquery`, дельты кумулятивных счётчиков, online, offline-буфер |
| `internal/logs` | инкрементальный парсер `access.log`/`xray-error.log` по offset |
| `internal/state` | атомарный JSON состояния (`state/state.json`) |
| `internal/systemd` | шаблоны unit-файлов и работа с `systemctl show/is-active/restart` |
| `internal/uninstall` | guard realpath, остановка/отключение юнитов, firewall (nft/ufw), rm -rf base |
| `internal/runner` | главный цикл: register, heartbeat+команды, config poll, apply-result, metrics, diagnose |
| `internal/execx` | абстракция запуска внешних команд (xray, systemctl, nft, ufw) |
| `internal/version` | имя/версия бинарника (переопределяется `-ldflags`) |

Внешних Go-модулей нет — только стандартная библиотека, сборка offline.

## 2. Сборка

Требуется Go 1.23+ (в CI/локально проверено на 1.27.3).

```bash
cd agent
# локальная сборка
CGO_ENABLED=0 go build -trimpath -o bin/vpn-agent ./cmd/vpn-agent

# релизные статические бинарники
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o bin/vpn-agent-linux-amd64 ./cmd/vpn-agent
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags "-s -w" -o bin/vpn-agent-linux-arm64 ./cmd/vpn-agent

# версия из git-тега (опционально)
CGO_ENABLED=0 go build -ldflags "-X vpn-agent/internal/version.Version=1.2.3" -o bin/vpn-agent ./cmd/vpn-agent
```

Проверки, которые обязаны быть зелёными:

```bash
cd agent
go build ./...
go vet ./...
go test ./...
gofmt -l .            # пусто
GOOS=linux GOARCH=arm64 go build ./cmd/vpn-agent
```

## 3. Установка на узел

Панель при провижининге создаёт каталог и юниты; ручная установка выглядит так:

```bash
BASE=/root/config/vpn
install -d -m 0755 "$BASE"/{bin,etc,log,state}
install -m 0755 vpn-agent-linux-amd64 "$BASE/bin/vpn-agent"

# <base>/etc/agent.json — режим 0600, содержит токен
cat > "$BASE/etc/agent.json" <<'JSON'
{
  "panel_url": "https://panel.example.com",
  "token": "<per-node bearer token>",
  "server_id": 1,
  "base_dir": "/root/config/vpn"
}
JSON
chmod 0600 "$BASE/etc/agent.json"

# юниты (шаблоны лежат в packaging/, при необходимости подставьте свой base)
install -m 0644 packaging/vpn-agent.service /etc/systemd/system/vpn-agent.service
install -m 0644 packaging/xray.service       /etc/systemd/system/xray.service
# отредактируйте пути в юнитах, если base != /root/config/vpn
systemctl daemon-reload
systemctl enable --now vpn-agent.service
systemctl enable --now xray.service

# проверка
systemctl status vpn-agent.service --no-pager
journalctl -u vpn-agent.service -n 100 --no-pager
```

Важно: юнит агента запускается как `<base>/bin/vpn-agent serve` **без**
`--base`, поэтому base определяется автоматически (root → `/root/config/vpn`,
пользователь → `$HOME/config/vpn`). Если base нестандартный, добавьте
`--base <путь>` в `ExecStart` или задайте `VPN_AGENT_BASE` в окружении юнита.

Geodata Xray (`geoip.dat`, `geosite.dat`) кладётся в `<base>/etc` — это
раскладка провижинера панели (`backend/app/services/provisioning.py`), и
каталог передаётся ядру как `XRAY_LOCATION_ASSET` (и для боевого запуска, и
для `xray run -test` при применении ревизии). Если geodata лежит в другом
месте, задайте `xray_asset_dir` в `agent.json`.

## 4. Конфигурация `<base>/etc/agent.json`

Обязательные поля:

| Поле | Описание |
|---|---|
| `panel_url` | базовый URL панели, только `http(s)://`, без query/fragment |
| `token` | per-node bearer-токен (файл должен быть 0600; иначе предупреждение в лог) |
| `server_id` | id узла в панели (0 — взять из ответа `register`) |
| `base_dir` | корень состояния узла |

Необязательные (значения по умолчанию в скобках):

| Поле | По умолчанию | Описание |
|---|---|---|
| `heartbeat_interval_s` | 30 | период heartbeat/команд |
| `config_poll_s` | 10 | период опроса конфигурации |
| `metrics_interval_s` | 15 | период сбора/отправки метрик |
| `request_timeout_s` | 15 | таймаут одного HTTP-запроса к панели |
| `health_timeout_s` | 15 | сколько ждать health после рестарта Xray |
| `apply_test_timeout_s` | 60 | таймаут `xray run -test` |
| `apply_restart_timeout_s` | 60 | таймаут `systemctl restart xray` |
| `metrics_buffer_ttl_hours` | 24 | сколько хранить метрики offline |
| `metrics_buffer_max_bytes` | 32 MiB | верхняя граница файла буфера |
| `replay_batch_limit` | 240 | сколько буферизованных батчей отдавать за один тик |
| `online_window_s` | 120 | окно last_seen для online-fallback |
| `retry_max_backoff_s` | 60 | максимальный backoff повторов |
| `auth_backoff_s` | 300 | пауза после 401/403 |
| `http_max_attempts` | 3 | число попыток HTTP-запроса |
| `xray_api_addr` | `127.0.0.1:10085` | адрес Xray Stats API |
| `xray_unit` / `agent_unit` | `xray.service` / `vpn-agent.service` | имена юнитов |
| `xray_binary` | `<base>/bin/xray` | путь к ядру |
| `systemctl_bin`, `nft_bin`, `ufw_bin` | `systemctl`, `nft`, `ufw` | пути/имена утилит |
| `xray_asset_dir` | `<base>/etc` | каталог geodata |
| `unit_dir` | `/etc/systemd/system` | каталог unit-файлов |
| `artifact_name` | `vpn-agent-<goos>-<goarch>` | имя артефакта для самообновления |
| `tls_insecure_skip_verify` | false | отключить проверку TLS (не для продакшена) |
| `send_diagnostics_metrics` | false | отправлять также дельты inbound/outbound-счётчиков |
| `log_level` | `info` | `debug`/`info`/`warn`/`error` |

## 5. CLI

```bash
vpn-agent [serve] [--base DIR] [--log-level LEVEL]  # демон (по умолчанию)
vpn-agent uninstall --yes [--base DIR]               # полное удаление узла
vpn-agent diagnose [--base DIR]                      # JSON-отчёт в stdout
vpn-agent version
```

- `--base` не задан → `VPN_AGENT_BASE`, иначе `/root/config/vpn` для root и
  `$HOME/config/vpn` для обычного пользователя.
- `uninstall` без `--yes` отказывается работать (exit code 2).
- `diagnose` не требует панели: печатает отчёт и выходит.
- Логи — в stderr (journald), структурированные (`slog`), без токена.

## 6. Протокол панели

Все запросы — HTTPS + `Authorization: Bearer <token>`, `User-Agent: vpn-agent/<version>`.
Эндпоинты — `docs/INTERFACES.md` §2.4.

| Шаг | Когда | Что |
|---|---|---|
| `POST /agent/v1/register` | при старте, с повторами | версия агента/Xray, hostname, os, arch, capabilities |
| `POST /agent/v1/heartbeat` | каждые 30 с | ревизия, SHA конфига, состояние юнитов, xray status, degraded_reason; ответ содержит команды и `apply_pending` |
| `GET /agent/v1/config?revision=N` | каждые 10 с | 200 — новая ревизия, 204 — изменений нет |
| `POST /agent/v1/apply-result` | после каждой попытки применения | `applied`/`failed`, error, log_tail, sha256 |
| `POST /agent/v1/metrics` | каждые 15 с | батч дельт (см. §8); ответ может содержать команды |
| `POST /agent/v1/diagnose` | по команде | `{"report": {...}}` |
| `POST /agent/v1/command-result` | после каждой команды | `command_id`, `status` = `done`/`error`, `output` |
| `GET /agent/v1/artifacts/{name}` | по команде `update_agent` | бинарник + `X-Checksum-Sha256` |
| `POST /agent/v1/uninstall` | перед выходом после очистки | `{"status":"done"}` |

Поведение при сбоях:

- сетевые ошибки и 5xx/429 повторяются (до `http_max_attempts`, экспоненциальный
  backoff с jitter);
- 401/403 — агент **не раскрывает** конфигурацию, продолжает работать на
  последней применённой ревизии и уходит в паузу `auth_backoff_s`;
- при недоступности панели Xray продолжает работать, метрики копятся локально;
- результат применения хранится в state как `pending_apply` и доставляется
  at-least-once: если агент упал между применением и отправкой, после рестарта
  результат будет отправлен (или помечен failed, если падение было во время apply).

Команды панели:

| type | Действие |
|---|---|
| `reapply` | принудительно запросить конфиг (`revision=-1`) и применить |
| `restart_services` | перезапустить Xray с health-check; payload `{"units":["agent"]}` дополнительно планирует рестарт самого агента после отчёта |
| `diagnose` | собрать отчёт и отправить в панель |
| `uninstall` | очистить узел и выйти |
| `update_agent` | скачать артефакт, проверить SHA-256, заменить бинарник, запланировать рестарт юнита |

Для `reapply` используется sentinel-ревизия `-1`: панель возвращает текущий
конфиг при любом несовпадении запрошенной ревизии, поэтому `-1` гарантирует
полную выгрузку конфигурации без отдельного эндпоинта.

## 7. Атомарное применение конфигурации

1. проверка: JSON валиден; SHA-256 сверяется с присланным панелью в двух
   формах — по сырым байтам и по канонической форме компилятора (sorted keys,
   compact separators, UTF-8), потому что панель хранит hash канонического
   JSON, а по HTTP отдаёт его повторно сериализованным. Совпадение любой формы
   считается подтверждением; расхождение — предупреждение в `apply.log`/
   `log_tail` (окончательная валидация всё равно выполняется `xray -test`);
2. запись кандидата `<base>/etc/xray.json.new` (0600, atomic write);
3. `<base>/bin/xray run -test -c <base>/etc/xray.json.new` — валидация **до**
   замены рабочего файла;
4. бэкап текущего `etc/xray.json` → `etc/xray.json.bak`;
5. `rename(xray.json.new → xray.json)` (атомарно в пределах ФС), режим 0600;
6. `systemctl restart xray.service`;
7. health-check: `systemctl show` = `active`, TCP-connect на все inbound-порты
   конфига, плюс `xray api statsquery` если в конфиге есть Stats API;
8. при провале шага 6/7 — rollback: вернуть `xray.json.bak`, рестарт, дождаться
   `active`, отправить `apply-result failed` с `error` и `log_tail`;
9. при успехе — сверить desired-state клиентов с `xray api inbounduser`;
   расхождение не откатывает конфиг, но помечает узел `degraded` в heartbeat.

Все шаги пишутся в `<base>/state/apply.log` (ротация при >256 KiB). При ошибке
первой установки (бэкапа нет) неудачный конфиг удаляется, чтобы узел не остался
в нерабочем состоянии.

## 8. Метрики

Батч `POST /agent/v1/metrics` (пути — `<base>/log/access.log`,
`<base>/log/xray-error.log`, состояние — `<base>/state/state.json`,
буфер — `<base>/state/metrics-buffer.jsonl`):

```json
{
  "ts": "2026-01-01T00:00:00Z",
  "revision": 42,
  "agent_version": "0.1.0",
  "xray": {"active": true, "uptime_s": 123456, "version": "26.11.9", "pid": 777, "goroutines": 12},
  "traffic_delta": {"up": 1234, "down": 5678},
  "clients": [{"email": "client-1@vpn", "up": 10, "down": 20, "online": true, "ips": ["203.0.113.7"]}],
  "connections": {"accepted_15s": 12, "blocked_15s": 2, "online": 7},
  "errors": {"rejected_15s": 3, "xray_error_15s": 0},
  "replay": false
}
```

- `traffic_delta` и `clients[].up/down` — **дельты** кумулятивных счётчиков
  `user>>>EMAIL>>>traffic>>>uplink|downlink`; baseline хранится в state,
  при рестарте ядра или уменьшении счётчика дельта = текущее значение
  (отрицательных значений и потерь нет); `reset=true` не используется.
- `inbound>>>`/`outbound>>>` счётчики опрашиваются для диагностики и отправляются
  только при `send_diagnostics_metrics: true` (поле `diagnostics`); квоты
  клиентов всегда считаются только по `user>>>`.
- online: `xray api statsonlineiplist -all` (fallback —
  `statsgetallonlineusers`), IP-адреса клиентов; если команды недоступны —
  fallback по last_seen из `access.log`/дельт трафика (`online_window_s`).
- accepted/blocked/rejected и ошибки — из логов по байтовым offset из state.
  Парсятся оба варианта стрелки Xray (`->` и `>>`), поддержан и JSON-формат
  логов. `accepted ... [inbound -> block]` — блокировка маршрутизации,
  `rejected ...` — отказ протокола, строки `[Error]` в error-логе — `xray_error_15s`.
- uptime процесса Xray — из `systemctl show` (`ActiveEnterTimestampMonotonic`
  + `/proc/uptime`), `goroutines` — best-effort из `xray api statssys`.
- При недоступности панели батчи дописываются в JSONL-буфер (fsync, 0600),
  TTL 24 ч; при восстановлении связи отправляются по порядку со
  `"replay": true` (не более `replay_batch_limit` за тик).

## 9. Диагностика

`vpn-agent diagnose` или команда панели `diagnose` собирают JSON-отчёт:
версии агента и Xray, ревизия и SHA конфига, hostname, ОС (`/etc/os-release`,
ядро, arch), состояния `xray.service`/`vpn-agent.service` (active state, PID,
uptime), локальный health-check, последние 200 строк логов (`access.log`,
`xray-error.log`, `apply.log`), размер offline-буфера, время последней
отправки метрик, `degraded_reason`. Токен и содержимое конфигов в отчёт не
попадают.

## 10. Удаление узла (uninstall)

`vpn-agent uninstall --yes` (CLI) или команда панели `uninstall`:

1. **guard**: realpath base-каталога, отказ для `/`, `$HOME` и системных
   каталогов, запрет symlink и каталогов вне home, обязательное наличие
   `etc/agent.json`; база удаляется только после успешной проверки;
2. снятие firewall-правил: nft-таблицы с префиксами `vpn_agent`/`vpn-agent`,
   ufw allow-правила для inbound-портов из `etc/xray.json` (fallback 443/18443);
3. `systemctl stop xray`, `disable xray`, `disable vpn-agent`; при запуске
   CLI-`uninstall --yes` (например, панелью по SSH) дополнительно выполняется
   `systemctl stop vpn-agent`, чтобы демон не воссоздал каталоги после очистки;
4. удаление unit-файлов, `daemon-reload`, `reset-failed`;
5. `rm -rf` base-каталога;
6. панель-команда: `POST /agent/v1/uninstall {"status":"done"}` и
   `command-result done`, затем агент вызывает
   `systemctl --no-block stop vpn-agent.service` и выходит с кодом 0: юнит уже
   отключён и удалён, а явный stop не даёт systemd перезапустить процесс по
   `Restart=always`.

Ограничение: при удалении по команде панели агент работает внутри юнита с
`ProtectSystem=strict`, поэтому `ufw`-команды могут не иметь доступа к
`/etc/ufw`; nft-очистка работает (netlink). Если узел использует ufw,
правила также снимаются панелью при провижининге/удалении по SSH либо вручную
из root-shell вне юнита; шаги очистки в отчёте uninstall показывают статус
каждого действия.

## 11. systemd hardening

Шаблоны: `packaging/vpn-agent.service`, `packaging/xray.service` (генерируются
функциями `internal/systemd`; тест сверяет файлы с рендерером).

Агент: `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=no`
(base в домашнем каталоге), `ReadWritePaths=<base>`, `PrivateTmp=yes`,
`Restart=always`, `UMask=0077`, `RestrictAddressFamilies=AF_UNIX AF_INET
AF_INET6 AF_NETLINK` (systemctl + nft/ufw), `ProtectKernelTunables`,
`ProtectControlGroups`, `RestrictRealtime`, `RestrictSUIDSGID`,
`LockPersonality`.

Xray: те же базовые ограничения + `Restart=on-failure`, `LimitNOFILE=65535`,
`ProtectKernelModules=yes`, `Environment=XRAY_LOCATION_ASSET=<base>/etc`.

Файл с токеном — 0600 (иначе агент пишет предупреждение), state и apply-лог —
0600, каталоги `etc`/`state` — 0700.

## 12. Тесты

```bash
cd agent
go test ./...                 # всё зелёное
go test -race ./internal/state ./internal/metrics
go test -run TestApply -v ./internal/apply
```

Покрыто:

- фейковая панель (`httptest`): register, heartbeat + команды, config 200/204,
  apply-result, metrics (включая offline-буфер и replay), diagnose,
  command-result, artifacts;
- fake `xray`/fake `systemctl` shell-скрипты: успешный apply, провал
  `xray run -test`, провал restart и health → rollback с восстановлением
  предыдущего `xray.json`; проверка SHA-256; degraded при расхождении клиентов;
- дельта-логика: первый срез, нормальный прирост, сброс счётчиков (рестарт ядра),
  смена baseline, агрегация по клиентам/inbound/outbound;
- парсер логов: реальные строки Xray 26.11.9, offset, частичная строка,
  ротация/усечение, блокировки, rejected, ошибки, tail;
- uninstall guard: `/`, home, системные каталоги, symlink, выход за home,
  отсутствие маркера; полный прогон удаления с fake nft/ufw/systemctl;
- HTTP retry/backoff: 5xx/429 повторяются, 401/403 без повторов, 4xx без
  повторов, `replay`-порядок;
- CLI: version/help/unknown command, uninstall без `--yes`, отсутствие конфига.

## 13. Траблшутинг

| Симптом | Что проверить |
|---|---|
| `401 Unauthorized` в логе | токен в `etc/agent.json` совпадает с панелью; файл 0600 |
| apply failed: `xray -test` | `log_tail` в ответе apply-result и `state/apply.log`; geodata в `<base>/etc`; версия Xray |
| apply failed: health check | `systemctl status xray`; порт inbound занят; `journalctl -u xray` |
| узел degraded | расхождение клиентов (`xray api inbounduser`), неактивный xray, ошибки statsquery |
| метрики не идут | `state/metrics-buffer.jsonl` растёт → панель недоступна; проверить `journalctl -u vpn-agent`, DNS/фаервол исходящих 443 |
| Xray не стартует после установки | `etc/geoip.dat`, `geosite.dat`; права `etc/xray.json`; `ReadWritePaths` в юните |
| state повреждён | агент сам переносит его в `state.json.corrupt` и начинает с чистого; ревизия перезапросится с панели |

## 14. Замечания для интеграции с backend

- `POST /agent/v1/diagnose` принимается как `{"report": <объект>}`.
- `command-result.status`: `done` / `error`; `output` — до 4096 символов.
- `apply-result.revision` — ровно та ревизия, что пришла в `/agent/v1/config`;
  агент не двигает локальную ревизию при `failed`.
- Имя артефакта по умолчанию `vpn-agent-<goos>-<goarch>`
  (`vpn-agent-linux-amd64`, `vpn-agent-linux-arm64`), переопределяется
  `artifact_name`; обязателен заголовок `X-Checksum-Sha256`.
- `reapply` запрашивает конфиг с `revision=-1` — бэкенд должен вернуть
  текущий конфиг при любом `revision != current` (это уже следует из §2.4).
- В metrics-батче кроме полей §7 design.md есть `agent_version` (требование
  спецификации «добавляя версию») и опциональное `diagnostics`; неизвестные
  поля панель обязана игнорировать.
