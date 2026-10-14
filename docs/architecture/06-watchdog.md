# §6. watchdog: панель ↔ config ↔ watchdog.sh ↔ state

> Часть документа [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) (вход и карта — там). Номера разделов `§` —
> общие для всех файлов: §1–3, 5, 8–12 — в главном файле, §4 — `04-routes.md`, §6 — `06-watchdog.md`,
> §7 — `07-chains.md`. Правила обновления — §12: правка кода и этого файла — в одном коммите.

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

Тики сериализует `/tmp/xkeen_watchdog.lock` (v35 — атомарно, noclobber), сохранения — `_WATCHDOG_CFG_LOCK`.
Запись = внеочередной тик ~30 с → связанные изменения писать **одним** вызовом. Изменения, требующие
рестарта xkeen (удаление подписки), — через режим применения (§6.7), а не записью живых файлов.

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
`DEEP_RETURN_HOLD_MAX` (86400, потолок растущего карантина, v33), `DEEP_PROBE_PORT_PRI/YT/FGN` (10819–10821), `DEEP_PROBE_URL_PRI/YT/FGN` (cloudflare trace, youtube
`generate_204`, `api.telegram.org`), `KILL_FAIL_THRESHOLD`/`KILL_PASS_THRESHOLD` (3/2), `RECVQ_THRESHOLD`,
`RECVQ_HARD`, `STALL_FAIL_NEED`, `STALL_COOLDOWN`, `CONNTRACK_MIN`, `FD_DELTA_THRESHOLD`,
`FD_STALL_COOLDOWN`, v37: `XRAY_BUSY_ENABLE` (1), `XRAY_BUSY_CPU` (50, % всех ядер), `XRAY_BUSY_KBIT_PER_PCT` (50),
`XRAY_BUSY_TICKS` (2), v38: `XRAY_BUSY_CALM_TICKS` (2), `XRAY_BUSY_REMIND_TICKS` (10), `XRAY_BUSY_TG_COOLDOWN` (1800).

### 6.3. watchdog.state — полный перечень полей

Одна строка, пишется в конце тика (`# ---------- Save state ----------`):

