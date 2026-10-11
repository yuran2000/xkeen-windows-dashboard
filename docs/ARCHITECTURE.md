# Архитектура xray-dashboard (панель управления XKeen)

> **Читать первым** перед любой правкой панели: устройство `dashboard.py` (~22 тыс. строк) и куда
> идти для правки — без повторной разведки кода. Ссылки на код — **только по идентификаторам**
> (функция, маршрут, константа, `id`, маркер раздела `# ============== ИМЯ ==============`); номеров
> строк нет — файл живой. Переход: `grep -n "def keenetic_ssh" dashboard.py`. Документ обновляется
> **в том же коммите**, что и код (§12).
>
> Справочники вынесены в `docs/architecture/` (номера § те же): §4 маршруты — `04-routes.md`, §6 watchdog —
> `06-watchdog.md`, §7 каналы и цепочки — `07-chains.md`, журнал изменений — `changelog.md`. Этот файл читать
> целиком; справочник — когда задача его касается.

Обозначения: `<install-dir>` — папка установки (обычно `C:\xray-dashboard`), `<router-ip>` — роутер
Keenetic, `<vpn-domain>` — `cfg.EXTERNAL_DOMAIN`, `<home-domain>` — `cfg.HOME_DOMAIN`,
`<github-user>` — аккаунт GitHub автора коммитов.

## Шпаргалка: куда идти

| Задача | Где |
|---|---|
| Что делает кнопка UI | `onclick` → JS-функция главного `<script>` `XKEEN_TEMPLATE` → `fetch('/api/…')` → §4 (`architecture/04-routes.md`) |
| Новый ключ `watchdog.config` | §6.2 (`architecture/06-watchdog.md`) + чек-лист §9.3 |
| Показать поле `watchdog.state` | §6.3, §6.4 (`architecture/06-watchdog.md`) + чек-лист §9.2 |
| Логика переключения каналов | `bootstrap/watchdog.sh.cur`, не Python (§6.6); поднять версию (§6.5) |
| Новая роль / цепочка | §7.1, §7.4 (`architecture/07-chains.md`) |
| Новый блок или карточка на `/xkeen` | §8.6 |
| Эндпоинт, пишущий на роутер | §5.2, §9 (fail-closed; бэкап → запись → откат) |
| Проверка в «🚑 Диагностике» | `api_xkeen_diagnose` + `XKEEN_AUTO_FIXES` + ветка `api_xkeen_auto_fix` (§3.10) |
| Файл в бэкапе | `xkeen_backup_paths.STATIC_FILES` / `DYNAMIC_GLOBS` |
| Проверить / выпустить | §10 / §11 |

---

## 1. Что это и как запущено

**Назначение.** Flask-приложение одним файлом: Python + четыре HTML/JS-шаблона в raw-строках.
Две половины: `/` — мониторинг локального xray-сервера на ПК (процессы, порты, логи, QR профилей,
подписка `/sub/<token>`); работает, только если в `config_local.py` заданы `PROFILES` /
`MONITORED_PORTS_INFO`, иначе режим client-only (`index` редиректит на `/xkeen`, флаг
`is_client_only`). И `/xkeen` + `/api/xkeen/*` + `/api/keenetic/*` — управление XKeen (xray) на роутере
Keenetic по SSH (~95 % кода). Источник истины — роутер; каждый рендер `/xkeen` читает его заново.

**Процесс.** `app.run(host="0.0.0.0", port=cfg.DASHBOARD_PORT, debug=False)` — Werkzeug,
многопоточный, **без автоперезагрузки**; порт по умолчанию 5000 (`install.bat` предлагает свободный из
5000–5019). Прод — задача Планировщика `XrayDashboard` (порт ≠ 5000 → `XrayDashboard-<port>`) от
`install_service.ps1` (через `install_service.bat`, нужен администратор):
`.venv\Scripts\pythonw.exe dashboard.py`, пользователь **SYSTEM**, AtStartup, RestartCount 10; вывод
pythonw не сохраняется. Разработка: `start.bat` (консоль), `start-bg.bat` (фон), `stop.bat`.
Мульти-инстанс — соглашение «папка = инстанс = порт».

Следствия SYSTEM: git не в `PATH` → `_find_git`; нет known_hosts → `ssh_bin.null_known_hosts()`; argv
перекодируется в cp1251 → в командах только ASCII, кириллица через stdin; toast из SYSTEM не виден →
отдельный демон; ключ SSH копируется в папку установки с ACL SYSTEM/Administrators.

**Спутники** (импортируют `config_local` и `ssh_bin`; `runtime_settings.json` **не** читают):

| Скрипт | Запуск | Что делает |
|---|---|---|
| `xkeen_toast_daemon.py` | задача пользователя ONLOGON (`install_toast_alerts.bat`) | раз в 60 с опрашивает `/api/xkeen/header-status`, Windows-toast |
| `xkeen_snapshot_daily.py` | ежедневная задача (UI предлагает `XkeenSnapshotDaily`) | снапшот v7 в `backups/xkeen-snapshot-ГГГГММДД-ЧЧММСС.json`, хранит 14 ежедневных |
| `sub_expiry_notifier.py` | ежедневная задача `SubExpiryCheck` | подписки из `subscription_meta.json`, истекающие за `WARNING_THRESHOLD_DAYS` (3) дня → Telegram; путь лога захардкожен |

**Код = git-репозиторий = прод.** Папка установки — клон публичного репозитория; панель исполняет
рабочее дерево. Python и шаблоны грузятся при старте → после правки нужен перезапуск.
`bootstrap/*.cur` читаются при каждом обращении → `watchdog.sh.cur` с поднятой версией уйдёт на роутер
при первом «Сохранить» в UI, **даже незакоммиченный** (§6.5).

**Как прод подхватывает изменения:** кнопка в UI → `POST /api/xkeen/restart-dashboard` →
`api_xkeen_restart_dashboard` (одноразовая задача `XrayDashboardRestarter` от SYSTEM:
`schtasks /End /TN XrayDashboard` → 3 с → `/Run` → самоудаление; имя `XrayDashboard` захардкожено);
`Restart-Clean.bat` (UAC, порт из конфига); `update.bat` → `update.ps1` (`git pull` с авто-stash, стоп
задачи, `pip install` при изменении `requirements.txt`, старт). Проверка — `/api/xkeen/dashboard-health`
(ровно один LISTEN) и версия в шапке.

---

## 2. Карта файлов репозитория

| Файл | Назначение |
|---|---|
| `dashboard.py` | монолит: конфиг, SSH-слой, логика, маршруты, шаблоны `DYNAMIC_TEMPLATE`, `LOGIN_TEMPLATE`, `TEMPLATE`, `XKEEN_TEMPLATE` |
| `xkeen_killswitch.py` | генератор netfilter.d-хука kill-switch политики XKeen, разбор статуса/утечек; без Flask |
| `wg_clients.py` | WireGuard-доступы: ключи X25519, скан роутера и свободный слот, интерфейс через RCI, access-list/NAT по режиму выхода, хук подмены адреса WG → ЛВС, отзыв, инвентарь, реестр выданного; без Flask (§3.4а) |
| `xkeen_backup_paths.py` | пути снапшота (`STATIC_FILES`, `DYNAMIC_GLOBS`), `build_snapshot_command`, `is_restorable`, `needs_exec_bit` |
| `ssh_bin.py` | `ssh_exe()` (встроенный OpenSSH по полному пути), `null_known_hosts()` |
| `geosite_scanner.py` | декодер `.dat`: `DatCache`, `detect_dat_type`, `lookup_domain`, `lookup_ip`, `list_categories` |
| `rkn_checker/` | вендоренный rkn-block-checker для «🔎 Почему не работает сайт» (`rkn_checker/VENDOR.md`) |
| `xkeen_snapshot_daily.py`, `sub_expiry_notifier.py`, `xkeen_toast_daemon.py` | спутники (§1) |
| `setup_helper.py` | для `install.bat`: `detect-port`, `busy-ports`, `set-port`, `gen-secrets`, `get-port`, `password-is-placeholder` |
| `config_local.example.py` | шаблон `config_local.py`, все `cfg.*` с комментариями |
| `requirements.txt` | flask, psutil, qrcode[pil], requests, PySocks, curl-cffi |
| `bootstrap/watchdog.sh.cur` | эталонный `watchdog.sh` — движок сторожа на роутере (§6) |
| `bootstrap/05_routing.template.json.cur` | эталонный routing-шаблон |
| `tests/test_*.py` | офлайн-тесты (§10.3) |
| `install.bat`, `install_service.bat`/`.ps1`, `start.bat`, `start-bg.bat`, `stop.bat`, `Restart-Clean.bat`, `update.bat`/`.ps1`, `install_toast_alerts.bat`, `uninstall_toast_alerts.bat` | установка, служба, запуск, обновление, toast |
| `release.ps1`, `release_notes_footer.md` | релиз (§11), футер release notes |
| `portable/build.bat`, `portable/config.ini.template` | portable-exe (PyInstaller), шаблон `config.ini` |
| `.github/workflows/release-archives.yml`, `.github/ISSUE_TEMPLATE/*` | архивы-ассеты к релизу, шаблоны issue |
| `docs/*.png`, `docs/ARCHITECTURE.md`, `docs/architecture/*.md` | скриншоты README; этот документ и его справочники (§4, §6, §7, журнал) |
| `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `THIRD-PARTY-NOTICES.md`, `LICENSE` | публичная документация («Архитектура» в `CONTRIBUTING.md` устарела) |
| `.gitattributes`, `.gitignore`, `icons8-favicon-64.png` | переводы строк (§9.5), исключения, иконка |

Вне git: `config_local.py`, `config.ini`, `runtime_settings.json`, `backups/` (снапшоты, `dat_cache/`,
`entware_backup_*.tar.gz`), `*.log`, `.ssh/`, `.venv/`, `*.bak-*`, `wg_clients.json` (+ `.bak`, `.tmp`).

---

## 3. Карта `dashboard.py` сверху вниз

~10,7 тыс. строк Python, затем `# ============== TEMPLATES ==============` (`XKEEN_TEMPLATE` — около
половины файла) и `if __name__ == "__main__"`. Подразделы — по маркерам в порядке следования; маршруты
перечислены в §4, детали подсистем — в §5–§8.

