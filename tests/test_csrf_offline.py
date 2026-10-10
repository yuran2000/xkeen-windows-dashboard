# -*- coding: utf-8 -*-
"""Офлайн-проверка защиты от CSRF (_csrf_origin_guard): изменяющий запрос с Origin/Referer чужого сайта
отклоняется 403 до обработчика; со своего хоста, без заголовков (скрипты) и GET — проходит;
cfg.TRUSTED_ORIGINS расширяет список; cookie сессии — SameSite=Lax. К роутеру не обращается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_csrf_offline.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402

calls = []


@D.app.route("/__csrf_probe", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def _csrf_probe():
    calls.append(1)
    return "ok"


@D.app.route("/api/__csrf_probe", methods=["POST"])
def _csrf_probe_api():
    calls.append(1)
    return D.jsonify({"ok": True})


D.app.config["TESTING"] = True
c = D.app.test_client()
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  | {extra}" if not cond else ""))
    if not cond:
        fails += 1


def req(method, path="/__csrf_probe", headers=None):
    n = len(calls)
    r = c.open(path, method=method, headers=headers or {}, base_url="http://192.168.1.100:5000")
    return r, len(calls) > n


EVIL = {"Origin": "https://evil.example"}
SAME = {"Origin": "http://192.168.1.100:5000"}

for m in ("POST", "PUT", "PATCH", "DELETE"):
    r, hit = req(m, headers=EVIL)
    check(f"{m} с чужим Origin -> 403, обработчик не вызван", r.status_code == 403 and not hit, r.status_code)
r, hit = req("POST", headers=SAME)
check("POST со своего хоста -> проходит", r.status_code == 200 and hit, r.status_code)
r, hit = req("POST")
check("POST без Origin/Referer (скрипт) -> проходит", r.status_code == 200 and hit, r.status_code)
r, hit = req("POST", headers={"Referer": "https://evil.example/page"})
check("POST без Origin, чужой Referer -> 403", r.status_code == 403 and not hit, r.status_code)
r, hit = req("POST", headers={"Referer": "http://192.168.1.100:5000/xkeen"})
check("POST без Origin, свой Referer -> проходит", r.status_code == 200 and hit, r.status_code)
r, hit = req("POST", headers={"Origin": "null"})
check("POST с Origin: null -> 403", r.status_code == 403 and not hit, r.status_code)
r, hit = req("POST", headers={"Origin": "http://192.168.1.100:5001"})
check("POST с другого порта -> 403", r.status_code == 403 and not hit, r.status_code)
r, hit = req("GET", headers=EVIL)
check("GET с чужим Origin -> проходит (ссылки и чтение)", r.status_code == 200 and hit, r.status_code)
r, hit = req("POST", "/api/__csrf_probe", headers=EVIL)
check("API: чужой Origin -> 403 JSON", r.status_code == 403 and not hit and (r.get_json() or {}).get("ok") is False, r.get_data()[:80])
r, hit = req("POST", "/login", headers=EVIL)
check("вход /login с чужого сайта -> 403", r.status_code == 403, r.status_code)
saved = getattr(D.cfg, "TRUSTED_ORIGINS", None)
D.cfg.TRUSTED_ORIGINS = ["panel.example.org"]
r, hit = req("POST", headers={"Origin": "https://panel.example.org"})
check("TRUSTED_ORIGINS: доверенный адрес прокси -> проходит", r.status_code == 200 and hit, r.status_code)
if saved is None:
    del D.cfg.TRUSTED_ORIGINS
else:
    D.cfg.TRUSTED_ORIGINS = saved
r = c.post("/login", data={"username": D.cfg.USERNAME, "password": D.cfg.PASSWORD}, base_url="http://192.168.1.100:5000",
           headers=SAME)
ck = r.headers.getlist("Set-Cookie")
check("cookie сессии SameSite=Lax", ck and all("SameSite=Lax" in x for x in ck), ck)

print(f"\ntest_csrf_offline: {'OK' if not fails else 'FAIL'} (провалов: {fails})")
sys.exit(1 if fails else 0)
