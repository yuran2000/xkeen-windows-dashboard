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
`DEEP_RETURN_HOLD_MAX` (86400, потолок растущего карантина, v33), `DEEP_PROBE_PORT_PRI/YT/FGN` (10819–10821), `DEEP_PROBE_URL_PRI/YT/FGN` (cloudflare trace, youtube
`generate_204`, `api.telegram.org`), `KILL_FAIL_THRESHOLD`/`KILL_PASS_THRESHOLD` (3/2), `RECVQ_THRESHOLD`,
`RECVQ_HARD`, `STALL_FAIL_NEED`, `STALL_COOLDOWN`, `CONNTRACK_MIN`, `FD_DELTA_THRESHOLD`,
`FD_STALL_COOLDOWN`.

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
`AUTO-ROLLBACK`, `SKIP (анти-флап)`, `UDP-STALL`, `FD-STALL`, `AUTO-FIX`, `WARN`, `ERROR`. Долгие
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

Порядок разделов `.cur`: lock → помощники (`_probe_connected`, `probe_tag`, `_num`, `is_ru_tag`, `killswitch_decide`,
`_state_field`, `_log_every5`, `deep_probe_check`, `tg_alert`, `generate_routing`, `routing_changed`,
`validate_routing_xray`, `health_check`) → досыл TG → UDP-залип (v19) → `53` в `port_exclude.lst` (v21)
→ очистка `YT_GEOIP_CATEGORIES` (v23) → self-heal `socks-ai-check` (v24) → параметры v27/v29/v30 →
self-heal `socks-deep-*` (v29) → fd-залип (v22) → AI-проба (v24/v25) → State → Force-mode → проба
PRIMARY и state-машина (глухота `DPRI_DEAF` форсирует down) → AI → YT → FOREIGN (kill-switch +
цепочки) → Apply (`generate_routing <default> <ai> <yt> <foreign> <ipv6>` → `routing_changed` →
анти-флап → `validate_routing_xray` → `mv` → `xkeen -restart` → `health_check` → `.lastgood` или
`AUTO-ROLLBACK`) → обход FAILOVER → Save state.

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

---
