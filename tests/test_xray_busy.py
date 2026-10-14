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
    "XRAY_BUSY_CALM_TICKS=2; XRAY_BUSY_REMIND_TICKS=10; XRAY_BUSY_TG_COOLDOWN=1800\n"
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


def proc(pid, xj, tj, wan, route=True, routes=None, extra_dev=""):
    """xj — jiffies xray (utime, stime поровну), tj — сумма строки cpu, wan — rx+tx байт ppp0.
    routes — маршруты по умолчанию [(iface, metric)], по умолчанию [("ppp0", 1000)]; route=False — ни одного."""
    if routes is None:
        routes = [("ppp0", 1000)] if route else []
    write(P("proc", str(pid), "stat"),
          f"{pid} (xray) R 1 1 1 0 -1 4194560 100 0 0 0 {xj // 2} {xj - xj // 2} 0 0 20 0 12 0 155675224\n")
    write(P("proc", "stat"), f"cpu  {tj - 1000} 0 0 1000 0 0 0 0 0 0\ncpu0 1 1 1 1\n")
    write(P("proc", "net", "route"),
          "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
          + "".join(f"{i}\t00000000\t00000000\t0001\t0\t0\t{m}\t00000000\t0\t0\t0\n" for i, m in routes)
          + "br1\t004B010A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n")
    rx = wan // 2
    write(P("proc", "net", "dev"),
          "Inter-|   Receive\n face |bytes    packets\n"
          f"    lo: 999999999999 1 0 0 0 0 0 0 999999999999 1 0 0 0 0 0 0\n"
          f"  ppp0: {rx} 1 0 0 0 0 0 0 {wan - rx} 1 0 0 0 0 0 0\n"
          f"   br0: 5 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0\n" + extra_dev)


def tick(now, pid, xj, tj, wan, tags, route=True, routes=None, extra_dev=""):
    """tags: (default, ai, yt, fgn). Возвращает (поля, лог)."""
    d, ai, yt, fgn = tags
    write(STATE, f"primary 0 {d} EFFECTIVE_AI={ai} AI_EFF={ai} YT_EFF={yt} FGN_EFF={fgn}\n")
    proc(pid, xj, tj, wan, route, routes, extra_dev)
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
check(open(BUSY).read().split()[8:12] == ["-", "-", "-", "-"] and len(open(BUSY).read().split()) == 14,
      "первый тик: спокойных каналов ещё нет («-»), в файле 14 полей",
      open(BUSY).read())

# 2) Спокойный тик: 5 % процессора, 2 Мбит/с.
T += 60; xj += 60 * HZ_ALL * 5 // 100; tj += 60 * HZ_ALL; wan += 60 * 2000 * KB
f, lg = tick(T, 4242, xj, tj, wan, CALM)
check(f.get("BUSY") == "0" and f.get("PCT") == "5" and f.get("KBIT") == "2000", "спокойно: 5 %, 2000 кбит/с", f)
check(open(BUSY).read().split()[8:12] == ["S3", "S1", "S1", "S3"], "спокойно: каналы ролей запомнены", open(BUSY).read())

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
check(lg.count("TG ") == 1 and "YouTube <code>S1</code> → <code>EE2</code>" in lg and "Признаю <code>EE2</code> неисправным" in lg,
      "2-й замер: один TG «началось после смены YouTube S1 → EE2, признаю EE2 неисправным»", lg)