```
<state> <counter> <default_tag> EFFECTIVE_AI= EFFECTIVE_YT= EFFECTIVE_FOREIGN= LAST_FULL_PROBE= PREV_ALIVE=
LAST_BAD_MD5= AI_BLOCKED= AI_KCTR= YT_BLOCKED= YT_KCTR= FOREIGN_BLOCKED= FOREIGN_KCTR= AI_EFF=
AI_CMISS= AI_SW_TS= AI_LEFT_TS= YT_EFF= YT_CMISS= YT_SW_TS= FGN_EFF= FGN_CMISS= FGN_SW_TS=
DPRI_CTR= DPRI_TS= DPRI_DEAF= DPRI_LEFT= DPRI_TAG= DYT_CTR= DYT_TS= DYT_DEAF= DYT_LEFT= DYT_TAG=
DFGN_CTR= DFGN_TS= DFGN_DEAF= DFGN_LEFT= DFGN_TAG=
+ v31: D{PRI,YT,FGN}_RES= PRI_MAIN= YT_MAIN= FGN_MAIN= DPRI_FSINCE= PRI_NOALT= DYT_NOALT= DFGN_NOALT=
+ v33: D{PRI,YT,FGN}_QN= D{PRI,YT,FGN}_CHK= D{PRI,YT,FGN}_HOLD=
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
| `DEEP_CTR`, `DEEP_TS` | legacy v27; ✅ с v34 не пишутся (никто не читал) |
| `AI_/YT_/FGN_CMISS`, `…_SW_TS`, `AI_LEFT_TS` | пустые круги цепочки; время смены канала; уход AI по глухоте |
| `D{PRI,YT,FGN}_CTR/_TS/_DEAF/_LEFT/_TAG` | сквозная проба роли: пустых подряд; время пробы; латч глухоты; время ухода по глухоте; тег замера |
| `D{PRI,YT,FGN}_RES` (v31) | итог последнего замера: `ok` / `fail` / `none` (замера на этом теге не было, служебный тег, проба выключена) |
| `PRI_MAIN`, `YT_MAIN`, `FGN_MAIN` (v31) | основной тег роли на прошлом тике: если в настройках сменили основной — карантин `D*_LEFT` обнуляется, лог «сменён в настройках (A → B)», без TG «вернулся» |
| `D{PRI,YT,FGN}_QN` (v33) | уходов с основного по глухоте подряд без успешной сквозной пробы на нём между ними; растит карантин (`_deep_hold`); сбрасывается успешной пробой на основном и сменой основного в настройках |
| `D{PRI,YT,FGN}_CHK` (v33) | `1` — роль вернулась на основной после такого ухода и ждёт сквозной проверки: замеры раз в минуту, а не раз в `DEEP_PROBE_INTERVAL`; снимается первым успехом («✅ … проверен») или признанием глухоты |
| `D{PRI,YT,FGN}_HOLD` (v33) | действующий срок карантина роли, с: `DEEP_RETURN_HOLD`, ×2 за каждый повторный уход, потолок `DEEP_RETURN_HOLD_MAX`; только для панели |
| `PRI_NOALT`, `DYT_NOALT`, `DFGN_NOALT`, `DPRI_FSINCE` (v31) | «предупреждение о том, что уходить некуда, уже отправлено» (один TG за эпизод); с какого момента форсируется failover глухого основного (лог раз в 5 мин) |

Имена не симметричны (kill-switch «Зарубежных» — `FOREIGN_*`, прочее — `FGN_*`/`DFGN_*`) — брать из
строки записи в `.cur`. Разбор только по токенам (`split()` + `split("=", 1)`): `EFFECTIVE_AI=` и
`AI_EFF=` — разные ключи. `watchdog.aiprobe` — `ts|country|last_alert|empty|stage`.

Метки `watchdog.log`: `SWITCH`, `FAILOVER-HEALTH`, `AI-`/`YT-`/`FOREIGN-FAILOVER` (+ `-RETURN`),
`…-KILL-SWITCH` (+ `-CLEAR`), `DEEP-PROBE` (`DEAF`, `RECOVER`), `XRAY-TEST FAIL`, `HEALTH FAIL`,
`AUTO-ROLLBACK`, `SKIP (анти-флап)`, `UDP-STALL`, `FD-STALL`, `XRAY-BUSY` (+ `BLAME`, v37), `AUTO-FIX`, `WARN`, `ERROR`. Долгие
состояния пишутся раз в 5 мин (`_log_every5`), иначе окно «последние 30 строк» забьётся.

### 6.4. Как панель показывает состояние

`keenetic_get_watchdog(deep_hold_sec)` — один SSH (`cat watchdog.state; echo '=AIPROBE='; cat
watchdog.aiprobe`) + `keenetic_tail_log(…, n=30)` → `state`, `counter`, `current_default`,
`effective_ai`, `effective_yt`, `effective_foreign`, `last_bad_md5`, `log`, `ai_probe` (`{ts, country,
empty, stage, age_min}`/`None`), `fields` (все `KEY=VAL`), `deep` (`{pri, yt, fgn}`: `{ctr, ts, deaf,
left, tag, age_min, hold_left_min, res, qn, chk}` или `None` у роли без полей — старый watchdog;
`hold_left_min` считается от `D*_HOLD`, у сторожа до v33 — от `deep_hold_sec`), `fgn_eff`, `yt_eff`,
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

Порядок разделов `.cur`: lock (`_wd_lock_try`; режим применения — пролог §6.7) → `. watchdog.config` →
`_xk_restart` / `_xk_busy` / `_bg_restart_or_defer` → помощники (`_probe_connected`, `probe_tag`, `_num`, `is_ru_tag`, `killswitch_decide`,
`_state_field`, `_log_every5`, `xray_busy_eval`, `deep_probe_check`, `tg_alert`, `generate_routing`, `routing_changed`,
`validate_routing_xray`, `health_check`) → досыл TG → UDP-залип (v19) → `53` в `port_exclude.lst` (v21)
→ очистка `YT_GEOIP_CATEGORIES` (v23) → self-heal `socks-ai-check` (v24) → параметры v27/v29/v30 →
self-heal `socks-deep-*` (v29) → fd-залип (v22) → перегрузка xray (v37, `xray_busy_eval`) → AI-проба (v24/v25) → State → Force-mode → проба
PRIMARY и state-машина (глухота `DPRI_DEAF` форсирует down) → AI → YT → FOREIGN (kill-switch +
цепочки) → Apply (`generate_routing <default> <ai> <yt> <foreign> <ipv6>` → `routing_changed` →
анти-флап → `validate_routing_xray` → `mv` → `_xk_restart` → `health_check` → `.lastgood` или
`AUTO-ROLLBACK`) → итог режима применения (§6.7) → обход FAILOVER → Save state.

**Перегрузка xray (v37, края — v38).** `xray_busy_eval` между тиками меряет долю процессора процесса xray от
всех ядер (`/proc/<pid>/stat` против строки `cpu` в `/proc/stat` — не зависит от HZ и числа ядер) и трафик
интерфейса маршрута по умолчанию с маской 0 и наименьшей метрикой (`/proc/net/route` → `/proc/net/dev`; нет
маршрута — самый нагруженный, кроме `lo`; только `lo` — замера нет); арифметика в awk (счётчики не помещаются в
32 бита ash). Перегружен = доля ≥ `XRAY_BUSY_CPU` и трафик < доля × `XRAY_BUSY_KBIT_PER_PCT` кбит/с (рабочая
нагрузка — сотни кбит/с на процент, холостая — единицы). Файл эпизода `/tmp/xkeen_watchdog.xraybusy` — строго
14 полей `ts pid xray_j total_j wan_bytes busy_n blamed flags calm_pri calm_ai calm_yt calm_fgn calm_n tg_ts`
(иначе, в т. ч. формат v37, — как нет файла): `busy_n` — перегруженных подряд, `blamed` 0/1/2 (искали
виновника / было напоминание), `flags` бит 1 — «🔥» эпизода отправлен, бит 2 — виновник назван; `calm_*` —
каналы ролей на последнем замере ВНЕ эпизода (`-` — нет). Эпизод открыт, пока `busy_n`/`blamed`/`calm_n` не нули;
закрывают `XRAY_BUSY_CALM_TICKS` спокойных подряд (гистерезис); до обвинения спокойный замер обнуляет `busy_n`
(виноватого — только по подряд идущим). Пока эпизод открыт (`XRAY_BUSY=1`): `deep_probe_check` не меряет и
счётчики не трогает, AI-проба не считает пустоту; на тике открытия (`XRAY_BUSY_OPENED=1`) проба отменяет ровно
одну свою пустую попытку, если та пришлась на измеренное окно (`DP_TS ≥ XRAY_BUSY_WIN`): замер ретроспективен,
эта минута уже была перегружена. `XRAY_BUSY_TICKS` перегруженных подряд — виноватыми считаются роли PRI/YT/FGN,
чей канал сменился со спокойного снимка (`XRAY_BUSY_BLAME` = `PRI=тег YT=тег`, один раз за эпизод):
`deep_probe_check` сразу ставит им `DP_DEAF=1` — дальше обычный уход по цепочке (растущий карантин — только
если виноват основной роли). TG: обвинение с действием («🔥 … признаю X неисправным — уйду, если есть куда») —
всегда; «только AI» / «не менялись» / «снимка нет» — не чаще `XRAY_BUSY_TG_COOLDOWN`; перегрузка держится ещё
`XRAY_BUSY_REMIND_TICKS` после обвинения — одно напоминание (после обвинения с действием — мимо кулдауна);
«✅ xray разгрузился» — только если «🔥» эпизода был отправлен. Края: внеочередной тик того же xray (< 20 с,
«Сохранить») — замера нет, база не сдвигается; рестарт xray / нечитаемый замер / счётчик назад — решения нет, но
открытый эпизод держит `XRAY_BUSY=1`; рестарт без обвинения — `busy_n` с нуля; разрыв > 600 с / часы назад —
эпизод и снимок забываются при любом pid; xray не запущен — `XRAY_BUSY=0`. Режим применения не оценивает.
Ограничения: виновник — по совпадению во времени (законно переключённая в том же окне роль тоже будет
обвинена); узел, начавший грузить не в первую минуту, попадёт в снимок как «спокойный». Повод — 14.10: ws-узел
в роли YouTube, xray 60–90 % при 0,2–0,3 Мбит/с, исправные Сервер_1/2/3 по очереди «глухие», 48 рестартов; края —
офлайн-проверки граничных случаев.
Тест — `tests/test_xray_busy.py`.

**Проба трафика (v36).** `traffic_check` — curl через СВОЙ вход `socks-health` (127.0.0.1:`HEALTH_PROBE_PORT`
10822; заводит self-heal, правило → `direct` ставит `generate_routing`) на `TRAFFIC_PROBE_URLS`: проверяется сам
xray, не VPN-узлы (упавший VPS ложного «xray сломан» не даёт). Не прошло — запрос мимо xray: роутер выходит, а
xray нет → 1; не выходит и роутер (нет интернета), нет входа/правила, curl не запускается (127) → 0. Обычный тик
после удачного рестарта: `_traffic_ok_or_heal` — один лечебный рестарт + TG, не чаще `TRAFFIC_HEAL_COOLDOWN`
(1800 с, `watchdog.trafficheal`), без отката routing. Режим применения: `_apply_health` (health → traffic,
причина в `_APPLY_HREASON`) — провал = откат файлов панели (`reason=traffic`).

**`_xk_restart` (v35).** XKeen 2.0 запускает `xkeen -restart` без терминала (cron, ssh, скрипт) **в фоне**
(`start-stop-daemon`, лог `/opt/var/log/xkeen-detached.log`) и сразу возвращает управление; синхронно — только с
`XKEEN_FOREGROUND=1`. До v35 `health_check` сразу после рестарта мерил ещё СТАРЫЙ xray. `_xk_restart` ждёт чужой
start/stop/restart (`_xk_busy`, ≤ 60 с), делает свой и ждёт его конца и нового pid xray (≤ 90 с; нет — код 1, и
применение считается неудачным). Готово (v38) = в `pidof` есть pid, которого не было, И хотя бы один старый ушёл
(старых не было — достаточно нового): пока старый xray не ушёл, `pidof` даёт два («новый старый»); посторонний
постоянный xray не мешает. Фоновые рестарты «и выход из тика» (self-heal inbound'ов v24/v29, правило v24,
v21, v23) — через `_bg_restart_or_defer`.

**Карантин возврата (v29, растущий — v33).** Ушли с основного роли по глухоте (`D*_DEAF`) — `D*_LEFT` = время
ухода, `D*_QN` += 1; пока не истёк срок `_deep_hold(D*_QN)` (`DEEP_RETURN_HOLD`, ×2 за каждый повторный
уход, потолок `DEEP_RETURN_HOLD_MAX`), роль на основной не возвращается, даже если он отвечает по TCP.
После возврата `deep_probe_check` (7-й аргумент — основной тег роли) ставит `D*_CHK=1` и меряет через
минуту, пустые пробы тоже повторяет через минуту: глухой узел признаётся за ~2 мин. Алерт возврата
после карантина — «↩️ … проверяю насквозь», «✅ … проверен» шлёт проба; успех на основном обнуляет
`D*_QN`. Тест — `tests/test_deep_quarantine.py`.

**`probe_tag` (TCP-проба узла, v32).** curl на `https://host:port/` (или `PRIMARY_PROBE_URL`): код 7 →
мёртв; код 28 (таймаут) → мёртв **только** при `%{num_connects}` = 0 (`_probe_connected`); прочие коды
→ жив. Проба — не VLESS: Reality отдаёт её сайту-маскировке (`dest`), и сторонний `dest` может молчать
при живом узле (до v32 это давало ложные уходы на резерв и лишние `xkeen -restart`). 🔴
`%{time_connect}` как признак соединения не годится: при таймауте на этапе TLS curl пишет 0 и для
установленного TCP. Тест — `tests/test_probe_tag.py`.

