import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from codex_app_server import AppServerError
from platforms.windows.delivery import DeliveryJournal, user_receipts, validate_delivery
from platforms.windows.native import NativeTextController, WindowsNativeHandler
from bridge_runtime import WINDOWS_NATIVE_TEXT
from tests import test_mac_bridge as fixtures

# Synthetic fixture IDs; never target a real Desktop task.
THREAD = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def thread(message=None, item_id="item-1", thread_id=THREAD):
    return {"id": thread_id, "name": "Current name", "status": {"type": "idle"}, "turns": [] if message is None else [{
        "id": "turn-1", "status": "completed", "items": [{"id": item_id, "type": "userMessage", "content": [{"type": "text", "text": message}]}]}]}


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = thread()
        self.client = Mock()
        self.client.read_thread.side_effect = lambda _: {"thread": copy.deepcopy(self.data)}
        self.client.request.return_value = {"data": [{"id": THREAD, "name": "Current name"}], "nextCursor": None}
        self.send = Mock(side_effect=self.sent)
        self.controller = NativeTextController(self.client, self.root, "key", dispatch=self.send, attempts=1)
        self.request_id = str(uuid.uuid4())

    def sent(self, payload):
        self.data = thread(payload["message"] + "\n")
        return {"status": "invoke_requested", "targetThreadId": payload["threadId"]}

    def test_receipt_is_exact_thread_message_and_stable_item(self):
        self.assertTrue(user_receipts(thread("hello\n"), THREAD, "hello"))
        for data in (thread("hello suffix"), thread("prefix hello"), thread("hello", thread_id=OTHER), thread("hello", item_id=""), thread("hello\n\n")):
            self.assertFalse(user_receipts(data, THREAD, "hello"))

    def test_unknown_content_cannot_confirm_text(self):
        data = thread("hello")
        data["turns"][0]["items"][0]["content"].append({"type": "image", "url": "private"})
        self.assertFalse(user_receipts(data, THREAD, "hello"))

    def test_observed_desktop_underscore_serialization(self):
        self.assertTrue(user_receipts(thread("POCKET\\_WINDOWS\\_OK\n"), THREAD, "POCKET_WINDOWS_OK"))
        self.assertFalse(user_receipts(thread("prefix POCKET\\_WINDOWS\\_OK\n"), THREAD, "POCKET_WINDOWS_OK"))
        self.assertFalse(user_receipts(thread("POCKET\\_WINDOWS\\_OK\n\n"), THREAD, "POCKET_WINDOWS_OK"))
        self.assertFalse(user_receipts(thread("a\\\\_b\n"), THREAD, "a\\_b"))

    def test_confirmed_request_is_idempotent_even_when_reopened(self):
        result = self.controller.deliver(self.request_id, THREAD, "hello")
        self.assertTrue(result["ok"])
        second = NativeTextController(self.client, self.root, "key", dispatch=self.send, attempts=1)
        self.assertTrue(second.deliver(self.request_id, THREAD, "hello")["ok"])
        self.assertEqual(self.send.call_count, 1)
        self.client.start_turn.assert_not_called()

    def test_observed_multiline_file_path_serialization(self):
        message = 'Read this_file\n\n["C:/files/upload_id.txt"]\nOnly read it.'
        serialized = 'Read this\\_file\\\n\\\n["C:/files/upload\\_id.txt"]\\\nOnly read it.\n'
        self.assertTrue(user_receipts(thread(serialized), THREAD, message))
        for changed in ('prefix ' + serialized, serialized + '\n',
                        serialized.replace('upload\\_id', 'other\\_id'),
                        serialized.replace('\\\n\\\n', '\\\n'),
                        serialized.replace('Only read', 'Only  read')):
            self.assertFalse(user_receipts(thread(changed), THREAD, message))
        self.assertTrue(user_receipts(thread('one\\\ntwo\n'), THREAD, 'one\ntwo'))
        self.assertFalse(user_receipts(thread('one\\\ntwo\n'), THREAD, 'one\r\ntwo'))
        self.assertFalse(user_receipts(thread('one\\\ntwo\n'), THREAD, 'one\\ntwo'))

    def test_multiline_late_receipt_confirms_without_second_ui_call(self):
        message = 'Read file\n["C:/file_id.txt"]'
        self.send.side_effect = lambda _: {'status': 'invoke_requested'}
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, message)['ok'])
        self.data = thread('Read file\\\n["C:/file\\_id.txt"]\n')
        result = self.controller.deliver(self.request_id, THREAD, message)
        self.assertTrue(result['ok'])
        self.assertTrue(self.controller.deliver(self.request_id, THREAD, message)['desktop']['duplicateRequest'])
        self.assertEqual(self.send.call_count, 1)

    def test_old_serialized_multiline_message_does_not_confirm_new_send(self):
        self.data = thread('one\\\ntwo\n')
        self.send.side_effect = lambda _: {'status': 'invoke_requested'}
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, 'one\ntwo')['ok'])

    def test_same_id_cannot_change_content(self):
        self.controller.deliver(self.request_id, THREAD, "hello")
        with self.assertRaisesRegex(ValueError, "different_content"):
            self.controller.deliver(self.request_id, THREAD, "changed")
        self.assertEqual(self.send.call_count, 1)

    def test_timeout_cannot_retry_ui_and_blocks_fresh_request_ids(self):
        self.send.side_effect = TimeoutError()
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, "hello")["retryAllowed"])
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, "hello")["retryAllowed"])
        with self.assertRaisesRegex(ValueError, "confirmation_pending"):
            self.controller.deliver(str(uuid.uuid4()), THREAD, "different")
        self.assertEqual(self.send.call_count, 1)

    def test_late_receipt_reconciles_without_resending(self):
        self.send.side_effect = TimeoutError()
        self.controller.deliver(self.request_id, THREAD, "hello")
        self.data = thread("hello\n")
        self.assertTrue(self.controller.deliver(self.request_id, THREAD, "hello")["ok"])
        self.assertEqual(self.send.call_count, 1)

    def test_default_window_accepts_receipt_after_three_seconds(self):
        self.controller.attempts = 32
        self.send.side_effect = lambda _: {'status': 'invoke_requested'}
        sleeps = []
        def tick(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 16:
                self.data = thread('hello\n')
        with patch('platforms.windows.native.time.sleep', side_effect=tick):
            self.assertTrue(self.controller.deliver(self.request_id, THREAD, 'hello')['ok'])
        self.assertEqual(len(sleeps), 16)
        self.send.assert_called_once()

    def test_receipt_only_missing_request_never_claims_or_dispatches(self):
        result = self.controller.deliver(self.request_id, THREAD, 'hello', receipt_only=True)
        self.assertEqual(result['error'], 'native_receipt_pending')
        self.assertIsNone(self.controller.journal.lookup(self.request_id, THREAD, 'hello'))
        self.send.assert_not_called()
        self.client.read_thread.assert_not_called()

    def test_receipt_only_after_restart_reconciles_but_never_reissues(self):
        self.send.side_effect = TimeoutError()
        self.controller.deliver(self.request_id, THREAD, 'hello')
        second = NativeTextController(self.client, self.root, 'key', dispatch=self.send, attempts=1)
        self.assertFalse(second.deliver(self.request_id, THREAD, 'hello', receipt_only=True)['ok'])
        self.data = thread('hello\n')
        self.assertTrue(second.deliver(self.request_id, THREAD, 'hello', receipt_only=True)['ok'])
        self.assertTrue(second.deliver(self.request_id, THREAD, 'hello', receipt_only=True)['desktop']['duplicateRequest'])
        with self.assertRaisesRegex(ValueError, 'different_content'):
            second.deliver(self.request_id, THREAD, 'changed', receipt_only=True)
        self.send.assert_called_once()

    def test_receipt_only_does_not_accept_old_or_multiple_matches(self):
        self.data = thread('hello')
        self.send.side_effect = TimeoutError()
        self.controller.deliver(self.request_id, THREAD, 'hello')
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, 'hello', receipt_only=True)['ok'])
        newer = thread('hello', item_id='new')['turns'][0]['items'][0]
        self.data['turns'][0]['items'].extend([newer, {**newer, 'id': 'another'}])
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, 'hello', receipt_only=True)['ok'])
        self.send.assert_called_once()

    def test_old_identical_message_does_not_confirm(self):
        self.data = thread("hello\n")
        self.send.side_effect = lambda _: {"status": "invoke_requested"}
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, "hello")["ok"])

    def test_existing_draft_refusal_does_not_retry_same_id(self):
        self.send.side_effect = lambda _: {"status": "refused", "reason": "desktop_draft_present"}
        result = self.controller.deliver(self.request_id, THREAD, "hello")
        self.assertEqual(result["error"], "native_desktop_draft_present")
        self.assertTrue(result["retryAllowed"])
        self.controller.deliver(self.request_id, THREAD, "hello")
        self.assertEqual(self.send.call_count, 1)

    def test_draft_guard_checks_verified_destination_not_source_task(self):
        script = (Path(__file__).resolve().parents[2] / 'platforms/windows/scripts/windows-desktop-send.ps1').read_text()
        flow = script[script.index('    $snapshot=$null\n'):]
        navigation = flow.index("Start-Process -FilePath ('codex://threads/'+$payload.threadId)")
        identity = flow.index('    Assert-PocketTarget $snapshot', navigation)
        draft_guard = flow.index('Test-PocketEmptyComposer')
        write = flow.index('$snapshot.value.SetValue($payload.message)')
        self.assertLess(navigation, identity)
        self.assertLess(identity, draft_guard)
        self.assertLess(draft_guard, write)
        # The display-off experiment added a final empty-draft recheck before
        # SetValue. Every check must still be scoped to the verified target.
        offset = 0
        checks = []
        while (offset := flow.find('Test-PocketEmptyComposer', offset)) != -1:
            checks.append(offset)
            offset += 1
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(identity < position < write for position in checks))
        self.assertIn("$result.draftScope='verified_target'", flow)

    def test_activation_precedes_composer_scan_but_not_safety_guards(self):
        script = (Path(__file__).resolve().parents[2] / 'platforms/windows/scripts/windows-desktop-send.ps1').read_text()
        start = script.index('    if (Test-PocketPreactivate $ActionPhase')
        flow = script[start:]
        self.assertLess(flow.index('Request-PocketForeground $sourceWindow $true'),
                        flow.index('$snapshot=Get-PocketSnapshot'))
        self.assertLess(flow.index('Assert-PocketTarget $snapshot'),
                        flow.index('$snapshot.value.SetValue($payload.message)'))
        activation = script[script.index('    function Request-PocketForeground'):script.index('    function Get-PocketSnapshot')]
        self.assertIn('Assert-PocketWindowHandle $window', activation)
        self.assertIn('ShowWindowAsync($handle,9)', activation)
        self.assertIn('AddSeconds(2)', activation)
        self.assertNotIn('SetValue', activation)
        self.assertNotIn('AttachThreadInput', script)
        self.assertNotIn('SendKeys', script)
        self.assertIn('win32Accepted=[PocketDeliveryDesktop]::SetForegroundWindow($handle)', activation)
        self.assertNotIn('[Microsoft.VisualBasic.Interaction]::AppActivate', activation)
        self.assertNotIn('.SetFocus()', activation)
        self.assertIn('$activationLogs.Count -lt 4', activation)
        self.assertIn('$activation.confirmed=[PocketDeliveryDesktop]::GetForegroundWindow() -eq $handle', activation)
        self.assertNotIn('Request-PocketForeground', script[script.index('$snapshot.value.SetValue($payload.message)'):])

    def test_readiness_retry_is_only_before_write_with_bounded_telemetry(self):
        script = (Path(__file__).resolve().parents[2] / 'platforms/windows/scripts/windows-desktop-send.ps1').read_text()
        write = script.index('$snapshot.value.SetValue($payload.message)')
        self.assertNotIn('Invoke-PocketReadiness', script[write:])
        self.assertEqual(script.count('$snapshot.value.SetValue($payload.message)'), 1)
        retry = script[script.index('function Invoke-PocketReadiness'):script.index('function Test-PocketDisplayOffRoute')]
        self.assertIn('$attempt -le 3', retry)
        self.assertNotIn('SetValue', retry.replace('# Read-only callbacks only. Never wrap SetValue or Invoke in this loop.', ''))
        self.assertNotIn('.Invoke()', retry)
        self.assertIn('AddSeconds(24)', script)
        self.assertIn('$scanLogs.Count -lt 8', script)
        self.assertIn("Get-PocketSnapshot 'after_write'", script[write:])

    def test_taskbar_click_is_opt_in_prewrite_and_exact_target_only(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / 'platforms/windows/scripts/windows-desktop-send.ps1').read_text()
        click = script.index('$result.taskbarActivation=Invoke-PocketTaskbarActivation')
        self.assertIn('$readyText -and $AllowTaskbarActivation', script[:click])
        self.assertIn('$AllowTaskbarActivation -and ($ActionPhase -or $AllowDisplayOff)', script)
        self.assertLess(click, script.index('$snapshot.value.SetValue($payload.message)'))
        self.assertIn("$snapshot=Get-PocketSnapshot 'taskbar'", script[click:])
        module = (root / 'platforms/windows/scripts/windows-taskbar-activate.ps1').read_text()
        self.assertIn('$found.Count -ne 1', module)
        self.assertIn('$matched.Count -ne 1', module)
        self.assertIn('Get-AppxPackageManifest', module)
        self.assertIn('AutomationElement]::FromPoint($point)', module)
        self.assertIn('$now.BoundingRectangle -ne $bounds', module)
        self.assertEqual(module.count('::Click('), 1)
        self.assertNotIn('SetValue(', module)
        self.assertNotIn('.Invoke()', module)
        self.assertIn('SetPhysicalCursorPos($original.X,$original.Y)', module)

    def test_missing_or_duplicate_current_name_prevents_ui(self):
        self.client.request.return_value = {"data": [{"id": THREAD, "name": "Current name"}, {"id": OTHER, "name": "Current name"}], "nextCursor": None}
        self.assertEqual(self.controller.deliver(self.request_id, THREAD, "hello")["error"], "task_identity_mismatch")
        self.send.assert_not_called()

    def test_active_task_prevents_ui(self):
        self.data["status"] = {"type": "active"}
        self.assertEqual(self.controller.deliver(self.request_id, THREAD, "hello")["error"], "desktop_turn_active")
        self.send.assert_not_called()

    def test_repeating_cursor_fails_closed(self):
        self.client.request.return_value = {"data": [{"id": THREAD, "name": "Current name"}], "nextCursor": "same"}
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, "hello")["ok"])
        self.send.assert_not_called()

    def test_stale_preview_title_is_not_used(self):
        self.data["preview"] = "old initial prompt"
        self.controller.deliver(self.request_id, THREAD, "hello")
        self.assertEqual(self.send.call_args.args[0]["expectedTitle"], "Current name")

    def test_no_prompt_in_journal(self):
        message = "SECRET_TEST_PROMPT_493"
        self.controller.deliver(self.request_id, THREAD, message)
        self.assertNotIn(message.encode(), (self.root / "native-delivery.sqlite").read_bytes())

    def test_crash_after_claim_is_not_replayed(self):
        self.controller.journal.claim(self.request_id, THREAD, "hello", set())
        self.assertFalse(self.controller.deliver(self.request_id, THREAD, "hello")["ok"])
        self.send.assert_not_called()

    def test_input_validation(self):
        for rid, tid, message in [("bad", THREAD, "hello"), (self.request_id, "../path", "hello"),
                                  (self.request_id, THREAD, " "), (self.request_id, THREAD, "x\0y"),
                                  (self.request_id, THREAD, "x" * 20001)]:
            with self.assertRaises(ValueError):
                validate_delivery(rid, tid, message)


