# -*- coding: utf-8 -*-
"""WireGuard-доступы на Keenetic: выдача в один клик, правила (access-list), NAT, подмена адреса
WG -> ЛВС, отзыв с удалением, инвентарь подключений роутера. Без Flask.

Связь с роутером — функция ssh(cmd, stdin_data=None, timeout=25) -> (rc, stdout, stderr), которую
задаёт панель через configure(). Настройки — объект Settings в S (панель собирает его из config_local
и того, что прочитала с роутера). Реестр выданного — JSON на ПК (приватные ключи не хранятся).

Топология Keenetic point-to-point: каждому доступу свой интерфейс WireguardN и своя /24
(<prefix>.X.0/24: роутер .1, клиент .2). Источник истины — роутер: доступ ищется по ключу клиента
среди пиров, не по имени.
"""
import base64
import datetime
import ipaddress
import json
import os
import re
import shutil
import socket
import threading
import time
import uuid as uuidlib


class Settings:
    """Параметры выдачи. Пустой endpoint_host — WAN-адрес роутера (интерфейс с defaultgw)."""

    def __init__(self, **kw):
        self.lan_subnet = "192.168.1.0/24"   # ЛВС роутера
        self.lan_if = "br0"                  # её интерфейс (для подмены адреса WG -> ЛВС)
        self.prefix = "172.173"              # подсети доступов: <prefix>.X.0/24
        self.subnet_start = 1                # с какого X искать свободную подсеть
        self.port_range = (48001, 48099)     # listen-port доступов
        self.max_ifaces = 48                 # потолок WG-интерфейсов Keenetic (индексы 0..47)
        self.block_extra = []                # сети, закрытые клиентам с интернетом сверх частных
        self.dns = "9.9.9.9"                 # DNS в клиентском .conf
        self.endpoint_host = ""              # белый IP / DDNS роутера; пусто — WAN-адрес
        self.mtu = 1324                      # PPPoE + WG
        self.services = []                   # пресеты доступа в ЛВС: [{id, label, ips:[...], ports}]
        self.registry_path = "wg_clients.json"
        for k, v in kw.items():
            setattr(self, k, v)


S = Settings()
_SSH = None


def configure(ssh_fn=None, settings=None):
    global _SSH, S
    if ssh_fn is not None:
        _SSH = ssh_fn
    if settings is not None:
        S = settings


def ssh(cmd, stdin_data=None, timeout=25):
    if _SSH is None:
        return 255, "", "SSH к роутеру не настроен"
    return _SSH(cmd, stdin_data=stdin_data, timeout=timeout)


# ---------------- RCI ----------------

def _rci_json(path, timeout=20):
    """GET RCI -> разобранный JSON или None (сбой чтения ≠ «пусто»). path начинается с /rci/."""
    rc, out, err = ssh(f"curl -s http://localhost:79{path}", timeout=timeout)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def _rci_post(obj, timeout=30):
    """POST JSON в RCI. Тело — через stdin (base64): не светится в командной строке (там бывает
    приватный ключ), не ломается кавычками, кириллица имени не идёт в argv. -> (rc, out, err)."""
    b64 = base64.b64encode(json.dumps(obj).encode("utf-8"))
    return ssh("base64 -d | curl -s -X POST http://localhost:79/rci/ "
               "-H 'Content-Type: application/json' -d @-", stdin_data=b64, timeout=timeout)


def _rci_errors(out, expected=None):
    """Ошибки из ответа RCI на parse-массив. Пустой/чужой формы ответ — тоже ошибка."""
    if not (out or "").strip():
        return ["пустой ответ RCI"]
    try:
        j = json.loads(out)
    except Exception:
        return ["не разобран ответ RCI: " + out[:200]]
    items = j if isinstance(j, list) else [j]
    if expected is not None and len(items) != expected:
        return [f"RCI ответил на {len(items)} команд из {expected}"]
    errs = []
    for el in items:
        if not isinstance(el, dict):
            errs.append("неожиданный ответ RCI")
            continue
        node = el.get("parse", el)
        for st in ((node or {}).get("status") or []):
            if isinstance(st, dict) and st.get("status") == "error":
                errs.append(st.get("message", "ошибка"))
    return errs


def _rci_run(cmds, label):
    """CLI-команды одним parse-массивом (контекст держится). -> None или текст ошибки."""
    rc, out, err = _rci_post([{"parse": c} for c in cmds])
    if rc != 0:
        return f"{label}: RCI недоступен ({(err or out).strip()[:200]})"
    e = _rci_errors(out, len(cmds))
    return (f"{label}: " + "; ".join(e[:3])) if e else None


def _iface_exists(iface):
    """True/False — есть ли интерфейс; None — проверить не удалось."""
    rc, out, err = ssh("curl -s -o /dev/null -w %{http_code} http://localhost:79/rci/show/interface/" + iface)
    code = out.strip()
    if rc != 0:
        return None
    return True if code == "200" else (False if code == "404" else None)


def _save():
    return _rci_run(["system configuration save"], "сохранение")


# ---------------- Реестр выданного ----------------

# Один замок на все изменения реестра и роутера: операции редкие, а сериализация убирает гонки
# «снимок реестра поверх свежего» и двойную выдачу слота.
_OP_LOCK = threading.RLock()


class RegistryError(Exception):
    pass


def _busy_retry(fn):
    """Windows: os.replace падает WinError 5, пока файл открыт на чтение. Это «занят», а не
    «повреждён»: 10 повторов по 50 мс."""
    for i in range(10):
        try:
            return fn()
        except PermissionError:
            if i == 9:
                raise
            time.sleep(0.05)


def _json_read(path):
    """{} если файла нет; RegistryError если файл есть, но битый (такой НЕ затирается)."""
    def load():
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    try:
        d = _busy_retry(load)
    except FileNotFoundError:
        return {}
    except PermissionError as e:
        raise RegistryError(f"{os.path.basename(path)} занят другим процессом (не повреждён), повторите: {e}")
    except Exception as e:
        raise RegistryError(f"{os.path.basename(path)} повреждён: {e}")
    if not isinstance(d, dict):
        raise RegistryError(f"{os.path.basename(path)}: ожидался объект JSON")
    return d


