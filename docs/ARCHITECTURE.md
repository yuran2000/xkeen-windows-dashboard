# Архитектура xray-dashboard (панель управления XKeen)

> **Читать первым** перед любой правкой панели: устройство `dashboard.py` (~22 тыс. строк) и куда
> идти для правки — без повторной разведки кода. Ссылки на код — **только по идентификаторам**
> (функция, маршрут, константа, `id`, маркер раздела `# ============== ИМЯ ==============`); номеров
> строк нет — файл живой. Переход: `grep -n "def keenetic_ssh" dashboard.py`. Документ обновляется
> **в том же коммите**, что и код (§12).

Обозначения: `<install-dir>` — папка установки (обычно `C:\xray-dashboard`), `<router-ip>` — роутер
Keenetic, `<vpn-domain>` — `cfg.EXTERNAL_DOMAIN`, `<home-domain>` — `cfg.HOME_DOMAIN`,
`<github-user>` — аккаунт GitHub автора коммитов.

## Шпаргалка: куда идти

| Задача | Где |
|---|---|
| Что делает кнопка UI | `onclick` → JS-функция главного `<script>` `XKEEN_TEMPLATE` → `fetch('/api/…')` → §4 |
| Новый ключ `watchdog.config` | §6.2 + чек-лист §9.3 |
| Показать поле `watchdog.state` | §6.3, §6.4 + чек-лист §9.2 |
| Логика переключения каналов | `bootstrap/watchdog.sh.cur`, не Python (§6.6); поднять версию (§6.5) |
| Новая роль / цепочка | §7.1, §7.4 |
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
| `docs/*.png`, `docs/ARCHITECTURE.md` | скриншоты README; этот документ |
| `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `THIRD-PARTY-NOTICES.md`, `LICENSE` | публичная документация («Архитектура» в `CONTRIBUTING.md` устарела) |
| `.gitattributes`, `.gitignore`, `icons8-favicon-64.png` | переводы строк (§9.5), исключения, иконка |

Вне git: `config_local.py`, `config.ini`, `runtime_settings.json`, `backups/` (снапшоты, `dat_cache/`,
`entware_backup_*.tar.gz`), `*.log`, `.ssh/`, `.venv/`, `*.bak-*`.

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
`_WATCHDOG_CFG_LOCK`, `app`, `PERMANENT_SESSION_LIFETIME` (`cfg.SESSION_DAYS`), `SUBS_LOG`.
`requires_auth` — на каждом маршруте (`before_request` не используется). `collect_dynamic_data`,
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
перезатираются).

### 3.4. `KILL-SWITCH ПОЛИТИКИ XKEEN (v1.1.0)` и соседи

Защита от утечки клиентов политики XKeen напрямую в окне `xkeen -restart`. Генератор —
`xkeen_killswitch.py` (`HOOK_PATH`, `CHAIN` = `xkeen_ks`, `MODES`: `protect` — DROP по connmark
политики, `observe` — только счёт; `build_hook`, `install_command`, `remove_command`, `parse_status`,
`parse_leaks`). В панели: `_KS_LOCK`, `_ks_read_params()` (метка политики по RCI `show/ip/policy`,
порты, WAN), `keenetic_killswitch_status()` (`absent`/`broken`/`observe`/`protect`, дрейф, утечки),
`keenetic_ensure_killswitch(mode, force)` (идемпотентно, маркер `KS_INSTALL_OK`). Дальше без маркера:
`api_wg_new_client` (`WG_EXE_PATHS`), `_keenetic_rci_post` (POST parse-команд ndm), ACL WG
(`wg_apply_acl`: отвязать → удалить → создать → привязать → перечитать → `system configuration
save`; сбой посередине = fail-closed), DAT-кэш (`_fetch_dat_via_ssh`, `_list_remote_dat`, `_get_dat_cache` → `<base>/backups/dat_cache`), anti-bot
(`_chrome_fp_probe`), sidecar-файлы (`keenetic_read_domain_notes`, `keenetic_write_domain_notes`,
`keenetic_read_sub_meta`, `keenetic_write_sub_meta`, `keenetic_set_sub_meta` — дата по ключу-`sub_url`
копируется во все pbk-«тени» подписки).

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

## 4. Реестр маршрутов

87 декораторов `@app.route` на 86 функциях; все, кроме «без auth», под `requires_auth`. «Роутер»:
`—` не трогает; `R` чтение по SSH; `W: …` запись (бэкап `.bak-<ts>`, если не оговорено); `⟳` явный
`xkeen -restart`; `WD` — `keenetic_write_watchdog_config` (config + внеочередной `watchdog.sh`,
который может перегенерировать routing и перезапустить xkeen, + авто-обновление `watchdog.sh`);
`ndm` — `ndmc`/RCI + `system configuration save`.

### 4.1. Страницы, служебные, состояние

| Путь | Методы | Функция | Что делает | Роутер |
|---|---|---|---|---|
| `/login`, `/logout` | GET,POST; GET | `login`, `logout` | вход/выход (без auth) | — |
| `/favicon.ico`, `/favicon.png` | GET | `favicon` | иконка (без auth) | — |
| `/` | GET | `index` | мониторинг локального xray | — |
| `/api/state` | GET | `api_state` | фрагмент `#dynamic` | — |
| `/api/tail` | GET | `api_tail` | `?log=access/error&n=` | — |
| `/sub/<token>` | GET | `subscription` | base64 от `cfg.PROFILES[*].url` + `Profile-*`/`Subscription-Userinfo`; токен = пароль (без auth) | — |
| `/xkeen` | GET | `xkeen_page` | главный экран (§3.6) | R (~11 SSH) |
| `/api/xkeen/state` | GET | `api_xkeen_state` | `{outbounds, routing, watchdog}` | R |
| `/api/xkeen/debug` | GET | `api_xkeen_debug` | сырой/разобранный state и config | R |
| `/api/xkeen/header-status` | GET | `api_xkeen_header_status` | индикатор шапки, кэш 30 с | R |
| `/api/xkeen/probe-status` | GET,POST | `api_xkeen_probe_status` | handshake-проба, `?force=1` (по умолчанию) / `0` — кэш | R |
| `/api/xkeen/dashboard-health` | GET | `api_xkeen_dashboard_health` | LISTEN на :5000 | — |
| `/api/xkeen/restart-dashboard` | POST | `api_xkeen_restart_dashboard` | перезапуск задачи | — |

