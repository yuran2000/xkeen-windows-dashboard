# -*- coding: utf-8 -*-
"""Офлайн-прогон выдачи WireGuard-доступов (wg_clients.py + маршруты /api/xkeen/wg-*) на имитации
Keenetic: RCI (интерфейсы, пиры, access-list, NAT, маршруты, save), хуки заворота в xray и хук
подмены адреса WG -> ЛВС. К настоящему роутеру обращений нет. Заворот в xray (wg_xray_connect /
wg_xray_disconnect) подменён: он ставит/снимает метку хука в имитации.

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_wg_clients_offline.py
"""
import base64
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402
import wg_clients as W  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wgtest_")
REG = os.path.join(TMP, "wg_clients.json")
WAN = "203.0.113.7"
LAN = "192.168.1.0/24"

D.cfg.WG_LAN_SUBNET = LAN
D.cfg.WG_LAN_IF = "br0"
D.cfg.WG_ENDPOINT_HOST = ""
D.cfg.WG_BLOCK_EXTRA = ["172.20.0.0/16"]
D.cfg.WG_SERVICES = [
    {"id": "rdp_pc", "label": "RDP к ПК", "ips": ["192.168.1.10"], "ports": "3389"},
    {"id": "smb_pc", "label": "Общие папки ПК", "ips": ["192.168.1.10"], "ports": "139,445"},
]
_orig_settings = D._wg_settings


def _test_settings():
    s = _orig_settings()
    s.registry_path = REG
    return s


D._wg_settings = _test_settings


class Router:
    def __init__(self):
        self.ifaces = {}      # name -> {desc, addr, port, pub, peers{pub:{allow,endpoint}}, up, acl_in}
        self.acls = {}        # name -> {"rules": [...], "auto": bool}
        self.nat = {"Bridge0"}
        self.routes = set()
        self.saved = None
        self.hooks = set()    # индексы nwg с хуком xray
        self.masq = None      # MAP в хуке подмены
        self.fail = set()     # имитация сбоев: "save", "acl_read", "post", "noiface", "xray_off"
        self.calls = []
        self.add("Wireguard0", "Office-link", "172.16.50.2", 51820, peers={"LINKPUB=": "198.51.100.20:51820"})
        self.add("Wireguard2", "Home-1", "172.173.1.1", 48001, peers={"HOMEPUB=": None})
        self.add("Wireguard4", "Old-down", "172.173.3.1", 48003, peers={"OLDPUB=": None}, up=False)
        self.save()

    def add(self, name, desc, addr, port, peers, up=True):
        self.ifaces[name] = {"desc": desc, "addr": addr, "port": port, "pub": "SRV" + name + "=",
                             "peers": {k: {"allow": [], "endpoint": v} for k, v in peers.items()},
                             "up": up, "acl_in": None}

    def save(self):
        self.saved = json.dumps([self.ifaces, self.acls, sorted(self.nat), sorted(self.routes)], sort_keys=True)

    def show_iface(self, k):
        v = self.ifaces[k]
        d = {"index": int(k[9:]), "type": "Wireguard", "description": v["desc"], "state": "up" if v["up"] else "down",
             "wireguard": {"public-key": v["pub"], "listen-port": v["port"],
                           "peer": [{"public-key": p, "online": False} for p in v["peers"]]}}
        if v["up"]:
            d["address"] = v["addr"]
        return d

    def conf_iface(self, k):
        v = self.ifaces[k]
        return {"description": v["desc"],
                "ip": {"address": {"address": v["addr"], "mask": "255.255.255.0"},
                       "access-group": [{"acl": v["acl_in"], "direction": "in"}] if v["acl_in"] else []},
                "wireguard": {"listen-port": {"port": v["port"]},
                              "peer": [dict({"key": p, "allow-ips": [{"address": a, "mask": mk} for a, mk in e["allow"]]},
                                            **({"endpoint": {"address": e["endpoint"]}} if e["endpoint"] else {}))
                                       for p, e in v["peers"].items()]}}

    def get(self, path):
        if path == "/rci/show/interface":
            d = {k: self.show_iface(k) for k in self.ifaces}
            d["PPPoE0"] = {"index": 0, "type": "PPPoE", "defaultgw": True, "address": WAN}
            return json.dumps(d)
        if path == "/rci/interface":
            return json.dumps({k: self.conf_iface(k) for k in self.ifaces})
        m = re.fullmatch(r"/rci/(show/)?interface/(\w+)", path)
        if m:
            k = m.group(2)
            if k not in self.ifaces:
                return ""
            return json.dumps(self.show_iface(k) if m.group(1) else self.conf_iface(k))
        if path == "/rci/access-list":
            if "acl_read" in self.fail:
                return ""
            out = []
            for a, d in self.acls.items():
                out += [dict(r, acl=a) for r in d["rules"]]
                if d["auto"]:
                    out.append({"acl": a, "auto-delete": True})
            return json.dumps(out)
        if path == "/rci/ip/nat":
            return json.dumps([{"interface": n} for n in sorted(self.nat)])
        raise AssertionError("неизвестный GET " + path)

    def run(self, cmds):
        res, ctx, peer = [], None, None
        for c in cmds:
            err = None
            try:
                err = self.cmd(c, ctx, peer)
                if isinstance(err, tuple):
                    ctx, peer, err = err
            except Exception as e:
                err = str(e)
            res.append({"parse": {"status": [{"status": "error" if err else "message", "message": err or "ok"}]}})
        return json.dumps(res)

    def cmd(self, c, ctx, peer):
        if c == "system configuration save":
            if "save" in self.fail:
                return "save failed"
            self.save()
            return None
        m = re.fullmatch(r"interface (Wireguard\d+)", c)
        if m:
            k = m.group(1)
            if k not in self.ifaces:
                self.ifaces[k] = {"desc": "", "addr": "", "port": None, "pub": "", "peers": {}, "up": False, "acl_in": None}
            return (("if", k), None, None)
        m = re.fullmatch(r"no interface (\w+)", c)
        if m:
            k = m.group(1)
            if k not in self.ifaces:
                return "not found"
            if "noiface" in self.fail:
                return "interface is busy"
            acl = self.ifaces[k]["acl_in"]
            if acl and self.acls.get(acl, {}).get("auto"):
                del self.acls[acl]
            del self.ifaces[k]
            self.nat.discard(k)
            return None
        m = re.fullmatch(r"access-list (\S+)", c)
        if m:
            self.acls.setdefault(m.group(1), {"rules": [], "auto": False})
            return (("acl", m.group(1)), None, None)
        m = re.fullmatch(r"no access-list (\S+)", c)
        if m:
            if m.group(1) not in self.acls:
                return "not found"
            del self.acls[m.group(1)]
            return None
        m = re.fullmatch(r"(no )?ip nat (\w+)", c)
        if m:
            (self.nat.discard if m.group(1) else self.nat.add)(m.group(2))
            return None
        m = re.fullmatch(r"(no )?ip route (\S+) (\S+) (\w+)( auto)?", c)
        if m:
            key = (m.group(2), m.group(3), m.group(4))
            (self.routes.discard if m.group(1) else self.routes.add)(key)
            return None
        if ctx and ctx[0] == "acl":
            a = self.acls[ctx[1]]
            if c == "auto-delete":
                if not any(v["acl_in"] == ctx[1] for v in self.ifaces.values()):
                    return "cannot enable auto-deletion for unreferenced lists."
                a["auto"] = True
                return None
            m = re.fullmatch(r"(permit|deny) (\w+) 0\.0\.0\.0 0\.0\.0\.0 (\S+) (\S+)(?: port (eq|range) (\d+)(?: (\d+))?)?", c)
            if not m:
                return "argument parse error."
            r = {"action": m.group(1), "protocol": m.group(2), "destination": m.group(3), "destination-mask": m.group(4)}
            if m.group(5):
                r["dst-port-operator"] = m.group(5)
                r["destination-port"] = m.group(6) + (("-" + m.group(7)) if m.group(7) else "")
            a["rules"].append(r)
            return None
        if ctx and ctx[0] == "if":
            v = self.ifaces[ctx[1]]
            if c.startswith("description "):
                d = c[12:]
                if not (d.startswith('"') and d.endswith('"')) and " " in d:
                    return "argument parse error."
                v["desc"] = d.strip('"')
                return None
            if c in ("security-level public", "ip tcp adjust-mss pmtu", "keepalive-interval 30", "connect"):
                return None
            m = re.fullmatch(r"ip address (\S+) \S+", c)
            if m:
                v["addr"] = m.group(1)
                return None
            m = re.fullmatch(r"wireguard private-key (\S+)", c)
            if m:
                k = int.from_bytes(base64.b64decode(m.group(1)), "little")
                v["pub"] = base64.b64encode(W.x25519(k, 9).to_bytes(32, "little")).decode()
                return None
            m = re.fullmatch(r"wireguard listen-port (\d+)", c)
            if m:
                v["port"] = int(m.group(1))
                return None
            m = re.fullmatch(r"wireguard peer (\S+) !(.*)", c)
            if m:
                v["peers"][m.group(1)] = {"allow": [], "endpoint": None}
                return (ctx, m.group(1), None)
            m = re.fullmatch(r"allow-ips (\S+) (\S+)", c)
            if m and peer:
                v["peers"][peer]["allow"].append((m.group(1), m.group(2)))
                return None
            if c == "exit":
                return (ctx, None, None)
            if c == "up":
                v["up"] = True
                return None
            m = re.fullmatch(r"(no )?ip access-group (\S+) in", c)
            if m:
                v["acl_in"] = None if m.group(1) else m.group(2)
                return None
        return "unknown command: " + c


