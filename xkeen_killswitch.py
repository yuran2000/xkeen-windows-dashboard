"""Kill-switch политики XKeen — генератор netfilter.d-хука и разбор его состояния.

Зачем. При `xkeen -restart` (его делает и watchdog при переключении каналов) XKeen сначала
снимает перехват (clean_firewall), затем останавливает xray, и только после старта xray
ставит перехват обратно. В этом окне (секунды, иногда десятки секунд) устройства из политики
XKeen ходят в интернет НАПРЯМУЮ — с домашним IP. Для AI-сервисов это «RU-IP в аккаунте»,
для пользователя — незаметно.

Что делает хук. В цепочке FORWARD первым правилом стоит переход в свою цепочку `xkeen_ks`
для трафика клиентов политики XKeen (по connmark, который ставит сам Keenetic, а не XKeen —
поэтому метка живёт и когда XKeen лежит). Внутри — те же исключения, что у XKeen
(DNAT, DSCP 62, приватные сети, ipset'ы user/geo/ext_exclude), затем DROP новых TCP/UDP
соединений на перехватываемые порты. Пока XKeen жив, такой трафик до FORWARD не доходит
(REDIRECT/TPROXY забирают его раньше) — правило пустое. Когда XKeen лёг — правило режет.
Режим «наблюдение»: вместо DROP стоит RETURN, утечки только считаются.

Модуль не зависит от Flask и роутера: чистые функции, чтобы их можно было проверять офлайн.
"""
import re

HOOK_PATH = "/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh"
CHAIN = "xkeen_ks"
HOOK_VERSION = 1
CRON_LINE = f"*/1 * * * * {HOOK_PATH} >/dev/null 2>&1"
MODES = ("protect", "observe")
MANDATORY_PORTS = ("80", "443")
_MULTIPORT_MAX = 15  # лимит элементов у -m multiport

# Исключения XKeen (S05xkeen: ipv4_exclude / ipv6_exclude) — назначения, которые XKeen
# НЕ перехватывает. Kill-switch обязан пропускать их так же, иначе при живом XKeen
# законные прямые соединения клиентов политики попадут под DROP.
DEFAULT_NETS4 = ("0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 "
                 "172.16.0.0/12 192.168.0.0/16 224.0.0.0/4 255.255.255.255")
DEFAULT_NETS6 = "::/128 ::1/128 64:ff9b::/96 2001::/32 2002::/16 fd00::/8 ff00::/8 fe80::/10"

_MARK_RE = re.compile(r"^[0-9a-fA-F]{1,8}$")
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,15}$")
_PORT_CHUNK_RE = re.compile(r"^[0-9:,]+$")


def normalize_mark(mark):
    """'ffffaac' | '0xffffaac' → '0xffffaac'. None если это не hex-метка."""
    s = str(mark or "").strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if not _MARK_RE.match(s):
        return None
    return "0x" + s


def mark_to_decimal(mark):
    """Метка в десятичном виде — так её печатает /proc/net/nf_conntrack (mark=268434092)."""
    m = normalize_mark(mark)
    return int(m, 16) if m else None


def parse_policy(rci_policy, policy_name="xkeen"):
    """Из ответа RCI `show/ip/policy` ({"Policy2": {"description": "XKeen", "mark": "ffffaac",
    "table4": 4100, ...}, ...}) достать политику XKeen. Имя сравнивается без учёта регистра —
    так же ищет её сам XKeen. Возврат: {"id", "description", "mark", "table4"} или None."""
    if not isinstance(rci_policy, dict):
        return None
    want = (policy_name or "").strip().lower()
    for pid, body in rci_policy.items():
        if not isinstance(body, dict):
            continue
        desc = str(body.get("description") or "").strip()
        if desc.lower() != want:
            continue
        mark = normalize_mark(body.get("mark"))
        if not mark:
            continue
        try:
            table4 = int(body.get("table4")) if body.get("table4") is not None else None
        except (TypeError, ValueError):
            table4 = None
        return {"id": pid, "description": desc, "mark": mark, "table4": table4}
    return None


