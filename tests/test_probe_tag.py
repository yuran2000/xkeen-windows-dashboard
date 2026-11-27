# -*- coding: utf-8 -*-
"""Офлайн-тест probe_tag из bootstrap/watchdog.sh.cur (v32): узел жив, если TCP установился,
даже когда curl вышел по таймауту (28) — Reality отдаёт пробу сайту-маскировке, и тот может молчать.
Мёртв — только если соединения не было (%{num_connects} = 0) или отказ (7).

Функции вырезаются из .cur и гоняются в sh с подменённым curl (без роутера и сети). Нужен POSIX sh
(на Windows — из Git for Windows); его нет — тест пропускается.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_probe_tag.py
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

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
check(int(re.search(r"watchdog\s+v(\d+)", "\n".join(src.splitlines()[:40])).group(1)) >= 32,
      "заголовок .cur — watchdog v32 или новее")
fn_conn = extract(src, "_probe_connected")
fn_probe = extract(src, "probe_tag")
fn_ready = extract(src, "_wd_probe_tool_ready")
check(fn_conn is not None and fn_probe is not None, "в .cur есть _probe_connected и probe_tag")
check("num_connects" in (fn_probe or ""), "probe_tag берёт %{num_connects}, а не time_connect")

sh = find_sh()
check(fn_ready is not None, "в .cur есть проверка локального curl")
if not sh or fn_conn is None or fn_probe is None or fn_ready is None:
    print("test_probe_tag: SKIP (нет sh)" if not sh else "test_probe_tag: FAIL")
    sys.exit(0 if not sh and fails == 0 else 1)

# Подменный curl: печатает FAKE_OUT (то, что дал бы -w) и выходит с FAKE_RC.
HARNESS = (
    r"""curl() {
  if [ -n "$FAKE_URL" ]; then
    for arg; do last=$arg; done
    [ "$last" = "$FAKE_URL" ] || return 3
  fi
  printf '%s' "$FAKE_OUT"
  return "$FAKE_RC"
}
get_host_port() { printf '%s 8443\n' "${FAKE_ADDRESS:-node.example}"; }
"""
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
    ("TCP: ошибка загрузчика curl (127)", "n1", "", "", 127, "DEAD"),
    ("TCP: сборка curl без нужной функции (4)", "n1", "", "0", 4, "DEAD"),
    ("TCP: неожиданный текст вместо числа", "n1", "", "undefined symbol", 0, "DEAD"),
    ("URL: ошибка загрузчика curl", "n1", "https://node.example:8443/", "", 127, "DEAD"),
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

for address in ("2001:db8::1", "[2001:db8::1]"):
    r = subprocess.run([sh, "-c", HARNESS, "probe", "n1", ""], capture_output=True, text=True,
                       env=dict(os.environ, FAKE_OUT="1", FAKE_RC="0",
                                FAKE_ADDRESS=address, FAKE_URL="https://[2001:db8::1]:8443/"),
                       stdin=subprocess.DEVNULL, timeout=30)
    check(r.stdout.strip() == "ALIVE", f"IPv6 TCP: адрес {address} получает корректный URL")

# Берем реальный вход в обычный тик до сетевых действий.
tick_start = src.index('# После отката прерванного применения проверяем curl')
tick_end = src.index('flush_outbox\n', tick_start) + len('flush_outbox\n')
cron_gate = src[tick_start:tick_end]
probe_shell = r'''
curl() {
    [ "$LD_BIND_NOW" = 1 ] || return 99
    [ "$1:$3:$5" = '-q::127.0.0.1:1' ] || return 98
    if [ "$FAKE_RC" != 7 ]; then
        printf 'curl: %s\n' "$FAKE_ERROR" >&2
    fi
    return "$FAKE_RC"
}
log() { printf '%s\n' "$*"; }
date() { printf '%s\n' "$FAKE_NOW"; }
flush_outbox() { echo TICK; }
''' + fn_ready + cron_gate

with tempfile.TemporaryDirectory(prefix="wdcurl_") as tmp:
    env = dict(os.environ, LOCK=(Path(tmp) / "lock").as_posix(), WD_APPLY_ID="")

    def tick(rc, now, error=""):
        r = subprocess.run([sh, "-c", probe_shell], capture_output=True, text=True, encoding="utf-8",
                           env={**env, "FAKE_RC": str(rc), "FAKE_NOW": str(now), "FAKE_ERROR": error},
                           stdin=subprocess.DEVNULL, timeout=30)
        return r.returncode, r.stdout

    rc, out = tick(127, 1000, "undefined symbol: curl_multi_notify_enable")
    check(rc == 1 and "CURL-UNAVAILABLE" in out and "undefined symbol" in out and "TICK" not in out,
          "cron: сломанный curl останавливает тик до сетевых действий")
    check(tick(127, 1060) == (1, ""), "cron: один сбой не засоряет журнал на каждом тике")
    rc, out = tick(4, 1300, "proxy support is disabled")
    check(rc == 1 and "proxy support is disabled" in out and "TICK" not in out,
          "cron: сборка без SOCKS останавливает тик")
    rc, out = tick(7, 1360)
    check(rc == 0 and "CURL-READY" in out and "TICK" in out,
          "cron: исправный curl с отказом на порту loopback возобновляет тик")
    check(tick(7, 1420) == (0, "TICK\n"), "cron: восстановление отмечается однажды")

if fails:
    print(f"test_probe_tag: FAIL ({fails})")
    sys.exit(1)
print("test_probe_tag: OK")