# 6) deep_probe_check при перегрузке: виновник → DEAF сразу, без curl; чужая роль — без замера.
write(STATE, "primary 0 S3 YT_EFF=EE2 DYT_TAG=EE2 DYT_TS=0 DYT_CTR=0 DYT_DEAF=0 DPRI_TAG=S3 DPRI_TS=0 DPRI_CTR=1\n")
os.remove(LOG)
out, _ = run('XRAY_BUSY=1; XRAY_BUSY_N=2; XRAY_BUSY_BLAME="YT=EE2"; XRAY_BUSY_PCT=85; XRAY_BUSY_KBIT=250\n'
             'deep_probe_check YT 10820 u EE2 YouTube 1 EE2; echo "Y $DP_DEAF $DP_CTR $DP_RES"\n'
             'deep_probe_check PRI 10819 u S3 Основной 1 S3; echo "P $DP_DEAF $DP_CTR $DP_RES $DP_TS"\n',
             {"FAKE_NOW": str(T), "FAKE_CODE": "000"})
lg = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
check("Y 1 2 fail" in out, "виновник: DEAF=1, CTR=порог, RES=fail", out)
check("P 0 1 none 0" in out, "чужая роль: замера нет, счётчик и время не тронуты (эпизод открылся не в этом тике)", out)
check("CURL" not in lg and "перегружает процессор" in lg, "при перегрузке curl не вызывается; лог про виновника", lg)

# 7) 3-й замер — эпизод продолжается, второго TG нет.
T += 60; xj2 += 60 * HZ_ALL * 90 // 100; tj += 60 * HZ_ALL; wan += 60 * 200 * KB
f, lg = tick(T, 5555, xj2, tj, wan, BAD)
check(f.get("BUSY") == "1" and f.get("N") == "3" and f.get("BLAME") == "" and "TG" not in lg,
      "3-й замер: BUSY, N=3, повторно не винит и не шлёт TG", f)

# 8) Ушли с EE2 (рестарт) и нагрузка спала → «✅ разгрузился», эпизод закрыт.
T += 60; tj += 60 * HZ_ALL; wan += 60 * 3000 * KB; xj3 = 500
f, _ = tick(T, 4242, xj3, tj, wan, ("S3", "S1", "S1", "S3"))
check(f.get("PCT") == "" and f.get("BUSY") == "1" and open(BUSY).read().split()[5] == "3",
      "новый pid после обвинения: эпизод хранится, пробы этого тика не считаются (BUSY=1)", open(BUSY).read())
T += 60; xj3 += 60 * HZ_ALL * 3 // 100; tj += 60 * HZ_ALL; wan += 60 * 3000 * KB
f, lg = tick(T, 4242, xj3, tj, wan, ("S3", "S1", "S1", "S3"))
check(f.get("BUSY") == "0" and "TG" not in lg and open(BUSY).read().split()[5] == "3",
      "1-й спокойный замер: эпизод ещё открыт (гистерезис), TG нет", lg)
T += 60; xj3 += 60 * HZ_ALL * 3 // 100; tj += 60 * HZ_ALL; wan += 60 * 3000 * KB
f, lg = tick(T, 4242, xj3, tj, wan, ("S3", "S1", "S1", "S3"))
check(f.get("BUSY") == "0" and "TG ✅" in lg and "нагрузка спала" in lg, "спала: лог и TG «✅ xray разгрузился»", lg)
check(open(BUSY).read().split()[5:7] == ["0", "0"], "спала: N=0, blamed=0", open(BUSY).read())
T += 1800   # дальше — новый эпизод вне кулдауна TG; разрыв > 600 с сбрасывает базу — новый базовый тик
tick(T, 4242, xj3, tj, wan, CALM)
T += 60; tj += 60 * HZ_ALL; wan += 60 * 2000 * KB; xj3 += 60 * HZ_ALL * 5 // 100
f, lg = tick(T, 4242, xj3, tj, wan, CALM)
check(f.get("PCT") == "5", "после паузы > 600 с: первый тик — база, следующий меряет", f)

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

def fresh(line=None):
    if os.path.exists(BUSY):
        os.remove(BUSY)
    if line is not None:
        write(BUSY, line)


def busy_step(now, pid, x, t, w, tags, pct, kbit, **kw):
    x += 60 * HZ_ALL * pct // 100; t += 60 * HZ_ALL; w += 60 * kbit * KB
    f, lg = tick(now, pid, x, t, w, tags, **kw)
    return f, lg, x, t, w