def parse_ports_lst(text):
    """Перехватываемые порты по логике XKeen (validate_and_clean_ports): содержимое
    /opt/etc/xkeen/port_proxying.lst + обязательные 80,443. Диапазоны `a-b`/`a:b` → `a:b`.
    Возврат: список чанков для `-m multiport --dports` (≤15 элементов в каждом), например
    ['80,443'] или ['80,443,8080,8443', '10000:10100']."""
    items = []
    for raw in (text or "").replace(",", "\n").splitlines():
        s = raw.split("#", 1)[0].strip().replace("-", ":")
        if not s:
            continue
        parts = s.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            continue
        if any(n < 1 or n > 65535 for n in nums):
            continue
        if len(nums) == 1:
            items.append((nums[0], nums[0]))
        elif len(nums) == 2:
            lo, hi = sorted(nums)
            items.append((lo, hi))
    for p in MANDATORY_PORTS:
        items.append((int(p), int(p)))
    # уникально и по возрастанию, как `sort -nu` у XKeen
    uniq = sorted(set(items))
    tokens = [str(lo) if lo == hi else f"{lo}:{hi}" for lo, hi in uniq]
    # диапазон занимает 2 «слота» multiport
    chunks, cur, weight = [], [], 0
    for t in tokens:
        w = 2 if ":" in t else 1
        if cur and weight + w > _MULTIPORT_MAX:
            chunks.append(",".join(cur))
            cur, weight = [], 0
        cur.append(t)
        weight += w
    if cur:
        chunks.append(",".join(cur))
    return chunks


def _check_params(mark, port_chunks, wan_if, mode):
    m = normalize_mark(mark)
    if not m:
        raise ValueError(f"некорректная метка политики: {mark!r}")
    if mode not in MODES:
        raise ValueError(f"режим должен быть один из {MODES}, получено {mode!r}")
    if not wan_if or not _IFACE_RE.match(wan_if):
        raise ValueError(f"некорректное имя WAN-интерфейса: {wan_if!r}")
    if not port_chunks or any(not _PORT_CHUNK_RE.match(c) for c in port_chunks):
        raise ValueError(f"некорректный список портов: {port_chunks!r}")
    return m