### 3.1. Шапка, `БАЗОВЫЕ ПАПКИ ПРИЛОЖЕНИЯ`, `ВЕРСИЯ ПАНЕЛИ`

Опциональные импорты с флагами: `_RKN_OK` (`_rkn_check_urls`), `_GEOSITE_OK` (`_geosite_scanner`),
`_CCFFI_OK` (`_ccffi_requests`, Chrome-отпечаток для anti-bot сайтов) — без модуля фича пишет
«недоступно» (DAT — 503), новую фичу на них — только за проверкой флага. `_app_base_dir()` — внешние
данные; `_resource_dir()` — вшитые ресурсы (`bootstrap/`, favicon; в onefile `sys._MEIPASS`).
Frozen-режим: автосоздание `config.ini`, подменный `config_local` из примера. `import config_local as
cfg` — **весь код читает настройки как `cfg.*`**; поверх ложатся `config.ini` (frozen: `[server]`,
`[ssh]`, `[auth]`) и `runtime_settings.json`. `_VERSION_FALLBACK` + `get_dashboard_version()`
(`git describe --tags --abbrev=0`, только `\d+.\d+.\d+` из вывода, кэш 60 с).

### 3.2. `RUNTIME SETTINGS`, `AUTH`, `DATA COLLECTORS`

`RUNTIME_SETTINGS_FILE`, `KEENETIC_RUNTIME_KEYS` (8 полей: `host`, `port`, `user`, `ssh_key`,
`xray_configs`, `xray_bak_dir`, `watchdog_state`, `watchdog_log` → `cfg.KEENETIC_*`),
`load_runtime_settings`, `save_runtime_settings`, `apply_runtime_overrides()` (`setattr(cfg, …)` при
импорте и после `POST /api/keenetic/settings` — без рестарта), `get_keenetic_settings_merged()`
(`{current, overridden}`). Пароль, порт, токен подписки — только `config_local.py`/`config.ini`. Тут же
`_WATCHDOG_CFG_LOCK`, `app`, `PERMANENT_SESSION_LIFETIME` (`cfg.SESSION_DAYS`), `SESSION_COOKIE_SAMESITE`
(`"Lax"` явно: cookie не уходит с кросс-сайт POST — защита от CSRF без токенов), `SUBS_LOG`.
`requires_auth` — на каждом маршруте. Единственный `before_request` — `_csrf_origin_guard`: изменяющий
запрос (`_CSRF_METHODS`) с `Origin` (или `Referer`, если `Origin` нет) чужого хоста → 403 до обработчика
(JSON для `/api/*`); без обоих заголовков (скрипты, тесты) — проходит; доп. адреса — `cfg.TRUSTED_ORIGINS`. `collect_dynamic_data`,
`parse_xray_config` и соседи — только для `/`.

### 3.3. `XKEEN MANAGEMENT (Phase 1)`

