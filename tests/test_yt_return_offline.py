# -*- coding: utf-8 -*-
"""Возврат YouTube: настоящие shell-блоки сторожа, подменённые сеть и рестарты.

Не импортирует панель, не выполняет целый watchdog, не обращается к роутеру.
Проверяет выбор канала и уведомления до/после применения, а также границы проб.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "bootstrap/watchdog.sh.cur").read_text(encoding="utf-8")
SH = shutil.which("sh") or r"C:\Program Files\Git\bin\sh.exe"
if not Path(SH).is_file():
    raise SystemExit("test_yt_return_offline: FAIL (нужен локальный sh)")


def extract(name):
    m = re.search(r"^" + re.escape(name) + r"\(\) \{\n.*?^\}\n", SRC, re.S | re.M)
    assert m, name
    return m.group(0)


NAMES = ("_num", "_state_field", "_deep_hold", "_log_every5", "_yt_speed_kbps",
         "yt_return_check", "yt_return_notify", "_yt_slowq_prune", "_yt_slowq_has")
FNS = "\n".join(extract(n) for n in NAMES)
YT = SRC[SRC.index("# ---------- YT kill-switch"):SRC.index("# ---------- FOREIGN kill-switch")]
APPLY = SRC[SRC.index("# ---------- Apply changes"):SRC.index("# ---------- v35: итог режима применения")]
checks = 0


def check(ok, name, detail=""):
    global checks
    assert ok, name + ": " + detail
    checks += 1
    print("OK " + name, flush=True)


with tempfile.TemporaryDirectory(prefix="yt-return-offline-") as tmp:
    base = Path(tmp).as_posix()
    pre = f"""
