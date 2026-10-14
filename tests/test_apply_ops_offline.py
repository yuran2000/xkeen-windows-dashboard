# -*- coding: utf-8 -*-
"""Офлайн-тест: операции панели с рестартом переведены на «один рестарт» (watchdog v36), проба трафика.

1. sh: `traffic_check` (socks-health → direct, «нет интернета», curl 127) / `_traffic_ok_or_heal` (кулдаун)
   из настоящего .cur с подменным curl (нет sh — SKIP).
2. `keenetic_apply_begin` / `keenetic_apply_commit`: недоступен → старый путь; подготовка файлов под id.
3. Добавление, удаление одного и нескольких outbound'ов: один вызов применения, без xkeen -restart
   панелью и без второго синка template; режим недоступен — прежний путь (запись + рестарт).
4. «Обновить из подписки»: 04_outbounds + чистка цепочек + IP template — одним применением.
5. Синк IP template с рестартом, restore, «Применить структуру» — через применение с force.
6. Общий лок: вторая операция во время первой — busy.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_apply_ops_offline.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import dashboard as D  # noqa: E402

CUR = os.path.join(ROOT, "bootstrap", "watchdog.sh.cur")
src = open(CUR, encoding="utf-8", newline="").read()
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  | {extra}" if not cond else ""))
    if not cond:
        fails += 1


def fn(name):
    m = re.search(r"^" + re.escape(name) + r"\(\) \{\n.*?^\}\n", src, re.S | re.M)
    return m.group(0) if m else ""


def find_sh():
    sh = shutil.which("sh")
    if sh:
        return sh
    for p in (r"C:\Program Files\Git\bin\sh.exe", r"C:\Program Files\Git\usr\bin\sh.exe"):
        if os.path.exists(p):
            return p
    return None


def wfile(path, text):
    with open(path, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(text)


# ---------- 1. sh: проба трафика ----------
sh = find_sh()
check("в .cur есть traffic_check, _traffic_ok_or_heal, _apply_health",
      bool(fn("traffic_check")) and bool(fn("_traffic_ok_or_heal")) and bool(fn("_apply_health")))
check("обычный тик после удачного рестарта зовёт _traffic_ok_or_heal (без отката routing)",
      '[ -z "$WD_APPLY_ID" ] && { _traffic_ok_or_heal || :; }' in src)
check("правило socks-health → direct в generate_routing и self-heal входа socks-health",
      '"inboundTag":["socks-health"],"outboundTag":"direct"' in src and '"tag":"socks-health"' in src)
if not sh:
    print("SKIP sh-часть (нет sh)")
else:
    def run_traffic(via_xray, direct, has_inbound=True, has_rule=True, curl127=False, heal=False,
                    heal_fixes=False, last_heal=0, heal_nonet=False):
        """via_xray / direct — отвечает ли запрос через socks-health и мимо xray."""
        t = tempfile.mkdtemp(prefix="wdtc_")
        tp = t.replace("\\", "/")
        wfile(f"{t}/inb.json", json.dumps({"inbounds": [{"tag": "socks-health"}] if has_inbound else []}))
        wfile(f"{t}/rt.json", '{"rules":[{"inboundTag":["socks-health"]}]}' if has_rule else "{}")
        if via_xray:
            open(f"{t}/healed", "w").close()
        if last_heal:
            wfile(f"{t}/heal", f"{int(time.time()) - last_heal}\n")
        wfile(f"{t}/curl", "#!/bin/sh\nsocks=0\nfor a in \"$@\"; do case \"$a\" in --socks5-hostname) socks=1;; esac; done\n"
                           f"echo \"socks=$socks\" >> {tp}/calls\n"
                           + (f"exit 127\n" if curl127 else "")
                           + f"if [ $socks = 1 ]; then [ -f {tp}/healed ] && {{ printf 204; exit 0; }}; printf 000; exit 28; fi\n"
                           + f"[ -f {tp}/nonet ] && {{ printf 000; exit 6; }}\n"
                           + (f"printf 200; exit 0\n" if direct else "printf 000; exit 6\n"))
        body = (fn("traffic_check").replace("/opt/etc/xray/configs/03_inbounds.json", f"{tp}/inb.json")
                .replace("/opt/etc/xray/configs/05_routing.json", f"{tp}/rt.json").replace("sleep 3", "sleep 0"))
        restart_stub = ("_xk_restart() { echo R >> " + tp + "/restarts; "
                        + (f"touch {tp}/healed; " if heal_fixes else "")
                        + (f"touch {tp}/nonet; " if heal_nonet else "") + "}")
        script = "\n".join([
            "TRAFFIC_HEAL_COOLDOWN=1800",
            "curl() { sh " + tp + "/curl \"$@\"; }",
            "TRAFFIC_PROBE_URLS='https://a/ http://b/'", f"TRAFFIC_HEAL_STATE={tp}/heal",
            "log() { echo \"$*\" >> " + tp + "/log; }", "tg_alert() { echo TG >> " + tp + "/log; }",
            restart_stub, body, fn("_traffic_ok_or_heal"),
            ("_traffic_ok_or_heal" if heal else "traffic_check") + f"; echo $? > {tp}/rc"])
        wfile(f"{t}/t.sh", script + "\n")
        subprocess.run([sh, f"{tp}/t.sh"], timeout=60)
        rd = lambda n: open(f"{t}/{n}", encoding="utf-8").read() if os.path.exists(f"{t}/{n}") else ""  # noqa: E731
        out = {"rc": rd("rc").strip(), "calls": rd("calls").split(), "restarts": rd("restarts").count("R"),
               "log": rd("log"), "heal_written": bool(rd("heal").strip())}
        shutil.rmtree(t, ignore_errors=True)
        return out

    o = run_traffic(via_xray=True, direct=True)
    check("xray выпускает напрямую → 0, первым же запросом", o["rc"] == "0" and o["calls"] == ["socks=1"], o)
    o = run_traffic(via_xray=False, direct=True)
    check("через xray нет, а роутер мимо xray выходит → 1 (xray сломан)", o["rc"] == "1" and "TRAFFIC" in o["log"], o)
    o = run_traffic(via_xray=False, direct=False)
    check("нет интернета и у самого роутера → 0 (xray не виноват)", o["rc"] == "0" and "нет интернета" in o["log"], o)
    o = run_traffic(via_xray=False, direct=True, curl127=True)
    check("curl не запускается (127) → 0 (мерить нечем)", o["rc"] == "0", o)
    o = run_traffic(via_xray=False, direct=True, has_inbound=False)
    check("нет входа socks-health → 0, без запросов", o["rc"] == "0" and not o["calls"], o)
    o = run_traffic(via_xray=False, direct=True, has_rule=False)
    check("вход есть, правила в живом routing ещё нет → 0", o["rc"] == "0" and not o["calls"], o)
    o = run_traffic(via_xray=False, direct=True, heal=True, heal_fixes=True)
    check("обычный тик: xray сломан → один лечебный рестарт, после него трафик пошёл → 0, время лечения записано",
          o["rc"] == "0" and o["restarts"] == 1 and o["heal_written"] and "TG" in o["log"], o)
    o = run_traffic(via_xray=False, direct=True, heal=True)
    check("лечение не помогло → 1, рестарт один, алерт", o["rc"] == "1" and o["restarts"] == 1 and "TG" in o["log"], o)
    o = run_traffic(via_xray=False, direct=True, heal=True, last_heal=300)
    check("лечебный рестарт был 5 мин назад → кулдаун: без рестарта и без алерта",
          o["rc"] == "1" and o["restarts"] == 0 and "кулдаун" in o["log"] and "TG" not in o["log"], o)
    o = run_traffic(via_xray=True, direct=True, heal=True)
    check("трафик есть → без рестарта", o["rc"] == "0" and o["restarts"] == 0, o)
    o = run_traffic(via_xray=False, direct=True, heal=True, heal_nonet=True)
    check("после лечения пропал интернет у роутера → 0, но алерта «трафик пошёл» нет",
          o["rc"] == "0" and o["restarts"] == 1 and "TG" not in o["log"] and "алерт не шлю" in o["log"], o)

# ---------- 2. begin / commit ----------
time.sleep = lambda s: None
FP = "".join(f"{p} {'a' * 32}\n" for p in D.APPLY_STAGE_FILES)
SSH_LOG = []


def ssh_ok(cmd, timeout=15, stdin_data=None):
    SSH_LOG.append((cmd, stdin_data))
    out = "BAKOK"
    if "[x]ray run" in cmd:
        out = "XRAY_UP"
    return {"ok": True, "stdout": out, "stderr": "", "code": 0}


D.keenetic_ssh = ssh_ok
D.keenetic_ensure_watchdog_current = lambda: {"action": "current", "remote_ver": 34}
r = D.keenetic_apply_begin()
check("begin: старый watchdog → unsupported (идти старым путём)", not r["ok"] and r["unsupported"], r)
D.keenetic_ensure_watchdog_current = lambda: {"action": "current", "remote_ver": 36}
D.keenetic_apply_fingerprint = lambda: None
r = D.keenetic_apply_begin()
check("begin: отпечатки не снялись → отказ (не unsupported)", not r["ok"] and not r["unsupported"], r)
D.keenetic_apply_fingerprint = lambda: FP
r = D.keenetic_apply_begin()
check("begin: ок → run_id и отпечатки", r["ok"] and r["fingerprint"] == FP and re.fullmatch(r"[A-Za-z0-9-]+", r["run_id"]), r)

APPLIED = []
REAL_STAGED = D.keenetic_apply_staged


def fake_staged(run_id, fingerprint, force=False, **kw):
    APPLIED.append({"run_id": run_id, "force": force})
    return {"ok": True, "status": "OK", "reason": "", "restart": "1", "routing": "same", "pending": False, "stderr": ""}


D.keenetic_apply_staged = fake_staged
WCFG = []
D.keenetic_write_watchdog_config = lambda updates, stage_id=None: (WCFG.append((updates, stage_id)), {"ok": True})[1]
D._template_ips_plan = lambda ob=None, tmpl_raw=None: {"ok": True, "changed": True, "added": ["9.9.9.9"], "removed": [],
                                                      "new_tmpl": (tmpl_raw or "LIVE") + "+IPS", "tmpl_path": "x"}
SSH_LOG.clear()
ctx = {"ok": True, "run_id": "R1", "fingerprint": FP}
info = D.keenetic_apply_commit(ctx, new_outbounds={"outbounds": [{"tag": "A"}]}, config_updates={"FAILOVER_TAGS": "S"})
cmds = [c for c, _ in SSH_LOG]
check("commit: 04_outbounds → .applynew.R1 (JSON), config → stage_id R1, template с IP → .applynew.R1",
      f"cat > {D.APPLY_STAGE_FILES[1]}.applynew.R1" in cmds
      and json.loads(dict(SSH_LOG)[f"cat > {D.APPLY_STAGE_FILES[1]}.applynew.R1"])["outbounds"][0]["tag"] == "A"
      and WCFG == [({"FAILOVER_TAGS": "S"}, "R1")]
      and dict(SSH_LOG).get(f"cat > {D.APPLY_STAGE_FILES[2]}.applynew.R1") == "LIVE+IPS", (cmds, WCFG))
check("commit: одно применение, итог с IP template", info["ok"] and len(APPLIED) == 1
      and info["template_added"] == ["9.9.9.9"] and "одним перезапуском" in D._apply_note(info), info)
APPLIED.clear(); SSH_LOG.clear()
info = D.keenetic_apply_commit(ctx, new_template="T", sync_template_ips=False, force=True)
check("commit: готовый template без пересчёта + force", dict(SSH_LOG).get(f"cat > {D.APPLY_STAGE_FILES[2]}.applynew.R1") == "T"
      and APPLIED == [{"run_id": "R1", "force": True}], (SSH_LOG, APPLIED))
APPLIED.clear(); SSH_LOG.clear()


def ssh_fail_ob(cmd, timeout=15, stdin_data=None):
    SSH_LOG.append((cmd, stdin_data))
    return {"ok": ".applynew" not in cmd or "rm -f" in cmd, "stdout": "", "stderr": "x", "code": 0}


D.keenetic_ssh = ssh_fail_ob
info = D.keenetic_apply_commit(ctx, new_outbounds={"outbounds": []})
check("commit: сбой подготовки → без применения, подготовленное убрано, «ничего не изменено»",
      not info["ok"] and not APPLIED and any(c.startswith("rm -f") and ".applynew.R1" in c for c, _ in SSH_LOG)
      and "ничего не изменено" in info["stderr"], info)
D.keenetic_ssh = ssh_ok

# ---------- 3. добавление / удаление ----------
OBS = {"outbounds": [{"tag": "S3", "protocol": "vless", "settings": {"vnext": [{"address": "1.1.1.1", "port": 443}]}},
                     {"tag": "OLD1", "protocol": "vless", "settings": {"vnext": [{"address": "2.2.2.2", "port": 443}]}},
                     {"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}]}
TARGETS = {"primary_tag": "S3", "ai_tag": "", "yt_tag": "", "foreign_tag": "", "ipv6_tag": "", "failover_tag": "S3",
           "failover_tags": [], "ai_failover_tags": [], "yt_failover_tags": [], "foreign_failover_tags": []}
D.keenetic_read_file = lambda path, timeout=15: json.dumps(OBS) if path.endswith("04_outbounds.json") else "{}"
D.keenetic_get_watchdog_targets = lambda strict=False: dict(TARGETS)
COMMITS = []


def fake_commit(ctx, **kw):
    COMMITS.append(kw)
    return {"ok": True, "status": "OK", "reason": "", "restart": "1", "routing": "same", "pending": False, "stderr": "",
            "template_added": [], "template_removed": ["2.2.2.2"], "template_note": ""}


REAL_BEGIN, REAL_COMMIT = D.keenetic_apply_begin, D.keenetic_apply_commit
D.keenetic_apply_begin = lambda: {"ok": True, "run_id": "R2", "fingerprint": FP}
D.keenetic_apply_commit = fake_commit
SSH_LOG.clear()
VLESS = "vless://11111111-2222-3333-4444-555555555555@3.3.3.3:443?security=reality&pbk=PBK&sni=ex.com&fp=chrome&type=tcp#NewNode"
r = D.keenetic_add_outbound(VLESS)
tags = [o["tag"] for o in COMMITS[-1]["new_outbounds"]["outbounds"]] if COMMITS else []
check("добавление: одно применение, новый узел перед direct, панель не перезапускает",
      r.get("ok") and r.get("applied_once") and len(COMMITS) == 1 and tags.index("NewNode") == tags.index("direct") - 1
      and not any("xkeen -restart" in c for c, _ in SSH_LOG), (r, tags))
COMMITS.clear(); SSH_LOG.clear()
r = D.keenetic_remove_outbound("OLD1")
check("удаление одного: одно применение без OLD1, без рестарта панелью",
      r.get("ok") and r.get("applied_once") and len(COMMITS) == 1
      and [o["tag"] for o in COMMITS[0]["new_outbounds"]["outbounds"]] == ["S3", "direct", "block"]
      and not any("xkeen -restart" in c for c, _ in SSH_LOG), r)
COMMITS.clear(); SSH_LOG.clear()
r = D.keenetic_remove_outbounds_bulk(["OLD1", "S3"])
check("удаление нескольких: защищённый S3 пропущен, одно применение без OLD1",
      r.get("ok") and r.get("applied_once") and r.get("removed") == ["OLD1"] and "S3" in r.get("skipped", {})
      and len(COMMITS) == 1, r)
COMMITS.clear()
D.keenetic_apply_commit = lambda ctx, **kw: {"ok": False, "status": "ROLLBACK", "reason": "health", "pending": False,
                                             "stderr": "откачены"}
r = D.keenetic_remove_outbound("OLD1")
check("применение откатилось → ok=False с текстом", not r.get("ok") and "откачены" in r.get("stderr", ""), r)
D.keenetic_apply_commit = fake_commit

# режим недоступен → прежний путь (запись живого файла + рестарт панелью)
D.keenetic_apply_begin = lambda: {"ok": False, "unsupported": True, "reason": "old"}
SSH_LOG.clear()
r = D.keenetic_remove_outbound("OLD1")
cmds = [c for c, _ in SSH_LOG]
check("недоступен → старый путь: живой 04_outbounds и xkeen -restart, без применения",
      r.get("ok") and not r.get("applied_once") and not COMMITS
      and any(re.search(r"cat > \S*04_outbounds\.json$", c) for c in cmds) and any("xkeen -restart" in c for c in cmds), cmds)
D.keenetic_apply_begin = lambda: {"ok": True, "run_id": "R2", "fingerprint": FP}

# маршруты: при applied_once второй синк template не делается
SYNC_CALLS = []
D._auto_sync_template_ips_silent = lambda: (SYNC_CALLS.append(1), {"changed": True, "added": [], "removed": []})[1]
D.keenetic_set_meta = lambda *a, **k: True
D.keenetic_remove_meta_entry = lambda *a, **k: True
client = D.app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
for url, data in (("/api/xkeen/add", {"payload": VLESS}), ("/api/xkeen/remove", {"tag": "OLD1"}),
                  ("/api/xkeen/remove-bulk", {"tags": "OLD1"})):
    resp = client.post(url, data=data).get_json()
    check(f"{url}: applied_once → без второго синка template", resp.get("ok") and not SYNC_CALLS, resp)
    SYNC_CALLS.clear()

# ---------- 4. «Обновить из подписки» ----------
SUB = "https://prov.example/sub"
OBS2 = {"outbounds": [{"tag": "S3", "protocol": "vless", "settings": {"vnext": [{"address": "1.1.1.1", "port": 443}]}},
                      {"tag": "Gone", "protocol": "vless", "settings": {"vnext": [{"address": "5.5.5.5", "port": 443}]}},
                      {"tag": "direct", "protocol": "freedom"}]}
D.keenetic_read_file = lambda path, timeout=15: json.dumps(OBS2) if path.endswith("04_outbounds.json") else "{}"
D._fetch_subscription = lambda url: ([VLESS], "", {})
D.keenetic_read_meta = lambda: {"Gone": {"sub_url": SUB}}
D.keenetic_write_meta = lambda meta: True
D.keenetic_read_sub_meta = lambda: {}
D.keenetic_write_sub_meta = lambda subs: True
T2 = dict(TARGETS, failover_tags=["S3", "Gone"])
D.keenetic_get_watchdog_targets = lambda strict=False: dict(T2)
WCFG.clear(); COMMITS.clear(); SYNC_CALLS.clear(); SSH_LOG.clear()
resp = client.post("/api/xkeen/subscription-sync", data={"url": SUB, "add_new": "1", "remove_orphans": "1"}).get_json()
ob_tags = [o["tag"] for o in COMMITS[0]["new_outbounds"]["outbounds"]] if COMMITS else []
check("синк подписки: одно применение — новый узел добавлен, orphan удалён, цепочка в том же применении",
      resp.get("ok") and len(COMMITS) == 1 and "NewNode" in ob_tags and "Gone" not in ob_tags
      and COMMITS[0].get("config_updates") == {"FAILOVER_TAGS": "S3"}, (resp, COMMITS))
check("синк подписки: ни отдельной записи config, ни второго синка template, ни рестарта панелью",
      not WCFG and not SYNC_CALLS and not any("xkeen -restart" in c for c, _ in SSH_LOG)
      and "одним перезапуском" in resp.get("stdout", ""), (WCFG, SYNC_CALLS))

# ---------- 5. template с рестартом / restore / структура ----------
D.keenetic_read_file = lambda path, timeout=15: (json.dumps(OBS) if path.endswith("04_outbounds.json")
                                                 else '{"routing":{"rules":[{"ip":["2.2.2.2"],"outboundTag":"direct"}]}}')
COMMITS.clear()
resp = client.post("/api/xkeen/sync-template-ips", data={"apply": "1", "restart": "1"}).get_json()
check("синк IP template с рестартом: одно применение с готовым template и force",
      resp.get("ok") and resp.get("restart_ok") and len(COMMITS) == 1 and COMMITS[0].get("force") is True
      and COMMITS[0].get("sync_template_ips") is False and COMMITS[0].get("new_template"), (resp, COMMITS))
import inspect  # noqa: E402
_rs = inspect.getsource(D.api_xkeen_restore)
check("restore: после записи файлов — применение с force (без «watchdog.sh && xkeen -restart» при поддержке)",
      "keenetic_apply_commit(ctx, force=True)" in _rs)
_ss = inspect.getsource(D.keenetic_sync_structure)
check("«Применить структуру»: template подготовленным файлом и применение с force",
      "keenetic_apply_commit(ctx, new_template=new_tmpl_text, sync_template_ips=True, force=True)" in _ss)

# ---------- 6. общий лок ----------
gate, entered = threading.Event(), threading.Event()
D.keenetic_apply_commit = lambda ctx, **kw: (entered.set(), gate.wait(5), fake_commit(ctx, **kw))[2]
res1 = {}
th = threading.Thread(target=lambda: res1.update(D.keenetic_remove_outbound("OLD1")))
th.start()
entered.wait(5)
r2 = D.keenetic_add_outbound(VLESS)
r3 = client.post("/api/xkeen/subscription-sync", data={"url": SUB}).get_json()
r4 = client.post("/api/xkeen/sync-template-ips", data={"apply": "0"}).get_json()
gate.set()
check("busy понятен любому JS (stderr, error, summary)", r3.get("busy") and r3.get("summary") and r3.get("error"), r3)
check("предпросмотр синка IP template во время применения работает (лок только для записи)",
      not r4.get("busy") and r4.get("ok"), r4)
th.join(10)
check("вторая операция во время первой (добавление, синк подписки) → busy",
      r2.get("busy") and r3.get("busy") and not r2.get("ok"), (r2, r3))
check("первая операция завершилась", res1.get("ok"), res1)

print("test_apply_ops_offline: " + ("OK" if fails == 0 else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
