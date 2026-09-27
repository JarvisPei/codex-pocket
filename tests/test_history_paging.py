import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codex_app_server import AppServerError, AppServerRejected, CodexAppServerClient, summarize_thread_detail
from tests import test_mac_bridge as fixtures
from mac_bridge import desktop_message_landed, thread_user_message_fingerprints


def turn(identifier, text="answer"):
    return {"id": identifier, "status": "completed", "items": [
        {"id": identifier + "-user", "type": "userMessage", "content": [{"type": "text", "text": "question"}]},
        {"id": identifier + "-agent", "type": "agentMessage", "text": text},
    ]}


class HistoryPagingTest(unittest.TestCase):
    def test_reads_newest_first_page_then_reverses_without_resuming(self):
        client = CodexAppServerClient(Path("fake"))
        client.request = Mock(side_effect=[
            {"data": [turn("new"), turn("old")], "nextCursor": "opaque"},
            {"thread": {"id": "thread", "name": "name"}},
        ])
        result = client.read_thread_page("thread", 30)
        self.assertEqual([t["id"] for t in result["thread"]["turns"]], ["old", "new"])
        self.assertEqual(result["thread"]["_historyPage"], {"nextCursor": "opaque", "older": False})
        self.assertEqual([c.args[0] for c in client.request.call_args_list], ["thread/turns/list", "thread/read"])
        self.assertEqual(client.request.call_args_list[0].args[1]["itemsView"], "full")
        self.assertFalse(client.request.call_args_list[1].args[1]["includeTurns"])

    def test_older_cursor_and_bounded_page_size(self):
        client = CodexAppServerClient(Path("fake"))
        client.request = Mock(side_effect=[{"data": [], "nextCursor": None}, {"thread": {"id": "thread"}}])
        result = client.read_thread_page("thread", 999, "opaque")
        self.assertEqual(client.request.call_args_list[0].args[1]["limit"], 60)
        self.assertEqual(client.request.call_args_list[0].args[1]["cursor"], "opaque")
        self.assertTrue(result["thread"]["_historyPage"]["older"])

    def test_old_binary_fallback_only_for_unsupported_method(self):
        client = CodexAppServerClient(Path("fake"))
        client.request = Mock(side_effect=[AppServerRejected("unknown method", -32601), {"thread": {}}, {"thread": {}}])
        client.read_thread_page("thread")
        client.read_thread_page("thread")
        self.assertEqual([c.args[0] for c in client.request.call_args_list], ["thread/turns/list", "thread/read", "thread/read"])
        with self.assertRaises(AppServerError):
            client.read_thread_page("thread", cursor="older")
        self.assertEqual(client.request.call_count, 3)

    def test_timeout_or_invalid_cursor_never_triggers_full_history_fallback(self):
        for error in [AppServerError("timeout"), AppServerRejected("bad cursor", -32602), AppServerRejected("denied", -32000)]:
            with self.subTest(error=error):
                client = CodexAppServerClient(Path("fake"))
                client.request = Mock(side_effect=error)
                with self.assertRaises(AppServerError):
                    client.read_thread_page("thread")
                self.assertEqual(client.request.call_count, 1)
                self.assertIsNone(client._history_paging_supported)

    def test_invalid_page_and_metadata_fail_closed(self):
        for page in [{}, {"data": [None]}, {"data": [{}]}, {"data": [], "nextCursor": []}]:
            client = CodexAppServerClient(Path("fake"))
            client.request = Mock(return_value=page)
            with self.assertRaises(AppServerError):
                client.read_thread_page("thread")
        client.request = Mock(side_effect=[{"data": []}, {"thread": {"id": "different"}}])
        with self.assertRaises(AppServerError):
            client.read_thread_page("thread")

    def test_rpc_rejection_preserves_machine_readable_code(self):
        client = CodexAppServerClient(Path("fake"))
        def reply(message):
            client._pending[message["id"]].put({"error": {"code": -32601, "message": "unknown"}})
        client._write = reply
        with self.assertRaises(AppServerRejected) as caught:
            client.request("thread/turns/list", {})
        self.assertEqual(caught.exception.code, -32601)

    def test_older_page_does_not_inject_live_turn_or_duplicate_activities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-thread.jsonl"
            path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "live"}}) + "\n")
            older = turn("old")
            older["items"].insert(1, {"id": "compact", "type": "contextCompaction"})
            detail = summarize_thread_detail({"id": "thread", "path": str(path), "turns": [older],
                                              "_historyPage": {"older": True, "nextCursor": "next"}})
            self.assertEqual([t["id"] for t in detail["turns"]], ["old"])
            self.assertEqual([i["type"] for i in detail["turns"][0]["items"]], ["userMessage", "contextCompaction", "agentMessage"])
            self.assertTrue(detail["historyTruncated"])
            self.assertIsNone(detail["totalTurns"])