class NativeApiTest(unittest.TestCase):
    request = fixtures.BridgeApiTest.request
    tearDown = fixtures.BridgeApiTest.tearDown

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        self.server.RequestHandlerClass = WindowsNativeHandler
        self.server.runtime = WINDOWS_NATIVE_TEXT
        self.server.native_text_controller = Mock()
        self.server.native_text_controller.deliver.return_value = {"ok": True, "mode": "desktop", "desktop": {"ok": True}}

    def test_authenticated_native_route_does_not_start_background_turn(self):
        payload = {"requestId": str(uuid.uuid4()), "message": "hello"}
        status, result = self.request("POST", f"/api/codex/threads/{THREAD}/turn", payload)
        self.assertEqual(status, 202)
        self.assertEqual(result["mode"], "desktop")
        self.assertEqual(self.app_server.started, [])
        self.server.native_text_controller.deliver.assert_called_once_with(payload["requestId"], THREAD, "hello")

    def test_native_route_requires_auth(self):
        status, _ = self.request("POST", f"/api/codex/threads/{THREAD}/turn", {"message": "hello"}, authorized=False)
        self.assertEqual(status, 401)
        self.server.native_text_controller.deliver.assert_not_called()

    def test_receipt_only_is_explicit_and_authenticated(self):
        payload = {'requestId': str(uuid.uuid4()), 'message': 'hello', 'receiptOnly': True}
        status, _ = self.request('POST', f'/api/codex/threads/{THREAD}/turn', payload)
        self.assertEqual(status, 202)
        self.server.native_text_controller.deliver.assert_called_once_with(payload['requestId'], THREAD, 'hello', receipt_only=True)
        self.server.native_text_controller.deliver.reset_mock()
        status, _ = self.request('POST', f'/api/codex/threads/{THREAD}/turn', payload, authorized=False)
        self.assertEqual(status, 401)
        status, _ = self.request('POST', f'/api/codex/threads/{THREAD}/continue', payload)
        self.assertEqual(status, 400)
        status, _ = self.request('POST', f'/api/codex/threads/{THREAD}/turn', {**payload, 'receiptOnly': 'true'})
        self.assertEqual(status, 400)
        self.server.native_text_controller.deliver.assert_not_called()

    def test_create_continue_and_attachments_are_explicitly_unsupported(self):
        for path, payload in [("/api/codex/threads", {"message": "hello"}),
                              (f"/api/codex/threads/{THREAD}/continue", {}),
                              (f"/api/codex/threads/{THREAD}/turn", {"message": "hello", "attachmentIds": ["id"]})]:
            status, _ = self.request("POST", path, payload)
            self.assertEqual(status, 501)
        self.assertEqual(self.app_server.started, [])
        self.server.native_text_controller.deliver.assert_not_called()
