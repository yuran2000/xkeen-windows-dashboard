# -*- coding: utf-8 -*-
"""Офлайн-проверка предпросмотра подписки: смена отпечатка TLS (fingerprint) видна.

Sync переписывает streamSettings целиком, в том числе fingerprint; раньше preview сравнивал только
адрес/pbk/SNI и для смены randomized → chrome показывал «изменений нет». Здесь: `_outbound_fp`,
`_fp_change_info`, ответ `/api/xkeen/subscription-preview` (fp_change, host_change, changed,
fp_lottery_*), отпечаток новых серверов, и что JS preview выводит отпечаток и предупреждение 🎲.
К роутеру и в сеть не обращается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_sub_preview_fp_offline.py
"""
import json
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


def ob(tag, host, port, fp, sec="reality", pbk="PBK1", sni="vk.com"):
    ss = {"network": "tcp", "security": sec}
    if sec == "reality":
        ss["realitySettings"] = {"publicKey": pbk, "serverName": sni, "shortId": "ab"}
        if fp is not None:
            ss["realitySettings"]["fingerprint"] = fp
    elif sec == "tls":
        ss["tlsSettings"] = {"serverName": sni}
        if fp is not None:
            ss["tlsSettings"]["fingerprint"] = fp
    return {"tag": tag, "protocol": "vless", "streamSettings": ss,
            "settings": {"vnext": [{"address": host, "port": port, "users": [{"id": "u"}]}]}}


def link(tag, host, port, fp=None, sec="reality", pbk="PBK1", sni="vk.com"):
    q = f"security={sec}&type=tcp&sni={sni}&pbk={pbk}&sid=cd"
    if fp is not None:
        q += f"&fp={fp}"
    return f"vless://11111111-2222-3333-4444-555555555555@{host}:{port}?{q}#{tag}"


# --- чистые функции ---
check("_outbound_fp: reality", D._outbound_fp(ob("A", "h", 1, "randomized")) == "randomized")
check("_outbound_fp: tls", D._outbound_fp(ob("A", "h", 1, "firefox", sec="tls")) == "firefox")
check("_outbound_fp: без TLS — пусто", D._outbound_fp(ob("A", "h", 1, None, sec="none")) == "")
check("_outbound_fp: поле не задано — пусто", D._outbound_fp(ob("A", "h", 1, None)) == "")

i = D._fp_change_info({"security": "reality", "fp": "chrome"}, "randomized")
check("randomized → chrome: смена, лотерея уходит",
      i["fp_change"] and i["fp_lottery_old"] and not i["fp_lottery_new"] and i["new_fp"] == "chrome", i)
i = D._fp_change_info({"security": "reality", "fp": "chrome"}, "chrome")
check("тот же отпечаток — без смены", not i["fp_change"], i)
i = D._fp_change_info({"security": "none", "fp": "chrome"}, "")
check("security none — отпечаток не пишется, смены нет", not i["fp_change"] and i["new_fp"] == "", i)
i = D._fp_change_info({"security": "reality", "fp": "randomized"}, "chrome")
check("chrome → randomized: смена и лотерея в новом", i["fp_change"] and i["fp_lottery_new"], i)
i = D._fp_change_info({"security": "tls", "fp": "chrome"}, "")
check("поле не было задано → chrome: смена (sync его запишет)", i["fp_change"], i)

# --- маршрут preview на моках ---
ROUTER = {"outbounds": [
    ob("S1", "vpn.example", 443, "randomized"),
    ob("S2", "vpn.example", 9443, "chrome"),
    ob("S3", "vpn.example", 8444, "chrome"),
    {"tag": "direct", "protocol": "freedom"},
]}
SUB = [
    link("S1", "vpn.example", 443, "chrome"),        # смена отпечатка, адрес тот же
    link("S2", "vpn.example", 9443, "chrome"),       # без изменений
    link("S3", "vpn.example", 8445, "chrome"),       # смена порта
    link("N1", "new.example", 443, "randomized"),    # новый сервер с randomized
    link("N2", "new.example", 444),                  # fp нет в ссылке → парсер даёт chrome
]
D._fetch_subscription = lambda url: (SUB, None, {})
D.keenetic_read_file = lambda path: json.dumps(ROUTER) if path.endswith("04_outbounds.json") else ""
D.keenetic_get_watchdog_targets = lambda *a, **k: {}
D.keenetic_read_meta = lambda: {}
D.keenetic_read_sub_meta = lambda: {}

c = D.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
r = c.post("/api/xkeen/subscription-preview", data={"url": "https://sub.example/x"})
j = r.get_json() or {}
check("preview: 200 и ok", r.status_code == 200 and j.get("ok"), (r.status_code, j))
m = {x["tag"]: x for x in j.get("matches", [])}
check("S1: смена отпечатка видна, changed=да при том же адресе",
      m.get("S1", {}).get("fp_change") and m["S1"].get("changed") and not m["S1"].get("host_change")
      and m["S1"].get("old_fp") == "randomized" and m["S1"].get("new_fp") == "chrome", m.get("S1"))
check("S1: признак ухода с randomized", m.get("S1", {}).get("fp_lottery_old") and not m["S1"].get("fp_lottery_new"))
check("S2: без изменений", m.get("S2") and not m["S2"].get("changed") and not m["S2"].get("fp_change"), m.get("S2"))
check("S3: смена адреса, отпечаток тот же",
      m.get("S3", {}).get("host_change") and m["S3"].get("changed") and not m["S3"].get("fp_change"), m.get("S3"))
o = {x["tag"]: x for x in j.get("orphans_in_sub", [])}
check("N1: новый сервер с randomized помечен", o.get("N1", {}).get("fp_lottery") and o["N1"].get("fp") == "randomized", o.get("N1"))
check("N2: без fp в ссылке — chrome, без пометки", o.get("N2", {}).get("fp") == "chrome" and not o["N2"].get("fp_lottery"), o.get("N2"))

# --- JS preview выводит отпечаток и предупреждение ---
src = D.XKEEN_TEMPLATE
check("JS: строка «отпечаток: старое → новое» в таблице совпадений", "отпечаток: ${escapeHtml(m.old_fp" in src)
check("JS: предупреждение 🎲 о randomized в подписке", "fpLotteryNew" in src and "🎲 В подписке отпечаток TLS" in src)
check("JS: заметка о замене randomized синхронизацией", "fpLotteryFixed" in src)
check("JS: пометка 🎲 у новых серверов", "o.fp_lottery" in src)

print("test_sub_preview_fp_offline: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
