import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mac_bridge import parse_args, resolve_macos_codex_binary


NEW = "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
OLD = "Contents/Resources/codex"


@unittest.skipIf(os.name == "nt", "macOS executable discovery uses POSIX file modes")
class MacCliDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "ChatGPT.app"

    def binary(self, relative, app=None, executable=True):
        path = (app or self.app) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture, never executed", encoding="utf-8")
        path.chmod(0o700 if executable else 0o600)
        return path.resolve()

    def discover(self):
        return resolve_macos_codex_binary(app_roots=[self.app])

    def test_new_nested_cli(self):
        expected = self.binary(NEW)
        self.assertEqual(self.discover(), expected)

    def test_legacy_cli(self):
        expected = self.binary(OLD)
        self.assertEqual(self.discover(), expected)

    def test_nested_layout_wins_within_same_app(self):
        self.binary(OLD)
        expected = self.binary(NEW)
        self.assertEqual(self.discover(), expected)

    def test_rechecks_layout_after_upgrade(self):
        old = self.binary(OLD)
        self.assertEqual(self.discover(), old)
        old.unlink()
        new = self.binary(NEW)
        self.assertEqual(self.discover(), new)

    def test_nonexecutable_nested_cli_falls_back(self):
        self.binary(NEW, executable=False)
        expected = self.binary(OLD)
        self.assertEqual(self.discover(), expected)

    def test_directory_and_missing_binary_rejected(self):
        (self.app / NEW).mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "not found"):
            self.discover()

    def test_missing_explicit_path_never_falls_back(self):
        self.binary(NEW)
        with self.assertRaisesRegex(ValueError, "--codex-binary"):
            resolve_macos_codex_binary(self.root / "missing", app_roots=[self.app])

    def test_explicit_override_wins(self):
        self.binary(NEW)
        selected = self.binary("custom/codex", app=self.root)
        self.assertEqual(resolve_macos_codex_binary(selected, app_roots=[self.app]), selected)

    def test_two_installations_require_explicit_selection(self):
        self.binary(NEW)
        other = self.root / "Codex.app"
        self.binary(OLD, app=other)
        with self.assertRaisesRegex(ValueError, "Multiple"):
            resolve_macos_codex_binary(app_roots=[self.app, other])

    def test_application_alias_is_deduplicated(self):
        expected = self.binary(NEW)
        alias = self.root / "Codex.app"
        alias.symlink_to(self.app, target_is_directory=True)
        self.assertEqual(resolve_macos_codex_binary(app_roots=[self.app, alias]), expected)

    def test_standard_user_install_location(self):
        app = self.root / "Applications" / "Codex.app"
        expected = self.binary(NEW, app=app)
        with patch("mac_bridge.Path.home", return_value=self.root), patch(
            "mac_bridge.os.access", side_effect=lambda path, mode: path == expected
        ):
            self.assertEqual(resolve_macos_codex_binary(), expected)

    def test_no_path_fallback(self):
        self.binary("bin/codex", app=self.root)
        with patch.dict(os.environ, {"PATH": str(self.root / "bin")}):
            with self.assertRaisesRegex(ValueError, "not found"):
                self.discover()


class MacCliArgumentsTest(unittest.TestCase):
    def test_default_is_auto_discovery(self):
        with patch("sys.argv", ["mac_bridge.py"]):
            self.assertIsNone(parse_args().codex_binary)

    def test_explicit_path_is_preserved(self):
        with patch("sys.argv", ["mac_bridge.py", "--codex-binary", "/custom/codex"]):
            self.assertEqual(parse_args().codex_binary, Path("/custom/codex"))
