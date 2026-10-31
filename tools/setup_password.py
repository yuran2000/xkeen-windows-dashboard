"""Ask for the initial panel password without editing configuration by hand."""
import ast
import getpass
import hashlib
from pathlib import Path
import shutil
import tempfile

def set_password(path, password):
    path=Path(path)
    raw=path.read_bytes(); text=raw.decode('utf-8-sig')
    tree=ast.parse(text)
    assignments=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PASSWORD' for t in n.targets)]
    if len(assignments)!=1: raise ValueError('Не найдена единственная настройка пароля. Файл не изменён.')
    node=assignments[0]
    old=ast.literal_eval(node.value)
    if isinstance(old,str) and old and not old.startswith(('ЗАМЕНИ_МЕНЯ','ChangeMe2026!')):
        return False
    if len(password)<10: raise ValueError('Пароль должен содержать не меньше 10 символов.')
    lines=text.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=['PASSWORD = '+repr(password)+'\n']
    result=''.join(lines);parsed=ast.parse(result)
    ast.literal_eval(next(n.value for n in parsed.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PASSWORD' for t in n.targets)))
    backup=path.with_name(path.name+'.bak-initial-password')
    if backup.exists(): raise ValueError('Копия предыдущей настройки уже существует. Нужна проверка; файл не изменён.')
    shutil.copy2(path,backup)
    if backup.read_bytes()!=raw: raise ValueError('Не удалось проверить резервную копию. Файл не изменён.')
    data=result.replace('\r\n','\n').encode('utf-8')
    replaced=False
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.password-',delete=False) as f:
            temporary=Path(f.name);f.write(data)
        if path.read_bytes()!=raw: raise ValueError('Настройки изменились в другом окне. Сохранение отменено.')
        temporary.replace(path)
        replaced=True
        if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(data).digest(): raise OSError('Ошибка проверки записи')
    except Exception:
        if replaced: path.write_bytes(raw)
        raise
    return True

def main():
    path=Path(__file__).resolve().parents[1]/'config_local.py'
    text=path.read_text(encoding='utf-8-sig')
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PASSWORD' for t in n.targets))
    old=ast.literal_eval(node.value)
    if isinstance(old,str) and old and not old.startswith(('ЗАМЕНИ_МЕНЯ','ChangeMe2026!')):
        print('Пароль уже настроен. Он сохранён без изменений.');return
    print('Нужно задать пароль для входа в панель. Он не отправляется на GitHub.')
    print('Ввести не меньше 10 символов. При вводе символы не показываются — это нормально.')
    while True:
        password=getpass.getpass('Новый пароль: ')
        repeated=getpass.getpass('Повторить пароль: ')
        if password!=repeated: print('Пароли отличаются. Повторить ввод.');continue
        if len(password)<10: print('Пароль должен содержать не меньше 10 символов.');continue
        set_password(path,password)
        print('Пароль сохранён. Его нужно сохранить в личном менеджере паролей.')
        print('Теперь можно запустить start.bat и войти в панель с именем admin.')
        return

if __name__=='__main__':main()