STATE_FILE='{base}/state'
CONFIG_DIR='{base}'
COUNT_FILE='{base}/calls'
log() {{ echo "LOG $*"; }}
tg_alert() {{ echo "TG $*"; }}
date() {{ echo "$NOW"; }}
curl() {{ echo "$*" >> "$COUNT_FILE"; echo "$SAMPLE"; }}
_yt_return_route_ready() {{ [ "$ROUTE_OK" = 1 ]; }}
probe_tag() {{
  if [ "$1" = "$YT_TAG" ]; then [ "$MAIN_TCP" = 1 ];
  elif [ "$1" = reserve ]; then [ "$RES_TCP" = 1 ];
  else [ "$ALT_TCP" = 1 ]; fi
}}
is_ru_tag() {{ return 1; }}
killswitch_decide() {{ KS_BLOCKED=0; KS_CTR=0; [ "$1" != down ] || KS_BLOCKED=1; }}
deep_probe_check() {{
  DP_CTR=0; DP_TS=$NOW; DP_DEAF=$DEAF; DP_TAG=$4; DP_RES=ok
  DP_QN=$(_state_field DYT_QN 0); DP_CHK=0
}}
yt_speed_check() {{ YT_SLOW=$SLOW; YT_SPD=31; YT_SPD_DIR=1391; }}
{FNS}
NOW=1000000; SAMPLE='819200 2000000 206'; ROUTE_OK=1
MAIN_TCP=1; RES_TCP=1; ALT_TCP=1; DEAF=0; SLOW=0
YT_TAG=main; YT_DOMAINS=youtube.com; YT_FAILOVER_TAGS='reserve other'
YT_SPEED_ENABLE=1; YT_SPEED_MIN_KBPS=500; YT_SPEED_RECHECK=120
YT_SPEED_BYTES=2000000; YT_SPEED_TIME=15; YT_SPEED_URL=https://invalid.example/test
YT_RETURN_PROBE_PORT=10823; YT_CHAIN_ALLOW_RU=1; YT_CHAIN_ROUNDS=2
YT_FAIL_BLOCK=0; YT_BLOCKED=0; YT_KCTR=0
DEEP_RETURN_HOLD=1800; DEEP_RETURN_HOLD_MAX=86400
XRAY_BUSY=0; WD_APPLY_ID=''; XRAY_BUSY_BLAME=''
EFFECTIVE_YT_TAG=$YT_TAG
"""

    def run(extra="", state="YT_MAIN=main YT_EFF=reserve DYT_QN=1 DYT_LEFT=990000", tail="", yt=True):
        (Path(tmp) / "state").write_text(state + "\n", encoding="utf-8", newline="\n")
        (Path(tmp) / "calls").write_text("", encoding="utf-8", newline="\n")
        script = pre + extra + "\n" + (YT if yt else "") + "\n" + tail
        # Текст через файл, чтобы Windows не перекодировал кириллицу/кавычки argv.
        script_path = Path(tmp) / "scenario.sh"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        result = subprocess.run([SH, script_path.as_posix()], encoding="utf-8", capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=35)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stderr, result.stderr
        return result.stdout, (Path(tmp) / "calls").read_text(encoding="utf-8")

    end = 'echo "RESULT=$EFFECTIVE_YT_TAG QN=$DYT_QN LEFT=$DYT_LEFT CHECKED=$YT_RETURN_OK"\n'
    out, calls = run(tail=end)
    check("RESULT=main" in out and "CHECKED=1" in out and len(calls.splitlines()) == 1,
          "быстрый основной проверен через отдельный вход", out)
    check("127.0.0.1:10823" in calls and "10820" not in calls, "проба не использует действующий резерв")
    check("TG " not in out, "до применения маршрута нет успешного уведомления", out)

    for label, sample in [("медленный", "31744 400000 206"), ("пустой", "0 0 000"),
                          ("ошибка HTTP", "10000000 2000000 403"),
                          ("короткая страница", "10000000 512 200")]:
        out, _ = run(f"SAMPLE='{sample}'\n", tail=end)
        check("RESULT=reserve" in out and "CHECKED=0" in out and "работает" not in out,
              label + ": остаётся резерв, успех не объявлен", out)

    for label, extra in [("маршрут пробы не готов", "ROUTE_OK=0"),
                         ("перегрузка", "XRAY_BUSY=1"), ("применение панели", "WD_APPLY_ID=test")]:
        out, calls = run(extra + "\n", tail=end)
        check("RESULT=reserve" in out and not calls, label + ": нет скачивания и возврата", out)

    out, calls = run(state="YT_MAIN=main YT_EFF=reserve DYT_QN=1 DYT_LEFT=999940", tail=end)
    check("RESULT=reserve" in out and not calls, "карантин не обходится")
    out, calls = run(state="YT_MAIN=main YT_EFF=reserve DYT_QN=1 YT_RETURN_TAG=main YT_RETURN_TS=999940", tail=end)
    check("RESULT=reserve" in out and not calls, "неудачная проба не повторяется каждую минуту")
    out, calls = run(state="YT_MAIN=main YT_EFF=reserve DYT_QN=1 YT_RETURN_TAG=main YT_RETURN_TS=1000060", tail=end)
    check("RESULT=main" in out and bool(calls), "часы назад не замораживают проверку")

    out, _ = run("SLOW=1; SAMPLE='31744 400000 206'\n", tail=end)
    check("RESULT=other" in out and "работает" not in out,
          "плохой резерв и медленный основной: выбирается другой резерв", out)
    out, _ = run("SLOW=1; RES_TCP=0; ALT_TCP=0; SAMPLE='31744 400000 206'\n", tail=end)
    check("RESULT=reserve" in out and "работает" not in out,
          "нет альтернатив: основной не выбирается в обход проверки", out)

    for sample, expected in [("31744 400000 206", "block"), ("819200 2000000 206", "main")]:
        out, _ = run(f"SAMPLE='{sample}'\n", state="YT_MAIN=main YT_EFF=block", tail=end)
        check("RESULT=" + expected in out, "выход из block: " + expected, out)

    out, calls = run("YT_SPEED_ENABLE=0\n", tail='PREV_YT=reserve\nyt_return_notify\n' + end)
    check("RESULT=main" in out and not calls and "работает" not in out and "выключен" in out,
          "выключенный контроль: нет обещания подтверждённой скорости", out)
    out, calls = run(state="YT_MAIN=old YT_EFF=old DYT_QN=2", tail='PREV_YT=old\nyt_return_notify\n' + end)
    check("RESULT=main" in out and not calls and "TG " not in out, "ручная смена не названа восстановлением", out)

    apply_pre = """
PREV_YT=reserve; PREV_AI=ai; PREV_FOREIGN=fgn
EFFECTIVE_AI_TAG=ai; EFFECTIVE_FOREIGN_TAG=fgn
NEW_DEFAULT=primary; CUR_DEFAULT=primary; PRIMARY_TAG=primary
LAST_BAD_MD5=''; APPLY_FILES=''; APPLY_OK=1; TEST_OK=1
generate_routing() { echo '{}' > "$CONFIG_DIR/05_routing.json.new"; }
routing_changed() { return 0; }
validate_routing_xray() { [ "$TEST_OK" = 1 ]; }
_xk_restart() { echo RESTART; return 0; }
health_check() { [ "$APPLY_OK" = 1 ]; }
_traffic_ok_or_heal() { return 0; }
echo '{}' > "$CONFIG_DIR/05_routing.json"
echo '{}' > "$CONFIG_DIR/05_routing.json.lastgood"
"""
    out, _ = run(tail=apply_pre + APPLY + end)
    check(out.count("TG ✅ <b>YouTube работает") == 1 and out.index("RESTART") < out.index("TG ✅"),
          "работает отправляется один раз после применения и health-check", out)
    check("QN=0" in out and "LEFT=0" in out, "подтверждённый возврат снимает карантин", out)
    for failure in ("APPLY_OK=0", "TEST_OK=0"):
        out, _ = run(tail=apply_pre + failure + "\n" + APPLY + end)
        check("TG ✅" not in out, failure + ": нет ложного успешного уведомления", out)

    # Отдельно настоящий deep_probe_check: короткий HTTP-ответ не доказывает скорость.
    deep_pre = extract("deep_probe_check") + """
