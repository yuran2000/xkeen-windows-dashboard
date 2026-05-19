# Security Policy

## 🔒 Сообщить об уязвимости

**НЕ открывай публичный Issue** для security-issues — это раскроет проблему всем до того как будет фикс.

Вместо этого:

1. **GitHub Security Advisories** (preferred): репо → вкладка **Security** → **Report a vulnerability** → откроется private-форма. Только владелец репо увидит.
2. Или **email** на адрес из коммитов: посмотри в `git log --format="%ae" | head -1` (без `mailto:` URL — анти-спам).

**Ожидаемое время ответа**: 1-7 дней.

## ✅ Что считается уязвимостью

- **Авторизация в обход PASSWORD**: CSRF, session-stealing, фиксация сессии
- **Command injection** через UI: если можно выполнить произвольный bash на роутере или Windows-PC через формы/параметры панели
- **Path traversal**: скачать `../config_local.py` или другие чувствительные файлы через URL
- **Утечка чужих данных**: подписочные UUID/pbk, SSH-ключи, watchdog.config другого юзера
- **XSS в UI**: который позволяет получить session-cookie другого юзера
- **SSRF**: через какой-то endpoint заставить дашборд сделать запрос на произвольный хост

## ❌ Что НЕ уязвимость

- Юзер положил `config_local.py` в публичный git — это его ошибка, наш `.gitignore` его блокирует
- Slow performance при 1000+ outbound'ов — это performance issue, не security
- Кнопка не работает без интернета — функциональный баг
- Дашборд требует доверенной локальной сети (это by design — он живёт в твоей LAN, защита через PASSWORD)
- Запуск под SYSTEM-сервисом — это by design (нужен доступ к SSH-ключу с правильным ACL)

## 🛡 Текущие меры

- **`@requires_auth`** на всех API endpoints (session-based, секрет SECRET_KEY в config_local.py)
- **`SECRET_KEY`** генерируется случайно через `secrets.token_hex(32)` при `install.bat`
- **`SUBSCRIPTION_TOKEN`** — отдельный токен только для read-only subscription URL, не даёт админ-доступ
- **SSH-ключ** имеет ACL только `SYSTEM`/`Administrators` (через `install_service.ps1`)
- **`XKEEN_COMMANDS`** dict — whitelist разрешённых shell-команд, нельзя выполнить произвольный bash через `/api/xkeen/cmd`
- **Регекс-валидация** всех пользовательских inputs: MAC (`^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$`), IPv4 (`^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:...)$`), имя политики (`^Policy\d+$`)
- **Проверка существования target policy** в `show ip policy` перед write-операцией (защита от опечаток вроде `Policy99`)
- **Server-side cache** для read-only endpoints (защита от DoS через slow SSH)
- **`config_local.py`, `.ssh/`, `backups/`, `*.log`** — gitignored, никогда не попадают в репо

## 🔄 Disclosure timeline

После confirmed-уязвимости:

1. Day 0: получено сообщение, подтверждено
2. Day 1-3: разработка фикса
3. Day 3-7: внутреннее тестирование
4. Day 7: public release с фиксом + CVE/advisory если применимо
5. Кредит репортёру в release notes (если он не против)

## 🤝 Bounty

Денежный bounty нет. Кредит в release notes + listing в Hall of Fame (если разовьём).