def build_hook(mark, port_chunks, wan_if, mode="protect", ipv6=True,
               nets4=DEFAULT_NETS4, nets6=DEFAULT_NETS6):
    """Текст хука /opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh (BusyBox ash).

    Параметры «запекаются» в шапку (KS_*) — панель сравнивает их с актуальными и
    перегенерирует хук при дрейфе. WAN-интерфейс и список WG-интерфейсов хук уточняет сам
    при каждом запуске (default-маршрут; файлы zz_wg_xray_<iface>.sh панели)."""
    m = _check_params(mark, port_chunks, wan_if, mode)
    ports = " ".join(port_chunks)
    lines = [
        "#!/bin/sh",
        "# xray-dashboard: kill-switch политики XKeen. Managed by xray-dashboard - панель перезапишет правки.",
        "# Пока XKeen жив - правила ничего не режут (перехват уводит трафик политики в xray до FORWARD).",
        "# Когда XKeen лёг (окно xkeen -restart / xkeen -stop) - новые соединения клиентов политики",
        "# по перехватываемым портам в WAN режутся, чтобы не уйти напрямую с домашним IP.",
        "# Режим observe: вместо DROP стоит RETURN, утечки только считаются панелью.",
        f"KS_VERSION={HOOK_VERSION}",
        f"KS_MODE={mode}",
        f"KS_MARK={m}",
        f'KS_PORTS="{ports}"',
        f"KS_WAN={wan_if}",
        f"KS_V6={1 if ipv6 else 0}",
        f"T={CHAIN}",
        f'NETS4="{nets4}"',
        f'NETS6="{nets6}"',
        "",
        "# WAN: default-маршрут (fallback - запечённый KS_WAN)",
        "wan=$(ip route show default 2>/dev/null | sed -n 's/.* dev \\([^ ]*\\).*/\\1/p' | head -1)",
        '[ -n "$wan" ] || wan=$KS_WAN',
        "# WG-интерфейсы, заведённые панелью в анти-DPI: метки политики они не несут, режем по интерфейсу",
        "wgs=$(ls /opt/etc/ndm/netfilter.d/zz_wg_xray_*.sh 2>/dev/null | sed -n 's#.*zz_wg_xray_\\([A-Za-z0-9]*\\)\\.sh#\\1#p' | tr '\\n' ' ')",
        "",
        "apply() {",
        "  I=$1; nets=$2; sfx=$3",
        "  fam=inet; [ -n \"$sfx\" ] && fam=inet6",
        "  # ipset'ы исключений XKeen - создаём теми же типами, что XKeen (иначе -m set не встанет,",
        "  # пока XKeen не стартовал; XKeen создаёт их с -exist и переживает наши ссылки).",
        "  for s in user_exclude geo_exclude geo_override; do ipset create ${s}${sfx} hash:net family $fam -exist >/dev/null 2>&1; done",
        "  ipset create ext_exclude${sfx} hash:ip family $fam -exist >/dev/null 2>&1",
        "  $I -w -t filter -N $T 2>/dev/null",
        "  sig=\"$KS_VERSION|$KS_MODE|$KS_MARK|$KS_PORTS|$nets\"",
        "  sigf=/tmp/xkeen_ks.sig.$I",
        "  have=$($I -w -t filter -S $T 2>/dev/null | grep -c '^-A ')",
        "  if [ \"$(cat $sigf 2>/dev/null)\" != \"$sig\" ] || [ \"$have\" -lt 8 ]; then",
        "    $I -w -t filter -F $T",
        "    $I -w -t filter -A $T -m conntrack --ctstate DNAT -j RETURN",
        "    $I -w -t filter -A $T -m dscp --dscp 62 -j RETURN",
        "    for n in $nets; do $I -w -t filter -A $T -d $n -j RETURN; done",
        "    $I -w -t filter -A $T -m set --match-set user_exclude${sfx} dst -j RETURN",
        "    $I -w -t filter -A $T -m set --match-set geo_exclude${sfx} dst -m set ! --match-set geo_override${sfx} dst -j RETURN",
        "    $I -w -t filter -A $T -m set --match-set ext_exclude${sfx} dst -j RETURN",
        "    act=DROP; [ \"$KS_MODE\" = protect ] || act=RETURN",
        "    for pc in $KS_PORTS; do",
        "      $I -w -t filter -A $T -p tcp -m multiport --dports $pc -m conntrack --ctstate NEW -m comment --comment ${T}_act -j $act",
        "      $I -w -t filter -A $T -p udp -m multiport --dports $pc -m conntrack --ctstate NEW -m comment --comment ${T}_act -j $act",
        "    done",
        "    echo \"$sig\" > $sigf",
        "  fi",
        "  # Переходы из FORWARD: убрать лишние (сменился WAN / убран WG-интерфейс), поставить нужные первыми",
        "  $I -w -t filter -S FORWARD 2>/dev/null | grep -- \"--comment $T \" | while read -r rule; do",
        "    keep=0",
        "    case \"$rule\" in *\"-o $wan \"*)",
        "      case \"$rule\" in",
        "        *\"-m connmark --mark $KS_MARK \"*) keep=1 ;;",
        "        *) for w in $wgs; do case \"$rule\" in *\"-i $w \"*) keep=1 ;; esac; done ;;",
        "      esac ;;",
        "    esac",
        "    [ \"$keep\" = 1 ] || $I -w -t filter $(echo \"$rule\" | sed 's/^-A /-D /')",
        "  done",
        "  first=$($I -w -t filter -S FORWARD 2>/dev/null | sed -n '2p')",
        "  case \"$first\" in *\"--comment $T \"*) ;; *)",
        "    # наши правила есть, но не первые (NDM пересобрал FORWARD) - переставить наверх",
        "    $I -w -t filter -S FORWARD 2>/dev/null | grep -- \"--comment $T \" | sed 's/^-A /-D /' | while read -r r; do $I -w -t filter $r; done ;;",
        "  esac",
        "  ens() {",
        "    n=$($I -w -t filter -S FORWARD 2>/dev/null | grep -cF -- \"-A FORWARD $* \")",
        "    while [ \"$n\" -gt 1 ]; do $I -w -t filter -D FORWARD \"$@\"; n=$((n-1)); done",
        "    $I -w -t filter -C FORWARD \"$@\" 2>/dev/null || $I -w -t filter -I FORWARD 1 \"$@\"",
        "  }",
        "  for w in $wgs; do ens -i $w -o $wan -m comment --comment $T -j $T; done",
        "  ens -o $wan -m connmark --mark $KS_MARK -m comment --comment $T -j $T",
        "}",
        "",
        "v6() { [ \"$KS_V6\" = 1 ] && command -v ip6tables >/dev/null 2>&1 && apply ip6tables \"$NETS6\" 6; }",
        "case \"$type\" in",
        "  ip6tables) v6 ;;",
        "  iptables)  apply iptables \"$NETS4\" \"\" ;;",
        "  *)         apply iptables \"$NETS4\" \"\"; v6 ;;",
        "esac",
        "exit 0",
        "",
    ]
    return "\n".join(lines)