def _json_write(path, d):
    """Атомарно: временный файл -> fsync -> копия старого в .bak -> replace."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        _busy_retry(lambda: shutil.copy2(path, path + ".bak"))
    _busy_retry(lambda: os.replace(tmp, path))


def registry_read():
    return _json_read(S.registry_path)


def registry_write(d):
    _json_write(S.registry_path, d)


def _mark_dirty(reg, cid):
    """Метка «панель шлёт роутеру команды, save ещё не прошёл» — в реестр ДО первой команды.
    По ней повторный отзыв знает, что сохранять надо своё; без метки save не делается, чтобы не
    закрепить чужое несохранённое. -> None или текст ошибки."""
    reg[cid]["router_dirty"] = True
    try:
        registry_write(reg)
    except Exception as e:
        return f"не записана метка в реестр ({e}) — к роутеру не обращались"
    return None


def _clear_dirty(reg):
    """Успешный save сохранил всё — метки снимаются у всех записей (реестр пишет вызывающий)."""
    for c in reg.values():
        c.pop("router_dirty", None)


# ---------------- Ключи: X25519 на чистом Python ----------------

_WG_P = 2 ** 255 - 19


def _wg_cswap(swap, a, b):
    dummy = swap * ((a - b) % _WG_P)
    return (a - dummy) % _WG_P, (b + dummy) % _WG_P


def x25519(k_int, u_int):
    """Скалярное умножение X25519 (RFC 7748, эталонная реализация)."""
    x1 = u_int
    x2, z2, x3, z3 = 1, 0, u_int, 1
    swap = 0
    for t in reversed(range(255)):
        k_t = (k_int >> t) & 1
        swap ^= k_t
        x2, x3 = _wg_cswap(swap, x2, x3)
        z2, z3 = _wg_cswap(swap, z2, z3)
        swap = k_t
        A = (x2 + z2) % _WG_P
        AA = A * A % _WG_P
        B = (x2 - z2) % _WG_P
        BB = B * B % _WG_P
        E = (AA - BB) % _WG_P
        C = (x3 + z3) % _WG_P
        D = (x3 - z3) % _WG_P
        DA = D * A % _WG_P
        CB = C * B % _WG_P
        x3 = ((DA + CB) % _WG_P) ** 2 % _WG_P
        z3 = x1 * (((DA - CB) % _WG_P) ** 2 % _WG_P) % _WG_P
        x2 = AA * BB % _WG_P
        z2 = E * ((AA + (121665 * E)) % _WG_P) % _WG_P
    x2, x3 = _wg_cswap(swap, x2, x3)
    z2, z3 = _wg_cswap(swap, z2, z3)
    return (x2 * pow(z2, _WG_P - 2, _WG_P)) % _WG_P


def genkey():
    """Пара ключей WireGuard (base64) -> (private, public). Побитно как `wg genkey | wg pubkey`."""
    try:
        priv = bytearray(os.urandom(32))
        priv[0] &= 248
        priv[31] &= 127
        priv[31] |= 64
        pub = x25519(int.from_bytes(priv, "little"), 9).to_bytes(32, "little")
        return base64.b64encode(bytes(priv)).decode("ascii"), base64.b64encode(pub).decode("ascii")
    except Exception:
        return None, None


# ---------------- Сети ----------------

def _pref():
    return S.prefix


def _lan():
    return ipaddress.ip_network(S.lan_subnet, strict=False)


def _octet3(addr):
    """'<prefix>.5.1' -> 5, если адрес из префикса доступов, иначе None."""
    pref = _pref()
    if addr and addr.startswith(pref + ".") and addr.count(".") == 3:
        try:
            return int(addr.split(".")[2])
        except Exception:
            return None
    return None


def _listen_port(wg):
    lp = (wg or {}).get("listen-port")
    port = lp.get("port") if isinstance(lp, dict) else lp
    return port if isinstance(port, int) else None


def _mask2cidr(mask):
    try:
        return ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen
    except Exception:
        return None


_WAN = {"host": ""}   # WAN-адрес роутера из последнего чтения show/interface


def _note_wan(show_ifaces):
    for v in (show_ifaces or {}).values():
        if isinstance(v, dict) and v.get("defaultgw") is True and v.get("address"):
            _WAN["host"] = v["address"]
            return


def endpoint_host():
    return (S.endpoint_host or "").strip() or _WAN["host"]


def _ensure_wan():
    if not endpoint_host():
        j = _rci_json("/rci/show/interface")
        if isinstance(j, dict):
            _note_wan(j)


def _endpoint_ip():
    """IPv4 роутера для клиента: сам адрес или результат резолва домена. None — не определён."""
    h = endpoint_host()
    if not h:
        return None
    try:
        return str(ipaddress.IPv4Address(h))
    except Exception:
        pass
    try:
        return socket.gethostbyname(h)
    except Exception:
        return None


def endpoint_warning():
    """Текст предупреждения, если endpoint пуст или серый (снаружи недостижим)."""
    h = endpoint_host()
    if not h:
        return "WAN-адрес роутера не определён — задайте WG_ENDPOINT_HOST (белый IP или DDNS) в config_local.py."
    try:
        a = ipaddress.ip_address(h)
        grey = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16", "127.0.0.0/8")
        if any(a in ipaddress.ip_network(n) for n in grey):
            return (f"Endpoint {h} — частный/CGNAT-адрес, снаружи он недостижим. Задайте WG_ENDPOINT_HOST "
                    f"(белый IP или DDNS) в config_local.py.")
    except ValueError:
        pass
    return ""


# ---------------- Скан роутера и свободный слот ----------------

XRAY_HOOK_GLOB = "/opt/etc/ndm/netfilter.d/zz_wg_xray_nwg*.sh"


def xray_hooks():
    """Хуки заворота в xray: {индекс nwg: подсеть или ""}. None — не прочитаны."""
    rc, out, err = ssh(f"for f in {XRAY_HOOK_GLOB}; do [ -f \"$f\" ] || continue; "
                       "echo \"$f $(sed -n 's/^SUBNET=//p' \"$f\" | head -1)\"; done; echo END")
    if rc != 0 or "END" not in out:
        return None
    hooks = {}
    for line in out.splitlines():
        m = re.search(r"zz_wg_xray_nwg(\d+)\.sh\s*(\S*)", line)
        if m:
            hooks[int(m.group(1))] = m.group(2)
    return hooks


def _s2s_nets(records):
    """client_pub -> удалённая ЛВС активных site-to-site записей реестра."""
    out = {}
    for c in records:
        if c.get("status") == "revoked" or not c.get("s2s") or not c.get("remote_lan"):
            continue
        try:
            out[c.get("client_pub")] = ipaddress.ip_network(c["remote_lan"])
        except ValueError:
            pass
    return out


def _reg_s2s():
    """_s2s_nets по реестру. Битый реестр -> пусто (свои s2s станут линками — не тронем)."""
    try:
        return _s2s_nets(registry_read().values())
    except RegistryError:
        return {}


def _is_link(cprs, addr, s2s):
    """Чужой межсайтовый туннель, который панель не трогает: у пира есть endpoint (роутер сам
    дозванивается) ИЛИ в allow-ips есть сеть, кроме своей туннельной /24 и ЛВС (туннель, куда
    дозванивается удалённый роутер, — у него endpoint нет). Исключение — site-to-site, выданный
    панелью: пиру записи разрешена ещё и её remote_lan, и только она. Выданное панелью и доработанное
    руками (добавили сеть) — линк: «отозвать» снесло бы чужую связь."""
    if any(p.get("endpoint") for p in cprs):
        return True
    own = [_lan()]
    if addr:
        own.append(ipaddress.ip_interface(f"{addr}/24").network)
    for p in cprs:
        mine = own + ([s2s[p["key"]]] if p.get("key") in s2s else [])
        for a in (p.get("allow-ips") or []):
            try:
                net = ipaddress.ip_network(f"{a.get('address')}/{a.get('mask')}", strict=False)
                if not any(net.subnet_of(o) for o in mine):
                    return True
            except (ValueError, TypeError):
                return True                     # не разобрано (или IPv6) — считаем чужим, не трогаем
    return False


def scan_router(timeout=20):
    """Живой роутер (только чтение) -> интерфейсы (индекс, имя, подсеть, порт, ключи пиров, линк ли),
    занятые подсети и порты. Fail-closed: /rci/show/interface (полный список, но у выключенного
    интерфейса нет адреса), /rci/interface (конфиг: адреса выключенных, endpoint пиров) и хуки xray
    должны прочитаться — иначе подсеть/индекс выдались бы повторно."""
    empty = {"subnets": set(), "ports": set(), "ifaces": [], "hooks": {}}
    j = _rci_json("/rci/show/interface", timeout)
    jc = _rci_json("/rci/interface", timeout)
    hooks = xray_hooks()
    if not isinstance(j, dict) or not isinstance(jc, dict) or hooks is None:
        return {"ok": False, "err": "роутер не ответил (RCI/SSH)", **empty}
    _note_wan(j)
    state = {"ok": True, "err": "", **empty, "hooks": hooks}
    s2s = _reg_s2s()
    for k, v in j.items():
        m = re.match(r"Wireguard(\d+)$", k, re.I)
        if not m or not isinstance(v, dict):
            continue
        cv = jc.get(k) or {}
        caddr = ((cv.get("ip") or {}).get("address") or {}).get("address", "")
        addr = v.get("address", "") or caddr or ""
        sub = _octet3(addr)
        if sub is not None:
            state["subnets"].add(sub)
        wg = v.get("wireguard") or {}
        port = _listen_port(wg) or _listen_port(cv.get("wireguard"))
        if port:
            state["ports"].add(port)
        cprs = (cv.get("wireguard") or {}).get("peer") or []
        state["ifaces"].append({
            "name": k, "n": int(m.group(1)), "index": v.get("index"),
            "desc": (v.get("description", "") or "").strip(), "addr": addr, "subnet": sub, "port": port,
            "peers": [p.get("public-key", "") for p in (wg.get("peer") or [])],
            "is_link": _is_link(cprs, addr, s2s),
        })
    for idx, subnet in hooks.items():           # подсети под хуками xray — тоже заняты
        sub = _octet3(subnet.split("/")[0]) if subnet else None
        if sub is not None:
            state["subnets"].add(sub)
    return state


def iface_by_pub(state, client_pub):
    """Интерфейс роутера, среди пиров которого есть ключ клиента (надёжнее имени: при ручной
    загрузке файла имя берётся из имени файла)."""
    if not client_pub:
        return None
    return next((f for f in state.get("ifaces", []) if client_pub in f.get("peers", [])), None)


def free_slot(state):
    """Свободные: индекс интерфейса, октет X подсети (<prefix>.X.0/24), listen-port.
    Индекс — низший свободный в 0..MAX-1, не занятый и не под хуком xray. Кончилось что-то -> full."""
    max_if = S.max_ifaces
    used_idx = {f["n"] for f in state.get("ifaces", [])} | set(state.get("hooks", {}))
    n = next((i for i in range(max_if) if i not in used_idx), None)
    used_sub = state.get("subnets", set())
    x = next((i for i in range(S.subnet_start, 255) if i not in used_sub), None)
    lo, hi = S.port_range
    used_ports = state.get("ports", set())
    port = next((p for p in range(lo, hi + 1) if p not in used_ports), None)
    why = [w for w, bad in (("индексы WireGuard", n is None), ("подсети", x is None),
                            ("порты", port is None)) if bad]
    info = {"sub_x": x, "port": port, "used": len({f["n"] for f in state.get("ifaces", [])}),
            "max": max_if, "full": bool(why), "why": ", ".join(why)}
    if n is None:
        info.update({"iface": None, "n": None, "acl": None})
    else:
        info.update({"iface": f"Wireguard{n}", "n": n, "acl": f"_WEBADMIN_Wireguard{n}"})
    return info


# ---------------- Доступы (гранты) и правила ----------------

# «Весь интернет» в AllowedIPs клиента: 0.0.0.0/0 МИНУС RFC1918. Литеральный 0.0.0.0/0 на Windows
# включает kill-switch WireGuard -> клиент теряет свою локалку.
SPLIT_ALLOWEDIPS = [
    "0.0.0.0/5", "8.0.0.0/7", "11.0.0.0/8", "12.0.0.0/6", "16.0.0.0/4", "32.0.0.0/3",
    "64.0.0.0/2", "128.0.0.0/3", "160.0.0.0/5", "168.0.0.0/6", "172.0.0.0/12", "172.32.0.0/11",
    "172.64.0.0/10", "172.128.0.0/9", "173.0.0.0/8", "174.0.0.0/7", "176.0.0.0/4", "192.0.0.0/9",
    "192.128.0.0/11", "192.160.0.0/13", "192.169.0.0/16", "192.170.0.0/15", "192.172.0.0/14",
    "192.176.0.0/12", "192.192.0.0/10", "193.0.0.0/8", "194.0.0.0/7", "196.0.0.0/6",
    "200.0.0.0/5", "208.0.0.0/4",
]

# Как доступ выходит в интернет: direct — штатный NAT Keenetic (ip nat) с адреса роутера;
# xray — заворот в XKeen (хук zz_wg_xray_nwgN.sh); "" — интернета нет, только ЛВС.
EXIT_MODES = {"direct": "🌐 Интернет напрямую через роутер", "xray": "🛡 Интернет через xray (анти-DPI)",
              "": "🏠 Только домашняя сеть"}

# Куда клиенту с интернетом нельзя (если явно не разрешено в доступе к ЛВС): частные сети, CGNAT,
# link-local и все туннели роутера. Сюда же сам роутер (свой .1 и внешний IP) — см. acl_lines.
BLOCK_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"]

# Порты управления роутером, закрытые на «своём» .1 в режиме xray (сам .1 там открыт: на него
# хук xray перенаправляет трафик и DNS).
ROUTER_ADMIN_PORTS = [21, 22, 23, 79, 80, 222, 443, 8080]


def _block_nets():
    nets = BLOCK_NETS + [f"{_pref()}.0.0/16"] + list(S.block_extra or [])
    ep = _endpoint_ip()
    if ep:
        nets.append(f"{ep}/32")
    return nets


def _parse_ports(s, where):
    """'139,445,1560-1591' -> [('eq',139),('eq',445),('range',1560,1591)]. Ошибка -> ValueError."""
    toks = []
    for part in re.split(r"[,\s]+", (s or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", part)
        if not m:
            raise ValueError(f"{where}: порт «{part}» — нужно число или диапазон A-B")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else None
        if not 1 <= a <= 65535 or (b is not None and not a < b <= 65535):
            raise ValueError(f"{where}: порт «{part}» вне 1..65535 или диапазон не по возрастанию")
        toks.append(("range", a, b) if b else ("eq", a))
    return toks


def collect_grants(selected_ids, full, extra_text=""):
    """Форма -> (grants, allowed). grants — для access-list: [{net, protos, ports, label}];
    ports=[] значит «все порты». allowed — сети для AllowedIPs клиента.
    Невалидный ввод -> ValueError ДО обращения к роутеру."""
    lan = _lan()
    if full:
        return ([{"net": lan, "protos": ["tcp", "udp"], "ports": [], "label": f"вся ЛВС {lan}"}],
                [str(lan)])
    grants = []
    for s in (S.services or []):
        if s.get("id") in selected_ids:
            toks = _parse_ports(s.get("ports", ""), s.get("label", s.get("id")))
            for ipc in s.get("ips", []):
                grants.append({"net": ipaddress.ip_network(ipc, strict=False), "protos": ["tcp"],
                               "ports": toks, "label": s.get("label", "")})
    for line in (extra_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        ip, ports = (line.split(":", 1) if ":" in line else (line, ""))
        try:
            net = ipaddress.ip_network(ip.strip(), strict=False)
        except ValueError:
            raise ValueError(f"«{line}»: не IPv4-адрес/сеть")
        if net.version != 4:
            raise ValueError(f"«{line}»: нужен IPv4")
        toks = _parse_ports(ports, f"«{line}»")
        # без портов — весь хост/сеть по tcp и udp
        grants.append({"net": net, "protos": ["tcp", "udp"] if not toks else ["tcp"],
                       "ports": toks, "label": "вручную"})
    allowed = []
    for g in grants:
        if str(g["net"]) not in allowed:
            allowed.append(str(g["net"]))
    return grants, allowed


def _grants_touch_lan(grants):
    lan = _lan()
    return any(g["net"].overlaps(lan) for g in grants)


def acl_lines(grants, exit_mode, own_ip=None):
    """CLI-строки access-list. Порядок важен (первое совпадение):
    1) разрешённое в ЛВС (+ пинг этих хостов);
    2) [xray] свой .1: закрыть порты управления, открыть остальное (цель перенаправления xray/DNS);
    3) [интернет] запрет частных сетей, туннелей и самого роутера;
    4) [интернет] всё остальное — можно. Без интернета — неявный запрет в конце списка."""
    out = []

    def add(line):
        if line not in out:          # дубль Keenetic может схлопнуть -> сбил бы проверку числа правил
            out.append(line)

    for g in grants:
        net, mask = str(g["net"].network_address), str(g["net"].netmask)
        for proto in g["protos"]:
            if not g["ports"]:
                add(f"permit {proto} 0.0.0.0 0.0.0.0 {net} {mask}")
            for tok in g["ports"]:
                if tok[0] == "range":
                    add(f"permit {proto} 0.0.0.0 0.0.0.0 {net} {mask} port range {tok[1]} {tok[2]}")
                else:
                    add(f"permit {proto} 0.0.0.0 0.0.0.0 {net} {mask} port eq {tok[1]}")
        add(f"permit icmp 0.0.0.0 0.0.0.0 {net} {mask}")
    if exit_mode == "xray" and own_ip:
        for p in ROUTER_ADMIN_PORTS:
            add(f"deny tcp 0.0.0.0 0.0.0.0 {own_ip} 255.255.255.255 port eq {p}")
        add(f"permit tcp 0.0.0.0 0.0.0.0 {own_ip} 255.255.255.255")
        add(f"permit udp 0.0.0.0 0.0.0.0 {own_ip} 255.255.255.255")
    if exit_mode:
        for n in _block_nets():
            nn = ipaddress.ip_network(n, strict=False)
            for proto in ("tcp", "udp", "icmp"):
                add(f"deny {proto} 0.0.0.0 0.0.0.0 {nn.network_address} {nn.netmask}")
        for proto in ("tcp", "udp", "icmp"):
            add(f"permit {proto} 0.0.0.0 0.0.0.0 0.0.0.0 0.0.0.0")
    return out


def access_list_text(acl_name, lines):
    return "\n".join([f"access-list {acl_name}"] + [f"    {ln}" for ln in lines] + ["    auto-delete"])


# ---------------- .conf ----------------

def _ula(sub_x):
    """IPv6-заглушка: ULA-адрес клиента + его /64 (из <prefix>.X.2)."""
    o = f"{_pref()}.{sub_x}.2".split(".")
    return f"fd00:{o[1]}:{o[2]}::{o[3]}", f"fd00:{o[1]}:{o[2]}::/64"


def internet_allowed():
    """«Весь интернет» для AllowedIPs МИНУС адрес самого роутера: иначе на macOS/iOS/Linux пакеты
    к серверу уходят в ещё не поднятый туннель (петля), и он не поднимается."""
    nets = [ipaddress.ip_network(a) for a in SPLIT_ALLOWEDIPS]
    ep = _endpoint_ip()
    if ep:
        epn = ipaddress.ip_network(f"{ep}/32")
        res = []
        for n in nets:
            res.extend(n.address_exclude(epn) if epn.subnet_of(n) else [n])
        nets = list(ipaddress.collapse_addresses(res))
    return [str(n) for n in nets]


def client_conf(client_priv, server_pub, sub_x, allowed, endpoint, dns, internet=False):
    """Клиентский .conf. AllowedIPs = туннель + разрешённое в ЛВС (+ весь интернет без адреса
    роутера и IPv6-заглушка при internet). dns пустой -> без строки DNS (роутер-клиент)."""
    pref = _pref()
    tunnel = f"{pref}.{sub_x}.0/24"
    ips = [tunnel] + [a for a in allowed if a != tunnel]
    addr = f"{pref}.{sub_x}.2/32"
    if internet:
        ips += [a for a in internet_allowed() if a not in ips]
        # IPv6 — в туннель (там он никуда не ведёт): иначе на двухстековой сети v6 шёл бы мимо
        ula, ula_net = _ula(sub_x)
        addr += f", {ula}/64"
        ips += [ula_net, "2000::/3"]
    lines = ["[Interface]", f"PrivateKey = {client_priv}", f"Address = {addr}"]
    if dns:
        lines.append(f"DNS = {dns}")
    lines.append(f"MTU = {S.mtu}")
    lines += ["", "[Peer]", f"PublicKey = {server_pub}", f"AllowedIPs = {', '.join(ips)}",
              f"Endpoint = {endpoint}", "PersistentKeepalive = 25", ""]
    return "\n".join(lines)


def _peer_allow(sub_x, remote_net=None):
    nets = [ipaddress.ip_network(f"{_pref()}.{sub_x}.0/24"), _lan()]
    if remote_net:
        nets.append(remote_net)
    return nets


def router_conf(slot, desc, server_priv, client_pub, remote_net=None):
    """Серверный .conf для Keenetic «Загрузить из файла» (ручной путь)."""
    pref = _pref()
    x = slot["sub_x"]
    return "\n".join([
        f"# WireGuard server side: {desc}",
        "# Keenetic: Internet -> Other connections -> WireGuard -> Import from file.",
        "[Interface]",
        f"PrivateKey = {server_priv}",
        f"Address = {pref}.{x}.1/24",
        f"ListenPort = {slot['port']}",
        "",
        "[Peer]",
        f"# {desc}",
        f"PublicKey = {client_pub}",
        f"AllowedIPs = {', '.join(str(n) for n in _peer_allow(x, remote_net))}",
        "",
    ])


def router_block_text(slot, desc, server_priv, client_pub, lines, exit_mode="", remote_net=None):
    """Справочный блок CLI (интерфейс + пир + access-list + NAT + маршрут s2s)."""
    pref = _pref()
    x = slot["sub_x"]
    iface = [
        f"interface {slot['iface']}",
        f'    description "{desc}"',
        "    security-level public",
        f"    ip address {pref}.{x}.1 255.255.255.0",
        f"    ip access-group {slot['acl']} in",
        "    ip tcp adjust-mss pmtu",
        f"    wireguard private-key {server_priv}",
        f"    wireguard listen-port {slot['port']}",
        f"    wireguard peer {client_pub} !{desc}",
        "        keepalive-interval 30",
    ]
    iface += [f"        allow-ips {n.network_address} {n.netmask}" for n in _peer_allow(x, remote_net)]
    iface += ["        connect", "    up"]
    text = "\n".join(iface) + "\n\n" + access_list_text(slot["acl"], lines)
    if exit_mode == "direct":
        text += f"\n\nip nat {slot['iface']}"
    if remote_net:
        text += f"\n\nip route {remote_net.network_address} {remote_net.netmask} {slot['iface']} auto"
    return text


def _translit(s):
    t = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
         "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
         "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
         "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    return "".join((t.get(c.lower(), c).capitalize() if c.isupper() else t.get(c, c)) for c in s)


def client_fname(name, fallback="wg"):
    """Имя клиентского .conf = имя туннеля в WireGuard для Windows: только [A-Za-z0-9_=+.-],
    до 32 символов (иначе «Tunnel name is not valid»)."""
    base = re.sub(r"[^A-Za-z0-9_=+.-]+", "_", _translit(name)).strip("_.") or fallback
    return base[:29] + "-wg.conf"


def router_fname(name):
    """Имя серверного .conf: Keenetic называет подключение по имени файла (кириллица допустима)."""
    fn = re.sub(r"[\\/:*?\"<>|']+", "", name).strip()
    return (re.sub(r"\s+", "_", fn) or "wg") + ".conf"


# ---------------- access-list и NAT через RCI ----------------

def _acl_raw_rules(acl):
    """Правила access-list. None — не прочитаны (≠ «правил нет»)."""
    j = _rci_json("/rci/access-list")
    if not isinstance(j, list):
        return None
    # запись без action — служебная пометка {"acl", "auto-delete": true}, не правило
    return [r for r in j if isinstance(r, dict) and r.get("acl") == acl and r.get("action")]


def iface_acl_rules(iface):
    """Текущие правила access-list интерфейса, человекочитаемо. None — не прочитаны."""
    raw = _acl_raw_rules(f"_WEBADMIN_{iface}")
    if raw is None:
        return None
    rules = []
    for r in raw:
        dst = r.get("destination") or ""
        bits = _mask2cidr(r.get("destination-mask", ""))
        dst = "любой" if dst in ("", "0.0.0.0") and bits in (0, None) else (
            dst if bits in (32, None) else f"{dst}/{bits}")
        op = r.get("dst-port-operator", "")
        port = r.get("destination-port", "")
        portstr = ""
        if port:
            end = r.get("destination-end-port", "")
            portstr = (":" + str(port)) if op != "range" else f" порты {port}-{end}" if end else f" порты {port}"
        off = " (выкл)" if r.get("disable") else ""
        rules.append(f"{r.get('action', 'permit')} {r.get('protocol') or 'ip'} → {dst}{portstr}{off}".strip())
    return rules


def nat_ifaces():
    """Интерфейсы со штатным NAT Keenetic (ip nat) -> set имён. None при ошибке чтения."""
    j = _rci_json("/rci/ip/nat")
    if not isinstance(j, list):
        return None
    return {(r.get("interface") or "") for r in j if isinstance(r, dict)}


def iface_info(iface):
    """show + config одного интерфейса -> dict или None (нет / не прочитан)."""
    st = _rci_json(f"/rci/show/interface/{iface}")
    cf = _rci_json(f"/rci/interface/{iface}")
    if not isinstance(st, dict) or not isinstance(cf, dict) or (not st.get("index") and st.get("index") != 0):
        return None
    cprs = (cf.get("wireguard") or {}).get("peer") or []
    addr = st.get("address") or ((cf.get("ip") or {}).get("address") or {}).get("address", "")
    return {"desc": st.get("description", iface), "addr": addr, "index": st.get("index"),
            "peers": [p.get("public-key", "") for p in ((st.get("wireguard") or {}).get("peer") or [])],
            "is_link": _is_link(cprs, addr, _reg_s2s())}


def apply_acl(iface, grants, exit_mode="", remote_net=None):
    """Правила + NAT + подмена адреса в ЛВС + save. Только для существующего НЕ-линка.
    Ввод уже проверен (collect_grants). -> (ok, msg)."""
    if not re.fullmatch(r"Wireguard\d+", iface or ""):
        return False, "некорректный интерфейс"
    info = iface_info(iface)
    if not info:
        return False, f"{iface} на роутере нет (или не прочитан) — правила не применены"
    if info["is_link"]:
        return False, f"{iface} — линк (роутер сам дозванивается наружу), его правила панель не трогает"
    if exit_mode == "xray" and not info["addr"]:
        return False, f"у {iface} нет адреса — режим xray не настроить"
    _ensure_wan()
    acl = f"_WEBADMIN_{iface}"
    lines = acl_lines(grants, exit_mode, own_ip=info["addr"] or None)

    _rci_run([f"interface {iface}", f"no ip access-group {acl} in"], "отвязка")      # допускает ошибку
    _rci_run([f"no access-list {acl}"], "очистка старого")                           # допускает ошибку
    err = _rci_run([f"access-list {acl}"] + lines, "создание правил")
    if err:
        return False, err + " (конфиг НЕ сохранён)"
    err = _rci_run([f"interface {iface}", f"ip access-group {acl} in"], "привязка")
    if err:
        return False, err + " (конфиг НЕ сохранён)"
    # auto-delete — только ПОСЛЕ привязки (на непривязанный список Keenetic отвечает ошибкой)
    _rci_run([f"access-list {acl}", "auto-delete"], "auto-delete")                   # допускает ошибку
    got = _acl_raw_rules(acl)
    if got is None:
        return False, "не удалось перечитать правила для проверки (конфиг НЕ сохранён)"
    if len(got) != len(lines):
        return False, (f"на роутере {len(got)} правил вместо {len(lines)} — старые не удалились, "
                       "проверьте «Межсетевой экран» интерфейса вручную (конфиг НЕ сохранён)")
    if exit_mode == "direct":
        err = _rci_run([f"ip nat {iface}"], "включение NAT")
        if err:
            return False, err + " (конфиг НЕ сохранён)"
    else:
        _rci_run([f"no ip nat {iface}"], "выключение NAT")                         # допускает ошибку
    err = _save()
    if err:
        return False, "правила применены, но НЕ сохранены — " + err
    srcs = []
    if _grants_touch_lan(grants):
        if info["addr"]:
            srcs.append(str(ipaddress.ip_interface(f"{info['addr']}/24").network))
        if remote_net:
            srcs.append(str(remote_net))
    masq_err = lan_masq_set(iface, srcs)
    tail = {"direct": "интернет через NAT роутера", "xray": "интернет через xray", "": "только ЛВС"}[exit_mode]
    msg = f"{iface}: {len(lines)} правил, {tail}. Конфиг роутера сохранён."
    if masq_err:
        msg += f" ⚠ Подмена адреса в ЛВС не настроена ({masq_err}) — общие папки могут не открываться."
    return True, msg


# ---------------- Подмена адреса WG -> ЛВС (один хук на все доступы) ----------------

# Хосты ЛВС видят адрес роутера, а не туннеля: брандмауэр Windows режет SMB от чужих подсетей.
# Какие подсети чьи — в самом хуке (MAP), роутер остаётся источником истины.
MASQ_HOOK = "/opt/etc/ndm/netfilter.d/zz_wg_lan_masq.sh"
_MAP_ITEM = re.compile(r"(Wireguard\d+)=([0-9./,]+)$")


def _parse_map(s):
    m = {}
    for tok in (s or "").split():
        mm = _MAP_ITEM.match(tok)
        if mm:
            m[mm.group(1)] = [x for x in mm.group(2).split(",") if x]
    return m


def _masq_hook_text(mp):
    lan, lif = str(_lan()), S.lan_if
    items = " ".join(f"{k}={','.join(v)}" for k, v in sorted(mp.items()))
    return "\n".join([
        "#!/bin/sh",
        "# xray-dashboard: masquerade WireGuard clients -> LAN (LAN hosts see the router address;",
        "# Windows firewall drops SMB from foreign subnets). Managed by xray-dashboard, do not edit.",
        '[ "$type" = "ip6tables" ] && exit 0',
        f'MAP="{items}"',
        f"LANNET={lan}",
        f"LANIF={lif}",
        'for m in $MAP; do for s in $(echo "${m#*=}" | tr "," " "); do '
        'iptables -w -t nat -C POSTROUTING -s $s -d $LANNET -o $LANIF -j MASQUERADE 2>/dev/null'
        ' || iptables -w -t nat -I POSTROUTING -s $s -d $LANNET -o $LANIF -j MASQUERADE; done; done',
        "exit 0", ""])


def _masq_hook_state():
    """(MAP, LANNET, LANIF) из хука на роутере; LANNET/LANIF — только проверенного формата (идут в
    shell-команду), иначе "". None — хук не прочитан."""
    rc, out, err = ssh(f"sed -n 's/^MAP=\"\\(.*\\)\"$/\\1/p' {MASQ_HOOK} 2>/dev/null; echo END; "
                       f"sed -n 's/^LANNET=/N /p; s/^LANIF=/I /p' {MASQ_HOOK} 2>/dev/null")
    if rc != 0 or "END" not in out:
        return None
    head, _, tail = out.partition("END")
    kv = {ln[0]: ln[2:].strip() for ln in tail.splitlines() if ln[:2] in ("N ", "I ")}
    olan = kv.get("N", "") if re.fullmatch(r"\d+\.\d+\.\d+\.\d+/\d+", kv.get("N", "")) else ""
    olif = kv.get("I", "") if re.fullmatch(r"[\w.-]+", kv.get("I", "")) else ""
    return _parse_map(head.strip()), olan, olif


def lan_masq_read():
    """{интерфейс: [подсети]} из хука на роутере; None — не прочитан."""
    st = _masq_hook_state()
    return None if st is None else st[0]


def lan_masq_set(iface, srcs):
    """Записать подсети подмены для интерфейса (пусто — убрать) и привести хук к объединению всех.
    Правила, поставленные со старыми LANNET/LANIF (ЛВС или мост сменились), снимаются тоже.
    -> None или текст ошибки."""
    with _OP_LOCK:
        st = _masq_hook_state()
        if st is None:
            return "не прочитан хук на роутере"
        mp, olan, olif = st
        have = sorted({s for v in mp.values() for s in v})
        if srcs:
            mp[iface] = list(srcs)
        else:
            mp.pop(iface, None)
        want = sorted({s for v in mp.values() for s in v})
        lan, lif = str(_lan()), S.lan_if
        dels = [(s, lan, lif) for s in have if s not in want]
        if olan and olif and (olan, olif) != (lan, lif):
            dels += [(s, olan, olif) for s in have]   # действующие хук ниже поставит заново с текущими
        script = [f"while iptables -w -t nat -D POSTROUTING -s {s} -d {l} -o {i} -j MASQUERADE "
                  f"2>/dev/null; do :; done" for s, l, i in dels]
        if mp:
            b64 = base64.b64encode(_masq_hook_text(mp).encode()).decode()
            script += [f"echo {b64} | base64 -d > {MASQ_HOOK}.new", f"sh -n {MASQ_HOOK}.new",
                       f"mv {MASQ_HOOK}.new {MASQ_HOOK}", f"chmod +x {MASQ_HOOK}", f"sh {MASQ_HOOK}"]
        else:
            script += [f"rm -f {MASQ_HOOK}"]
        script.append("echo MASQ_OK")
        rc, out, err = ssh("sh -s", stdin_data=("set -e\n" + "\n".join(script) + "\n").encode())
        if rc != 0 or "MASQ_OK" not in out:
            return (err or out or "ошибка").strip()[:200]
        return None


# ---------------- Создание / удаление интерфейса через RCI ----------------

def _rollback(iface, remote_net=None):
    """Убрать свежесозданный интерфейс. -> True, если его точно нет."""
    if remote_net:
        _rci_run([f"no ip route {remote_net.network_address} {remote_net.netmask} {iface}"], "маршрут")
    _rci_run([f"no ip nat {iface}"], "NAT")
    _rci_run([f"no interface {iface}"], "откат")
    return _iface_exists(iface) is False


def create_iface(slot, desc, server_priv, server_pub, client_pub, remote_net=None):
    """Создать WG-интерфейс с пиром ОДНИМ RCI parse-массивом со своим серверным ключом.
    Перед созданием индекс должен быть свободен (иначе `interface X` вошёл бы в чужой интерфейс
    и переписал его). Проверка: роутер отдаёт тот же публичный ключ. Ошибка -> откат. Без save.
    -> (ok, msg, left) — left=True: интерфейс остался на роутере (откат не удался)."""
    iface = slot["iface"]
    ex = _iface_exists(iface)
    if ex is None:
        return False, f"не удалось проверить, свободен ли {iface}", False
    if ex:
        return False, f"{iface} уже занят на роутере (создан только что?) — повторите выдачу", False
    pref, x = _pref(), slot["sub_x"]
    # description с пробелом — только в кавычках (иначе argument parse error); комментарий пира
    # (!…) — БЕЗ кавычек: он берёт остаток строки, кавычки попали бы в само имя.
    cmds = [f"interface {iface}", f'description "{desc}"', "security-level public",
            f"ip address {pref}.{x}.1 255.255.255.0", "ip tcp adjust-mss pmtu",
            f"wireguard private-key {server_priv}", f"wireguard listen-port {slot['port']}",
            f"wireguard peer {client_pub} !{desc}"]
    cmds += [f"allow-ips {n.network_address} {n.netmask}" for n in _peer_allow(x, remote_net)]
    cmds += ["keepalive-interval 30", "exit", "up"]
    err = _rci_run(cmds, "создание")
    if not err and remote_net:
        err = _rci_run([f"ip route {remote_net.network_address} {remote_net.netmask} {iface} auto"],
                       "маршрут к удалённой ЛВС")
    if not err:
        st = _rci_json(f"/rci/show/interface/{iface}")
        if not isinstance(st, dict):
            err = "не удалось перечитать интерфейс для проверки ключа"
        elif (st.get("wireguard") or {}).get("public-key", "") != server_pub:
            err = "роутер отдал другой публичный ключ"
    if err:
        gone = _rollback(iface, remote_net)
        return (False, err + ("; откат выполнен" if gone else f"; ⚠ откат НЕ удался — {iface} остался на роутере"),
                not gone)
    return True, f"{iface} создан", False


def xray_hook_for(index):
    """Есть ли у nwg{index} хук или cron заворота в xray. None — проверить не удалось."""
    rc, out, err = ssh(f"ls /opt/etc/ndm/netfilter.d/zz_wg_xray_nwg{index}.sh 2>/dev/null; "
                       f"crontab -l 2>/dev/null | grep -c 'zz_wg_xray_nwg{index}\\.sh'; echo END")
    if rc != 0 or "END" not in out:
        return None
    return ".sh" in out.split("\n")[0] or out.split("END")[0].strip().endswith(("1", "2", "3"))


def delete_iface(iface, client_pub, remote_net=None, before_change=None, xray_off=None, xray_on=None):
    """Удалить доступ с роутера: только если единственный пир — ключ клиента из реестра и это не
    линк. Заворот в xray снимается через xray_off(iface) (нет функции — отказ). NAT -> маршрут ->
    интерфейс (ACL с auto-delete уйдёт сам) -> проверка -> save. Интерфейс не удалился — снятые NAT,
    маршрут и заворот (xray_on) возвращаются. before_change() (метка router_dirty в реестре) — после
    снятия заворота, перед первой командой конфигурации Keenetic: хук xray — файлы и cron, а не
    конфигурация, save его не касается; вернула текст — отказ. -> (ok, msg)."""
    if not re.fullmatch(r"Wireguard\d+", iface or ""):
        return False, "некорректный интерфейс"
    info = iface_info(iface)
    if not info:
        return False, f"{iface} не прочитан с роутера"
    if client_pub not in info["peers"]:
        return False, f"{iface} на роутере не совпадает с записью (ключ клиента) — удаление отменено"
    if info["is_link"]:
        return False, (f"{iface} доработан вне панели (в allow-ips чужая сеть или endpoint) — панель его "
                       f"не удаляет: удалите его в Keenetic вручную, потом отзовите запись")
    if len(info["peers"]) != 1:
        return False, (f"у {iface} кроме клиента ещё пиров: {len(info['peers']) - 1} — интерфейс не удаляется: "
                       f"уберите только пир клиента в Keenetic, потом отзовите запись")
    hook = xray_hook_for(info["index"])
    if hook is None:
        return False, "не удалось проверить хуки xray на роутере"
    nat = nat_ifaces()
    if nat is None:
        return False, "NAT с роутера не прочитан — удаление отменено"
    if hook:
        if not xray_off:
            return False, f"у {iface} включён заворот в xray (nwg{info['index']}) — сначала отключите его"
        ok, msg = xray_off(iface)
        if not ok or xray_hook_for(info["index"]) is not False:
            return False, f"заворот в xray у {iface} не снят ({msg}) — удаление отменено"
    if before_change:
        err = before_change()
        if err:
            return False, err + (f"; ⚠ заворот в xray у {iface} уже снят — включите его снова, если доступ остаётся"
                                 if hook else "")
    nat_off = iface in nat and not _rci_run([f"no ip nat {iface}"], "NAT")
    route = f"{remote_net.network_address} {remote_net.netmask} {iface}" if remote_net else ""
    route_off = bool(route) and not _rci_run([f"no ip route {route}"], "маршрут")
    err = _rci_run([f"no interface {iface}"], "удаление")
    if err or _iface_exists(iface) is not False:
        # сами сняли — сами и вернём, иначе доступ остался бы без NAT/маршрута молча
        back = [(f"ip nat {iface}", "NAT")] if nat_off else []
        back += [(f"ip route {route} auto", f"маршрут к {remote_net}")] if route_off else []
        lost = [what for c, what in back if _rci_run([c], "возврат")]
        tail = (f"; ⚠ НЕ возвращено: {', '.join(lost)} — проверьте в Keenetic" if lost
                else "; снятые NAT/маршрут возвращены" if back else "")
        if hook:    # заворот тоже снимали сами — вернуть, иначе клиент xray остался бы без интернета молча
            xok = bool(xray_on) and xray_on(iface)[0]
            tail += ("; заворот в xray возвращён" if xok else
                     "; ⚠ заворот в xray снят и НЕ возвращён — включите его заново или повторите отзыв")
        return False, "не удалён: " + (err or "интерфейс всё ещё на роутере") + tail
    err = _save()
    if err:
        return False, "удалён, но НЕ сохранён (после перезагрузки роутера вернётся): " + err
    masq_err = lan_masq_set(iface, [])
    return True, f"{iface} удалён с роутера, конфиг сохранён" + (f" ⚠ хук подмены: {masq_err}" if masq_err else "")


# ---------------- Инвентарь роутера ----------------

def _fmt_bytes(n):
    try:
        n = float(n)
    except Exception:
        return "0"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024


def client_conf_template(server_pub, sub_x, port, internet=False):
    """Клиентский .conf-шаблон из данных роутера: приватный ключ клиента — плейсхолдер."""
    return client_conf("ВСТАВЬТЕ_ПРИВАТНЫЙ_КЛЮЧ", server_pub, sub_x, [str(_lan())],
                       f"{endpoint_host()}:{port}", S.dns, internet=internet).replace(
        "[Interface]\n", "[Interface]\n# PrivateKey: приватный ключ устройства (выдан при создании доступа). "
                         "Потерян — нужен перевыпуск.\n", 1)


def backup_text(iface, cfgj, stj):
    """Бэкап одного доступа: метаданные + .conf-шаблон + публичные ключи (приватных нет)."""
    desc = cfgj.get("description", iface)
    ipc = cfgj.get("ip", {}) or {}
    a = ipc.get("address", {}) or {}
    addr, bits = a.get("address", ""), _mask2cidr(a.get("mask", "")) or 24
    net = str(ipaddress.ip_interface(f"{addr}/{bits}").network) if addr else "(нет адреса)"
    sub_x = _octet3(addr)
    wgc = cfgj.get("wireguard", {}) or {}
    port = _listen_port(wgc)
    peers = wgc.get("peer") or []
    peer = peers[0] if peers else {}
    client_pub = peer.get("key", "") or ""
    allow_str = ", ".join(f"{x.get('address')}/{_mask2cidr(x.get('mask', ''))}"
                          for x in (peer.get("allow-ips") or []) if x.get("address")) or "(нет)"
    server_pub = ((stj.get("wireguard") or {}).get("public-key", "")) or ""
    acl = next((ag.get("acl", "") for ag in (ipc.get("access-group") or []) if ag.get("direction") == "in"), "")
    lines = [
        f"# === Бэкап WG-доступа: {desc} ({iface}) ===",
        "# Приватные ключи НЕ включены — роутер их не отдаёт.",
        "# Это запись для ПЕРЕСОЗДАНИЯ и ПЕРЕВЫПУСКА, не восстановление 1-в-1.",
        "# Полное восстановление (те же ключи) — только штатный бэкап конфигурации Keenetic",
        "# (веб-интерфейс -> Управление -> Файлы -> startup-config).",
        "",
        "[Параметры]",
        f"Интерфейс:            {iface}",
        f"Имя:                  {desc}",
        f"Адрес роутера:        {addr or '(нет)'}/{bits}",
        f"Подсеть туннеля:      {net}",
        f"ListenPort:           {port}",
        f"Access-list:          {acl or '(нет)'}",
        f"Публичный ключ роутера: {server_pub}",
        f"Публичный ключ клиента: {client_pub}",
        f"AllowedIPs пира:      {allow_str}",
        "",
        "=== Клиентский .conf (шаблон — вставить свой приватный ключ) ===",
        client_conf_template(server_pub, sub_x, port) if (server_pub and sub_x is not None and port)
        else "(недостаточно данных или подсеть не из диапазона панели)",
    ]
    return "\n".join(lines)


def router_inventory(reg=None):
    """Инвентарь WG-интерфейсов роутера (только чтение). Тип: линк (роутер сам дозванивается
    наружу или чужая сеть в allow-ips) или доступ (роутер слушает, устройство подключается)."""
    j = _rci_json("/rci/show/interface", 25)
    conf = _rci_json("/rci/interface", 25)
    if not isinstance(j, dict) or not isinstance(conf, dict):
        return {"ok": False, "err": "роутер не ответил (RCI/SSH)", "items": []}
    _note_wan(j)
    nat = nat_ifaces() or set()
    hooks = xray_hooks() or {}
    by_pub = {c.get("client_pub"): c for c in (reg or {}).values() if c.get("status") != "revoked"}
    s2s = _s2s_nets(by_pub.values())
    items = []
    for k, v in j.items():
        if not re.match(r"Wireguard\d+$", k, re.I) or not isinstance(v, dict):
            continue
        cv = conf.get(k) or {}
        addr = v.get("address", "") or ((cv.get("ip") or {}).get("address") or {}).get("address", "") or ""
        is_link = _is_link((cv.get("wireguard") or {}).get("peer") or [], addr, s2s)
        wg = v.get("wireguard") or {}
        peers = wg.get("peer") or []
        p = peers[0] if peers else {}
        sub_x = _octet3(addr)
        server_pub = wg.get("public-key", "") or ""
        port = _listen_port(wg) or _listen_port(cv.get("wireguard"))
        rec = by_pub.get(p.get("public-key"))
        internet = bool(rec.get("exit")) if rec else (k in nat or v.get("index") in hooks)
        tpl = ""
        if server_pub and port and sub_x is not None and not is_link:
            tpl = client_conf_template(server_pub, sub_x, port, internet=internet)
        desc = v.get("description", "") or "(без имени)"
        items.append({
            "iface": k, "index": v.get("index"), "desc": desc, "fname": client_fname(desc, k),
            "addr": addr, "subnet": str(ipaddress.ip_interface(f"{addr}/24").network) if addr else "",
            "port": port, "up": v.get("state") == "up", "online": bool(p.get("online")),
            "is_link": is_link, "peers": len(peers),
            "remote_ip": (p.get("remote-endpoint-address", "") or "").replace("0.0.0.0", ""),
            "rx": _fmt_bytes(p.get("rxbytes", 0)), "tx": _fmt_bytes(p.get("txbytes", 0)),
            "conf_tpl": tpl, "nat": k in nat, "xray": v.get("index") in hooks,
            "issued": bool(rec), "issued_name": (rec or {}).get("name", ""),
        })
    items.sort(key=lambda i: (i["is_link"], i["index"] if isinstance(i["index"], int) else 999))
    return {"ok": True, "err": "", "items": items}


# ---------------- Сценарии (выдача, отзыв, правила) ----------------

NAME_RE = re.compile(r"[\w .()-]{1,40}")


def parse_remote_lan(s):
    """Подсеть удалённой ЛВС для site-to-site: /8../30, не пересекается с нашими."""
    try:
        net = ipaddress.ip_network((s or "").strip(), strict=True)
    except ValueError:
        raise ValueError("подсеть удалённой ЛВС — в формате 192.168.50.0/24 (адрес сети, без хоста)")
    if net.version != 4 or not 8 <= net.prefixlen <= 30:
        raise ValueError("подсеть удалённой ЛВС: IPv4 с маской от /8 до /30")
    if net.overlaps(_lan()) or net.overlaps(ipaddress.ip_network(f"{_pref()}.0.0/16")):
        raise ValueError("подсеть удалённой ЛВС пересекается с ЛВС роутера или подсетями доступов")
    return net


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def issue(name, exit_mode="direct", full=False, services=None, extra="", s2s=False, remote_lan="",
          create=True, xray_on=None):
    """Выдать доступ: проверка ввода -> ключи -> свободный слот -> оба .conf -> [создать на роутере:
    интерфейс -> правила + NAT + save -> заворот в xray через xray_on(iface)].
    Приватные ключи не сохраняются — возвращаются один раз. -> dict для ответа API."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    services = list(services or [])
    # имя уходит в CLI роутера (description / комментарий пира) — только безопасные символы
    if not NAME_RE.fullmatch(name):
        return {"ok": False, "error": "Имя: буквы, цифры, пробел и - _ . ( ) — от 1 до 40 символов."}
    if exit_mode not in EXIT_MODES or s2s:      # site-to-site — связь ЛВС-ЛВС, без интернета
        exit_mode = ""
    try:
        remote_net = parse_remote_lan(remote_lan) if s2s else None
        grants, allowed = collect_grants(services, full, extra)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not exit_mode and not grants:
        return {"ok": False, "error": "Не выбрано ни одного доступа: включите интернет или отметьте, что "
                                      "открыть в ЛВС" + (" (для site-to-site — обязательно)." if s2s else ".")}
    with _OP_LOCK:
        try:
            reg = registry_read()
        except RegistryError as e:
            return {"ok": False, "error": f"Реестр выданного не читается, выдача остановлена: {e}"}
        state = scan_router()
        if not state.get("ok"):
            return {"ok": False, "error": "Роутер недоступен — имя и слот не проверить: " + state.get("err", "")}
        if not endpoint_host():                  # scan_router обновил WAN; без адреса .conf у клиента нерабочий
            return {"ok": False, "error": "Выдача остановлена: " + endpoint_warning()}
        active = [c for c in reg.values() if c.get("status") != "revoked"]
        norm = lambda s: router_fname((s or "").strip().lower())
        existing = {norm(f.get("desc")) for f in state["ifaces"] if f.get("desc")}
        existing |= {norm(c.get("name")) for c in active}
        if norm(name) in existing:
            return {"ok": False, "error": f"Имя «{name}» уже занято (на роутере или в реестре). Выберите другое."}
        # выданные, но ещё не загруженные на роутер — их слоты тоже заняты
        for c in active:
            if iface_by_pub(state, c.get("client_pub")):
                continue
            if isinstance(c.get("sub_x"), int):
                state["subnets"].add(c["sub_x"])
            if isinstance(c.get("port"), int):
                state["ports"].add(c["port"])
            mm = re.match(r"Wireguard(\d+)$", c.get("iface", "") or "")
            if mm:
                state["ifaces"].append({"name": c["iface"], "n": int(mm.group(1)), "peers": []})
        slot = free_slot(state)
        if slot.get("full"):
            return {"ok": False, "error": f"Кончились {slot['why']} — освободите место (отзовите ненужный доступ)."}
        s_priv, s_pub = genkey()
        c_priv, c_pub = genkey()
        if not (s_priv and c_priv):
            return {"ok": False, "error": "Не удалось сгенерировать ключи."}
        if s2s:
            allowed = [str(_lan())]              # роутер-клиент маршрутизирует в туннель всю нашу ЛВС
        dns = "" if s2s else S.dns
        endpoint = f"{endpoint_host()}:{slot['port']}"
        conf = client_conf(c_priv, s_pub, slot["sub_x"], allowed, endpoint, dns, internet=bool(exit_mode))
        rconf = router_conf(slot, name, s_priv, c_pub, remote_net)
        lines = acl_lines(grants, exit_mode, own_ip=f"{_pref()}.{slot['sub_x']}.1")
        rblock = router_block_text(slot, name, s_priv, c_pub, lines, exit_mode, remote_net)
        cid = uuidlib.uuid4().hex[:12]
        reg[cid] = {
            "name": name, "iface": slot["iface"], "sub_x": slot["sub_x"], "port": slot["port"],
            "client_pub": c_pub, "server_pub": s_pub,
            "allowed": allowed, "full": bool(full), "exit": exit_mode, "services": services, "extra": extra or "",
            "endpoint": endpoint, "dns": dns, "s2s": bool(s2s), "remote_lan": str(remote_net) if s2s else "",
            "created": _now(), "status": "pending",
        }
        if create:
            reg[cid]["router_dirty"] = True      # пишется этой же записью — ДО первой команды роутеру
        try:
            registry_write(reg)
        except Exception as e:
            return {"ok": False, "error": f"Реестр не записан ({e}) — выдача отменена."}
        created = None
        if create:
            ok, msg, left = create_iface(slot, name, s_priv, s_pub, c_pub, remote_net)
            if ok:
                ok, msg2 = apply_acl(slot["iface"], grants, exit_mode, remote_net)
                msg = f"{msg}; {msg2}"
                if not ok:
                    gone = _rollback(slot["iface"], remote_net)
                    left = not gone
                    msg += "; интерфейс " + ("убран (откат)" if gone else "⚠ НЕ удалось откатить — он на роутере без правил")
            xray = None
            if ok:
                reg[cid]["status"] = "active"
                _clear_dirty(reg)               # apply_acl сохранил конфиг
                try:
                    registry_write(reg)
                except Exception as e:
                    msg += f" ⚠ реестр не обновлён: {e}"
                if exit_mode == "xray" and xray_on:
                    xok, xmsg = xray_on(slot["iface"])
                    xray = {"ok": xok, "msg": xmsg}
            created = {"ok": ok, "msg": msg, "left": left, "xray": xray}
    return {"ok": True, "id": cid, "name": name, "slot": slot, "conf": conf, "router_conf": rconf,
            "router_block": rblock, "conf_fname": client_fname(name), "router_fname": router_fname(name),
            "s2s": bool(s2s), "remote_lan": str(remote_net) if s2s else "", "exit": exit_mode,
            "exit_label": EXIT_MODES[exit_mode], "created": created, "endpoint_warn": endpoint_warning()}