Плейсхолдеры template: `__DEFAULT_TAG__`, `__BLOCK_RULE_BLOCK__`, `__DIRECT_RULE_BLOCK__`,
`__AI_RULE_BLOCK__`, `__YT_RULE_BLOCK__`, `__YT_RULE_GEOIP__`, `__FOREIGN_RULE_BLOCK__`,
`__FOREIGN_RULE_GEOIP__`, `__IPV6_RULE_BLOCK__` (пусто → правило не генерится). Правила для
`socks-ai-check` и `socks-deep-*` watchdog вставляет сам.

### 6.7. Режим применения (v35, `WD_APPLY=<id>`)

Запускает панель (`keenetic_apply_staged`, §3.5): `WD_APPLY=<id> [WD_APPLY_FORCE=1] nohup /opt/etc/xray/watchdog.sh`
(`WD_APPLY_FORCE=1`, v36 — рестарт и без подменённых файлов: restore, «Применить структуру»). Id — только
`[A-Za-z0-9_-]`. Файлы: подготовленные `<живой>.applynew.<id>` (список `APPLY_STAGE_LIST` = `watchdog.config`,
`04_outbounds.json`, routing-template — тот же, что `APPLY_STAGE_FILES` панели), манифест
`/tmp/xkeen_apply.<id>.manifest` (`<путь> <md5 | ->`), итог `/tmp/xkeen_apply.<id>.res`, копии `*.bak-apply-<id>`
(по 3 последних на файл, `_apply_prune_baks`; плюс живой `05_routing.json`).

