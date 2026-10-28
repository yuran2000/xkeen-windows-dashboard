# -*- coding: utf-8 -*-
"""Офлайн-тест замера скорости канала YouTube из bootstrap/watchdog.sh.cur (v41): `yt_speed_check`,
`_yt_speed_kbps`, карантин медленных `_yt_slowq_prune` / `_yt_slowq_has` и их подключение к уходу по цепочке.

Функции вырезаются из .cur и гоняются в sh с подменёнными curl, date, log (без роутера и сети). curl
отвечает «скорость_байт/с скачано_байт»: через socks — FAKE_YT, напрямую — FAKE_DIR. Нужен POSIX sh (на
Windows — из Git for Windows); его нет — тест пропускается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_yt_speed.py
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
check(ver >= 41, f"заголовок .cur — watchdog v{ver} (нужен ≥ 41)")
names = ("_num", "_state_field", "_deep_hold", "_yt_speed_kbps", "yt_speed_check", "_yt_slowq_prune", "_yt_slowq_has")
fns = {n: extract(src, n) for n in names}
check(all(fns.values()), "в .cur есть " + ", ".join(names), [n for n, v in fns.items() if not v])

# --- Подключение к логике ухода (статически) ---
for f in ("YT_SPD", "YT_SPD_TS", "YT_SPD_TAG", "YT_SPD_CTR", "YT_SLOW", "YT_SPD_DIR", "YT_SLOWQ"):
    check(re.search(r"\b" + f + r"=\$(\{" + f + r"[:}]|" + f + r"\b)", src) is not None, f"state пишет {f}")
yt_block = src[src.index("yt_speed_check \"$_yt_eff_now\""):src.index("# ---------- FOREIGN kill-switch")]
check('if [ "$YT_BLOCKED" = "1" ] || [ "$_yt_bad" = "1" ]; then' in yt_block,
      "вход в перебор цепочки YouTube — по _yt_bad (глух ИЛИ медленный)")
check('${DYT_DEAF:-0}" = "1" ] && [ "$_yt" = "$_yt_eff_prev" ]' not in yt_block,
      "текущий канал пропускается по _yt_bad, а не только по глухоте")
check("_yt_slowq_has \"$_yt\"" in yt_block, "цепочка пропускает резервы из карантина медленных")
check('[ "$_yt_bad" = "1" ] && [ "$_yt_pick" != "$YT_TAG" ]; then DYT_LEFT=' in yt_block,
      "уход с медленного заводит карантин возврата DYT_LEFT")
check('[ "$_yt_bad" != "1" ] && probe_tag "$_yt_eff_prev"' in yt_block,
      "cooldown не держит на медленном текущем канале")
check("🐢 <b>YouTube переключён на резерв</b>" in yt_block, "TG называет причину ухода — медленный канал")
check('[ "$_yt_bad" = "1" ] || DYT_NOALT=0' in yt_block, "«переключиться некуда» — один раз на эпизод и для медленного")

sh = find_sh()
if not sh or not all(fns.values()):
    print("test_yt_speed: SKIP (нет sh)" if not sh and fails == 0 else "test_yt_speed: FAIL")
    sys.exit(0 if not sh and fails == 0 else 1)

tmp = tempfile.mkdtemp(prefix="wdys_")
STATE = os.path.join(tmp, "watchdog.state").replace("\\", "/")
LOG = os.path.join(tmp, "log.txt").replace("\\", "/")
CNT = os.path.join(tmp, "curl.cnt").replace("\\", "/")

PRE = (
    "STATE_FILE='" + STATE + "'\n"
    "log() { echo \"LOG $*\" >> '" + LOG + "'; }\n"
    "date() { echo \"$FAKE_NOW\"; }\n"
    "curl() { echo x >> '" + CNT + "'; case \" $* \" in *' --socks5-hostname '*) printf '%s' \"$FAKE_YT\" ;; "
    "*) printf '%s' \"$FAKE_DIR\" ;; esac; }\n"
    "YT_SPEED_ENABLE=1; YT_SPEED_MIN_KBPS=300; YT_SPEED_INTERVAL=900; YT_SPEED_RECHECK=120; YT_SPEED_SETTLE=120\n"
    "YT_SPEED_THRESHOLD=2; YT_SPEED_RATIO=3; YT_SPEED_BYTES=2000000; YT_SPEED_TIME=15; YT_SPEED_URL=https://x/\n"
    "DEEP_PROBE_PORT_YT=10820; DEEP_RETURN_HOLD=1800; DEEP_RETURN_HOLD_MAX=86400; XRAY_BUSY=0; WD_APPLY_ID=''\n"
    + "".join(fns[n] for n in names)
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


def tick(state, now, yt, direct="1500000 2000000", tag="usa", deaf="0", pre=""):
    """Один вызов yt_speed_check; yt/direct — ответ curl «байт/с скачано». Возвращает (поля, лог, вызовов curl)."""
    open(STATE, "w", encoding="utf-8", newline="").write(state + "\n")
    for p in (LOG, CNT):
        if os.path.exists(p):
            os.remove(p)
    body = (pre + f'yt_speed_check "{tag}" "{deaf}"\n'
            'echo "SPD=$YT_SPD TS=$YT_SPD_TS TAG=$YT_SPD_TAG CTR=$YT_SPD_CTR SLOW=$YT_SLOW DIR=$YT_SPD_DIR"\n')
    out, err = run(body, {"FAKE_NOW": str(now), "FAKE_YT": yt, "FAKE_DIR": direct})
    fields = dict(kv.split("=", 1) for kv in out.split()) if out else {}
    lg = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
    n = len(open(CNT).read().split()) if os.path.exists(CNT) else 0
    return fields, lg + err, n


def st(f):
    return (f"YT_SPD={f.get('SPD', 0)} YT_SPD_TS={f.get('TS', 0)} YT_SPD_TAG={f.get('TAG', '')} "
            f"YT_SPD_CTR={f.get('CTR', 0)} YT_SLOW={f.get('SLOW', 0)} YT_SPD_DIR={f.get('DIR', 0)}")


T0 = 1_000_000
# 1) Новый канал: замера нет, первый — через SETTLE (120 с), не через INTERVAL.
f, lg, n = tick("", T0, "11000 165000")
check(f.get("TAG") == "usa" and f.get("CTR") == "0" and f.get("SLOW") == "0" and n == 0,
      "новый канал: тег запомнен, curl не вызывался", (f, n))
check(f.get("TS") == str(T0 - 900 + 120), "новый канал: первый замер через 120 с", f)
f2, lg, n = tick(st(f), T0 + 60, "11000 165000")
check(n == 0, "через 60 с после смены канала замера нет", n)

# 2) Медленный канал при быстром интернете: 1-й медленный — счётчик, повтор через 120 с; 2-й — SLOW.
f, lg, n = tick(st(f), T0 + 120, "11264 165000")      # 11 КБ/с
check(f.get("SPD") == "11" and f.get("CTR") == "1" and f.get("SLOW") == "0" and n == 2,
      "1-й медленный замер: 11 КБ/с, подряд 1, мерили и напрямую (2 вызова curl)", (f, n))
check(f.get("DIR") == str(1500000 // 1024), "скорость напрямую сохранена", f)
check("подряд 1 (порог 2)" in lg, "лог называет счётчик и порог", lg)
_, _, n = tick(st(f), T0 + 200, "11264 165000")
check(n == 0, "до повтора (120 с) замера нет", n)
f, lg, n = tick(st(f), T0 + 240, "11264 165000")
check(f.get("CTR") == "2" and f.get("SLOW") == "1", "2-й медленный подряд → канал медленный (SLOW=1)", f)
check("YT-SPEED SLOW" in lg, "лог SLOW", lg)

# 3) Быстрый замер снимает латч; дальше — обычный интервал.
f, lg, n = tick(st(f), T0 + 360, "900000 2000000")
check(f.get("SLOW") == "0" and f.get("CTR") == "0" and n == 1, "быстрый замер: латч снят, напрямую не мерили", (f, n))
check("снова быстрый" in lg, "лог «снова быстрый»", lg)
_, _, n = tick(st(f), T0 + 360 + 600, "900000 2000000")
check(n == 0, "после быстрого — следующий через 900 с, не через 120", n)

# 4) Интернет медленный и сам — канал не винится, счётчик не растёт.
f0 = {"SPD": 0, "TS": T0, "TAG": "usa", "CTR": 0, "SLOW": 0, "DIR": 0}
f, lg, n = tick(st(f0), T0 + 900, "102400 1000000", direct="204800 2000000")   # 100 КБ/с vs 200 напрямую
check(f.get("CTR") == "0" and f.get("SLOW") == "0", "напрямую тоже медленно (200 КБ/с < 300) — не засчитано", f)
check("канал не виню" in lg, "лог «канал не виню»", lg)
f, lg, n = tick(st(f0), T0 + 900, "204800 1000000", direct="512000 2000000")   # 200 vs 500: < 3×
check(f.get("CTR") == "0", "напрямую быстрее меньше чем в 3 раза при RATIO=3 — не засчитано", f)
f, lg, n = tick(st(f0), T0 + 900, "512000 1000000", direct="1126400 2000000", pre="YT_SPEED_MIN_KBPS=600; YT_SPEED_RATIO=2\n")
check(f.get("CTR") == "1", "RATIO=2: канал 500 КБ/с при 1100 напрямую — засчитан (порог 600)", f)
check(re.search(r'YT_SPEED_RATIO=\$\(_num "\$\{YT_SPEED_RATIO:-2\}" 2\)', src) is not None, "дефолт YT_SPEED_RATIO в .cur — 2")

# 5) Ничего не скачано — замер не засчитан и скорость не перезаписана нулём.
f, lg, n = tick(st({**f0, "SPD": 700}), T0 + 900, "0 0")
check(f.get("CTR") == "0" and f.get("SPD") == "700", "пустой замер не считается и не затирает прошлую скорость", f)

# 6) Не мерить: глухой канал, перегрузка xray, режим применения, выключено, служебный тег.
_, _, n = tick(st(f0), T0 + 900, "11264 165000", deaf="1")
check(n == 0, "глухой канал — замера нет", n)
_, _, n = tick(st(f0), T0 + 900, "11264 165000", pre="XRAY_BUSY=1\n")
check(n == 0, "xray перегружен — замера нет", n)
_, _, n = tick(st(f0), T0 + 900, "11264 165000", pre="WD_APPLY_ID=abc\n")
check(n == 0, "режим применения — замера нет", n)
f, _, n = tick(st({**f0, "SLOW": 1, "CTR": 2}), T0 + 900, "11264 165000", pre="YT_SPEED_ENABLE=0\n")
check(n == 0 and f.get("SLOW") == "0" and f.get("CTR") == "0", "выключено — латч снят, замера нет", (f, n))
f, _, n = tick(st({**f0, "SLOW": 1}), T0 + 900, "11264 165000", tag="block")
check(n == 0 and f.get("SLOW") == "0", "block — не меряется, латч снят", (f, n))

# 7) Смена канала сбрасывает медленность.
f, _, n = tick(st({**f0, "SLOW": 1, "CTR": 2, "SPD": 11}), T0 + 900, "11264 165000", tag="server3")
check(f.get("SLOW") == "0" and f.get("CTR") == "0" and f.get("TAG") == "server3" and n == 0,
      "смена канала: латч и счётчик сброшены, замер позже", (f, n))

# 8) Карантин медленных: истёкшие записи выпадают, поиск по тегу (теги с эмодзи и двоеточием в имени).
out, err = run('YT_SLOWQ="🇸🇪_Sweden:999000,a:b:999500,old:990000"; _yt_slowq_prune; echo "Q=$YT_SLOWQ"; '
               '_yt_slowq_has "a:b" && echo HAS_AB; _yt_slowq_has old || echo NO_OLD; _yt_slowq_has "🇸🇪_Sweden" && echo HAS_SE',
               {"FAKE_NOW": str(T0)})
check("Q=🇸🇪_Sweden:999000,a:b:999500" in out, "prune: старше DEEP_RETURN_HOLD выпали, свежие на месте", out + err)
check("HAS_AB" in out and "NO_OLD" in out and "HAS_SE" in out, "_yt_slowq_has находит теги, в т. ч. с двоеточием", out)

print("test_yt_speed: " + ("OK" if fails == 0 else f"FAIL ({fails})"))
sys.exit(0 if fails == 0 else 1)