R = Router()
R.masq_old = ""          # строки "N <LANNET>"/"I <LANIF>" из хука подмены (после END)
R.last_masq_script = ""


def _res(rc, out, err=""):
    return {"ok": rc == 0, "stdout": out, "stderr": err, "code": rc}


def fake_keenetic_ssh(cmd, timeout=15, stdin_data=None):
    R.calls.append(cmd)
    if "post" in R.fail and "base64 -d | curl" in cmd:
        return _res(-1, "", "timeout")
    m = re.fullmatch(r"curl -s http://localhost:79(\S+)", cmd)
    if m:
        return _res(0, R.get(m.group(1)))
    m = re.fullmatch(r"curl -s -o /dev/null -w %\{http_code\} http://localhost:79/rci/show/interface/(\w+)", cmd)
    if m:
        return _res(0, "200" if m.group(1) in R.ifaces else "404")
    if cmd.startswith("base64 -d | curl"):
        data = stdin_data if isinstance(stdin_data, bytes) else stdin_data.encode()
        body = json.loads(base64.b64decode(data))
        return _res(0, R.run([x["parse"] for x in body]))
    if cmd.startswith("for f in /opt/etc/ndm/netfilter.d/zz_wg_xray_nwg"):
        return _res(0, "".join(f"/opt/etc/ndm/netfilter.d/zz_wg_xray_nwg{i}.sh 172.173.9.0/24\n" for i in R.hooks) + "END\n")
    if cmd.startswith("ls /opt/etc/ndm/netfilter.d/zz_wg_xray_nwg"):
        if "hookread" in R.fail:
            return _res(1, "", "timeout")
        i = int(re.search(r"nwg(\d+)\.sh", cmd).group(1))
        return _res(0, (f"/opt/etc/ndm/netfilter.d/zz_wg_xray_nwg{i}.sh\n1\n" if i in R.hooks else "0\n") + "END\n")
    if cmd.startswith("sed -n 's/^MAP="):
        if not cmd.rstrip().endswith("echo END"):
            # как в настоящем sh: код цепочки = код последней команды; sed по отсутствующему хуку — 1/2
            return _res(0 if R.masq is not None else 2, (R.masq + "\n" if R.masq else "") + "END\n" + R.masq_old)
        return _res(0, (R.masq + "\n" if R.masq else "") + "SEP\n" + R.masq_old + "END\n")
    if cmd == "sh -s":
        s = stdin_data.decode() if isinstance(stdin_data, bytes) else stdin_data
        R.last_masq_script = s
        m = re.search(r"echo (\S+) \| base64 -d", s)
        if m:
            hook = base64.b64decode(m.group(1)).decode()
            assert "LANNET=" + LAN in hook and hook.startswith("#!/bin/sh"), hook
            R.masq = re.search(r'MAP="(.*)"', hook).group(1)
        elif "rm -f" in s:
            R.masq = None
        return _res(0, "MASQ_OK\n")
    raise AssertionError("неизвестная ssh-команда: " + cmd)


def fake_xray_connect(iface_id):
    if "xray_on" in R.fail:
        return {"ok": False, "error": "хук не встал"}
    R.hooks.add(int(iface_id[9:]))
    return {"ok": True, "connected": True, "kernel": "nwg" + iface_id[9:], "msg": "подключён"}


def fake_xray_disconnect(iface_id):
    if "xray_off" in R.fail:
        return {"ok": False, "error": "catch-all всё ещё на месте"}
    R.hooks.discard(int(iface_id[9:]))
    return {"ok": True, "connected": False, "kernel": "nwg" + iface_id[9:], "msg": "отключён"}


