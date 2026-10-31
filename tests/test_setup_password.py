"""Initial password setup preserves existing configuration and validates changes."""
import importlib.util
from pathlib import Path
import tempfile
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('setup_password',ROOT/'tools/setup_password.py')
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'config.py'
    original=b'PASSWORD = "ChangeMe2026!"\nOTHER = 42\n'
    path.write_bytes(original)
    assert S.set_password(path,"quoted-'password\\safe")
    assert b'OTHER = 42' in path.read_bytes()
    assert path.with_name('config.py.bak-initial-password').read_bytes()==original
    before=path.read_bytes()
    assert not S.set_password(path,'another-password')
    assert path.read_bytes()==before
    short=Path(tmp)/'short.py';short.write_bytes(original)
    try:S.set_password(short,'short')
    except ValueError:pass
    else:raise AssertionError('short password accepted')
    assert short.read_bytes()==original
    bad=Path(tmp)/'bad.py';bad.write_bytes(b'PASSWORD="ChangeMe2026!"\nPASSWORD="other"\n')
    try:S.set_password(bad,'safe-password-123')
    except ValueError:pass
    else:raise AssertionError('ambiguous assignment accepted')
print('test_setup_password: OK')
