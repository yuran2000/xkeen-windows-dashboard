# -*- coding: utf-8 -*-
"""Офлайн-проверки генератора kill-switch (без роутера и Flask).

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_killswitch.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xkeen_killswitch as K  # noqa: E402

fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}: {detail}")


# --- метка политики ---
check("normalize_mark hex", K.normalize_mark("ffffaac") == "0xffffaac")
check("normalize_mark 0x", K.normalize_mark("0xFFFFAAC") == "0xffffaac")
check("normalize_mark junk", K.normalize_mark("zz") is None and K.normalize_mark("") is None)
check("mark_to_decimal", K.mark_to_decimal("0xffffaac") == 268434092)

rci = {
    "Policy1": {"description": "hidemy", "mark": "ffffaaa", "table4": 4096},
    "Policy2": {"description": "XKeen", "mark": "ffffaac", "table4": 4100},
    "Policy3": {"description": "xkeen-old", "mark": "ffffaad"},
}
pol = K.parse_policy(rci, "xkeen")
check("parse_policy found", pol and pol["id"] == "Policy2" and pol["mark"] == "0xffffaac" and pol["table4"] == 4100, str(pol))
check("parse_policy case-insensitive", K.parse_policy({"P": {"description": "xKeEn", "mark": "1"}})["mark"] == "0x1")
check("parse_policy missing", K.parse_policy({"P": {"description": "Other", "mark": "1"}}) is None)
check("parse_policy bad mark", K.parse_policy({"P": {"description": "XKeen", "mark": "nope"}}) is None)
check("parse_policy not dict", K.parse_policy(None) is None and K.parse_policy([]) is None)

# --- порты ---
check("ports empty → 80,443", K.parse_ports_lst("") == ["80,443"])
check("ports dedup", K.parse_ports_lst("443\n80\n") == ["80,443"])
check("ports range/comments/garbage", K.parse_ports_lst("8080\n10000-10100 # x\n#c\n443,80\n70000\nabc\n") == ["80,443,8080,10000:10100"])
many = "\n".join(str(p) for p in range(1000, 1020))
chunks = K.parse_ports_lst(many)
check("ports chunked ≤15", all(len(c.split(",")) <= 15 for c in chunks) and len(chunks) == 2, str(chunks))
check("ports range weight", len(K.parse_ports_lst("\n".join(f"{p}-{p+1}" for p in range(1000, 1030, 2)))) >= 2)

# --- хук ---
hook = K.build_hook("ffffaac", ["80,443"], "ppp0", mode="protect", ipv6=True)
for needle in [
    "#!/bin/sh", "KS_VERSION=1", "KS_MODE=protect", "KS_MARK=0xffffaac", 'KS_PORTS="80,443"', "KS_WAN=ppp0", "KS_V6=1",
    "-m connmark --mark $KS_MARK ", "--ctstate DNAT -j RETURN", "--dscp 62 -j RETURN",
    "--match-set user_exclude${sfx} dst", "! --match-set geo_override${sfx} dst", "--match-set ext_exclude${sfx} dst",
    "ipset create ${s}${sfx} hash:net family $fam -exist", "ipset create ext_exclude${sfx} hash:ip family $fam -exist",
    "--ctstate NEW -m comment --comment ${T}_act -j $act", "act=DROP; [ \"$KS_MODE\" = protect ] || act=RETURN",
    "-I FORWARD 1", "zz_wg_xray_", "ip route show default", "case \"$type\" in", "exit 0",
]:
    check(f"hook contains {needle!r}", needle in hook)
check("hook no triple quotes", '"""' not in hook)
check("hook LF only", "\r" not in hook)
check("hook v6 disabled", "KS_V6=0" in K.build_hook("ffffaac", ["80,443"], "ppp0", ipv6=False))
check("hook observe", "KS_MODE=observe" in K.build_hook("ffffaac", ["80,443"], "ppp0", mode="observe"))
for bad in [dict(mark="zz"), dict(wan_if="bad iface"), dict(wan_if=""), dict(mode="drop"), dict(port_chunks=["80;443"]), dict(port_chunks=[])]:
    kw = dict(mark="ffffaac", port_chunks=["80,443"], wan_if="ppp0", mode="protect")
    kw.update(bad)
    try:
        K.build_hook(**kw)
        check(f"build_hook rejects {bad}", False, "не выбросил ValueError")
    except ValueError:
        pass

params = K.parse_hook_params(hook)
check("parse_hook_params", params == {"KS_VERSION": "1", "KS_MODE": "protect", "KS_MARK": "0xffffaac",
                                      "KS_PORTS": "80,443", "KS_WAN": "ppp0", "KS_V6": "1"}, str(params))
sig = K.params_signature("ffffaac", ["80,443"], "ppp0", "protect", True)
check("signature == hook params", sig == params, f"{sig} vs {params}")

# --- команды ---
inst = K.install_command()
check("install: sh -n before mv", inst.index("sh -n") < inst.index("mv "))
check("install: cron dedup", "grep -vF" in inst and K.CRON_LINE in inst and "KS_INSTALL_OK" in inst)
rem = K.remove_command()
check("remove: file, cron, chain", K.HOOK_PATH in rem and "-X xkeen_ks" in rem and "KS_REMOVE_OK" in rem)

# --- статус ---
st = K.parse_status(
    "hook_exists=1\nhook_exec=1\ncron=1\nwan=ppp0\nwgs=nwg2,nwg6,\n"
    "iptables_present=1\niptables_jumps=3\niptables_first=1\niptables_chain_rules=17\niptables_act=DROP\n"
    "ip6tables_present=1\nip6tables_jumps=1\nip6tables_first=1\nip6tables_chain_rules=15\nip6tables_act=DROP\n"
    "---HOOK_HEAD---\n#!/bin/sh\nKS_VERSION=1\nKS_MODE=protect\nKS_MARK=0xffffaac\nKS_PORTS=\"80,443\"\nKS_WAN=ppp0\nKS_V6=1\n")
check("status ints", st["iptables_jumps"] == 3 and st["hook_exec"] == 1 and st["cron"] == 1)
check("status wgs", st["wgs"] == ["nwg2", "nwg6"])
check("status hook params", st["hook_params"]["KS_PORTS"] == "80,443" and st["hook_params"]["KS_MODE"] == "protect")
check("status empty", K.parse_status("")["hook_params"] == {} and K.parse_status("")["wgs"] == [])

# --- утечки ---
ls = K.leaks_script("ffffaac", ["80,443,10000:10100"], wan_ip="203.0.113.7")
check("leaks: decimal mark", "M=268434092" in ls)
check("leaks: ports cond", "dp==80" in ls and "dp==443" in ls and "dp>=10000 && dp<=10100" in ls)
check("leaks: wan ip passed", 'WAN="203.0.113.7"' in ls and "excluded(dst)" in ls and '$1=="ipv4"' in ls)
check("leaks: bad wan ip ignored", 'WAN=""' in K.leaks_script("ffffaac", ["80"], wan_ip="not-an-ip"))
check("leaks parse", K.parse_leaks("1.2.3.4 443 tcp ESTABLISHED\n5.6.7.8 80 udp\ncount=2") == {"count": 2, "samples": ["1.2.3.4 443 tcp ESTABLISHED", "5.6.7.8 80 udp"]})
check("leaks parse empty", K.parse_leaks("") == {"count": 0, "samples": []})

if fails:
    print("FAIL:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("test_killswitch: OK")