# 13) Битый файл эпизода (пусто / 5 полей / CRLF / пустой ts) → как нет файла: никого не винить.
for name, line in (("пустой", ""), ("5 полей", "1 2 3 4 5\n"), ("ведущий пробел (date пуст)", " 4242 1 2 3 0 0 0 S3 S1 S1 S3 0 0\n"),
                   ("старый формат v37 (11 полей)", "%d 4242 1 2 3 0 0 S3 S1 S1 S3\n"),
                   ("пустые calm (14 полей с пропуском)", "%d 4242 1 2 3 0 0 0  S1 S1 S3 0 0\n")):
    fresh(line.replace("%d", str(T - 60)) if "%d" in line else line)
    x, t, w = 10_000, 50_000_000, 10_000_000
    tick(T, 4242, x, t, w, CALM)                      # если файл не принят — этот тик лишь база
    lgs = ""; blame = ""
    for k in range(3):
        f, lg, x, t, w = busy_step(T + 60 * (k + 1), 4242, x, t, w, CALM, 80, 100)
        lgs += lg; blame += f.get("BLAME", "")
    check(blame == "" and "<code></code>" not in lgs, f"битый файл ({name}): никого не винит, пустых каналов в TG нет", (blame, lgs[-200:]))
T += 600

# 14) Дребезг у порога 55/55/45 % — один эпизод, не 40 TG в час.
fresh(); x, t, w = 10_000, 60_000_000, 20_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)    # спокойный снимок
tg = 0
for k in range(60):
    pct = (55, 55, 45)[k % 3]
    f, lg, x, t, w = busy_step(T + 120 + 60 * k, 4242, x, t, w, CALM, pct, 100)
    tg += lg.count("TG ")
check(tg <= 2, f"дребезг 55/55/45 % час: TG {tg} (≤ 2)", tg)
T += 120 + 60 * 60 + 1800

# 15) Внеочередной тик (dt=10) посреди эпизода после обвинения: эпизод не сброшен, второго виновника нет.
fresh(); x, t, w = 10_000, 70_000_000, 30_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
f, lg, x, t, w = busy_step(T + 120, 4242, x, t, w, BAD, 80, 100)
f, lg, x, t, w = busy_step(T + 180, 4242, x, t, w, BAD, 80, 100)
check(f.get("BLAME") == "YT=EE2", "15: виновник YT=EE2", f)
S2 = ("S3", "S1", "S2", "S3")
f, lg = tick(T + 190, 4242, x + 100, t + 4000, w, S2)
check(f.get("BUSY") == "1" and f.get("PCT") == "", "внеочередной тик (10 с): замера нет, перегрузка держится (BUSY=1)", f)
blames = ""
for k in range(3):
    f, lg, x, t, w = busy_step(T + 240 + 60 * k, 4242, x, t, w, S2, 80, 100)
    blames += f.get("BLAME", "") + lg
check("YT=S2" not in blames and "TG 🔥" not in blames, "после внеочередного тика исправный S2 не винится, второго 🔥 нет", blames[-300:])
T += 2400

# 16) Рестарт xray без виновника: одиночный ложный замер до рестарта + один после → обвинения нет; пробы тика рестарта — нет.
fresh(); x, t, w = 10_000, 80_000_000, 40_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
f, lg, x, t, w = busy_step(T + 120, 4242, x, t, w, BAD, 80, 100)    # n=1
check(f.get("N") == "1", "16: один перегруженный замер (N=1)", f)
f, lg = tick(T + 180, 5555, 1000, t + 60 * HZ_ALL, w + 60 * 100 * KB, BAD)
check(f.get("BUSY") == "1" and f.get("N") == "0", "рестарт без виновника: пробы тика не считаются, счёт с нуля", f)
f, lg, x2, t, w = busy_step(T + 240, 5555, 1000, t + 60 * HZ_ALL, w + 60 * 100 * KB, BAD, 80, 100)
check(f.get("BLAME") == "" and f.get("N") == "1", "после рестарта один замер — ещё не обвинение", f)
T += 2400

