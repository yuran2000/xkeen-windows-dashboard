"""Check tracked public source. Optional private regex rules stay outside Git."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

RULES = {
    'private-key': r'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----',
    'service-token': r'(?<![\w])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,})',
    'internal-note': r'\u0441\u043a\u0435\u043f\u0442\u0438\u043a|\u043e\u043f\u0440\u043e\u0432\u0435\u0440\u0433\u0430\u044e\u0449|memory/[^\s`]+\.md|shared.?[\\/]lessons[\\/]',
}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private-patterns', type=Path)
    a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    patterns={name: re.compile(value,re.I) for name,value in RULES.items()}
    if a.private_patterns:
        for i,rule in enumerate(a.private_patterns.read_text(encoding='utf-8').splitlines(),1):
            if rule.strip() and not rule.startswith('#'): patterns['private-rule-'+str(i)]=re.compile(rule,re.I)
    raw=subprocess.check_output(['git','-C',str(root),'ls-files','-z'])
    failures=[]
    for name in raw.decode().split('\0'):
        if not name: continue
        data=(root/name).read_bytes()
        if b'\0' in data[:8192]: continue
        try: text=data.decode('utf-8')
        except UnicodeDecodeError: continue
        for no,line in enumerate(text.splitlines(),1):
            for kind,rx in patterns.items():
                if rx.search(line): failures.append(f'{name}:{no}: {kind}')
    print('\n'.join(failures) if failures else 'Public source check: OK')
    return bool(failures)

if __name__=='__main__': sys.exit(main())