### 4.2. Outbound'ы, подписки, мета (все POST, кроме GET `orphan-sub-meta`)

| Путь | Функция | Параметры | Роутер |
|---|---|---|---|
| `/api/xkeen/add` | `api_xkeen_add` | `payload` (vless:// или JSON), `tag`, `overwrite`, `sub_url`, `note` | W: `04_outbounds.json` ⟳ откат, `outbound_meta.json`, IP в template |
| `/api/xkeen/remove` | `api_xkeen_remove` | `tag` | то же |
| `/api/xkeen/remove-bulk` | `api_xkeen_remove_bulk` | `tags` (одна транзакция, `skipped`) | то же |
| `/api/xkeen/remove-subscription` | `api_xkeen_remove_subscription` | `key` (sub_url или pbk) | WD, W: `04_outbounds.json` ⟳, `subscription_meta.json` |
| `/api/xkeen/subscription-preview` | `api_xkeen_subscription_preview` | `url` (ничего не пишет) | R + HTTP с ПК |
| `/api/xkeen/subscription-sync` | `api_xkeen_subscription_sync` | `url`, `add_new`, `remove_orphans`, `update_expires`, `overwrite_expires`, `add_tags` | W: `04_outbounds.json` ⟳ откат, WD, обе meta |
| `/api/xkeen/meta` | `api_xkeen_meta` | `tag` + имя/заметка | W: `outbound_meta.json` (без бэкапа) |
| `/api/xkeen/sub-meta` | `api_xkeen_sub_meta` | `pbk` (историческое имя ключа группы), `name`, `note`, `expires_at`, `sub_url` | W: `subscription_meta.json` (без бэкапа) |
| `/api/xkeen/orphan-sub-meta` | `api_xkeen_orphan_sub_meta` | — | R |
| `/api/xkeen/remove-orphan-sub-meta` | `api_xkeen_remove_orphan_sub_meta` | — | W: `subscription_meta.json` |

### 4.3. Роли, режим, домены, категории, цепочки (все POST, кроме GET `get-domain-notes`)

| Путь | Функция | Параметры → ключ config | Роутер |
|---|---|---|---|
| `/api/xkeen/set-target/<role>` | `api_xkeen_set_target` | role primary/failover/ai/yt/foreign/ipv6, `tag` (пусто снимает ai/yt/foreign/ipv6) | WD |
| `/api/xkeen/watchdog/<mode>` | `api_xkeen_watchdog` | primary/failover/auto → `FORCE_MODE` | WD |
| `/api/xkeen/set-failover-chain` | `api_xkeen_set_failover_chain` | `tags` через запятую (≥ 1) → `FAILOVER_TAGS` | WD |
| `/api/xkeen/set-channel-chain` | `api_xkeen_set_channel_chain` | `kind` ai/yt/foreign, `tags`, опц. `allow_ru`, `use_common` (foreign) | R + WD |
| `/api/xkeen/set-{direct,block,ai,yt,foreign}-domains` | `api_xkeen_set_{direct,block,ai,yt,foreign}_domains` | `domains` → `*_DOMAINS` | WD |
| `/api/xkeen/set-ipv6-domains` | `api_xkeen_set_ipv6_domains` | `domains` или `action=add/remove` + `domain` | WD + ndm (`ip host <d> 198.18.0.1`) |
| `/api/xkeen/set-{ai,yt,foreign}-fail-block` | `api_xkeen_set_{ai,yt,foreign}_fail_block` | `enabled=1/0` → `*_FAIL_BLOCK` | WD |
| `/api/xkeen/set-{ai,yt,foreign}-ext-categories` | `api_xkeen_set_{ai,yt,foreign}_ext_categories` | `categories` → `*_EXT_CATEGORIES` (проверка geosite) | R + WD |
| `/api/xkeen/set-foreign-geoip-categories`, `set-yt-geoip-categories` (совместимость) | `api_xkeen_set_foreign_geoip_categories`, `api_xkeen_set_yt_geoip_categories` | `categories` → `*_GEOIP_CATEGORIES` (проверка geoip) | R + WD |
| `/api/xkeen/set-ai-ip-ranges` | `api_xkeen_set_ai_ip_ranges` | `ranges` (CIDR) → `AI_IP_RANGES` | WD |
| `/api/xkeen/get-domain-notes`, `set-domain-notes` | `api_xkeen_get_domain_notes`, `api_xkeen_set_domain_notes` | примечания к доменам по каналам | R / W: `domain_notes.json` |

### 4.4. Kill-switch, WireGuard, клиенты политики

| Путь | Методы | Функция | Что делает | Роутер |
|---|---|---|---|---|
| `/api/xkeen/killswitch-status` | GET | `api_xkeen_killswitch_status` | состояние, утечки; `?heal=1` | R (W при heal) |
| `/api/xkeen/killswitch-preview` | GET | `api_xkeen_killswitch_preview` | текст хука | R |
| `/api/xkeen/killswitch-apply` | POST | `api_xkeen_killswitch_apply` | `mode` protect/observe | W: хук + cron |
| `/api/xkeen/killswitch-remove` | POST | `api_xkeen_killswitch_remove` | снять всё | W |
| `/api/xkeen/wg-interfaces` | GET | `api_wg_interfaces` | WG-интерфейсы, WAN, LAN-IP | R |
| `/api/xkeen/wg-client-conf` | POST | `api_wg_client_conf` | текст `.conf` | R |
| `/api/xkeen/wg-new-client` | POST | `api_wg_new_client` | ключи `wg.exe`, интерфейс + peer | ndm |
| `/api/xkeen/wg-connect`, `wg-disconnect`, `wg-cleanup-orphan` | POST | `api_wg_connect`, `api_wg_disconnect`, `api_wg_cleanup_orphan` | хук `zz_wg_xray_<iface>.sh` + cron self-heal | W: netfilter.d, crontab |
| `/api/xkeen/wg-acl`, `wg-acl-apply` | GET; POST | `api_wg_acl_get`, `api_wg_acl_apply` | ACL интерфейса | R / ndm |
| `/api/xkeen/policy-clients` | GET | `api_xkeen_policy_clients` | клиенты, политики, порты, `.lst` | R |
| `/api/xkeen/set-client-policy` | POST | `api_xkeen_set_client_policy` | `mac`, `policy` | ndm |
| `/api/xkeen/flush-conntrack` | POST | `api_xkeen_flush_conntrack` | `ip` | `conntrack -D` или ndm |
| `/api/xkeen/install-conntrack-tools` | POST | `api_xkeen_install_conntrack_tools` | opkg | W |

### 4.5. Template, структура, бэкапы, настройки, CLI, диагностика

| Путь | Методы | Функция | Что делает | Роутер |
|---|---|---|---|---|
| `/api/xkeen/routing-template-rules` | GET | `api_xkeen_routing_template_rules` | базовые правила template | R |
| `/api/xkeen/sync-template-ips` | POST | `api_xkeen_sync_template_ips` | preview; `apply=1`; `restart=1` | W: template; ⟳ при `restart=1` |
| `/api/keenetic/sync-structure` | POST | `api_keenetic_sync_structure` | «⚙ Применить структуру» | W ⟳ откат |
| `/api/keenetic/bootstrap-preview` | GET | `api_keenetic_bootstrap_preview` | план развёртывания | R |
| `/api/keenetic/bootstrap-apply` | POST | `api_keenetic_bootstrap_apply` | `{keys, overwrite_existing}` | W, прогон watchdog, ⟳ |
| `/api/xkeen/backup` | GET | `api_xkeen_backup` | снапшот v7 файлом | R |
| `/api/xkeen/backup-to-disk` | POST | `api_xkeen_backup_to_disk` | снапшот в `<base>/backups/` | R |
| `/api/xkeen/migration-backup` | POST | `api_xkeen_migration_backup` | tar `/opt` → `scp -O` | W: временный архив |
| `/api/xkeen/restore` | POST | `api_xkeen_restore` | файл `snapshot` (v7/v6) | W ⟳ |
| `/api/keenetic/settings` | GET,POST | `api_keenetic_settings` | override'ы (пусто = снять) | — |
| `/api/keenetic/test-connection` | POST | `api_keenetic_test_connection` | SSH с переданными параметрами | R |
| `/api/keenetic/tg-settings` | GET,POST | `api_keenetic_tg_settings` | `{token, chat_id}` | R / WD |
| `/api/keenetic/watchdog-settings` | GET,POST | `api_keenetic_watchdog_settings` | сквозная проба (§7.6) | R / WD |
| `/api/keenetic/tg-test` | POST | `api_keenetic_tg_test` | тест с роутера | R |
| `/api/keenetic/repair-tg-channel` | POST | `api_keenetic_repair_tg_channel` | self-heal канала алертов | W ⟳ откат |
| `/api/xkeen/cmd` | POST | `api_xkeen_cmd` | `cmd` ∈ `XKEEN_COMMANDS` | по команде |
| `/api/xkeen/diagnose` | GET | `api_xkeen_diagnose` | 13 проверок | R |
| `/api/xkeen/auto-fix` | POST | `api_xkeen_auto_fix` | `fix_id` ∈ `XKEEN_AUTO_FIXES` | по ветке |
| `/api/diagnose/site` | GET | `api_diagnose_site` | `?url=` — проба с ПК | — |
| `/api/xkeen/iphost` | GET,POST | `api_xkeen_iphost` | `ip host`; `domain`, `action` | ndm |
| `/api/xkeen/test-socks` | GET,POST | `api_xkeen_test_socks` | статус; `action=remove` | W ⟳ при remove |
| `/api/xkeen/dat-categories` | GET | `api_xkeen_dat_categories` | категории в routing, `.dat` | R |
| `/api/xkeen/dat/lookup` | GET | `api_xkeen_dat_lookup` | поиск по `.dat` | R |
| `/api/xkeen/dat/refresh-cache` | POST | `api_xkeen_dat_refresh` | перекачать кэш | R |
| `/api/xkeen/dat/cache-status` | GET | `api_xkeen_dat_cache_status` | кэш vs роутер | R |

---

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
| `/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh`, `zz_wg_xray_<iface>.sh` | kill-switch, WG | Keenetic, снапшот (`zz_*.sh`) |
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

## 6. watchdog: панель ↔ watchdog.config ↔ watchdog.sh ↔ watchdog.state

Движок переключений живёт **на роутере** — `/opt/etc/xray/watchdog.sh` (эталон
`bootstrap/watchdog.sh.cur`, BusyBox ash, cron раз в минуту). Панель пишет его конфиг, раскатывает
версии и показывает состояние.

### 6.1. Связка

```
UI → POST (§4.3, §4.5) → keenetic_write_watchdog_config(updates)
  [_WATCHDOG_CFG_LOCK] keenetic_ensure_watchdog_current() → keenetic_read_watchdog_config() (None → отказ)
                       → дефолты отсутствующих ключей → update → _build_watchdog_config()
                       → cp .bak-<ts> → cat > watchdog.config
  [вне лока]           ssh /opt/etc/xray/watchdog.sh (timeout 30) — внеочередной тик
cron */1 → watchdog.sh: . watchdog.config → пробы → state-машина → generate_routing → проверки
         → mv 05_routing.json → xkeen -restart → health_check → watchdog.state / .log / .aiprobe
/xkeen → keenetic_get_watchdog_targets() → targets.*   keenetic_get_watchdog() → watchdog.*
```

Тики сериализует `/tmp/xkeen_watchdog.lock`, сохранения — `_WATCHDOG_CFG_LOCK`. Запись = внеочередной
тик ~30 с → связанные изменения писать **одним** вызовом.

### 6.2. Ключи watchdog.config, которыми управляет панель

Файл — shell (`KEY="value"`, подключается `. "$CFG"`). `_build_watchdog_config` пишет все ключи
`keys_order` в этом порядке (нет ключа → `KEY=""`), затем прочие ключи файла (не теряются; комментарии
при «Сохранить» пропадают). «Дефолт» — значение `watchdog.sh` при пустом ключе; оно же в
`DEFAULT_WATCHDOG_CONFIG` и в дефолтах записи, если не оговорено. «Явно» — запись ставит число, если пусто.

| Ключи (порядок `keys_order`) | Дефолт / особенности | Кто пишет | `targets` |
|---|---|---|---|
| `PRIMARY_TAG` | пусто → routing берёт `direct`; запись без ключа — legacy `vless-reality` | set-target/primary | `primary_tag` |
| `FAILOVER_TAGS` | `""`; запись без ключа — legacy `provider-a-nl` | set-target/primary, /failover, set-failover-chain, sync, remove-subscription | `failover_tags`, `failover_tag` |
| `AI_TAG` | `""`; запись без ключа — `provider-a-nl` | set-target/ai | `ai_tag` |
| `AI_DOMAINS`, `AI_EXT_CATEGORIES`, `AI_IP_RANGES` | bootstrap: домены с гео-сервисами для AI-пробы, CIDR AI-провайдеров | set-ai-domains, -ext-categories, -ip-ranges | `ai_domains`, `ai_ext_categories`, `ai_ip_ranges` |
| `AI_FAIL_BLOCK` | `1`; записью не дефолтится | set-ai-fail-block | `ai_fail_block` (нет → True) |
| `AI_FAILOVER_TAGS`, `AI_CHAIN_ALLOW_RU` | `""`, `0` явно | set-channel-chain kind=ai | `ai_failover_tags`, `ai_chain_allow_ru` |
| `YT_TAG`, `YT_DOMAINS`, `YT_EXT_CATEGORIES` | `""`; bootstrap: домены YouTube | set-target/yt, set-yt-* | `yt_tag`, `yt_domains`, `yt_ext_categories` |
| `YT_GEOIP_CATEGORIES` | watchdog очищает сам (v23), UI нет | set-yt-geoip-categories | `yt_geoip_categories` |
| `YT_FAIL_BLOCK` | `0` | set-yt-fail-block | `yt_fail_block` (нет → **True**, §9.3) |
| `YT_FAILOVER_TAGS`, `YT_CHAIN_ALLOW_RU` | `""`, `1` явно | set-channel-chain kind=yt | `yt_failover_tags`, `yt_chain_allow_ru` |
| `FOREIGN_TAG`, `FOREIGN_DOMAINS`, `FOREIGN_EXT_CATEGORIES`, `FOREIGN_GEOIP_CATEGORIES` | `""` | set-target/foreign, set-foreign-* | `foreign_tag`, `foreign_domains`, `foreign_ext_categories`, `foreign_geoip_categories` |
| `FOREIGN_FAIL_BLOCK` | `0` | set-foreign-fail-block | `foreign_fail_block` |
| `FOREIGN_FAILOVER_TAGS`, `FOREIGN_CHAIN_ALLOW_RU`, `FOREIGN_CHAIN_USE_COMMON` | `""`, `0`, `0` (флаги явно) | set-channel-chain kind=foreign | `foreign_failover_tags`, `foreign_chain_allow_ru`, `foreign_chain_use_common` |
| `IPV6_TAG`, `IPV6_DOMAINS` | `""` | set-target/ipv6, set-ipv6-domains | `ipv6_tag`, `ipv6_domains` |
| `DIRECT_DOMAINS`, `BLOCK_DOMAINS` | `""` | set-direct-/set-block-domains | `direct_domains`, `block_domains` |
| `PRIMARY_PROBE_URL` | пусто → TCP-проба; запись без ключа — `https://<vpn-domain>:8444/` | set-target/primary (для `vless-reality` URL, иначе пусто) | — |
| `FAIL_THRESHOLD`, `PASS_THRESHOLD` | `2`, `3` (`_num`) | только вручную | — |
| `DEEP_PROBE_ENABLE`, `DEEP_PROBE_INTERVAL`, `DEEP_PROBE_THRESHOLD`, `DEEP_RETURN_HOLD` | `1`, `180` с, `2`, `1800` с — все явно | watchdog-settings | `deep_probe_enable`, `deep_probe_interval`, `deep_probe_threshold`, `deep_return_hold` |
| `TG_BOT_TOKEN`, `TG_CHAT_ID` | пусто → алерты выключены | tg-settings | — |
| `FORCE_MODE` | `auto` | watchdog/&lt;mode&gt; | `force_mode` |

В `targets` числа санитизируются (`_wd_int`), списки режутся по `[,\s]+`, у `*_CHAIN_ALLOW_RU` и
`DEEP_PROBE_ENABLE` пусто = дефолт watchdog.

**Только в watchdog.sh** (UI нет; добавленные руками сохраняются хвостом): `AI_PROBE_PORT` (10818),
`AI_PROBE_INTERVAL` (600), `AI_PROBE_ALERT_COOLDOWN` (3600), `AI_PROBE_BAD_COUNTRIES`,
`AI_DEAF_THRESHOLD` (3), `AI_DEAF_AUTORESTART` (1), `AI_RETURN_HOLD` (1800),
`AI_/YT_/FOREIGN_CHAIN_ROUNDS` (2), `AI_/YT_/FOREIGN_CHAIN_SWITCH_COOLDOWN` (300),
`DEEP_PROBE_PORT_PRI/YT/FGN` (10819–10821), `DEEP_PROBE_URL_PRI/YT/FGN` (cloudflare trace, youtube
`generate_204`, `api.telegram.org`), `KILL_FAIL_THRESHOLD`/`KILL_PASS_THRESHOLD` (3/2), `RECVQ_THRESHOLD`,
`RECVQ_HARD`, `STALL_FAIL_NEED`, `STALL_COOLDOWN`, `CONNTRACK_MIN`, `FD_DELTA_THRESHOLD`,
`FD_STALL_COOLDOWN`.

### 6.3. watchdog.state — полный перечень полей

Одна строка, пишется в конце тика (`# ---------- Save state ----------`):

```
<state> <counter> <default_tag> EFFECTIVE_AI= EFFECTIVE_YT= EFFECTIVE_FOREIGN= LAST_FULL_PROBE= PREV_ALIVE=
LAST_BAD_MD5= AI_BLOCKED= AI_KCTR= YT_BLOCKED= YT_KCTR= FOREIGN_BLOCKED= FOREIGN_KCTR= AI_EFF= DEEP_CTR= DEEP_TS=
AI_CMISS= AI_SW_TS= AI_LEFT_TS= YT_EFF= YT_CMISS= YT_SW_TS= FGN_EFF= FGN_CMISS= FGN_SW_TS=
DPRI_CTR= DPRI_TS= DPRI_DEAF= DPRI_LEFT= DPRI_TAG= DYT_CTR= DYT_TS= DYT_DEAF= DYT_LEFT= DYT_TAG=
DFGN_CTR= DFGN_TS= DFGN_DEAF= DFGN_LEFT= DFGN_TAG=
+ v31: D{PRI,YT,FGN}_RES= PRI_MAIN= YT_MAIN= FGN_MAIN= DPRI_FSINCE= PRI_NOALT= DYT_NOALT= DFGN_NOALT=
```
(точный порядок — строка `echo "$NEW_STATE …` в `.cur`).

| Поле | Смысл |
|---|---|
| позиции 1–3 | `primary`/`failover` (`unknown` в панели без файла); подряд-проб к порогу; действующий default |
| `EFFECTIVE_AI`/`_YT`/`_FOREIGN` | применённый тег канала: свой, резерв, `block` или пусто |
| `LAST_FULL_PROBE`, `PREV_ALIVE` | обход FAILOVER раз в 300 с и число живых (алерт при ≤ 2) |
| `LAST_BAD_MD5` | md5 кандидата routing, уже ронявшего сеть (анти-флап) |
| `AI_/YT_/FOREIGN_BLOCKED`, `…_KCTR` | латч kill-switch канала и счётчик гистерезиса |
| `AI_EFF`, `YT_EFF`, `FGN_EFF` | дубли `EFFECTIVE_*` под короткими именами |
| `DEEP_CTR`, `DEEP_TS` | legacy v27, переносятся как есть |
| `AI_/YT_/FGN_CMISS`, `…_SW_TS`, `AI_LEFT_TS` | пустые круги цепочки; время смены канала; уход AI по глухоте |
| `D{PRI,YT,FGN}_CTR/_TS/_DEAF/_LEFT/_TAG` | сквозная проба роли: пустых подряд; время пробы; латч глухоты; время ухода по глухоте; тег замера |
| `D{PRI,YT,FGN}_RES` (v31) | итог последнего замера: `ok` / `fail` / `none` (замера на этом теге не было, служебный тег, проба выключена) |
| `PRI_MAIN`, `YT_MAIN`, `FGN_MAIN` (v31) | основной тег роли на прошлом тике: если в настройках сменили основной — карантин `D*_LEFT` обнуляется, лог «сменён в настройках (A → B)», без TG «вернулся» |
| `PRI_NOALT`, `DYT_NOALT`, `DFGN_NOALT`, `DPRI_FSINCE` (v31) | «предупреждение о том, что уходить некуда, уже отправлено» (один TG за эпизод); с какого момента форсируется failover глухого основного (лог раз в 5 мин) |

Имена не симметричны (kill-switch «Зарубежных» — `FOREIGN_*`, прочее — `FGN_*`/`DFGN_*`) — брать из
строки записи в `.cur`. Разбор только по токенам (`split()` + `split("=", 1)`): `EFFECTIVE_AI=` и
`AI_EFF=` — разные ключи. `watchdog.aiprobe` — `ts|country|last_alert|empty|stage`.

Метки `watchdog.log`: `SWITCH`, `FAILOVER-HEALTH`, `AI-`/`YT-`/`FOREIGN-FAILOVER` (+ `-RETURN`),
`…-KILL-SWITCH` (+ `-CLEAR`), `DEEP-PROBE` (`DEAF`, `RECOVER`), `XRAY-TEST FAIL`, `HEALTH FAIL`,
`AUTO-ROLLBACK`, `SKIP (анти-флап)`, `UDP-STALL`, `FD-STALL`, `AUTO-FIX`, `WARN`, `ERROR`. Долгие
состояния пишутся раз в 5 мин (`_log_every5`), иначе окно «последние 30 строк» забьётся.

### 6.4. Как панель показывает состояние

`keenetic_get_watchdog(deep_hold_sec)` — один SSH (`cat watchdog.state; echo '=AIPROBE='; cat
watchdog.aiprobe`) + `keenetic_tail_log(…, n=30)` → `state`, `counter`, `current_default`,
`effective_ai`, `effective_yt`, `effective_foreign`, `last_bad_md5`, `log`, `ai_probe` (`{ts, country,
empty, stage, age_min}`/`None`), `fields` (все `KEY=VAL`), `deep` (`{pri, yt, fgn}`: `{ctr, ts, deaf,
left, tag, age_min, hold_left_min}` или `None` у роли без полей — старый watchdog), `fgn_eff`, `yt_eff`,
`ai_eff`. Те же ключи — в quick-fail литерале. В шаблоне: баннер режима (`.wd-state`), Jinja-статусы
kill-switch, «📋 Лог watchdog», JS-константы (§8.4). `api_xkeen_state`/`api_xkeen_debug` отдают тот же
словарь, но страница их не опрашивает — обновление только перезагрузкой.

### 6.5. Автосинк watchdog.sh по номеру версии

- Версия — первое совпадение `watchdog\s+v(\d+)` в первых **40** строках (`_parse_watchdog_version`).
  Заголовок `.cur`: `# XKeen failover watchdog vNN (дата, суть)`, история ниже — **новую версию писать
  в самый верх**. Template: `template\s+v(\d+)` в первых **5** строках (`_parse_template_version`).
- `keenetic_ensure_watchdog_current()` — в начале **каждой** `keenetic_write_watchdog_config`, в
  sync-structure и repair-tg-channel. Условие: на роутере **строго меньше**. Проверка — только `sh -n`.
  Результат `current`/`upgraded`/`skip`/`failed`; при сбое старый watchdog не трогается.
- Template автосинком **не** обновляется — только «⚙ Применить структуру». Баннер «структура отстала» —
  `_structure_drift_status()` (`outdated` = remote > 0 и local > remote).

> 🔴 **Правишь `bootstrap/watchdog.sh.cur` — поднимай версию** в первой строке заголовка и добавляй
> историю сверху. Без новой версии правка не дойдёт ни до одного роутера; с новой — уйдёт при первом
> «Сохранить» с боевой панели, поэтому поднимать после проверки (§10). Новые ключи / поля —
> синхронно в §6.2/§6.3 и в Python (§9.2, §9.3).

### 6.6. Тик watchdog.sh

Порядок разделов `.cur`: lock → помощники (`probe_tag`, `_num`, `is_ru_tag`, `killswitch_decide`,
`_state_field`, `_log_every5`, `deep_probe_check`, `tg_alert`, `generate_routing`, `routing_changed`,
`validate_routing_xray`, `health_check`) → досыл TG → UDP-залип (v19) → `53` в `port_exclude.lst` (v21)
→ очистка `YT_GEOIP_CATEGORIES` (v23) → self-heal `socks-ai-check` (v24) → параметры v27/v29/v30 →
self-heal `socks-deep-*` (v29) → fd-залип (v22) → AI-проба (v24/v25) → State → Force-mode → проба
PRIMARY и state-машина (глухота `DPRI_DEAF` форсирует down) → AI → YT → FOREIGN (kill-switch +
цепочки) → Apply (`generate_routing <default> <ai> <yt> <foreign> <ipv6>` → `routing_changed` →
анти-флап → `validate_routing_xray` → `mv` → `xkeen -restart` → `health_check` → `.lastgood` или
`AUTO-ROLLBACK`) → обход FAILOVER → Save state.

Плейсхолдеры template: `__DEFAULT_TAG__`, `__BLOCK_RULE_BLOCK__`, `__DIRECT_RULE_BLOCK__`,
`__AI_RULE_BLOCK__`, `__YT_RULE_BLOCK__`, `__YT_RULE_GEOIP__`, `__FOREIGN_RULE_BLOCK__`,
`__FOREIGN_RULE_GEOIP__`, `__IPV6_RULE_BLOCK__` (пусто → правило не генерится). Правила для
`socks-ai-check` и `socks-deep-*` watchdog вставляет сам.

---

## 7. Каналы и цепочки резерва

### 7.1. Роли

| Роль | Ключи | Действующий тег | Kill-switch | Цепочка (RU по умолч.) | Сквозная проба | UI (`id`) |
|---|---|---|---|---|---|---|
| PRIMARY / FAILOVER | `PRIMARY_TAG`, `FAILOVER_TAGS`, `PRIMARY_PROBE_URL` | state поз. 3 | — | `FAILOVER_TAGS` | `DPRI_*` | `select-primary`, `info-primary`, `select-failover`, `info-failover`, `failover-active-label`, `deep-pri-note`, `failover-chain` |
| AI | `AI_TAG`, `AI_DOMAINS`, `AI_EXT_CATEGORIES`, `AI_IP_RANGES` | `EFFECTIVE_AI` | `AI_FAIL_BLOCK` (1) | `AI_FAILOVER_TAGS` (нет) | AI-проба страны | `select-ai`, `info-ai`, `info-ai-active`, `ai-chain-*`, `ai-fail-block-toggle` |
| YouTube | `YT_TAG`, `YT_DOMAINS`, `YT_EXT_CATEGORIES` | `EFFECTIVE_YT` | `YT_FAIL_BLOCK` (0) | `YT_FAILOVER_TAGS` (да) | `DYT_*` | `select-yt`, `info-yt`, `info-yt-active`, `yt-chain-*`, `yt-fail-block-toggle` |
| Зарубежные сервисы | `FOREIGN_TAG`, `FOREIGN_DOMAINS`, `FOREIGN_EXT_CATEGORIES`, `FOREIGN_GEOIP_CATEGORIES` | `EFFECTIVE_FOREIGN` | `FOREIGN_FAIL_BLOCK` (0) | `FOREIGN_FAILOVER_TAGS` или общая (нет) | `DFGN_*` | `select-foreign`, `info-foreign`, `info-foreign-active`, `foreign-chain-*`, `foreign-chain-use-common`, `foreign-fail-block-toggle` |
| Через IPv6 | `IPV6_TAG`, `IPV6_DOMAINS` | — | — | — | — | `select-ipv6` |
| DIRECT / BLOCK | `DIRECT_DOMAINS`, `BLOCK_DOMAINS` | — | — | — | — | секции `ds-direct`, `ds-block` |

- **IPv6**: домены получают fake-IPv4 `198.18.0.1` (`ndmc ip host`); узел обязан иметь IPv6-egress и
  `routeOnly:false`; `direct` и RU-узлы не годятся.
- **AI**: `AI_DOMAINS` намеренно содержит гео-сервисы — по ним watchdog проверяет страну выхода; UI
  красит рискованные варианты (`ai_risk`), `setRole` спрашивает подтверждение.
- **GeoIP-категории** (IP-only клиенты) — только у «Зарубежных»; нужен расширенный `geoip.dat` с
  `telegram` (`geoip_status`, auto-fix `install_extended_geoip`). Категории `discord` нет ни в одной
  geoip-базе — Discord идёт доменами.
- **Базовые правила template** из панели не редактируются (read-only блок «📐 Базовые правила
  маршрутизации»).

### 7.2. Цепочки резерва каналов

- Порядок = приоритет; перебор всегда сверху; возврат на более приоритетный — не чаще
  `*_CHAIN_SWITCH_COOLDOWN` (аварийный уход — сразу). Основной тег проверяется раньше и в цепочку не
  входит. `block` — последняя мера: вся цепочка молчит `*_CHAIN_ROUNDS` кругов и включён `*_FAIL_BLOCK`.
  Пустая цепочка = прежнее поведение.
- `direct`/`block` в цепочке запрещены; лимит `CHANNEL_CHAIN_MAX` = 8.
- RU-фильтр: `is_ru_tag()` в watchdog ≡ `wd_is_ru_tag()` в панели (вплоть до асимметрии «`Ru_` есть,
  `Ru-` нет»). `chain_tag_info[tag]`: `ru_wd` (как увидит watchdog), `ru_look` (эвристика панели),
  `anti_dpi`, `group`.
- `api_xkeen_set_channel_chain`: словарь `_KINDS` (`label`, `key_chain`, `key_ru`, `key_main`,
  `t_allow_ru`); дедуп; `targets` с `strict=True`; основной тег убирается; теги сверяются с
  `keenetic_get_outbounds()`; **не пришедшие `allow_ru`/`use_common` не затираются** (так работает
  `removeFromChannelChain`).
- Общая цепочка `FAILOVER_TAGS`: `api_xkeen_set_failover_chain` (`saveFailoverChain`,
  `pingFailoverChain`, `removeDeadFromChain`, `removeFromFailoverChain`, `bulkAddToChain`) и set-target.

### 7.3. «Общая цепочка» у «Зарубежных» (`FOREIGN_CHAIN_USE_COMMON`)

`=1` → watchdog подставляет `FOREIGN_FAILOVER_TAGS="$FAILOVER_TAGS"`, RU-фильтр по
`FOREIGN_CHAIN_ALLOW_RU` действует и тут; собственный список хранится, но не действует. Чекбокс
`foreign-chain-use-common` только перерисовывает (`chainRender('foreign')` → `chainRenderCommonView`:
`foreign-chain-editor` гаснет, read-only `foreign-chain-common-view` из `WATCHDOG_FAILOVER_TAGS`);
пишет «Сохранить цепочку» (`saveChannelChain('foreign')` шлёт `use_common`). `chainPositionOf` и
Jinja-статус kill-switch берут действующий список. Теги собственного списка защищены от удаления и при
use_common=1 — иначе при выключении галочки «проснётся» тег-зомби.

### 7.4. Где учитывается членство в цепочках (менять синхронно)

Удалённый тег, оставшийся в `*_FAILOVER_TAGS`, — **тег-зомби**: в аварии цепочка молча укоротится.
Новая роль или цепочка — пройти всё:
1. `keenetic_get_watchdog_targets` + quick-fail `targets`; `keys_order`, дефолты записи,
   `DEFAULT_WATCHDOG_CONFIG`.
2. `keenetic_remove_outbound` (блокирует любое членство) и `keenetic_remove_outbounds_bulk`
   (`*_chain_set`, причина в `skipped`).
3. `_subscription_removal_plan` (`critical`, `*_chain_tail`) + `keenetic_remove_subscription` (одна
   запись config, `removed_from_*_chain`); `api_xkeen_subscription_sync` (pre-check, автоочистка).
4. `xkeen_page`: `in_*_chain`, `*_chain_position`, `is_inactive`, `any_active` групп.
5. Jinja: бейджи в `failover-chain` и «📋 Все outbounds» (`has_current`, «резерв … №N» +
   `removeFromChannelChain(kind, tag)`), статус kill-switch карточки, условные тексты блокировки.
6. JS: `CHAIN_STATE[kind]`, `chainPositionOf` (маппинг `roleLabel`), `chainRender`,
   `saveChannelChain`, `DOMContentLoaded`, `refresh*Info`, `applyStatusesToMeta`.
7. Help (`help-channels`) и `tests/test_v29_ui_offline.py`.

### 7.5. Три разных «жив»

| Что | Где меряется | Значит | Показ |
|---|---|---|---|
| Handshake-проба | панель: `probe_outbounds` → curl с роутера до `host:port` | порт узла отвечает | `VLESS_META[tag].status_*` → `getChannelStatusNote`, `_latencyBadge`, `refreshStatuses`, `probeChannelFromInfo` |
| Сквозная проба | watchdog: `deep_probe_check` через `socks-deep-*` по **действующему** каналу | трафик идёт насквозь | `WATCHDOG_DEEP` → `deepProbeNote`, `renderDeepProbeSummary` |
| AI-проба | watchdog: `socks-ai-check` → страна выхода | туннель AI жив, выход не в запрещённой стране | `WATCHDOG_AI_PROBE` → `aiThroughProbeNote` |

Порт отвечает ≠ трафик идёт. `ok: None` / `stale` / `kind: ssh_fail` — «не смог проверить», не «мёртв»;
авто-действия обязаны это различать.

### 7.6. Сквозная проба (DEEP_PROBE, watchdog v29+)

Inbound'ы `socks-deep-pri`/`-yt`/`-fgn` watchdog заводит сам и маршрутизирует на действующий канал
роли (у AI — своя проба с v25). Раз в `DEEP_PROBE_INTERVAL` — запрос к цели, успех = любой HTTP-код.
`DEEP_PROBE_THRESHOLD` пустых подряд → латч `D*_DEAF=1` (до успешной пробы) → PRIMARY форсирует
failover, YouTube и «Зарубежные» уходят по цепочке; реакция ≈ интервал × порог. Смена канала роли
сбрасывает счётчики; после ухода — карантин `DEEP_RETURN_HOLD`. UI: «🩺 Сквозная проба каналов» в
карточке «🎯 Основное…» — форма `deep-probe-form` (`deep-probe-enable`, `deep-probe-interval`,
`deep-probe-threshold`, `deep-return-hold-min`) → `saveDeepProbeSettings` → `POST
/api/keenetic/watchdog-settings` JSON `{deep_probe_enable, deep_probe_interval, deep_probe_threshold,
deep_return_hold_min}` → `_validate_deep_probe_settings` (`DEEP_PROBE_LIMITS`: 60–900 с, 1–5,
5–1440 мин; карантин ×60) → `keenetic_write_watchdog_config`. GET — значения + `limits` (503 без
config). Watchdog ≤ v28 полей `D*` не пишет → `deep[role] = None` → «нет данных».

Поведение v31 (важно для текстов UI): в `block` роль уходит только при смерти основного по TCP
(`*_BLOCKED=1`); основной лишь глух и живого резерва нет → роль остаётся на основном, одно
предупреждение за эпизод, без `xkeen -restart` (иначе «качели» block ↔ глухой узел). Глухой основной без
FAILOVER не форсирует failover. TG «переключаюсь на резерв» — только если у роли есть резерв (иначе «резерв
не настроен»). Бейдж `deepProbeNote` — «⚪ не измеряется», когда `res == 'none'` (у старого watchdog без
`*_RES` — по `ts == 0`, служебному тегу или выключенной пробе); подсказка «сторож переключит на резерв» —
только при `deepRoleHasReserve(role)`. POST `/api/keenetic/watchdog-settings`: тело не объект или
пустое → 400; отсутствующее поле — ключ не трогается. Дефолты `DEEP_*` при записи config ставятся только
если `keenetic_ensure_watchdog_current()` вернул `current`/`upgraded` (старому watchdog v27/v28 нельзя
включать его нерабочую пробу через `socks-local`).

---

## 8. Фронтенд `/xkeen` (`XKEEN_TEMPLATE`)

### 8.1. Компоновка

`<head>`/`<style>` → шапка `<h1>🚦 XKeen` (индикатор xray, версия) → `#flash` → баннер приветствия
(`is_first_run`) → баннер «структура отстала» (`syncStructure(this)`) → разделы `<h2 class="sec-…">` →
скрипт WG-помощника (`initWgGen`) → «❓ Помощь» → главный `<script>` → `<footer>`.

| Раздел (класс) | Категория | Содержимое |
|---|---|---|
| 🎯 Основное и резервное подключение (`sec-routing`) | Каналы | баннер режима, PRIMARY/FAILOVER, подпункты `data-xk-sub` (🤖 AI, 📺 YouTube, 📱 Зарубежные сервисы, 🌐 Через IPv6, ↪️ Напрямую, ⛔ Блокировать, 👥 Клиенты), ⚡ Принудительно, 🩺 Сквозная проба, 📋 Лог watchdog |
| ⛓️ Цепочка резерва (FAILOVER) (`sec-chain`) | Каналы | `failover-chain` (drag-n-drop) |
| 📋 Все outbounds (`sec-list`), ➕ Добавить (`sec-add`), 📡 Обновить из подписки (`sec-sync`) | Каналы | группы, bulk, форма, preview/sync |
| 💾 Бэкап и восстановление (`sec-backup`), 🔧 Подключение к роутеру (`sec-router`, `section-router-config`), 🛠 Управление XKeen (`sec-xkmgmt`), 🐉 Подключение клиентов WireGuard (`sec-router`) | Обслуживание | снапшоты, SSH и bootstrap, CLI и GeoFile, WG |
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
- Тексты UI — справочные, без обращения на «ты».

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
| `test_template_ips.py` | `.cur`-template (версия, два IPv4-массива), синк IP эндпоинтов |
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
- Объём держать ≤ ~80 КБ: детали — в коде и docstring'ах, здесь — карта и правила.

### Журнал изменений документа

| Дата | Изменение |
|---|---|
| 2026-10-03 | Создание. Описано рабочее дерево: панель на базе v1.1.3 с UI для watchdog v29/v30 (цепочка и «общая цепочка» «Зарубежных», сквозная проба ролей и её настройки, статус `geoip.dat` и его связь с `geoip_v2fly.dat`), `bootstrap/watchdog.sh.cur` v30, template v13; 87 маршрутов, 13 проверок диагностики, 6 офлайн-тестов. |
| 2026-10-03 | Независимое ревью → watchdog v31 (поля `*_RES`, `*_MAIN`, `*_NOALT`, `DPRI_FSINCE`; §6.3, §7.6); SSH-слой: `stdin=DEVNULL` во всех запусках ssh/scp (§5.2); geoip: статус без скана файла, проверка telegram в фоне с кэшем по `(size, mtime)`, auto-fix делает `geoip.dat` симлинком на `geoip_v2fly.dat`, откат при падении xray, 2 бэкапа. |
