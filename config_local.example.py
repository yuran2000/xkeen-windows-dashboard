# =============================================================================
# config_local.example.py — ОБРАЗЕЦ конфига xray-dashboard
# =============================================================================
# Это безопасный образец БЕЗ персональных данных. Скопируй в config_local.py
# и заполни своими значениями. config_local.py никогда не передавай посторонним:
# в нём пароль панели, домашний внешний IP и vless-ссылки (UUID, pbk, sid).
#
# Quick start:
#   cp config_local.example.py config_local.py
#   # отредактируй config_local.py:
#   #   1. SECRET_KEY — сгенерируй: python -c "import secrets; print(secrets.token_hex(32))"
#   #   2. PASSWORD — задай свой
#   #   3. SUBSCRIPTION_TOKEN — сгенерируй: python -c "import secrets; print(secrets.token_urlsafe(24))"
#   #   4. EXTERNAL_IP — внешний IP твоего дома (whatismyip.com)
#   #   5. PROFILES — твои vless URL (если используешь home-VPN); либо оставь пустым []
#   #   6. KEENETIC_* — можно оставить как есть, потом переопределить через UI
#   #     («🔧 Подключение к роутеру» → вписать IP/порт/ключ → «Сохранить»)
# =============================================================================


# === ПОРТ ПАНЕЛИ ===
# По умолчанию 5000. Для нескольких инстансов панели одновременно (например, управление
# несколькими роутерами с одного PC) — ставь разные порты в разных папках установки:
#   C:\xray-dashboard\      → DASHBOARD_PORT = 5000  (домашний роутер)
#   C:\xray-dashboard-work\ → DASHBOARD_PORT = 5001  (рабочий роутер через WG)
# install.bat автоматически найдёт свободный порт если 5000 уже занят при первой установке.
DASHBOARD_PORT = 5000


# === АВТОРИЗАЦИЯ (session-based) ===
USERNAME = "admin"
PASSWORD = "ЗАМЕНИ_МЕНЯ_СВОИМ_ПАРОЛЕМ"   # ⚠ ОБЯЗАТЕЛЬНО ПОМЕНЯЙ

# Случайная строка для подписи cookie. Сгенерируй один раз:
#   python -c "import secrets; print(secrets.token_hex(32))"
# Если поменяешь — все текущие сессии станут невалидными (придётся залогиниться заново).
SECRET_KEY = "ЗАМЕНИ_МЕНЯ_64_СИМВОЛАМИ_HEX_ИЗ_SECRETS_TOKEN_HEX_32"

# Длительность сессии при включённой галочке «Запомнить» (в днях)
SESSION_DAYS = 30


# === SUBSCRIPTION URL (для Happ) ===
# Токен в URL подписки. Если кто-то узнает токен — получит твои vless конфиги.
# Сгенерируй: python -c "import secrets; print(secrets.token_urlsafe(24))"
SUBSCRIPTION_TOKEN = "ЗАМЕНИ_МЕНЯ_ТОКЕНОМ_ИЗ_SECRETS_TOKEN_URLSAFE_24"

# Лимит истории клиентов подписки (хранится в памяти dashboard, не на диске)
SUBS_LOG_LIMIT = 200

# Имя подписки (Profile-Title) — отображается в Happ как название группы профилей
SUBSCRIPTION_TITLE = "Мой VPN"

# Как часто Happ должен обновлять подписку (часов)
SUBSCRIPTION_UPDATE_HOURS = 24


# === ПУТИ К ДОМАШНЕМУ XRAY (если запускаешь xray локально на этом PC) ===
# Если xray у тебя в другом месте — поправь. Если не используешь home-VPN —
# можешь оставить как есть, разделы «Логи xray» просто будут пустыми.
XRAY_DIR = r"C:\xray"
XRAY_CONFIG = r"C:\xray\config.json"
XRAY_ACCESS_LOG = r"C:\xray\access.log"
XRAY_ERROR_LOG = r"C:\xray\error.log"