def revoke(cid, xray_off=None, xray_on=None):
    """Отозвать: удалить подключение с роутера (по ключу клиента) и сохранить конфиг.
    Запись считается отозванной только после успешного save. xray_off / xray_on — снять заворот
    перед удалением и вернуть его, если удаление сорвалось. -> (ok, msg)."""
    with _OP_LOCK:
        try:
            reg = registry_read()
        except RegistryError as e:
            return False, f"Реестр выданного не читается: {e}"
        c = reg.get(cid)
        if not c or c.get("status") == "revoked":
            return False, "Запись не найдена или уже отозвана."
        name = c.get("name", "")
        state = scan_router()
        if not state.get("ok"):
            return False, f"Роутер недоступен — «{name}» не отозван: {state.get('err', '')}"
        remote_net = ipaddress.ip_network(c["remote_lan"]) if c.get("remote_lan") else None
        f = iface_by_pub(state, c.get("client_pub"))
        if f:
            ok, msg = delete_iface(f["name"], c.get("client_pub", ""), remote_net,
                                   before_change=lambda: _mark_dirty(reg, cid), xray_off=xray_off,
                                   xray_on=xray_on)
            if not ok:
                return False, f"«{name}»: {msg}. Запись НЕ отозвана."
            _clear_dirty(reg)
            note = f"«{name}» отозван: {msg}."
        else:
            # на роутере нет: не загружали, удалили в Keenetic или прошлый отзыв удалил, но не
            # сохранил. Save — только по метке (свои команды остались без save), иначе он закрепил
            # бы чужое несохранённое: интерфейс из CLI, правила после сбоя.
            if c.get("router_dirty"):
                err = _save()
                if err:
                    return False, f"«{name}»: на роутере его нет, но конфиг не сохранился ({err}). Запись НЕ отозвана."
                _clear_dirty(reg)
                note = f"«{name}» отозван (на роутере его уже не было; прошлые изменения панели сохранены)."
            else:
                note = f"«{name}» отозван (на роутере его не было)."
            # интерфейс с этим именем ещё есть (индекс занят новым) — его подмену адреса не трогаем
            if c.get("iface") and not any(x["name"] == c["iface"] for x in state["ifaces"]):
                lan_masq_set(c["iface"], [])
        c["status"] = "revoked"
        c["revoked_at"] = _now()
        try:
            registry_write(reg)
        except Exception as e:
            note += f" ⚠ реестр не обновлён: {e}"
    return True, note


