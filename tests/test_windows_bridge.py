import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import test_mac_bridge as fixtures
from bridge_runtime import BridgeRuntime, WINDOWS_PREVIEW
from mac_bridge import BridgeHandler, DeviceRegistry, create_projectless_workspace, thread_turn_is_active
from windows_bridge import (
    NoDesktopController, codex_binary_path, main, pairing_url, parse_args,
    read_windows_battery, state_directory, windows_token, make_server, local_request,
)
from codex_app_server import AppServerError


class WindowsUnreadApiTest(unittest.TestCase):
    request = fixtures.BridgeApiTest.request
    tearDown = fixtures.BridgeApiTest.tearDown

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        from windows_bridge import WindowsBridgeServer
        self.server.__class__ = WindowsBridgeServer

    def test_read_endpoint_uses_windows_acknowledgement_without_json_write(self):
        before = self.codex_state_path.read_bytes()
        payload = {'scope': 'account-host', 'revision': '123'}
        with patch('windows_read_state.mark_read', return_value=True) as mark:
            status, result = self.request('POST', '/api/codex/threads/thread-2/read', payload)
        self.assertEqual(status, 200)
        self.assertFalse(result['isUnread'])
        mark.assert_called_once_with(self.codex_state_path, self.app_server, 'thread-2', payload)
        self.assertEqual(self.codex_state_path.read_bytes(), before)

    def test_unconfirmed_ack_is_error_not_false_success(self):
        with patch('windows_read_state.mark_read', side_effect=AppServerError('unconfirmed')):
            status, result = self.request('POST', '/api/codex/threads/thread-2/read', {})
        self.assertEqual(status, 502)
        self.assertFalse(result['ok'])

    def test_read_ack_requires_device_auth(self):
        with patch('windows_read_state.mark_read') as mark:
            status, _ = self.request('POST', '/api/codex/threads/thread-2/read', {}, authorized=False)
        self.assertEqual(status, 401)
        mark.assert_not_called()