_HEAD_RE = re.compile(r'^(KS_[A-Z0-9]+)=("?)(.*?)\2\s*$', re.M)


def parse_hook_params(hook_text):
    """Запечённые параметры из шапки хука: {'KS_VERSION': '1', 'KS_MODE': 'protect',
    'KS_MARK': '0xffffaac', 'KS_PORTS': '80,443', 'KS_WAN': 'ppp0', 'KS_V6': '1'}."""
    out = {}
    for m in _HEAD_RE.finditer(hook_text or ""):
        out[m.group(1)] = m.group(3)
    return out


def params_signature(mark, port_chunks, wan_if, mode, ipv6=True):
    """Сигнатура параметров для сравнения «что должно быть» с «что запечено в хуке»."""
    return {
        "KS_VERSION": str(HOOK_VERSION),
        "KS_MODE": mode,
        "KS_MARK": normalize_mark(mark) or "",
        "KS_PORTS": " ".join(port_chunks or []),
        "KS_WAN": wan_if or "",
        "KS_V6": "1" if ipv6 else "0",
    }


def install_command():
    """Команда роутеру для установки хука; тело хука (base64) подаётся через stdin.
    Заливка во временный файл → sh -n → подмена → chmod → первый прогон → cron-строка →
    маркер результата. При провале sh -n боевой хук не трогается."""
    p = HOOK_PATH
    return (
        f"mkdir -p $(dirname {p}) && base64 -d > {p}.new && sh -n {p}.new && "
        f"mv {p}.new {p} && chmod 755 {p} && sh {p}; "
        f"(crontab -l 2>/dev/null | grep -vF '{p}'; echo '{CRON_LINE}') | crontab -; "
        f"[ -x {p} ] && crontab -l 2>/dev/null | grep -qF '{p}' && echo KS_INSTALL_OK || {{ rm -f {p}.new; echo KS_INSTALL_FAIL; }}"
    )


def remove_command():
    """Снять kill-switch: файл, cron-строка, переходы из FORWARD, сама цепочка, сигнатуры."""
    p = HOOK_PATH
    steps = [
        f"rm -f {p} {p}.new",
        f"crontab -l 2>/dev/null | grep -vF '{p}' | crontab -",
        f"rm -f /tmp/xkeen_ks.sig.iptables /tmp/xkeen_ks.sig.ip6tables",
    ]
    for ipt in ("iptables", "ip6tables"):
        steps.append(
            f"command -v {ipt} >/dev/null 2>&1 && {{ "
            f"{ipt} -w -t filter -S FORWARD 2>/dev/null | grep -- '--comment {CHAIN} ' | sed 's/^-A /-D /' "
            f"| while read -r r; do {ipt} -w -t filter $r; done; "
            f"{ipt} -w -t filter -F {CHAIN} 2>/dev/null; {ipt} -w -t filter -X {CHAIN} 2>/dev/null; }}"
        )
    steps.append(f"[ -f {p} ] && echo KS_REMOVE_FAIL || echo KS_REMOVE_OK")
    return "; ".join(steps)


def status_script():
    """Скрипт для `sh` (через stdin): состояние хука и правил одним заходом, key=value построчно."""
    p = HOOK_PATH
    return f"""
echo hook_exists=$([ -f {p} ] && echo 1 || echo 0)
echo hook_exec=$([ -x {p} ] && echo 1 || echo 0)
echo cron=$(crontab -l 2>/dev/null | grep -cF '{p}')
echo wan=$(ip route show default 2>/dev/null | sed -n 's/.* dev \\([^ ]*\\).*/\\1/p' | head -1)
echo wgs=$(ls /opt/etc/ndm/netfilter.d/zz_wg_xray_*.sh 2>/dev/null | sed -n 's#.*zz_wg_xray_\\([A-Za-z0-9]*\\)\\.sh#\\1#p' | tr '\\n' ',')
for I in iptables ip6tables; do
  if command -v $I >/dev/null 2>&1; then
    echo ${{I}}_present=1
    echo ${{I}}_jumps=$($I -w -t filter -S FORWARD 2>/dev/null | grep -c -- "--comment {CHAIN} ")
    echo ${{I}}_first=$($I -w -t filter -S FORWARD 2>/dev/null | sed -n '2p' | grep -c -- "--comment {CHAIN} ")
    echo ${{I}}_chain_rules=$($I -w -t filter -S {CHAIN} 2>/dev/null | grep -c '^-A ')
    echo ${{I}}_act=$($I -w -t filter -S {CHAIN} 2>/dev/null | grep -- '--comment {CHAIN}_act ' | sed -n 's/.* -j \\([A-Z]*\\)$/\\1/p' | head -1)
  else
    echo ${{I}}_present=0
  fi
done
echo '---HOOK_HEAD---'
head -20 {p} 2>/dev/null
"""