REAL_XRAY_DISCONNECT = D.wg_xray_disconnect
REAL_XRAY_CONNECT = D.wg_xray_connect
D.keenetic_ssh = fake_keenetic_ssh
D.wg_xray_connect = fake_xray_connect
D.wg_xray_disconnect = fake_xray_disconnect
D.app.config["TESTING"] = True
c = D.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True


def post(url, body):
    return c.post(url, json=body).get_json()


def reg():
    return W._json_read(REG)


def mask_of(k):
    """MAP хука подмены -> {интерфейс: [подсети]}."""
    return W._parse_map(R.masq or "")


fails = 0


def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (("  | " + str(extra)[:300]) if not cond and extra else ""))
    if not cond:
        fails += 1


# 0. без входа — редирект на /login, до роутера не доходит
c0 = D.app.test_client()
n = len(R.calls)
r = c0.post("/api/xkeen/wg-issue", json={"name": "x", "exit": "direct", "create": True})
check("0 без входа: 302 и роутер не тронут", r.status_code == 302 and len(R.calls) == n)

# 1. direct + create (кириллическое имя)
d = post("/api/xkeen/wg-issue", {"name": "Ноутбук Анны", "exit": "direct", "create": True})
cr = d.get("created") or {}
check("1 direct: готово на роутере", d.get("ok") and cr.get("ok"), d)
k1 = next(k for k, v in R.ifaces.items() if v["desc"] == "Ноутбук Анны")
check("1 direct: NAT вкл", k1 in R.nat)
check("1 direct: сохранено", R.saved and k1 in R.saved)
rules1 = R.acls[f"_WEBADMIN_{k1}"]["rules"]
check("1 direct: deny частных сетей + permit в конце", rules1[-1]["action"] == "permit"
      and any(x["destination"] == "192.168.0.0" and x["action"] == "deny" for x in rules1))
check("1 direct: внешний IP роутера закрыт", any(x["destination"] == WAN and x["action"] == "deny" for x in rules1))
check("1 direct: auto-delete", R.acls[f"_WEBADMIN_{k1}"]["auto"])
check("1 файл клиента латиницей", d.get("conf_fname") == "Noutbuk_Anny-wg.conf", d.get("conf_fname"))
check("1 endpoint = WAN роутера, не в AllowedIPs", f"Endpoint = {WAN}:" in d["conf"] and f"{WAN}/32" not in d["conf"]
      and "2000::/3" in d["conf"])
check("1 QR отдан", (d.get("qr") or "").startswith("data:image/png;base64,"))
check("1 белый WAN — без предупреждения", d.get("endpoint_warn") == "", d.get("endpoint_warn"))
W._WAN["host"] = "100.64.3.4"
D.cfg.WG_ENDPOINT_HOST = ""
check("1 серый WAN (CGNAT) — предупреждение", "CGNAT" in W.endpoint_warning())
W._WAN["host"] = WAN
check("1 статус active", any(v["status"] == "active" and v["name"] == "Ноутбук Анны" for v in reg().values()))
check("1 подмена ЛВС не нужна", R.masq is None)
check("1 подсеть не занятая выключенным (.3) и существующим (.1)", R.ifaces[k1]["addr"] not in ("172.173.3.1", "172.173.1.1"),
      R.ifaces[k1]["addr"])
check("1 заворот xray не ставился", not R.hooks)

lst = c.get("/api/xkeen/wg-clients").get_json()
man = {m["iface"]: m for m in lst.get("manual", [])}
check("1m «Выданные»: созданные вручную доступы показаны, линк и выданное панелью — нет",
      "Wireguard2" in man and "Wireguard4" in man and "Wireguard0" not in man and k1 not in man
      and man["Wireguard2"]["desc"] == "Home-1", sorted(man))

# 2. только ЛВС + SMB -> подмена адреса
d = post("/api/xkeen/wg-issue", {"name": "NAS user", "exit": "", "services": ["smb_pc"], "create": True})
k2 = next(k for k, v in R.ifaces.items() if v["desc"] == "NAS user")
check("2 ЛВС: нет permit на любой адрес", not any(x["destination"] == "0.0.0.0" and x["action"] == "permit"
                                                   and x["protocol"] != "icmp" for x in R.acls[f"_WEBADMIN_{k2}"]["rules"]))
check("2 ЛВС: порты SMB", {x.get("destination-port") for x in R.acls[f"_WEBADMIN_{k2}"]["rules"]} >= {"139", "445"})
check("2 ЛВС: NAT выкл", k2 not in R.nat)
check("2 подмена ЛВС для подсети интерфейса", mask_of(k2).get(k2) == [R.ifaces[k2]["addr"].rsplit(".", 1)[0] + ".0/24"], R.masq)

# 3. отзыв 2 -> подмена убрана
cid2 = next(k for k, v in reg().items() if v["name"] == "NAS user")
d = post("/api/xkeen/wg-revoke", {"id": cid2})
check("3 отзыв: интерфейса нет и сохранено", d.get("ok") and k2 not in R.ifaces and k2 not in R.saved, d)
check("3 отзыв: подмена убрана", R.masq is None)
check("3 отзыв: ACL ушёл", f"_WEBADMIN_{k2}" not in R.acls)

# 4. ручной путь: найден по ключу, правила по кнопке
d = post("/api/xkeen/wg-issue", {"name": "Guest Laptop", "exit": "direct", "create": False})
check("4 ручной: выдан без created", d.get("ok") and d.get("created") is None and "[Interface]" in d.get("router_conf", ""))
cid4 = next(k for k, v in reg().items() if v["name"] == "Guest Laptop")
rec = reg()[cid4]
R.add("Wireguard7", "Guest_Laptop", f"172.173.{rec['sub_x']}.1", rec["port"], peers={rec["client_pub"]: None})
lst = c.get("/api/xkeen/wg-clients").get_json()
check("4 ручной: найден по ключу", any(x["id"] == cid4 and x["router_iface"] == "Wireguard7" for x in lst["clients"]))
d = post("/api/xkeen/wg-acl-apply", {"iface_id": "Wireguard7", "exit": "direct"})
check("4 ручной: применено", d.get("ok") and reg()[cid4]["status"] == "active" and "Wireguard7" in R.nat, d)

# 5. отзыв со сбоем save -> не отозван; повтор -> отозван с save
R.fail.add("save")
d = post("/api/xkeen/wg-revoke", {"id": cid4})
check("5 сбой save: не отозван", not d.get("ok") and reg()[cid4]["status"] != "revoked" and "Wireguard7" not in R.ifaces, d)
R.fail.discard("save")
d = post("/api/xkeen/wg-revoke", {"id": cid4})
check("5 повтор: отозван и сохранено", d.get("ok") and reg()[cid4]["status"] == "revoked" and "Wireguard7" not in R.saved, d)

