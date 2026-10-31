"""Exercise source updates and rollback in disposable Git repositories only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('checkout',ROOT/'tools/update_checkout.py')
U=importlib.util.module_from_spec(spec); spec.loader.exec_module(U)
checks=0

def run(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.STDOUT).decode().strip()

def fixture(root,name):
    repo=root/name; repo.mkdir()
    run(repo,'init','-b','main'); run(repo,'config','user.name','Test'); run(repo,'config','user.email','test@example.invalid')
    (repo/'.gitignore').write_text('config_local.py\nruntime_settings.json\n.ssh/\nbackups/\n.update-needs-deps\n')
    (repo/'app.py').write_text('VERSION=1\n')
    run(repo,'add','.'); run(repo,'commit','-m','initial')
    old=run(repo,'rev-parse','HEAD')
    (repo/'app.py').write_text('VERSION=2\n')
    (repo/'added.py').write_text('NEW=True\n')
    run(repo,'add','.');run(repo,'commit','-m','update')
    target=run(repo,'rev-parse','HEAD');run(repo,'reset','--hard',old)
    (repo/'config_local.py').write_text('PASSWORD="private-test"\n')
    (repo/'runtime_settings.json').write_text('{"custom":true}')
    (repo/'.ssh').mkdir();(repo/'.ssh/key').write_text('test-only-key')
    return repo,old,target

def check(ok,message):
    global checks
    assert ok,message
    checks+=1

def refused(call):
    try: call()
    except RuntimeError: return
    raise AssertionError('Expected refusal')

with tempfile.TemporaryDirectory(prefix='dashboard-update-tests-') as tmp:
    root=Path(tmp)
    repo,old,target=fixture(root,'ff')
    backup=U.apply_update(repo,target)
    check(run(repo,'rev-parse','HEAD')==target,'fast forward')
    check((repo/'config_local.py').read_text()=='PASSWORD="private-test"\n','settings unchanged')
    check((repo/'.ssh/key').read_text()=='test-only-key','key unchanged')
    check((repo/'runtime_settings.json').read_text()=='{"custom":true}','runtime unchanged')
    check((backup/'history.bundle').is_file(),'history backed up')
    manifest=json.loads((backup/'manifest.json').read_text())
    check(manifest['old_head']==old and 'config_local.py' in manifest['files'],'settings backed up')

    repo,old,target=fixture(root,'dirty')
    (repo/'app.py').write_text('LOCAL=42\n');(repo/'notes.txt').write_text('local notes')
    refused(lambda:U.apply_update(repo,target))
    check((repo/'app.py').read_text()=='LOCAL=42\n','dirty refusal preserves files')
    backup=U.apply_update(repo,target,allow_dirty=True)
    check((backup/'files/app.py').read_text()=='LOCAL=42\n','dirty code backed up')
    check((repo/'notes.txt').read_text()=='local notes','untracked notes preserved')

    repo,old,target=fixture(root,'divergent')
    (repo/'app.py').write_text('LOCAL_COMMIT=1\n');run(repo,'add','.');run(repo,'commit','-m','local')
    local=run(repo,'rev-parse','HEAD')
    refused(lambda:U.apply_update(repo,target))
    check(run(repo,'rev-parse','HEAD')==local,'divergence refusal')
    backup=U.apply_update(repo,target,allow_rewrite=True)
    check(run(repo,'rev-parse','HEAD')==target,'explicit history recovery')
    check(local in run(repo,'bundle','list-heads',str(backup/'history.bundle')),'local commit recoverable')

    repo,old,target=fixture(root,'rollback')
    (repo/'app.py').write_text('LOCAL_BEFORE_FAILURE=1\n')
    def fail(_): raise RuntimeError('injected verification failure')
    refused(lambda:U.apply_update(repo,target,allow_dirty=True,after_apply=fail))
    check(run(repo,'rev-parse','HEAD')==old,'head rolled back')
    check((repo/'app.py').read_text()=='LOCAL_BEFORE_FAILURE=1\n','dirty contents restored')
    check(not (repo/'added.py').exists(),'new tracked file rolled back')
    check((repo/'.ssh/key').read_text()=='test-only-key','key survives rollback')

    repo,old,target=fixture(root,'protected')
    run(repo,'reset','--hard',target)
    (repo/'config_local.py').write_text('DO_NOT_INSTALL=1\n');run(repo,'add','-f','config_local.py');run(repo,'commit','-m','unsafe target')
    unsafe=run(repo,'rev-parse','HEAD');run(repo,'reset','--hard',old)
    refused(lambda:U.apply_update(repo,unsafe))
    check(run(repo,'rev-parse','HEAD')==old,'protected target refused')
    check(U.protected('CONFIG_LOCAL.PY'),'Windows path comparison is case insensitive')

    repo,old,target=fixture(root,'merge')
    (repo/'.git/MERGE_HEAD').write_text(target+'\n')
    refused(lambda:U.apply_update(repo,target,allow_rewrite=True,allow_dirty=True))
    check(run(repo,'rev-parse','HEAD')==old,'unfinished merge preserved')

    repo,old,target=fixture(root,'branch')
    run(repo,'checkout','-b','custom')
    refused(lambda:U.apply_update(repo,target,allow_rewrite=True))
    check(run(repo,'branch','--show-current')=='custom','custom branch preserved')

check(U.protected('.private/settings.json'),'hidden local settings directory protected')
check(not U.protected('.github/workflows/release.yml'),'public workflow directory allowed')
print('test_update_checkout: OK ('+str(checks)+' checks)')