def parse_status(output):
    """Разбор вывода status_script → dict с числами/строками + запечённые параметры хука."""
    head, _, hook_head = (output or "").partition("---HOOK_HEAD---")
    st = {}
    for ln in head.splitlines():
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip()
        if v.isdigit():
            st[k] = int(v)
        else:
            st[k] = v
    st["hook_params"] = parse_hook_params(hook_head)
    st["wgs"] = [w for w in str(st.get("wgs", "")).split(",") if w]
    return st


def leaks_script(mark, port_chunks, wan_ip=None):
    """Скрипт для `sh` (через stdin): сколько соединений клиентов политики сейчас идут в WAN
    напрямую, минуя xray. Признак: у потока метка политики, порт назначения из перехватываемых,
    и ответ приходит с реального порта сервера (а не с порта xray после REDIRECT/TPROXY).
    Не считаются назначения, которые XKeen (и kill-switch) не перехватывают: приватные/спец-сети
    и адрес WAN самого роутера — иначе счётчик показывал бы законные прямые соединения.
    Только IPv4. Печатает `count=N`, затем до 8 строк `dst dport proto state`."""
    dec = mark_to_decimal(mark)
    if dec is None:
        raise ValueError("некорректная метка")
    # раскрыть чанки портов в awk-условие
    conds = []
    for chunk in port_chunks or []:
        for tok in chunk.split(","):
            if ":" in tok:
                lo, hi = tok.split(":", 1)
                conds.append(f"(dp>={int(lo)} && dp<={int(hi)})")
            elif tok:
                conds.append(f"dp=={int(tok)}")
    cond = " || ".join(conds) or "0"
    wan = wan_ip if wan_ip and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", wan_ip) else ""
    return f"""
awk -v M={dec} -v WAN="{wan}" '
  function excluded(ip,   o) {{
    split(ip, o, ".")
    if (o[1]==10 || o[1]==127 || o[1]==0 || o[1]>=224) return 1
    if (o[1]==169 && o[2]==254) return 1
    if (o[1]==172 && o[2]>=16 && o[2]<=31) return 1
    if (o[1]==192 && o[2]==168) return 1
    if (o[1]==100 && o[2]>=64 && o[2]<=127) return 1
    if (WAN != "" && ip == WAN) return 1
    return 0
  }}
  $1=="ipv4" && $0 ~ ("mark=" M " ") {{
    dp=0; sp2=0; dst=""; st=""; proto=$3; np=0; nsp=0
    for (i=1;i<=NF;i++) {{
      if ($i ~ /^dport=/) {{ np++; if (np==1) dp=substr($i,7) }}
      if ($i ~ /^sport=/) {{ nsp++; if (nsp==2) sp2=substr($i,7) }}
      if ($i ~ /^dst=/ && dst=="") dst=substr($i,5)
      if ($i ~ /^[A-Z_]+$/ && st=="") st=$i
    }}
    if (({cond}) && sp2==dp && !excluded(dst)) {{ n++; if (n<=8) print dst, dp, proto, st }}
  }}
  END {{ print "count=" n+0 }}
' /proc/net/nf_conntrack 2>/dev/null
"""


def parse_leaks(output):
    """→ {'count': N, 'samples': ['1.2.3.4 443 tcp ESTABLISHED', ...]}"""
    count, samples = 0, []
    for ln in (output or "").splitlines():
        ln = ln.strip()
        if ln.startswith("count="):
            try:
                count = int(ln[6:])
            except ValueError:
                count = 0
        elif ln:
            samples.append(ln)
    return {"count": count, "samples": samples[:8]}