1. Пролог: ждать lock до `WD_APPLY_WAIT` (150 с) → нет — `FAIL reason=lock`, свои `.applynew.<id>` убраны.
   Сверка md5 живых файлов с манифестом (`_md5_or_dash`) → расхождение — `FAIL reason=changed` (чужая правка
   не затирается); `sh -n` подготовленного config → `FAIL reason=badconfig`. Копии → подмена (`mv`); сбой —
   `FAIL reason=stage`. Ловушка `_apply_on_exit` (EXIT/INT/TERM/HUP): итога нет — откат и `FAIL reason=crash`
   (рестарт уже шёл — ещё один на возвращённых файлах, `restart=2`). Нет `watchdog.config` — выход после lock,
   итог пишет ловушка. Убитое `kill -9` / OOM применение подбирает обычный тик (блок перед досылом TG):
   манифест без итога и с копиями `*.bak-apply-<id>` → откат, рестарт, `FAIL reason=killed`; без копий (ещё
   ждёт lock) — не трогается; брошенные > 60 мин `xkeen_apply.*` и `*.applynew.*` удаляются.
2. Тик идёт как обычно, кроме: AI-проба, UDP- и FD-залип не оцениваются (решит следующий тик); фоновые
   рестарты откладываются (`_APPLY_NEED=1`).