# 6. правила на несуществующий интерфейс и на линк
before = set(R.ifaces)
d = post("/api/xkeen/wg-acl-apply", {"iface_id": "Wireguard9", "exit": "direct"})
check("6 нет интерфейса: отказ, не создан", not d.get("ok") and set(R.ifaces) == before, d)
d = post("/api/xkeen/wg-acl-apply", {"iface_id": "Wireguard0", "exit": "direct", "full": True})
check("6b линк не трогаем", not d.get("ok") and "Wireguard0" not in R.nat and "_WEBADMIN_Wireguard0" not in R.acls, d)
f = c.get("/api/xkeen/wg-acl?iface_id=Wireguard0").get_json()
check("6c форма правил для линка: отказ", not f.get("ok") and f.get("is_link"), f)

# 9. битый реестр не затирается
raw = open(REG, encoding="utf-8").read()
open(REG, "w", encoding="utf-8").write("{битый")
d = post("/api/xkeen/wg-issue", {"name": "Dave", "exit": "direct", "create": True})
check("9 битый реестр: выдача остановлена, файл цел", not d.get("ok") and open(REG, encoding="utf-8").read() == "{битый", d)
lst = c.get("/api/xkeen/wg-clients").get_json()
check("9 битый реестр виден в списке", bool(lst.get("reg_err")))
open(REG, "w", encoding="utf-8-sig").write(raw)
check("9 BOM читается", len(reg()) >= 3)

# 10. site-to-site
d = post("/api/xkeen/wg-issue", {"name": "Dacha", "s2s": True, "remote_lan": "192.168.50.0/33", "services": ["rdp_pc"], "create": True})
check("10 /33 отклонён до реестра", not d.get("ok") and not any(v["name"] == "Dacha" for v in reg().values()))
d = post("/api/xkeen/wg-issue", {"name": "Dacha", "s2s": True, "remote_lan": "192.168.50.0/24", "create": True})
check("10 s2s без доступа отклонён", not d.get("ok") and not any(v["name"] == "Dacha" for v in reg().values()))
d = post("/api/xkeen/wg-issue", {"name": "Dacha", "s2s": True, "remote_lan": "192.168.50.0/24", "full": True, "create": True})
kd = next((k for k, v in R.ifaces.items() if v["desc"] == "Dacha"), None)
check("10 s2s: маршрут", kd and ("192.168.50.0", "255.255.255.0", kd) in R.routes, d)
check("10 s2s: без DNS и QR", "DNS =" not in d.get("conf", "") and not d.get("qr"))
check("10 s2s: подмена для удалённой ЛВС", "192.168.50.0/24" in mask_of(kd).get(kd, []), R.masq)
cidd = next(k for k, v in reg().items() if v["name"] == "Dacha")
d = post("/api/xkeen/wg-acl-apply", {"iface_id": kd, "exit": "", "services": ["rdp_pc"]})
check("10 своя s2s остаётся управляемой (не линк)", d.get("ok") and
      any(x.get("destination-port") == "3389" for x in R.acls[f"_WEBADMIN_{kd}"]["rules"]), d)
post("/api/xkeen/wg-revoke", {"id": cidd})
# 10b. чужой входящий межсайтовый туннель: endpoint нет, в allow-ips чужая ЛВС -> линк
R.add("Wireguard11", "Filial-in", "172.173.77.1", 48077, peers={"FILIALPUB=": None})
R.ifaces["Wireguard11"]["peers"]["FILIALPUB="]["allow"] = [("172.173.77.0", "255.255.255.0"), ("192.168.77.0", "255.255.255.0")]
R.acls["_WEBADMIN_Wireguard11"] = {"rules": [{"action": "permit", "protocol": "ip", "destination": "192.168.1.0",
                                             "destination-mask": "255.255.255.0"}], "auto": True}
R.ifaces["Wireguard11"]["acl_in"] = "_WEBADMIN_Wireguard11"
d = post("/api/xkeen/wg-acl-apply", {"iface_id": "Wireguard11", "exit": "direct", "full": True})
check("10b чужой входящий s2s = линк, правила не тронуты", not d.get("ok") and len(R.acls["_WEBADMIN_Wireguard11"]["rules"]) == 1
      and "Wireguard11" not in R.nat, d)
inv = c.get("/api/xkeen/wg-router").get_json()
it = {i["iface"]: i for i in inv["items"]}
check("10b в инвентаре помечен линком", it["Wireguard11"]["is_link"] and it["Wireguard0"]["is_link"] and not it["Wireguard2"]["is_link"])
check("10b шаблон conf только у доступов", it["Wireguard2"]["conf_tpl"] and not it["Wireguard0"]["conf_tpl"])
del R.ifaces["Wireguard11"]
del R.acls["_WEBADMIN_Wireguard11"]
check("10 s2s отзыв: маршрут и подмена убраны", not any(x[2] == kd for x in R.routes) and R.masq is None, R.masq)

# 11. плохой extra — до роутера не доходит
n = len(R.calls)
d = post("/api/xkeen/wg-acl-apply", {"iface_id": k1, "exit": "direct", "extra": "192.168.1.50:8080-"})
check("11 опечатка в порте: правила не тронуты", not d.get("ok") and not any("base64 -d | curl" in x for x in R.calls[n:]), d)
D._wg_prepare()
g, a = W.collect_grants([], False, "192.168.1.50\n192.168.1.0/28:80")
lines = W.acl_lines(g, "")
check("11 без портов = весь хост, CIDR сохранён", "permit tcp 0.0.0.0 0.0.0.0 192.168.1.50 255.255.255.255" in lines
      and "permit tcp 0.0.0.0 0.0.0.0 192.168.1.0 255.255.255.240 port eq 80" in lines)

# 12. гонка: индекс заняли между сканом и созданием
orig = W.free_slot


def racy(state):
    s = orig(state)
    R.add(s["iface"], "Чужой", "172.173.200.1", 48090, peers={"OTHER=": None})
    return s


W.free_slot = racy
d = post("/api/xkeen/wg-issue", {"name": "Racer", "exit": "direct", "create": True})
W.free_slot = orig
check("12 гонка: чужой интерфейс цел", any(v["desc"] == "Чужой" for v in R.ifaces.values())
      and "уже занят" in ((d.get("created") or {}).get("msg") or ""), d)

