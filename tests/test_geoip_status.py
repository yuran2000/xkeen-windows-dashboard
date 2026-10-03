# -*- coding: utf-8 -*-
"""Офлайн-проверки GeoIP (без роутера, на моках SSH):

1. keenetic_geoip_dat_status(): ОДНА лёгкая SSH-команда (размер/время/симлинк geoip.dat и
   geoip_v2fly.dat, без grep по 16 МБ); категория telegram — фоновой проверкой один раз на версию
   файла (кэш по size+mtime), до готовности has_telegram=None; мусор/сбой → None.
2. Секция «🌍 GeoIP-категории» на /xkeen во всех состояниях: неизвестно (роутер недоступен /
   не прочитался), расширенная-ссылка на geoip_v2fly.dat, расширенная-обычный файл (+ кнопка
   «Связать»), telegram есть, но файл маленький, база «только страны», файла нет.
3. Auto-fix install_extended_geoip на модели файловой системы роутера: симлинк на geoip_v2fly.dat,
   «уже связан», скачивание (код возврата curl не теряется: rc≠0 → рабочий файл цел), маленький
   файл → отказ, авто-откат при падении xray (из .bak и из прежнего симлинка), ротация бэкапов.
4. Диагностика (check geoip_categories) читает FOREIGN_GEOIP_CATEGORIES и YT_GEOIP_CATEGORIES.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_geoip_status.py
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402

NOW = int(time.time())
MB = 1024 * 1024
fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}: {detail}")


def ok(out=""):
    return {"ok": True, "stdout": out, "stderr": "", "code": 0}


def reset_tg_cache():
    with D._GEOIP_TG_LOCK:
        D._GEOIP_TG_CACHE.clear()
        D._GEOIP_TG_PENDING.clear()
        D._GEOIP_TG_FAILED.clear()


def wait_tg(timeout=10):
    """Дождаться фоновых проверок категории telegram."""
    end = time.time() + timeout
    while time.time() < end:
        with D._GEOIP_TG_LOCK:
            threads = list(D._GEOIP_TG_PENDING.values())
        if not threads:
            return True
        for th in threads:
            th.join(0.2)
    return False


# ======================= 1. статус: разбор и кэш telegram =======================
SSH = {"status": ok(""), "tg": ok("TG_YES\n"), "calls": []}


def status_ssh(cmd, timeout=None, **kw):
    SSH["calls"].append(cmd)
    if "grep -q -a -F TELEGRAM" in cmd:
        return SSH["tg"]
    if cmd.startswith("F=/opt/etc/xray/dat/geoip.dat;"):
        return SSH["status"]
    return ok("")


D.keenetic_ssh = status_ssh


def status(g, v, tg="TG_YES", wait=True):
    reset_tg_cache()
    SSH["status"] = ok(f"G={g}\nV={v}\n")
    SSH["tg"] = ok(tg + "\n") if tg else {"ok": False, "stdout": "", "stderr": "timeout", "code": -1}
    SSH["calls"].clear()
    first = D.keenetic_geoip_dat_status()
    if wait:
        wait_tg()
    return first, D.keenetic_geoip_dat_status()


BIG = 17301504
first, st = status(f"{BIG}|{NOW - 3600}|", f"{BIG}|{NOW - 600}")
check("regular: dict", isinstance(st, dict), repr(st))
check("regular: first call has_telegram None (фоновая проверка)", first and first["has_telegram"] is None, repr(first))
check("regular: first call already extended (size>5MB, telegram ещё не опровергнут)", first and first["is_extended"] is True, repr(first))
check("regular: after bg check has_telegram True", st and st["has_telegram"] is True, repr(st))
check("regular: not link", st and st["is_link"] is False and st["linked_to_v2fly"] is False and st["link_target"] is None, repr(st))
check("regular: v2fly fields", st and st["v2fly_exists"] and st["v2fly_usable"] and st["v2fly_size"] == BIG, repr(st))
check("regular: mtime_str ДД.ММ ЧЧ:ММ", st and re.fullmatch(r"\d\d\.\d\d \d\d:\d\d", st["mtime_str"] or "") is not None, repr(st and st["mtime_str"]))
check("regular: size_mb", st and st["size_mb"] == 16.5, repr(st and st["size_mb"]))
status_cmds = [c for c in SSH["calls"] if c.startswith("F=")]
tg_cmds = [c for c in SSH["calls"] if "grep -q -a -F TELEGRAM" in c]
check("status: cheap command (no grep in page-load command)", status_cmds and all("grep" not in c for c in status_cmds), str(status_cmds)[:300])
check("status: command reads link/size/date + v2fly", status_cmds and all(k in status_cmds[0] for k in ("readlink", "wc -c", "date -r", "geoip_v2fly.dat")), status_cmds[0] if status_cmds else "")
check("status: telegram grep ran once for 2 page loads", len(tg_cmds) == 1, str(len(tg_cmds)))

first, st = status(f"{BIG}|{NOW - 3600}|geoip_v2fly.dat", f"{BIG}|{NOW - 3600}")
check("link: is_link + linked_to_v2fly", st and st["is_link"] and st["linked_to_v2fly"] and st["link_target"] == "geoip_v2fly.dat", repr(st))
check("link: extended", st and st["is_extended"] is True, repr(st))
first, st = status(f"{BIG}|{NOW}|/opt/etc/xray/dat/geoip_v2fly.dat", f"{BIG}|{NOW}")
check("link absolute path → linked_to_v2fly", st and st["linked_to_v2fly"] is True, repr(st))
first, st = status(f"{BIG}|{NOW}|zkeenip.dat", f"0|0")
check("link to other file → not linked_to_v2fly", st and st["is_link"] and not st["linked_to_v2fly"] and not st["v2fly_exists"] and not st["v2fly_usable"], repr(st))

first, st = status(f"300000|{NOW}|", "0|0", tg="TG_NO")
check("small countries-only: has_telegram False, not extended", st and st["has_telegram"] is False and st["is_extended"] is False, repr(st))
check("small: before bg check not extended either", first and first["is_extended"] is False, repr(first))
first, st = status(f"{4 * MB}|{NOW}|", "0|0", tg="TG_YES")
check("telegram but small: not extended", st and st["has_telegram"] is True and st["is_extended"] is False, repr(st))
first, st = status(f"{20 * MB}|{NOW}|", "0|0", tg="TG_NO")
check("big without telegram: not extended", st and st["has_telegram"] is False and st["is_extended"] is False, repr(st))
first, st = status("0|0|", "0|0")
check("no file: exists False, has_telegram False", st and st["exists"] is False and st["has_telegram"] is False and st["is_extended"] is False and st["mtime_str"] == "", repr(st))
check("no file: no grep spawned", not any("grep -q" in c for c in SSH["calls"]), str(SSH["calls"]))
first, st = status("0|0|geoip_v2fly.dat", "0|0")
check("dangling link: not exists, is_link", st and st["exists"] is False and st["is_link"] is True and st["linked_to_v2fly"] is False, repr(st))

# сбой фоновой проверки → None, повтор не раньше чем через 5 минут
first, st = status(f"{BIG}|{NOW}|", "0|0", tg=None)
check("tg check failed → None, still extended", st and st["has_telegram"] is None and st["is_extended"] is True, repr(st))
check("tg check failed → no immediate retry", len([c for c in SSH["calls"] if "grep -q" in c]) == 1, str(SSH["calls"]))

# кэш по версии: новый mtime → новая проверка
reset_tg_cache()
SSH["tg"] = ok("TG_YES\n")
SSH["calls"].clear()
SSH["status"] = ok(f"G={BIG}|{NOW - 100}|\nV=0|0\n")
D.keenetic_geoip_dat_status(); wait_tg()
D.keenetic_geoip_dat_status(); wait_tg()
SSH["status"] = ok(f"G={BIG}|{NOW - 50}|\nV=0|0\n")
D.keenetic_geoip_dat_status(); wait_tg()
check("cache keyed by (size, mtime): 2 greps for 2 versions", len([c for c in SSH["calls"] if "grep -q" in c]) == 2, str(len(SSH["calls"])))

for name, reply in [
    ("мусор", ok("garbage\n")),
    ("нет строки V", ok(f"G={BIG}|{NOW}|\n")),
    ("не число", ok("G=12|x|\nV=0|0\n")),
    ("мало полей", ok("G=12|1\nV=0|0\n")),
    ("пусто", ok("")),
    ("ssh ошибка", {"ok": False, "stdout": "", "stderr": "Connection refused", "code": 255}),
    ("таймаут", {"ok": False, "stdout": "G=1990", "stderr": "timeout", "code": -1}),
]:
    SSH["status"] = reply
    check(f"status {name} → None", D.keenetic_geoip_dat_status() is None, repr(D.keenetic_geoip_dat_status()))

# ======================= 2. рендер секции =======================
ROUTER_CFG = {
    "PRIMARY_TAG": "vless-reality", "FAILOVER_TAGS": "nl-1", "AI_TAG": "nl-1", "YT_TAG": "nl-1",
    "FOREIGN_TAG": "nl-1", "FOREIGN_GEOIP_CATEGORIES": "telegram", "FORCE_MODE": "auto",
}
OUTBOUNDS = {"outbounds": [
    {"tag": "direct", "protocol": "freedom", "settings": {}},
    {"tag": "block", "protocol": "blackhole", "settings": {}},
] + [
    {"tag": t, "protocol": "vless",
     "settings": {"vnext": [{"address": f"{t}.example.net", "port": 443, "users": [{"id": "u"}]}]},
     "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.example.com"}}}
    for t in ("vless-reality", "nl-1")
]}
ROUTING = {"routing": {"rules": [{"inboundTag": ["redirect", "tproxy"], "outboundTag": "vless-reality"}]}}
GEOIP_REPLY = {"r": ok("")}


def page_ssh(cmd, stdin_data=None, timeout=None, **kw):
    if cmd.startswith("F=/opt/etc/xray/dat/geoip.dat;"):
        return GEOIP_REPLY["r"]
    if "grep -q -a -F TELEGRAM" in cmd:
        return ok("TG_YES\n")
    if "watchdog.config; else echo __WDCFG_ABSENT__" in cmd:
        return ok("\n".join(f'{k}="{v}"' for k, v in ROUTER_CFG.items()) + "\n")
    if cmd.startswith("cat ") and "watchdog.state" in cmd:
        return ok("primary 0 vless-reality EFFECTIVE_AI=nl-1 EFFECTIVE_YT=nl-1 EFFECTIVE_FOREIGN=nl-1 LAST_BAD_MD5=\n")
    if cmd.startswith("cat ") and cmd.endswith("04_outbounds.json"):
        return ok(json.dumps(OUTBOUNDS))
    if cmd.startswith("cat ") and cmd.endswith("05_routing.json"):
        return ok(json.dumps(ROUTING))
    if cmd.startswith("head -"):
        return ok("# XKeen failover watchdog v31 (2026-10-03)\n")
    return ok("")


D.keenetic_ssh = page_ssh
D.keenetic_ensure_watchdog_current = lambda *a, **kw: {"action": "current"}
D.keenetic_read_meta = lambda *a, **kw: {}
D.keenetic_read_sub_meta = lambda *a, **kw: {}
D.app.config["TESTING"] = True
client = D.app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
    s["username"] = "test"


def geoip_section(html):
    i = html.find("🌍 GeoIP-категории <span")
    if i < 0:
        return ""
    j = html.find("</details>", i)
    return html[i:j]


def render(reachable, g=None, v="0|0", tg=None):
    """g/v — ответ статуса; tg — заранее известный результат проверки telegram (True/False/None)."""
    reset_tg_cache()
    if g is not None:
        GEOIP_REPLY["r"] = ok(f"G={g}\nV={v}\n")
        size, mtime = int(g.split("|")[0]), int(g.split("|")[1])
        if tg is not None:
            D._GEOIP_TG_CACHE[(size, mtime)] = tg
        else:
            D._GEOIP_TG_FAILED[(size, mtime)] = time.time()   # «проверяется» — без фонового потока
    D.is_keenetic_reachable = lambda *a, **kw: reachable
    r = client.get("/xkeen")
    html = r.get_data(as_text=True)
    check(f"render {g} 200", r.status_code == 200, str(r.status_code))
    check(f"render {g} no error", "Ошибка при рендеринге" not in html and "Traceback" not in html, html[:300])
    return geoip_section(html)


# (а) роутер недоступен → неизвестно
sec = render(False)
check("unknown: section found", bool(sec))
check("unknown: grey line", 'data-geoip-state="unknown"' in sec and "Состояние geoip.dat неизвестно (роутер недоступен)" in sec, sec[:400])
check("unknown: no button", "installExtendedGeoip()" not in sec)
check("unknown: hint grey", "Состояние geoip.dat неизвестно (роутер недоступен)." in sec)
# (б) роутер доступен, но статус не прочитался → «не удалось прочитать»
GEOIP_REPLY["r"] = {"ok": False, "stdout": "", "stderr": "timeout", "code": -1}
sec = render(True)
check("ssh-fail: unknown + honest text", 'data-geoip-state="unknown"' in sec and "Не удалось прочитать состояние geoip.dat" in sec
      and "роутер недоступен" not in sec, sec[:400])
# (в) расширенная, ссылка на geoip_v2fly.dat
sec = render(True, f"{BIG}|{NOW - 7200}|geoip_v2fly.dat", f"{BIG}|{NOW - 7200}", tg=True)
check("linked: green line", 'data-geoip-state="extended" data-geoip-linked="1"' in sec, sec[:600])
check("linked: text", "✅ Расширенная база geoip.dat (16 МБ) — обновляется вместе с XKeen ежедневно (ссылка на <code>geoip_v2fly.dat</code>), последнее обновление " in sec, sec[:700])
check("linked: no buttons", "installExtendedGeoip()" not in sec)
check("linked: no yellow", "#fff3cd" not in sec)
# (г) расширенная, обычный файл, geoip_v2fly.dat есть → кнопка «Связать»
sec = render(True, f"{BIG}|{NOW - 7200}|", f"{BIG}|{NOW - 600}", tg=True)
check("regular: green + not updated note", 'data-geoip-linked="0"' in sec and "XKeen обновляет <code>geoip_v2fly.dat</code>, а этот файл — нет." in sec, sec[:700])
check("regular: date ДД.ММ", re.search(r"\(16 МБ, от \d\d\.\d\d\)", sec) is not None, sec[:500])
check("regular: link button", "🔗 Связать с ежедневно обновляемой базой" in sec and "installExtendedGeoip()" in sec, sec[:900])
# (г2) обычный файл, telegram ещё проверяется, geoip_v2fly.dat нет → без кнопки, с пометкой
sec = render(True, f"{BIG}|{NOW - 7200}|", "0|0", tg=None)
check("regular no v2fly: no link button", "Связать с ежедневно" not in sec and "installExtendedGeoip()" not in sec, sec[:700])
check("regular pending tg: note", "проверяется в фоне" in sec, sec[:700])
# (д) telegram есть, но файл маленький
sec = render(True, f"{4 * MB}|{NOW}|", "0|0", tg=True)
check("small-telegram: honest text", 'data-geoip-state="small-telegram"' in sec and "категория <code>telegram</code> есть" in sec
      and "нет категории" not in sec and "installExtendedGeoip()" in sec, sec[:700])
# (е) база «только страны»
sec = render(True, f"1200000|{NOW}|", "0|0", tg=False)
check("basic: yellow banner", 'data-geoip-state="basic"' in sec and "#fff3cd" in sec and "нет категории сервиса <code>telegram</code>" in sec, sec[:600])
check("basic: button", "installExtendedGeoip()" in sec and "📥 Установить / обновить расширенный geoip.dat" in sec)
check("basic: hint", "Требует расширенный geoip.dat (см. кнопку выше)." in sec)
sec = render(True, f"1200000|{NOW}|", "0|0", tg=None)
check("basic pending tg: no false claim", 'data-geoip-state="basic"' in sec and "нет категории" not in sec and "слишком мала" in sec, sec[:600])
# (ж) файла нет / битая ссылка
sec = render(True, "0|0|", "0|0")
check("missing file: yellow + not found", 'data-geoip-state="basic"' in sec and "на роутере не найден" in sec and "installExtendedGeoip()" in sec, sec[:600])
sec = render(True, "0|0|geoip_v2fly.dat", "0|0")
check("dangling link: honest text", "ссылка на <code>geoip_v2fly.dat</code>, но файла по ней нет" in sec, sec[:600])
# общее для всех состояний
check("no discord preset button", "addForeignGeoipCat('discord')" not in sec)
check("placeholder telegram", 'placeholder="через пробел: telegram"' in sec)
check("no discord promise", re.search(r"категори[йи] сервисов \(telegram, discord", sec) is None)
full = client.get("/xkeen").get_data(as_text=True)
m = re.search(r"async function installExtendedGeoip\(\) \{.*?\)\) return;", full, re.S)
check("confirm found", m is not None)
if m:
    check("confirm no discord", "discord" not in m.group(0), m.group(0)[:300])
    check("confirm mentions link + auto rollback", "станет ссылкой" in m.group(0) and "вернётся сама" in m.group(0), m.group(0)[:500])
check("html comment without Discord", "(Telegram/Discord по IP)" not in full)
m = re.search(r'<details class="help-section" id="help-v2fly-geoip">.*?</details>', full, re.S)
check("help-v2fly-geoip found", m is not None)
if m:
    h = m.group(0)
    check("help: no Get-NetTCPConnection", "Get-NetTCPConnection" not in h)
    check("help: no YT_FAIL_BLOCK", "YT_FAIL_BLOCK" not in h)
    check("help: Зарубежные сервисы", "«📱 Зарубежные сервисы»" in h)

# ======================= 3. auto-fix на модели ФС роутера =======================
OLD = {"type": "file", "size": 1200000, "label": "old"}
V2 = {"type": "file", "size": BIG, "label": "v2fly"}


class FakeRouter:
    """Модель /opt/etc/xray/dat: имя → {type: file|link, size, label | target}. Интерпретирует
    команды auto-fix (по характерным подстрокам) и ведёт историю."""

    def __init__(self, files, curl=(0, 19 * MB), statuses=("запущен",), load_errors=()):
        self.fs = {k: dict(v) for k, v in files.items()}
        self.curl = curl                      # (rc, size)
        self.statuses = list(statuses)        # что ответит xkeen -status на каждый рестарт
        self.load_errors = list(load_errors)  # свежие ошибки error.log на каждый рестарт
        self.history = []
        self.restarts = 0

    def resolve(self, name):
        e = self.fs.get(name)
        while e and e["type"] == "link":
            e = self.fs.get(e["target"].rsplit("/", 1)[-1])
        return e

    def ssh(self, cmd, stdin_data=None, timeout=None, **kw):
        self.history.append(cmd)
        fs = self.fs
        if 'echo "CUR=link"' in cmd:                                   # состояние
            fs.pop("geoip.dat.new", None)
            g = fs.get("geoip.dat")
            out = []
            if g and g["type"] == "link":
                out += ["CUR=link", "CURLINK=" + g["target"]]
            elif g:
                out.append("CUR=file")
            else:
                out.append("CUR=none")
            v = self.resolve("geoip_v2fly.dat")
            out.append(f"V2FLY={v['size'] if v else 0}")
            return ok("\n".join(out) + "\n")
        if "CURL_RC=" in cmd:                                          # скачивание
            fs.pop("geoip.dat.new", None)
            rc, size = self.curl
            out = []
            if rc == 0:
                fs["geoip.dat.new"] = {"type": "file", "size": size, "label": "loyalsoldier"}
            else:
                out.append(f"curl: ({rc}) The requested URL returned error: 404")
            out.append(f"CURL_RC={rc}")
            n = fs.get("geoip.dat.new")
            out.append(f"NEW={n['size'] if n else 0}")
            return ok("\n".join(out) + "\n")
        if "SWAP_OK" in cmd:                                           # замена
            out = []
            g = fs.get("geoip.dat")
            if g and g["type"] == "link":
                out.append("OLDLINK=" + g["target"])
            elif g:
                bak = re.search(r"cp geoip\.dat (geoip\.dat\.bak-\S+) ", cmd).group(1)
                fs[bak] = dict(g)
                out.append("BACKUP=" + bak)
            if "ln -sf geoip_v2fly.dat geoip.dat.new" in cmd:
                fs["geoip.dat.new"] = {"type": "link", "target": "geoip_v2fly.dat"}
            elif "geoip.dat.new" not in fs:
                return ok("\n".join(out + ["NEW_MISSING"]) + "\n")
            fs["geoip.dat"] = fs.pop("geoip.dat.new")
            out.append("SWAP_OK")
            return ok("\n".join(out) + "\n")
        if "RB_OK" in cmd:                                             # откат
            m = re.search(r"cp (geoip\.dat\.bak-\S+) geoip\.dat\.rb", cmd)
            if m:
                fs["geoip.dat"] = dict(fs[m.group(1)])
            elif "ln -sf" in cmd:
                tgt = re.search(r"ln -sf '?([^' ]+)'? geoip\.dat\.rb", cmd).group(1)
                fs["geoip.dat"] = {"type": "link", "target": tgt}
            else:
                fs.pop("geoip.dat", None)
            return ok("RB_OK\n")
        if "sort -r | tail -n +3" in cmd:                              # ротация
            baks = sorted((k for k in fs if k.startswith("geoip.dat.bak-")), reverse=True)
            out = []
            for k in baks[2:]:
                fs.pop(k)
                out.append("ROT=" + k)
            return ok("\n".join(out) + ("\n" if out else ""))
        if cmd.strip() == "rm -f /opt/etc/xray/dat/geoip.dat.new":
            fs.pop("geoip.dat.new", None)
            return ok("")
        if cmd.startswith("xkeen -restart"):
            self.restarts += 1
            return ok("Перезапуск xkeen ... готово\n")
        if cmd.startswith("xkeen -status"):
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return ok(("Прокси-клиент запущен в режиме TPROXY\n") if st == "запущен" else "Прокси-клиент не запущен\n")
        if cmd.startswith("wc -l <") and "error.log" in cmd:
            return ok("120\n")
        if "error.log" in cmd and "failed to load" in cmd:
            err = self.load_errors.pop(0) if self.load_errors else ""
            return ok(err)
        return ok("")


def run_fix(files, **kw):
    fr = FakeRouter(files, **kw)
    D.keenetic_ssh = fr.ssh
    j = client.post("/api/xkeen/auto-fix", data={"fix_id": "install_extended_geoip"}).get_json()
    return fr, j


# (а) geoip_v2fly.dat есть → geoip.dat становится симлинком, без curl
fr, j = run_fix({"geoip.dat": OLD, "geoip_v2fly.dat": V2})
log = j.get("log") or ""
check("link ok", j.get("ok") is True, log[:800])
check("link: geoip.dat is symlink → v2fly", fr.fs["geoip.dat"] == {"type": "link", "target": "geoip_v2fly.dat"}, str(fr.fs))
check("link: old file backed up", any(k.startswith("geoip.dat.bak-") and v == OLD for k, v in fr.fs.items()), str(fr.fs))
check("link: via temp name + mv -f", any("ln -sf geoip_v2fly.dat geoip.dat.new" in c and "mv -f geoip.dat.new geoip.dat" in c for c in fr.history), str(fr.history)[:500])
check("link: no curl", not any("curl" in c for c in fr.history))
check("link: restart once", fr.restarts == 1, str(fr.restarts))
check("link: log says link", "станет ссылкой" in log and "ссылка на geoip_v2fly.dat" in log, log[:800])
check("link: v2fly itself untouched", fr.fs["geoip_v2fly.dat"] == V2)
# (а2) уже связан → ничего не делаем
fr, j = run_fix({"geoip.dat": {"type": "link", "target": "geoip_v2fly.dat"}, "geoip_v2fly.dat": V2})
check("already linked: ok, no swap, no restart", j.get("ok") is True and fr.restarts == 0 and not any("SWAP_OK" in c for c in fr.history)
      and "уже ссылается" in (j.get("log") or ""), (j.get("log") or "")[:500])
# (а3) geoip.dat был симлинком на другое → новый симлинк, бэкап не нужен
fr, j = run_fix({"geoip.dat": {"type": "link", "target": "zkeenip.dat"}, "zkeenip.dat": OLD, "geoip_v2fly.dat": V2})
check("relink ok", j.get("ok") is True and fr.fs["geoip.dat"]["target"] == "geoip_v2fly.dat"
      and not any(k.startswith("geoip.dat.bak-") for k in fr.fs) and "zkeenip.dat" in (j.get("log") or ""), str(fr.fs))

# (б) geoip_v2fly.dat нет → скачивание
fr, j = run_fix({"geoip.dat": OLD})
check("curl ok", j.get("ok") is True and fr.fs["geoip.dat"]["label"] == "loyalsoldier", str(j)[:500])
check("curl: backup", any(k.startswith("geoip.dat.bak-") for k in fr.fs), str(fr.fs))
curl_cmd = next((c for c in fr.history if "CURL_RC=" in c), "")
check("curl: --fail, -o .new, rc captured", "--fail" in curl_cmd and "-o geoip.dat.new" in curl_cmd and "rc=$?" in curl_cmd, curl_cmd)
check("curl: no pipe after curl (rc not lost)", re.search(r"curl [^;]*\|", curl_cmd) is None, curl_cmd)
# v2fly маленький (< 5 МБ) → тоже curl
fr, j = run_fix({"geoip.dat": OLD, "geoip_v2fly.dat": {"type": "file", "size": 900000, "label": "v2"}})
check("small v2fly → curl", j.get("ok") is True and fr.fs["geoip.dat"]["label"] == "loyalsoldier", str(fr.fs))

# (в) curl rc≠0 → рабочий файл цел
fr, j = run_fix({"geoip.dat": OLD}, curl=(22, 0))
log = j.get("log") or ""
check("curl rc!=0 → fail", j.get("ok") is False, log[:500])
check("curl rc!=0 → working intact", fr.fs.get("geoip.dat") == OLD, str(fr.fs))
check("curl rc!=0 → no .new, no backup, no restart", "geoip.dat.new" not in fr.fs and not any(k.startswith("geoip.dat.bak-") for k in fr.fs)
      and fr.restarts == 0, str(fr.fs))
check("curl rc!=0 → honest reason", "curl завершился с кодом 22" in log and "Рабочий geoip.dat не тронут" in log, log[:600])
# (г) скачался маленький файл → отказ
fr, j = run_fix({"geoip.dat": OLD}, curl=(0, 300000))
log = j.get("log") or ""
check("small download → fail, intact", j.get("ok") is False and fr.fs.get("geoip.dat") == OLD and "geoip.dat.new" not in fr.fs
      and fr.restarts == 0 and "слишком маленький (300000 байт" in log, log[:600])

# (д) xray не поднялся → авто-откат из .bak
fr, j = run_fix({"geoip.dat": OLD, "geoip_v2fly.dat": V2}, statuses=("не запущен", "запущен"))
log = j.get("log") or ""
check("rollback: fail reported", j.get("ok") is False, log[:600])
check("rollback: old file back as regular file", fr.fs.get("geoip.dat") == OLD, str(fr.fs))
check("rollback: second restart", fr.restarts == 2, str(fr.restarts))
check("rollback: honest log", "Авто-откат" in log and "После отката xray запущен" in log, log[-600:])
check("rollback: no rotation on failure", not any("sort -r | tail -n +3" in c for c in fr.history))
# (д2) откат на прежний симлинк
fr, j = run_fix({"geoip.dat": {"type": "link", "target": "zkeenip.dat"}, "zkeenip.dat": OLD, "geoip_v2fly.dat": V2},
                statuses=("не запущен", "запущен"))
check("rollback to previous link", j.get("ok") is False and fr.fs.get("geoip.dat") == {"type": "link", "target": "zkeenip.dat"}, str(fr.fs))
# (д3) xray запущен, но в СВЕЖИХ строках error.log ошибка загрузки → откат
fr, j = run_fix({"geoip.dat": OLD}, load_errors=("failed to load geoip: TELEGRAM\n", ""))
check("rollback on fresh load error", j.get("ok") is False and fr.fs.get("geoip.dat") == OLD and fr.restarts == 2, str(fr.fs))
elog_cmd = next((c for c in fr.history if "failed to load" in c), "")
check("load errors only from fresh lines", "tail -n +121" in elog_cmd, elog_cmd)
# (д4) после отката xray всё равно лежит → честно
fr, j = run_fix({"geoip.dat": OLD, "geoip_v2fly.dat": V2}, statuses=("не запущен",))
check("rollback still down → manual check", j.get("ok") is False and "всё ещё не запущен" in (j.get("log") or ""), (j.get("log") or "")[-400:])

# (е) ротация: остаются 2 самые свежие копии
files = {"geoip.dat": OLD, "geoip_v2fly.dat": V2,
         "geoip.dat.bak-20260101-000000": OLD, "geoip.dat.bak-20260201-000000": OLD, "geoip.dat.bak-20260301-000000": OLD}
fr, j = run_fix(files)
baks = sorted(k for k in fr.fs if k.startswith("geoip.dat.bak-"))
check("rotation: 2 newest kept", j.get("ok") is True and len(baks) == 2 and "geoip.dat.bak-20260301-000000" in baks
      and baks[-1] > "geoip.dat.bak-20260301-000000", str(baks))
check("rotation: logged", "хранятся 2 последние" in (j.get("log") or ""), (j.get("log") or "")[-400:])

# исходник ветки: рабочий geoip.dat не удаляется до проверки нового, скачивание — во временный файл
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py"), encoding="utf-8").read()
i0 = src.index('elif fix_id == "install_extended_geoip":')
i1 = src.index('elif fix_id == "install_libnghttp2":', i0)
branch = src[i0:i1]
check("branch curl into .new", "-o geoip.dat.new" in branch)
check("branch: rm of working geoip.dat only in rollback-to-nothing", branch.count("rm -f geoip.dat &&") == 1 and "rm -f /opt/etc/xray/dat/geoip.dat " not in branch)
check("auto-fix explain no discord promise", re.search(r"telegram, discord", D.XKEEN_AUTO_FIXES["install_extended_geoip"]["explain"]) is None)
check("auto-fix explain mentions link", "ссылкой" in D.XKEEN_AUTO_FIXES["install_extended_geoip"]["explain"])
check("_cat_missing_msg no discord promise", "например discord" not in D._cat_missing_msg(["discord"], "geoip"))

# ======================= 3б. _list_remote_dat: geoip.dat-симлинк виден =======================
D.keenetic_ssh = lambda cmd, timeout=None, **kw: ok("geoip.dat|17301504|1700000000\ngeoip_v2fly.dat|17301504|1700000000\nzkeen.dat|100|1\n")
lst = D._list_remote_dat()
check("_list_remote_dat keeps geoip.dat", [e["name"] for e in lst] == ["geoip.dat", "geoip_v2fly.dat", "zkeen.dat"], str(lst))
_seen = []
D.keenetic_ssh = lambda cmd, timeout=None, **kw: (_seen.append(cmd), ok(""))[1]
D._list_remote_dat()
check("_list_remote_dat: own name for geoip.dat/geosite.dat", _seen and 'geoip.dat|geosite.dat) base=' in _seen[0], _seen[0] if _seen else "")


# ======================= 4. диагностика: FOREIGN + YT =======================
def diag_with(cfg_lines, xray_test_out):
    calls = []

    def ssh(cmd, stdin_data=None, timeout=None, **kw):
        calls.append(cmd)
        if "_GEOIP_CATEGORIES=" in cmd and cmd.startswith("grep"):
            return ok(cfg_lines)
        if "code not found in geoip" in cmd:
            return ok(xray_test_out)
        return ok("")
    D.keenetic_ssh = ssh
    D.is_keenetic_reachable = lambda *a, **kw: True
    jj = client.get("/api/xkeen/diagnose").get_json() or {}
    row = next((c for c in jj.get("checks", []) if c.get("id") == "geoip_categories"), None)
    return row, calls


row, calls = diag_with('FOREIGN_GEOIP_CATEGORIES="telegram facebook"\nYT_GEOIP_CATEGORIES="telegram"\n', "")
check("diag row exists", row is not None, str(row))
if row:
    check("diag reads FOREIGN+YT", any("FOREIGN|YT" in c for c in calls), str([c for c in calls if "GEOIP" in c]))
    check("diag union no dups", "telegram facebook" in (row.get("detail") or "") and (row.get("detail") or "").count("telegram") == 1, row.get("detail"))
    check("diag ok when test clean", row.get("ok") is True, str(row))
row, _ = diag_with('FOREIGN_GEOIP_CATEGORIES="telegram"\n', "code not found in geoip.dat: TELEGRAM\n")
if row:
    check("diag missing → fix", row.get("ok") is False and row.get("fix_id") == "install_extended_geoip", str(row))
    check("diag fix_explain no discord promise", "telegram, discord" not in (row.get("fix_explain") or ""), row.get("fix_explain"))
row, _ = diag_with("", "")
if row:
    check("diag empty → info", row.get("ok") is True and "FOREIGN_GEOIP_CATEGORIES" in (row.get("detail") or ""), str(row))

if fails:
    print("FAIL:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("test_geoip_status: OK")
