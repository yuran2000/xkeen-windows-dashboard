# -*- coding: utf-8 -*-
"""Офлайн-проверка снятия заворота WG в xray (wg_xray_disconnect) и очистки осиротевшего хука
(/api/xkeen/wg-cleanup-orphan): успех — только когда сняты правила И удалены файл хука и строка cron.
Иначе cron (self-heal раз в минуту) вернул бы заворот, а панель уже ответила бы «отключён».
К роутеру не обращается: keenetic_ssh и чтение интерфейсов подменены.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_wg_disconnect_offline.py
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


IFACES = {"ok": True, "interfaces": [{"id": "Wireguard3", "kernel_name": "nwg3", "subnet_cidr": "172.173.2.0/24"}],
          "orphan_hooks": ["nwg9"]}
D.keenetic_get_wireguard_interfaces = lambda *a, **k: IFACES
answer = {"stdout": ""}
sent = []


def fake_ssh(cmd, timeout=None, stdin_data=None):
    sent.append(cmd)
    return {"ok": True, "stdout": answer["stdout"], "stderr": "", "code": 0}


D.keenetic_ssh = fake_ssh

# --- wg_xray_disconnect ---
answer["stdout"] = "WG_GONE\n"
r = D.wg_xray_disconnect("Wireguard3")
check("снято всё → ok", r.get("ok") is True and r.get("connected") is False, r)
check("команда проверяет и файл хука, и cron", "WG_FILE_LEFT" in sent[-1] and "WG_CRON_LEFT" in sent[-1], sent[-1][-300:])

answer["stdout"] = "WG_CRON_LEFT\nWG_GONE\n"
r = D.wg_xray_disconnect("Wireguard3")
check("осталась строка cron → не успех", r.get("ok") is False and "cron" in (r.get("error") or ""), r)

answer["stdout"] = "WG_FILE_LEFT\nWG_GONE\n"
r = D.wg_xray_disconnect("Wireguard3")
check("остался файл хука → не успех", r.get("ok") is False and "zz_wg_xray_nwg3.sh" in (r.get("error") or ""), r)

answer["stdout"] = "WG_STILL\n"
r = D.wg_xray_disconnect("Wireguard3")
check("catch-all на месте → не успех", r.get("ok") is False and "catch-all" in (r.get("error") or ""), r)

# --- /api/xkeen/wg-cleanup-orphan ---
D.app.config["TESTING"] = True
c = D.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True

answer["stdout"] = "WG_ORPHAN_GONE\n"
j = c.post("/api/xkeen/wg-cleanup-orphan", json={"kernel": "nwg9"}).get_json()
check("осиротевший: убрано всё → ok", j.get("ok") is True, j)
check("осиротевший: команда проверяет cron", "WG_ORPHAN_CRON" in sent[-1], sent[-1][-200:])

answer["stdout"] = "WG_ORPHAN_CRON\nWG_ORPHAN_GONE\n"
j = c.post("/api/xkeen/wg-cleanup-orphan", json={"kernel": "nwg9"}).get_json()
check("осиротевший: осталась строка cron → не успех", j.get("ok") is False and "cron" in (j.get("error") or ""), j)

j = c.post("/api/xkeen/wg-cleanup-orphan", json={"kernel": "nwg3"}).get_json()
check("живой интерфейс очисткой осиротевших не снимается", j.get("ok") is False, j)

print("test_wg_disconnect_offline: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
