# -*- coding: utf-8 -*-
"""Офлайн-проверки мелочей панели: порт и имя задачи экземпляра (здоровье, рестарт), галки блокировки
каналов — как их читает watchdog (`${X:-дефолт}`: пусто = нет ключа = дефолт), kill-switch в сайдбаре.
К роутеру и Планировщику не обращается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_panel_misc_offline.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402
_ORIG_READ_WD = D.keenetic_read_watchdog_config   # ниже тесты подменяют её без возврата

fails = 0


def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  | {extra}" if not cond else ""))
    if not cond:
        fails += 1


# --- порт и имя задачи ---
orig_port = getattr(D.cfg, "DASHBOARD_PORT", None)
for port, task in ((5000, "XrayDashboard"), (5002, "XrayDashboard-5002"), (None, "XrayDashboard"), ("abc", "XrayDashboard")):
    D.cfg.DASHBOARD_PORT = port
    check(f"задача для порта {port!r} → {task}", D._dashboard_task_name() == task, D._dashboard_task_name())

# --- рестарт берёт задачу своего экземпляра ---
calls = []


class _R:
    returncode = 0
    stdout = stderr = ""


D.subprocess.run = lambda args, **k: (calls.append(list(args)), _R())[1]
D.subprocess.Popen = lambda args, **k: calls.append(list(args))
D.app.config["TESTING"] = True
c = D.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
D.cfg.DASHBOARD_PORT = 5002
j = c.post("/api/xkeen/restart-dashboard").get_json()
create = next((a for a in calls if "/Create" in a), [])
tr = create[create.index("/TR") + 1] if "/TR" in create else ""
check("рестарт: ответ ok", j.get("ok") is True, j)
check("рестарт: /End и /Run своей задачи XrayDashboard-5002",
      "/End /TN XrayDashboard-5002 " in tr and "/Run /TN XrayDashboard-5002 " in tr, tr)
check("рестарт: одноразовая задача со своим именем", "XrayDashboardRestarter-5002" in create, create)

# --- здоровье смотрит свой порт ---
j = c.get("/api/xkeen/dashboard-health").get_json()
check("здоровье: сообщение про свой порт :5002", ":5002" in (j.get("msg") or ""), j)
D.cfg.DASHBOARD_PORT = orig_port

# --- галки блокировки каналов = дефолты watchdog ---
cases = [
    ({}, (True, False, False), "ключей нет"),
    ({"AI_FAIL_BLOCK": "", "YT_FAIL_BLOCK": "", "FOREIGN_FAIL_BLOCK": ""}, (True, False, False), "пустые значения"),
    ({"AI_FAIL_BLOCK": "0", "YT_FAIL_BLOCK": "1", "FOREIGN_FAIL_BLOCK": "1"}, (False, True, True), "явные значения"),
    ({"AI_FAIL_BLOCK": " 1 ", "YT_FAIL_BLOCK": " 0 "}, (True, False, False), "с пробелами"),
]
for cfgd, want, label in cases:
    D.keenetic_read_watchdog_config = lambda d=cfgd: dict(d)
    t = D.keenetic_get_watchdog_targets()
    got = (t["ai_fail_block"], t["yt_fail_block"], t["foreign_fail_block"])
    check(f"галки AI/YT/FOREIGN ({label}) = {want}", got == want, got)

# --- kill-switch политики в сайдбаре не «Прочее» ---
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py"), encoding="utf-8").read()
check("catFor относит kill-switch к «Обслуживание»", "wireguard|kill-switch/.test(t)) return 'Обслуживание'" in src)

# keenetic_read_watchdog_config: один повтор при разовом сбое SSH, второй сбой — None (fail-closed)
_orig_ssh, _orig_sleep = D.keenetic_ssh, D._time_mod.sleep
_calls = []
def _fake_ssh(seq):
    def f(cmd, *a, **k):
        _calls.append(cmd)
        ok = seq.pop(0)
        return {"ok": ok, "stdout": 'PRIMARY_TAG="S3"\n' if ok else "", "stderr": "" if ok else "boom", "code": 0 if ok else 255}
    return f
D._time_mod.sleep = lambda s: None
try:
    _calls.clear(); D.keenetic_ssh = _fake_ssh([False, True])
    r = _ORIG_READ_WD()
    check("чтение watchdog.config: разовый сбой SSH — повтор и успех", isinstance(r, dict) and r.get("PRIMARY_TAG") == "S3" and len(_calls) == 2, r)
    _calls.clear(); D.keenetic_ssh = _fake_ssh([False, False])
    check("чтение watchdog.config: два сбоя подряд — None (fail-closed)", _ORIG_READ_WD() is None and len(_calls) == 2)
    _calls.clear(); D.keenetic_ssh = _fake_ssh([True])
    check("чтение watchdog.config: успех с первого раза — без повтора", _ORIG_READ_WD() is not None and len(_calls) == 1)
finally:
    D.keenetic_ssh, D._time_mod.sleep = _orig_ssh, _orig_sleep

print("test_panel_misc_offline: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