class WindowsPreviewApiTest(unittest.TestCase):
    # Reuse the HTTP fixture without inheriting/re-running Mac-specific tests.
    request = fixtures.BridgeApiTest.request
    tearDown = fixtures.BridgeApiTest.tearDown

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        self.server.runtime = WINDOWS_PREVIEW
        self.server.controller = NoDesktopController()
        self.server.screen_lock_probe = lambda: False
        self.server.battery_probe = lambda: {"available": True, "percent": 61}

    def test_background_mode_is_not_a_fake_screen_lock(self):
        self.assertFalse(self.server.screen_is_locked())
        self.assertTrue(self.server.uses_background_execution())
        status, payload = self.request("GET", "/api/desktop/interrupt/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["taskTitle"], "")
        self.assertFalse(payload["interruptible"])
        self.assertEqual(payload["capabilities"], WINDOWS_PREVIEW.capabilities())

    def test_capability_status_still_requires_auth(self):
        status, _ = self.request("GET", "/api/desktop/interrupt/status", authorized=False)
        self.assertEqual(status, 401)

    def test_windows_metrics_do_not_call_mac_probes(self):
        with patch("mac_bridge.read_local_hotspot_status", side_effect=AssertionError):
            status, payload = self.request("GET", "/api/system/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(payload["battery"]["percent"], 61)
        self.assertFalse(payload["localHotspot"]["configured"])

    def test_send_and_stop_owned_background_turn_without_desktop(self):
        status, payload = self.request("POST", "/api/codex/threads/thread-1/turn", {"message": "中文 test"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["mode"], "background")
        self.assertEqual(self.app_server.started, [("thread-1", "中文 test")])
        status, _ = self.request("POST", "/api/codex/threads/thread-1/interrupt", {})
        self.assertEqual(status, 400)
        status, payload = self.request("POST", "/api/codex/threads/thread-1/interrupt", {"confirm": True})
        self.assertEqual(status, 202)
        self.assertEqual(payload["run"]["status"], "interrupting")

    def test_active_desktop_turn_is_not_taken_over(self):
        self.app_server.last_turn_status = "inProgress"
        status, payload = self.request("POST", "/api/codex/threads/thread-1/turn", {"message": "test"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "desktop_turn_active")
        self.assertEqual(self.app_server.started, [])

    def test_continue_interrupted_turn(self):
        self.app_server.last_turn_status = "interrupted"
        status, payload = self.request("POST", "/api/codex/threads/thread-1/continue", {})
        self.assertEqual(status, 202)
        self.assertEqual(payload["mode"], "background")
        self.assertEqual(self.app_server.continued, ["thread-1"])

    def test_new_task_keeps_project_assignment(self):
        status, payload = self.request("POST", "/api/codex/threads", {"projectId": "project-id", "message": "hello"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["mode"], "background")
        self.assertEqual(payload["thread"]["project"]["id"], "project-id")
        self.assertEqual(len(self.app_server.started), 1)

    def test_recents_task_stays_projectless(self):
        status, payload = self.request("POST", "/api/codex/threads", {"projectId": None, "message": "CON"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["thread"]["collection"], "recent")

    def test_native_stop_and_approval_are_explicitly_unsupported(self):
        for path in ("/api/desktop/interrupt", "/api/desktop/request"):
            with self.subTest(path=path):
                status, payload = self.request("POST", path, {})
                self.assertEqual(status, 501)
                self.assertEqual(payload["error"], "desktop_control_unsupported")

    def test_upload_is_rejected_before_any_disk_write(self):
        device = self.server.device_registry.enroll("test", "test")
        # Repeat: Windows can reset a closed socket with unread request bytes.
        for _ in range(20):
            status, payload = self.request("POST", "/api/attachments", {"fake": "body"}, bearer=device["deviceToken"])
            self.assertEqual(status, 501)
            self.assertEqual(payload["error"], "attachments_unsupported")
        self.assertFalse(self.server.attachment_store.root.exists())

    def test_cannot_bypass_attachment_capability_with_existing_id(self):
        device = self.server.device_registry.enroll("test", "test")
        attachment = self.server.attachment_store.create(device["id"], "image.png", "image/png", io.BytesIO(b"test"), 4)
        status, payload = self.request("POST", "/api/codex/threads", {
            "message": "look", "attachmentIds": [attachment["id"]],
        }, bearer=device["deviceToken"])
        self.assertEqual(status, 501)
        self.assertEqual(payload["error"], "attachments_unsupported")
        self.assertEqual(self.app_server.started, [])


class WindowsLauncherTest(unittest.TestCase):
    def test_rejection_drain_does_not_read_large_or_ambiguous_bodies(self):
        for headers in ({"Content-Length": "65537"}, {"Content-Length": "invalid"},
                        {"Content-Length": "-1"}, {"Content-Length": "2", "Transfer-Encoding": "chunked"}):
            handler = object.__new__(BridgeHandler)
            handler.headers = headers
            handler.rfile = Mock()
            handler._discard_small_rejected_body()
            handler.rfile.read1.assert_not_called()

    def test_rejection_drain_restores_timeout_and_preserves_following_bytes(self):
        handler = object.__new__(BridgeHandler)
        handler.headers = {"Content-Length": "2"}
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = 7
        handler.rfile = io.BytesIO(b"oknext")
        handler._discard_small_rejected_body()
        self.assertEqual(handler.rfile.read(), b"next")
        handler.connection.settimeout.assert_called_with(7)
        handler.rfile = Mock()
        handler.rfile.read1.side_effect = TimeoutError
        handler._discard_small_rejected_body()
        handler.connection.settimeout.assert_called_with(7)

    def test_mac_capabilities_are_unchanged_by_default(self):
        self.assertTrue(BridgeRuntime().desktop_control)
        self.assertEqual(BridgeRuntime().execution_mode, "desktop")
        with self.assertRaises(ValueError):
            BridgeRuntime(execution_mode="background")

    def test_thread_runtime_status_blocks_takeover_even_without_turns(self):
        self.assertTrue(thread_turn_is_active({"status": {"type": "active"}, "turns": []}))

    def test_pair_link_carries_only_single_use_ticket(self):
        self.assertEqual(pairing_url("https://pc.example.ts.net", "abc/+"),
                         "https://pc.example.ts.net/#pairing=abc%2F%2B")
        for origin in ("http://pc/", "https://u:p@pc", "https://pc/?token=x", "https://pc/#x", "https://pc/path"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                pairing_url(origin, "ticket")

    def test_no_implicit_binary_or_shell_shim(self):
        for value in (None, "codex.exe", "codex.cmd", "wsl"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                codex_binary_path(value)
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "a path 中文" / "codex.exe"
            binary.parent.mkdir()
            binary.touch()
            self.assertEqual(codex_binary_path(str(binary)), binary.resolve())

    def test_state_is_per_user(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            self.assertEqual(state_directory(), Path(directory) / "CodexPocket")

    @patch("windows_bridge.subprocess.run")
    def test_secret_is_not_in_command_line_or_error_logs(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "s" * 44 + "\n", "")
        self.assertEqual(windows_token(), "s" * 44)
        self.assertNotIn("s" * 44, str(run.call_args.args))
        self.assertNotIn("-Command", run.call_args.args[0])
        run.return_value = subprocess.CompletedProcess([], 1, "secret", "secret")
        with self.assertRaises(RuntimeError) as caught:
            windows_token()
        self.assertNotIn("secret", str(caught.exception).replace("windows-pocket-secret.ps1", ""))

    def test_port_validation(self):
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["serve", "--port", "0"])

    def test_windows_entry_point_cannot_start_on_mac(self):
        with patch("windows_bridge.sys.platform", "darwin"), self.assertRaises(SystemExit):
            main(["init"])

    def test_reserved_windows_workspace_names(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(create_projectless_workspace(Path(directory), "CON").name, "task-con")

    def test_registry_without_posix_fchmod(self):
        with tempfile.TemporaryDirectory() as directory:
            # Windows lacks fchmod; ACL protection belongs to the native launcher.
            fchmod = getattr(os, "fchmod", None)
            try:
                if fchmod is not None:
                    del os.fchmod
                registry = DeviceRegistry(Path(directory) / "devices.json")
                registry.ensure_ready()
                device = registry.enroll("中文 phone", "test")
                self.assertEqual(registry.count(), 1)
                self.assertNotIn(device["deviceToken"], registry.path.read_text(encoding="utf-8"))
            finally:
                if fchmod is not None:
                    os.fchmod = fchmod


@unittest.skipUnless(sys.platform == "win32", "requires Windows DPAPI and NTFS ACLs")
class WindowsNativeSecurityTest(unittest.TestCase):
    def test_diagnostic_classification_and_direct_utf8_output_without_ui(self):
        with tempfile.TemporaryDirectory(prefix="pocket 中文 ") as directory:
            report = Path(directory) / "report.json"
            script = Path(__file__).with_name("test_windows_desktop_diagnostic.ps1")
            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
                 "-File", str(script), "-ReportPath", str(report)],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data, {"label": "恢复", "testsPassed": 53})

    def test_dpapi_and_acl_roundtrip_in_isolated_directory(self):
        with tempfile.TemporaryDirectory(prefix="pocket 中文 ") as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            token = windows_token(initialize=True)
            self.assertEqual(windows_token(), token)
            self.assertEqual(windows_token(initialize=True), token)
            self.assertNotIn(token.encode(), (state_directory() / "token.dpapi").read_bytes())
            registry = DeviceRegistry(state_directory() / "devices.json")
            registry.ensure_ready()
            registry.enroll("测试", "test")
            # Registry writes inherit the protected root DACL.
            self.assertEqual(windows_token(), token)

    def test_corrupt_credential_is_not_silently_regenerated(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            windows_token(initialize=True)
            secret_file = state_directory() / "token.dpapi"
            secret_file.write_bytes(b"corrupted")
            with self.assertRaises(RuntimeError):
                windows_token(initialize=True)
            self.assertEqual(secret_file.read_bytes(), b"corrupted")

    @unittest.skipUnless(os.environ.get("CODEX_POCKET_TEST_BINARY"), "requires explicit native Codex test binary")
    def test_real_private_backend_with_dpapi_and_loopback_http(self):
        # No model calls, account credentials, real workspace, or saved sidebar.
        binary = Path(os.environ["CODEX_POCKET_TEST_BINARY"])
        with tempfile.TemporaryDirectory(prefix="pocket smoke ") as directory, \
                patch.dict(os.environ, {"LOCALAPPDATA": directory, "CODEX_HOME": directory}):
            token = windows_token(initialize=True)
            server = make_server(binary, token, 0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                host, port = server.server_address
                self.assertEqual(host, "127.0.0.1")
                status = local_request(port, token, "/api/desktop/interrupt/status")
                self.assertEqual(status["capabilities"], WINDOWS_PREVIEW.capabilities())
                ticket = local_request(port, token, "/api/devices/pairing-ticket", method="POST", body={})
                self.assertTrue(ticket["pairingTicket"])
                self.assertNotEqual(ticket["pairingTicket"], token)
                self.assertEqual(server.app_server.request("thread/loaded/list", {})["data"], [])
            finally:
                server.shutdown()
                worker.join(timeout=5)
                server.server_close()
