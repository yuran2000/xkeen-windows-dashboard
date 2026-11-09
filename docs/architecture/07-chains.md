# §7. Каналы и цепочки резерва

> Часть документа [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) (вход и карта — там). Номера разделов `§` —
> общие для всех файлов: §1–3, 5, 8–12 — в главном файле, §4 — `04-routes.md`, §6 — `06-watchdog.md`,
> §7 — `07-chains.md`. Правила обновления — §12: правка кода и этого файла — в одном коммите.

### 7.1. Роли

| Роль | Ключи | Действующий тег | Kill-switch | Цепочка (RU по умолч.) | Сквозная проба | UI (`id`) |
|---|---|---|---|---|---|---|
| PRIMARY / FAILOVER | `PRIMARY_TAG`, `FAILOVER_TAGS`, `PRIMARY_PROBE_URL` | state поз. 3 | — | `FAILOVER_TAGS` | `DPRI_*` | `select-primary`, `info-primary`, `select-failover`, `info-failover`, `failover-active-label`, `deep-pri-note`, `failover-chain` |
| AI | `AI_TAG`, `AI_DOMAINS`, `AI_EXT_CATEGORIES`, `AI_IP_RANGES` | `EFFECTIVE_AI` | `AI_FAIL_BLOCK` (1) | `AI_FAILOVER_TAGS` (нет) | AI-проба страны | `select-ai`, `info-ai`, `info-ai-active`, `ai-chain-*`, `ai-fail-block-toggle` |
| YouTube | `YT_TAG`, `YT_DOMAINS`, `YT_EXT_CATEGORIES` | `EFFECTIVE_YT` | `YT_FAIL_BLOCK` (0) | `YT_FAILOVER_TAGS` (да) | `DYT_*` | `select-yt`, `info-yt`, `info-yt-active`, `yt-chain-*`, `yt-fail-block-toggle` |
| Зарубежные сервисы | `FOREIGN_TAG`, `FOREIGN_DOMAINS`, `FOREIGN_EXT_CATEGORIES`, `FOREIGN_GEOIP_CATEGORIES` | `EFFECTIVE_FOREIGN` | `FOREIGN_FAIL_BLOCK` (0) | `FOREIGN_FAILOVER_TAGS` или общая (нет) | `DFGN_*` | `select-foreign`, `info-foreign`, `info-foreign-active`, `foreign-chain-*`, `foreign-chain-use-common`, `foreign-fail-block-toggle` |
| Домены .ru через VPN | `RU_VPN_TAG`, `RU_VPN_DOMAINS`, `RU_VPN_SUBDOMAINS` | `ru_vpn_active` из routing | блокировка списка при исчерпании цепочки, если `RU_VPN_FAIL_DIRECT=0` | `RU_VPN_FAILOVER_TAGS` (RU разрешены) | — | `ru-vpn-mode`, `ru-chain-list`, `ru-vpn-status` |
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

У списка .ru настройки сохраняются вместе с доменами через `saveRuVpnExceptions`.
`ru_vpn_select` сначала проверяет основной VLESS-выход, затем до восьми резервов в сохраненном
порядке. Уход требует `FAIL_THRESHOLD` неудач текущего выхода, возврат — `PASS_THRESHOLD`
успехов одного более приоритетного выхода подряд. Проба проверяет
TCP-порт сервера, поэтому HTTP-отказ сайта и вход в аккаунт остаются отдельной проверкой.
Общая цепочка FAILOVER здесь не используется. Из подписки нельзя удалить выбранный основной или резервный выход.
Карточка показывает примененный выход на момент загрузки и позволяет обновить снимок.

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
   подготовка config `stage_id`, bulk с `targets` без хвостов, `removed_from_*_chain`; применение — ОДНИМ
   рестартом, режим применения watchdog §6.7); `api_xkeen_subscription_sync` (pre-check, автоочистка).
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

Четвёртое — у YouTube (v41): трафик идёт, но **медленно** — замер скорости `yt_speed_check` (§7.7); «мс» у узла
о скорости ничего не говорят.

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

### 7.7. Скорость канала YouTube (watchdog v41)

Повод — 28.10.2026: узлы провайдера отвечали за 54 мс и проходили сквозную пробу, а качали 11–22 КБ/с; видео стояло,
сторож не видел беды. `yt_speed_check <тег> <глух>` (вызов в блоке YouTube сразу после `deep_probe_check YT`):
раз в `YT_SPEED_INTERVAL` (900 с) `_yt_speed_kbps` качает `YT_SPEED_BYTES` (2 МБ, `curl -r`) с `YT_SPEED_URL`
(dl.google.com) через `socks-deep-yt` — то есть через **действующий** канал роли. Таймаут curl (`YT_SPEED_TIME`, 15 с)
— нормальный исход медленного канала: `speed_download` печатается и тогда; скачано 0 — замер не засчитан.
Медленнее `YT_SPEED_MIN_KBPS` (300) — тот же файл мимо xray (трафик роутера политикой XKeen не перехватывается):
засчитывается, только если напрямую ≥ порога и ≥ `YT_SPEED_RATIO` (2, до v43 — 3) × скорость канала — иначе тормозит весь
интернет. Повтор медленного — через `YT_SPEED_RECHECK` (120 с); `YT_SPEED_THRESHOLD` (2) подряд → латч `YT_SLOW`
(до быстрого замера или смены канала). Смена канала — счётчики с нуля, первый замер через `YT_SPEED_SETTLE` (120 с).
Не меряет: выключено (`YT_SPEED_ENABLE=0`, дефолт), служебный тег, глухой канал (его уводит сквозная проба),
перегрузка xray, режим применения.

