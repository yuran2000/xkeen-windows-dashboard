r"""sub_expiry_notifier.py — ежедневная проверка истечения подписок.

Запускается Task Scheduler-задачей `SubExpiryCheck` ежедневно в 10:00 МСК.
Читает /opt/etc/xray/subscription_meta.json с Кинетика по SSH, проверяет
поле expires_at каждой подписки, шлёт в Telegram если подписка просрочена
или истекает в ближайшие N дней (по умолчанию N=3).

TG_BOT_TOKEN и TG_CHAT_ID берутся из /opt/etc/xray/watchdog.config на роутере.
Если пусто — выходим без ошибки (не настроено).

Логика уведомлений:
  • Просрочена (days < 0)        — алерт каждый день
  • Истекает сегодня              — алерт
  • 1-3 дня                       — алерт каждый день
  • 4+ дней                       — тишина

Все подписки которые "горят" собираются в одно сообщение чтобы не спамить.

Лог: C:\xray-dashboard\sub_expiry_notifier.log
"""
import sys
import os
import json
import re
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config_local as cfg

LOG_FILE = r"C:\xray-dashboard\sub_expiry_notifier.log"
WARNING_THRESHOLD_DAYS = 3   # начинать предупреждать за N дней
META_PATH = "/opt/etc/xray/subscription_meta.json"
WATCHDOG_CONFIG = "/opt/etc/xray/watchdog.config"

try:
    from zoneinfo import ZoneInfo
    MOSCOW_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    # Fallback если tzdata-пакета нет (Windows venv обычно без него)
    MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")


def log(msg):
    line = "{}  {}".format(datetime.now(tz=MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S МСК"), msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ssh_cat(remote_path, timeout=10):
    """Прочитать файл с роутера через SSH-cat. Возвращает текст или None."""
    args = [
        "ssh", "-i", cfg.KEENETIC_SSH_KEY, "-p", str(cfg.KEENETIC_PORT),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=NUL",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-o", "LogLevel=ERROR",
        f"{cfg.KEENETIC_USER}@{cfg.KEENETIC_HOST}",
        f"cat {remote_path}",
    ]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", errors="replace")
    except Exception as ex:
        log(f"ERR ssh_cat({remote_path}): {ex}")
        return None


def parse_watchdog_config(raw):
    """KEY="value" → dict."""
    out = {}
    if not raw:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^(\w+)\s*=\s*"?([^"]*)"?\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def days_until(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MOSCOW_TZ)
        now = datetime.now(tz=MOSCOW_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        return (d - now).days
    except Exception:
        return None


def send_telegram(token, chat_id, text):
    """Отправляет сообщение через Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            j = json.loads(body)
            if not j.get("ok"):
                log(f"ERR telegram API: {body[:200]}")
                return False
            return True
    except Exception as ex:
        log(f"ERR telegram send: {ex}")
        return False


def main():
    # 1. Читаем watchdog.config для TG_BOT_TOKEN/TG_CHAT_ID
    wcfg_raw = ssh_cat(WATCHDOG_CONFIG)
    if not wcfg_raw:
        log("WARN не удалось прочитать watchdog.config — нет SSH или файла")
        return 0

    wcfg = parse_watchdog_config(wcfg_raw)
    token = wcfg.get("TG_BOT_TOKEN", "").strip()
    chat_id = wcfg.get("TG_CHAT_ID", "").strip()

    if not token or not chat_id:
        log("INFO TG_BOT_TOKEN или TG_CHAT_ID не настроены в watchdog.config — пропуск")
        return 0

    # 2. Читаем subscription_meta.json с роутера
    meta_raw = ssh_cat(META_PATH)
    if not meta_raw:
        log("INFO subscription_meta.json не найден или пуст — нет подписок с примечаниями")
        return 0

    try:
        meta_data = json.loads(meta_raw)
    except Exception as ex:
        log(f"ERR парсинг subscription_meta.json: {ex}")
        return 1

    subs = meta_data.get("subs", {})
    if not subs:
        log("INFO subscription_meta.subs пуст")
        return 0

    # 3. Собираем подписки которые надо упомянуть
    alerts = []  # list of (days_left, note, pbk_short, expires_at)
    for pbk, entry in subs.items():
        expires = (entry.get("expires_at") or "").strip()
        if not expires:
            continue
        days = days_until(expires)
        if days is None:
            log(f"WARN некорректная expires_at для pbk={pbk[:12]}…: {expires!r}")
            continue
        if days > WARNING_THRESHOLD_DAYS:
            continue  # ещё далеко, не упоминаем
        note = (entry.get("note") or "").strip()
        alerts.append({
            "days": days,
            "note": note,
            "pbk_short": pbk[:12] + "…",
            "expires_at": expires,
        })

    if not alerts:
        log("OK нет подписок к уведомлению (все живые >14 дней)")
        return 0

    # Сортируем: сначала самые горящие (минимум days, включая отрицательные)
    alerts.sort(key=lambda a: a["days"])

    # 4. Строим сообщение
    lines = ["📅 <b>Проверка подписок VPN</b>", ""]
    for a in alerts:
        d = a["days"]
        if d < 0:
            head = f"🚨 <b>ИСТЕКЛА</b> {abs(d)} дн. назад ({a['expires_at']})"
        elif d == 0:
            head = f"🚨 <b>ИСТЕКАЕТ СЕГОДНЯ</b> ({a['expires_at']})"
        elif d <= 3:
            head = f"⚠️ Истекает через <b>{d} дн.</b> ({a['expires_at']})"
        elif d <= 7:
            head = f"⏰ Истекает через {d} дн. ({a['expires_at']})"
        else:
            head = f"📆 Истекает через {d} дн. ({a['expires_at']})"
        lines.append(head)
        if a["note"]:
            lines.append(f"   📝 {a['note']}")
        lines.append(f"   <code>pbk={a['pbk_short']}</code>")
        lines.append("")

    # LAN URL для ссылки в TG — берём из cfg.LAN_HOST если задан, иначе generic placeholder
    lan_host = getattr(cfg, "LAN_HOST", "") or "<your-pc-lan-ip>"
    lines.append(f"Управление: <code>http://{lan_host}:5000/xkeen</code> (нажми ✏️ Подписка у группы)")
    text = "\n".join(lines)

    # 5. Отправляем
    ok = send_telegram(token, chat_id, text)
    if ok:
        log(f"OK отправлено уведомление о {len(alerts)} подписк(е/ах)")
        return 0
    else:
        log(f"ERR не смог отправить уведомление о {len(alerts)} подписк(е/ах)")
        return 2


if __name__ == "__main__":
    try:
        rc = main()
        sys.exit(rc)
    except Exception as ex:
        log(f"FATAL {ex!r}")
        sys.exit(99)
