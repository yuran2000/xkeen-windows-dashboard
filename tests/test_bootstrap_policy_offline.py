# -*- coding: utf-8 -*-
"""Офлайн-проверка bootstrap: политика XKeen в Keenetic и tcpdump.

На чистом роутере политики XKeen нет: XKeen проксирует всё устройство, kill-switch ставить не к чему.
Bootstrap создаёт политику (`_xkeen_policy_plan` + `keenetic_ensure_xkeen_policy`) с привязкой
устройств домашних сегментов и ставит необязательный tcpdump (`keenetic_ensure_tcpdump`).
Здесь: чистый планировщик, исполнитель на подменном SSH/RCI (команды ndmc, сохранение, перезапуск
XKeen, ожидание метки в proxy.sh, сбои), tcpdump, пункты плана и порядок применения, справка.
К роутеру не обращается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_bootstrap_policy_offline.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  | {extra}" if not cond else ""))
    if not cond:
        fails += 1


IFACES = {
    "Bridge0": {"type": "Bridge", "security-level": "private", "description": "Home network"},
    "Bridge1": {"type": "Bridge", "security-level": "protected", "description": "Guest network"},
    "PPPoE0": {"type": "PPPoE", "security-level": "public", "defaultgw": True},
    "Wireguard0": {"type": "Wireguard", "security-level": "private"},
}
M = lambda n: "aa:bb:cc:dd:ee:%02x" % n   # noqa: E731
HOTSPOT = {"host": [
    {"mac": M(1), "registered": True, "interface": {"id": "Bridge0"}},    # дом, активен → да
    {"mac": M(2), "registered": False, "interface": {"id": "Bridge0"}},   # дом, не зарегистрирован, активен → да
    {"mac": M(3), "registered": False, "interface": {"id": "Bridge1"}},   # гость → нет
    {"mac": M(4), "registered": True},                                   # офлайн, зарегистрирован → да
    {"mac": M(5), "registered": False},                                  # офлайн, не зарегистрирован → нет
    {"mac": M(6), "registered": True, "interface": {"id": "Bridge0"}},    # другая политика → нет
    {"mac": "AA:BB:CC:DD:EE:01", "registered": True},                    # дубль (регистр) → один раз
    {"mac": "bad;rm -rf /", "registered": True},                         # мусор → нет
]}
HOSTPOL = {M(6): "Policy0"}

# --- чистый планировщик ---
plan = D._xkeen_policy_plan({"Policy0": {}, "Policy2": {}}, IFACES, HOTSPOT, HOSTPOL)
check("план: свободное имя — первая дыра (Policy1)", plan.get("policy_id") == "Policy1", plan)
check("план: выход — интерфейс с defaultgw", plan.get("wan") == "PPPoE0", plan)
check("план: привязка дом активные + офлайн зарегистрированные", plan.get("bind") == [M(1), M(2), M(4)], plan.get("bind"))
check("план: другая политика и гость посчитаны", plan.get("skipped_other") == 1 and plan.get("skipped_guest") == 1, plan)
check("план: без defaultgw — ошибка", "error" in D._xkeen_policy_plan({}, {"Bridge0": IFACES["Bridge0"]}, HOTSPOT, {}))
check("план: имя WAN с метасимволами — ошибка",
      "error" in D._xkeen_policy_plan({}, {"PPP;x": {"defaultgw": True}}, HOTSPOT, {}))
check("план: RCI не прочитан — ошибка", "error" in D._xkeen_policy_plan(None, IFACES, HOTSPOT, {}))
check("план: нет хостов — пустая привязка", D._xkeen_policy_plan({}, IFACES, None, {}).get("bind") == [])
check("план: занятые 0–63 — ошибка", "error" in D._xkeen_policy_plan({f"Policy{n}": {} for n in range(64)}, IFACES, {}, {}))

# --- исполнитель на подменах ---
R = {}


def reset(policy_exists=False, rc_ok=True, script_ok=True, mark_after=True, created_ok=True):
    R.clear()
    R.update(policy_exists=policy_exists, rc_ok=rc_ok, script_ok=script_ok, mark_after=mark_after,
             created_ok=created_ok, created=False, calls=[], script=None, restarts=0)


def fake_rci(path, timeout=12):
    if path == "show/ip/policy":
        pol = {"Policy0": {"description": "Other", "mark": "ffffaab"}}
        if R["policy_exists"] or (R["created"] and R["created_ok"]):
            pol["Policy1"] = {"description": "XKeen", "mark": "ffffaac"}
        return pol
    if path == "show/interface":
        return IFACES
    if path == "show/ip/hotspot":
        return HOTSPOT
    return None


def fake_ssh(cmd, stdin_data=None, timeout=None, **kw):
    R["calls"].append(cmd)
    ok = lambda out="": {"ok": True, "stdout": out, "stderr": "", "code": 0}   # noqa: E731
    if "show running-config" in cmd:
        return ok(f"    host {M(6)} policy Policy0\n__RC_END__\n" if R["rc_ok"] else "")
    if cmd == "sh" and stdin_data and "ip policy" in stdin_data:
        R["script"] = stdin_data
        R["created"] = True
        return ok("Created policy\nPOLICY_SCRIPT_END\n" if R["script_ok"] else "partial\n")
    if "xkeen -restart" in cmd:
        R["restarts"] += 1
        return ok("")
    if cmd.startswith("grep -m1 '^policy_mark='"):
        return ok("policy_mark='0xffffaac'\n" if R["mark_after"] and R["restarts"] else "policy_mark=''\n")
    if "command -v tcpdump" in cmd and "opkg" not in cmd:
        return ok("HAS_TCPDUMP\n" if R.get("tcpdump") else "")
    if "opkg install tcpdump" in cmd:
        if R.get("tcpdump_installable"):
            R["tcpdump"] = True
            return ok("Configuring tcpdump.\nTCPDUMP_OK\n")
        return ok("Unknown package 'tcpdump'.\n")
    return ok("")


D._keenetic_rci = fake_rci
D.keenetic_ssh = fake_ssh

reset(policy_exists=True)
r = D.keenetic_ensure_xkeen_policy(poll_sec=0)
check("политика есть → current, ничего не создаётся", r["ok"] and r["action"] == "current" and R["script"] is None, r)
check("политика есть → XKeen не перезапускается", R["restarts"] == 0)

reset()
r = D.keenetic_ensure_xkeen_policy(poll_sec=0)
s = R["script"] or ""
check("создание: ok/created", r["ok"] and r["action"] == "created", r)
check("создание: ip policy + description + permit global WAN",
      'ndmc -c "ip policy Policy1"' in s and 'ndmc -c "ip policy Policy1 description XKeen"' in s
      and 'ndmc -c "ip policy Policy1 permit global PPPoE0"' in s, s)
check("создание: привязаны дом/офлайн-зарегистрированные, не гость и не чужие",
      all(f'ip hotspot host {m} policy Policy1' in s for m in (M(1), M(2), M(4)))
      and M(3) not in s and M(5) not in s and M(6) not in s and "rm -rf" not in s, s)
check("создание: сохранение конфигурации после привязок",
      s.find('system configuration save') > s.rfind('ip hotspot host'), s)
check("создание: скрипт идёт через stdin, не в argv", all("ip policy" not in c for c in R["calls"] if c != "sh"))
check("создание: XKeen перезапущен и метка подхвачена", R["restarts"] == 1 and "подхватил метку" in r["detail"], r["detail"])
check("создание: отчёт о привязке и о новых устройствах",
      "привязано устройств домашней сети: 3" in r["detail"] and "«👥 Клиенты»" in r["detail"], r["detail"])

reset(mark_after=False)
r = D.keenetic_ensure_xkeen_policy(poll_sec=0)
check("метка не подхвачена → ok, но предупреждение", r["ok"] and "ещё не подхватил" in r["detail"], r["detail"])

reset(rc_ok=False)
r = D.keenetic_ensure_xkeen_policy(poll_sec=0)
check("running-config не прочитан → отказ без изменений", not r["ok"] and R["script"] is None, r)

reset(script_ok=False, created_ok=False)
r = D.keenetic_ensure_xkeen_policy(poll_sec=0)
check("скрипт оборван и политики нет → отказ, XKeen не трогается", not r["ok"] and R["restarts"] == 0, r)

# --- tcpdump ---
reset(); R["tcpdump"] = True
check("tcpdump есть → current", D.keenetic_ensure_tcpdump()["action"] == "current")
reset(); R["tcpdump_installable"] = True
t = D.keenetic_ensure_tcpdump()
check("tcpdump нет → installed", t["ok"] and t["action"] == "installed", t)
reset()
t = D.keenetic_ensure_tcpdump()
check("tcpdump не ставится → fail с причиной", not t["ok"] and "Unknown package" in t["detail"], t)

# --- bootstrap: пункты плана, порядок, необязательность tcpdump ---
D.keenetic_ensure_killswitch = lambda mode=None, force=False: {"action": "installed", "detail": "mock"}
D.keenetic_ensure_watchdog_current = lambda *a, **kw: {"action": "current"}
D.keenetic_ensure_socks_local = lambda *a, **kw: {"ok": True, "detail": "mock"}
_base_ssh = fake_ssh


def boot_ssh(cmd, stdin_data=None, timeout=None, **kw):
    if cmd.startswith("which xkeen"):
        return {"ok": True, "stdout": "INSTALLED\n", "stderr": "", "code": 0}
    if cmd.startswith("test -e ") and "wc -c" in cmd:
        return {"ok": True, "stdout": "MISSING\n", "stderr": "", "code": 0}
    return _base_ssh(cmd, stdin_data=stdin_data, timeout=timeout, **kw)


D.keenetic_ssh = boot_ssh
_orig_ensure_policy = D.keenetic_ensure_xkeen_policy
D.keenetic_ensure_xkeen_policy = lambda: _orig_ensure_policy(poll_sec=0)
c = D.app.test_client()
with c.session_transaction() as sess:
    sess["logged_in"] = True

reset()
j = c.get("/api/keenetic/bootstrap-preview").get_json()
keys = [it["key"] for it in j.get("items", [])]
check("план: есть xkeen_policy и tcpdump, kill-switch последний",
      "xkeen_policy" in keys and "tcpdump" in keys and keys[-1] == "killswitch"
      and keys.index("xkeen_policy") < keys.index("killswitch"), keys)
pi = next(it for it in j["items"] if it["key"] == "xkeen_policy")
check("план: политики нет → create", pi["action"] == "create" and pi["mode"] == "policy", pi)
ti = next(it for it in j["items"] if it["key"] == "tcpdump")
check("план: tcpdump нет → create", ti["action"] == "create" and ti["mode"] == "opkg", ti)

reset(policy_exists=True); R["tcpdump"] = True
j = c.get("/api/keenetic/bootstrap-preview").get_json()
acts = {it["key"]: it["action"] for it in j.get("items", [])}
check("план: политика и tcpdump есть → keep", acts.get("xkeen_policy") == "keep" and acts.get("tcpdump") == "keep", acts)

reset()   # политики нет, tcpdump не ставится
j = c.post("/api/keenetic/bootstrap-apply",
           json={"keys": ["tcpdump", "killswitch", "xkeen_policy"], "overwrite_existing": False}).get_json()
order = [l["key"] for l in j.get("log", [])]
check("применение: порядок политика → kill-switch → tcpdump", order[:3] == ["xkeen_policy", "killswitch", "tcpdump"], order)
check("применение: неудача tcpdump не валит развёртывание", j.get("ok") is True, j)
tl = next(l for l in j["log"] if l["key"] == "tcpdump")
check("применение: tcpdump — предупреждение «необязательный»", tl["ok"] and "необязательный" in tl["msg"], tl)
check("применение: политика создана", any(l["key"] == "xkeen_policy" and l["ok"] and l["action_taken"] == "create" for l in j["log"]), j["log"])

reset(policy_exists=True)
j = c.post("/api/keenetic/bootstrap-apply", json={"keys": ["xkeen_policy"], "overwrite_existing": True}).get_json()
pl = next(l for l in j["log"] if l["key"] == "xkeen_policy")
check("применение с «Перезаписать»: существующая политика не трогается",
      pl["ok"] and pl["action_taken"] == "skip" and R["script"] is None, pl)

# --- справка ---
src = D.XKEEN_TEMPLATE
check("справка: шаг 8 — панель создаёт политику сама", "Шаг 8. Политика «XKeen» в Keenetic — панель создаёт её на шаге 7" in src)
check("справка: шаг 7 перечисляет политику и tcpdump",
      "Политика <code>XKeen</code> в Keenetic, если её нет" in src and "<code>tcpdump</code> из Entware" in src)

print("test_bootstrap_policy_offline: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
