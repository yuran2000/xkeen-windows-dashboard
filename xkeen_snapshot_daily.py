"""Ежедневный snapshot XKeen-конфигов с роутера.
Запускается Task Scheduler'ом (раз в день) — настройка в README.md.
Не зависит от Flask/HTTP — открывает SSH напрямую через те же креды что dashboard.

Формат: v7 ({version, timestamp, files: [{path, content}, ...]})
Совместим с /api/xkeen/restore.

Retention: последние 14 файлов (старые удаляются).
"""
import os
import sys
import subprocess
import time
import glob

# Читаем настройки из config_local.py (тот же что dashboard.py использует).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import config_local as cfg
    SSH_KEY  = cfg.KEENETIC_SSH_KEY
    SSH_HOST = cfg.KEENETIC_HOST
    SSH_PORT = cfg.KEENETIC_PORT
    SSH_USER = cfg.KEENETIC_USER
except Exception as ex:
    print(f"[FATAL] Не удалось загрузить config_local.py: {ex}", file=sys.stderr)
    print("Убедись что рядом со скриптом лежит config_local.py с переменными KEENETIC_HOST/PORT/USER/SSH_KEY.", file=sys.stderr)
    sys.exit(1)

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
RETENTION = 14

FILES = [
    "/opt/etc/xray/configs/01_log.json",
    "/opt/etc/xray/configs/02_dns.json",
    "/opt/etc/xray/configs/03_inbounds.json",
    "/opt/etc/xray/configs/04_outbounds.json",
    "/opt/etc/xray/configs/05_routing.json",
    "/opt/etc/xray/configs/06_policy.json",
    "/opt/etc/xray/configs.bak/05_routing.template.json",
    "/opt/etc/xray/configs.bak/05_routing.primary.json",
    "/opt/etc/xray/configs.bak/05_routing.failover.json",
    "/opt/etc/xray/configs.bak/05_routing.original-balancer.json",
    "/opt/etc/xray/configs.bak/07_observatory.disabled.json",
    "/opt/etc/xray/watchdog.sh",
    "/opt/etc/xray/watchdog.config",
    "/opt/etc/xray/watchdog.state",
    "/opt/var/spool/cron/crontabs/root",
    "/opt/etc/dropbear/authorized_keys",
]


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    parts = [
        f'[ -f "{p}" ] && jq -n --arg p "{p}" --rawfile c "{p}" \'{{path: $p, content: $c}}\''
        for p in FILES
    ]
    remote_cmd = "(" + " ; ".join(parts) + ') | jq -s --arg ts "$(date)" \'{version: "v7", timestamp: $ts, files: .}\''
    ssh = [
        "ssh", "-i", SSH_KEY, "-p", str(SSH_PORT),
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL",
        "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
        f"{SSH_USER}@{SSH_HOST}", remote_cmd,
    ]
    r = subprocess.run(ssh, capture_output=True, timeout=60)
    if r.returncode != 0:
        sys.stderr.write(f"SSH failed (code {r.returncode}): {r.stderr.decode('utf-8', errors='replace')[:500]}\n")
        sys.exit(1)
    if not r.stdout.strip():
        sys.stderr.write("Empty output from router\n")
        sys.exit(2)

    ts = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(BACKUP_DIR, f"xkeen-snapshot-{ts}.json")
    with open(out, "wb") as f:
        f.write(r.stdout)
    size_kb = os.path.getsize(out) / 1024
    print(f"[{ts}] Saved {out} ({size_kb:.1f} KB)")

    snapshots = sorted(glob.glob(os.path.join(BACKUP_DIR, "xkeen-snapshot-*.json")))
    deleted = 0
    for old in snapshots[:-RETENTION]:
        try:
            os.remove(old)
            deleted += 1
        except OSError as e:
            sys.stderr.write(f"Cannot delete {old}: {e}\n")
    if deleted:
        print(f"Retention: deleted {deleted} old snapshot(s), kept {RETENTION}")


if __name__ == "__main__":
    main()
