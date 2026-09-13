"""Единый список путей роутера, которые попадают в snapshot XKeen.

Используется и панелью (dashboard.py: /api/xkeen/backup, backup-to-disk, restore),
и ежедневным xkeen_snapshot_daily.py — раньше у каждого был свой список, и они
разъезжались (daily собирал 16 путей, панель — 19; sidecar-файлы выпадали из
ежедневных снапшотов). Теперь источник один.

Формат snapshot не меняется (v7: {version, timestamp, files: [{path, content}]}),
старые файлы восстанавливаются как прежде.
"""
import re

SNAPSHOT_VERSION = "v7"

# Фиксированные пути — существуют на любом роутере с XKeen + панелью.
STATIC_FILES = [
    # Активные xray-конфиги (то, что xray читает прямо сейчас)
    "/opt/etc/xray/configs/01_log.json",
    "/opt/etc/xray/configs/02_dns.json",
    "/opt/etc/xray/configs/03_inbounds.json",
    "/opt/etc/xray/configs/04_outbounds.json",
    "/opt/etc/xray/configs/05_routing.json",
    "/opt/etc/xray/configs/06_policy.json",
    # Шаблоны и backup-конфиги watchdog
    "/opt/etc/xray/configs.bak/05_routing.template.json",
    "/opt/etc/xray/configs.bak/05_routing.primary.json",
    "/opt/etc/xray/configs.bak/05_routing.failover.json",
    "/opt/etc/xray/configs.bak/05_routing.original-balancer.json",
    "/opt/etc/xray/configs.bak/07_observatory.disabled.json",
    # Watchdog: скрипт, конфиг, состояние, результат сквозной AI-пробы
    "/opt/etc/xray/watchdog.sh",
    "/opt/etc/xray/watchdog.config",
    "/opt/etc/xray/watchdog.state",
    "/opt/etc/xray/watchdog.aiprobe",
    # Sidecar-файлы панели (имена провайдеров/подписок, примечания к доменам)
    "/opt/etc/xray/outbound_meta.json",
    "/opt/etc/xray/subscription_meta.json",
    "/opt/etc/xray/domain_notes.json",
    # Cron-задачи (watchdog, xkeen autoupdate, self-heal хуков)
    "/opt/var/spool/cron/crontabs/root",
    # SSH authorized keys
    "/opt/etc/dropbear/authorized_keys",
]

# Динамические пути — набор файлов известен только на роутере: хуки netfilter.d, которые
# ставит панель (zz_wg_xray_<iface>.sh, zz_xkeen_killswitch.sh) и любые пользовательские zz_*.sh.
# Без них восстановленный crontab ссылался бы на несуществующие скрипты.
DYNAMIC_GLOBS = [
    "/opt/etc/ndm/netfilter.d/zz_*.sh",
]

# Какие динамические пути разрешено ВОССТАНАВЛИВАТЬ (защита от записи в произвольное место).
_DYNAMIC_ALLOWED_RE = re.compile(r"^/opt/etc/ndm/netfilter\.d/zz_[A-Za-z0-9_.\-]+\.sh$")


def is_restorable(path):
    """Путь из snapshot можно записать на роутер: либо из фиксированного списка, либо
    хук netfilter.d с безопасным именем."""
    return path in STATIC_FILES or bool(_DYNAMIC_ALLOWED_RE.match(path or ""))


def needs_exec_bit(path):
    """Файлы, которым после восстановления нужен chmod +x (скрипты sh)."""
    return bool(path) and path.endswith(".sh")


def _jq_item(path_expr):
    # path_expr — либо литерал в кавычках, либо "$f" из цикла for
    return (f'[ -f {path_expr} ] && jq -n --arg p {path_expr} --rawfile c {path_expr} '
            "'{path: $p, content: $c}'")


def build_snapshot_command():
    """Shell-команда для роутера: собрать все файлы в JSON-bundle формата v7 через jq.
    Отсутствующие файлы пропускаются. Динамические — через for по glob."""
    parts = [_jq_item(f'"{p}"') for p in STATIC_FILES]
    loop_item = _jq_item('"$f"')
    for g in DYNAMIC_GLOBS:
        parts.append(f"for f in {g}; do {loop_item}; done")
    body = "(" + " ; ".join(parts) + ")"
    return (body + " | jq -s --arg ts \"$(date)\" "
            f"'{{version: \"{SNAPSHOT_VERSION}\", timestamp: $ts, files: .}}'")
