"""Offline process-launch regression; no router or user configuration required."""
import ast
from pathlib import Path
import json
import os
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'dashboard.py').read_text(encoding='utf-8')
FUNCTION = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == 'keenetic_ssh')


def load_ssh(process_module):
    namespace = {'subprocess': process_module, '_ssh_args': lambda cmd: ['ssh', cmd]}
    exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), '<ssh-layer>', 'exec'), namespace)
    return namespace


class ProcessLaunchTests(unittest.TestCase):
    def make_process(self, windows=True):
        process = SimpleNamespace(run=Mock(return_value=subprocess.CompletedProcess([], 0, b'ok', b'')),
                                  DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
        if windows:
            process.CREATE_NO_WINDOW = 0x08000000
        return process

    def test_windows_launch_keeps_read_pipes_and_null_input(self):
        p = self.make_process()
        self.assertEqual(load_ssh(p)['keenetic_ssh']('status', timeout=7)['stdout'], 'ok')
        kw = p.run.call_args.kwargs
        self.assertEqual(kw['creationflags'], p.CREATE_NO_WINDOW)
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertEqual(kw['timeout'], 7)
        self.assertTrue(kw['capture_output'])
        self.assertNotIn('input', kw)

    def test_utf8_input_excludes_stdin(self):
        p = self.make_process()
        load_ssh(p)['keenetic_ssh']('cat', stdin_data='данные')
        self.assertEqual(p.run.call_args.kwargs['input'], 'данные'.encode())
        self.assertNotIn('stdin', p.run.call_args.kwargs)

    def test_nonzero_exit_and_decoding(self):
        p = self.make_process()
        p.run.return_value = subprocess.CompletedProcess([], 7, b'partial', b'bad\xff')
        result = load_ssh(p)['keenetic_ssh']('status')
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 7)
        self.assertEqual(result['stdout'], 'partial')
        self.assertEqual(result['stderr'], 'bad\ufffd')

    def test_timeout_keeps_partial_output(self):
        p = self.make_process()
        p.run.side_effect = subprocess.TimeoutExpired('ssh', 7, output=b'partial', stderr=b'waiting')
        result = load_ssh(p)['keenetic_ssh']('status', timeout=7)
        self.assertEqual(result['code'], -1)
        self.assertEqual(result['stdout'], 'partial')
        self.assertIn('waiting', result['stderr'])

    def test_non_windows_falls_back_to_zero(self):
        p = self.make_process(windows=False)
        self.assertTrue(load_ssh(p)['keenetic_ssh']('status')['ok'])
        self.assertEqual(p.run.call_args.kwargs['creationflags'], 0)

    def test_all_ssh_scp_launch_sites_hide_window(self):
        # Covers standalone entry points without importing their config or running them.
        count = 0
        for filename in ('dashboard.py', 'xkeen_snapshot_daily.py', 'sub_expiry_notifier.py'):
            tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call) or ast.unparse(n.func) != 'subprocess.run' or not n.args:
                    continue
                arg = ast.unparse(n.args[0])
                if arg not in ('_ssh_args(cmd)', 'scp_args', 'args', 'ssh') and 'ssh_bin.ssh_exe' not in arg:
                    continue
                flags = next((k.value for k in n.keywords if k.arg == 'creationflags'), None)
                self.assertIsNotNone(flags, (filename, n.lineno))
                self.assertEqual(eval(compile(ast.Expression(flags), '<flag>', 'eval'),
                                      {'subprocess': self.make_process()}), 0x08000000)
                count += 1
        self.assertEqual(count, 6)

    @unittest.skipUnless(os.name == 'nt', 'Windows console API')
    def test_real_child_has_no_console_and_keeps_input_output(self):
        namespace = load_ssh(subprocess)
        code = ('import ctypes,json,sys; '
                'print(json.dumps({"console":bool(ctypes.windll.kernel32.GetConsoleWindow()),'
                '"data":sys.stdin.buffer.read().hex()})); sys.stderr.write("err")')
        namespace['_ssh_args'] = lambda cmd: [sys.executable, '-c', code]
        result = namespace['keenetic_ssh']('unused', stdin_data='данные', timeout=10)
        self.assertTrue(result['ok'], result)
        self.assertEqual(json.loads(result['stdout']), {'console': False, 'data': 'данные'.encode().hex()})
        self.assertEqual(result['stderr'], 'err')


if __name__ == '__main__':
    unittest.main(verbosity=2)