# 13. правила упали после создания -> откат, pending
R.fail.add("acl_read")
d = post("/api/xkeen/wg-issue", {"name": "Failer", "exit": "direct", "create": True})
R.fail.discard("acl_read")
check("13 сбой правил: откат, запись pending", not (d.get("created") or {}).get("ok")
      and not any(v["desc"] == "Failer" for v in R.ifaces.values())
      and any(v["name"] == "Failer" and v["status"] == "pending" for v in reg().values()), d)

# 14. отзыв доступа с заворотом: заворот снимается сам, потом удаление
cid1 = next(k for k, v in reg().items() if v["name"] == "Ноутбук Анны")
R.hooks.add(int(k1[9:]))
R.fail.add("xray_off")
d = post("/api/xkeen/wg-revoke", {"id": cid1})
check("14 заворот не снялся -> отзыв отказан", not d.get("ok") and k1 in R.ifaces and reg()[cid1]["status"] != "revoked", d)
R.fail.discard("xray_off")
check("14 заворот не снят: метки save нет (к конфигурации Keenetic не обращались)", not reg()[cid1].get("router_dirty"))
# 14b. индекс под хуком не выдаётся
R.hooks.add(5)
D._wg_prepare()
st = W.scan_router()
check("14b индекс под хуком занят", W.free_slot(st)["n"] != 5)
inv = c.get("/api/xkeen/wg-router").get_json()
check("14c осиротевший хук nwg5 виден", "nwg5" in inv.get("orphan_hooks", []), inv.get("orphan_hooks"))
R.hooks.discard(5)
d = post("/api/xkeen/wg-revoke", {"id": cid1})
check("14 повтор: заворот снят, интерфейс удалён", d.get("ok") and k1 not in R.ifaces and int(k1[9:]) not in R.hooks, d)
check("14 после save меток нет", not any("router_dirty" in v for v in reg().values()))

# 15. xray: свой .1 открыт, порты управления закрыты
g, a = W.collect_grants([], False, "")
L = W.acl_lines(g, "xray", "172.173.4.1")
check("15 xray: deny 222 на свой .1 раньше permit", L.index("deny tcp 0.0.0.0 0.0.0.0 172.173.4.1 255.255.255.255 port eq 222") <
      L.index("permit tcp 0.0.0.0 0.0.0.0 172.173.4.1 255.255.255.255") < L.index("deny tcp 0.0.0.0 0.0.0.0 172.173.0.0 255.255.0.0"))

# 15b. выдача в режиме xray включает заворот сам; смена режима на direct — снимает
d = post("/api/xkeen/wg-issue", {"name": "Phone X", "exit": "xray", "create": True})
kx = next(k for k, v in R.ifaces.items() if v["desc"] == "Phone X")
check("15b xray: заворот включён при выдаче", d.get("ok") and (d["created"].get("xray") or {}).get("ok") and int(kx[9:]) in R.hooks, d.get("created"))
check("15b xray: NAT не включён", kx not in R.nat)
f = c.get(f"/api/xkeen/wg-acl?iface_id={kx}").get_json()
check("15b форма правил: режим из реестра", f.get("ok") and f["pre"]["exit"] == "xray", f)
d = post("/api/xkeen/wg-acl-apply", {"iface_id": kx, "exit": "direct"})
check("15b direct: заворот снят, NAT вкл", d.get("ok") and int(kx[9:]) not in R.hooks and kx in R.nat, d)
lst = c.get("/api/xkeen/wg-clients").get_json()
check("15b реестр: режим обновлён", any(x["name"] == "Phone X" and x["exit"] == "direct" for x in lst["clients"]))
# 15c. чужой доступ (не из реестра) с хуком: форма угадывает xray
R.hooks.add(2)
f = c.get("/api/xkeen/wg-acl?iface_id=Wireguard2").get_json()
check("15c форма для подключения не из реестра: xray по хуку", f.get("ok") and f["pre"]["exit"] == "xray" and f["pre"]["src"] == "роутера", f)
R.hooks.discard(2)

# 17. выдан панелью, потом руками добавлена чужая сеть -> линк, отзыв не удаляет
d = post("/api/xkeen/wg-issue", {"name": "Branch office", "exit": "direct", "create": True})
kk = next(k for k, v in R.ifaces.items() if v["desc"] == "Branch office")
cidk = next(k for k, v in reg().items() if v["name"] == "Branch office")
pk = reg()[cidk]["client_pub"]
R.ifaces[kk]["peers"][pk]["allow"].append(("192.168.5.0", "255.255.255.0"))
R.save()
d = post("/api/xkeen/wg-revoke", {"id": cidk})
check("17 доработан руками: не удалён, не отозван", not d.get("ok") and kk in R.ifaces and kk in R.saved
      and reg()[cidk]["status"] != "revoked" and "доработан вне панели" in d.get("error", ""), d)
d = post("/api/xkeen/wg-acl-apply", {"iface_id": kk, "exit": "", "services": ["rdp_pc"]})
check("17 доработан руками: правила не тронуты", not d.get("ok") and R.acls[f"_WEBADMIN_{kk}"]["rules"][-1]["action"] == "permit")
# 17b. своя s2s + сеть вне remote_lan -> тоже линк
d = post("/api/xkeen/wg-issue", {"name": "Dacha2", "s2s": True, "remote_lan": "192.168.60.0/24", "full": True, "create": True})
k6 = next(k for k, v in R.ifaces.items() if v["desc"] == "Dacha2")
D._wg_prepare()
st = W.scan_router()
check("17b своя s2s без доработки — не линк", not next(x for x in st["ifaces"] if x["name"] == k6)["is_link"])
p6 = next(iter(R.ifaces[k6]["peers"]))
R.ifaces[k6]["peers"][p6]["allow"].append(("10.10.0.0", "255.255.0.0"))
st = W.scan_router()
check("17b своя s2s + чужая сеть — линк", next(x for x in st["ifaces"] if x["name"] == k6)["is_link"])
R.ifaces[k6]["peers"][p6]["allow"].pop()

# 18. в интерфейсе панели чужой пир -> интерфейс не удаляется
R.ifaces[kk]["peers"][pk]["allow"].pop()
R.ifaces[kk]["peers"]["GUEST="] = {"allow": [], "endpoint": None}
R.save()
d = post("/api/xkeen/wg-revoke", {"id": cidk})
check("18 чужой пир: интерфейс цел, не отозван", not d.get("ok") and kk in R.ifaces and "GUEST=" in R.ifaces[kk]["peers"]
      and reg()[cidk]["status"] != "revoked", d)
del R.ifaces[kk]["peers"]["GUEST="]

