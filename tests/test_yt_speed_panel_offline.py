# -*- coding: utf-8 -*-
"""Офлайн-тест панели для скорости канала YouTube (watchdog v41) на моках SSH, без роутера: ключи config
(`keys_order`, `DEFAULT_WATCHDOG_CONFIG`, дефолты записи), `targets.yt_speed_*`, разбор полей state
(`watchdog.yt_speed`), настройки через /api/keenetic/watchdog-settings (поля yt_speed_*: диапазоны, не тронутые
поля не пишутся), разовый замер /api/xkeen/yt-speed-test (`_parse_yt_speed_test`) и блок «⚡ Скорость канала»
на /xkeen обоими путями (quick-fail и с моками роутера).

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_yt_speed_panel_offline.py
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  | {extra}"))
    if not cond:
        fails += 1


NOW = int(time.time())
ROUTER = {
    "config": {
        "PRIMARY_TAG": "vless-reality", "FAILOVER_TAGS": "nl-1", "YT_TAG": "us-1", "YT_DOMAINS": "youtube.com",
        "YT_FAILOVER_TAGS": "srv-1 srv-3", "DEEP_PROBE_ENABLE": "1", "DEEP_PROBE_INTERVAL": "180",
        "DEEP_PROBE_THRESHOLD": "2", "DEEP_RETURN_HOLD": "1800", "FORCE_MODE": "auto",
        "YT_SPEED_ENABLE": "1", "YT_SPEED_MIN_KBPS": "400", "YT_SPEED_INTERVAL": "600",
    },
    "state": ("primary 0 vless-reality EFFECTIVE_YT=us-1 YT_EFF=us-1 "
              f"DYT_CTR=0 DYT_TS={NOW - 60} DYT_DEAF=0 DYT_LEFT=0 DYT_TAG=us-1 DYT_RES=ok "
              f"YT_SPD=11 YT_SPD_TS={NOW - 180} YT_SPD_TAG=us-1 YT_SPD_CTR=2 YT_SLOW=1 YT_SPD_DIR=1480 "
              f"YT_SLOWQ=🇸🇪_Sweden:{NOW - 900}"),
    "written": [],
    "speed_out": ('        "outboundTag": "us-1",\nYT 11264 165000 206\nDIR 1515520 2000000\n'),
}
OUTBOUNDS = {"outbounds": [
    {"tag": "direct", "protocol": "freedom", "settings": {}},
    {"tag": "block", "protocol": "blackhole", "settings": {}},
] + [
    {"tag": t, "protocol": "vless",
     "settings": {"vnext": [{"address": f"{t}.example.net", "port": 443, "users": [{"id": "u"}]}]},
     "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.example.com"}}}
    for t in ("vless-reality", "nl-1", "us-1", "srv-1", "srv-3")
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
        return ok(ROUTER["state"] + "\n=AIPROBE=\n")
    if cmd.startswith("cat ") and cmd.endswith("04_outbounds.json"):
        return ok(json.dumps(OUTBOUNDS))
    if cmd.startswith("cat ") and cmd.endswith("05_routing.json"):
        return ok(json.dumps(ROUTING))
    if "socks-deep-yt" in cmd and "speed_download" in cmd:
        return ok(ROUTER["speed_out"])
    if cmd.startswith("tail -n"):
        return ok("2026-10-28 21:00:00 YT-SPEED SLOW: канал us-1 медленный\n")
    if cmd.startswith("head -"):
        return ok("# XKeen failover watchdog v41 (2026-10-28)\n")
    return ok("")


D.keenetic_ssh = fake_ssh
D.keenetic_ensure_watchdog_current = lambda *a, **kw: {"action": "current"}
D.keenetic_read_meta = lambda *a, **kw: {}
D.keenetic_read_sub_meta = lambda *a, **kw: {}
client = D.app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
src = open(D.__file__, encoding="utf-8").read()

# --- config: ключи и дефолты ---
for k in ("YT_SPEED_ENABLE", "YT_SPEED_MIN_KBPS", "YT_SPEED_INTERVAL"):
    check(f"{k} в keys_order", f'"{k}"' in src[src.index("keys_order = ["):src.index("keys_order = [") + 2500])
    check(f"{k} в DEFAULT_WATCHDOG_CONFIG", re.search(r"^" + k + r'="\d+"$', D.DEFAULT_WATCHDOG_CONFIG, re.M) is not None)
check("DEFAULT: уход с медленного выключен", 'YT_SPEED_ENABLE="0"' in D.DEFAULT_WATCHDOG_CONFIG)

# --- targets ---
t = D.keenetic_get_watchdog_targets()
check("targets.yt_speed_*", (t["yt_speed_enable"], t["yt_speed_min_kbps"], t["yt_speed_interval"]) == (True, 400, 600), t)

# --- state → watchdog.yt_speed ---
w = D.keenetic_get_watchdog()
ys = w.get("yt_speed") or {}
check("yt_speed: скорость, тег, латч, напрямую", (ys.get("kbps"), ys.get("tag"), ys.get("slow"), ys.get("ctr"),
      ys.get("direct_kbps")) == (11, "us-1", True, 2, 1480), ys)
check("yt_speed: возраст замера 3 мин", ys.get("age_min") == 3, ys)
check("yt_speed: карантин медленных — теги без времени", ys.get("slow_tags") == ["🇸🇪_Sweden"], ys)
_old = ROUTER["state"]
ROUTER["state"] = "primary 0 vless-reality EFFECTIVE_YT=us-1 DYT_TAG=us-1"
check("сторож до v41 — yt_speed None", D.keenetic_get_watchdog().get("yt_speed") is None)
ROUTER["state"] = _old

# --- настройки ---
upd, err = D._validate_deep_probe_settings({"yt_speed_enable": True, "yt_speed_min_kbps": 300, "yt_speed_interval_min": 15})
check("валидация: 300 КБ/с, 15 мин → секунды", err is None and upd == {"YT_SPEED_ENABLE": "1", "YT_SPEED_MIN_KBPS": "300",
      "YT_SPEED_INTERVAL": "900"}, (upd, err))
for bad in ({"yt_speed_min_kbps": 10}, {"yt_speed_min_kbps": 9000}, {"yt_speed_interval_min": 1},
            {"yt_speed_interval_min": 500}, {"yt_speed_min_kbps": "abc"}, {"yt_speed_min_kbps": True}):
    _, err = D._validate_deep_probe_settings(bad)
    check(f"валидация отвергает {bad}", err is not None)
upd, err = D._validate_deep_probe_settings({"yt_speed_enable": "0"})
check("только галочка — пишется только YT_SPEED_ENABLE", upd == {"YT_SPEED_ENABLE": "0"}, upd)
s = D._deep_probe_settings_from_config(ROUTER["config"])
check("GET настроек: yt_speed_*", (s["yt_speed_enable"], s["yt_speed_min_kbps"], s["yt_speed_interval_min"]) == (True, 400, 10), s)

ROUTER["written"].clear()
r = client.post("/api/keenetic/watchdog-settings", json={"yt_speed_enable": False, "yt_speed_min_kbps": 250, "yt_speed_interval_min": 20})
check("POST настроек → 200 ok", r.status_code == 200 and r.get_json().get("ok"), r.get_data(as_text=True)[:200])
c = ROUTER["config"]
check("записано: выкл, 250, 1200 с", (c.get("YT_SPEED_ENABLE"), c.get("YT_SPEED_MIN_KBPS"), c.get("YT_SPEED_INTERVAL")) == ("0", "250", "1200"), c)
check("POST настроек скорости не трогает DEEP_*", (c.get("DEEP_PROBE_INTERVAL"), c.get("DEEP_RETURN_HOLD")) == ("180", "1800"), c)
r = client.post("/api/keenetic/watchdog-settings", json={"yt_speed_min_kbps": 1})
check("POST вне диапазона → 400", r.status_code == 400)
ROUTER["config"].update({"YT_SPEED_ENABLE": "1", "YT_SPEED_MIN_KBPS": "400", "YT_SPEED_INTERVAL": "600"})

# дефолты записи для актуального сторожа: пустые ключи → числа
_saved = dict(ROUTER["config"])
for k in ("YT_SPEED_ENABLE", "YT_SPEED_MIN_KBPS", "YT_SPEED_INTERVAL"):
    ROUTER["config"].pop(k, None)
D.keenetic_write_watchdog_config({"FORCE_MODE": "auto"})
c = ROUTER["config"]
check("дефолты записи: 0 / 300 / 900", (c.get("YT_SPEED_ENABLE"), c.get("YT_SPEED_MIN_KBPS"), c.get("YT_SPEED_INTERVAL")) == ("0", "300", "900"), c)
ROUTER["config"] = _saved

# --- разовый замер ---
p = D._parse_yt_speed_test(ROUTER["speed_out"])
check("_parse_yt_speed_test", (p["tag"], p["kbps"], p["bytes"], p["code"], p["direct_kbps"], p["direct_bytes"]) ==
      ("us-1", 11, 165000, "206", 1480, 2000000), p)
p = D._parse_yt_speed_test("YT 0 0 000\nDIR\n")
check("_parse_yt_speed_test: ничего не скачано, напрямую пусто", p["bytes"] == 0 and p["direct_kbps"] is None and p["tag"] is None, p)
r = client.post("/api/xkeen/yt-speed-test")
j = r.get_json() or {}
check("POST yt-speed-test → ok, 11 КБ/с через us-1", r.status_code == 200 and j.get("ok") and j.get("kbps") == 11 and j.get("tag") == "us-1", j)
cmd = [x for x in ssh_calls if "speed_download" in x][-1]
check("замер — через socks 10820 и напрямую, команда ASCII", "--socks5-hostname 127.0.0.1:10820" in cmd and cmd.count("curl ") == 2
      and cmd.isascii(), cmd)
ROUTER["speed_out"] = "YT 0 0 000\nDIR 1515520 2000000\n"
j = client.post("/api/xkeen/yt-speed-test").get_json() or {}
check("ничего не скачалось через канал → ok=False с причиной", j.get("ok") is False and "ничего не скачалось" in (j.get("error") or ""), j)

# --- рендер /xkeen ---
D.is_keenetic_reachable = lambda *a, **kw: True
html = client.get("/xkeen").get_data(as_text=True)
check("рендер с роутером: без ошибки", "Ошибка при рендеринге" not in html, html[:300])
check("блок «⚡ Скорость канала» и кнопка замера", 'id="yt-speed-box"' in html and 'onclick="ytSpeedTest()"' in html)
check("последний замер сторожа: 11 КБ/с через us-1, медленный", "<strong>11 КБ/с</strong> через <code>us-1</code>" in html and "🐢 медленный" in html)
check("карантин медленных показан", "В карантине как медленные: <code>🇸🇪_Sweden</code>" in html)
check("галочка включена, порог 400, интервал 10", re.search(r'id="yt-speed-enable"\s+checked', html) is not None
      and 'id="yt-speed-min" min="50" max="5000" step="50" value="400"' in html
      and 'id="yt-speed-interval" min="5" max="120" step="5" value="10"' in html)
check("JS: ytSpeedTest и saveYTSpeedSettings", "async function ytSpeedTest()" in html and "async function saveYTSpeedSettings()" in html)
D.is_keenetic_reachable = lambda *a, **kw: False
r = client.get("/xkeen")
html = r.get_data(as_text=True)
check("quick-fail: 200 без ошибки", r.status_code == 200 and "Ошибка при рендеринге" not in html, html[:300])
check("quick-fail: блок есть, замеров нет", 'id="yt-speed-box"' in html and "Замеров сторожа нет" in html)

print("test_yt_speed_panel_offline: " + ("OK" if fails == 0 else f"FAIL ({fails})"))
sys.exit(0 if fails == 0 else 1)