def _reg_for_iface(info):
    """(id, запись реестра) для интерфейса — по ключу клиента среди его пиров."""
    try:
        reg = registry_read()
    except RegistryError:
        return None, None
    for cid, c in reg.items():
        if c.get("status") != "revoked" and c.get("client_pub") in (info or {}).get("peers", []):
            return cid, c
    return None, None


def rules_form(iface):
    """Данные для формы правил интерфейса: текущие правила, предзаполнение, признаки. -> dict."""
    if not re.fullmatch(r"Wireguard\d+", iface or ""):
        return {"ok": False, "error": "некорректный интерфейс"}
    info = iface_info(iface)
    if not info:
        return {"ok": False, "error": f"{iface} на роутере нет или роутер не ответил"}
    if info["is_link"]:
        return {"ok": False, "is_link": True,
                "error": f"{iface} — линк (роутер сам дозванивается наружу): его правила панель не трогает"}
    cid, rec = _reg_for_iface(info)
    current = iface_acl_rules(iface)
    hook = xray_hook_for(info["index"])
    # Фактический выход определяет хук: при нём TCP/UDP интерфейса идут в xray независимо от ip nat
    # (и от режима в реестре — заворот могли включить кнопкой «🛡 Анти-DPI»).
    if rec:
        ex = "xray" if hook and not rec.get("remote_lan") else rec.get("exit", "")
        pre = {"exit": ex, "services": rec.get("services", []),
               "full": rec.get("full", False), "extra": rec.get("extra", ""), "src": "реестра панели"}
    else:
        nat = nat_ifaces()
        if nat is None or current is None or hook is None:
            pre = {"exit": None, "services": [], "full": False, "extra": "", "src": ""}
        else:
            pre = {"exit": "xray" if hook else ("direct" if iface in nat else ""),
                   "services": [], "full": False, "extra": "", "src": "роутера"}
    return {"ok": True, "iface": iface, "desc": info["desc"], "current": current, "pre": pre,
            "s2s": bool(rec and rec.get("remote_lan")), "issued": bool(rec), "xray_on": bool(hook)}


