"""xkeen_toast_daemon.py — Windows-toast уведомления о падении xray.

Зачем: dashboard.py работает как SYSTEM-сервис, toast из SYSTEM-сессии
пользователь НЕ видит (это особенность Windows session isolation).
Этот скрипт бежит как interactive user (yuran) через Task Scheduler.

Что делает:
1. Раз в 60 сек делает GET /api/xkeen/header-status на локальный dashboard.
2. Парсит state (ok/warn/error/stopped/unreachable).
3. При переходе good→bad → показывает Windows toast «🔴 XRAY УПАЛ».
4. При остающемся bad — повторяет toast каждые 10 минут.
5. При восстановлении good → toast «✅ Xray восстановлен» (один раз).

Логирует в xkeen_toast_daemon.log рядом со скриптом.

Регистрация в Task Scheduler:
   install_toast_alerts.bat — создаёт task под текущим юзером с /IT флагом
   (interactive). После reboot стартует автоматически.

Архитектура — почему через HTTP к dashboard, а не сам SSH:
- SSH-ключ имеет ACL только SYSTEM/Administrators (см. memory grабля 2026-05-16).
- yuran не может прочитать ключ → SSH под yuran падает с «Permission denied».
- Зато dashboard уже бежит как SYSTEM, имеет доступ к ключу, делает SSH,
  и отдаёт результат через HTTP. Daemon просто HTTP-клиент.
- Дополнительный бонус — server-cache 30s в endpoint'е объединяет запросы
  от daemon, открытых вкладок и Restart-Clean.bat probe'а.
"""

import sys
import os
import time
import json
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import logging
import atexit
from pathlib import Path

# ============== CONFIG ==============
SCRIPT_DIR = Path(__file__).parent
LOG_FILE = SCRIPT_DIR / "xkeen_toast_daemon.log"
STATE_FILE = SCRIPT_DIR / "xkeen_toast_daemon.state.json"
LOCK_FILE = SCRIPT_DIR / "xkeen_toast_daemon.lock"

# Загружаем config_local.py из той же папки
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import config_local as cfg
except Exception as e:
    print(f"ERROR: не удалось импортировать config_local.py: {e}", file=sys.stderr)
    sys.exit(2)

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = getattr(cfg, "DASHBOARD_PORT", 5000)
DASHBOARD_USER = getattr(cfg, "USERNAME", "admin")
DASHBOARD_PASS = getattr(cfg, "PASSWORD", "")
BASE_URL = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"

POLL_INTERVAL_SEC = 60                     # как часто опрашиваем дашборд
TOAST_REPEAT_SEC = 10 * 60                 # повтор toast'а при остающемся bad
HTTP_TIMEOUT = 15

# ============== LOGGING ==============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("xkeen-toast")


# ============== SINGLE-INSTANCE LOCK ==============
def _is_pid_alive(pid):
    """Проверить что процесс с этим PID реально существует и это pythonw с нашим скриптом
    (защита от PID-reuse — Windows может выделить старый PID новому процессу)."""
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        p = psutil.Process(pid)
        cmdline = " ".join(p.cmdline() or [])
        return "xkeen_toast_daemon" in cmdline.lower()
    except Exception:
        # Если psutil недоступен — fallback на грубый os.kill(pid, 0)
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def acquire_lock_or_exit():
    """Single-instance: если другой daemon уже работает — этот выходит молча.
    Зачем: Task Scheduler с /SC ONLOGON может запустить параллельно при /Run + login,
    и daemon'ы будут конкурировать за state.json и слать дублирующие toast'ы."""
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            if old_pid != os.getpid() and _is_pid_alive(old_pid):
                log.warning(f"Another xkeen_toast_daemon уже работает (PID {old_pid}). Выхожу.")
                sys.exit(0)
            # Stale lock — старый PID не существует, перезапишем
        except Exception as e:
            log.warning(f"stale lock-файл (ошибка чтения {e}) — перезаписываю")
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        log.error(f"не удалось записать lock: {e}")
        sys.exit(2)

    # Удалить lock при выходе
    def _cleanup():
        try:
            if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                LOCK_FILE.unlink()
        except Exception:
            pass
    atexit.register(_cleanup)


# ============== STATE PERSISTENCE ==============
def load_state():
    """Читает state.json — последний known state + ts последнего toast'а.
    Нужно чтобы при перезапуске daemon не спамил toast'ом если состояние не менялось."""
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"не удалось прочитать state: {e}")
    return {"prev_state": None, "last_toast_ts": 0}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"не удалось сохранить state: {e}")


# ============== HTTP CLIENT ==============
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))


