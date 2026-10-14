# -*- coding: utf-8 -*-
"""Офлайн-тест «изменения панели — одним рестартом» (watchdog v35, режим применения WD_APPLY).

1. sh: куски настоящего bootstrap/watchdog.sh.cur — атомарный lock (`_wd_lock_try`), пролог режима
   применения (сверка md5 с манифестом, `sh -n` config, подмена `*.applynew.<id>`, ловушка падения),
   итог (`# v35: итог режима применения`), ожидание фонового рестарта XKeen 2.0 (`_xk_restart`),
   `_bg_restart_or_defer`. Рестарт ровно один при успехе; откат файлов и routing при провале;
   отказ без изменений при занятом lock / изменённых файлах / битом config (нет sh — SKIP).
2. Статика .cur: версия v35, config читается после lock, все «рестарт в фоне + exit» под защитой.
3. `keenetic_apply_staged` на имитации SSH: OK / ROLLBACK / FAIL / запуск не подтвердился / итога нет.
4. `keenetic_remove_subscription`: отпечатки ДО чтения, файлы только готовятся (*.applynew.<id>),
   применение — один вызов, метаданные — по свежему чтению и только при OK, старый watchdog и
   нестандартные пути — отказ до изменений, второй запрос — «занято».
5. `keenetic_write_watchdog_config(stage_id=…)` пишет только watchdog.config.applynew.<id>.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_apply_once_offline.py
"""
import hashlib
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
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  | {extra}" if not cond else ""))
    if not cond:
        fails += 1


def find_sh():
    sh = shutil.which("sh")
    if sh:
        return sh
    for p in (r"C:\Program Files\Git\bin\sh.exe", r"C:\Program Files\Git\usr\bin\sh.exe"):
        if os.path.exists(p):
            return p
    return None


src = open(CUR, encoding="utf-8", newline="").read()


def fn(name):
    m = re.search(r"^" + re.escape(name) + r"\(\) \{\n.*?^\}\n", src, re.S | re.M)
    return m.group(0) if m else ""


def between(start, end):
    a = src.index(start)
    b = src.index(end, a)
    return src[a:b]


