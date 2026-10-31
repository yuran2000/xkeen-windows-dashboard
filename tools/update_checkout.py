"""Update tracked source with a verified recovery copy. Does not stop services."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import uuid

ORIGINS = {
    'git@github.com:yuran2000/xkeen-windows-dashboard',
    'git@github.com:yuran2000/xkeen-windows-dashboard.git',
    'git@github.com:yuran2000/xray-dashboard',
    'git@github.com:yuran2000/xray-dashboard.git',
    'https://github.com/yuran2000/xkeen-windows-dashboard',
    'https://github.com/yuran2000/xkeen-windows-dashboard.git',
    'https://github.com/yuran2000/xray-dashboard',
    'https://github.com/yuran2000/xray-dashboard.git',
}
PROTECTED = {'config_local.py', 'config.ini', 'runtime_settings.json', 'wg_clients.json',
             '.env', '.venv', '.ssh', 'ssh', 'backups', '.update-needs-deps'}

def git(repo, *args, check=True):
    p = subprocess.run(['git', '-C', str(repo), *args], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if check and p.returncode:
        raise RuntimeError('git ' + args[0] + ' failed: ' + p.stderr.decode('utf-8', 'replace').strip())
    return p

def lines(repo, *args):
    return [s for s in git(repo, *args).stdout.decode('utf-8').split('\0') if s]

def protected(name):
    p = Path(name)
    return any(s.startswith('.') and s != '.github' for s in p.parts[:-1]) or bool({s.lower() for s in p.parts} & PROTECTED) or p.suffix.lower() in {'.pem', '.key', '.crt'}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def apply_update(repo, target, *, allow_rewrite=False, allow_dirty=False, after_apply=None):
    repo = Path(repo).resolve()
    top = Path(git(repo, 'rev-parse', '--show-toplevel').stdout.decode().strip()).resolve()
    if top != repo:
        raise RuntimeError('Run from the installation root')
    branch = git(repo, 'symbolic-ref', '--short', 'HEAD').stdout.decode().strip()
    if branch != 'main':
        raise RuntimeError('Only the main branch is supported; local branches remain untouched')
    for marker in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
        marker_path = Path(git(repo, 'rev-parse', '--git-path', marker).stdout.decode().strip())
        if not marker_path.is_absolute(): marker_path = repo / marker_path
        if marker_path.exists(): raise RuntimeError('Предыдущее обновление не завершено. Ничего не изменено. Сохранить текст ошибки и обратиться за помощью.')
    target = git(repo, 'rev-parse', '--verify', target + '^{commit}').stdout.decode().strip()
    old = git(repo, 'rev-parse', 'HEAD').stdout.decode().strip()
    names = lines(repo, 'ls-tree', '-r', '-z', '--name-only', target)
    for name in names:
        try: (repo / name).resolve().relative_to(repo)
        except ValueError: raise RuntimeError('Target path escapes the installation root')
    if any(protected(n) for n in names):
        raise RuntimeError('Target contains a protected settings/key/runtime path; update refused')
    tree = git(repo, 'ls-tree', '-r', target).stdout.decode()
    if any(line.startswith(('120000 ', '160000 ')) for line in tree.splitlines()):
        raise RuntimeError('Symlinks and submodules are not supported in an update')
    ff = git(repo, 'merge-base', '--is-ancestor', old, target, check=False).returncode == 0
    dirty = bool(git(repo, 'status', '--porcelain').stdout)
    if not ff and not allow_rewrite:
        raise RuntimeError('Нужен разовый переход. Выполнить шаги «Восстановление обновлений» в инструкции. Панель не остановлена.')
    if dirty and not allow_dirty:
        raise RuntimeError('Найдены местные изменения программы. Нужен переход с резервной копией через Recover-Update.bat.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    backup = repo / 'backups' / ('update-' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    tracked = lines(repo, 'ls-files', '-z')
    untracked = lines(repo, 'ls-files', '--others', '--exclude-standard', '-z')
    settings = [n for n in PROTECTED if (repo / n).is_file()]
    candidates = sorted(set(tracked + untracked + settings))
    manifest = {}
    missing = []
    for name in candidates:
        src = repo / name
        try: src.resolve().relative_to(repo)
        except ValueError: raise RuntimeError('Local path escapes the installation root: ' + name)
        if src.is_symlink(): raise RuntimeError('Recovery refuses symlinks: ' + name)
        if not src.exists(): missing.append(name); continue
        if not src.is_file(): continue
        dest = backup / 'files' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        checksum = digest(src)
        if digest(dest) != checksum: raise RuntimeError('Recovery copy verification failed')
        manifest[name] = checksum
    git(repo, 'bundle', 'create', str(backup / 'history.bundle'), '--all')
    git(repo, 'bundle', 'verify', str(backup / 'history.bundle'))
    state = {'old_head': old, 'target': target, 'files': manifest, 'missing': missing,
             'bundle_sha256': digest(backup / 'history.bundle')}
    (backup / 'manifest.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    for name, checksum in manifest.items():
        if digest(repo / name) != checksum: raise RuntimeError('Files changed while preparing update; try again')
    settings_hashes = {name: checksum for name, checksum in manifest.items() if protected(name)}
    try:
        if ff and not dirty:
            git(repo, 'merge', '--ff-only', target)
        else:
            git(repo, 'reset', '--hard', target)
        if after_apply: after_apply(repo)
        if git(repo, 'rev-parse', 'HEAD').stdout.decode().strip() != target:
            raise RuntimeError('Checkout verification failed')
        # Compare all tracked content with the exact target before any service restart.
        git(repo, 'diff', '--exit-code', target, '--')
        for name, checksum in settings_hashes.items():
            if digest(repo / name) != checksum: raise RuntimeError('Protected settings changed')
    except Exception:
        git(repo, 'reset', '--hard', old)
        for name in manifest:
            dest = repo / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / 'files' / name, dest)
        for name in missing:
            dest = repo / name
            if dest.is_file(): dest.unlink()
        raise
    (repo / '.update-needs-deps').write_text(target + '\n', encoding='ascii')
    return backup

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--target')
    p.add_argument('--repair', action='store_true')
    a = p.parse_args()
    origin = git(a.repo, 'remote', 'get-url', 'origin').stdout.decode().strip()
    if origin not in ORIGINS: raise RuntimeError('Unexpected origin; no files changed')
    if a.target:
        fetched = git(a.repo, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}').stdout.decode().strip()
        if a.target != fetched: raise RuntimeError('Target does not match the fetched main branch')
        target = fetched
    else:
        git(a.repo, 'fetch', 'origin', 'refs/heads/main')
        target = git(a.repo, 'rev-parse', 'FETCH_HEAD').stdout.decode().strip()
    if a.repair:
        print('Будут обновлены файлы программы. Личные настройки и ключи останутся на месте.')
        print('Сначала будет создана и проверена резервная копия прежних файлов и настроек.')
        print('Работающая панель и роутер этим переходом не перезапускаются.')
        if input('Продолжить? Ввести английскую y и нажать Enter; для отмены — только Enter: ').strip().lower() != 'y':
            print('Отменено. Установка не изменена.'); return 2
    source = git(a.repo, 'show', target + ':dashboard.py').stdout.decode('utf-8')
    version = re.search(r'^_VERSION_FALLBACK\s*=\s*"(\d+\.\d+\.\d+)"', source, re.M)
    if not version: raise RuntimeError('Не удалось определить версию обновления. Файлы не изменены.')
    tag = 'v' + version.group(1)
    tracking = 'refs/remotes/origin/release-' + tag
    git(a.repo, 'fetch', '--no-tags', 'origin', '+refs/tags/' + tag + ':' + tracking)
    tag_object = git(a.repo, 'rev-parse', tracking).stdout.decode().strip()
    tag_commit = git(a.repo, 'rev-parse', tracking + '^{commit}').stdout.decode().strip()
    if git(a.repo, 'merge-base', '--is-ancestor', tag_commit, target, check=False).returncode:
        raise RuntimeError('Метка выпуска не соответствует обновлению. Файлы не изменены.')
    old_tag = git(a.repo, 'rev-parse', '--verify', 'refs/tags/' + tag, check=False)
    if old_tag.returncode == 0 and old_tag.stdout.decode().strip() != tag_object and not a.repair:
        raise RuntimeError('Локальная метка версии отличается. Нужен подтверждаемый переход через Recover-Update.bat.')
    backup = apply_update(a.repo, target, allow_rewrite=a.repair, allow_dirty=a.repair)
    # Local tags are changed only after the verified bundle preserves previous refs.
    previous = old_tag.stdout.decode().strip() if old_tag.returncode == 0 else '0' * 40
    git(a.repo, 'update-ref', 'refs/tags/' + tag, tag_object, previous)
    print('ГОТОВО: файлы программы обновлены. Резервная копия: ' + str(backup))
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
