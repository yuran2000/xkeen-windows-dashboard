# -*- coding: utf-8 -*-
"""Офлайн-прогон UI watchdog v29/v30/v31 на моках SSH (без роутера): цепочка резерва
«Зарубежных сервисов» (kind=foreign, use_common/allow_ru, RU-предупреждения по действующей
цепочке), настройки сквозной пробы (/api/keenetic/watchdog-settings: валидация диапазонов,
не-объект → 400, отсутствующее поле не трогается), дефолты DEEP_* только для актуального
сторожа, парсинг полей state v29/v31 (fields/deep/res) и рендер /xkeen обоими путями
(quick-fail и с моками роутера).

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_v29_ui_offline.py
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402

# ---------- мок «роутера» ----------
NOW = int(time.time())
ROUTER = {
    "config": {
        "PRIMARY_TAG": "vless-reality", "FAILOVER_TAGS": "nl-1 de-1 ru-msk",
        "AI_TAG": "nl-1", "YT_TAG": "ru-msk", "FOREIGN_TAG": "nl-1",
        "FOREIGN_FAILOVER_TAGS": "de-1 ee-1", "FOREIGN_CHAIN_ALLOW_RU": "0", "FOREIGN_CHAIN_USE_COMMON": "0",
        "DEEP_PROBE_ENABLE": "1", "DEEP_PROBE_INTERVAL": "180", "DEEP_PROBE_THRESHOLD": "2", "DEEP_RETURN_HOLD": "1800",
        "FORCE_MODE": "auto",
    },
    "state": ("primary 0 vless-reality EFFECTIVE_AI=nl-1 EFFECTIVE_YT=ru-msk EFFECTIVE_FOREIGN=de-1 LAST_BAD_MD5= "
              "AI_EFF=nl-1 YT_EFF=ru-msk FGN_EFF=de-1 "
              f"DPRI_CTR=0 DPRI_TS={NOW - 120} DPRI_DEAF=0 DPRI_LEFT=0 DPRI_TAG=vless-reality "
              f"DYT_CTR=1 DYT_TS={NOW - 60} DYT_DEAF=0 DYT_LEFT=0 DYT_TAG=ru-msk "
              f"DFGN_CTR=2 DFGN_TS={NOW - 30} DFGN_DEAF=1 DFGN_LEFT={NOW - 600} DFGN_TAG=nl-1 "
              "PRI_MAIN=vless-reality YT_MAIN=ru-msk FGN_MAIN=nl-1 DPRI_RES=ok DYT_RES=none DFGN_RES=fail "
              "PRI_NOALT=0 DPRI_FSINCE=0 DYT_NOALT=0 DFGN_NOALT=0"),
    "written": [],
}
OUTBOUNDS = {"outbounds": [
    {"tag": "direct", "protocol": "freedom", "settings": {}},
    {"tag": "block", "protocol": "blackhole", "settings": {}},
] + [
    {"tag": t, "protocol": "vless",
     "settings": {"vnext": [{"address": f"{t}.example.net", "port": 443, "users": [{"id": "u"}]}]},
     "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.example.com"}}}
    for t in ("vless-reality", "nl-1", "de-1", "ee-1", "ru-msk")
]}
ROUTING = {"routing": {"rules": [{"inboundTag": ["redirect", "tproxy"], "outboundTag": "vless-reality"}]}}
ssh_calls = []


def _cfg_text():
    return "\n".join(f'{k}="{v}"' for k, v in ROUTER["config"].items()) + "\n"


def fake_ssh(cmd, stdin_data=None, timeout=None, **kw):
    ssh_calls.append(cmd)
    ok = lambda out="": {"ok": True, "stdout": out, "stderr": "", "code": 0}
    if "watchdog.config; else echo __WDCFG_ABSENT__" in cmd:
        return ok(_cfg_text())
    if cmd.startswith("cat > /opt/etc/xray/watchdog.config"):
        ROUTER["written"].append(stdin_data)
        ROUTER["config"] = D._parse_watchdog_config(stdin_data)
        return ok("")
    if cmd.startswith("cat ") and "watchdog.state" in cmd:
        return ok(ROUTER["state"] + "\n=AIPROBE=\n" + f"{NOW - 300}|NL||0|0\n")
    if cmd.startswith("cat ") and cmd.endswith("04_outbounds.json"):
        return ok(json.dumps(OUTBOUNDS))
    if cmd.startswith("cat ") and cmd.endswith("05_routing.json"):
        return ok(json.dumps(ROUTING))
    if cmd.startswith("tail -n"):
        return ok("2026-10-02 12:00:00 DEEP-PROBE FGN: узел nl-1 глух\n")
    if cmd.startswith("head -"):
        return ok("# XKeen failover watchdog v30 (2026-10-02)\n")
    return ok("")


D.keenetic_ssh = fake_ssh
D.keenetic_ensure_watchdog_current = lambda *a, **kw: {"action": "current"}
D.keenetic_read_meta = lambda *a, **kw: {}
D.keenetic_read_sub_meta = lambda *a, **kw: {}
D.app.config["TESTING"] = True
client = D.app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
    s["username"] = "test"

fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}: {detail}")


# ---------- targets: новые ключи ----------
t = D.keenetic_get_watchdog_targets()
check("targets foreign chain", t["foreign_failover_tags"] == ["de-1", "ee-1"], str(t.get("foreign_failover_tags")))
check("targets foreign allow_ru False", t["foreign_chain_allow_ru"] is False)
check("targets foreign use_common False", t["foreign_chain_use_common"] is False)
check("targets deep ints", (t["deep_probe_interval"], t["deep_probe_threshold"], t["deep_return_hold"]) == (180, 2, 1800), str(t))
check("targets deep enable", t["deep_probe_enable"] is True)
ROUTER["config"]["DEEP_PROBE_INTERVAL"] = "abc"
check("targets deep sanitize → default", D.keenetic_get_watchdog_targets()["deep_probe_interval"] == 180)
ROUTER["config"]["DEEP_PROBE_INTERVAL"] = "180"

# quick-fail литерал xkeen_page обязан содержать ВСЕ ключи targets (иначе 500 без роутера)
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py"), encoding="utf-8").read()
qf = re.search(r"if not is_keenetic_reachable\(\):(.*?)\n    else:", src, re.S).group(1)
qf_keys = set(re.findall(r'^\s+"([a-z0-9_]+)":', qf, re.M))
check("quick-fail targets ⊇ get_watchdog_targets", set(t.keys()) <= qf_keys, str(sorted(set(t.keys()) - qf_keys)))

# ---------- state v29: fields / deep ----------
w = D.keenetic_get_watchdog(deep_hold_sec=1800)
check("wd fields parsed", w["fields"].get("DFGN_DEAF") == "1" and w["fields"].get("EFFECTIVE_FOREIGN") == "de-1", str(w["fields"])[:200])
check("wd effective_foreign", w["effective_foreign"] == "de-1")
check("wd fgn_eff", w["fgn_eff"] == "de-1" and w["yt_eff"] == "ru-msk" and w["ai_eff"] == "nl-1")
check("wd deep pri ok", w["deep"]["pri"]["deaf"] is False and w["deep"]["pri"]["ctr"] == 0 and w["deep"]["pri"]["age_min"] == 2, str(w["deep"]["pri"]))
check("wd deep yt ctr", w["deep"]["yt"]["ctr"] == 1 and w["deep"]["yt"]["hold_left_min"] is None, str(w["deep"]["yt"]))
check("wd deep fgn deaf+hold", w["deep"]["fgn"]["deaf"] is True and w["deep"]["fgn"]["tag"] == "nl-1" and 19 <= w["deep"]["fgn"]["hold_left_min"] <= 20, str(w["deep"]["fgn"]))
check("wd quick-fail keys ⊇ get_watchdog", set(w.keys()) <= qf_keys, str(sorted(set(w.keys()) - qf_keys)))
# watchdog v31: итог последнего замера (D*_RES) и новые служебные поля не ломают разбор
check("wd deep res parsed", (w["deep"]["pri"]["res"], w["deep"]["yt"]["res"], w["deep"]["fgn"]["res"]) == ("ok", "none", "fail"), str(w["deep"]))
check("wd v31 fields kept", w["fields"].get("YT_MAIN") == "ru-msk" and w["fields"].get("PRI_NOALT") == "0", str(w["fields"])[:300])
_st = ROUTER["state"]
ROUTER["state"] = _st.replace("DYT_RES=none", "DYT_RES=garbage")
check("wd deep res garbage → None", D.keenetic_get_watchdog()["deep"]["yt"]["res"] is None)
ROUTER["state"] = _st.replace(" DPRI_RES=ok DYT_RES=none DFGN_RES=fail", "")
check("wd deep res absent (v30) → None", D.keenetic_get_watchdog()["deep"]["pri"]["res"] is None)
ROUTER["state"] = _st
# старый watchdog (≤ v28): нет полей D*_ → deep[role] = None, ничего не падает
_old = ROUTER["state"]
ROUTER["state"] = "primary 0 vless-reality EFFECTIVE_AI=nl-1 EFFECTIVE_YT=ru-msk EFFECTIVE_FOREIGN=nl-1 LAST_BAD_MD5="
w2 = D.keenetic_get_watchdog()
check("wd old watchdog → deep None", w2["deep"] == {"pri": None, "yt": None, "fgn": None} and w2["fgn_eff"] is None, str(w2["deep"]))
ROUTER["state"] = _old

# ---------- /xkeen рендер: quick-fail (роутер недоступен) ----------
D.is_keenetic_reachable = lambda *a, **kw: False
r = client.get("/xkeen")
html = r.get_data(as_text=True)
check("render quick-fail 200", r.status_code == 200, str(r.status_code))
check("render quick-fail no error", "Ошибка при рендеринге" not in html, html[:500])
check("render quick-fail has foreign chain box", 'id="foreign-chain-list"' in html and 'id="foreign-chain-use-common"' in html)
check("render quick-fail deep form", 'id="deep-probe-form"' in html and "const WATCHDOG_DEEP = null;" in html)

# ---------- /xkeen рендер: с моками роутера ----------
D.is_keenetic_reachable = lambda *a, **kw: True
r = client.get("/xkeen")
html = r.get_data(as_text=True)
check("render mock 200", r.status_code == 200, str(r.status_code))
check("render mock no error", "Ошибка при рендеринге" not in html, html[:500])
check("render mock foreign chain badge", "📱 резерв Зарубежных №1" in html, "badge missing")
check("render mock killswitch chain branch", "работает резерв из цепочки — de-1 (#1)" in html, "ks branch missing")
check("render mock WATCHDOG_DEEP json", re.search(r'const WATCHDOG_DEEP = \{.*"fgn": \{.*"deaf": true', html) is not None)
check("render mock CHAIN_STATE.foreign", re.search(r'foreign: \{ tags: \["de-1", "ee-1"\]', html) is not None)
check("render mock WATCHDOG_FAILOVER_TAGS", 'const WATCHDOG_FAILOVER_TAGS = ["nl-1", "de-1", "ru-msk"];' in html)
check("render mock deep inputs", 'id="deep-probe-interval" min="60" max="900" step="30" value="180"' in html and 'id="deep-return-hold-min"' in html and 'value="30"' in html)
check("render mock help anchors", 'id="help-watchdog"' in html and 'id="help-channels"' in html and 'id="help-tg-alerts"' in html)
check("render mock no Watchdog v6", "Watchdog v6" not in html)
check("render mock res in WATCHDOG_DEEP", re.search(r'const WATCHDOG_DEEP = \{.*"yt": \{[^}]*"res": "none"', html) is not None)
check("render mock DEEP_PROBE_ENABLE const", "const DEEP_PROBE_ENABLE = true;" in html)
_fn = re.search(r"function deepProbeNote\(roleKey\) \{.*?\n\}", html, re.S)
check("render mock deepProbeNote has «не измеряется»", _fn is not None and "⚪ не измеряется" in _fn.group(0) and "d.res === 'none'" in _fn.group(0))
check("render mock deepProbeNote reserve hint conditional", _fn is not None and "deepRoleHasReserve(roleKey)" in _fn.group(0)
      and "Резерва у роли нет" in _fn.group(0))
_cp = re.search(r"function chainPositionOf\(roleLabel, tag\) \{.*?\n\}", html, re.S)
check("render chainPositionOf by saved useCommon", _cp is not None and "st.useCommon" in _cp.group(0) and "chainUseCommon()" not in _cp.group(0))
_cr = re.search(r"function chainRender\(kind\) \{.*?\n\}", html, re.S)
check("render chainRender: common view AFTER innerHTML", _cr is not None
      and _cr.group(0).rfind("chainRenderCommonView(allowRu)") > _cr.group(0).rfind("box.innerHTML = html;"))
ROUTER["config"]["DEEP_PROBE_ENABLE"] = "0"
check("render DEEP_PROBE_ENABLE false", "const DEEP_PROBE_ENABLE = false;" in client.get("/xkeen").get_data(as_text=True))
ROUTER["config"]["DEEP_PROBE_ENABLE"] = "1"
# use_common=1 → в статусе kill-switch ищется по общей цепочке
ROUTER["config"]["FOREIGN_CHAIN_USE_COMMON"] = "1"
html = client.get("/xkeen").get_data(as_text=True)
check("render use_common ks branch (common #2)", "работает резерв из цепочки (общей) — de-1 (#2)" in html, "common branch missing")
check("render use_common chain-box open", re.search(r'<details class="chain-box" data-xk-keep-inline open [^>]*>\s*<summary[^>]*>⛓️ Цепочка резерва <span[^>]*>— куда переводить «Зарубежные сервисы»', html) is not None)
ROUTER["config"]["FOREIGN_CHAIN_USE_COMMON"] = "0"

# ---------- /api/xkeen/set-channel-chain kind=foreign ----------
ROUTER["written"].clear()
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "ee-1,de-1,ru-msk", "allow_ru": "0", "use_common": "0"}).get_json()
check("chain foreign ok", j.get("ok"), str(j)[:300])
check("chain foreign written", ROUTER["config"].get("FOREIGN_FAILOVER_TAGS") == "ee-1 de-1 ru-msk", ROUTER["config"].get("FOREIGN_FAILOVER_TAGS"))
check("chain foreign allow_ru written 0", ROUTER["config"].get("FOREIGN_CHAIN_ALLOW_RU") == "0")
check("chain foreign use_common written 0", ROUTER["config"].get("FOREIGN_CHAIN_USE_COMMON") == "0")
check("chain foreign RU note", any("бесполезен" in n for n in j.get("notes", [])), str(j.get("notes")))
check("chain foreign label", "Зарубежные сервисы" in (j.get("stdout") or ""), j.get("stdout"))
# без allow_ru/use_common — текущие значения не трогаются (removeFromChannelChain)
ROUTER["config"]["FOREIGN_CHAIN_ALLOW_RU"] = "1"
ROUTER["config"]["FOREIGN_CHAIN_USE_COMMON"] = "1"
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "ee-1"}).get_json()
check("chain foreign partial ok", j.get("ok"), str(j)[:300])
check("chain foreign partial keeps flags", ROUTER["config"].get("FOREIGN_CHAIN_ALLOW_RU") == "1" and ROUTER["config"].get("FOREIGN_CHAIN_USE_COMMON") == "1", str(ROUTER["config"]))
check("chain foreign use_common note", j.get("use_common") is True and any("общая цепочка" in n for n in j.get("notes", [])), str(j.get("notes")))
# P5: при общей цепочке RU-предупреждение — по FAILOVER_TAGS (там ru-msk), а не по собственному списку
ROUTER["config"]["FOREIGN_CHAIN_ALLOW_RU"] = "0"
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "ee-1", "use_common": "1"}).get_json()
check("chain common RU note from FAILOVER", j.get("ok") and any("из общей цепочки" in n and "ru-msk" in n for n in j.get("notes", [])), str(j.get("notes")))
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "ru-msk", "use_common": "1", "allow_ru": "0"}).get_json()
_skip = [n for n in j.get("notes", []) if "ПРОПУСТИТ" in n]
check("chain common: RU skip note only about the common chain", j.get("ok") and len(_skip) == 1 and "из общей цепочки" in _skip[0], str(j.get("notes")))
ROUTER["config"]["FAILOVER_TAGS"] = "nl-1 de-1"
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "ru-msk", "use_common": "1"}).get_json()
check("chain common: no RU in FAILOVER → no RU note", j.get("ok") and not any("ПРОПУСТИТ" in n for n in j.get("notes", [])), str(j.get("notes")))
ROUTER["config"]["FAILOVER_TAGS"] = "nl-1 de-1 ru-msk"
ROUTER["config"]["FOREIGN_CHAIN_ALLOW_RU"] = "1"
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "ee-1"}).get_json()
check("chain foreign stdout own list", "собственный список" in (j.get("stdout") or ""), j.get("stdout"))
# use_common → выключить
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "ee-1", "use_common": "0"}).get_json()
check("chain foreign use_common off", j.get("ok") and ROUTER["config"].get("FOREIGN_CHAIN_USE_COMMON") == "0" and j.get("use_common") is False, str(j)[:200])
# основной тег выкидывается из цепочки
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "nl-1,ee-1"}).get_json()
check("chain foreign drops main", j.get("ok") and j.get("chain") == ["ee-1"], str(j)[:200])
# yt/ai не получили use_common-ключ
ROUTER["config"].pop("FOREIGN_CHAIN_USE_COMMON", None)
j = client.post("/api/xkeen/set-channel-chain", data={"kind": "yt", "tags": "de-1", "use_common": "1"}).get_json()
check("chain yt ignores use_common", j.get("ok") and ROUTER["config"].get("FOREIGN_CHAIN_USE_COMMON") == "0", str(ROUTER["config"].get("FOREIGN_CHAIN_USE_COMMON")))
check("chain bad kind", not client.post("/api/xkeen/set-channel-chain", data={"kind": "ipv6", "tags": ""}).get_json().get("ok"))
check("chain direct refused", not client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "direct"}).get_json().get("ok"))
check("chain unknown tag refused", not client.post("/api/xkeen/set-channel-chain", data={"kind": "foreign", "tags": "no-such"}).get_json().get("ok"))

# ---------- /api/keenetic/watchdog-settings ----------
j = client.get("/api/keenetic/watchdog-settings").get_json()
check("wds get", j.get("ok") and j["deep_probe_interval"] == 180 and j["deep_probe_threshold"] == 2 and j["deep_return_hold_min"] == 30 and j["deep_probe_enable"] is True, str(j))
j = client.post("/api/keenetic/watchdog-settings", json={"deep_probe_enable": False, "deep_probe_interval": 300, "deep_probe_threshold": 3, "deep_return_hold_min": 45}).get_json()
check("wds post ok", j.get("ok"), str(j))
check("wds post written", (ROUTER["config"].get("DEEP_PROBE_ENABLE"), ROUTER["config"].get("DEEP_PROBE_INTERVAL"),
                           ROUTER["config"].get("DEEP_PROBE_THRESHOLD"), ROUTER["config"].get("DEEP_RETURN_HOLD")) == ("0", "300", "3", "2700"), str(ROUTER["config"]))
for bad in ({"deep_probe_interval": 59, "deep_probe_threshold": 2, "deep_return_hold_min": 30},
            {"deep_probe_interval": 901, "deep_probe_threshold": 2, "deep_return_hold_min": 30},
            {"deep_probe_interval": 180, "deep_probe_threshold": 0, "deep_return_hold_min": 30},
            {"deep_probe_interval": 180, "deep_probe_threshold": 6, "deep_return_hold_min": 30},
            {"deep_probe_interval": 180, "deep_probe_threshold": 2, "deep_return_hold_min": 4},
            {"deep_probe_interval": 180, "deep_probe_threshold": 2, "deep_return_hold_min": 1441},
            {"deep_probe_interval": "abc", "deep_probe_threshold": 2, "deep_return_hold_min": 30},
            {"deep_probe_interval": None, "deep_probe_threshold": 2, "deep_return_hold_min": 30}):
    rr = client.post("/api/keenetic/watchdog-settings", json=dict(bad, deep_probe_enable=True))
    check("wds reject " + json.dumps(bad), rr.status_code == 400 and not rr.get_json().get("ok"), f"{rr.status_code} {rr.get_json()}")
check("wds still 300 after rejects", ROUTER["config"].get("DEEP_PROBE_INTERVAL") == "300")
# P3: тело не объект → 400; пустой объект → 400; конфиг не тронут
_nw = len(ROUTER["written"])
for body in ([1, 2], "deep_probe_enable", 5, None, {}):
    rr = client.post("/api/keenetic/watchdog-settings", json=body)
    check("wds non-dict/empty 400: " + json.dumps(body), rr.status_code == 400 and not (rr.get_json() or {}).get("ok"), f"{rr.status_code} {rr.get_data(as_text=True)[:200]}")
rr = client.post("/api/keenetic/watchdog-settings", data="{not json", content_type="application/json")
check("wds broken json 400", rr.status_code == 400, str(rr.status_code))
check("wds non-dict: nothing written", len(ROUTER["written"]) == _nw, str(len(ROUTER["written"]) - _nw))
# P3: отсутствующее поле — ключ не трогается (в т.ч. deep_probe_enable)
j = client.post("/api/keenetic/watchdog-settings", json={"deep_probe_threshold": 4}).get_json()
check("wds partial ok", j.get("ok"), str(j))
check("wds partial: only threshold changed", (ROUTER["config"].get("DEEP_PROBE_ENABLE"), ROUTER["config"].get("DEEP_PROBE_INTERVAL"),
      ROUTER["config"].get("DEEP_PROBE_THRESHOLD"), ROUTER["config"].get("DEEP_RETURN_HOLD")) == ("0", "300", "4", "2700"), str(ROUTER["config"]))
j = client.post("/api/keenetic/watchdog-settings", json={"deep_probe_enable": True}).get_json()
check("wds partial enable only", j.get("ok") and ROUTER["config"].get("DEEP_PROBE_ENABLE") == "1" and ROUTER["config"].get("DEEP_PROBE_INTERVAL") == "300", str(ROUTER["config"]))
upd, err = D._validate_deep_probe_settings({"deep_return_hold_min": 60})
check("validate partial → only DEEP_RETURN_HOLD", err is None and upd == {"DEEP_RETURN_HOLD": "3600"}, f"{upd} {err}")
check("validate non-dict → error", D._validate_deep_probe_settings([1])[0] is None and D._validate_deep_probe_settings("x")[1])

# P4: дефолты DEEP_* дописываются, ТОЛЬКО если сторож на роутере актуальный (current/upgraded)
for action, expect in (("skip", False), ("failed", False), ("current", True), ("upgraded", True)):
    for k in ("DEEP_PROBE_ENABLE", "DEEP_PROBE_INTERVAL", "DEEP_PROBE_THRESHOLD", "DEEP_RETURN_HOLD"):
        ROUTER["config"].pop(k, None)
    D.keenetic_ensure_watchdog_current = (lambda a: (lambda *x, **kw: {"action": a, "from": 28, "to": 31, "note": "test"}))(action)
    r_ = D.keenetic_write_watchdog_config({"FORCE_MODE": "auto"})
    _dv = [ROUTER["config"].get(k, "") for k in ("DEEP_PROBE_ENABLE", "DEEP_PROBE_INTERVAL", "DEEP_PROBE_THRESHOLD", "DEEP_RETURN_HOLD")]
    has = all(re.fullmatch(r"\d+", v or "") for v in _dv)
    none_ = not any(_dv)   # пусто = «не задано»: старый сторож возьмёт свой дефолт (у v27/v28 проба выключена)
    if not expect:
        ROUTER["written"].pop()   # намеренно без DEEP_* — не участвует в проверке «числа явно» ниже
    check(f"P4 deep defaults for sync={action}", r_.get("ok") and (has if expect else none_), str({k: v for k, v in ROUTER["config"].items() if k.startswith("DEEP")}))
D.keenetic_ensure_watchdog_current = lambda *a, **kw: {"action": "current"}
ROUTER["config"].update({"DEEP_PROBE_ENABLE": "0", "DEEP_PROBE_INTERVAL": "300", "DEEP_PROBE_THRESHOLD": "4", "DEEP_RETURN_HOLD": "2700"})
# все записанные config'и — числа явно, без пустых DEEP_*
for txt in ROUTER["written"]:
    for k in ("DEEP_PROBE_ENABLE", "DEEP_PROBE_INTERVAL", "DEEP_PROBE_THRESHOLD", "DEEP_RETURN_HOLD", "FOREIGN_CHAIN_ALLOW_RU", "FOREIGN_CHAIN_USE_COMMON"):
        m = re.search(rf'^{k}="(.*)"$', txt, re.M)
        check(f"written {k} numeric", m is not None and re.fullmatch(r"\d+", m.group(1) or ""), (m.group(0) if m else "missing")[:80])
check("keys_order has new keys in order", re.search(r'"FOREIGN_FAIL_BLOCK",\s*"FOREIGN_FAILOVER_TAGS", "FOREIGN_CHAIN_ALLOW_RU", "FOREIGN_CHAIN_USE_COMMON",', src) is not None
      and re.search(r'"PASS_THRESHOLD",\s*"DEEP_PROBE_ENABLE", "DEEP_PROBE_INTERVAL", "DEEP_PROBE_THRESHOLD", "DEEP_RETURN_HOLD",', src) is not None)
keys_in_default = set(re.findall(r"^([A-Z0-9_]+)=", D.DEFAULT_WATCHDOG_CONFIG, re.M))
check("DEFAULT_WATCHDOG_CONFIG new keys", {"FOREIGN_FAILOVER_TAGS", "FOREIGN_CHAIN_ALLOW_RU", "FOREIGN_CHAIN_USE_COMMON",
                                            "DEEP_PROBE_ENABLE", "DEEP_PROBE_INTERVAL", "DEEP_PROBE_THRESHOLD", "DEEP_RETURN_HOLD"} <= keys_in_default)

# ---------- защиты удаления: foreign-цепочка ----------
ROUTER["config"]["FOREIGN_FAILOVER_TAGS"] = "ee-1"
ROUTER["config"]["FOREIGN_CHAIN_USE_COMMON"] = "1"   # защищаем и при общей цепочке
r1 = D.keenetic_remove_outbound("ee-1")
check("remove protects foreign chain", not r1.get("ok") and "Зарубежные сервисы" in r1.get("stderr", ""), str(r1))
r2 = D.keenetic_remove_outbounds_bulk(["ee-1"])
check("bulk skips foreign chain", not r2.get("ok") and "Зарубежных" in str(r2.get("skipped", {}).get("ee-1", "")), str(r2))
plan = D._subscription_removal_plan("", [], D.keenetic_get_watchdog_targets(), {})
check("removal plan has foreign_chain_tail", "foreign_chain_tail" in plan, str(plan))

if fails:
    print("FAIL:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("test_v29_ui_offline: OK")
