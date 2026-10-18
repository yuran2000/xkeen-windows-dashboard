# §4. Реестр маршрутов

> Часть документа [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) (вход и карта — там). Номера разделов `§` —
> общие для всех файлов: §1–3, 5, 8–12 — в главном файле, §4 — `04-routes.md`, §6 — `06-watchdog.md`,
> §7 — `07-chains.md`. Правила обновления — §12: правка кода и этого файла — в одном коммите.

91 декоратор `@app.route` на 90 функциях; все, кроме «без auth», под `requires_auth`. «Роутер»:
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
| `/api/xkeen/add` | `api_xkeen_add` | `payload` (vless:// или JSON), `tag`, `overwrite`, `sub_url`, `note` | W: `04_outbounds.json` + IP в template — ⟳ один (§6.7; нет — прежний путь с откатом), `outbound_meta.json` |
| `/api/xkeen/remove` | `api_xkeen_remove` | `tag` | то же |
| `/api/xkeen/remove-bulk` | `api_xkeen_remove_bulk` | `tags` (одна транзакция, `skipped`) | то же |
| `/api/xkeen/remove-subscription` | `api_xkeen_remove_subscription` | `key` (sub_url или pbk) | WD, W: `04_outbounds.json`, template, `subscription_meta.json`; ⟳ один — режим применения watchdog (`keenetic_apply_staged`, §6.7); до ~8 мин ответа |
| `/api/xkeen/subscription-preview` | `api_xkeen_subscription_preview` | `url` (ничего не пишет); в `matches` — `host_change`, `fp_change`, `old_fp`/`new_fp`, `fp_lottery_old`/`_new`, в `orphans_in_sub` — `fp`, `fp_lottery` | R + HTTP с ПК |
| `/api/xkeen/subscription-sync` | `api_xkeen_subscription_sync` | `url`, `add_new`, `remove_orphans`, `update_expires`, `overwrite_expires`, `add_tags` | W: `04_outbounds.json` + чистка цепочек + IP template — ⟳ один (§6.7), обе meta |
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
| `/api/xkeen/wg-clients` | GET | `api_wg_clients` | выданные (реестр + где на роутере), свободный слот, пресеты | R |
| `/api/xkeen/wg-issue` | POST | `api_wg_issue` | `name`, `exit`, `full`, `services`, `extra`, `s2s`, `remote_lan`, `create` → `.conf`, QR, серверный `.conf`, CLI | ndm, W: netfilter.d (подмена, заворот), crontab |
| `/api/xkeen/wg-revoke` | POST | `api_wg_revoke` | `id` записи реестра | ndm, W: netfilter.d, crontab |
| `/api/xkeen/wg-router` | GET | `api_wg_router` | инвентарь WG + `orphan_hooks` | R |
| `/api/xkeen/wg-backup` | GET | `api_wg_backup` | `?iface=WireguardN` → текст-вложение | R |
| `/api/xkeen/wg-connect`, `wg-disconnect`, `wg-cleanup-orphan` | POST | `api_wg_connect`, `api_wg_disconnect`, `api_wg_cleanup_orphan` | хук `zz_wg_xray_<iface>.sh` + cron self-heal | W: netfilter.d, crontab |
| `/api/xkeen/wg-acl`, `wg-acl-apply` | GET; POST | `api_wg_acl_get`, `api_wg_acl_apply` | форма правил (`rules_form`: `current`, `pre`, `warnings`); `iface_id`, `exit`, `full`, `services`, `extra` → access-list, NAT, подмена, заворот по итоговому режиму; линк — отказ | R / ndm, W: netfilter.d, crontab |
| `/api/xkeen/policy-clients` | GET | `api_xkeen_policy_clients` | клиенты, политики, порты, `.lst` | R |
| `/api/xkeen/set-client-policy` | POST | `api_xkeen_set_client_policy` | `mac`, `policy` | ndm |
| `/api/xkeen/flush-conntrack` | POST | `api_xkeen_flush_conntrack` | `ip` | `conntrack -D` или ndm |
| `/api/xkeen/install-conntrack-tools` | POST | `api_xkeen_install_conntrack_tools` | opkg | W |

### 4.5. Template, структура, бэкапы, настройки, CLI, диагностика

| Путь | Методы | Функция | Что делает | Роутер |
|---|---|---|---|---|
| `/api/xkeen/routing-template-rules` | GET | `api_xkeen_routing_template_rules` | базовые правила template | R |
| `/api/xkeen/sync-template-ips` | POST | `api_xkeen_sync_template_ips` | preview; `apply=1`; `restart=1` | W: template; ⟳ один при `restart=1` (применение с force) |
| `/api/keenetic/sync-structure` | POST | `api_keenetic_sync_structure` | «⚙ Применить структуру» | W ⟳ один (применение с force), откат |
| `/api/keenetic/bootstrap-preview` | GET | `api_keenetic_bootstrap_preview` | план развёртывания | R |
| `/api/keenetic/bootstrap-apply` | POST | `api_keenetic_bootstrap_apply` | `{keys, overwrite_existing}` | W, прогон watchdog, ⟳ |
| `/api/xkeen/backup` | GET | `api_xkeen_backup` | снапшот v7 файлом | R |
| `/api/xkeen/backup-to-disk` | POST | `api_xkeen_backup_to_disk` | снапшот в `<base>/backups/` | R |
| `/api/xkeen/migration-backup` | POST | `api_xkeen_migration_backup` | tar `/opt` → `scp -O` | W: временный архив |
| `/api/xkeen/restore` | POST | `api_xkeen_restore` | файл `snapshot` (v7/v6) | W ⟳ один (применение с force) |
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