# === ВНЕШНИЙ IP (твой домашний, для отображения в дашборде) ===
# Можно посмотреть на https://2ip.ru или https://whatismyip.com
EXTERNAL_IP = "0.0.0.0"   # ЗАМЕНИ на свой реальный

# === ВНЕШНИЕ ИДЕНТИФИКАТОРЫ (для отображения в Help-секциях и URL подписки) ===
# Используются в шаблонах как `{{ cfg.X or '<placeholder>' }}` — если оставить пустыми,
# в UI будут показываться placeholder'ы. Если заполнить — везде в подсказках и Help
# появятся твои реальные значения.
EXTERNAL_DOMAIN = ""       # например "vpn.example.com" — твой VPN-домен (для PRIMARY_PROBE_URL и Help)
HOME_DOMAIN = ""           # например "home.example.com" — поддомен для дашборд-подписки через Caddy/nginx
LAN_HOST = ""              # например "192.168.1.100" — LAN-IP этого PC (для отображения в Help как "LAN URL")

# Порты которые СЛУШАЕТ что-то на PC локально (для health-check панели).
# Если не используешь home-VPN — можешь оставить пустым: []
MONITORED_PORTS_INFO = [
    # {"port": 2053, "owner": "xray",  "purpose": "VLESS raw TCP + Vision"},
    # {"port": 443,  "owner": "xray",  "purpose": "VLESS xhttp + Reality"},
    # {"port": 80,   "owner": "caddy", "purpose": "ACME HTTP-01 + redirect"},
    # {"port": 8443, "owner": "caddy", "purpose": "HTTPS site (Reality dest)"},
]
MONITORED_PORTS = [p["port"] for p in MONITORED_PORTS_INFO]

# NAT-форварды на роутере (для отображения «доп. внешний порт»).
NAT_FORWARDS = [
    # {"external": 4443, "internal": 443, "description": "доп. внешний порт для inbound 443"},
]


# === KEENETIC (XKeen management) ===
# Эти значения — defaults. Реально использует runtime_settings.json (через UI:
# «🔧 Подключение к роутеру» → ввёл → «Сохранить»). Если ничего не задавать
# через UI — будут использоваться значения отсюда.
KEENETIC_HOST = "192.168.1.1"           # IP роутера (стандарт для Keenetic)
KEENETIC_PORT = 222                     # SSH-порт dropbear из Entware
KEENETIC_USER = "root"
KEENETIC_SSH_KEY = r"D:\path\to\id_ed25519"   # ЗАМЕНИ путём к своему ключу
# Удалённые пути на роутере — стандартные для XKeen, обычно менять не нужно
KEENETIC_XRAY_CONFIGS  = "/opt/etc/xray/configs"
KEENETIC_XRAY_BAK_DIR  = "/opt/etc/xray/configs.bak"
KEENETIC_WATCHDOG_STATE = "/opt/etc/xray/watchdog.state"
KEENETIC_WATCHDOG_LOG   = "/opt/var/log/xray/watchdog.log"
# Скрипт для добавления outbound'а в XKeen (не обязателен, кнопка добавления в UI работает и без него)
ADD_XKEEN_PS1 = r""


# === ПРОФИЛИ для отображения с QR на главной странице ===
# Список твоих vless URL для генерации QR. Если не используешь home-VPN —
# можешь оставить пустым: PROFILES = []
PROFILES = [
    # {
    #     "name": "MY-HOME",
    #     "subtitle": "raw TCP+Vision, для Wi-Fi",
    #     "external_port": 2053,
    #     "inbound_port": 2053,
    #     "url": "vless://UUID@EXTERNAL_IP:2053?security=reality&flow=xtls-rprx-vision&fp=chrome&pbk=PBKEY&sid=SID&sni=web.max.ru&type=tcp#MY-HOME"
    # },
]
