# -*- coding: utf-8 -*-
"""Офлайн-прогон API v1.1.0 на моках SSH (без роутера): kill-switch, snapshot/restore,
bootstrap-план и диагностика. Проверяет контракты эндпоинтов и то, что на «роутер» уходят
ожидаемые команды.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_api_offline.py
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402
import xkeen_killswitch as K  # noqa: E402

# ---------- мок «роутера» ----------
ROUTER = {"hook": None, "cron": set(), "files": {}, "chmod": [], "restart": 0}
ssh_calls = []


def _status_out():
    inst = ROUTER["hook"] is not None
    hp = K.parse_hook_params(ROUTER["hook"] or "")
    act = "DROP" if hp.get("KS_MODE") == "protect" else "RETURN"
    lines = [
        f"hook_exists={1 if inst else 0}", f"hook_exec={1 if inst else 0}",
        f"cron={1 if K.HOOK_PATH in ROUTER['cron'] else 0}", "wan=ppp0", "wgs=nwg2,",
        "iptables_present=1", f"iptables_jumps={2 if inst else 0}", f"iptables_first={1 if inst else 0}",
        f"iptables_chain_rules={17 if inst else 0}", f"iptables_act={act if inst else ''}",
        "ip6tables_present=1", f"ip6tables_jumps={1 if inst else 0}", f"ip6tables_first={1 if inst else 0}",
        f"ip6tables_chain_rules={16 if inst else 0}", f"ip6tables_act={act if inst else ''}",
        "---HOOK_HEAD---",
    ]
    return "\n".join(lines) + "\n" + "\n".join((ROUTER["hook"] or "").splitlines()[:20]) + "\n"


def fake_ssh(cmd, stdin_data=None, timeout=None, **kw):
    ssh_calls.append(cmd)
    ok = lambda out="": {"ok": True, "stdout": out, "stderr": "", "code": 0}
    if cmd == "sh" and stdin_data:
        if "hook_exists=" in stdin_data:
            return ok(_status_out())
        if "nf_conntrack" in stdin_data:
            return ok("count=0\n")
        return ok("")
    if cmd == "sh -n":
        return ok("")
    if "port_proxying.lst" in cmd and "###WAN###" in cmd:
        return ok("80\n443\n###WAN###\ndefault dev ppp0 scope link  metric 1000\n###V6###\nyes\n")
    if K.HOOK_PATH + ".new" in cmd and "base64 -d" in cmd:
        import base64
        ROUTER["hook"] = base64.b64decode(stdin_data).decode("utf-8")
        ROUTER["cron"].add(K.HOOK_PATH)
        return ok("KS_INSTALL_OK\n")
    if cmd.startswith(f"rm -f {K.HOOK_PATH}"):
        ROUTER["hook"] = None
        ROUTER["cron"].discard(K.HOOK_PATH)
        return ok("KS_REMOVE_OK\n")
    if cmd.startswith(f"test -x {K.HOOK_PATH}"):
        return ok("EXISTS\n" if ROUTER["hook"] else "")
    if cmd.startswith("mkdir -p $(dirname ") and "cat >" in cmd:
        path = cmd.split("cat > ", 1)[1].strip()
        ROUTER["files"][path] = stdin_data
        return ok("")
    if cmd.startswith("chmod +x "):
        ROUTER["chmod"].extend(cmd.split()[2:])
        return ok("")
    if "xkeen -restart" in cmd:
        ROUTER["restart"] += 1
        return ok("")
    if cmd.startswith("which xkeen"):
        return ok("INSTALLED\n")
    if cmd.startswith("crontab -l"):
        return ok("\n".join(f"*/1 * * * * {p}" for p in sorted(ROUTER["cron"])) + "\n")
    if cmd.startswith("wc -c <"):
        return ok("100\n")
    if cmd.startswith("test -e ") and "wc -c" in cmd:
        return ok("MISSING\n")
    if cmd.startswith("test -d /opt/var/log/xray"):
        return ok("EXISTS\n")
    if "jq -s" in cmd and "files: ." in cmd:
        return ok(json.dumps({"version": "v7", "timestamp": "x", "files": [
            {"path": "/opt/etc/xray/watchdog.config", "content": 'PRIMARY_TAG="a"\n'},
            {"path": "/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh", "content": "#!/bin/sh\nexit 0\n"},
        ]}))
    return ok("")


D.keenetic_ssh = fake_ssh
D._keenetic_rci = lambda path, timeout=12: (
    {"Policy1": {"description": "other", "mark": "ffffaaa", "table4": 4096},
     "Policy2": {"description": "XKeen", "mark": "ffffaac", "table4": 4100}} if path == "show/ip/policy" else None)
D.keenetic_ensure_watchdog_current = lambda *a, **kw: {"action": "current"}
D.keenetic_ensure_socks_local = lambda *a, **kw: {"ok": True, "detail": "mock"}
D.keenetic_load_json = lambda *a, **kw: {"outbounds": [{"tag": "direct"}, {"tag": "block"}]}
D.app.config["TESTING"] = True
client = D.app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
    s["username"] = "test"

fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}: {detail}")


# ---------- kill-switch ----------
j = client.get("/api/xkeen/killswitch-status").get_json()
check("status absent", j["ok"] and j["state"] == "absent" and not j["installed"], str(j)[:300])
check("status params", j["params"]["mark"] == "0xffffaac" and j["params"]["ports"] == ["80,443"] and j["params"]["wan"] == "ppp0" and j["params"]["ipv6"] is True, str(j.get("params")))
check("status leaks", j.get("leaks") == {"count": 0, "samples": []}, str(j.get("leaks")))

j = client.get("/api/xkeen/killswitch-preview?mode=observe").get_json()
check("preview ok", j["ok"] and "KS_MODE=observe" in j["hook"] and "KS_MARK=0xffffaac" in j["hook"], str(j)[:200])
check("preview bad mode", not client.get("/api/xkeen/killswitch-preview?mode=x").get_json()["ok"])

j = client.post("/api/xkeen/killswitch-apply", json={"mode": "observe"}).get_json()
check("apply observe", j["ok"] and j["action"] == "installed", str(j)[:300])
check("hook on router observe", ROUTER["hook"] and "KS_MODE=observe" in ROUTER["hook"] and K.HOOK_PATH in ROUTER["cron"])
j = client.get("/api/xkeen/killswitch-status").get_json()
check("status observe", j["state"] == "observe" and j["active"] and not j["drift"], str(j)[:300])

j = client.post("/api/xkeen/killswitch-apply", json={"mode": "protect"}).get_json()
check("apply protect", j["ok"] and j["action"] == "updated" and "KS_MODE=protect" in ROUTER["hook"], str(j)[:300])
j = client.get("/api/xkeen/killswitch-status").get_json()
check("status protect", j["state"] == "protect" and j["v4"]["act"] == "DROP", str(j)[:300])

# ensure без force при актуальном хуке — ничего не переустанавливает
n0 = len(ssh_calls)
r = D.keenetic_ensure_killswitch()
check("ensure current", r["action"] == "current", str(r)[:200])
check("ensure current no install", not any(K.HOOK_PATH + ".new" in c for c in ssh_calls[n0:]))

# дрейф: подменим порты в хуке → status=broken → heal чинит
ROUTER["hook"] = ROUTER["hook"].replace('KS_PORTS="80,443"', 'KS_PORTS="80"')
j = client.get("/api/xkeen/killswitch-status").get_json()
check("status broken on drift", j["state"] == "broken" and any("KS_PORTS" in d for d in j["drift"]), str(j.get("drift")))
j = client.get("/api/xkeen/killswitch-status?heal=1").get_json()
check("heal fixes drift", j["state"] == "protect" and j.get("healed", {}).get("action") == "updated", str(j.get("healed")))

j = client.post("/api/xkeen/killswitch-remove").get_json()
check("remove", j["ok"] and ROUTER["hook"] is None)
check("status after remove", client.get("/api/xkeen/killswitch-status").get_json()["state"] == "absent")

check("apply bad mode", not client.post("/api/xkeen/killswitch-apply", json={"mode": "nope"}).get_json()["ok"])

# ---------- snapshot / restore ----------
r = client.get("/api/xkeen/backup")
check("backup content-disposition", "xkeen-snapshot-download-" in r.headers.get("Content-Disposition", ""), r.headers.get("Content-Disposition"))
check("backup cmd uses shared list", any("for f in /opt/etc/ndm/netfilter.d/zz_*.sh" in c for c in ssh_calls))

snap = {"version": "v7", "timestamp": "t", "files": [
    {"path": "/opt/etc/xray/watchdog.config", "content": 'PRIMARY_TAG="x"\n'},
    {"path": "/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh", "content": "#!/bin/sh\nexit 0\n"},
    {"path": "/opt/etc/ndm/netfilter.d/proxy.sh", "content": "#!/bin/sh\n"},   # не в whitelist
    {"path": "/etc/passwd", "content": "root::0:0\n"},                          # не в whitelist
    {"path": "/opt/var/spool/cron/crontabs/root", "content": "*/1 * * * * /opt/etc/xray/watchdog.sh\n"},
]}
ROUTER["files"].clear(); ROUTER["chmod"].clear(); ROUTER["hook"] = None
n0 = len(ssh_calls)
r = client.post("/api/xkeen/restore", data={"snapshot": (io.BytesIO(json.dumps(snap).encode("utf-8")), "snap.json")},
                content_type="multipart/form-data")
j = r.get_json()
check("restore ok", j.get("ok"), str(j)[:300])
check("restore wrote hook + config", "/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh" in ROUTER["files"] and "/opt/etc/xray/watchdog.config" in ROUTER["files"], str(list(ROUTER["files"])))
check("restore skipped non-whitelist", "/opt/etc/ndm/netfilter.d/proxy.sh" not in ROUTER["files"] and "/etc/passwd" not in ROUTER["files"])
check("restore reported skipped", "Пропущены" in (j.get("stdout") or "") and "2" in (j.get("stdout") or ""), j.get("stdout"))
check("restore chmod hook", "/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh" in ROUTER["chmod"], str(ROUTER["chmod"]))
check("restore crontab applied", any(c.startswith("crontab /opt/var/spool/cron/crontabs/root") for c in ssh_calls[n0:]))
check("restore ensured killswitch", ROUTER["hook"] is not None and "Kill-switch" in (j.get("stdout") or ""), j.get("stdout"))
check("restore sh -n validated", any(c == "sh -n" for c in ssh_calls[n0:]))

# невалидный snapshot: битый JSON внутри → ничего не пишется
bad = {"files": [{"path": "/opt/etc/xray/configs/05_routing.json", "content": "{not json"}]}
ROUTER["files"].clear()
j = client.post("/api/xkeen/restore", data={"snapshot": (io.BytesIO(json.dumps(bad).encode("utf-8")), "b.json")},
                content_type="multipart/form-data").get_json()
check("restore rejects invalid", not j.get("ok") and j.get("stage") == "validate" and not ROUTER["files"], str(j)[:200])

# ---------- bootstrap ----------
ROUTER["hook"] = None; ROUTER["cron"].discard(K.HOOK_PATH)   # чистый роутер: хука ещё нет
j = client.get("/api/keenetic/bootstrap-preview").get_json()
keys = [it["key"] for it in j.get("items", [])]
check("bootstrap has killswitch item", "killswitch" in keys and keys[-1] == "killswitch", str(keys))
rt = next(it for it in j["items"] if it["key"] == "routing_template")
check("routing_template not forced", rt["action"] in ("create", "keep"), rt["action"])
ks_item = next(it for it in j["items"] if it["key"] == "killswitch")
check("killswitch item create when absent", ks_item["action"] == "create" and ks_item["mode"] == "generated")

j = client.post("/api/keenetic/bootstrap-apply", json={"keys": ["killswitch"], "overwrite_existing": False}).get_json()
check("bootstrap apply killswitch", j.get("ok") and any(l["key"] == "killswitch" and l["ok"] for l in j.get("log", [])), str(j)[:300])
check("bootstrap installed hook", ROUTER["hook"] and "KS_MODE=protect" in ROUTER["hook"])

# ---------- diagnose ----------
j = client.get("/api/xkeen/diagnose").get_json()
ids = [c["id"] for c in j.get("checks", [])]
check("diagnose has killswitch", "killswitch" in ids, str(ids))
ksc = next(c for c in j["checks"] if c["id"] == "killswitch")
check("diagnose killswitch ok", ksc["ok"] and ksc["fix_id"] is None, str(ksc)[:200])
ROUTER["hook"] = None
j = client.get("/api/xkeen/diagnose").get_json()
ksc = next(c for c in j["checks"] if c["id"] == "killswitch")
check("diagnose killswitch absent → fix", not ksc["ok"] and ksc["fix_id"] == "install_killswitch", str(ksc)[:200])
j = client.post("/api/xkeen/auto-fix", data={"fix_id": "install_killswitch"}).get_json()
check("auto-fix installs", j.get("ok") and ROUTER["hook"] is not None, str(j)[:200])

# ---------- HTTP-ошибки не превращаются в 500 ----------
check("404 stays 404", client.get("/no-such-path-zzz").status_code == 404, str(client.get("/no-such-path-zzz").status_code))
check("405 stays 405", client.get("/api/xkeen/killswitch-apply").status_code == 405, str(client.get("/api/xkeen/killswitch-apply").status_code))

# ---------- DEFAULT_WATCHDOG_CONFIG покрывает все ключи панели ----------
keys_in_default = set(re.findall(r"^([A-Z0-9_]+)=", D.DEFAULT_WATCHDOG_CONFIG, re.M))
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py"), encoding="utf-8").read()
m = re.search(r"keys_order = \[(.*?)\]", src, re.S)
keys_order = set(re.findall(r'"([A-Z0-9_]+)"', m.group(1))) if m else set()
missing = keys_order - keys_in_default
check("DEFAULT_WATCHDOG_CONFIG has all keys_order", not missing, str(sorted(missing)))

if fails:
    print("FAIL:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("test_api_offline: OK")