def login_to_dashboard():
    """POST /login с USERNAME/PASSWORD из config_local.py.
    Cookie сохраняется в _cookie_jar — последующие GET автоматически авторизованы."""
    data = urllib.parse.urlencode({
        "username": DASHBOARD_USER,
        "password": DASHBOARD_PASS,
        "remember": "on",
    }).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/login", data=data, method="POST")
    try:
        with _opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            # Redirect → значит логин успешный (302 → /xkeen или /)
            # Без redirect или с error — fail
            return resp.status in (200, 302) or any(c.name == "session" for c in _cookie_jar)
    except urllib.error.HTTPError as e:
        # 302-redirect может попасть в HTTPError но это всё равно success для login
        if e.code in (302, 303):
            return True
        log.error(f"login HTTP error: {e.code} {e.reason}")
        return False
    except Exception as e:
        log.error(f"login network error: {e}")
        return False


def get_header_status():
    """GET /api/xkeen/header-status. Возвращает dict или None при ошибке."""
    req = urllib.request.Request(f"{BASE_URL}/api/xkeen/header-status")
    try:
        with _opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                log.warning(f"header-status HTTP {resp.status}")
                return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Сессия истекла — перелогиниться
            log.info("session expired, re-login")
            if login_to_dashboard():
                return get_header_status()  # one retry
        log.error(f"header-status HTTP {e.code} {e.reason}")
        return None
    except Exception as e:
        log.error(f"header-status network error: {e}")
        return None


# ============== WINDOWS TOAST ==============
def show_toast(title, message):
    """Показать Windows toast через PowerShell + WinRT API.
    Не требует pip-зависимостей (winrt/win11toast). Работает на Win10/Win11."""
    title_esc = title.replace('"', "'").replace("`", "'")
    message_esc = message.replace('"', "'").replace("`", "'")
    ps_script = f'''
$ErrorActionPreference = "Stop"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$xml = [xml]$template.GetXml()
$nodes = $xml.SelectNodes("//text")
$nodes[0].AppendChild($xml.CreateTextNode("{title_esc}")) | Out-Null
$nodes[1].AppendChild($xml.CreateTextNode("{message_esc}")) | Out-Null
$xmldoc = New-Object Windows.Data.Xml.Dom.XmlDocument
$xmldoc.LoadXml($xml.OuterXml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xmldoc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("xray-dashboard").Show($toast)
'''
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, timeout=15, text=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")
            log.warning(f"toast ps non-zero exit: {result.returncode} stderr={stderr[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"toast subprocess error: {e}")
        return False


# ============== MAIN LOOP ==============
def main():
    log.info(f"=== xkeen_toast_daemon стартует (PID {os.getpid()}) ===")
    log.info(f"dashboard URL: {BASE_URL}")
    log.info(f"poll interval: {POLL_INTERVAL_SEC}s, toast repeat: {TOAST_REPEAT_SEC}s")
    acquire_lock_or_exit()
    log.info(f"single-instance lock acquired: {LOCK_FILE}")

    state = load_state()
    log.info(f"loaded state: prev={state.get('prev_state')}, last_toast_ts={state.get('last_toast_ts')}")

    if not login_to_dashboard():
        log.error("login failed на старте — но продолжаем (login повторится при первом 401)")

    while True:
        try:
            time.sleep(POLL_INTERVAL_SEC)

            j = get_header_status()
            if j is None:
                log.warning("header-status недоступен — пропускаю итерацию")
                continue

            new_state = j.get("state", "unknown")
            label = j.get("label", "?")
            now_ts = int(time.time())
            prev = state.get("prev_state")
            last_ts = int(state.get("last_toast_ts") or 0)

            is_bad = new_state in ("stopped", "error")
            was_good = prev in (None, "ok", "warn")
            was_bad = prev in ("stopped", "error")

            if is_bad:
                if was_good:
                    # ПЕРВЫЙ переход в bad
                    log.info(f"transition good→bad: prev={prev} new={new_state} label={label!r}")
                    show_toast("🔴 Xray упал!", label + " — открой /xkeen")
                    state["last_toast_ts"] = now_ts
                elif was_bad and (now_ts - last_ts >= TOAST_REPEAT_SEC):
                    # ПОВТОРНЫЙ toast пока bad
                    log.info(f"repeat toast (bad >= {TOAST_REPEAT_SEC}s): label={label!r}")
                    show_toast("🔴 Xray всё ещё упал", label + " — открой /xkeen")
                    state["last_toast_ts"] = now_ts
                state["prev_state"] = new_state
            else:
                if was_bad:
                    # Восстановление
                    log.info(f"transition bad→good: prev={prev} new={new_state} label={label!r}")
                    show_toast("✅ Xray восстановлен", label)
                    state["last_toast_ts"] = now_ts
                state["prev_state"] = new_state

            save_state(state)

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — exit")
            break
        except Exception as e:
            log.exception(f"main loop exception (продолжаем): {e}")


if __name__ == "__main__":
    main()
