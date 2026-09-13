# -*- coding: utf-8 -*-
"""Офлайн-проверки общего списка путей snapshot (без роутера).

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_backup_paths.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xkeen_backup_paths as B  # noqa: E402

fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}: {detail}")


check("static has core files", all(p in B.STATIC_FILES for p in [
    "/opt/etc/xray/configs/04_outbounds.json", "/opt/etc/xray/watchdog.sh", "/opt/etc/xray/watchdog.config",
    "/opt/etc/xray/watchdog.aiprobe", "/opt/etc/xray/outbound_meta.json", "/opt/etc/xray/subscription_meta.json",
    "/opt/etc/xray/domain_notes.json", "/opt/var/spool/cron/crontabs/root", "/opt/etc/dropbear/authorized_keys"]))
check("static unique", len(B.STATIC_FILES) == len(set(B.STATIC_FILES)))
check("restorable static", B.is_restorable("/opt/etc/xray/watchdog.config"))
check("restorable hook", B.is_restorable("/opt/etc/ndm/netfilter.d/zz_xkeen_killswitch.sh"))
check("restorable wg hook", B.is_restorable("/opt/etc/ndm/netfilter.d/zz_wg_xray_nwg2.sh"))
for bad in ["/opt/etc/ndm/netfilter.d/proxy.sh", "/opt/etc/ndm/netfilter.d/zz_x/../../passwd.sh",
            "/opt/etc/ndm/netfilter.d/zz_a b.sh", "/etc/passwd", "/opt/etc/init.d/S05xkeen", "", None,
            "/opt/etc/ndm/netfilter.d/zz_evil.sh;rm -rf /"]:
    check(f"not restorable {bad!r}", not B.is_restorable(bad))
check("exec bit sh", B.needs_exec_bit("/opt/etc/xray/watchdog.sh") and B.needs_exec_bit("/opt/etc/ndm/netfilter.d/zz_a.sh"))
check("exec bit json", not B.needs_exec_bit("/opt/etc/xray/configs/05_routing.json"))

cmd = B.build_snapshot_command()
check("cmd has all static", all(f'"{p}"' in cmd for p in B.STATIC_FILES))
check("cmd has glob loop", "for f in /opt/etc/ndm/netfilter.d/zz_*.sh; do" in cmd and '--rawfile c "$f"' in cmd)
check("cmd v7 envelope", 'version: "v7"' in cmd and "timestamp: $ts" in cmd and "jq -s" in cmd)
check("cmd single line", "\n" not in cmd)

if fails:
    print("FAIL:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("test_backup_paths: OK")