def apply_rules(iface, exit_mode, full=False, services=None, extra=""):
    """Применить правила к интерфейсу (правила + NAT + save), обновить запись реестра.
    -> (ok, msg, итоговый режим): для site-to-site режим всегда "" — по нему вызывающий решает,
    нужен ли заворот в xray."""
    if not re.fullmatch(r"Wireguard\d+", iface or ""):
        return False, "некорректный интерфейс", exit_mode
    if exit_mode not in EXIT_MODES:
        return False, "Выберите режим выхода в интернет.", exit_mode
    services = list(services or [])
    info = iface_info(iface)
    if not info:
        return False, f"{iface} на роутере нет или роутер не ответил", exit_mode
    cid, rec = _reg_for_iface(info)
    remote_net = ipaddress.ip_network(rec["remote_lan"]) if rec and rec.get("remote_lan") else None
    if remote_net:
        exit_mode = ""
    try:
        grants, _ = collect_grants(services, full, extra)
    except ValueError as e:
        return False, f"{e} — правила не тронуты.", exit_mode
    if not exit_mode and not grants:
        return False, "Не выбрано ни одного доступа — правила не тронуты.", exit_mode
    with _OP_LOCK:
        if cid:
            try:
                reg = registry_read()
            except RegistryError as e:
                return False, f"Реестр выданного не читается: {e} — правила не тронуты.", exit_mode
            err = _mark_dirty(reg, cid) if cid in reg else None
            if err:
                return False, err, exit_mode
        ok, msg = apply_acl(iface, grants, exit_mode, remote_net)
        if ok:
            try:
                reg = registry_read()
                touched = cid in reg or any("router_dirty" in r for r in reg.values())
                if cid in reg:
                    reg[cid].update({"status": "active", "exit": exit_mode, "services": services,
                                     "full": bool(full), "extra": extra or ""})
                _clear_dirty(reg)            # save прошёл — сохранено всё
                if touched:
                    registry_write(reg)
            except Exception as e:
                msg += f" ⚠ реестр не обновлён: {e}"
    return ok, msg, exit_mode


def clients_view(state):
    """Записи реестра для списка «Выданные» с привязкой к роутеру. -> (clients, reg_err)."""
    reg_err = ""
    try:
        reg = registry_read()
    except RegistryError as e:
        reg, reg_err = {}, str(e)
    clients = []
    for cid, c in reg.items():
        f = iface_by_pub(state, c.get("client_pub")) if state.get("ok") else None
        clients.append({
            "id": cid, "name": c.get("name", ""), "exit": c.get("exit", ""),
            "exit_label": EXIT_MODES.get(c.get("exit", ""), ""), "s2s": bool(c.get("s2s")),
            "remote_lan": c.get("remote_lan", ""), "subnet": f"{_pref()}.{c.get('sub_x')}.0/24",
            "port": c.get("port"), "created": c.get("created", ""), "status": c.get("status", ""),
            "revoked_at": c.get("revoked_at", ""), "router_iface": f["name"] if f else "",
            "xray_on": bool(f) and f.get("index") in state.get("hooks", {}),
        })
    clients.sort(key=lambda c: (c["status"] == "revoked", c["created"]))
    return clients, reg_err
