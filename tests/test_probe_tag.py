# -*- coding: utf-8 -*-
"""Офлайн-тест probe_tag из bootstrap/watchdog.sh.cur (v32): узел жив, если TCP установился,
даже когда curl вышел по таймауту (28) — Reality отдаёт пробу сайту-маскировке, и тот может молчать.
Мёртв — только если соединения не было (%{num_connects} = 0) или отказ (7).

Функции вырезаются из .cur и гоняются в sh с подменённым curl (без роутера и сети). Нужен POSIX sh
(на Windows — из Git for Windows); его нет — тест пропускается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_probe_tag.py
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUR = os.path.join(ROOT, "bootstrap", "watchdog.sh.cur")

fails = 0


def check(cond, msg):
    global fails
    print(("  ✅ " if cond else "  ❌ ") + msg)
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
check(re.search(r"watchdog\s+v(\d+)", "\n".join(src.splitlines()[:40])).group(1) == "32",
      "заголовок .cur — watchdog v32")
fn_conn = extract(src, "_probe_connected")
fn_probe = extract(src, "probe_tag")
check(fn_conn is not None and fn_probe is not None, "в .cur есть _probe_connected и probe_tag")
check("num_connects" in (fn_probe or ""), "probe_tag берёт %{num_connects}, а не time_connect")

sh = find_sh()
if not sh or fn_conn is None or fn_probe is None:
    print("test_probe_tag: SKIP (нет sh)" if not sh else "test_probe_tag: FAIL")
    sys.exit(0 if not sh and fails == 0 else 1)

# Подменный curl: печатает FAKE_OUT (то, что дал бы -w) и выходит с FAKE_RC.
HARNESS = (
    "curl() { printf '%s' \"$FAKE_OUT\"; return \"$FAKE_RC\"; }\n"
    "get_host_port() { echo 'node.example 8443'; }\n"
    + fn_conn + fn_probe +
    "if probe_tag \"$1\" \"$2\"; then echo ALIVE; else echo DEAD; fi\n"
)

CASES = [
    # (описание, tag, probe_url, вывод -w, код curl, ожидание)
    ("TCP: ответ получен (0)", "n1", "", "1", 0, "ALIVE"),
    ("TCP: SSL-ошибка после connect (35)", "n1", "", "1", 35, "ALIVE"),
    ("TCP: таймаут, TCP установлен (28, num_connects=1) — медленный dest", "n1", "", "1", 28, "ALIVE"),
    ("TCP: таймаут без соединения (28, num_connects=0)", "n1", "", "0", 28, "DEAD"),
    ("TCP: отказ в соединении (7)", "n1", "", "0", 7, "DEAD"),
    ("TCP: пустой вывод при 28", "n1", "", "", 28, "DEAD"),
    ("direct — всегда жив", "direct", "", "0", 7, "ALIVE"),
    ("URL: HTTP 404", "n1", "https://node.example:8443/", "404 1", 0, "ALIVE"),
    ("URL: код 000, TCP установлен (таймаут dest)", "n1", "https://node.example:8443/", "000 1", 28, "ALIVE"),
    ("URL: код 000, соединения не было", "n1", "https://node.example:8443/", "000 0", 28, "DEAD"),
    ("URL: отказ (7)", "n1", "https://node.example:8443/", "000 0", 7, "DEAD"),
]

for desc, tag, url, out, rc, want in CASES:
    env = dict(os.environ, FAKE_OUT=out, FAKE_RC=str(rc))
    r = subprocess.run([sh, "-c", HARNESS, "probe", tag, url], capture_output=True, text=True,
                       env=env, stdin=subprocess.DEVNULL, timeout=30)
    got = r.stdout.strip()
    check(got == want, f"{desc}: {got or r.stderr.strip()[:120]} (ждали {want})")

if fails:
    print(f"test_probe_tag: FAIL ({fails})")
    sys.exit(1)
print("test_probe_tag: OK")
