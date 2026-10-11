# -*- coding: utf-8 -*-
"""Офлайн-тест карантина возврата из bootstrap/watchdog.sh.cur (v33): растущий срок `_deep_hold`
и проверка основного канала сквозной пробой сразу после возврата (`deep_probe_check`, поля D*_QN,
D*_CHK).

Функции вырезаются из .cur и гоняются в sh с подменёнными curl, date, log и tg_alert (без роутера
и сети). Нужен POSIX sh (на Windows — из Git for Windows); его нет — тест пропускается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_deep_quarantine.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUR = os.path.join(ROOT, "bootstrap", "watchdog.sh.cur")

fails = 0


def check(cond, msg, extra=""):
    global fails
    print(("  ✅ " if cond else "  ❌ ") + msg + ("" if cond else f"  | {extra}"))
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


def extract(src, name):
    m = re.search(r"^" + re.escape(name) + r"\(\) \{\n.*?^\}\n", src, re.S | re.M)
    return m.group(0) if m else None


src = open(CUR, encoding="utf-8", newline="").read()
ver = int(re.search(r"watchdog\s+v(\d+)", "\n".join(src.splitlines()[:40])).group(1))
check(ver >= 33, f"заголовок .cur — watchdog v{ver} (нужен ≥ 33)")
fns = {n: extract(src, n) for n in ("_num", "_state_field", "_log_every5", "_deep_hold", "deep_probe_check")}
check(all(fns.values()), "в .cur есть _num, _state_field, _log_every5, _deep_hold, deep_probe_check",
      [n for n, v in fns.items() if not v])
check(src.count('"$_dpri_hold"') >= 1 and src.count('"$_dyt_hold"') >= 2 and src.count('"$_dfgn_hold"') >= 2,
      "карантин ролей сравнивается с растущим сроком (_dpri_hold/_dyt_hold/_dfgn_hold), а не с DEEP_RETURN_HOLD")
check(re.search(r"\)\) -lt \"\$DEEP_RETURN_HOLD\"", src) is None, "возраст карантина нигде не сравнивается с голым DEEP_RETURN_HOLD")
for f in ("DPRI_QN", "DPRI_CHK", "DPRI_HOLD", "DYT_QN", "DYT_CHK", "DYT_HOLD", "DFGN_QN", "DFGN_CHK", "DFGN_HOLD"):
    check(f"{f}=${f}" in src, f"state пишет {f}")

sh = find_sh()
if not sh or not all(fns.values()):
    print("test_deep_quarantine: SKIP (нет sh)" if not sh and fails == 0 else "test_deep_quarantine: FAIL")
    sys.exit(0 if not sh and fails == 0 else 1)

tmp = tempfile.mkdtemp(prefix="wdq_")
STATE = os.path.join(tmp, "watchdog.state").replace("\\", "/")
LOG = os.path.join(tmp, "log.txt").replace("\\", "/")

PRE = (
    "STATE_FILE='" + STATE + "'\n"
    "log() { echo \"LOG $*\" >> '" + LOG + "'; }\n"
    "tg_alert() { echo \"TG $*\" >> '" + LOG + "'; }\n"
    "date() { echo \"$FAKE_NOW\"; }\n"
    "curl() { printf '%s' \"$FAKE_CODE\"; }\n"
    "DEEP_PROBE_ENABLE=1; DEEP_PROBE_INTERVAL=180; DEEP_PROBE_THRESHOLD=2\n"
    "DEEP_RETURN_HOLD=1800; DEEP_RETURN_HOLD_MAX=86400\n"
    + fns["_num"] + fns["_state_field"] + fns["_log_every5"] + fns["_deep_hold"] + fns["deep_probe_check"]
)


def run(body, env=None):
    e = dict(os.environ)
    e.update(env or {})
    try:
        r = subprocess.run([sh, "-c", PRE + body], capture_output=True, text=True, encoding="utf-8", env=e,
                           stdin=subprocess.DEVNULL, timeout=60)
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT"
    return r.stdout.strip(), r.stderr.strip()


# --- _deep_hold ---
out, err = run('for n in 0 1 2 3 4 5 6 7 20; do printf "%s " "$(_deep_hold $n)"; done')
check(out.split() == ["1800", "1800", "3600", "7200", "14400", "28800", "57600", "86400", "86400"],
      "_deep_hold: 0/1 → 1800, дальше удвоение, потолок 86400", out + err)
out, _ = run('DEEP_RETURN_HOLD_MAX=1800; printf "%s" "$(_deep_hold 5)"')
check(out == "1800", "_deep_hold: потолок, равный базе, не растёт", out)
out, _ = run('printf "%s" "$(_deep_hold abc)"')
check(out == "1800", "_deep_hold: мусор вместо числа → база", out)


def tick(state, now, code, tag, main="main1"):
    """Один вызов deep_probe_check PRI; state — строка полей до тика. Возвращает (поля, лог)."""
    open(STATE, "w", encoding="utf-8", newline="").write(state + "\n")
    if os.path.exists(LOG):
        os.remove(LOG)
    body = (f'deep_probe_check PRI 10819 https://x/ "{tag}" "Основной канал" 1 "{main}"\n'
            'echo "CTR=$DP_CTR TS=$DP_TS DEAF=$DP_DEAF TAG=$DP_TAG RES=$DP_RES QN=$DP_QN CHK=$DP_CHK"\n')
    out, err = run(body, {"FAKE_NOW": str(now), "FAKE_CODE": code})
    fields = dict(kv.split("=", 1) for kv in out.split()) if out else {}
    lg = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
    return fields, lg + err


def as_state(f):
    return " ".join(f"DPRI_{k}={v}" for k, v in f.items())


T0 = 1_000_000
# 1) Возврат на основной после карантина: QN=1 → проверка (CHK=1), первый замер через минуту.
f, lg = tick("DPRI_TAG=res1 DPRI_TS=900000 DPRI_QN=1 DPRI_DEAF=0 DPRI_CTR=0", T0, "200", "main1")
check(f.get("CHK") == "1" and f.get("TAG") == "main1", "возврат после карантина: CHK=1, тег основного", f)
check(f.get("TS") == str(T0 - 180 + 60), "возврат: замер назначен через минуту (TS = now − интервал + 60)", f)
check("проверяю насквозь" in lg and "TG" not in lg, "возврат: строка в лог, без TG", lg)
# 1а) через минуту проба прошла → QN=0, CHK=0, «✅ … проверен».
f2, lg = tick(as_state(f), T0 + 60, "200", "main1")
check(f2.get("RES") == "ok" and f2.get("QN") == "0" and f2.get("CHK") == "0",
      "проверка прошла: RES=ok, QN сброшен, CHK=0", f2)
check("TG ✅" in lg and "проверен насквозь" in lg, "проверка прошла: TG «✅ … проверен насквозь»", lg)

# 2) Возврат, а основной всё ещё глух: два пустых замера с шагом в минуту → DEAF за 2 мин.
f, _ = tick("DPRI_TAG=res1 DPRI_TS=900000 DPRI_QN=1", T0, "000", "main1")
f, lg = tick(as_state(f), T0 + 60, "000", "main1")
check(f.get("CTR") == "1" and f.get("CHK") == "1" and f.get("DEAF") == "0", "проверка: 1-я проба пуста, CHK держится", f)
check(f.get("TS") == str(T0 + 60 - 180 + 60), "проверка: следующий замер снова через минуту", f)
f, lg = tick(as_state(f), T0 + 120, "000", "main1")
check(f.get("DEAF") == "1" and f.get("CHK") == "0", "проверка: 2-я пустая проба → глух (DEAF=1), CHK снят", f)
check("так и не пропускает" in lg, "проверка: TG «после карантина так и не пропускает трафик»", lg)
check(f.get("QN") == "1", "QN считает вызывающий: deep_probe_check его при глухоте не трогает", f)

# 3) Обычный уход на резерв (не на основной) — без проверки, замер через полный интервал.
f, lg = tick("DPRI_TAG=main1 DPRI_TS=900000 DPRI_QN=1", T0, "200", "res1")
check(f.get("CHK") == "0" and f.get("TS") == str(T0), "смена на резерв: CHK=0, обычный интервал", f)

# 4) Возврат на основной без истории глухоты (QN=0) — без проверки.
f, lg = tick("DPRI_TAG=res1 DPRI_TS=900000 DPRI_QN=0", T0, "200", "main1")
check(f.get("CHK") == "0" and f.get("TS") == str(T0), "возврат без карантина (QN=0): CHK=0, обычный интервал", f)

# 5) Успешная проба на резерве не сбрасывает QN основного.
f, _ = tick("DPRI_TAG=res1 DPRI_TS=900000 DPRI_QN=2", T0, "200", "res1")
check(f.get("QN") == "2" and f.get("RES") == "ok", "проба ok на резерве: QN основного сохраняется", f)

# 6) Старый state без QN/CHK (апгрейд с v32) — нули, без ошибок.
f, lg = tick("DPRI_TAG=main1 DPRI_TS=900000 DPRI_CTR=0", T0, "200", "main1")
check(f.get("QN") == "0" and f.get("CHK") == "0" and f.get("RES") == "ok", "state v32 без новых полей: QN=0, CHK=0", f)

# 7) Проба выключена — CHK сбрасывается.
open(STATE, "w").write("DPRI_TAG=main1 DPRI_CHK=1 DPRI_QN=1\n")
out, _ = run('DEEP_PROBE_ENABLE=0; deep_probe_check PRI 1 u main1 n 1 main1; echo "$DP_CHK $DP_QN"',
             {"FAKE_NOW": str(T0), "FAKE_CODE": "200"})
check(out == "0 1", "проба выключена: CHK=0, QN не трогается", out)

shutil.rmtree(tmp, ignore_errors=True)
print("test_deep_quarantine: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