# 17) Сброс счётчика WAN (переподключение ppp0) → замера нет, а не «0 кбит/с».
fresh(); x, t, w = 10_000, 90_000_000, 50_000_000
tick(T, 4242, x, t, w, CALM)
x += 60 * HZ_ALL * 60 // 100; t += 60 * HZ_ALL
f, lg = tick(T + 60, 4242, x, t, 1000, CALM)
check(f.get("PCT") == "" and f.get("BUSY") == "0", "счётчик WAN пошёл назад: замера нет, перегрузки нет", f)
T += 600

# 18) Только lo в /proc/net/dev и нет маршрута → замера нет.
fresh()
write(STATE, "primary 0 S3 AI_EFF=S1 YT_EFF=S1 FGN_EFF=S3\n")
for now, xx in ((T, 10_000), (T + 60, 10_000 + 60 * HZ_ALL * 70 // 100)):
    proc(4242, xx, 100_000_000 + (now - T) * HZ_ALL, 1, route=False)
    write(P("proc", "net", "dev"), "Inter-|\n face |\n    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n")
    out, _ = run('xray_busy_eval; echo "B=$XRAY_BUSY P=$XRAY_BUSY_PCT"', {"FAKE_NOW": str(now), "FAKE_PID": "4242"})
check(out == "B=0 P=", "только lo: замера нет (не «0 кбит/с»)", out)
T += 600

# 19) Два маршрута по умолчанию: берётся с меньшей метрикой (ppp0 1000), а не первый (usb0 2000).
fresh(); x, t, w = 10_000, 110_000_000, 60_000_000
r2 = [("usb0", 2000), ("ppp0", 1000)]
usb = "  usb0: 7 1 0 0 0 0 0 0 7 1 0 0 0 0 0 0\n"
tick(T, 4242, x, t, w, CALM, routes=r2, extra_dev=usb)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 60, 20000, routes=r2, extra_dev=usb)
check(f.get("BUSY") == "0" and f.get("KBIT") == "20000", "два маршрута по умолчанию: трафик с ppp0 (метрика меньше)", f)
T += 600

# 20) Напоминание: после обвинения перегрузка держится ещё 10 замеров → один TG, дальше тишина.
fresh(); x, t, w = 10_000, 120_000_000, 70_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
tg_all = ""
for k in range(16):
    f, lg, x, t, w = busy_step(T + 120 + 60 * k, 4242, x, t, w, BAD if k < 2 else S2, 80, 100)
    tg_all += "".join(l for l in lg.splitlines(True) if l.startswith("TG"))
check(tg_all.count("TG 🔥") == 2 and "Держится уже" in tg_all, "напоминание: ровно одно после обвинения (всего 2 «🔥»)", tg_all[-300:])
T += 3600

# 21) date вернул не число — тик не падает, замера нет.
out, err = run('xray_busy_eval; echo "END B=$XRAY_BUSY"', {"FAKE_NOW": "Tue Oct 14", "FAKE_PID": "4242"})
check(out == "END B=0", "нечисловой date: функция вернулась, тик не оборван", (out, err[-120:]))

# --- 2-й раунд ревью ---
def dp(body, state, now):
    write(STATE, state + "\n")
    if os.path.exists(LOG):
        os.remove(LOG)
    out, _ = run(body, {"FAKE_NOW": str(now), "FAKE_CODE": "000"})
    return out


# 22) Отмена пустой пробы: только при открытии эпизода и только пробы из измеренного (перегруженного) окна.
base = "primary 0 S3 DPRI_TAG=S3 DPRI_RES=fail DPRI_CTR=1"
out = dp('XRAY_BUSY=1; XRAY_BUSY_N=1; XRAY_BUSY_OPENED=1; XRAY_BUSY_WIN=1000\n'
         'deep_probe_check PRI 10819 u S3 Основной 1 S3; echo "C=$DP_CTR"', base + " DPRI_TS=1030", 1060)
check(out == "C=0", "открытие эпизода: пустая проба внутри окна отменена (CTR 1 → 0)", out)
out = dp('XRAY_BUSY=1; XRAY_BUSY_N=1; XRAY_BUSY_OPENED=1; XRAY_BUSY_WIN=1000\n'
         'deep_probe_check PRI 10819 u S3 Основной 1 S3; echo "C=$DP_CTR"', base + " DPRI_TS=900", 1060)
check(out == "C=1", "открытие эпизода: проба ДО окна не отменяется", out)
out = dp('XRAY_BUSY=1; XRAY_BUSY_N=3; XRAY_BUSY_OPENED=0; XRAY_BUSY_WIN=1000\n'
         'deep_probe_check PRI 10819 u S3 Основной 1 S3; echo "C=$DP_CTR"', base + " DPRI_TS=1030", 1060)
check(out == "C=1", "эпизод уже шёл: счётчик не трогается", out)

# 23) Настоящая глухота + короткие перегрузки 1 из 4 минут → канал всё равно признан глухим.
fresh(); x, t, w = 10_000, 130_000_000, 80_000_000
state_dp = "primary 0 S3 AI_EFF=S1 YT_EFF=S1 FGN_EFF=S3 DPRI_TAG=S3 DPRI_TS=0 DPRI_CTR=0 DPRI_DEAF=0"
deaf_at = None
for k in range(40):
    now = T + 60 * k
    pct, kb = (80, 100) if k % 4 == 3 else (5, 2000)
    x += 60 * HZ_ALL * pct // 100; t += 60 * HZ_ALL; w += 60 * kb * KB
    proc(4242, x, t, w)
    write(STATE, state_dp + "\n")
    if os.path.exists(LOG):
        os.remove(LOG)
    out, _ = run('xray_busy_eval; deep_probe_check PRI 10819 u S3 Основной 1 S3\n'
                 'echo "DPRI_TAG=$DP_TAG DPRI_TS=$DP_TS DPRI_CTR=$DP_CTR DPRI_DEAF=$DP_DEAF DPRI_RES=$DP_RES"',
                 {"FAKE_NOW": str(now), "FAKE_PID": "4242", "FAKE_CODE": "000"})
    state_dp = "primary 0 S3 AI_EFF=S1 YT_EFF=S1 FGN_EFF=S3 " + out.strip()
    if "DPRI_DEAF=1" in out and deaf_at is None:
        deaf_at = k
check(deaf_at is not None and deaf_at <= 12, f"глухой канал при перегрузках 1 из 4 мин признан глухим (тик {deaf_at})", deaf_at)
T += 3600

# 24) До обвинения спокойный замер рвёт серию: 60 %, 5 %, 60 % — обвинения нет.
fresh(); x, t, w = 10_000, 140_000_000, 90_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
blames = ""
for k, pct in enumerate((60, 5, 60)):
    f, lg, x, t, w = busy_step(T + 120 + 60 * k, 4242, x, t, w, S2, pct, 100)
    blames += f.get("BLAME", "")
check(blames == "", "60/5/60 %: перегруженные не подряд — обвинения нет", blames)
T += 3600

# 25) Кулдаун: второй эпизод с обвинением через 14 мин — TG всё равно уходит (обвинение с действием).
fresh(); x, t, w = 10_000, 150_000_000, 100_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
for k in range(2):
    f, lg, x, t, w = busy_step(T + 120 + 60 * k, 4242, x, t, w, BAD, 80, 100)
for k in range(3):
    f, lg, x, t, w = busy_step(T + 240 + 60 * k, 4242, x, t, w, S2, 5, 2000)
FG = ("S3", "S1", "S2", "EE5")
tg2 = ""
for k in range(2):
    f, lg, x, t, w = busy_step(T + 840 + 60 * k, 4242, x, t, w, FG, 80, 100)
    tg2 += lg
check(f.get("BLAME") == "FGN=EE5" and "TG 🔥" in tg2 and "<code>EE5</code> неисправным" in tg2,
      "обвинение через 14 мин после прошлого TG: сообщено (кулдаун его не глушит)", tg2[-250:])
T += 3600

# 26) Дребезг 55/55/45/45 % час: «виновника не вижу» — с кулдауном, «✅» без «🔥» не шлётся.
fresh(); x, t, w = 10_000, 160_000_000, 110_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
tgs = []
for k in range(60):
    f, lg, x, t, w = busy_step(T + 120 + 60 * k, 4242, x, t, w, CALM, (55, 55, 45, 45)[k % 4], 100)
    tgs += [l for l in lg.splitlines() if l.startswith("TG")]
ok_n = sum(1 for l in tgs if "✅" in l)
check(len(tgs) <= 4 and ok_n <= 2, f"дребезг 55/55/45/45 % час: TG {len(tgs)} (≤ 4), из них «✅» {ok_n}", tgs)
T += 3600

# 27) Напоминание без названного виновника — без слов «после ухода с виновника».
fresh(); x, t, w = 10_000, 170_000_000, 120_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
alltg = ""
for k in range(14):
    f, lg, x, t, w = busy_step(T + 120 + 60 * k, 4242, x, t, w, CALM, 80, 100)
    alltg += lg
check("после ухода с виновника" not in alltg, "напоминание без виновника не говорит про «уход с виновника»", alltg[-300:])
T += 3600

# 28) Пауза > 600 с с другим pid — снимок забыт, двухчасовой снимок не винит.
fresh(); x, t, w = 10_000, 180_000_000, 130_000_000
tick(T, 4242, x, t, w, CALM)
f, lg, x, t, w = busy_step(T + 60, 4242, x, t, w, CALM, 5, 2000)
T2 = T + 7200
f, lg = tick(T2, 5555, 1000, t + 7200 * HZ_ALL, w + 7200 * 100 * KB, S2)
x2, t2, w2 = 1000, t + 7200 * HZ_ALL, w + 7200 * 100 * KB
bl = ""
for k in range(2):
    f, lg, x2, t2, w2 = busy_step(T2 + 60 * (k + 1), 5555, x2, t2, w2, S2, 80, 100)
    bl += f.get("BLAME", "")
check(bl == "" and "нет (перезагрузка роутера, первый запуск или долгая пауза" in lg, "пауза 2 ч + новый pid: снимок забыт, обвинения нет", (bl, lg[-200:]))
T += 10800

# 29) xray не запущен, эпизод открыт → XRAY_BUSY=0 (пробы не давим).
fresh("%d 4242 1 2 3 3 1 1 S3 S1 S1 S3 0 0\n" % (T - 60))
write(STATE, "primary 0 S3 AI_EFF=S1 YT_EFF=S1 FGN_EFF=S3\n")
out, _ = run('xray_busy_eval; echo "B=$XRAY_BUSY"', {"FAKE_NOW": str(T), "FAKE_PID": ""})
check(out == "B=0", "xray не запущен: XRAY_BUSY=0, пробы не давятся", out)

# 12) Выключено — ничего не меряет.
out, _ = run('XRAY_BUSY_ENABLE=0; xray_busy_eval; echo "$XRAY_BUSY/$XRAY_BUSY_PCT"', {"FAKE_NOW": str(T), "FAKE_PID": "4242"})
check(out == "0/", "XRAY_BUSY_ENABLE=0: замера нет", out)

shutil.rmtree(tmp, ignore_errors=True)
print("test_xray_busy: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