def wfile(path, text):
    with open(path, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(text)


def rfile(path):
    return open(path, encoding="utf-8").read().strip() if os.path.exists(path) else None


# ---------- 2. статика .cur ----------
ver = int(re.search(r"watchdog\s+v(\d+)", "\n".join(src.splitlines()[:5])).group(1))
check(f"заголовок .cur — v{ver} ≥ {D.APPLY_MIN_WATCHDOG}", ver >= D.APPLY_MIN_WATCHDOG)
i_lock = src.index('if [ -n "$WD_APPLY_ID" ]; then')
i_cfg = src.index('\n. "$CFG"\n')
check("config читается ПОСЛЕ lock и подмены файлов", i_cfg > i_lock and src.count('\n. "$CFG"\n') == 1)
bad = []
lines = src.splitlines()
# Блоки, целиком пропускаемые в режиме применения (охрана — проверка ниже).
_guarded = [(src[:src.index("# ---------- v24: AI exit-IP probe")].count("\n"),
             src[:src.index("# ---------- /v24+v25")].count("\n"))]
for n, ln in enumerate(lines):
    if re.search(r"xkeen -(restart|stop)[^\n]*&\s*$", ln) and not ln.strip().startswith("#"):
        ctx = "\n".join(lines[max(0, n - 40):n + 1])
        if "_bg_restart_or_defer() {" in ctx or "WD_APPLY_ID" in ctx or any(a <= n <= b for a, b in _guarded):
            continue
        bad.append(n + 1)
check("фоновые рестарты тика — только через _bg_restart_or_defer / в блоках под WD_APPLY_ID", not bad, bad)
check("в блоке Apply нет голых `xkeen -restart` (только _xk_restart)",
      between("# ---------- Apply changes ----------", "# ---------- Failover health check").count("/opt/sbin/xkeen -restart") == 0)
_rb = between("AUTO-ROLLBACK: после отката health-check всё ещё FAIL — нужна ручная проверка", "            else")
check("после неудачного отката в режиме применения третьего рестарта нет", "_xk_restart" not in _rb, _rb)
check("AI-проба, UDP- и FD-залип в режиме применения не оцениваются",
      'elif [ -n "$WD_APPLY_ID" ]; then\n  : # v35: в режиме применения AI-проба' in src
      and '[ "$_do_recover" -eq 1 ] 2>/dev/null && [ -z "$WD_APPLY_ID" ]' in src
      and 'if [ -n "$WD_APPLY_ID" ]; then\n  : # v35: в режиме применения fd-залип' in src)

# ---------- 1. sh ----------
sh = find_sh()
PROLOGUE = between('WD_APPLY_ID="${WD_APPLY:-}"', '\n. "$CFG"\n')
EPILOGUE = between("# ---------- v35: итог режима применения", "# ---------- Failover health check")


def _paths(text, tp):
    return (text
            .replace("/opt/etc/xray/configs/04_outbounds.json", f"{tp}/ob.json")
            .replace("/opt/etc/xray/configs.bak/05_routing.template.json", f"{tp}/tmpl.json")
            .replace('"/tmp/xkeen_apply.', f'"{tp}/apply.'))


def harness(t, done=0, rb="", hc_seq="0", xt=0, wait=3, need=0, crash=False, done_before_crash=False, tc=0, force=0,
            edit_after_swap=False):
    """Скрипт: стабы + настоящие пролог/итог из .cur. Пути роутера → временная папка."""
    tp = t.replace("\\", "/")
    parts = [
        f"LOCK={tp}/lock", f"CFG={tp}/cfg", f"CONFIG_DIR={tp}", "WD_APPLY=T1", f"WD_APPLY_WAIT={wait}",
        'log() { echo "$*" >> ' + tp + '/log; }',
        'tg_alert() { echo "TG $*" >> ' + tp + '/log; }',
        "_xk_restart() { echo R >> " + tp + "/restarts; return 0; }",
        "xray_test_live() { return " + str(xt) + "; }",
        "traffic_check() { return " + str(tc) + "; }",
        f"WD_APPLY_FORCE={force}",
        "_hc_n=0",
        "health_check() {",
        '  _hc_n=$((_hc_n+1)); _r=$(echo "' + hc_seq + '" | cut -d" " -f$_hc_n); [ -z "$_r" ] && _r=0; return $_r',
        "}",
        fn("_wd_lock_try"),
        fn("_apply_health"),
        _paths(PROLOGUE, tp),
        '. "$CFG"',
        f"echo EDITED=1 > {tp}/cfg" if edit_after_swap else "",
        "_APPLY_DONE=1" if done_before_crash else "",
        "exit 3" if crash else "",
        f"_APPLY_NEED={need}",
        f'_APPLY_DONE={done}; _APPLY_RB="{rb}"',
        _paths(EPILOGUE, tp),
    ]
    return "\n".join(parts) + "\n"


def run_case(staged=("cfg", "ob.json", "tmpl.json"), lock_holder_secs=0, bad_manifest=False,
             bad_cfg=False, foreign_stage=False, **kw):
    t = tempfile.mkdtemp(prefix="wdapply_")
    tp = t.replace("\\", "/")
    for f in ("cfg", "ob.json", "tmpl.json", "05_routing.json"):
        wfile(os.path.join(t, f), f"OLD_{f.replace('.', '_')}=1\n")
    man = []
    for f in ("cfg", "ob.json", "tmpl.json"):
        md5 = "0" * 32 if bad_manifest else hashlib.md5(open(os.path.join(t, f), "rb").read()).hexdigest()
        man.append(f"{tp}/{f} {md5}")
        if f in staged:
            body = "if then fi (\n" if (bad_cfg and f == "cfg") else f"NEW_{f.replace('.', '_')}=1\n"
            wfile(os.path.join(t, f + ".applynew.T1"), body)
        if foreign_stage:
            wfile(os.path.join(t, f + ".applynew.OTHER"), "STALE=1\n")
    wfile(os.path.join(t, "apply.T1.manifest"), "\n".join(man) + "\n")
    wfile(os.path.join(t, "h.sh"), harness(t, **kw))
    holder = None
    if lock_holder_secs:
        # держатель lock — «сторож»: в его cmdline есть слово watchdog (иначе lock счёлся бы брошенным)
        wfile(os.path.join(t, "watchdog_holder.sh"), f"echo $$ > {tp}/lock\nsleep {lock_holder_secs}\n")
        holder = subprocess.Popen([sh, f"{tp}/watchdog_holder.sh"])
        for _ in range(50):
            if rfile(f"{t}/lock"):
                break
            time.sleep(0.1)
    t0 = time.time()
    rc = subprocess.run([sh, f"{tp}/h.sh"], timeout=60, stderr=subprocess.DEVNULL).returncode
    took = time.time() - t0
    if holder:
        holder.wait(timeout=30)
    out = {"rc": rc, "took": took, "res": rfile(f"{t}/apply.T1.res") or "",
           "restarts": (rfile(f"{t}/restarts") or "").count("R"), "log": rfile(f"{t}/log") or "",
           "cfg": rfile(f"{t}/cfg"), "ob": rfile(f"{t}/ob.json"), "tmpl": rfile(f"{t}/tmpl.json"),
           "rt": rfile(f"{t}/05_routing.json"),
           "left": sorted(f for f in os.listdir(t) if ".applynew." in f),
           "baks": [f for f in os.listdir(t) if ".bak-apply-" in f],
           "manifest_left": os.path.exists(os.path.join(t, "apply.T1.manifest")),
           "lock_after": rfile(f"{t}/lock")}
    shutil.rmtree(t, ignore_errors=True)
    return out


OLD = ("OLD_cfg=1", "OLD_ob_json=1", "OLD_tmpl_json=1")
NEW = ("NEW_cfg=1", "NEW_ob_json=1", "NEW_tmpl_json=1")


def files(o):
    return (o["cfg"], o["ob"], o["tmpl"])


if not sh:
    print("SKIP sh-часть (нет sh)")
else:
    o = run_case(done=1)
    check("routing пересобран (рестарт сделан тиком) → OK, лишнего рестарта нет",
          o["res"].startswith("OK routing=changed restart=1") and o["restarts"] == 0, o)
    check("файлы подменены, прежние (и routing) → *.bak-apply-<id>, свои .applynew и манифест убраны",
          files(o) == NEW and len(o["baks"]) == 4 and not o["left"] and not o["manifest_left"], o)
    check("lock отпущен после выхода", o["lock_after"] is None, o["lock_after"])

    o = run_case()
    check("routing тот же, подменён 04_outbounds → ровно один рестарт, OK routing=same",
          o["res"].startswith("OK routing=same restart=1") and o["restarts"] == 1, o)

    o = run_case(staged=("cfg",))
    check("подменён только config, routing тот же → без рестарта (xray config не читает)",
          o["res"].startswith("OK routing=same restart=0") and o["restarts"] == 0 and o["cfg"] == "NEW_cfg=1", o)

    o = run_case(staged=("cfg",), need=1)
    check("…но тик отложил свой рестарт (self-heal) → один рестарт",
          o["res"].startswith("OK routing=same restart=1") and o["restarts"] == 1, o)

    o = run_case(hc_seq="1 0")
    check("health-check провален → откат файлов + откатный рестарт (2), ROLLBACK after=ok, выход до Save state",
          o["res"].startswith("ROLLBACK reason=health restart=2 after=ok") and o["restarts"] == 2
          and files(o) == OLD and o["rc"] == 1, o)

    o = run_case(hc_seq="1 1")
    check("и после отката проверка не прошла → ROLLBACK after=fail", "after=fail" in o["res"], o)

    o = run_case(tc=1)
    check("v36: после рестарта xray не выпускает трафик → откат файлов, ROLLBACK reason=traffic",
          o["res"].startswith("ROLLBACK reason=traffic restart=2 after=fail") and files(o) == OLD, o)

    o = run_case(hc_seq="1 0", edit_after_swap=True)
    check("откат не затирает файл, изменённый ПОСЛЕ подмены (правка в панели во время применения)",
          o["res"].startswith("ROLLBACK") and o["cfg"] == "EDITED=1" and o["ob"] == "OLD_ob_json=1"
          and "не откатываю" in o["log"], o)

    o = run_case(staged=(), force=1, rb="blocked")
    check("force без файлов, кандидат отклонён → FAIL … nofiles=1 (панель не пишет «откачены»)",
          o["res"].startswith("FAIL reason=blocked restart=0 nofiles=1") and o["restarts"] == 0, o)

    o = run_case(staged=(), force=1, rb="health", done=1)
    check("force без файлов, откат routing к lastgood → ROLLBACK … nofiles=1 БЕЗ выхода (Save state сохранит LAST_BAD_MD5)",
          "ROLLBACK reason=health restart=2" in o["res"] and "nofiles=1" in o["res"] and o["rc"] == 0, o)

    o = run_case(staged=(), force=1, hc_seq="1")
    check("v36: force без подменённых файлов, проверка не прошла → FAIL nofiles, без второго рестарта и отката",
          o["res"].startswith("FAIL reason=health restart=1 nofiles=1") and o["restarts"] == 1
          and o["rt"] == "OLD_05_routing_json=1", o)

    o = run_case(staged=(), force=1)
    check("v36: WD_APPLY_FORCE без подменённых файлов → ровно один рестарт, OK",
          o["res"].startswith("OK routing=same restart=1") and o["restarts"] == 1, o)

    o = run_case(staged=())
    check("ничего не подменено и без force → без рестарта", o["res"].startswith("OK routing=same restart=0")
          and o["restarts"] == 0, o)

    o = run_case(rb="blocked")
    check("кандидат отклонён до рестарта → FAIL без рестарта, файлы откачены",
          o["res"].startswith("FAIL reason=blocked restart=0") and o["restarts"] == 0 and files(o) == OLD, o)

    o = run_case(rb="blocked", need=1)
    check("отклонён, но тик отложил рестарт → файлы откачены, отложенный рестарт сделан (один)",
          o["res"].startswith("FAIL reason=blocked restart=1 deferred=1") and o["restarts"] == 1 and files(o) == OLD, o)

    o = run_case(xt=1)
    check("xray -test живого confdir провален → FAIL xraytest без рестарта, откат",
          o["res"].startswith("FAIL reason=xraytest restart=0") and o["restarts"] == 0 and o["ob"] == "OLD_ob_json=1", o)

    o = run_case(rb="health", done=1)
    check("откат после рестарта тика → ROLLBACK, без лишних рестартов в итоге",
          o["res"].startswith("ROLLBACK reason=health restart=2") and o["restarts"] == 0, o)

    o = run_case(staged=("ob.json",))
    check("подменяется только подготовленное (config и template не тронуты)",
          files(o) == ("OLD_cfg=1", "NEW_ob_json=1", "OLD_tmpl_json=1"), o)

    o = run_case(bad_manifest=True)
    check("живые файлы изменились с подготовки (md5 ≠ манифест) → FAIL changed, ничего не подменено",
          o["res"].startswith("FAIL reason=changed restart=0") and files(o) == OLD
          and o["restarts"] == 0 and not o["left"], o)

    o = run_case(bad_cfg=True)
    check("подготовленный config с ошибкой синтаксиса → FAIL badconfig, ничего не подменено",
          o["res"].startswith("FAIL reason=badconfig restart=0") and files(o) == OLD, o)

    o = run_case(crash=True)
    check("тик упал после подмены (до итога) → ловушка: файлы и routing откачены, FAIL crash, lock снят",
          o["res"].startswith("FAIL reason=crash") and files(o) == OLD
          and o["rt"] == "OLD_05_routing_json=1" and o["lock_after"] is None, o)

    o = run_case(foreign_stage=True)
    check("чужие *.applynew.<другой id> не применяются и не трогаются",
          files(o) == NEW and o["left"] == sorted(f"{f}.applynew.OTHER" for f in ("cfg", "ob.json", "tmpl.json")), o)

    o = run_case(crash=True, done_before_crash=True)
    check("сигнал посреди рестарта применения → файлы откачены и перезапуск на прежних, FAIL crash restart=2",
          o["res"].startswith("FAIL reason=crash restart=2") and files(o) == OLD and o["restarts"] == 1, o)

    # обычный тик подбирает применение, убитое после подмены (kill -9): K1 — копии есть, итога нет;
    # K2 — ещё ждёт lock (копий нет) — не трогать
    t = tempfile.mkdtemp(prefix="wdkill_")
    tp = t.replace("\\", "/")
    for f in ("cfg", "ob.json", "tmpl.json", "05_routing.json"):
        wfile(f"{t}/{f}", f"NEW_{f}\n")
        wfile(f"{t}/{f}.bak-apply-K1", f"OLD_{f}\n")
    wfile(f"{t}/apply.K1.manifest", "x\n")
    wfile(f"{t}/apply.K2.manifest", "x\n")
    wfile(f"{t}/ob.json.applynew.K2", "PENDING\n")
    block = _paths(between("# v35: применение, убитое после подмены", "# ---------- Досыл застрявших"), tp)
    block = block.replace("/tmp/xkeen_apply.", f"{tp}/apply.").replace("find /tmp ", f"find {tp} ")
    wfile(f"{t}/k.sh", "\n".join([
        f"CFG={tp}/cfg", f"CONFIG_DIR={tp}", "WD_APPLY_ID=''",
        f"APPLY_STAGE_LIST='{tp}/cfg {tp}/ob.json {tp}/tmpl.json'", f"APPLY_ROUTING={tp}/05_routing.json",
        "log() { :; }", "tg_alert() { :; }", f"_xk_restart() {{ echo R >> {tp}/restarts; }}", block]) + "\n")
    subprocess.run([sh, f"{tp}/k.sh"], timeout=30, stderr=subprocess.DEVNULL)
    check("убитое после подмены применение: файлы и routing откачены, один рестарт, итог FAIL killed",
          [rfile(f"{t}/{f}") for f in ("cfg", "ob.json", "tmpl.json", "05_routing.json")]
          == [f"OLD_{f}" for f in ("cfg", "ob.json", "tmpl.json", "05_routing.json")]
          and (rfile(f"{t}/restarts") or "").count("R") == 1
          and (rfile(f"{t}/apply.K1.res") or "").startswith("FAIL reason=killed"))
    check("применение, ещё ждущее lock (копий нет), не тронуто",
          rfile(f"{t}/ob.json.applynew.K2") == "PENDING" and os.path.exists(f"{t}/apply.K2.manifest")
          and not os.path.exists(f"{t}/apply.K2.res"))
    shutil.rmtree(t, ignore_errors=True)

    o = run_case(lock_holder_secs=6, wait=2)
    check("тик держит lock дольше WD_APPLY_WAIT → FAIL lock, ничего не тронуто, свои .applynew убраны",
          o["res"].startswith("FAIL reason=lock restart=0") and o["restarts"] == 0 and o["rc"] == 1
          and files(o) == OLD and not o["left"], o)

    o = run_case(lock_holder_secs=2, wait=10)
    check("короткий тик → дождался lock и применил", o["res"].startswith("OK") and o["took"] >= 1.5, o)

    # атомарность lock: три претендента одновременно — побеждает ровно один
    t = tempfile.mkdtemp(prefix="wdlock_")
    tp = t.replace("\\", "/")
    wfile(f"{t}/watchdog_racer.sh",
          f"LOCK={tp}/lock\n{fn('_wd_lock_try')}\nif _wd_lock_try; then echo W >> {tp}/won; sleep 2; fi\n")
    multi = []
    for _ in range(8):
        for f in ("lock", "won"):
            try:
                os.remove(f"{t}/{f}")
            except OSError:
                pass
        ps = [subprocess.Popen([sh, f"{tp}/watchdog_racer.sh"]) for _ in range(3)]
        for pr in ps:
            pr.wait(timeout=30)
        multi.append((rfile(f"{t}/won") or "").count("W"))
    check("lock: из трёх одновременных претендентов побеждает ровно один (8 раз)", multi == [1] * 8, multi)
    # pid в lock жив, но это не сторож (переиспользован) → lock брошенный, берётся
    wfile(f"{t}/plain_holder.sh", f"echo $$ > {tp}/lock; sleep 3\n")
    for f in ("lock", "won"):
        try:
            os.remove(f"{t}/{f}")
        except OSError:
            pass
    hp = subprocess.Popen([sh, f"{tp}/plain_holder.sh"])
    for _ in range(50):
        if rfile(f"{t}/lock"):
            break
        time.sleep(0.1)
    subprocess.run([sh, f"{tp}/watchdog_racer.sh"], timeout=30)
    hp.wait(timeout=30)
    check("lock с живым НЕ-сторожем (pid переиспользован) считается брошенным", (rfile(f"{t}/won") or "") == "W")
    shutil.rmtree(t, ignore_errors=True)

    # _xk_restart: ждёт чужой рестарт, потом свой детач-рестарт и новый pid
    t = tempfile.mkdtemp(prefix="wdxk_")
    tp = t.replace("\\", "/")
    xk = (fn("_xk_restart")
          .replace("/opt/sbin/xkeen -restart", f"sh {tp}/fakexk")
          .replace("pidof xray 2>/dev/null", f"cat {tp}/pid 2>/dev/null")
          .replace("-lt 90", "-lt 6"))
    busy = "_xk_busy() { [ -f " + tp + "/restarting ]; }\n"
    wfile(f"{t}/fakexk", f"echo call >> {tp}/calls\ntouch {tp}/restarting\n"
                         f"( sleep 2; echo $(( $(cat {tp}/pid) + 1 )) > {tp}/pid; rm -f {tp}/restarting ) >/dev/null 2>&1 &\n")
    wfile(f"{t}/pid", "100\n")
    wfile(f"{t}/x.sh", "log() { echo \"$*\" >> " + tp + "/log; }\n" + busy + xk
          + "\n_xk_restart; echo rc=$? > " + tp + "/rc\n")
    t0 = time.time()
    subprocess.run([sh, f"{tp}/x.sh"], timeout=60)
    took = time.time() - t0
    check("_xk_restart: детач-рестарт — ждёт его конца и нового pid",
          rfile(f"{t}/rc") == "rc=0" and took >= 1.8, (rfile(f"{t}/rc"), took))
    # чужой рестарт уже идёт: дождаться его, потом свой (вызов fakexk — один, после чужого)
    os.remove(f"{t}/calls")
    wfile(f"{t}/restarting", "")
    subprocess.Popen([sh, "-c", f"sleep 2; rm -f {tp}/restarting"])
    t0 = time.time()
    subprocess.run([sh, f"{tp}/x.sh"], timeout=60)
    took = time.time() - t0
    check("_xk_restart: идёт чужой рестарт → сначала ждёт его, потом свой",
          rfile(f"{t}/rc") == "rc=0" and took >= 3.5 and (rfile(f"{t}/calls") or "").count("call") == 1,
          (rfile(f"{t}/rc"), took))
    # v38: во время рестарта pidof отдаёт два pid («новый старый») — это ещё не конец рестарта.
    wfile(f"{t}/pid", "100\n")
    wfile(f"{t}/fakexk", f"echo call >> {tp}/calls\n"
                         f"( sleep 1; echo '101 100' > {tp}/pid; sleep 3; echo 101 > {tp}/pid ) >/dev/null 2>&1 &\n")
    t0 = time.time()
    subprocess.run([sh, f"{tp}/x.sh"], timeout=60)
    took = time.time() - t0
    check("_xk_restart: два pid (новый + старый) — ждёт, пока старый уйдёт",
          rfile(f"{t}/rc") == "rc=0" and took >= 3.8, (rfile(f"{t}/rc"), took))
    wfile(f"{t}/pid", "100 200\n")
    wfile(f"{t}/fakexk", f"( sleep 1; echo 200 > {tp}/pid ) >/dev/null 2>&1 &\n")
    subprocess.run([sh, f"{tp}/x.sh"], timeout=60)
    check("_xk_restart: остался один из СТАРЫХ pid — не новый xray, код 1",
          rfile(f"{t}/rc") == "rc=1", rfile(f"{t}/rc"))
    wfile(f"{t}/pid", "100 555\n")
    wfile(f"{t}/fakexk", f"( sleep 1; echo '101 100 555' > {tp}/pid; sleep 2; echo '101 555' > {tp}/pid ) >/dev/null 2>&1 &\n")
    t0 = time.time()
    subprocess.run([sh, f"{tp}/x.sh"], timeout=60)
    took = time.time() - t0
    check("_xk_restart: посторонний постоянный xray (555) не мешает — готово, когда ушёл старый 100",
          rfile(f"{t}/rc") == "rc=0" and 2.8 <= took < 15, (rfile(f"{t}/rc"), took))
    wfile(f"{t}/pid", "\n")
    wfile(f"{t}/fakexk", f"( sleep 1; echo 101 > {tp}/pid ) >/dev/null 2>&1 &\n")
    subprocess.run([sh, f"{tp}/x.sh"], timeout=60)
    check("_xk_restart: xray не работал — достаточно появления нового pid", rfile(f"{t}/rc") == "rc=0", rfile(f"{t}/rc"))
    wfile(f"{t}/pid", "100\n")
    wfile(f"{t}/fakexk", "true\n")
    subprocess.run([sh, f"{tp}/x.sh"], timeout=60)
    check("_xk_restart: xray не перезапустился → код 1 и запись в лог",
          rfile(f"{t}/rc") == "rc=1" and "не закончился" in (rfile(f"{t}/log") or ""), rfile(f"{t}/rc"))
    shutil.rmtree(t, ignore_errors=True)

    # _bg_restart_or_defer
    t = tempfile.mkdtemp(prefix="wdbg_")
    tp = t.replace("\\", "/")
    bg = fn("_bg_restart_or_defer").replace("/opt/sbin/xkeen -restart", f"echo R >> {tp}/r; true")
    for mode, expect_rc, expect_r, expect_need in (("T1", "1", 0, "1"), ("", "0", 1, "")):
        wfile(f"{t}/b.sh", f"WD_APPLY_ID='{mode}'\nlog() {{ :; }}\n{bg}\n_bg_restart_or_defer x; "
                           f"echo $? > {tp}/rc; echo \"$_APPLY_NEED\" > {tp}/need; wait\n")
        try:
            os.remove(f"{t}/r")
        except OSError:
            pass
        subprocess.run([sh, f"{tp}/b.sh"], timeout=30)
        nr = (rfile(f"{t}/r") or "").count("R")
        check(f"_bg_restart_or_defer ({'применение' if mode else 'обычный тик'}): код {expect_rc}, "
              f"рестартов {expect_r}, отложенный рестарт помечен",
              rfile(f"{t}/rc") == expect_rc and nr == expect_r and rfile(f"{t}/need") == expect_need,
              (rfile(f"{t}/rc"), nr, rfile(f"{t}/need")))
    shutil.rmtree(t, ignore_errors=True)

# ---------- 3. keenetic_apply_staged ----------
time.sleep = lambda s: None
FP = "".join(f"{p} {'a' * 32}\n" for p in D.APPLY_STAGE_FILES)


def make_ssh(res_line="OK routing=same restart=1", start_ok=True):
    log = []

    def fake(cmd, timeout=15, stdin_data=None):
        log.append((cmd, stdin_data))
        out = ""
        if "nohup /opt/etc/xray/watchdog.sh" in cmd:
            out = "APPLY_STARTED\n" if start_ok else ""
        elif cmd.startswith("cat /tmp/xkeen_apply."):
            out = res_line
        return {"ok": True, "stdout": out, "stderr": "", "code": 0}
    return fake, log


p = D._parse_apply_result("ROLLBACK reason=health restart=2 after=fail\n")
check("разбор итога", p["status"] == "ROLLBACK" and p["reason"] == "health" and p["after"] == "fail", p)
check("пустой итог — статуса нет", D._parse_apply_result("")["status"] == "")
check("мусор — статуса нет", D._parse_apply_result("garbage x=1")["status"] == "")

D.keenetic_ssh, log = make_ssh()
r = D.keenetic_apply_staged("RID-1", FP, poll_every=0)
cmds = [c for c, _ in log]
check("apply_staged OK", r["ok"] and r["status"] == "OK" and not r["pending"], r)
check("манифест уходит ДО запуска, запуск — WD_APPLY=<id> nohup watchdog.sh",
      cmds[0] == "cat > /tmp/xkeen_apply.RID-1.manifest" and log[0][1] == FP
      and cmds[1].startswith("WD_APPLY=RID-1 nohup /opt/etc/xray/watchdog.sh "), cmds[:2])
check("панель сама xkeen -restart не зовёт", not any("xkeen -restart" in c for c in cmds), cmds)
check("команды SSH — только ASCII", all(all(ord(ch) < 128 for ch in c) for c in cmds))
check("id операции — только [A-Za-z0-9-]", re.fullmatch(r"[A-Za-z0-9-]+", D._apply_new_id()) is not None)

D.keenetic_ssh, log = make_ssh("ROLLBACK reason=health restart=2 after=fail")
r = D.keenetic_apply_staged("RID-1", FP, poll_every=0)
check("ROLLBACK after=fail → ok=False, текст про откат и ручную проверку",
      not r["ok"] and "откачены" in r["stderr"] and "ручная проверка" in r["stderr"], r)

D.keenetic_ssh, log = make_ssh("FAIL reason=changed restart=0 files=x")
r = D.keenetic_apply_staged("RID-1", FP, poll_every=0)
check("FAIL changed → понятный текст", not r["ok"] and "изменились" in r["stderr"], r)

D.keenetic_ssh, log = make_ssh(start_ok=False)
r = D.keenetic_apply_staged("RID-1", FP, poll_every=0)
check("запуск не подтвердился → pending (процесс мог стартовать), подготовленное не трогается",
      r["pending"] and not r["ok"] and not any(c.startswith("rm -f") for c, _ in log), r)

D.keenetic_ssh, log = make_ssh(res_line="")
r = D.keenetic_apply_staged("RID-1", FP, poll_timeout=0.05, poll_every=0)
check("итога нет → pending, без отката и без rm",
      r["pending"] and not r["ok"] and not any(c.startswith("rm -f") for c, _ in log), r)

# ---------- 4. keenetic_remove_subscription ----------
URL = "https://prov.example/s/abc"
OBS = [{"tag": "A1", "protocol": "vless", "sub_url": URL}, {"tag": "A2", "protocol": "vless", "sub_url": URL},
       {"tag": "S3", "protocol": "vless"}, {"tag": "direct", "protocol": "freedom"}]
TARGETS = {"primary_tag": "S3", "ai_tag": "S3", "yt_tag": "", "foreign_tag": "", "ipv6_tag": "",
           "failover_tag": "S3", "failover_tags": ["S3", "A1", "A2"], "ai_failover_tags": ["A2"],
           "yt_failover_tags": [], "foreign_failover_tags": []}
SUBS = {URL: {"name": "Prov"}, "pbk1": {"sub_url": URL}, "other": {"name": "Keep"}}
calls = {}
order = []
REAL_BULK = D.keenetic_remove_outbounds_bulk


def setup(apply_status="OK", wd_ver=36, fresh_subs=None):
    calls.clear()
    order.clear()
    D.keenetic_get_outbounds = lambda: list(OBS)
    D.keenetic_get_watchdog_targets = lambda strict=False: dict(TARGETS)
    _reads = [dict(SUBS), dict(fresh_subs if fresh_subs is not None else SUBS)]
    D.keenetic_read_sub_meta = lambda: _reads.pop(0) if len(_reads) > 1 else _reads[0]
    D.keenetic_ensure_watchdog_current = lambda: {"action": "current", "remote_ver": wd_ver, "local_ver": wd_ver}

    def fp():
        order.append("fingerprint")
        return FP
    D.keenetic_apply_fingerprint = fp

    def wcfg(updates, stage_id=None):
        order.append("config")
        calls.setdefault("wcfg", []).append((updates, stage_id))
        return {"ok": True}
    D.keenetic_write_watchdog_config = wcfg

    def rf(path, timeout=15):
        order.append("read " + path.rsplit("/", 1)[-1])
        return json.dumps({"outbounds": OBS})
    D.keenetic_read_file = rf

    def bulk(tags, stage_id=None, targets=None):
        calls.setdefault("bulk", []).append((list(tags), stage_id, targets))
        return REAL_BULK(tags, stage_id=stage_id, targets=targets)
    D.keenetic_remove_outbounds_bulk = bulk
    D._template_ips_plan = lambda ob=None: (calls.setdefault("tplan", []).append(ob),
                                            {"ok": True, "changed": True, "added": [], "removed": ["1.2.3.4"],
                                             "new_tmpl": "NEWT", "tmpl_path": "/t/tmpl.json"})[1]

    def ap(run_id, fingerprint, **kw):
        calls.setdefault("apply", []).append((run_id, fingerprint))
        ok = apply_status == "OK"
        return {"ok": ok, "status": apply_status, "reason": "" if ok else "health", "restart": "1",
                "routing": "same", "pending": False, "stderr": "" if ok else "откачены"}
    D.keenetic_apply_staged = ap

    def wsub(new):
        calls.setdefault("wsub", []).append(new)
        return True
    D.keenetic_write_sub_meta = wsub

    def ssh(cmd, timeout=15, stdin_data=None):
        calls.setdefault("ssh", []).append((cmd, stdin_data))
        return {"ok": True, "stdout": "BAKOK", "stderr": "", "code": 0}
    D.keenetic_ssh = ssh


setup()
r = D.keenetic_remove_subscription(URL)
ssh_cmds = [c for c, _ in calls.get("ssh", [])]
rid = (calls.get("apply") or [("?", "")])[0][0]
check("удаление подписки: ok", r.get("ok"), r)
check("отпечатки сняты ДО чтения файлов (config, 04_outbounds)",
      order and order[0] == "fingerprint" and "config" in order, order)
check("config — только подготовка под id операции, одной записью, цепочки без узлов подписки",
      calls.get("wcfg") == [({"FAILOVER_TAGS": "S3", "AI_FAILOVER_TAGS": ""}, rid)], calls.get("wcfg"))
check("bulk — подготовка под тем же id, с цепочками уже без узлов подписки",
      len(calls.get("bulk", [])) == 1 and calls["bulk"][0][1] == rid
      and calls["bulk"][0][2]["failover_tags"] == ["S3"] and calls["bulk"][0][2]["ai_failover_tags"] == [], calls.get("bulk"))
check("04_outbounds пишется только в .applynew.<id> (живой не трогается)",
      any(c.endswith(f"04_outbounds.json.applynew.{rid}") for c in ssh_cmds)
      and not any(re.search(r"cat > \S*04_outbounds\.json$", c) for c in ssh_cmds), ssh_cmds)
check("template пишется только в .applynew.<id>", f"cat > /t/tmpl.json.applynew.{rid}" in ssh_cmds, ssh_cmds)
check("template считается по НОВОМУ списку outbound'ов (без узлов подписки)",
      calls.get("tplan") and [o["tag"] for o in calls["tplan"][0]["outbounds"]] == ["S3", "direct"], calls.get("tplan"))
check("применение — ровно один вызов, с отпечатками", len(calls.get("apply", [])) == 1 and calls["apply"][0][1] == FP)
check("панель сама xkeen -restart не зовёт", not any("xkeen -restart" in c for c in ssh_cmds), ssh_cmds)
check("метаданные: удалены ключ и pbk-тень, чужая запись цела",
      calls.get("wsub") and set(calls["wsub"][0]) == {"other"}, calls.get("wsub"))
check("сообщение про один перезапуск и IP template",
      "одним перезапуском" in r.get("stdout", "") and "−1" in r.get("stdout", ""), r.get("stdout"))

setup(fresh_subs={**SUBS, "new-sub": {"name": "Added meanwhile"}})
r = D.keenetic_remove_subscription(URL)
check("метаданные — по свежему чтению: подписка, добавленная за время применения, не теряется",
      calls.get("wsub") and set(calls["wsub"][0]) == {"other", "new-sub"}, calls.get("wsub"))

setup(apply_status="ROLLBACK")
r = D.keenetic_remove_subscription(URL)
check("применение откатилось → ok=False, removed пуст, метаданные не тронуты",
      not r.get("ok") and r.get("removed") == [] and not calls.get("wsub") and r.get("phase") == "apply", (r, calls.get("wsub")))

setup(wd_ver=34)
r = D.keenetic_remove_subscription(URL)
check("watchdog на роутере старее нужной версии → отказ ДО любых изменений",
      not r.get("ok") and r.get("phase") == "watchdog" and not calls.get("wcfg") and not calls.get("bulk")
      and not calls.get("ssh"), (r, calls))

setup()
_saved = D.cfg.KEENETIC_XRAY_CONFIGS
D.cfg.KEENETIC_XRAY_CONFIGS = "/opt/custom/configs"
r = D.keenetic_remove_subscription(URL)
D.cfg.KEENETIC_XRAY_CONFIGS = _saved
check("нестандартные пути XKeen → отказ до изменений", not r.get("ok") and r.get("phase") == "paths"
      and not calls.get("wcfg") and not calls.get("ssh"), r)

# второй запрос во время первого — «занято»
setup()
gate = threading.Event()
entered = threading.Event()
D.keenetic_apply_staged = lambda run_id, fp, **kw: (entered.set(), gate.wait(5),
                                                    {"ok": True, "status": "OK", "reason": "", "restart": "1",
                                                     "routing": "same", "pending": False, "stderr": ""})[2]
res1 = {}
th = threading.Thread(target=lambda: res1.update(D.keenetic_remove_subscription(URL)))
th.start()
entered.wait(5)
r2 = D.keenetic_remove_subscription(URL)
gate.set()
th.join(10)
check("второй запрос во время первого → busy, без изменений", r2.get("busy") and not r2.get("ok"), r2)
check("первый запрос после этого завершился успешно", res1.get("ok"), res1)

# ---------- 5. write_watchdog_config(stage_id=…) ----------
import importlib  # noqa: E402
D = importlib.reload(D)
ran = []
D.keenetic_ensure_watchdog_current = lambda: {"action": "current"}
D.keenetic_read_watchdog_config = lambda: {"PRIMARY_TAG": "S3"}
D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None: (ran.append(cmd), {"ok": True, "stdout": "", "stderr": "", "code": 0})[1]
r = D.keenetic_write_watchdog_config({"FAILOVER_TAGS": "S3"}, stage_id="RID-9")
check("stage_id: только watchdog.config.applynew.<id>, без прогона и без живой записи",
      r.get("ok") and ran == ["cat > /opt/etc/xray/watchdog.config.applynew.RID-9"], ran)
ran.clear()
r = D.keenetic_write_watchdog_config({"FAILOVER_TAGS": "S3"})
check("по умолчанию — живая запись и прогон watchdog.sh, как раньше",
      any(c == "cat > /opt/etc/xray/watchdog.config" for c in ran) and any(c.strip() == "/opt/etc/xray/watchdog.sh" for c in ran), ran)

print("test_apply_once_offline: " + ("OK" if fails == 0 else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