SSH-слой (§5.2): `_ssh_args`, `is_keenetic_reachable`, **`keenetic_ssh(cmd, timeout, stdin_data)` →
`{ok, stdout, stderr, code}` — единственная точка выполнения команд на роутере**, `keenetic_read_file`,
`_strip_json_comments`, `keenetic_load_json`, `keenetic_tail_log`. `keenetic_geoip_dat_status()` —
одной лёгкой командой размер/mtime/симлинк `geoip.dat` (`GEOIP_DAT_PATH`) и `geoip_v2fly.dat`
(`GEOIP_V2FLY_DAT_PATH`, его ежедневно обновляет `xkeen -ug`) → `{exists, size, size_mb, mtime,
mtime_str, is_link, link_target, linked_to_v2fly, v2fly_exists, v2fly_size, v2fly_size_mb, v2fly_mtime,
v2fly_mtime_str, v2fly_usable, has_telegram, is_extended}` или `None`. Поиск `TELEGRAM` в 16 МБ на
роутере идёт ~9 с, поэтому он **в фоне и один раз на версию файла**: `_geoip_tg_lookup` /
`_geoip_tg_check_worker`, кэш `_GEOIP_TG_CACHE` по `(size, mtime)`; пока результата нет —
`has_telegram=None`. Расширенная = больше `GEOIP_EXTENDED_MIN_SIZE` (5 МБ) и `has_telegram` не False.
RCI: `_keenetic_rci` (GET `localhost:79/rci/<path>` curl'ом на роутере). WireGuard-клиенты:
`keenetic_get_wireguard_interfaces`, `WG_SPLIT_ALLOWEDIPS` (без RFC1918 — иначе
Windows-клиент включает свой kill-switch), `build_wg_client_conf`, `build_wg_server_conf`,
`_build_wg_xray_hook` — **источник истины** хука `zz_wg_xray_<iface>.sh` (ручные правки на роутере
перезатираются); `wg_xray_connect(iface_id)` / `wg_xray_disconnect(iface_id)` — установка и снятие хука
(dict-результат; маршруты `wg-connect`/`wg-disconnect` — обёртки, их же зовут выдача, отзыв и правила;
снятие после чистки по факту прогоняет хук подмены адреса `wg_clients.MASQ_HOOK`). Снятие НЕ требует адреса
интерфейса (выключенный его не имеет): подсеть для MASQUERADE — из `SUBNET=` самого хука, иначе из интерфейса. `_keenetic_lan_net()` —
(ЛВС, её интерфейс) по адресу `cfg.KEENETIC_HOST` в `ip addr` роутера, только если он на мосту `brN`;
иначе (панель ходит к удалённому роутеру по WG/VPN, адрес на WAN, имя вместо IP) — с моста `br0`. Кэш
`_WG_LAN_CACHE` 10 мин; при сбое чтения — последнее удачное определение; кэша нет — при `guess=True`
догадка `<адрес>/24, br0`, при `guess=False` — `(None, br0)`. `_keenetic_lan_net_ex` отдаёт ещё признак
`known` (прочитано, а не догадка) — по нему `_wg_settings` ставит `Settings.lan_known`. `wg_xray_connect`: запись
хука — условие для остального (`… || { …; echo WG_CONN_WRITEFAIL; exit 1; }`), не записался — `ok:False`.

### 3.4. `KILL-SWITCH ПОЛИТИКИ XKEEN (v1.1.0)` и соседи

Защита от утечки клиентов политики XKeen напрямую в окне `xkeen -restart`. Генератор —
`xkeen_killswitch.py` (`HOOK_PATH`, `CHAIN` = `xkeen_ks`, `MODES`: `protect` — DROP по connmark
политики, `observe` — только счёт; `build_hook`, `install_command`, `remove_command`, `parse_status`,
`parse_leaks`). В панели: `_KS_LOCK`, `_ks_read_params()` (метка политики по RCI `show/ip/policy`,
порты, WAN), `keenetic_killswitch_status()` (`absent`/`broken`/`observe`/`protect`, дрейф, утечки),
`keenetic_ensure_killswitch(mode, force)` (идемпотентно, маркер `KS_INSTALL_OK`). Kill-switch сам
перечитывает хуки `zz_wg_xray_*` (WG под заворотом пропускаются в FORWARD); хук подмены адреса под этот
шаблон не попадает. Дальше без маркера: блок `WG-ДОСТУПЫ` (§3.4а), DAT-кэш (`_fetch_dat_via_ssh`, `_list_remote_dat`, `_get_dat_cache` → `<base>/backups/dat_cache`), anti-bot
(`_chrome_fp_probe`), sidecar-файлы (`keenetic_read_domain_notes`, `keenetic_write_domain_notes`,
`keenetic_read_sub_meta`, `keenetic_write_sub_meta`, `keenetic_set_sub_meta` — дата по ключу-`sub_url`
копируется во все pbk-«тени» подписки).

### 3.4а. `WG-ДОСТУПЫ` — выдача WireGuard-доступов (`wg_clients.py`)

Логика — модуль `wg_clients.py` без Flask; связь с роутером через `configure(ssh_fn, settings)`:
панельный `_wg_ssh` (адаптер `keenetic_ssh` → `(rc, stdout, stderr)`) и `_wg_settings()` →
`wg_clients.Settings` из `cfg.WG_*` (`config_local.example.py`, раздел WIREGUARD-ДОСТУПЫ) + ЛВС с роутера.
`_wg_prepare(write)` — в начале каждого WG-маршрута; ЛВС не подтверждена (нет `WG_LAN_SUBNET`, чтение
не удалось, кэша нет) — `Settings.lan_known=False`: маршруты записи (`wg-issue`, `wg-revoke`, `wg-acl-apply`)
отказывают с `_WG_LAN_UNKNOWN` (догадка записала бы на роутер чужую сеть); чтение (`wg-clients`, `wg-router`,
`wg-acl`) работает по догадке, с пометкой «?» у ЛВС и `lan_warn` (заглушка вместо догадки превращала
выданные доступы в «линки» и портила шаблон `.conf`). Реестр выданного — `<base>/wg_clients.json`
(`_app_base_dir`), атомарная запись, битый не затирается (`RegistryError`), приватных ключей нет.
- Топология: доступ = свой `WireguardN` и своя `/24` `<prefix>.X.0/24` (роутер .1, клиент .2); доступ на
  роутере ищется **по ключу клиента** среди пиров (`iface_by_pub`), не по имени.
- `scan_router` — fail-closed: `/rci/show/interface` + `/rci/interface` + хуки xray (`xray_hooks`) обязаны
  прочитаться; WAN-адрес (интерфейс с `defaultgw`) запоминается в `_WAN` — это endpoint по умолчанию
  (`endpoint_host`, `endpoint_warning` для пустого/серого). `free_slot` — низший свободный индекс (не под
  хуком xray), подсеть, порт.
- `_is_link` — линк (не трогаем): endpoint у пира или чужая сеть в allow-ips; своя s2s из реестра — не линк,
  пока все сети пира в своей /24, ЛВС и `remote_lan`.
- `issue` — проверка ввода (`NAME_RE`, `collect_grants`, `parse_remote_lan`) → скан → нет адреса подключения
  (`endpoint_host` пуст) — отказ до реестра и роутера → имя не занято → слот →
  `genkey` ×2 → `client_conf` / `router_conf` / `router_block_text` → запись реестра с меткой
  `router_dirty` → [`create_iface` (проверка 404 индекса, один RCI parse-массив, сверка публичного ключа,
  откат) → `apply_acl` → при режиме xray `xray_on` = `_wg_xray_on`].
- `apply_acl` — отвязать → удалить → создать (`acl_lines`: разрешённое в ЛВС → [xray: порты управления
  своего .1 закрыты, сам .1 открыт] → deny `BLOCK_NETS` + `<prefix>.0.0/16` + `block_extra` + свой WAN →
  permit; порты управления — `ROUTER_ADMIN_PORTS` + `Settings.admin_ports` = `cfg.KEENETIC_PORT`, SSH панели) →
  привязать → `auto-delete` → перечитать и сверить число → NAT (`ip nat` только `direct`) →
  save → `lan_masq_set`. Режимы — `EXIT_MODES`: `direct`, `xray`, `""` (только ЛВС).
- Подмена адреса WG → ЛВС — один хук `MASQ_HOOK` (`zz_wg_lan_masq.sh`); какие подсети чьи — в самом
  хуке строкой `MAP="WireguardN=a,b …"` (`lan_masq_read`, `_masq_hook_state`, `_parse_map`): роутер — источник
  истины. `lan_masq_set` снимает и правила со старыми `LANNET`/`LANIF` из хука (ЛВС или мост сменились);
  значения из хука идут в команду только после проверки формата. 🔴 Команда чтения (`_masq_hook_state`)
  обязана кончаться `echo END`: код возврата цепочки — код последней команды, `sed` по отсутствующему
  хуку даёт 1/2, и «хука ещё нет» читалось бы как сбой — хук не создавался бы никогда (поймано
  офлайн-тестами; имитация в тесте отвечает ненулевым кодом, если команда кончается не на `echo END`).
- `revoke` / `delete_iface` — отказ для линка и интерфейса с чужими пирами; заворот снимается через
  `xray_off`, и только ПОСЛЕ этого ставится метка `router_dirty` (`before_change`): хук xray — файлы и cron,
  не конфигурация Keenetic, а метка без единой команды конфигурации привела бы к лишнему save; NAT →
  маршрут → `no interface` → не удалился — вернуть снятое, включая заворот (`xray_on`); save; «отозван»
  только после save; «на роутере нет» → save только по метке (не закрепить чужое несохранённое).
- `rules_form` / `apply_rules` — форма «🔒 Правила». Режим предзаполняется по факту: есть хук заворота →
  `xray` (раньше NAT и записи реестра — заворот могли включить кнопкой), признак `xray_on` для
  предупреждения в подтверждении. `apply_rules` → `(ok, msg, итоговый режим)`: для site-to-site режим
  всегда `""`. `api_wg_acl_apply` решает по итоговому режиму: `xray` — включить заворот (и если хук не
  прочитан: включение идемпотентно), другой режим — снять; хук не прочитан — предупреждение в ответе.
- Доступ не из реестра (создан вручную) — галочки предзаполняет `prefill_from_acl(raw, exit_mode, own_ip)`
  из **текущих** правил (`_acl_raw_rules`; режим — по хуку и `ip nat`), `pre.src` = «текущих правил
  роутера»: разрешения TCP к хосту, покрывающие все порты пресета `WG_SERVICES`, — галочка пресета
  (порты — `_rule_ports`: диапазон RCI бывает и строкой `1560-1591`, и парой `destination-port` +
  `destination-end-port`); TCP+UDP (или `ip`) ко всей ЛВС без портов — «полный доступ»; остальное — строки
  «вручную» `IP[/маска][:порты]`; ICMP и «любой» после запретов в режиме с интернетом пропускаются (их ставит
  панель). 🔴 В режиме xray разрешение к своему .1 (`own_ip`) во «вручную» не переносится: строка «вручную»
  встала бы выше запретов портов управления и открыла бы клиенту управление роутером. Что модель панели не
  выражает — не предзаполняется, а уходит в `warnings` (блок «⚠ При применении» в диалоге): выключенные
  правила (будут удалены), «TCP/UDP → любой» без режима или до любого запрета (шире модели), TCP без UDP
  (хост откроется целиком), UDP отдельно (пропадёт), запрет, сужающий разрешение ниже него или (в режиме с
  интернетом) вне `_block_nets`, — пропадёт; правила не TCP/UDP/ICMP. У выданного панелью предзаполнение —
  из реестра, `warnings` пуст. Правила, поставленные самой панелью, восстанавливаются без потерь и
  предупреждений во всех режимах (проверка «обратимость» в тесте).
- `manual_view` — доступы роутера, выданные не панелью и не линки: поле `manual` в `wg-clients`, серые строки
  «создано вручную» в «📋 Выданные» (правила — да, отзыв — нет).
- `router_inventory` — все `WireguardN` (доступ/линк, онлайн, трафик, NAT, заворот, `.conf`-шаблон);
  `backup_text` — бэкап одного доступа.

### 3.5. `OUTBOUND LIVENESS PROBES` — ядро outbound'ов и watchdog

- Handshake-пробы (§7.5): `probe_outbounds`, `_probe_via_router`, `_probe_targets_batched`,
  `_OUTBOUND_STATUS_CACHE` (TTL `_OUTBOUND_STATUS_TTL` = 60 с), `_PROBE_SSH_LOCK`.
- Мета: `keenetic_read_meta`, `keenetic_write_meta`, `keenetic_set_meta` (`""` очищает, `None` не
  трогает), `keenetic_remove_meta_entry`.
- `keenetic_get_outbounds()` (`None` — сбой, `[]` — пусто), `keenetic_get_routing_active()`.
- watchdog (§6): `keenetic_get_watchdog`, `keenetic_read_watchdog_config`,
  `keenetic_get_watchdog_targets(strict)`, `_build_watchdog_config`, `_structure_drift_status`,
  `keenetic_ensure_watchdog_current`, **`keenetic_write_watchdog_config`**, `keenetic_set_watchdog_target`
  (новый PRIMARY → старый встаёт головой `FAILOVER_TAGS`, `PRIMARY_PROBE_URL` переключается),
  `keenetic_force_mode`.
- Удаление (§7.4): `keenetic_remove_outbound`, `keenetic_remove_outbounds_bulk`,
  `_subscription_removal_plan` (**чистая** функция), `keenetic_remove_subscription`,
  `keenetic_find_orphan_sub_meta`, `keenetic_purge_orphan_sub_meta`.
- `keenetic_add_outbound` — вставка перед `direct`, бэкап → запись → `xkeen -restart` → проверка
  **процесса** xray (код возврата рестарта ненадёжен) → откат.
- socks-local (канал TG-алертов watchdog, `SOCKS_LOCAL_TAG`, `SOCKS_LOCAL_PORT` = 10808):
  `keenetic_ensure_socks_local` (inbound + тест + рестарт + откат), `keenetic_ensure_socks_route`.
- `keenetic_sync_structure()` — «⚙ Применить структуру»: `*.syncbak-<ts>` → watchdog.sh → baseline
  template + `_auto_sync_template_ips_silent` → `watchdog.sh` → `xray run -test` (ожидание тишины,
  повтор при RC=137) → `xkeen -restart` + health-check → при провале откат трёх файлов.

### 3.6. `ROUTES`, `XKEEN ROUTES` — `/` и `/xkeen`

`index` (URL подписки `https://<home-domain>:8443/sub/<token>` — порт захардкожен), `api_state`,
`_xkeen_no_cache` (`no-store` на `/xkeen*`, `/api/xkeen*`), `_xkeen_error_handler` (HTTPException как
есть; на `/xkeen*`/`/api/xkeen*` — traceback в stdout и в ответ: JSON `{ok:false, error, traceback}`
или HTML «❌ Ошибка при рендеринге /xkeen»).

**`xkeen_page`** (~600 строк):
1. `is_keenetic_reachable()` нет → **quick-fail**: литералы `watchdog` и `targets` с дефолтами (ключи
   1:1 с функциями, §9.2);
2. иначе `keenetic_get_outbounds`, `keenetic_get_routing_active`, **сначала**
   `keenetic_get_watchdog_targets`, потом `keenetic_get_watchdog(deep_hold_sec=…)`;
3. статусы только из `_OUTBOUND_STATUS_CACHE` (рендер проб не запускает);
4. флаги outbound'а: `is_default`, `is_primary`, `is_failover`, `is_ai`, `is_yt`, `is_foreign`,
   `in_failover_chain`, `in_ai_chain`/`in_yt_chain`/`in_foreign_chain` + `*_chain_position`,
   `is_anti_dpi` (`_ANTI_DPI_KW` или SNI ≠ host), `is_inactive`;
5. `vless_meta` (локальный `_resolve` обязан восстанавливать socket-timeout), `chain_outbounds`;
6. `outbound_groups` (`SERVICE_GROUP`, `ORPHAN_GROUP`; ключ — `sub_url` из meta, иначе `(pbk, sni)`;
   одиночные → «Без подписки»; `_group_name`, `_days_until`; в DOM ключ группы — `data-pbk`, имя
   историческое);
7. `vless_options` (`is_ru` по `_RU_KW`, виртуальная `direct`), `vless_option_groups`
   (`GROUP_BG_COLORS`, `DIRECT_GROUP_NAME`), `chain_tag_info`, `saved_sub_urls`;
8. `structure`, `geoip_status` (при непустых outbounds, иначе `None`) → `render_template_string` (§8.3).

### 3.7. `HEADER STATUS (v1.7.19)`, подписки, бэкапы

`api_xkeen_header_status` — одна команда с маркерами `===STATUS===`/`===ERROR===`/`===STATE===`, кэш
`_HEADER_STATUS_CACHE` 30 с; **`watchdog.state` здесь разбирается отдельно** (3-й токен).
`api_xkeen_dashboard_health` — LISTEN на **5000 (захардкожено)**. Подписки: `_fetch_subscription`
(HTTP с ПК под видом мобильного клиента; заголовки — идентификатор «устройства» у провайдера, **их
правка плодит устройства и съедает лимит**), `_parse_happ_multiprofile_json`, `_parse_sub_userinfo`,
`_parse_profile_title`, `_normalize_outbound_tag` (`_SHELL_UNSAFE_RE` → `_`), `_uniquify_parsed_tags`
(preview и sync обязаны давать одинаковые теги), `_parse_vless_url_dict`, `_build_vless_outbound_dict`;
preview ничего не пишет (эвристика `_ANTI_DPI_KEYWORDS` намеренно отличается от страницы); sync —
pre-check критических ролей → update/add/remove → `.bak-pre-sync-<ts>` → рестарт с откатом → одна
запись config с чисткой цепочек → обе meta. Orphan — только outbound с `meta.sub_url == url`.
Бэкапы: `XKEEN_BACKUP_FILES`, restore валидирует **до записи** (JSON локально, `*.sh` — `sh -n` на
роутере), фильтр `is_restorable`, `chmod +x` по `needs_exec_bit`, `crontab <file>`,
`keenetic_ensure_killswitch()`, `watchdog.sh && xkeen -restart`. Снапшот v7 — `{version, timestamp,
files: [{path, content}]}`, собирается `jq` на роутере.

### 3.8. Домены, категории, цепочки

Обработчики `set-*` (§4.3) пишут только через `keenetic_write_watchdog_config`. Категории перед
записью проверяет `keenetic_validate_route_categories(names, kind)` (мини-`xray run -test` на роутере +
sentinel `google`/`private`; sentinel не прошёл → fail-open) с текстом `_cat_missing_msg`. IPv6:
`_IPHOST_DOMAIN_RE`, `_FAKE_IP_FOR_V6ONLY`. Цепочки: `CHANNEL_CHAIN_MAX`, `wd_is_ru_tag`
(`_WD_RU_SUBSTR`, `_WD_RU_SUFFIX`, `_WD_RU_WORDS`), `api_xkeen_set_channel_chain` (§7.2).

### 3.9. `POLICY CLIENTS (v1.7.6)`, routing-template, `KEENETIC CONNECTION SETTINGS`, `BOOTSTRAP CLEAN XKEEN`, `TELEGRAM-BOT SETTINGS`

- Клиенты политики: `_parse_ndmc_hosts`, `_parse_ndmc_policies`, `_parse_host_policies`,
  `_parse_lst_file`, `_parse_xkeen_ports` (`show running-config` — таймаут 45 с + RCI-fallback, не
  уменьшать).
- Template: `api_xkeen_routing_template_rules` парсит **template**, не активный routing (плейсхолдеры
  `__[A-Z0-9_]+__` скрываются — регэксп обязан допускать цифры), `_humanize_regex`,
  `_describe_network_rule`; IP эндпоинтов — `_endpoint_ipv4s_from_outbounds` (домены резолвятся на ПК),
  `_template_ip_arrays`, `_template_replace_ip_arrays` (меняются только `"ip": [...]` из IPv4-литералов),
  `_auto_sync_template_ips_silent` (без рестарта; после add/remove/bulk/sync и в sync-structure).
- `api_keenetic_test_connection` строит argv сам (параметры не применяются).
- Bootstrap: `BOOTSTRAP_SEARCH_DIRS`, `_bootstrap_find`, `DEFAULT_WATCHDOG_CONFIG` (обычная
  `'''`-строка, без личных данных), `EMPTY_OUTBOUND_META`, `EMPTY_SUBSCRIPTION_META`,
  `EMPTY_DOMAIN_NOTES`, `DEFAULT_OUTBOUNDS_JSON` (`direct` + `block`), `WATCHDOG_CRON_LINE`,
  `_bootstrap_collect_state`, `_bootstrap_deploy_item`. Порядок ключей: `log_dir`, `watchdog_config`,
  `outbound_meta`, `subscription_meta`, `domain_notes`, `outbounds_defaults`, `routing_template`,
  `watchdog_sh`, `cron_entry`, `killswitch`. `watchdog_sh` перезаписывается всегда,
  `routing_template` — нет (живой template несёт IP эндпоинтов).
- `DEEP_PROBE_LIMITS`, `_deep_probe_settings_from_config`, `_validate_deep_probe_settings` (чистая
  функция → `(updates, error)`), `api_keenetic_tg_test` (сообщение **с роутера** через socks-local).

### 3.10. `УПРАВЛЕНИЕ XKEEN`, `СКАНИРОВАНИЕ … DAT`, `SELF-HEALING / DIAGNOSE / AUTO-REPAIR`

`XKEEN_COMMANDS` — белый список для `api_xkeen_cmd`: `status`, `restart`, `stop`, `restart-clean`
(nohup stop → 15 с → start), `update-xray` (`xkeen -ux`), `update-geofile` (`xkeen -ug`),
`update-xkeen` (`xkeen -uk`); поля `cmd`, `label`, `timeout`, `stdin`, опц. `version_cmd`
(`_format_version_diff`). `V2FLY_KNOWN_CATEGORIES`, `V2FLY_DESC_BY_NAME` — справочник для UI.

`api_xkeen_diagnose` — 13 проверок (`_add(check_id, label, ok, severity, detail, fix_id, fix_label,
fix_explain)`):

| `check_id` | Проверка | `fix_id` |
|---|---|---|
| `xray_binary` | `xray version`, архитектура | — |
| `xkeen_running` | `xkeen -status` | `restart_xkeen` |
| `config_valid` | `xray run -test -confdir` | `regen_routing` |
| `watchdog_version` | watchdog.sh на роутере vs `.cur` | — (догонит «Сохранить») |
| `watchdog_primary` | `PRIMARY_TAG` задан | `set_primary_direct` |
| `outbounds_defaults` | есть `direct` и `block` | `bootstrap_outbounds` |
| `routing_json_valid` | `05_routing.json` — JSON | `regen_routing` |
| `cron_watchdog` | cron-строка watchdog | `bootstrap_cron` |
| `killswitch` | kill-switch политики | `install_killswitch` |
| `watchdog_no_errors` | свежие ERROR/WARN в логе | — |
| `geoip_categories` | `FOREIGN_GEOIP_CATEGORIES` (+ `YT_…`) есть в `geoip.dat` | `install_extended_geoip` |
| `curl_rci_works` | curl + RCI (libnghttp2) | `install_libnghttp2` |
| `entware_tar` | GNU tar для миграции | `install_entware_tar` |

«13 проверок» продублировано текстом в UI (секция «🚑» и `runDiagnose`) — менять вместе.
`XKEEN_AUTO_FIXES` + `api_xkeen_auto_fix`: ветка `install_extended_geoip` — путь 1: если рядом есть
`geoip_v2fly.dat` > 5 МБ, `geoip.dat` становится **симлинком** на него (временное имя + `mv -f`; XKeen
обновляет `geoip_v2fly.dat`, а `geoip.dat` не трогает); путь 2: скачивание в `geoip.dat.new` с проверкой
кода curl и размера. Обычный файл перед заменой → `geoip.dat.bak-<ts>` (хранятся 2), затем
`xkeen -restart`; xray не поднялся или в **новых** строках `error.log` ошибки загрузки → авто-откат.
Тест-SOCKS (`_TEST_SOCKS_FILE`, `_TEST_SOCKS_TTL`): **создание отключено**, работает только удаление. Диагностика сайта —
`api_diagnose_site` (`_rkn_check_urls` + второй проход `_ccffi_requests`), `api_xkeen_iphost` (ndm
`ip host`, без рестарта). В конце Python-части: `logout`, `favicon`, `parse_user_agent`,
`subscription`, `api_tail`.

### 3.11. `TEMPLATES` и `__main__`

`DYNAMIC_TEMPLATE` (`#dynamic` главной), `LOGIN_TEMPLATE`, `TEMPLATE` (главная: NAT-форварды, подписка
+ QR, профили, inbounds, live-tail через `/api/tail` и `/api/state` раз в 5 с), `XKEEN_TEMPLATE` (§8).
Все — `r"""…"""` + `render_template_string`. Общего JS у `TEMPLATE` и `XKEEN_TEMPLATE` нет
(`copyText` объявлен в обоих с разной сигнатурой). `__main__` — печать URL и `app.run(...)`.

---

## 4. Реестр маршрутов → [`docs/architecture/04-routes.md`](architecture/04-routes.md)

Все `@app.route` (91) по группам: метод, функция, что делает, что трогает на роутере (`—` / `R` / `W` / `⟳` /
`WD` / `ndm`). Подразделы:
- 4.1. Страницы, служебные, состояние
- 4.2. Outbound'ы, подписки, мета (все POST, кроме GET `orphan-sub-meta`)
- 4.3. Роли, режим, домены, категории, цепочки (все POST, кроме GET `get-domain-notes`)
- 4.4. Kill-switch, WireGuard, клиенты политики
- 4.5. Template, структура, бэкапы, настройки, CLI, диагностика

## 5. Роутер: файлы, пути, SSH-слой

### 5.1. Что панель читает и пишет

Пути частично из `cfg` (`KEENETIC_XRAY_CONFIGS`, `KEENETIC_XRAY_BAK_DIR`, `KEENETIC_WATCHDOG_STATE`,
`KEENETIC_WATCHDOG_LOG`), частично **захардкожены** (`/opt/etc/xray/watchdog.config`,
`WATCHDOG_REMOTE_PATH`, `TEMPLATE_REMOTE_PATH`, `GEOIP_DAT_PATH`, `xkeen_backup_paths`,
`api_xkeen_header_status`, `keenetic_ensure_socks_route`) — при смене раскладки искать оба варианта.

| Путь | Пишет | Читает / примечание |
|---|---|---|
| `/opt/etc/xray/configs/01_log.json`, `02_dns.json`, `06_policy.json` | restore | снапшот |
| `…/configs/03_inbounds.json` | `keenetic_ensure_socks_local` (`socks-local` :10808); watchdog: `socks-ai-check` :10818, `socks-deep-pri/yt/fgn` :10819–10821 | снапшот |
| `…/configs/04_outbounds.json` | add/remove/sync/bootstrap/auto-fix (`.bak-<ts>`, `.bak-pre-sync-<ts>`) | панель, watchdog |
| `…/configs/05_routing.json` | **только watchdog** (`.new` → проверки → `mv`; `.lastgood`, `.new.bad-<ts>`) | панель (default, DAT) |
| `/opt/etc/xray/configs.bak/05_routing.template.json` | синк IP, `keenetic_ensure_socks_route`, sync-structure, bootstrap (`.bak-*`, `.syncbak-<ts>`) | watchdog (`TEMPL`), панель |
| `…/configs.bak/05_routing.primary.json`, `.failover.json`, `.original-balancer.json`, `07_observatory.disabled.json` | legacy | снапшот |
| `/opt/etc/xray/watchdog.sh` | `keenetic_ensure_watchdog_current` (`.vnew` → `sh -n` → `.bak-<ts>` → `mv`), sync-structure, bootstrap, restore | cron, `_structure_drift_status` |
| `/opt/etc/xray/watchdog.config` | **только** `keenetic_write_watchdog_config`, bootstrap, restore | панель, watchdog, `sub_expiry_notifier.py` |
| `/opt/etc/xray/watchdog.state`, `watchdog.aiprobe` | **только watchdog** | `keenetic_get_watchdog`, header-status |
| `/opt/etc/xray/watchdog.udpstall`, `watchdog.fdstall`, `tg_outbox` | watchdog | панель не читает |
| `/opt/etc/xray/outbound_meta.json`, `subscription_meta.json`, `domain_notes.json` | панель (sidecar; xray не читает) | панель, уведомитель |
| `/opt/etc/xray/dat/*.dat` | `xkeen -ug` (cron; обновляет `geoip_v2fly.dat`, не `geoip.dat`), auto-fix `install_extended_geoip` (`geoip.dat` → симлинк или скачанный файл) | xray, панель (`_list_remote_dat` дедуплицирует симлинки по `readlink -f`) |
| `/opt/var/log/xray/watchdog.log`, `error.log` | watchdog, xray | `keenetic_tail_log(n=30)`, header-status |
| `/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh`, `zz_wg_xray_<iface>.sh`, `zz_wg_lan_masq.sh` | kill-switch, WG (заворот; подмена адреса WG → ЛВС, `MAP` внутри) | Keenetic, снапшот (`zz_*.sh`) |
| `/opt/var/spool/cron/crontabs/root` | bootstrap, auto-fix, kill-switch, WG, restore | watchdog `*/1`, self-heal, `xkeen -ug` |
| `/opt/etc/xkeen/port_proxying.lst`, `port_exclude.lst`, `ip_exclude.lst` | watchdog держит `53` в `port_exclude.lst` | policy-clients, `_ks_read_params` |
| `/opt/etc/dropbear/authorized_keys` | restore | снапшот |
| `/tmp/xkeen_watchdog.lock` | watchdog | анти-перекрытие тиков |
| Конфигурация Keenetic | `ndmc -c "…"` / RCI + `system configuration save` | политики, `ip host`, WG, ACL |

### 5.2. SSH-слой и его ограничения

- **Вызов = процесс ssh = соединение** (~0,5–1 с). Чтения объединяются одной командой с
  ASCII-маркерами (`=AIPROBE=`, `===STATE===`, `###WAN###`, `__WDCFG_ABSENT__`); новое чтение на
  тяжёлой странице — в существующий комбинированный вызов.
- **stdin процесса ssh — всегда свой**: без входных данных `stdin=subprocess.DEVNULL`, с данными —
  `input=`. Иначе `ssh.exe` (Windows OpenSSH) наследует stdin панели, и второй параллельный вызов из
  другого потока висит до таймаута даже после окончания первого (замер: 40 с → 0,4 с). Касается всех
  запусков ssh/scp, включая argv мимо `_ssh_args` (список ниже).
- **Параллельные сессии**: `_PROBE_SSH_LOCK`, ≤ 3 параллельно, пачки по 8 (исторически объяснялось
  обрывами dropbear; часть таких «обрывов» могла быть зависанием из-за общего stdin — до замера
  параллелизм не поднимать).
- **sftp-server нет**: запись через stdin (`cat > файл`, `base64 -d > файл`); `scp` только с `-O`.
  Большие данные в argv не передавать (ARG_MAX BusyBox, буферы dropbear).
- **Таймаут** → `code=-1`, частичный stdout; команда могла выполниться (особенно `xkeen -restart` при
  50+ outbound'ах) — перепроверять состояние. Иное исключение → `code=-2`.
- **Кодировка**: вывод — байты + UTF-8 `errors="replace"`; argv от SYSTEM — cp1251, в командах только
  ASCII. Вывод Keenetic CLI с ANSI — чистить; ошибки `ndmc` искать в stdout (`error[`), код возврата
  часто 0.
- **JSON XKeen с `//`-комментариями** — `keenetic_load_json`/`_strip_json_comments`.
- **Fail-closed**: `keenetic_read_watchdog_config()` → `{}` (файла нет) ≠ `None` (сбой);
  `keenetic_get_outbounds()` → `[]` ≠ `None`. В удалении и записи сбой чтения = отказ
  (`keenetic_get_watchdog_targets(strict=True)`).
- argv мимо `_ssh_args`: `api_keenetic_test_connection`, `api_xkeen_migration_backup`,
  `xkeen_snapshot_daily.py`, `sub_expiry_notifier.py` — опции ssh менять везде.

---

## 6. watchdog → [`docs/architecture/06-watchdog.md`](architecture/06-watchdog.md)

Движок переключений живёт **на роутере** — `/opt/etc/xray/watchdog.sh` (эталон `bootstrap/watchdog.sh.cur`,
BusyBox ash, cron раз в минуту); панель пишет его конфиг, раскатывает версии и показывает состояние.
Подразделы:
- 6.1. Связка
- 6.2. Ключи watchdog.config, которыми управляет панель
- 6.3. watchdog.state — полный перечень полей
- 6.4. Как панель показывает состояние
- 6.5. Автосинк watchdog.sh по номеру версии
- 6.6. Тик watchdog.sh

## 7. Каналы и цепочки резерва → [`docs/architecture/07-chains.md`](architecture/07-chains.md)

Роли (каналы), их цепочки резерва, «общая цепочка» «Зарубежных», где членство в цепочках учитывается
(менять синхронно), что значит «жив», сквозная проба. Подразделы:
- 7.1. Роли
- 7.2. Цепочки резерва каналов
- 7.3. «Общая цепочка» у «Зарубежных» (`FOREIGN_CHAIN_USE_COMMON`)
- 7.4. Где учитывается членство в цепочках (менять синхронно)
- 7.5. Три разных «жив»
- 7.6. Сквозная проба (DEEP_PROBE, watchdog v29+)

## 8. Фронтенд `/xkeen` (`XKEEN_TEMPLATE`)

### 8.1. Компоновка

`<head>`/`<style>` → шапка `<h1>🚦 XKeen` (индикатор xray, версия) → `#flash` → баннер приветствия
(`is_first_run`) → баннер «структура отстала» (`syncStructure(this)`) → разделы `<h2 class="sec-…">` →
скрипт WG-раздела (`initWg`) → «❓ Помощь» → главный `<script>` → `<footer>`.

| Раздел (класс) | Категория | Содержимое |
|---|---|---|
| 🎯 Основное и резервное подключение (`sec-routing`) | Каналы | баннер режима, PRIMARY/FAILOVER, подпункты `data-xk-sub` (🤖 AI, 📺 YouTube, 📱 Зарубежные сервисы, 🌐 Через IPv6, ↪️ Напрямую, ⛔ Блокировать, 👥 Клиенты), ⚡ Принудительно, 🩺 Сквозная проба, 📋 Лог watchdog |
| ⛓️ Цепочка резерва (FAILOVER) (`sec-chain`) | Каналы | `failover-chain` (drag-n-drop) |
| 📋 Все outbounds (`sec-list`), ➕ Добавить (`sec-add`), 📡 Обновить из подписки (`sec-sync`) | Каналы | группы, bulk, форма, preview/sync |
| 💾 Бэкап и восстановление (`sec-backup`), 🔧 Подключение к роутеру (`sec-router`, `section-router-config`), 🛠 Управление XKeen (`sec-xkmgmt`), 🐉 Подключение клиентов WireGuard (`sec-router`) | Обслуживание | снапшоты, SSH и bootstrap, CLI и GeoFile, WG: подпункты сайдбара «➕ Выдать доступ», «📋 Выданные», «📡 На роутере…», «🧩 .conf вручную…» (`.card > details`) + диалог `wgRulesDlg` |
| 🚨 Алерты при падении xray (`sec-xkmgmt`), 🚑 Восстановление и диагностика (`sec-rescue`), 🤖 Telegram-бот для алертов (`sec-tg`) | Диагностика | индикатор/toast, диагностика + auto-fix, TG |
| 🛡 Kill-switch политики XKeen (`sec-killswitch`) | **Прочее** (не ловится `catFor`) | `ks-status` |
| ❓ Помощь (`sec-help`) | Помощь | 21 тема `details.help-section` |

### 8.2. Сайдбар (`buildKeeneticSidebar`)

IIFE в конце главного скрипта перестраивает отрендеренный DOM: начало раздела — прямой потомок
`<body>` (`details.section-collapsible` или `<h2 class="sec-…">`); при < 4 разделах ничего не делает.
Строит `.xk-layout` (`aside.xk-sidebar`, `.xk-resizer` с шириной в `localStorage` `xkSidebarWidth`,
`main.xk-content`); раздел → `.xk-page[data-page]`. В отдельные под-страницы **вырезаются** элементы с
`data-xk-sub` (подпись — значение атрибута), `details.help-section` и
`.card > details:not([data-xk-keep-inline])` (подпись — `<summary>`); их вложенные `<details>` (2–8)
становятся под-под-пунктами. `data-xk-group` поддерживается, но не используется. `catFor(title)` —
категория по регэкспу заголовка (переименование `<h2>` может перенести раздел). `showPage(i)`
запоминает `xkActivePage`. `window.__xkActivatePageForEl(el)` — мост для внешних ссылок (им пользуется
`openHelpAnchor`). Поиск — `filterNav` + глубокий индекс `h2/h3/h4/summary/[data-xk-sub]`.

**Новый `<details>` прямо в `.card` станет отдельным пунктом сайдбара**, если на нём нет
`data-xk-keep-inline` (он есть у `chain-box` и «🩺 Сквозная проба»; у «📋 Лог watchdog» — нет).

### 8.3. Переменные `render_template_string` для `/xkeen` (23)

`outbounds`, `chain_outbounds`, `outbound_groups`, `routing`, `watchdog` (§6.4), `targets` (§6.2),
`vless_tags`, `vless_options`, `vless_option_groups`, `anti_dpi_tags_set`, `anti_dpi_tags_json`,
`vless_meta_json`, `saved_sub_urls`, `username`, `keenetic_settings`, `is_first_run`, `is_client_only`,
`dashboard_version`, `domain_notes_json`, `cfg`, `structure`, `geoip_status` (секция «🌍
GeoIP-категории»: неизвестно / расширенная и связанная с `geoip_v2fly.dat` / расширенная отдельным
файлом / без `telegram` — баннер с кнопкой `installExtendedGeoip`), `chain_tag_info`.

### 8.4. JS-константы из шаблона

`|tojson` (на Jinja `Undefined` → 500; источник обязан быть в quick-fail): `WATCHDOG_CURRENT_DEFAULT`,
`WATCHDOG_PRIMARY_TAG` (`targets.primary_tag`), `WATCHDOG_STATE`, `WATCHDOG_LAST_BAD_MD5`,
`WATCHDOG_EFFECTIVE_AI`/`_YT`/`_FOREIGN`, `WATCHDOG_AI_PROBE`, `AI_FAIL_BLOCK`/`YT_FAIL_BLOCK`/
`FOREIGN_FAIL_BLOCK` (`targets.*_fail_block`), `WATCHDOG_DEEP` (`watchdog.deep`),
`WATCHDOG_FAILOVER_TAGS`, `DEEP_PROBE_THRESHOLD`, `CHAIN_TAG_INFO`, `CHAIN_STATE.ai/yt/foreign`
(`tags`, `mainTag`, `useCommon`; плюс `label`, `effective`, `mainSel`). `|safe` (готовый JSON):
`VLESS_META`, `ANTI_DPI_TAGS` (`new Set(...)`), `WINDOW_DOMAIN_NOTES`. Клиентские: `DOMAIN_SECTIONS`,
`AI_PRESETS`, `YT_PRESETS`, `FOREIGN_PRESETS`, `IPV6_PRESETS`, `DIRECT_PRESETS`, `BLOCK_PRESETS` (это
`const`, не свойства `window` — доступ через getter'ы `DOMAIN_SECTIONS`), `CHAIN_MAX`,
`_LATENCY_ROLES`, `_CARDS_THRESHOLD`, `RKN_VERDICTS`, `RKN_CONF`.

### 8.5. Общие утилиты и ключевые функции

- Общие: `flash(ok, message, details, options)` (`message` — HTML; `details` экранируется),
  `apiCall(url, formData)`, `escapeHtml`, `openHelpAnchor(id)`, `renderOutboundInfo(divId, tag, opts)`
  (`opts.activeNote` — **доверенный HTML**), `syncSelectColor`.
- Каналы: `getChannelStatusNote`, `deepProbeNote`, `aiThroughProbeNote`, `makeProbeBtn`,
  `refreshPrimaryInfo`, `refreshFailoverInfo`, `refreshAIInfo`, `refreshYTInfo`, `refreshForeignInfo`,
  `refreshActiveInfo` (+ `refreshAIActiveInfo`, `refreshYTActiveInfo`, `refreshForeignActiveInfo`),
  `renderDeepProbeSummary`, `applyStatusesToMeta`, `setTarget`, `setRole`, `forceMode`.
- Цепочки: `chainPositionOf`, `chainUseCommon`, `chainRender`, `chainRenderCommonView`, `chainAdd`,
  `chainMove`, `chainRemove`, `saveChannelChain`, `removeFromChannelChain`.
- Домены: `_extCatAdd`, `_saveExtCategories`, `addPresetGeneric`, `removePresetGeneric`,
  `autoMatchPresets`, `injectSectionControls`.
- Обслуживание: `xkeenCmd`, `runDiagnose`, `applyFixByIdx`, `installExtendedGeoip`, `ksRefresh`,
  `tgSave`, `tgTest`, `repairTgChannel`, `saveDeepProbeSettings`, `syncStructure`, `bootstrapApply`.
- WG (отдельный `<script>` после раздела, IIFE `initWg`): `loadClients` (`wg-clients`), `issue`,
  `revoke`, `loadRouter` (`wg-router`), `xrayToggle`, `renderOrphans`/`cleanupOrphan`, `openRules`/
  `applyRules` (диалог `wgRulesDlg` переносится в `<body>`: базовая страница раздела скрыта, пока открыт
  подпункт; `warnings` формы — блок «⚠ При применении»), `backupDl` (бэкап запросом: ошибка — во `flash`, а не уход со страницы с показанным `.conf`),
  ручной генератор `loadIfaces`/`gen`. Чтение роутера — только когда блок показан
  (`onVisible`: `offsetParent !== null` + проверка по клику). «Выдать доступ» активна только при вычисленном
  слоте и известной ЛВС (`setIssueReady`); не прочитался роутер — `loadClients` сам повторяет 2 раза через 4 с
  (`WG.retry`; сразу после рестарта панели первое чтение тонет в залпе запросов страницы), потом «🔄 Повторить».
- Шапка/UX: `refreshXrayHeaderStatus` (60 с), `refreshDashboardHealth` (15 с), `injectSelectorCounters`,
  `injectLatencyAndCards`, `_convertSelectorsToCards`, `_xkRemindAssignRole`.

Главный `DOMContentLoaded` (у редактора цепочек): `refreshPrimaryInfo` → `renderDeepProbeSummary` →
`refreshFailoverInfo` → AI/YT/Foreign `refresh*Info` + `refresh*ActiveInfo` → `chainRender` ×3 →
индикаторы. `CHAIN_STATE` объявлен `const` ниже читающих его функций — до инициализации ReferenceError
(TDZ), поэтому в `chainPositionOf`/`chainUseCommon` стоит `try/catch`.

**Справка**: `details.help-section` (21 тема). Якоря — `id` на `<details>`: `help-channels`,
`help-watchdog`, `help-tg-alerts`, `help-scenarios` (внутри `help-yt-recaptcha`), `help-v2fly-geoip`,
`help-ipv6-leak`, `help-dns-block`, `help-foreign`, `help-killswitch` (kill-switch **политики**),
`help-wg-client`. Ссылаться через `openHelpAnchor('help-x')` можно только на тему с `id`.

### 8.6. Как добавить новый блок или карточку

1. **Данные**: в существующую функцию чтения или отдельной переменной (как `geoip_status`) + аргумент
   `render_template_string` + явный дефолт в quick-fail.
2. **Разметка**: пункт сайдбара — обёртка с `data-xk-sub="Эмодзи Название"` или `<details>` в `.card`;
   инлайн — `<details … data-xk-keep-inline>`; новый раздел — `<details class="section-collapsible">
   <summary><h2 class="sec-…">…</h2></summary><div class="card">…</div></details>` на уровне `<body>`
   (проверить категорию в `catFor`).
3. **id** — по схемам (`select-<role>`, `info-<role>`, `<kind>-chain-list`/`-add`/`-allow-ru`): JS
   собирает их конкатенацией.
4. **JS**: константы — рядом с `WATCHDOG_*` через `|tojson`; функции — в главный `<script>`; вызов — в
   `DOMContentLoaded`, при зависимости от статусов — и в `applyStatusesToMeta`.
5. **Сохранение**: для `watchdog.config` — только `keenetic_write_watchdog_config` одной записью; ответ
   `{ok, stdout/stderr}` → `flash` → `location.reload()` при необходимости.
6. **Справка**: тема с `id` и ссылка «❓ помощь». Затем §9, §10 и этот документ.

---

## 9. 🔴 Правила правок (грабли)

### 9.1. JS/HTML внутри Python raw-строки

Шаблоны — `r"""…"""` + Jinja2: текст уходит в браузер **как написан**.
- **Никаких `"""`** внутри шаблона.
- **Обратный слэш + апостроф — одним слэшем** (`\'`); привычное `\\'` даст в JS `\\` + закрывающий
  апостроф → SyntaxError, и весь главный `<script>` не выполнится.
- **Кириллица с апострофом** (`kill-switch'ем`, `outbound'ов`) в JS-строке с одинарными кавычками
  рвёт литерал → backtick-литерал, `&#39;`/`&quot;` в атрибутах или без апострофа.
- **Без «умных» кавычек** `‘ ’ “ ”`.
- **onclick с аргументами** из JS — через `&quot;`: `'onclick="chainMove(&quot;' + kind + '&quot;, ' +
  i + ', -1)"'`.
- **Jinja интерпретирует `{{`, `{%`, `{#`** везде, включая JS и CSS.
- **`escapeHtml` — к листовым значениям** (тег, страна, текст с роутера), не к готовому фрагменту.
  `activeNote` и `message` у `flash` не экранируются.
- Тексты UI — безличные: инструкция инфинитивом («нажать», «открыть»), описание в третьем лице; без «ты» и
  без «вы» (в том числе в сообщениях об ошибках `wg_clients.py` и спутников). Сторож — `tests/test_ui_impersonal.py`.

### 9.2. Quick-fail литералы `xkeen_page`

Без роутера `watchdog` и `targets` собираются литералами. Ключ, который шаблон читает через `|tojson`,
`{% for %}`, `.index()`, `|length`, обязан там быть — иначе 500 (`UndefinedError`) только при
выключенном роутере, при обычной проверке незаметно. **Чек-лист «Новое поле в шаблон»**: return
функции → тот же ключ в литерале → JS-константа при необходимости → рендер обоими путями (§10.2).
`tests/test_v29_ui_offline.py` проверяет, что ключи quick-fail ⊇ ключей функций.

### 9.3. watchdog.config

- **Чек-лист «Новый ключ»**: `keys_order` → дефолт в `keenetic_write_watchdog_config` →
  `DEFAULT_WATCHDOG_CONFIG` (комментарий **отдельной строкой**) → `keenetic_get_watchdog_targets` +
  quick-fail → `${X:-…}` в `.cur` + версия → §6.2. `tests/test_api_offline.py` сверяет
  `keys_order ⊆ DEFAULT_WATCHDOG_CONFIG` регэкспом по литералу `keys_order = [` — переименование
  переменной молча выключит проверку.
- **Числа — только числом** (`X="2"`): пустое или комментарий в кавычках → `[ "$X" -ge … ]` в ash →
  «bad number», тик ломается. Флаги (`*_CHAIN_ALLOW_RU`, `FOREIGN_CHAIN_USE_COMMON`, `DEEP_PROBE_*`)
  писать явно `"0"`/`"1"`: пустое watchdog трактует как свой дефолт, и галочка UI разъедется.
  Известные расхождения: `targets.yt_fail_block` без ключа `True` при дефолте watchdog `0`;
  `AI_FAIL_BLOCK=""` UI покажет выключенным, watchdog применит `1`.
- Запись — только `keenetic_write_watchdog_config`, одним вызовом; fail-closed не обходить; значения
  экранирует `_esc` — строки config руками не собирать.
- Теги без пробелов и shell-метасимволов (`_normalize_outbound_tag`): цепочки — «теги через пробел».
- `is_ru_tag()` (`.cur`) и `wd_is_ru_tag()` менять только вместе.

### 9.4. Роутер и xray

- **`xray run -test` — всегда с `XRAY_LOCATION_ASSET=/opt/etc/xray/dat`**, иначе категории не найдутся.
- **Гонка `xray -test` с `xkeen -restart`**: cron-тик может перезапускать xkeen (`S05xkeen` делает
  `killall xray` → тест убит → ложный RC=137 → ложный откат). Ждать тишины, повторять при
  RC=137/«Killed» — как в `keenetic_sync_structure`.
- Категория, которой нет в `.dat`, роняет весь xray — защита двойная (`keenetic_validate_route_categories`
  + `validate_routing_xray`), не убирать ни одну.
- `xkeen -restart` может обнажить скрытую поломку (нет libnghttp2 → xray не поднимется): рестарт только
  где нужен, по возможности с pre-check (`api_xkeen_sync_template_ips`).
- Мутации `04_outbounds.json`: бэкап → stdin → рестарт → проверка процесса → откат; удаление — вместе с
  очисткой `*_FAILOVER_TAGS` (§7.4).
- **BusyBox**: нет `stat -c` (`wc -c <`), `date -r` есть, `strings` может не быть (`grep -a` по
  бинарю), коды категорий в `.dat` ЗАГЛАВНЫЕ, `\b` в grep ненадёжен, `tar` урезанный. Роутер медленный
  (MIPS): grep по 16 МБ `.dat` ~9 с — тяжёлые проверки не делать на каждый рендер, а в фоне с кэшем
  (образец — `_geoip_tg_lookup`).

### 9.5. Файлы, кодировки, прод

- `.gitattributes`: `*.sh`, `*.sh.cur`, `*.json`, `*.json.cur`, `*.py`, `*.md` — **LF**; `*.bat`, `*.ps1`
  — **CRLF**. Шелл-скрипт с CRLF на роутере не запустится.
- **`.ps1` — только ASCII** (PowerShell 5.1 читает в ANSI-кодировке); кириллица — во внешних UTF-8
  файлах (как `release_notes_footer.md`). `.bat` — тоже ASCII.
- Правка в боевой папке = правка прода (§1, §6.5). `api_xkeen_dashboard_health` (порт 5000) и
  `api_xkeen_restart_dashboard` (задача `XrayDashboard`) захардкожены. Спутники не видят
  `runtime_settings.json`. Свой argv ssh — только с `ssh_bin.ssh_exe()`/`ssh_bin.null_known_hosts()`.

---

## 10. Проверка изменений: четыре слоя

Без роутера; интерпретатор — `.venv\Scripts\python.exe` из папки установки.

**10.1. Синтаксис и grep-гейты.** `python -m py_compile dashboard.py`. Гейты по добавленным строкам
(`git diff -U0 dashboard.py`): `"""` внутри шаблона; `\\'` (двойной слэш перед апострофом); умные кавычки; кириллица с
апострофом в одинарных кавычках (регэксп вида `= '[^']*[а-яё]'[а-яё]`). Правка `.cur` — `sh -n` и
заголовок версии.

**10.2. Офлайн-рендер `test_client` двумя путями.** `import dashboard as D`, `D.app.test_client()`,
`logged_in` через `session_transaction()`: (1) quick-fail — `D.is_keenetic_reachable` → False;
(2) моки — `D.is_keenetic_reachable` → True, `D.keenetic_ssh` отвечает по подстрокам команды
(`04_outbounds.json`, `watchdog.config`, `watchdog.state` с `=AIPROBE=`, лог, meta, `head -40
…watchdog.sh` с текущей версией — иначе баннер дрейфа), при необходимости
`D.keenetic_ensure_watchdog_current` → `{"action": "current"}`. Ожидать 200 без «Ошибка при
рендеринге» и новые элементы в HTML. Образцы — `tests/test_api_offline.py` (`fake_ssh`, `ROUTER`) и
`tests/test_v29_ui_offline.py`.

**10.3. Тесты `tests/`** — скрипты без pytest (печатают `<имя>: OK`, при провале код 1); запуск по одному:
`.venv\Scripts\python.exe tests\<файл>.py` (из POSIX-оболочки — с `PYTHONUTF8=1`).

| Тест | Покрывает |
|---|---|
| `test_api_offline.py` | kill-switch, backup/restore, bootstrap, diagnose + auto-fix, 404/405 ≠ 500, `keys_order ⊆ DEFAULT_WATCHDOG_CONFIG` |
| `test_backup_paths.py` | `STATIC_FILES`, `is_restorable`, `needs_exec_bit`, `build_snapshot_command` |
| `test_geoip_status.py` | `keenetic_geoip_dat_status` (+ фоновая проверка `telegram`), все состояния секции GeoIP, auto-fix `install_extended_geoip` (симлинк, скачивание, авто-откат, ротация бэкапов), категории в диагностике |
| `test_killswitch.py` | генератор хука, разбор статуса/утечек |
| `test_deep_quarantine.py` | карантин возврата из `.cur` в `sh` с подменными curl/date (v33): `_deep_hold` (удвоение, потолок), `deep_probe_check` — проверка основного через минуту после возврата, сброс `D*_QN` успехом только на основном, глухота после двух пустых проверок, state v32 без новых полей (нет `sh` — SKIP) |
| `test_probe_tag.py` | `probe_tag`/`_probe_connected` из `.cur` в `sh` с подменным curl: таймаут при установленном TCP = жив, без соединения = мёртв (нет `sh` — SKIP) |
| `test_template_ips.py` | `.cur`-template (версия, два IPv4-массива), синк IP эндпоинтов |
| `test_wg_clients_offline.py` | выдача WG на имитации Keenetic (RCI, ACL, NAT, маршруты, save, хуки): режимы, ЛВС и подмена адреса, ручной путь, s2s, линки, отзыв (метка save, возврат NAT/маршрута, чужие пиры, заворот), форма правил созданного вручную (`prefill_from_acl`: пресеты, диапазоны в обоих форматах RCI, предупреждения, свой .1 в xray, обратимость), гонка за индекс, откат, битый/занятый реестр, X25519 = RFC 7748, рендер `/xkeen` |
| `test_csrf_offline.py` | `_csrf_origin_guard`: чужой Origin/Referer/`null`/другой порт → 403 до обработчика, свой хост, без заголовков и GET — проходят, `TRUSTED_ORIGINS`, `SameSite=Lax` |
| `test_ui_impersonal.py` | тексты интерфейса и сообщений без обращения на «ты»/«вы»: местоимения, повелительные формы, 2-е лицо (`-ешь`/`-ишь`) во всех строках, кроме комментариев Python |
| `test_v29_ui_offline.py` | цепочка «Зарубежных» (`allow_ru`/`use_common`), `watchdog-settings`, `fields`/`deep`, quick-fail ⊇ ключей, рендер обоими путями |

Новая функциональность = новые проверки по тому же образцу (подмена `D.*` **до** создания `test_client`).

**10.4. Реальный браузер на временном инстансе.** Отдельный процесс на свободном порту (не боевом) с
моками из 10.2 и авто-логином (функция в `D.app.before_request_funcs`, ставящая
`session["logged_in"]`), `D.app.run(host="127.0.0.1", port=<свободный>, debug=False, use_reloader=False)`;
headless Chromium (Playwright из `.venv`): 0 `pageerror` и ошибок консоли, построен
`aside.xk-sidebar`, блок на нужной странице, JS-отрисовка и переключатели работают, `openHelpAnchor`
открывает цель. Харнессы в репозиторий не входят. **Не проверять UI на панели с боевым роутером**:
«Сохранить» там пишет на роутер и раскатывает `watchdog.sh`.

---

## 11. Релиз

`release.ps1 -Version X.Y.Z -Message "…"` (PowerShell, папка установки; пушит сразу):
1. Формат `X.Y.Z`; тега `vX.Y.Z` ещё нет.
2. `[1/7]` регэкспом `_VERSION_FALLBACK = "…"` → новая версия (UTF-8 без BOM).
3. `[2/7]` `py_compile`; ошибка → `git checkout -- dashboard.py`, выход.
4. `[3/7]` `git add -A` (**уходит всё незакоммиченное** — сначала `git status`) + sanitize staged diff
   по шаблону личных данных (ловит лишь несколько шаблонов).
5. Сообщение `vX.Y.Z: <Message>` и notes (`<Message>` + `release_notes_footer.md`) — во временные UTF-8
   файлы (PowerShell 5.1 портит кириллицу в аргументах).
6. `[4/7]` commit `-F`, `[5/7]` `tag -a`, `[6/7]` push `main` и тега, `[7/7]` `gh release create`
   (репозиторий захардкожен); workflow `release-archives.yml` прикладывает архивы.

`_VERSION_FALLBACK` — единственный источник версии для ZIP, portable и SYSTEM без git; бампать после
§10. `-Message` уходит в коммит, тег и Release — стерильно, одним абзацем о том, что изменилось для
пользователя. Изменения на роутере пользователь применяет через «🔬 Диагностика» → «Сохранить» /
«Починить» или «⚙ Применить структуру».

🔴 **Git identity — репозиторий публичный.** Перед коммитом и пушем:

```
git config user.email                 # обязан быть <github-user>@users.noreply.github.com
git log -1 --format='%an <%ae>'       # под кем последний коммит
```

Локальный identity молча теряется при пере-клонировании, `filter-repo`, `reset --hard` — git берёт
глобальный профиль; `release.ps1` это **не проверяет**. Если
не noreply — до коммита `git config user.name <github-user>` и
`git config user.email <github-user>@users.noreply.github.com`.

---

## 12. Как обновлять этот документ

- **В том же коммите, что и код**: маршрут, ключ config, поле state, переменная шаблона, JS-константа,
  раздел UI, тест, файл, шаг релиза. Новая версия `watchdog.sh.cur` → §6 и строка в журнале.
- **По идентификаторам, не по строкам**; переименование — `grep` по документу.
- Репозиторий публичный: никаких личных адресов, доменов, имён, путей личных машин, токенов, chat id.
- Проверка: каждый идентификатор существует (`grep -n` по `dashboard.py`, `.py`, `.cur`); счётчики
  (маршруты, проверки, темы справки, переменные шаблона) пересчитаны.
- Объём главного файла держать ≤ ~80 КБ (его читают первым целиком): детали — в коде и docstring'ах, здесь —
  карта и правила. Справочник, который разросся, — отдельным файлом в `docs/architecture/` с тем же номером §
  и заглушкой здесь (заголовок, суть в 2–3 строки, ссылка, список подразделов); заглушку обновлять вместе с ним.
- Строку в журнал — в `docs/architecture/changelog.md`.

### Журнал изменений документа → [`docs/architecture/changelog.md`](architecture/changelog.md)

Строка на каждую правку документа (дата, что изменилось) — таблица там.