DEEP_PROBE_ENABLE=1; DEEP_PROBE_INTERVAL=180; DEEP_PROBE_THRESHOLD=2
evidence_capture() { :; }
curl() { echo 200; }
deep_probe_check YT 10820 https://invalid.example main YouTube 1 main
echo "QN=$DP_QN"
"""
    out, _ = run(state="DYT_TAG=main DYT_TS=900000 DYT_QN=2 DYT_CHK=1", tail=deep_pre, yt=False)
    check("работает" not in out and "QN=2" in out, "сквозной успех не подтверждает скорость и не сбрасывает карантин", out)

    # Fail-closed чтение пробы: настоящая обвязка jq, подменён только JSON-процессор.
    route_fn = extract("_yt_return_route_ready")
    for rc in (0, 1):
        out, _ = run(tail=route_fn + f"""
jq() {{ cat >/dev/null; return {rc}; }}
echo '{{}}' > "$CONFIG_DIR/05_routing.json"
echo '{{}}' > "$CONFIG_DIR/03_inbounds.json"
if _yt_return_route_ready; then echo READY; else echo UNREADY; fi
""", yt=False)
        check(("UNREADY" if rc else "READY") in out, "jq результат " + str(rc) + " корректно обработан")

    check(SRC.count("yt_return_notify\n") == 1, "уведомление подключено только в успешной ветке Apply")
    check("YT_RETURN_TAG=$YT_RETURN_TAG" in SRC and "YT_RETURN_TS=${YT_RETURN_TS:-0}" in SRC,
          "повторные пробы сохраняют свой таймер в state")

    # Создание входа: проверяем настоящую файловую обвязку с ошибками jq.
    ensure_fn = extract("yt_return_ensure_inbound")
    ensure_stub = r'''
HAS_RETURN=0; PORT_FREE=1; WRITE_OK=1
jq() {
  case "$*" in
    *'.inbounds += '*)
      [ "$WRITE_OK" = 1 ] || return 1
      printf '%s\n' "$3" > "$CONFIG_DIR/jq-filter"
      echo '{"inbounds":[{"tag":"socks-yt-return","listen":"127.0.0.1","port":10823,"protocol":"socks"}]}' ;;
    *'length == 0'*) [ "$PORT_FREE" = 1 ] ;;
    *) [ "$HAS_RETURN" = 1 ] ;;
  esac
}
'''
    inb = Path(tmp) / "03_inbounds.json"
    for label, setup, success in [("новый вход", "", True),
                                  ("уже существует", "HAS_RETURN=1", False),
                                  ("порт занят", "PORT_FREE=0", False),
                                  ("ошибка записи jq", "WRITE_OK=0", False)]:
        original = '{"inbounds":[]}\n'
        inb.write_text(original, encoding="utf-8", newline="\n")
        out, _ = run(tail=ensure_fn + ensure_stub + setup + '\nyt_return_ensure_inbound || :\n', yt=False)
        check((inb.read_text(encoding="utf-8") != original) == success,
              label + ": корректно решается, менять ли исходный файл", out)
        check(not (Path(tmp) / "03_inbounds.json.tmp-yt-return").exists(), label + ": временный файл убран")

    # JSON-литерал добавления входа проверяется настоящим JSON-парсером, без jq на ПК.
    import json
    literal = re.search(r"'\.inbounds \+= (\[.*?\])'", ensure_fn, re.S).group(1)
    value = json.loads(literal.replace("$p", "10823"))[0]
    check(value["listen"] == "127.0.0.1" and value["settings"]["udp"] is False,
          "литерал нового входа — корректный JSON, только loopback, без UDP")

    route_marker = '# Проба возврата идёт на НАСТРОЕННЫЙ основной'
    route_block = SRC[SRC.index(route_marker):SRC.index('# v9: валидация JSON перед возвратом')]
    route_literal = re.search(r"\.routing.rules = (\[.*?\]) \+ \.routing.rules", route_block, re.S).group(1)
    value = json.loads(route_literal.replace("$t", json.dumps("основной")))[0]
    check(value == {"type": "field", "inboundTag": ["socks-yt-return"], "outboundTag": "основной"},
          "литерал маршрута — точный вход на основной, без замены маршрута клиентов")

print(f"test_yt_return_offline: OK ({checks} checks)", flush=True)