class HistoryPagingApiTest(unittest.TestCase):
    setUp = fixtures.BridgeApiTest.setUp
    tearDown = fixtures.BridgeApiTest.tearDown
    request = fixtures.BridgeApiTest.request

    def page(self, ids, cursor=None, older=False):
        return {"thread": {"id": "thread-1", "turns": [turn(i) for i in ids],
                           "_historyPage": {"nextCursor": cursor, "older": older}}}

    def test_http_pages_are_cached_separately_and_never_resume(self):
        self.app_server.read_thread_page = Mock(side_effect=[self.page(["new"], "older"), self.page(["old"], older=True)])
        base = "/api/codex/threads/thread-1?turns=30"
        for suffix, expected in [("", "new"), ("&historyBefore=older", "old"), ("", "new")]:
            status, body = self.request("GET", base + suffix)
            self.assertEqual(status, 200)
            self.assertEqual(body["thread"]["turns"][0]["id"], expected)
        self.assertEqual(self.app_server.read_thread_page.call_count, 2)
        self.assertEqual(self.app_server.read_count, 0)

    def test_refresh_reads_only_tail_and_does_not_poison_initial_page_cache(self):
        self.app_server.read_thread_page = Mock(return_value=self.page(["new"], "older"))
        base = "/api/codex/threads/thread-1?turns=30"
        _, first = self.request("GET", base)
        cursor = first["thread"]["historyCursor"]
        query = f"&tailTurnId=new&tailRevision={cursor['revision']}"
        status, result = self.request("GET", base + query)
        self.assertEqual(status, 200)
        self.assertEqual(result["thread"]["turns"], [])
        self.app_server.read_thread_page.assert_called_with("thread-1", 1, None)
        _, initial = self.request("GET", base)
        self.assertEqual(len(initial["thread"]["turns"]), 1)
        self.assertNotIn("historyDelta", initial["thread"])

    def test_new_turn_fetches_bounded_window_instead_of_full_history(self):
        self.app_server.read_thread_page = Mock(side_effect=[self.page(["new"]), self.page(["old", "new"])])
        status, result = self.request("GET", "/api/codex/threads/thread-1?turns=30&tailTurnId=old&tailRevision=" + "a" * 64)
        self.assertEqual(status, 200)
        self.assertEqual([c.args[1] for c in self.app_server.read_thread_page.call_args_list], [1, 30])
        self.assertEqual(result["thread"]["historyDelta"]["baseTurnId"], "old")

    def test_page_failure_does_not_claim_empty_history(self):
        self.app_server.read_thread_page = Mock(side_effect=AppServerError("private detail"))
        status, result = self.request("GET", "/api/codex/threads/thread-1")
        self.assertEqual(status, 502)
        self.assertNotIn("thread", result)
        self.assertNotIn("private detail", json.dumps(result))

    def test_oversized_cursor_and_unauthorized_reads_never_reach_backend(self):
        self.app_server.read_thread_page = Mock()
        status, _ = self.request("GET", "/api/codex/threads/thread-1?historyBefore=" + "x" * 4097)
        self.assertEqual(status, 400)
        status, _ = self.request("GET", "/api/codex/threads/thread-1", authorized=False)
        self.assertEqual(status, 401)
        self.app_server.read_thread_page.assert_not_called()

    def test_send_preflight_uses_fresh_tail_not_full_history_or_display_cache(self):
        self.app_server.read_thread_page = Mock(return_value=self.page(["recent"]))
        self.app_server.read_thread = Mock(side_effect=AppServerError("full history timeout"))
        self.server.cache_thread_detail("thread-1", "", 30, {"turns": []})
        status, result = self.request("POST", "/api/codex/threads/thread-1/turn", {"message": "new instruction"})
        self.assertEqual(status, 202)
        self.assertEqual(result["mode"], "desktop")
        self.app_server.read_thread_page.assert_called_once_with("thread-1", 5, None)
        self.app_server.read_thread.assert_not_called()

    def test_failed_send_preflight_never_reaches_desktop(self):
        self.app_server.read_thread_page = Mock(side_effect=AppServerError("timeout"))
        self.controller.send_to_desktop = Mock()
        status, result = self.request("POST", "/api/codex/threads/thread-1/turn", {"message": "new instruction"})
        self.assertEqual(status, 502)
        self.assertEqual(result["error"], "codex_thread_unavailable")
        self.controller.send_to_desktop.assert_not_called()

    def test_continue_and_settings_still_validate_latest_turn(self):
        page = self.page(["recent"])
        page["thread"]["turns"][-1]["status"] = "inProgress"
        self.app_server.read_thread_page = Mock(return_value=page)
        self.controller.send_to_desktop = Mock()
        status, result = self.request("POST", "/api/codex/threads/thread-1/continue", {})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "thread_not_interrupted")
        status, result = self.request("POST", "/api/codex/threads/thread-1/settings", {"model": "test", "effort": "high"})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "model_settings_locked")
        self.controller.send_to_desktop.assert_not_called()

    def test_receipt_uses_tail_and_never_confirms_an_old_identical_message(self):
        original = self.page(["old"])["thread"]
        updated = self.page(["old", "new"])["thread"]
        client = Mock(spec=["read_thread_page", "read_thread"])
        client.read_thread_page.side_effect = [{"thread": original}, {"thread": updated}]
        with patch("mac_bridge.time.sleep"):
            self.assertTrue(desktop_message_landed(client, "thread-1", thread_user_message_fingerprints(original), "question", attempts=2))
        self.assertEqual(client.read_thread_page.call_count, 2)
        client.read_thread.assert_not_called()
        client.read_thread_page.side_effect = None
        client.read_thread_page.return_value = {"thread": original}
        self.assertFalse(desktop_message_landed(client, "thread-1", thread_user_message_fingerprints(original), "question", attempts=1))