Уход: `_yt_bad` = `DYT_DEAF` или `YT_SLOW` — по нему блок YouTube входит в перебор цепочки, пропускает текущий
канал, не держит его по cooldown, заводит `DYT_LEFT` / растущий карантин основного (как у глухого). Резерв, с
которого ушли как с медленного, — в `YT_SLOWQ` («тег:время»; срок — `_deep_hold 1` = `DEEP_RETURN_HOLD`), цепочка
его пропускает: без этого два медленных резерва гоняли бы роль друг на друга с `xkeen -restart` на каждом шаге.
Медленно всё — роль остаётся на основном, «переключиться некуда» один раз за эпизод. TG: «🐢 YouTube переключён
на резерв … качает N КБ/с (норма от M, напрямую интернет K КБ/с)». Ограничение: замер делит канал с видео,
которое смотрят в этот момент, — отсюда два замера подряд. Потолок: роутер сам качает не быстрее 1–1,5 МБ/с
(процессор, TLS в curl), поэтому при `YT_SPEED_RATIO` 2 медленным признаётся канал примерно до 700 КБ/с, как бы
высоко ни стоял порог; 3 (v41–v42) давало потолок ~450 КБ/с — меньше, чем нужно для 1080p (v43, 28.10).

Панель: блок «⚡ Скорость канала» (`yt-speed-box`) в карточке YouTube — последний замер сторожа (`watchdog.yt_speed`),
карантин медленных, кнопка «⚡ Замер скорости» (`ytSpeedTest` → `POST /api/xkeen/yt-speed-test`, ничего не пишет),
галочка и поля → `saveYTSpeedSettings` → `POST /api/keenetic/watchdog-settings` JSON `{yt_speed_enable,
yt_speed_min_kbps, yt_speed_interval_min}` (`DEEP_PROBE_LIMITS`: 50–5000 КБ/с, 5–120 мин; минуты ×60). Справка —
абзац «Медленный канал YouTube» в `help-watchdog`. Тесты — `tests/test_yt_speed.py` (сторож в `sh`),
`tests/test_yt_speed_panel_offline.py` (панель).

### 7.8. Проверка скорости до возврата YouTube на основной (watchdog v44)

При включённом `YT_SPEED_ENABLE` автоматический возврат на `YT_TAG` требует отдельного замера
скорости **до смены маршрута клиентов**. `yt_return_ensure_inbound` создаёт loopback-вход
`socks-yt-return` на 127.0.0.1:10823 (константа `YT_RETURN_PROBE_PORT`). `generate_routing` ставит первым
точное правило этого входа → **настроенный основной** `YT_TAG`; `socks-deep-yt` продолжает измерять
действующий резерв. Inbound добавляется с бэкапом; порт занят или JSON не прочитан — отказ без замены.
При первом включении вход и правило подхватываются штатным применением routing; отдельного вызова
`xkeen -restart` эта функция не делает. Конфликт/ошибка штатной проверки не считается успехом пробы.

`yt_return_check` допускает основной после истечения растущего карантина и успешного замера:
HTTP 200/206, скачано не менее 100000 байт, скорость ≥ `YT_SPEED_MIN_KBPS`. Источник — тот же
`YT_SPEED_URL` и лимиты, что у обычного замера. Сравнение с прямым интернетом для допуска не нужно:
если общей скорости недостаточно, возврат тоже откладывается. Перед скачиванием проверяется первый
маршрут и loopback-вход (`_yt_return_route_ready`); дрейф, ошибка чтения, пустой/короткий ответ,
низкая скорость, перегрузка xray и режим `WD_APPLY` оставляют текущий канал. Проба не запускается
чаще `YT_SPEED_RECHECK` (120 с), таймер хранится отдельно от скорости действующего канала
(`YT_RETURN_TAG`, `YT_RETURN_TS`); успех не переносится между тиками. Проверка действует также,
если резерв сам неисправен, нет подходящей альтернативы или происходит выход из `block`.
Если основной не прошёл, перебор может выбрать другой резерв. Явная смена основного в панели
сохраняет ручную семантику. При выключенном контроле скорости допуск прежний, сообщение нейтральное.

`yt_return_notify` вызывается только в успешной ветке Apply, после завершения перезапуска и
health-check: «YouTube работает на основном» содержит подтверждённую скорость. Отказ проверки,
анти-флап, ошибка генерации, неуспешное применение/откат не порождают успешного уведомления.
Убрано дублирующее сообщение YT kill-switch. Короткая сквозная проба YouTube больше не называет
канал работающим по скорости и при включённом контроле не сбрасывает растущий карантин;
подтверждённый возврат снимает его. Настройки и UI-ключи не добавлены. Тест:
`tests/test_yt_return_offline.py` (настоящие shell-блоки выбора/Apply, сеть и перезапуски подменены).

---
