# setup_helper.py — вспомогательные операции установки для install.bat.
#
# Зачем отдельный файл, а не inline `python -c "..."` в .bat:
# install.bat работает под `setlocal EnableDelayedExpansion`, где символ "!"
# имеет специальное значение и вырезается из строки. Из-за этого встроенные
# python-однострочники с "!=" превращались в "=" (SyntaxError), "^" в "[^...]"
# съедался как escape, а кириллица в аргументах ломалась под `chcp 65001`.
# В итоге авто-подбор свободного порта и генерация секретов молча падали.
# Здесь та же логика, но в обычном .py — без ограничений парсинга cmd.
#
# Вызов из install.bat:
#   python setup_helper.py detect-port [start] [end]   -> печатает первый свободный порт
#   python setup_helper.py busy-ports  [start] [end]   -> печатает занятые порты диапазона
#   python setup_helper.py set-port <port>             -> прописывает DASHBOARD_PORT в config_local.py
#   python setup_helper.py gen-secrets                 -> генерит SECRET_KEY/SUBSCRIPTION_TOKEN если плейсхолдеры
#   python setup_helper.py get-port                    -> печатает текущий DASHBOARD_PORT
#   python setup_helper.py password-is-placeholder     -> exit 0 если PASSWORD плейсхолдер, иначе exit 1

import sys
import re
import socket
import secrets

CONFIG = "config_local.py"
PLACEHOLDER_PREFIX = "ЗАМЕНИ_МЕНЯ"
# Известные дефолтные пароли, которые нужно считать «ещё не заданным».
PASSWORD_PLACEHOLDERS = ("ЗАМЕНИ_МЕНЯ", "ChangeMe2026!")


def _read():
    with open(CONFIG, encoding="utf-8") as f:
        return f.read()


def _write(text):
    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write(text)


def _is_free(port):
    """True если порт свободен (никто не слушает на 127.0.0.1:port)."""
    s = socket.socket()
    try:
        # connect_ex == 0 -> соединение удалось -> порт занят
        return s.connect_ex(("127.0.0.1", port)) != 0
    finally:
        s.close()


def detect_port(start, end):
    for p in range(start, end):
        if _is_free(p):
            print(p)
            return
    print(start)


def busy_ports(start, end):
    busy = [str(p) for p in range(start, end) if not _is_free(p)]
    print("    " + (", ".join(busy) if busy else "(нет занятых, все свободны)"))


def set_port(port):
    s = _read()
    s = re.sub(r"(?m)^DASHBOARD_PORT\s*=\s*\d+", "DASHBOARD_PORT = %d" % port, s)
    _write(s)
    print("  DASHBOARD_PORT = %d" % port)


def _value(text, name):
    m = re.search(r'(?m)^' + name + r'\s*=\s*"([^"]*)"', text)
    return m.group(1) if m else None


def _replace_if_placeholder(text, name, new_value):
    """Заменяет значение name на new_value, только если текущее — плейсхолдер."""
    m = re.search(r'(?m)^(' + name + r'\s*=\s*)"([^"]*)"', text)
    if not m or not m.group(2).startswith(PLACEHOLDER_PREFIX):
        return text, False
    return text[:m.start()] + m.group(1) + '"' + new_value + '"' + text[m.end():], True


def gen_secrets():
    s = _read()
    s, c1 = _replace_if_placeholder(s, "SECRET_KEY", secrets.token_hex(32))
    s, c2 = _replace_if_placeholder(s, "SUBSCRIPTION_TOKEN", secrets.token_urlsafe(24))
    if c1 or c2:
        _write(s)
        done = [n for n, c in (("SECRET_KEY", c1), ("SUBSCRIPTION_TOKEN", c2)) if c]
        print("Сгенерировано: " + ", ".join(done))
    else:
        print("Секреты уже заданы, пропускаю.")


def get_port():
    m = re.search(r"(?m)^DASHBOARD_PORT\s*=\s*(\d+)", _read())
    print(m.group(1) if m else "5000")


def password_is_placeholder():
    val = _value(_read(), "PASSWORD") or ""
    is_ph = any(val == p or val.startswith(p) for p in PASSWORD_PLACEHOLDERS)
    sys.exit(0 if is_ph else 1)


def _arg_int(args, idx, default):
    try:
        return int(args[idx])
    except (IndexError, ValueError):
        return default


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "detect-port":
        detect_port(_arg_int(args, 1, 5000), _arg_int(args, 2, 5020))
    elif cmd == "busy-ports":
        busy_ports(_arg_int(args, 1, 5000), _arg_int(args, 2, 5020))
    elif cmd == "set-port":
        set_port(_arg_int(args, 1, 5000))
    elif cmd == "gen-secrets":
        gen_secrets()
    elif cmd == "get-port":
        get_port()
    elif cmd == "password-is-placeholder":
        password_is_placeholder()
    else:
        sys.stderr.write("setup_helper.py: unknown command %r\n" % cmd)
        sys.exit(2)


if __name__ == "__main__":
    main()
