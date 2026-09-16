# -*- coding: utf-8 -*-
"""Офлайн-тест синхронизации IP VPN-эндпоинтов в routing-template (v13: два IPv4-массива —
direct-правило и «QUIC (UDP 443) → напрямую») + резолв доменных адресов outbounds.
Без роутера и сети (DNS замокан).

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_template_ips.py
"""
import json
import os
import re
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        fails += 1


CUR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "bootstrap", "05_routing.template.json.cur")
raw = open(CUR, encoding="utf-8", newline="").read()

print("Эталонный шаблон:")
check(raw.startswith("// Watchdog template v13"), "заголовок template v13")
clean = D._strip_json_comments(raw)
clean = re.sub(r'^\s*__[A-Z0-9_]+__\s*$', '', clean, flags=re.MULTILINE)
tmpl = json.loads(clean)
rules = tmpl["routing"]["rules"]
quic = [r for r in rules if r.get("network") == "udp" and str(r.get("port")) == "443"]
check(len(quic) == 2, "два правила UDP 443: direct к эндпоинтам + block")
check(quic[0]["outboundTag"] == "direct" and quic[0].get("ip") and quic[1]["outboundTag"] == "block",
      "порядок: сначала «QUIC → напрямую» (с ip), потом блок")
direct_rule = next(r for r in rules if r.get("ip") and not r.get("network"))
check(sorted(quic[0]["ip"]) == sorted(direct_rule["ip"]), "список IP в QUIC-правиле = список direct-правила")
arrays = D._template_ip_arrays(raw)
check(len(arrays) == 2, f"_template_ip_arrays находит ровно 2 IPv4-массива (нашёл {len(arrays)})")

print("Замена массивов:")
new_text, current = D._template_replace_ip_arrays(raw, {"203.0.113.7", "198.51.100.9"})
check(current == set(direct_rule["ip"]), "current_ips = объединение массивов")
clean2 = re.sub(r'^\s*__[A-Z0-9_]+__\s*$', '', D._strip_json_comments(new_text), flags=re.MULTILINE)
tmpl2 = json.loads(clean2)
ipsets = [sorted(r["ip"]) for r in tmpl2["routing"]["rules"] if r.get("ip") and all(D._IPV4_LITERAL_RE.match(x) for x in r["ip"])]
check(ipsets == [["198.51.100.9", "203.0.113.7"]] * 2, "оба массива переписаны одним списком, JSON валиден")
check(len(tmpl2["routing"]["rules"]) == len(rules), "число правил не изменилось")
check("__DEFAULT_TAG__" in new_text and "__BLOCK_RULE_BLOCK__" in new_text, "placeholder'ы не тронуты")
geo_raw = '{"routing":{"rules":[{"ip":["geoip:private"],"outboundTag":"direct"},{"ip":["10.0.0.0/8"],"outboundTag":"direct"}]}}'
check(D._template_ip_arrays(geo_raw) == [], "geoip:/CIDR-массивы не считаются списком эндпоинтов")

print("IP эндпоинтов из outbounds (DNS замокан):")
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *a, **k: (
    [(2, 1, 6, "", ("192.0.2.10", 0)), (2, 1, 6, "", ("192.0.2.11", 0))] if host == "vpn.example.com"
    else (_ for _ in ()).throw(socket.gaierror("mock"))
)
ob = {"outbounds": [
    {"protocol": "vless", "settings": {"vnext": [{"address": "vpn.example.com"}]}},
    {"protocol": "vless", "settings": {"vnext": [{"address": "203.0.113.5"}]}},
    {"protocol": "trojan", "settings": {"servers": [{"address": "dead.example.invalid"}]}},
    {"protocol": "freedom", "settings": {}},
]}
ips, resolved = D._endpoint_ipv4s_from_outbounds(ob)
socket.getaddrinfo = _orig
check(ips == {"192.0.2.10", "192.0.2.11", "203.0.113.5"}, f"литералы + резолв домена ({sorted(ips)})")
check(resolved.get("dead.example.invalid") == [], "нерезолвящийся домен → пусто, без исключения")

print("\nИТОГ:", "ВСЁ ЗЕЛЁНОЕ" if fails == 0 else f"ПРОВАЛОВ: {fails}")
sys.exit(1 if fails else 0)
