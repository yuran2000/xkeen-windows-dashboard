# -*- coding: utf-8 -*-
"""Офлайн-тест «xray перегружен сам» из bootstrap/watchdog.sh.cur (v37): замер доли процессора xray и
трафика между тиками (`xray_busy_eval`), поиск виновника по смене каналов ролей и поведение
`deep_probe_check` при перегрузке.

Функции вырезаются из .cur и гоняются в sh с подменёнными /proc (XB_PROC), pidof, date, curl, log и
tg_alert (без роутера и сети). Нужен POSIX sh (на Windows — из Git for Windows); его нет — тест
пропускается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_xray_busy.py
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
check(ver >= 37, f"заголовок .cur — watchdog v{ver} (нужен ≥ 37)")
names = ("_num", "_state_field", "_log_every5", "xray_busy_eval", "deep_probe_check")
fns = {n: extract(src, n) for n in names}
check(all(fns.values()), "в .cur есть " + ", ".join(names), [n for n, v in fns.items() if not v])
# Вызов замера стоит до AI-пробы и до проб ролей, и не в режиме применения.
i_call = src.find('[ -z "$WD_APPLY_ID" ] && xray_busy_eval')
check(i_call > 0, "замер вызывается вне режима применения")
check(0 < i_call < src.find("# ---------- v24: AI exit-IP probe"), "замер — до AI-пробы")
check(i_call < src.find("deep_probe_check PRI "), "замер — до сквозной пробы основного канала")
check('if [ "$XRAY_BUSY" = "1" ]; then' in src.split("# ---------- v24: AI exit-IP probe")[1],
      "AI-проба не считает пустоту при перегрузке")

sh = find_sh()
if not sh or not all(fns.values()):
    print("test_xray_busy: SKIP (нет sh)" if not sh and fails == 0 else "test_xray_busy: FAIL")
    sys.exit(0 if not sh and fails == 0 else 1)

tmp = tempfile.mkdtemp(prefix="wdb_")
P = lambda *a: os.path.join(tmp, *a).replace("\\", "/")  # noqa: E731
os.makedirs(P("proc", "net"))
os.makedirs(P("proc", "4242"))
os.makedirs(P("proc", "5555"))
STATE, LOG, BUSY = P("watchdog.state"), P("log.txt"), P("xraybusy")

PRE = (
    "STATE_FILE='" + STATE + "'\n"
    "XRAY_BUSY_FILE='" + BUSY + "'\n"
    "XB_PROC='" + P("proc") + "'\n"
    "log() { echo \"LOG $*\" >> '" + LOG + "'; }\n"
    "tg_alert() { echo \"TG $*\" >> '" + LOG + "'; }\n"
    "date() { echo \"$FAKE_NOW\"; }\n"
    "pidof() { [ -n \"$FAKE_PID\" ] && echo \"$FAKE_PID\"; }\n"
    "curl() { echo \"CURL $*\" >> '" + LOG + "'; printf '%s' \"$FAKE_CODE\"; }\n"
    "XRAY_BUSY_ENABLE=1; XRAY_BUSY_CPU=50; XRAY_BUSY_KBIT_PER_PCT=50; XRAY_BUSY_TICKS=2\n"
    "DEEP_PROBE_ENABLE=1; DEEP_PROBE_INTERVAL=180; DEEP_PROBE_THRESHOLD=2\n"
    + "".join(fns[n] for n in names)
)


def run(body, env=None):
    e = dict(os.environ)
    e.update(env or {})
    # Скрипт — файлом: в аргументе `sh -c` он упирается в предел длины командной строки Windows
    # (32 КБ) и обрезается («unexpected end of file»).
    script = P("run.sh")
    write(script, PRE + body)
    try:
        r = subprocess.run([sh, script], capture_output=True, text=True, encoding="utf-8", env=e,
                           stdin=subprocess.DEVNULL, timeout=60)
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT"
    return r.stdout.strip(), r.stderr.strip()


def write(path, text):
    open(path, "w", encoding="utf-8", newline="").write(text)


def proc(pid, xj, tj, wan, route=True):
    """xj — jiffies xray (utime, stime поровну), tj — сумма строки cpu, wan — rx+tx байт ppp0."""
    write(P("proc", str(pid), "stat"),
          f"{pid} (xray) R 1 1 1 0 -1 4194560 100 0 0 0 {xj // 2} {xj - xj // 2} 0 0 20 0 12 0 155675224\n")
    write(P("proc", "stat"), f"cpu  {tj - 1000} 0 0 1000 0 0 0 0 0 0\ncpu0 1 1 1 1\n")
    write(P("proc", "net", "route"),
          "Iface\tDestination\tGateway\tFlags\n"
          + ("ppp0\t00000000\t00000000\t0001\n" if route else "")
          + "br1\t004B010A\t00000000\t0001\n")
    rx = wan // 2
    write(P("proc", "net", "dev"),
          "Inter-|   Receive\n face |bytes    packets\n"
          f"    lo: 999999999999 1 0 0 0 0 0 0 999999999999 1 0 0 0 0 0 0\n"
          f"  ppp0: {rx} 1 0 0 0 0 0 0 {wan - rx} 1 0 0 0 0 0 0\n"
          f"   br0: 5 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0\n")


def tick(now, pid, xj, tj, wan, tags, route=True):
    """tags: (default, ai, yt, fgn). Возвращает (поля, лог)."""
    d, ai, yt, fgn = tags
    write(STATE, f"primary 0 {d} EFFECTIVE_AI={ai} AI_EFF={ai} YT_EFF={yt} FGN_EFF={fgn}\n")
    proc(pid, xj, tj, wan, route)
    if os.path.exists(LOG):
        os.remove(LOG)
    out, err = run('xray_busy_eval\necho "BUSY=$XRAY_BUSY N=$XRAY_BUSY_N PCT=$XRAY_BUSY_PCT KBIT=$XRAY_BUSY_KBIT"\n'
                   'echo "BLAME=$XRAY_BUSY_BLAME" | tr " " "+"\n',
                   {"FAKE_NOW": str(now), "FAKE_PID": str(pid)})
    f = {}
    for line in out.splitlines():
        for kv in line.split():
            if "=" in kv:
                k, v = kv.split("=", 1)
                f[k] = v.replace("+", " ").strip()
    lg = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
    return f, lg + err


T, HZ_ALL = 1_000_000, 400          # 4 ядра × 100 Гц → 400 jiffies/с на всех
CALM = ("S3", "S1", "S1", "S3")
BAD = ("S3", "S1", "EE2", "S3")
KB = 1000 // 8                       # байт на 1 кбит/с за секунду
xj, tj, wan = 50_000, 10_000_000, 1_000_000

# 1) Первый запуск — замера нет, файл заведён.
f, lg = tick(T, 4242, xj, tj, wan, CALM)
check(f.get("BUSY") == "0" and f.get("PCT") == "" and os.path.exists(BUSY), "первый тик: замера нет, файл есть", f)
check(open(BUSY).read().split()[7:] == ["-", "-", "-", "-"], "первый тик: спокойных каналов ещё нет («-»)",
      open(BUSY).read())

# 2) Спокойный тик: 5 % процессора, 2 Мбит/с.
T += 60; xj += 60 * HZ_ALL * 5 // 100; tj += 60 * HZ_ALL; wan += 60 * 2000 * KB
f, lg = tick(T, 4242, xj, tj, wan, CALM)
check(f.get("BUSY") == "0" and f.get("PCT") == "5" and f.get("KBIT") == "2000", "спокойно: 5 %, 2000 кбит/с", f)
check(open(BUSY).read().split()[7:] == ["S3", "S1", "S1", "S3"], "спокойно: каналы ролей запомнены", open(BUSY).read())

# 3) YouTube сменили на EE2 → рестарт xray (новый pid): замера нет, эпизода нет.
T += 60; tj += 60 * HZ_ALL; wan += 60 * 300 * KB
xj2 = 1000
f, lg = tick(T, 5555, xj2, tj, wan, BAD)
check(f.get("BUSY") == "0" and f.get("PCT") == "", "новый pid xray: замера нет", f)

# 4) Перегрузка: 80 % при 300 кбит/с — 1-й замер: пробы не считаются, виновника ещё не ищем.
T += 60; xj2 += 60 * HZ_ALL * 80 // 100; tj += 60 * HZ_ALL; wan += 60 * 300 * KB
f, lg = tick(T, 5555, xj2, tj, wan, BAD)
check(f.get("BUSY") == "1" and f.get("N") == "1" and f.get("PCT") == "80" and f.get("KBIT") == "300",
      "перегрузка 1-й замер: BUSY=1, N=1, 80 %, 300 кбит/с", f)
check(f.get("BLAME") == "" and "TG" not in lg and "XRAY-BUSY: xray занимает 80%" in lg,
      "1-й замер: лог есть, TG и виновника нет", lg)

# 5) 2-й замер подряд — виновник YouTube EE2, один TG.
T += 60; xj2 += 60 * HZ_ALL * 85 // 100; tj += 60 * HZ_ALL; wan += 60 * 250 * KB
f, lg = tick(T, 5555, xj2, tj, wan, BAD)
check(f.get("BLAME") == "YT=EE2", "2-й замер: виновник YT=EE2", f)
check(lg.count("TG ") == 1 and "YouTube <code>S1</code> → <code>EE2</code>" in lg and "Ухожу с <code>EE2</code>" in lg,
      "2-й замер: один TG «началось после смены YouTube S1 → EE2, ухожу с EE2»", lg)

# 6) deep_probe_check при перегрузке: виновник → DEAF сразу, без curl; чужая роль — без замера.
write(STATE, "primary 0 S3 YT_EFF=EE2 DYT_TAG=EE2 DYT_TS=0 DYT_CTR=0 DYT_DEAF=0 DPRI_TAG=S3 DPRI_TS=0 DPRI_CTR=1\n")
os.remove(LOG)
out, _ = run('XRAY_BUSY=1; XRAY_BUSY_N=2; XRAY_BUSY_BLAME="YT=EE2"; XRAY_BUSY_PCT=85; XRAY_BUSY_KBIT=250\n'
             'deep_probe_check YT 10820 u EE2 YouTube 1 EE2; echo "Y $DP_DEAF $DP_CTR $DP_RES"\n'
             'deep_probe_check PRI 10819 u S3 Основной 1 S3; echo "P $DP_DEAF $DP_CTR $DP_RES $DP_TS"\n',
             {"FAKE_NOW": str(T), "FAKE_CODE": "000"})
lg = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
check("Y 1 2 fail" in out, "виновник: DEAF=1, CTR=порог, RES=fail", out)
check("P 0 1 none 0" in out, "чужая роль: счётчики и время замера не тронуты", out)
check("CURL" not in lg and "перегружает процессор" in lg, "при перегрузке curl не вызывается; лог про виновника", lg)

# 7) 3-й замер — эпизод продолжается, второго TG нет.
T += 60; xj2 += 60 * HZ_ALL * 90 // 100; tj += 60 * HZ_ALL; wan += 60 * 200 * KB
f, lg = tick(T, 5555, xj2, tj, wan, BAD)
check(f.get("BUSY") == "1" and f.get("N") == "3" and f.get("BLAME") == "" and "TG" not in lg,
      "3-й замер: BUSY, N=3, повторно не винит и не шлёт TG", f)

# 8) Ушли с EE2 (рестарт) и нагрузка спала → «✅ разгрузился», эпизод закрыт.
T += 60; tj += 60 * HZ_ALL; wan += 60 * 3000 * KB; xj3 = 500
f, _ = tick(T, 4242, xj3, tj, wan, ("S3", "S1", "S1", "S3"))
check(f.get("PCT") == "" and open(BUSY).read().split()[5] == "3", "новый pid: эпизод хранится до замера", open(BUSY).read())
T += 60; xj3 += 60 * HZ_ALL * 3 // 100; tj += 60 * HZ_ALL; wan += 60 * 3000 * KB
f, lg = tick(T, 4242, xj3, tj, wan, ("S3", "S1", "S1", "S3"))
check(f.get("BUSY") == "0" and "TG ✅" in lg and "нагрузка спала" in lg, "спала: лог и TG «✅ xray разгрузился»", lg)
check(open(BUSY).read().split()[5:7] == ["0", "0"], "спала: N=0, blamed=0", open(BUSY).read())

# 9) Рабочая нагрузка: 60 % при 20 Мбит/с — не перегрузка.
T += 60; xj3 += 60 * HZ_ALL * 60 // 100; tj += 60 * HZ_ALL; wan += 60 * 20000 * KB
f, lg = tick(T, 4242, xj3, tj, wan, CALM)
check(f.get("BUSY") == "0" and f.get("PCT") == "60", "60 % при 20 Мбит/с — рабочая нагрузка, не перегрузка", f)

# 10) Перегрузка без смены каналов — TG «виновника не вижу», никого не винит.
for i in range(2):
    T += 60; xj3 += 60 * HZ_ALL * 70 // 100; tj += 60 * HZ_ALL; wan += 60 * 100 * KB
    f, lg = tick(T, 4242, xj3, tj, wan, CALM)
check(f.get("BUSY") == "1" and f.get("BLAME") == "" and "виновника не вижу" in lg, "без смены каналов: только TG", lg)

# 11) Счётчики за 32 бита и нет маршрута по умолчанию (берётся самый нагруженный интерфейс, кроме lo).
os.remove(BUSY)
big_x, big_t, big_w = 3_000_000_000, 9_000_000_000, 5_000_000_000_000
tick(T, 4242, big_x, big_t, big_w, CALM, route=False)
T += 60
f, _ = tick(T, 4242, big_x + 60 * HZ_ALL * 80 // 100, big_t + 60 * HZ_ALL, big_w + 60 * 400 * KB, CALM, route=False)
check(f.get("PCT") == "80" and f.get("KBIT") == "400", "большие счётчики и без маршрута: 80 %, 400 кбит/с", f)

# 12) Выключено — ничего не меряет.
out, _ = run('XRAY_BUSY_ENABLE=0; xray_busy_eval; echo "$XRAY_BUSY/$XRAY_BUSY_PCT"', {"FAKE_NOW": str(T), "FAKE_PID": "4242"})
check(out == "0/", "XRAY_BUSY_ENABLE=0: замера нет", out)

shutil.rmtree(tmp, ignore_errors=True)
print("test_xray_busy: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