# 19. интерфейс не удалился -> NAT и маршрут возвращены
R.fail.add("noiface")
d = post("/api/xkeen/wg-revoke", {"id": cidk})
check("19 не удалился: NAT вернулся", not d.get("ok") and kk in R.ifaces and kk in R.nat and "возвращены" in d.get("error", ""), d)
cid6 = next(k for k, v in reg().items() if v["name"] == "Dacha2")
d = post("/api/xkeen/wg-revoke", {"id": cid6})
check("19 не удалился: маршрут s2s вернулся", ("192.168.60.0", "255.255.255.0", k6) in R.routes, d)
R.fail.discard("noiface")
post("/api/xkeen/wg-revoke", {"id": cid6})

# 20. «на роутере нет» без метки -> save не делается, чужое несохранённое не закрепляется
R.save()
d = post("/api/xkeen/wg-issue", {"name": "Manual only", "exit": "direct", "create": False})
cidm = next(k for k, v in reg().items() if v["name"] == "Manual only")
check("20 ручной путь: метки нет", "router_dirty" not in reg()[cidm])
R.add("Wireguard30", "CLI-unsaved", "172.173.230.1", 48099, peers={"CLIPUB=": None})
d = post("/api/xkeen/wg-revoke", {"id": cidm})
check("20 без метки: отозван, чужое НЕ сохранено", d.get("ok") and reg()[cidm]["status"] == "revoked"
      and "CLI-unsaved" not in R.saved, d)
del R.ifaces["Wireguard30"]
check("20 после успешных save меток нет", not any("router_dirty" in v for v in reg().values()),
      [v["name"] for v in reg().values() if "router_dirty" in v])

# 21. реестр «занят» (WinError 5 на replace) — повтор, а не сбой
real_replace, busy = os.replace, [3]


def busy_replace(a, b):
    if busy[0]:
        busy[0] -= 1
        raise PermissionError(13, "Отказано в доступе")
    return real_replace(a, b)


W.os.replace = busy_replace
try:
    dd = reg()
    D._wg_prepare()
    W.registry_write(dd)
    check("21 занятый реестр: записан после повторов", busy[0] == 0 and reg() == dd)
finally:
    W.os.replace = real_replace

# 24. Исправления после ревью 10.10 (F1–F7 + находки критика)
# F1: снятие заворота не удалось -> метки save нет; интерфейс потом удалён в CLI без save + чужое
#     несохранённое -> повторный отзыв save не делает
d = post("/api/xkeen/wg-issue", {"name": "Phone F1", "exit": "xray", "create": True})
kf1 = next(k for k, v in R.ifaces.items() if v["desc"] == "Phone F1")
cf1 = next(k for k, v in reg().items() if v["name"] == "Phone F1")
R.fail.add("xray_off")
d = post("/api/xkeen/wg-revoke", {"id": cf1})
R.fail.discard("xray_off")
check("24 F1 сбой снятия заворота: отказ без метки save", not d.get("ok") and not reg()[cf1].get("router_dirty"), d)
R.hooks.discard(int(kf1[9:]))
del R.ifaces[kf1]                                     # удалили в Keenetic через CLI, без save
R.add("Wireguard31", "CLI-unsaved-2", "172.173.231.1", 48098, peers={"CLIPUB2=": None})
d = post("/api/xkeen/wg-revoke", {"id": cf1})
check("24 F1 повтор: отозван, чужое несохранённое НЕ закреплено", d.get("ok") and "CLI-unsaved-2" not in R.saved, d)
del R.ifaces["Wireguard31"]
R.save()

# F2: удаление сорвалось -> снятый заворот возвращается (или честно сказано, что нет)
d = post("/api/xkeen/wg-issue", {"name": "Phone F2", "exit": "xray", "create": True})
kf2 = next(k for k, v in R.ifaces.items() if v["desc"] == "Phone F2")
cf2 = next(k for k, v in reg().items() if v["name"] == "Phone F2")
R.fail.add("noiface")
d = post("/api/xkeen/wg-revoke", {"id": cf2})
check("24 F2 не удалился: заворот возвращён", not d.get("ok") and int(kf2[9:]) in R.hooks
      and "заворот в xray возвращён" in d.get("error", ""), d)
R.fail.add("xray_on")
d = post("/api/xkeen/wg-revoke", {"id": cf2})
R.fail.discard("xray_on")
check("24 F2 возврат не удался: честное предупреждение", not d.get("ok") and "НЕ возвращён" in d.get("error", ""), d)
R.fail.discard("noiface")
post("/api/xkeen/wg-revoke", {"id": cf2})
check("24 F2 повтор: удалён", kf2 not in R.ifaces)

# F3: решение о завороте — по итоговому режиму; непрочитанный хук не молчит
d = post("/api/xkeen/wg-issue", {"name": "Dacha F3", "s2s": True, "remote_lan": "192.168.70.0/24", "full": True, "create": True})
kf3 = next(k for k, v in R.ifaces.items() if v["desc"] == "Dacha F3")
d = post("/api/xkeen/wg-acl-apply", {"iface_id": kf3, "exit": "xray", "full": True})
check("24 F3 s2s + exit=xray по API: заворот НЕ ставится", d.get("ok") and int(kf3[9:]) not in R.hooks, d)
R.hooks.add(int(kf3[9:]))
d = post("/api/xkeen/wg-acl-apply", {"iface_id": kf3, "exit": "xray", "full": True})
check("24 F3 s2s с хуком: хук снят (итоговый режим — только ЛВС)", d.get("ok") and int(kf3[9:]) not in R.hooks, d)
post("/api/xkeen/wg-revoke", {"id": next(k for k, v in reg().items() if v["name"] == "Dacha F3")})
d = post("/api/xkeen/wg-issue", {"name": "Laptop F3", "exit": "direct", "create": True})
kf3b = next(k for k, v in R.ifaces.items() if v["desc"] == "Laptop F3")
R.fail.add("hookread")
d1 = post("/api/xkeen/wg-acl-apply", {"iface_id": kf3b, "exit": "direct"})
d2 = post("/api/xkeen/wg-acl-apply", {"iface_id": kf3b, "exit": "xray"})
R.fail.discard("hookread")
check("24 F3 хук не прочитан + direct: предупреждение", d1.get("ok") and "не прочитано" in d1.get("msg", ""), d1)
check("24 F3 хук не прочитан + xray: заворот включён", d2.get("ok") and int(kf3b[9:]) in R.hooks
      and "Заворот в xray включён" in d2.get("msg", ""), d2)

