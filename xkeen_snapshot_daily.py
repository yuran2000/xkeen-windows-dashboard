"""Ежедневный snapshot XKeen-конфигов с роутера.
Запускается Task Scheduler'ом (раз в день) — настройка в README.md.
Не зависит от Flask/HTTP — открывает SSH напрямую через те же креды что dashboard.

Формат: v7 ({version, timestamp, files: [{path, content}, ...]})
Совместим с /api/xkeen/restore. Список путей — общий с панелью (xkeen_backup_paths.py),
чтобы ежедневный snapshot не отставал от ручного.

Retention: хранятся последние 14 ЕЖЕДНЕВНЫХ снапшотов (xkeen-snapshot-ГГГГММДД-ЧЧММСС.json),
старые удаляются. Ручные и именованные (xkeen-snapshot-manual-*, -pre-*, -v7-* и т.п.)
ретеншн не трогает — их удаляет только пользователь.
"""
import os
import sys
import subprocess
import time
import glob
import re

import xkeen_backup_paths
import ssh_bin

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
    print("Убедиться что рядом со скриптом лежит config_local.py с переменными KEENETIC_HOST/PORT/USER/SSH_KEY.", file=sys.stderr)
    sys.exit(1)

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
RETENTION = 14
DAILY_NAME_RE = re.compile(r"^xkeen-snapshot-\d{8}-\d{6}\.json$")



def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    remote_cmd = xkeen_backup_paths.build_snapshot_command()
    ssh = [
        ssh_bin.ssh_exe(), "-i", SSH_KEY, "-p", str(SSH_PORT),
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=" + ssh_bin.null_known_hosts(),
        "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
        f"{SSH_USER}@{SSH_HOST}", remote_cmd,
    ]
    r = subprocess.run(ssh, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
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

    # Ретеншн только по ежедневным файлам этого скрипта. Ручные/именованные снапшоты
    # (…-manual-…, …-pre-…) в счёт не идут: иначе при 14+ ручных файлах имя ежедневного
    # (цифра < буквы в sorted) оказывалось первым и удалялось сразу после создания.
    daily = [
        f for f in glob.glob(os.path.join(BACKUP_DIR, "xkeen-snapshot-*.json"))
        if DAILY_NAME_RE.match(os.path.basename(f))
    ]
    snapshots = sorted(daily)  # имя = метка времени → сортировка по имени = по времени
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