3. Итог после блока Apply: рестарт уже был (routing пересобран) — `OK routing=changed restart=1`; нужен
   (подменён `04_outbounds`, `_APPLY_NEED` или `WD_APPLY_FORCE`) — `xray_test_live` → `_xk_restart` + `health_check`
   + `traffic_check` (`_apply_health`) →
   `OK routing=same restart=1`; не нужен (только config) — `OK routing=same restart=0`. Кандидат отклонён
   (`antiflap` / `blocked` / `generate` / `routing_mv` / `xraytest`) — откат файлов, без рестарта
   (`FAIL … restart=0`; был отложенный — один рестарт, `deferred=1`); `health_check` провален — откат файлов и
   routing, откатный рестарт, `ROLLBACK reason=health|traffic restart=2 after=ok|fail` (при `after=fail`
   третьего рестарта нет — только алерт); файл, изменённый ПОСЛЕ подмены (md5 в `/tmp/xkeen_apply.<id>.swapped`),
   не откатывается. Force без подменённых файлов: провал — `FAIL … nofiles=1` (откатывать нечего), при
   пересборке routing — откат к `.lastgood`, как в обычном тике. FAIL / ROLLBACK выходят
   **до Save state** (состояние от отвергнутых файлов не пишется).

Строки лога — `APPLY <id>: …`. Тест — `tests/test_apply_once_offline.py`.

---