# F4: форма правил — режим по факту заворота
f = c.get(f"/api/xkeen/wg-acl?iface_id={kf3b}").get_json()
check("24 F4 в реестре direct, но заворот включён -> форма предлагает xray", f.get("ok") and f["pre"]["exit"] == "xray"
      and f.get("xray_on") is True, f.get("pre"))
R.add("Wireguard33", "NAT-and-hook", "172.173.233.1", 48097, peers={"NHPUB=": None})
R.nat.add("Wireguard33")
R.hooks.add(33)
f = c.get("/api/xkeen/wg-acl?iface_id=Wireguard33").get_json()
check("24 F4 не из реестра, NAT + хук -> xray", f.get("ok") and f["pre"]["exit"] == "xray", f.get("pre"))
R.hooks.discard(33)
f = c.get("/api/xkeen/wg-acl?iface_id=Wireguard33").get_json()
check("24 F4 не из реестра, только NAT -> direct", f.get("ok") and f["pre"]["exit"] == "direct" and f.get("xray_on") is False, f.get("pre"))
del R.ifaces["Wireguard33"]
R.nat.discard("Wireguard33")

# F5: адрес подключения не определён -> выдача остановлена, роутер не тронут
saved_get, saved_wan = R.get, W._WAN["host"]


def get_no_wan(path):
    out = saved_get(path)
    if path == "/rci/show/interface":
        j = json.loads(out)
        j["PPPoE0"].pop("defaultgw", None)
        out = json.dumps(j)
    return out


R.get = get_no_wan
W._WAN["host"] = ""
n = len(R.calls)
before_reg = json.dumps(reg(), sort_keys=True)
d = post("/api/xkeen/wg-issue", {"name": "No WAN", "exit": "direct", "create": True})
check("24 F5 нет адреса: отказ, реестр и роутер не тронуты", not d.get("ok") and "WG_ENDPOINT_HOST" in d.get("error", "")
      and json.dumps(reg(), sort_keys=True) == before_reg and not any("base64 -d | curl" in x for x in R.calls[n:]), d)
D.cfg.WG_ENDPOINT_HOST = "vpn.example.org"
d = post("/api/xkeen/wg-issue", {"name": "With DDNS", "exit": "direct", "create": False})
check("24 F5 задан WG_ENDPOINT_HOST: выдача идёт", d.get("ok") and "Endpoint = vpn.example.org:" in d.get("conf", ""), d.get("error"))
D.cfg.WG_ENDPOINT_HOST = ""
R.get, W._WAN["host"] = saved_get, saved_wan
post("/api/xkeen/wg-revoke", {"id": next(k for k, v in reg().items() if v["name"] == "With DDNS")})

# F6: правила подмены со старыми LANNET/LANIF снимаются; мусор из хука в команду не попадает
D._wg_prepare()
R.masq = "Wireguard40=172.173.240.0/24"
R.masq_old = "N 10.20.0.0/16\nI br1\n"
W.lan_masq_set("Wireguard41", ["172.173.241.0/24"])
check("24 F6 старые LANNET/LANIF сняты", "-d 10.20.0.0/16 -o br1" in R.last_masq_script
      and "-s 172.173.240.0/24 -d 10.20.0.0/16" in R.last_masq_script, R.last_masq_script[:300])
R.masq = "Wireguard40=172.173.240.0/24"
R.masq_old = "N 1.2.3.0/24;reboot\nI br0\n"
W.lan_masq_set("Wireguard41", [])
check("24 F6 мусор в LANNET в команду не попал", "reboot" not in R.last_masq_script)
R.masq, R.masq_old = None, ""
D._WG_LAN_CACHE.update(ts=0.0, val=("10.0.0.0/16", "br0"))
saved_ssh = D.keenetic_ssh
D.cfg.KEENETIC_HOST, saved_host = "10.0.0.1", getattr(D.cfg, "KEENETIC_HOST", "")
D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None: _res(255, "", "timeout")
check("24 F6 сбой SSH: последнее удачное определение ЛВС, а не /24", D._keenetic_lan_net() == ("10.0.0.0/16", "br0"))
D.keenetic_ssh = saved_ssh
D.cfg.KEENETIC_HOST = saved_host
D._WG_LAN_CACHE.update(ts=0.0, val=None)

# F7: cookie сессии с SameSite=Lax
c3 = D.app.test_client()
r = c3.post("/login", data={"username": D.cfg.USERNAME, "password": D.cfg.PASSWORD})
cookies = r.headers.getlist("Set-Cookie")
check("24 F7 SameSite=Lax задан", D.app.config.get("SESSION_COOKIE_SAMESITE") == "Lax"
      and cookies and all("SameSite=Lax" in x for x in cookies), cookies)

# Критик: снятие заворота у выключенного интерфейса (без адреса) — подсеть из самого хука
captured = []
saved_gwi, saved_ssh = D.keenetic_get_wireguard_interfaces, D.keenetic_ssh
D.keenetic_get_wireguard_interfaces = lambda: {"ok": True, "interfaces": [
    {"id": "Wireguard6", "kernel_name": "nwg6", "subnet_cidr": None}]}
D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None: (captured.append(cmd), _res(0, "WG_GONE\n"))[1]
dd = REAL_XRAY_DISCONNECT("Wireguard6")
D.keenetic_get_wireguard_interfaces, D.keenetic_ssh = saved_gwi, saved_ssh
check("24 критик: заворот снимается у интерфейса без адреса", dd is not None and dd.get("ok")
      and "SUBNET=$(sed -n 's/^SUBNET=//p' /opt/etc/ndm/netfilter.d/zz_wg_xray_nwg6.sh" in (captured[0] if captured else ""), dd)

# 25. Исправления после проверки граничных случаев
# хука подмены ещё нет — это пустая карта, а не сбой (иначе хук не создавался бы никогда)
D._wg_prepare()
R.masq, R.masq_old = None, ""
check("25 нет хука подмены: пустая карта, не сбой", W._masq_hook_state() == ({}, "", ""), W._masq_hook_state())
check("25 нет хука: подмена ставится с нуля", W.lan_masq_set("Wireguard45", ["172.173.245.0/24"]) is None
      and mask_of("Wireguard45").get("Wireguard45") == ["172.173.245.0/24"], R.masq)
