"""Выбор ssh/scp для вызовов роутера.

Голый "ssh" в PATH может оказаться ssh из Git for Windows (msys). Он понимает
`UserKnownHostsFile=NUL` не как устройство, а как обычный файл и создаёт `./NUL`
в текущем каталоге. Файл с таким именем почти не удаляется средствами Windows
и обрывает распаковку любого архива, в который попал.

Поэтому берём встроенный OpenSSH Windows по полному пути, а если его нет —
ssh из PATH с `/dev/null` (msys/cygwin переводят его в пустое устройство).
"""
import os

_SYSTEM_ROOT = os.environ.get("SystemRoot", r"C:\Windows")
# Sysnative — для 32-битного Python на 64-битной Windows: там System32 подменяется
# на SysWOW64, где OpenSSH нет.
_OPENSSH_DIRS = [
    os.path.join(_SYSTEM_ROOT, "System32", "OpenSSH"),
    os.path.join(_SYSTEM_ROOT, "Sysnative", "OpenSSH"),
]


def _find(name):
    for d in _OPENSSH_DIRS:
        path = os.path.join(d, name + ".exe")
        if os.path.isfile(path):
            return path
    return None


def ssh_exe(name="ssh"):
    """Полный путь к ssh.exe / scp.exe встроенного OpenSSH, иначе имя для поиска в PATH."""
    return _find(name) or name


def null_known_hosts(name="ssh"):
    """Значение для `-o UserKnownHostsFile=` под выбранный бинарник."""
    return "NUL" if _find(name) else "/dev/null"
