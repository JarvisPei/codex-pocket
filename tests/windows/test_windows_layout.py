"""Keep package, script and launcher paths coherent without launching Desktop."""
import ast
import importlib
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / 'platforms' / 'windows'


class WindowsLayoutTests(unittest.TestCase):
    def test_modules_are_importable_without_starting_services(self):
        for path in PLATFORM.glob('*.py'):
            if path.stem != '__init__':
                module = importlib.import_module('platforms.windows.' + path.stem)
                self.assertEqual(Path(module.__file__).resolve(), path.resolve())
        self.assertFalse(list(ROOT.glob('windows_*.py')))

    def test_runtime_scripts_are_packaged_at_declared_root(self):
        from platforms.windows import bridge, read_state
        self.assertEqual(bridge.ROOT, ROOT)
        self.assertEqual(read_state.ROOT, ROOT)
        for source in PLATFORM.glob('*.py'):
            for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.startswith('platforms/windows/scripts/') and node.value.endswith('.ps1'):
                        self.assertTrue((ROOT / node.value).is_file(), node.value)

    def test_root_launcher_and_gui_bootstrap_use_package_paths(self):
        launcher = (ROOT / 'Start Pocket.cmd').read_text(encoding='utf-8')
        self.assertIn('platforms\\windows\\scripts\\windows-install-launcher.ps1', launcher)
        tray = (PLATFORM / 'scripts/windows-pocket-tray.ps1').read_text(encoding='utf-8')
        self.assertIn('from platforms.windows.pocket import main', tray)
        self.assertIn("Join-Path $PSScriptRoot '..\\..\\..'", tray)
        self.assertTrue((ROOT / 'scripts/pair-device.py').is_file())

    def test_module_entry_points_help_from_repository_root(self):
        for name in ('bridge', 'pocket', 'desktop_helper'):
            command = [sys.executable, '-m', 'platforms.windows.' + name, '--help']
            if sys.flags.isolated:
                # Embedded Python omits cwd from sys.path; mirror the tray bootstrap.
                command = [sys.executable, '-c',
                           'import sys,runpy;sys.path.insert(0,sys.argv.pop(1));'
                           'runpy.run_module(sys.argv.pop(1),run_name="__main__")',
                           str(ROOT), 'platforms.windows.' + name, '--help']
            result = subprocess.run(command,
                                    cwd=ROOT, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('usage:', result.stdout)