W.lan_masq_set("Wireguard45", [])
check("25 последний убран: хук удалён", R.masq is None)
# SSH-порт панели к роутеру закрыт на своём .1 в режиме xray
saved_port = getattr(D.cfg, "KEENETIC_PORT", 222)
D.cfg.KEENETIC_PORT = 2222
D._wg_prepare()
L = W.acl_lines([], "xray", "172.173.4.1")
check("25 xray: свой SSH-порт роутера закрыт", "deny tcp 0.0.0.0 0.0.0.0 172.173.4.1 255.255.255.255 port eq 2222" in L)
D.cfg.KEENETIC_PORT = saved_port
# ЛВС: адрес KEENETIC_HOST на туннельном интерфейсе / имя -> мост br0
saved_ssh, saved_host = D.keenetic_ssh, D.cfg.KEENETIC_HOST
for host, out, want in (
        ("10.8.0.1", "H nwg3 10.8.0.1/24\nB br0 192.168.40.1/24\n", ("192.168.40.0/24", "br0")),
        ("router.example", "B br0 192.168.41.1/24\n", ("192.168.41.0/24", "br0")),
        ("192.168.42.1", "H br0 192.168.42.1/23\nB br0 192.168.42.1/23\n", ("192.168.42.0/23", "br0")),
        ("203.0.113.9", "B br0 10.20.30.1/24\nH eth3 203.0.113.9/24\n", ("10.20.30.0/24", "br0"))):
    D.cfg.KEENETIC_HOST = host
    D._WG_LAN_CACHE.update(ts=0.0, val=None)
    D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None, _o=out: _res(0, _o)
    check(f"25 ЛВС при KEENETIC_HOST={host}", D._keenetic_lan_net() == want, D._keenetic_lan_net())
D.keenetic_ssh, D.cfg.KEENETIC_HOST = saved_ssh, saved_host
D._WG_LAN_CACHE.update(ts=0.0, val=None)

# 26. После повторной проверки: ЛВС не прочитана -> запись на роутер остановлена, не угадывается
saved_ssh, saved_lan = D.keenetic_ssh, D.cfg.WG_LAN_SUBNET
D.cfg.WG_LAN_SUBNET = ""
D._WG_LAN_CACHE.update(ts=0.0, val=None)
D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None: (_res(255, "", "timeout") if cmd.startswith("ip -o -4 addr show")
                                                           else saved_ssh(cmd, timeout=timeout, stdin_data=stdin_data))
n = len(R.calls)
before_ifaces = sorted(R.ifaces)
d = post("/api/xkeen/wg-issue", {"name": "No LAN", "exit": "direct", "create": True})
check("26 ЛВС не прочитана: выдача отказана, роутер не тронут", not d.get("ok") and "ЛВС роутера не определена" in d.get("error", "")
      and sorted(R.ifaces) == before_ifaces and not any("base64 -d | curl" in x for x in R.calls[n:]), d)
d = post("/api/xkeen/wg-acl-apply", {"iface_id": "Wireguard2", "exit": "direct"})
check("26 ЛВС не прочитана: правила отказаны", not d.get("ok") and "ЛВС роутера не определена" in d.get("error", ""), d)
lst = c.get("/api/xkeen/wg-clients").get_json()
check("26 список: предупреждение и ЛВС с «?»", lst.get("lan", "").endswith(" ?") and "не определена" in lst.get("lan_warn", ""), lst.get("lan"))
inv = c.get("/api/xkeen/wg-router").get_json()
it = {i["iface"]: i for i in inv.get("items", [])}
check("26 ЛВС-догадка: доступ НЕ превращается в линк, conf-шаблон есть, предупреждение есть",
      not it["Wireguard2"]["is_link"] and it["Wireguard2"]["conf_tpl"] and "не определена" in inv.get("lan_warn", ""), it.get("Wireguard2"))
f = c.get("/api/xkeen/wg-acl?iface_id=Wireguard2").get_json()
check("26 форма правил открывается с предупреждением", f.get("ok") and "не определена" in f.get("lan_warn", ""), f.get("error"))
check("26 _keenetic_lan_net(guess=False) без кэша: None", D._keenetic_lan_net(guess=False) == (None, "br0"))
D.keenetic_ssh, D.cfg.WG_LAN_SUBNET = saved_ssh, saved_lan
D._WG_LAN_CACHE.update(ts=0.0, val=None)
# хук заворота не записался -> не «поставлен»
saved_rp, saved_ssh = D._wg_resolve_params, D.keenetic_ssh
D._wg_resolve_params = lambda iid: ({"kernel": "nwg7", "subnet": "172.173.7.0/24", "lan_ip": "192.168.1.1", "dns_port": "41102",
                                     "mss": 1240, "lan_net": LAN, "lan_if": "br0"}, None)
cap = []
D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None: (cap.append(cmd), _res(1, "WG_CONN_WRITEFAIL\n"))[1]
dx = REAL_XRAY_CONNECT("Wireguard7")
check("26 хук не записан: ok=False", dx.get("ok") is False and "не записан" in dx.get("error", ""), dx)
check("26 команда: запись — условие для остального", cap and "|| { rm -f" in cap[0] and "echo WG_CONN_WRITEFAIL; exit 1; }" in cap[0])
D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None: _res(0, "WG_CONN_OK\n")
check("26 хук записан и правило встало: ok", REAL_XRAY_CONNECT("Wireguard7").get("connected") is True)
D._wg_resolve_params, D.keenetic_ssh = saved_rp, saved_ssh

# 22. ключи X25519 совпадают с эталоном RFC 7748 (вектор 6.1)
alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
k = bytearray(alice)
k[0] &= 248
k[31] &= 127
k[31] |= 64
pub = W.x25519(int.from_bytes(k, "little"), 9).to_bytes(32, "little").hex()
check("22 X25519 = RFC 7748", pub == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a", pub)

# 23. ответы и страницы
for u in ["/api/xkeen/wg-clients", "/api/xkeen/wg-router", f"/api/xkeen/wg-backup?iface={kx}", f"/api/xkeen/wg-acl?iface_id={kx}"]:
    r = c.get(u)
    check(f"23 GET {u.split('?')[0]} -> 200", r.status_code == 200, r.status_code)
r = c.get("/api/xkeen/wg-backup?iface=../etc")
check("23 бэкап: некорректный интерфейс -> 400", r.status_code == 400)
D.is_keenetic_reachable = lambda *a, **kw: False
D.keenetic_ssh = lambda cmd, timeout=15, stdin_data=None: _res(1, "")   # quick-fail: роутер «молчит»
html = c.get("/xkeen").get_data(as_text=True)
check("23 /xkeen рендерится с новым разделом", "Ошибка при рендеринге" not in html and 'id="wgiBtn"' in html
      and 'id="wgrList"' in html and 'id="wgRulesDlg"' in html and "wg-new-client" not in html
      and "data-backup=" in html and "function backupDl" in html)

print(f"\ntest_wg_clients_offline: {'OK' if not fails else 'FAIL'} (провалов: {fails}) | реестр во временной папке {TMP}")
sys.exit(1 if fails else 0)
