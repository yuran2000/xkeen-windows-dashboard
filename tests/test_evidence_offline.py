# -*- coding: utf-8 -*-
"""Офлайн-тест улик сбоя канала из bootstrap/watchdog.sh.cur (v40): `evidence_capture`, `_ev_run`, `_ev_ip_from_ns`,
`evidence_save_errlog` и места их вызова.

Функции вырезаются из .cur и гоняются в sh с подменёнными date, pidof, jq, nslookup, curl, netstat, ip, ipset, dmesg
и log; iptables-save и tcpdump — скрипты в fakebin (имя с дефисом функцией в POSIX sh не подменить; tcpdump должен
находиться через `command -v`). Журналы xray, outbounds, журнал рестартов XKeen и conntrack — настоящие файлы во
временной папке (XRAY_LOG_DIR, EV_OUTBOUNDS, EV_DETACHED_LOG, EV_CONNTRACK, EV_TMP). Проверяется содержимое разделов,
поводы и частота (один снимок за тик, по роли: deaf/heal/новый pid — 60 с, start/ok — 300 с, repeat — 1800 с, часы
назад, не больше 6 в час кроме deaf/heal), источники IP (литерал/кэш/DNS роутера/77.88.8.8), conntrack только
исходного направления, дельта iptables, tcpdump, ротация, копии error.log, ложное «сохранено», ограничение
зависшего nslookup. Нужен POSIX sh (на Windows — из Git for Windows); его нет — тест пропускается. Скрипт
запускается файлом, не `sh -c` (аргумент режется на 8186 символах).

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_evidence_offline.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUR = os.path.join(ROOT, "bootstrap", "watchdog.sh.cur")
fails = 0


def check(cond, msg, extra=""):
    global fails
    print(("  ✅ " if cond else "  ❌ ") + msg + ("" if cond else f"  | {str(extra)[:500]}"))
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
check(ver >= 40, f"заголовок .cur — watchdog v{ver} (нужен ≥ 40)")
names = ("_num", "_log_every5", "_ev_run", "_ev_ip_from_ns", "evidence_capture", "evidence_save_errlog")
fns = {n: extract(src, n) for n in names}
check(all(fns.values()), "в .cur есть " + ", ".join(names), [n for n, v in fns.items() if not v])

# --- места вызова (статически) ---
dpc = extract(src, "deep_probe_check") or ""
after_fail = dpc.split("DP_CTR=$((DP_CTR + 1)); DP_RES=fail", 1)[-1][:700]
check('evidence_capture "$_dpr_role" "$_dpr_tag" "$_dpr_port" "$_dpr_url" "$_dpr_ev"' in after_fail
      and "_dpr_ev=start" in after_fail and "_dpr_ev=deaf" in after_fail,
      "deep_probe_check: снимок сразу после пустой пробы, повод start/deaf/repeat")
ok_part = dpc.split('if [ -n "$_dpr_code" ] && [ "$_dpr_code" != "000" ]; then', 1)[-1][:500]
check('evidence_capture "$_dpr_role" "$_dpr_tag" "$_dpr_port" "$_dpr_url" ok' in ok_part and '"$DP_CHK" = "1"' in ok_part,
      "deep_probe_check: снимок восстановления (ok), в т.ч. после проверки с карантина")
ai = src.split("# ---------- v24: AI exit-IP probe")[1]
check(ai.index('evidence_capture AI "$_ai_eff_now"') < ai.index('AI_DEAF_AUTORESTART" = "1"'), "AI-проба: снимок до авто-рестарта")
check("_ai_ev=start" in ai and "_ai_ev=deaf" in ai and "https://ipinfo.io/country\" ok" in ai, "AI-проба: поводы start/deaf/repeat и ok")
heal = extract(src, "_traffic_ok_or_heal") or ""
check('evidence_capture TRAFFIC "" "$HEALTH_PROBE_PORT"' in heal and heal.index("evidence_capture TRAFFIC") < heal.index("_xk_restart"),
      "лечебный рестарт: снимок с пробой через socks-health до него")
xk = extract(src, "_xk_restart") or ""
check(xk.index("evidence_save_errlog") < xk.index("/opt/sbin/xkeen -restart"), "_xk_restart: копия error.log до рестарта")
bg = extract(src, "_bg_restart_or_defer") or ""
check(bg.index("evidence_save_errlog") < bg.index("/opt/sbin/xkeen -restart"), "_bg_restart_or_defer: копия error.log до рестарта")
for why, anchor in (("fdstall", "xkeen_fdstall.log"), ("ai-deaf", "/opt/sbin/xkeen -restart >/dev/null 2>&1 &\n            exit 0"),
                    ("udpstall", "xkeen -stop; sleep 15; xkeen -start")):
    i = src.find("evidence_save_errlog " + why)
    check(i > 0 and 0 < src.find(anchor, i) - i < 400, f"копия error.log до рестарта: {why}")
check(">/dev/null 2>&1 & _ev_k=$!" in fns.get("_ev_run", "") and '"$@" & _ev_p=$!' in fns.get("_ev_run", ""),
      "_ev_run: команда в фоне без подоболочки, таймер не держит вывод")
check(not re.search(r"^\s*wait\s*(#|$)", fns.get("evidence_capture", ""), re.M), "evidence_capture: нет голого wait (ждёт только свои задачи)")
check("diff " not in fns.get("evidence_capture", ""), "дельта iptables без diff (у BusyBox unified-формат)")

sh = find_sh()
if not sh or not all(fns.values()):
    print("test_evidence_offline: SKIP (нет sh)" if not sh and fails == 0 else "test_evidence_offline: FAIL")
    sys.exit(0 if not sh and fails == 0 else 1)

tmp = tempfile.mkdtemp(prefix="wdev_")
P = lambda *a: os.path.join(tmp, *a).replace("\\", "/")  # noqa: E731
LOG, JQLOG, STATE, EVD, CNT = P("log.txt"), P("jq.txt"), P("state"), P("evidence"), P("ipt.cnt")
for d in ("xlog", "cfg", "fakebin", "evtmp"):
    os.makedirs(P(d))


def wr(path, text):
    open(path, "w", encoding="utf-8", newline="").write(text)


wr(STATE, "primary 0 S3 EFFECTIVE_AI=S2 " + " ".join(f"F{i}=v{i}" for i in range(70)) + " LASTFIELD=end\n")
wr(P("xlog", "error.log"), "2026/10/14 18:20:01 [Warning] core: Xray 26.5.9 started\n"
   "2026/10/14 18:20:05 [Warning] failed to dial: lookup vps.example on 127.0.0.1:53: i/o timeout\n")
wr(P("xlog", "access.log"),
   "2026/10/14 18:20:06 from 192.168.1.100:1 accepted tcp:1.1.1.1:443 [redirect -> S3]\n"
   "2026/10/14 18:20:07 from 192.168.1.100:2 accepted tcp:2.2.2.2:443 [redirect >> S3]\n"
   "2026/10/14 18:20:08 from 192.168.1.100:3 accepted tcp:3.3.3.3:443 [redirect -> OTHER]\n"
   "2026/10/14 18:20:09 from 127.0.0.1:4 accepted tcp:192.0.2.10:8444 [redirect -> S3]\n")
wr(P("cfg", "04_outbounds.json"), "{}\n")
wr(P("cfg", "01_log.json"), '{"log":{"loglevel":"warning"},"inbounds":[{"id":"SECRET-UUID-1111","privateKey":"SECRETPK"}]}\n')
wr(P("detached.log"), "=== 2026-10-14 21:18:51 /opt/sbin/xkeen -restart ===\n")
# conntrack: исходящее роутера (WAN 198.51.100.1) к узлу, исходящее ЛВС к узлу, и ВХОДЯЩЕЕ узел→роутер (обратный кортеж
# содержит dst=192.0.2.10 — не должно попасть в раздел)
wr(P("conntrack"),
   "ipv4 2 tcp 6 10 SYN_SENT src=198.51.100.1 dst=192.0.2.10 sport=40000 dport=8444 [UNREPLIED] src=192.0.2.10 dst=198.51.100.1 sport=8444 dport=40000 mark=0 use=1\n"
   "ipv4 2 tcp 6 300 ESTABLISHED src=192.168.1.100 dst=192.0.2.10 sport=50000 dport=443 src=192.0.2.10 dst=198.51.100.1 sport=443 dport=50000 [ASSURED] [FASTNAT] mark=0 use=1\n"
   "ipv4 2 tcp 6 300 ESTABLISHED src=192.0.2.10 dst=198.51.100.1 sport=33333 dport=22 src=192.168.1.1 dst=192.0.2.10 sport=22 dport=33333 [ASSURED] mark=0 use=1\n")
# iptables-save: счётчик растёт от вызова к вызову (дельта должна быть видна); tcpdump: SYN без ответа
wr(P("fakebin", "iptables-save"), "#!/bin/sh\nn=$(cat '" + CNT + "' 2>/dev/null || echo 0); n=$((n+1)); echo $n > '" + CNT + "'\n"
   "echo \"[$n:$((n*60))] -A OUTPUT -j _NDM_DNAT\"\necho '[5:300] -A FORWARD -j xkeen_ks'\n")
wr(P("fakebin", "tcpdump"), "#!/bin/sh\necho 'IP 198.51.100.1.40000 > 192.0.2.10.8444: Flags [S], seq 1'\nexec sleep 30\n")
FB = P("fakebin")
FB_MSYS = "/" + FB[0].lower() + FB[2:] if FB[1:2] == ":" else FB
NS_OLD = "printf 'Server: 127.0.0.1\\nAddress 1: 127.0.0.1 localhost\\n\\nName: vps.example\\nAddress 1: 192.0.2.10 vps\\n'"
NS_NEW = "printf 'Server:\\t\\t77.88.8.8\\nAddress:\\t77.88.8.8:53\\n\\nNon-authoritative answer:\\nName:\\tvps.example\\nAddress: 192.0.2.10\\n\\n*** Can not find vps.example: No answer\\n'"

PRE = (
    "export PATH='" + FB_MSYS + "':$PATH\n"
    "log() { echo \"LOG $*\" >> '" + LOG + "'; }\n"
    "STATE_FILE='" + STATE + "'\n"
    "XRAY_LOG_DIR='" + P("xlog") + "'; EV_OUTBOUNDS='" + P("cfg", "04_outbounds.json") + "'; EV_DETACHED_LOG='" + P("detached.log") + "'\n"
    "EV_CONNTRACK='" + P("conntrack") + "'; EV_TMP='" + P("evtmp") + "'\n"
    "EVIDENCE_ENABLE=1; EVIDENCE_COOLDOWN=1800; EVIDENCE_KEEP=40; EVIDENCE_KEEP_ERRLOG=15; EVIDENCE_MAX_PER_HOUR=6\n"
    "EVIDENCE_DIR='" + EVD + "'\n"
    "date() { echo \"$FAKE_NOW\"; }\n"
    "pidof() { [ -n \"$FAKE_PID\" ] && echo \"$FAKE_PID\"; }\n"
    "jq() { cat >/dev/null; printf '%s\\n' \"$*\" >> '" + JQLOG + "'\n"
    "  case \"$*\" in *'.address == $h'*) printf '8444\\n443\\n' ;; *'{a:'*) echo '{\"a\":\"vps.example\",\"s\":null}' ;;\n"
    "    *) [ -n \"$JQ_EMPTY\" ] || echo \"${JQ_HOST:-vps.example} 8444\" ;; esac; }\n"
    "nslookup() { [ -n \"$NS_HANG\" ] && exec sleep 30\n"
    "  case \"$2\" in 127.0.0.1) [ -n \"$NS_FAIL\" ] && { echo \"nslookup: can't resolve '$1'\"; return 0; }; " + NS_OLD + " ;;\n"
    "    *) [ -n \"$NS2_FAIL\" ] && { echo \"nslookup: can't resolve '$1'\"; return 0; }; " + NS_NEW + " ;; esac; }\n"
    "curl() { case \"$*\" in *socks5*) echo '* TLSv1.3 (OUT), TLS handshake, Client hello (1):'; echo 'итог: code=000 connect=0s total=8s' ;;\n"
    "  *--resolve*) echo '  PORT: connects=0 connect=0s code=000' ;; *) echo 'connects=1 connect=0.1s code=404' ;; esac; }\n"
    "netstat() { echo 'tcp 0 0 198.51.100.1:40000 192.0.2.10:8444 SYN_SENT 4242/xray'; echo 'tcp 0 120 198.51.100.1:5001 192.0.2.10:443 ESTABLISHED 4242/xray'; }\n"
    "ip() { case \"$*\" in *'addr show'*) echo '    inet 198.51.100.1/32 scope global ppp0' ;; *'rule'*) echo '0: from all lookup local' ;;\n"
    "  *'route get'*) echo '192.0.2.10 dev ppp0 src 198.51.100.1' ;; *) : ;; esac; }\n"
    "ipset() { case \"$1\" in list) printf 'ext_exclude\\next_exclude6\\n_NDM_x\\n' ;; test) echo \"$3 is NOT in set $2.\" ;; esac; }\n"
    "dmesg() { echo 'device ppp0 entered promiscuous mode'; echo 'kernel: something real'; }\n"
    + "".join(fns[n] for n in names)
)


def run(body, env=None, timeout=120):
    e = dict(os.environ)
    e.setdefault("FAKE_PID", "4242")
    e.update(env or {})
    script = P("run.sh")
    wr(script, PRE + body)
    try:
        r = subprocess.run([sh, script], capture_output=True, text=True, encoding="utf-8", env=e,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT"
    return r.stdout.strip(), r.stderr.strip()


def snaps():
    return sorted(f for f in os.listdir(EVD) if f.endswith(".txt")) if os.path.isdir(EVD) else []


def read(name):
    p = os.path.join(EVD, name)
    return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""


def cap(role, why, now, port="10819", tag="S3", env=None, url="https://x/"):
    e = {"FAKE_NOW": str(now)}
    e.update(env or {})
    return run(f'evidence_capture {role} "{tag}" "{port}" "{url}" {why}', e)


def fresh():
    shutil.rmtree(EVD, ignore_errors=True)


# 1) Снимок start: имя, разделы, настоящие строки журналов, jq-фильтры, без секретов.
t0 = time.time(); cap("PRI", "start", 1000); t_base = time.time() - t0
body = read("1000-PRI-start.txt")
check(snaps() == ["1000-PRI-start.txt"], "снимок: <дата>-<роль>-<повод>.txt", snaps())
for sec in ("роль=PRI повод=start канал=S3 узел=vps.example:8444", "lookup vps.example on 127.0.0.1:53: i/o timeout",
            "tcp:1.1.1.1:443 [redirect -> S3]", "tcp:2.2.2.2:443 [redirect >> S3]", "== DNS vps.example: A через роутер",
            "мимо кэша роутера (77.88.8.8)", "Non-authoritative answer", "IP=192.0.2.10 (откуда: DNS роутера)",
            "ПО IP 192.0.2.10 на все порты", "PORT: connects=0", "ext_exclude: 192.0.2.10 is NOT", "accepted tcp:192.0.2.10:8444",
            "SYN_SENT 192.0.2.10", "Send-Q>0: 1", "РОУТЕР SYN_SENT dport=8444 UNREPLIED", "ЛВС ESTABLISHED dport=443 ок",
            "Client hello", "итог: code=000", "-A OUTPUT -j _NDM_DNAT", "== SYN/RST на ppp0 к 192.0.2.10",
            "Flags [S]", "kernel: something real", "21:18:51 /opt/sbin/xkeen -restart", "LASTFIELD=end", '"loglevel":"warning"'):
    check(sec in body, f"в снимке есть «{sec}»", body[-700:])
check("dport=22" not in body, "conntrack: входящее узел→роутер (обратный кортеж) не попадает в раздел", "")
check("-A FORWARD -j xkeen_ks" not in body, "дельта iptables: неизменившиеся строки не печатаются", "")
check("tcp:3.3.3.3:443 [redirect -> OTHER]" not in body.split("== access.log: последние 15")[0], "раздел канала не берёт чужой тег", "")
check("promiscuous" not in body and "ext_exclude6" not in body, "без шума dmesg и IPv6-наборов", "")
check("SECRET" not in body, "секреты из однострочного json в снимок не попадают", "")
jql = open(JQLOG, encoding="utf-8").read() if os.path.exists(JQLOG) else ""
check("--arg t S3" in jql and ".settings.vnext[0] // .settings.servers[0]" in jql and "select(.tag == $t)" in jql,
      "jq: тег через --arg, адрес из vnext[0] // servers[0]", jql[:300])
check("select(.address == $h) | .port" in jql, "jq: порты всех outbound'ов того же узла", jql[:300])
check(not os.listdir(P("evtmp")), "временные файлы снимка удалены", os.listdir(P("evtmp")))

# 2) Один снимок за тик.
fresh()
run('evidence_capture PRI S3 10819 https://x/ start; evidence_capture FGN S3 10821 https://y/ start', {"FAKE_NOW": "2000"})
check(snaps() == ["2000-PRI-start.txt"], "один снимок за тик: второй в том же тике пропущен", snaps())

# 3) Частота по роли.
fresh()
cap("PRI", "start", 3000)
cap("PRI", "repeat", 3100); check(len(snaps()) == 1, "repeat через 100 с — нет (1800)", snaps())
cap("FGN", "start", 3100); check("3100-FGN-start.txt" in snaps(), "другая роль — свой кулдаун", snaps())
cap("PRI", "deaf", 3050); check("3050-PRI-deaf.txt" not in snaps(), "deaf через 50 с — нет (60)", snaps())
cap("PRI", "deaf", 3070); check("3070-PRI-deaf.txt" in snaps(), "deaf через 70 с — есть", snaps())
cap("PRI", "ok", 3200); check("3200-PRI-ok.txt" not in snaps(), "ok через 130 с — нет (start/ok — 300)", snaps())
cap("PRI", "ok", 3400); check("3400-PRI-ok.txt" in snaps(), "ok через 330 с — есть", snaps())
cap("PRI", "repeat", 3470, env={"FAKE_PID": "5555"}); check("3470-PRI-repeat.txt" in snaps(), "repeat после смены pid xray (70 с) — есть", snaps())
cap("PRI", "repeat", 2000, env={"FAKE_PID": "5555"}); check("2000-PRI-repeat.txt" in snaps(), "часы назад — кулдаун истёк", snaps())

# 4) Не больше 6 в час, кроме deaf/heal.
fresh()
for i in range(8):
    cap(f"R{i}", "start", 10000 + i * 61)
n = len(snaps())
check(n == 6, f"лимит в час: из 8 ролей снято {n} (6)", snaps())
cap("R9", "deaf", 10600); check("10600-R9-deaf.txt" in snaps(), "deaf сверх лимита — есть", snaps())
cap("R10", "start", 13700); check("13700-R10-start.txt" in snaps(), "через час лимит обнулился", snaps())

# 5) Источники IP: DNS роутера не ответил, кэша нет → 77.88.8.8 (новый формат BusyBox).
fresh()
cap("PRI", "start", 20000, env={"NS_FAIL": "1"})
b = read("20000-PRI-start.txt")
check("IP=192.0.2.10 (откуда: 77.88.8.8)" in b and "conntrack к 192.0.2.10" in b, "DNS роутера молчит: IP из 77.88.8.8, разделы сняты", b[:600])
# кэш: оба DNS молчат — IP из кэша прошлого снимка
cap("FGN", "start", 20400, env={"NS_FAIL": "1", "NS2_FAIL": "1"})
b = read("20400-FGN-start.txt")
check("IP=192.0.2.10 (откуда: кэш)" in b, "оба DNS молчат: IP из кэша", b[:600])
# литерал IP в outbound
cap("YT", "start", 20800, env={"JQ_HOST": "203.0.113.5", "NS_FAIL": "1", "NS2_FAIL": "1"})
check("IP=203.0.113.5 (откуда: адрес узла)" in read("20800-YT-start.txt"), "адрес узла — IP: берётся как есть", "")

# 6) Outbound без адреса — без сетевых разделов узла.
fresh()
cap("YT", "start", 21000, env={"JQ_EMPTY": "1"})
b = read("21000-YT-start.txt")
check(b != "" and "== DNS" not in b and "ПО IP" not in b, "outbound без адреса: DNS/TCP-разделы пропущены", b[:300])

# 7) heal без тега, с пробой.
run('evidence_capture TRAFFIC "" 10822 https://ya.ru/ heal', {"FAKE_NOW": "21100"})
b = read("21100-TRAFFIC-heal.txt")
check("повод=heal" in b and "итог: code=000" in b and "== DNS" not in b, "heal: снимок с пробой, без раздела узла", b[:300])

# 8) Ротация KEEP=3 и KEEP=0.
fresh()
for i, now in enumerate(range(30001, 30006)):
    run(f'EVIDENCE_KEEP=3; evidence_capture R{i} S3 "" "" deaf', {"FAKE_NOW": str(now)})
check(len(snaps()) == 3 and snaps()[-1].startswith("30005"), "ротация: 3 последних", snaps())
run('EVIDENCE_KEEP=0; evidence_capture Z S3 "" "" deaf', {"FAKE_NOW": "30100"})
check("30100-Z-deaf.txt" in snaps(), "KEEP=0 не удаляет только что снятый", snaps())

# 9) Ложное «сохранено»: файл снимка не записался.
fresh(); os.makedirs(os.path.join(EVD, "40000-PRI-deaf.txt"))
if os.path.exists(LOG):
    os.remove(LOG)
cap("PRI", "deaf", 40000)
lg = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
check("не записан" in lg and "EVIDENCE: улики" not in lg, "файл не записался — WARN, а не «сохранено»", lg)

# 10) Копии error.log: ротация, KEEP_ERRLOG=0, повод с пробелом.
fresh()
for now in range(50001, 50005):
    run('EVIDENCE_KEEP_ERRLOG=2; evidence_save_errlog restart', {"FAKE_NOW": str(now)})
el = sorted(f for f in os.listdir(EVD) if f.startswith("errlog-"))
check(len(el) == 2 and "i/o timeout" in open(os.path.join(EVD, el[-1]), encoding="utf-8").read(), "errlog: копия хвоста, хранятся 2", el)
run('EVIDENCE_KEEP_ERRLOG=0; evidence_save_errlog "bg-v23 migration"', {"FAKE_NOW": "50100"})
el = sorted(f for f in os.listdir(EVD) if f.startswith("errlog-"))
check("errlog-50100-bg-v23_migration.log" in el, "KEEP_ERRLOG=0 не стирает свежую копию; пробел в поводе заменён", el)

# 11) Зависший nslookup ограничен: прибавка к обычному снимку меньше 2 пределов _ev_run (6 с) с запасом.
fresh()
t0 = time.time(); cap("H", "start", 60000, env={"NS_HANG": "1"}); t_hang = time.time() - t0
check(t_hang - t_base < 25, f"зависший nslookup: +{t_hang - t_base:.1f} с к обычному снимку ({t_base:.1f} с; без предела было бы ≥ 30)", t_hang)

# 12) Выключено.
fresh()
run('EVIDENCE_ENABLE=0; evidence_capture PRI S3 "" "" deaf', {"FAKE_NOW": "70000"})
check(snaps() == [], "EVIDENCE_ENABLE=0: снимков нет", snaps())

shutil.rmtree(tmp, ignore_errors=True)
print("test_evidence_offline: " + ("OK" if not fails else f"FAIL ({fails})"))
sys.exit(1 if fails else 0)
