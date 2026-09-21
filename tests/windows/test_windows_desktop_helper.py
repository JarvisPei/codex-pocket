import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from platforms.windows import desktop_helper as helper
class DesktopHelperTest(unittest.TestCase):
    def test_worker_refuses_ssh_session_before_opening_mailbox(self):
        with patch.object(helper, "session_id", return_value=0), \
                patch.object(helper, "acquire_lock") as lock:
            with self.assertRaisesRegex(RuntimeError, "desktop_powershell"):
                helper.serve(Path("unused"), "test-key")
            lock.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "requires Windows byte-range locks")
    def test_native_worker_and_client_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for acquire in (helper.acquire_lock, helper.acquire_client_lock):
                first = acquire(root)
                try:
                    with self.assertRaises(RuntimeError):
                        acquire(root)
                finally:
                    first.close()
                again = acquire(root)
                again.close()

    def test_authentication_and_unicode_roundtrip(self):
        signed = helper.sign({"label": "恢复"}, "secret")
        self.assertEqual(helper.verify(signed, "secret"), {"label": "恢复"})
        for envelope in ({**signed, "mac": "f" * 64}, {**signed, "mac": "中" * 64},
                         {**signed, "body": "{}"}, {"body": "x"}, []):
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                helper.verify(envelope, "secret")

    def test_requests_are_fixed_schema_expiring_and_instance_bound(self):
        request = {"id": "a" * 32, "instance": "current", "action": "diagnose", "createdAt": 100}
        helper.validate_request(request, "current", 110)
        for change in ({"command": "whoami"}, {"path": "file"}, {"action": "send"},
                       {"action": []}, {"instance": "old"}, {"id": "../response"},
                       {"createdAt": 0}, {"createdAt": 120}, {"createdAt": float("nan")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                helper.validate_request({**request, **change}, "current", 110)

    def test_signed_files_are_bounded_and_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            helper.write_signed(path, {"sessionId": 1}, "key")
            self.assertEqual(helper.read_signed(path, "key"), {"sessionId": 1})
            with self.assertRaises(ValueError):
                helper.read_signed(path, "wrong-key")
            with patch.object(helper, "LIMIT", 16), self.assertRaises(ValueError):
                helper.read_signed(path, "key")
            self.assertEqual([p.name for p in root.iterdir()], ["state.json"])

    def test_windows_atomic_write_retries_transient_sharing_errors(self):
        for code in (5, 32, 33):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "state.json"
                helper.write_signed(path, {"value": "old"}, "key")
                error = PermissionError("sharing conflict")
                error.winerror = code
                replace = os.replace
                attempts = []

                def transient(source, target):
                    attempts.append(target)
                    if len(attempts) < 3:
                        raise error
                    replace(source, target)

                with patch.object(helper.sys, "platform", "win32"), \
                        patch.object(helper.os, "replace", side_effect=transient), \
                        patch.object(helper.time, "sleep") as sleep:
                    helper.write_signed(path, {"value": "new"}, "key")
                self.assertEqual(len(attempts), 3)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(helper.read_signed(path, "key"), {"value": "new"})
                self.assertEqual(list(root.iterdir()), [path])

    def test_atomic_write_failure_is_bounded_and_preserves_existing_state(self):
        for platform, code, attempts in (("win32", 5, 6), ("win32", 999, 1), ("darwin", 5, 1)):
            with self.subTest(platform=platform, code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "state.json"
                helper.write_signed(path, {"value": "old"}, "key")
                error = PermissionError("access denied")
                error.winerror = code
                with patch.object(helper.sys, "platform", platform), \
                        patch.object(helper.os, "replace", side_effect=error) as replace, \
                        patch.object(helper.time, "sleep"), self.assertRaises(PermissionError):
                    helper.write_signed(path, {"value": "new"}, "key")
                self.assertEqual(replace.call_count, attempts)
                self.assertEqual(helper.read_signed(path, "key"), {"value": "old"})
                self.assertEqual(list(root.iterdir()), [path])

    @unittest.skipIf(os.name == "nt", "creating test symlinks can require elevation")
    def test_symlink_mailbox_files_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("original")
            link = root / "response.json"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                helper.write_signed(link, {}, "key")
            with self.assertRaises(ValueError):
                helper.read_signed(link, "key")
            self.assertEqual(target.read_text(), "original")

    def test_fixed_diagnostic_command_and_timeout(self):
        with patch.object(helper.subprocess, "run") as run:
            run.return_value = Mock(returncode=0, stdout='{"readOnly":true,"sessionId":1}')
            self.assertEqual(helper.run_diagnostic()["report"]["sessionId"], 1)
            command = run.call_args.args[0]
            self.assertIn("-File", command)
            self.assertNotIn("-Command", command)
            self.assertEqual(command[-2:], ["-View", "Raw"])
            self.assertEqual(run.call_args.kwargs["timeout"], 25)
            run.side_effect = subprocess.TimeoutExpired(command, 25)
            self.assertEqual(helper.run_diagnostic(), {"error": "diagnostic_timeout"})
            run.side_effect = UnicodeError()
            self.assertEqual(helper.run_diagnostic(), {"error": "diagnostic_failed"})

    def test_native_delivery_only_uses_fixed_script_and_stdin(self):
        delivery = {"threadId": "11111111-1111-4111-8111-111111111111", "expectedTitle": "test", "message": "private"}
        with patch.object(helper.subprocess, "run") as run:
            run.return_value = Mock(returncode=0, stdout=json.dumps({"status": "invoke_requested", "targetThreadId": delivery["threadId"]}))
            self.assertEqual(helper.run_native_send(delivery)["nativeSend"]["status"], "invoke_requested")
            self.assertNotIn("private", " ".join(run.call_args.args[0]))
            self.assertNotIn("-AllowDisplayOff", run.call_args.args[0])
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), delivery)
            run.side_effect = subprocess.TimeoutExpired([], 55)
            self.assertEqual(helper.run_native_send(delivery)["nativeSend"]["status"], "uncertain")

    def test_display_off_requires_explicit_local_opt_in(self):
        delivery = {"threadId": "11111111-1111-4111-8111-111111111111", "expectedTitle": "test", "message": "private"}
        with patch.object(helper.subprocess, "run") as run:
            run.return_value = Mock(returncode=0, stdout='{"status":"refused"}')
            helper.run_native_send(delivery, allow_display_off=True)
            self.assertEqual(run.call_args.args[0].count("-AllowDisplayOff"), 1)
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), delivery)

    def test_taskbar_activation_requires_local_opt_in(self):
        delivery = {"threadId": "11111111-1111-4111-8111-111111111111", "expectedTitle": "test", "message": "private"}
        with patch.object(helper.subprocess, "run") as run:
            run.return_value = Mock(returncode=0, stdout=json.dumps({"status": "refused", "targetThreadId": delivery["threadId"]}))
            helper.run_native_send(delivery)
            self.assertNotIn("-AllowTaskbarActivation", run.call_args.args[0])
            helper.run_native_send(delivery, allow_taskbar_activation=True)
            self.assertEqual(run.call_args.args[0].count("-AllowTaskbarActivation"), 1)
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), delivery)
        with self.assertRaises(SystemExit):
            helper.main(["serve", "--enable-taskbar-activation"])

    def test_native_delivery_request_schema(self):
        request = {"id": "a" * 32, "instance": "current", "action": "native-send", "createdAt": 100,
                   "delivery": {"threadId": "11111111-1111-4111-8111-111111111111", "expectedTitle": "name", "message": "text"}}
        helper.validate_request(request, "current", 110)
        with self.assertRaises(ValueError):
            helper.validate_request({**request, "delivery": {**request["delivery"], "command": "x"}}, "current", 110)
        for field in ("allowDisplayOff", "allow_display_off", "allowTaskbarActivation", "allow_taskbar_activation"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                helper.validate_request({**request, "delivery": {**request["delivery"], field: True}}, "current", 110)
            with self.subTest(top_level=field), self.assertRaises(ValueError):
                helper.validate_request({**request, field: True}, "current", 110)

    def test_fixed_smoke_send_command_and_uncertain_timeout(self):
        with patch.object(helper.subprocess, "run") as run:
            run.return_value = Mock(returncode=0, stdout='{"action":"smoke-send","status":"invoke_requested"}')
            self.assertEqual(helper.run_smoke_send()["smokeSend"]["status"], "invoke_requested")
            command = run.call_args.args[0]
            self.assertTrue(command[-1].endswith("windows-desktop-smoke-send.ps1"))
            self.assertNotIn("-Command", command)
            self.assertEqual(run.call_args.kwargs["timeout"], 25)
            run.side_effect = subprocess.TimeoutExpired(command, 25)
            self.assertEqual(helper.run_smoke_send()["smokeSend"]["status"], "outcome_unknown")

    def test_smoke_send_requires_opt_in_and_is_one_attempt_even_if_uncertain(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory, \
                    patch.object(helper, "run_smoke_send", return_value={"smokeSend": {"status": "outcome_unknown"}}) as send, \
                    patch.object(helper, "acquire_client_lock", return_value=Mock()):
                root = Path(directory)
                worker = threading.Thread(target=helper.worker_loop, args=(root, "key", 1),
                                          kwargs={"enable_smoke_send": enabled}, daemon=True)
                worker.start()

                def ready():
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        try:
                            state = helper.read_signed(root / "state.json", "key")
                            if state["status"] == "ready":
                                return state
                        except FileNotFoundError:
                            pass
                        time.sleep(0.02)
                    self.fail("worker did not become ready")

                try:
                    self.assertEqual(ready()["smokeSendAvailable"], enabled)
                    first = helper.request_action(root, "key", "smoke-send")
                    self.assertFalse(ready()["smokeSendAvailable"])
                    if enabled:
                        self.assertEqual(first["smokeSend"]["status"], "outcome_unknown")
                    else:
                        self.assertIn("error", first)
                    second = helper.request_action(root, "key", "smoke-send")
                    self.assertIn("error", second)
                    ready()
                    self.assertEqual(send.call_count, int(enabled))
                finally:
                    ready()
                    helper.request_action(root, "key", "stop")
                    worker.join(timeout=3)
                    self.assertFalse(worker.is_alive())

    def test_stale_status_does_not_claim_reachable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper.write_signed(root / "state.json", {"status": "ready", "updatedAt": 1}, "key")
            self.assertFalse(helper.request_action(root, "key", "status")["reachable"])
            with self.assertRaises(RuntimeError):
                helper.request_action(root, "key", "diagnose")
            self.assertFalse((root / "request.json").exists())

    def test_mailbox_roundtrip_and_stop_only_worker(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(helper, "run_diagnostic", return_value={"report": {"readOnly": True}}) as probe, \
                patch.object(helper, "acquire_client_lock", return_value=Mock()):
            root = Path(directory)
            worker = threading.Thread(target=helper.worker_loop, args=(root, "key", 1), daemon=True)
            worker.start()
            try:
                deadline = time.monotonic() + 3
                while not (root / "state.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(helper.request_action(root, "key", "status")["reachable"])
                result = helper.request_action(root, "key", "diagnose")
                self.assertTrue(result["report"]["readOnly"])
                deadline = time.monotonic() + 3
                while helper.read_signed(root / "state.json", "key")["status"] != "ready" and time.monotonic() < deadline:
                    time.sleep(0.02)
                result = helper.request_action(root, "key", "stop")
                self.assertTrue(result["stopped"])
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(helper.request_action(root, "key", "status")["reachable"])
                probe.assert_called_once()
            finally:
                if worker.is_alive():
                    state = helper.read_signed(root / "state.json", "key")
                    helper.write_signed(root / "request.json", {"id": "f" * 32, "instance": state["instance"],
                                        "action": "stop", "createdAt": time.time()}, "key")
                    worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
