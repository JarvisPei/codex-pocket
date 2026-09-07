import os
import queue
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codex_app_server import (
    AppServerError, AppServerRejected, CodexAppServerClient, ManagedTurnConflict,
    _read_saved_model_settings,
)


BINARY = Path("/private/tmp/fake-codex")


class ReadOnlySettingsTest(unittest.TestCase):
    def test_transport_failure_is_not_misclassified_as_rpc_rejection(self):
        client = CodexAppServerClient(BINARY)
        client._write = lambda message: client._fail_pending("connection lost")
        with self.assertRaises(AppServerError) as error:
            client.request("turn/start", {})
        self.assertNotIsInstance(error.exception, AppServerRejected)

    def test_transport_close_does_not_overwrite_queued_response(self):
        client = CodexAppServerClient(BINARY)
        response = queue.Queue(maxsize=1)
        response.put({"result": {"ok": True}})
        client._pending[1] = response
        client._fail_pending("connection lost")
        self.assertEqual(response.get_nowait(), {"result": {"ok": True}})

    def test_read_settings_never_resumes_or_loads_thread(self):
        client = CodexAppServerClient(BINARY)
        client.request = Mock(side_effect=[
            {"thread": {"id": "t1", "cwd": "/workspace"}},
            {"config": {"model": "default", "model_reasoning_effort": "high",
                        "service_tier": "priority"}},
        ])
        with patch("codex_app_server._read_saved_model_settings", return_value={
            "model": "saved", "effort": "low",
        }):
            result = client.read_thread_settings("t1")
        self.assertEqual(result["model"], "saved")
        self.assertEqual(result["effort"], "low")
        self.assertEqual(result["source"], "saved")
        self.assertEqual([c.args[0] for c in client.request.call_args_list],
                         ["thread/read", "config/read"])
        self.assertFalse(client.request.call_args_list[0].args[1]["includeTurns"])

    def test_missing_metadata_uses_defaults_not_resume(self):
        client = CodexAppServerClient(BINARY)
        client.request = Mock(side_effect=[
            {"thread": {"id": "t1", "cwd": "/workspace"}},
            {"config": {"model": "default", "model_reasoning_effort": "high"}},
        ])
        with patch("codex_app_server._read_saved_model_settings", return_value={}):
            result = client.read_thread_settings("t1")
        self.assertEqual(result["model"], "default")
        self.assertEqual(result["source"], "defaults")

    def test_saved_settings_use_read_only_versioned_database(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state_10.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE threads (id TEXT, model TEXT, reasoning_effort TEXT)")
                db.execute("INSERT INTO threads VALUES (?, ?, ?)", ("t1", "saved", "low"))
            with patch.dict(os.environ, {"CODEX_HOME": root}):
                self.assertEqual(_read_saved_model_settings("t1"),
                                 {"model": "saved", "effort": "low"})
                self.assertEqual(_read_saved_model_settings("' OR 1=1 --"), {})

    def test_missing_and_old_schema_do_not_create_or_modify_database(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"CODEX_HOME": root}):
            self.assertEqual(_read_saved_model_settings("t1"), {})
            self.assertEqual(list(Path(root).iterdir()), [])
            with sqlite3.connect(Path(root) / "state_1.sqlite") as db:
                db.execute("CREATE TABLE threads (id TEXT)")
            self.assertEqual(_read_saved_model_settings("t1"), {})


class WriterOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.reader = CodexAppServerClient(BINARY)
        self.reader.request = Mock(side_effect=AssertionError("reader must stay read-only"))
        self.writers = []

    def new_writer(self, *args, **kwargs):
        writer = CodexAppServerClient(BINARY, dedicated=True)
        writer.start = Mock()
        writer.close = Mock()
        def request(method, params, timeout=15):
            if method == "thread/resume":
                return {"thread": {"id": params["threadId"]}}
            if method == "turn/start":
                return {"turn": {"id": "turn-1", "status": "inProgress"}}
            if method == "turn/interrupt":
                return {}
            raise AssertionError(method)
        writer.request = Mock(side_effect=request)
        self.writers.append(writer)
        return writer

    def writer_factory(self):
        # Keep a real constructor reference: patch only the factory used by
        # methods in codex_app_server, not this module's imported class.
        return patch("codex_app_server.CodexAppServerClient", side_effect=self.new_writer)

    def complete(self, writer, thread_id, status="completed"):
        writer._handle_notification("turn/completed", {
            "threadId": thread_id, "turn": {"id": "turn-1", "status": status},
        })
        if writer._retirement:
            writer._retirement.join(timeout=2)
            self.assertFalse(writer._retirement.is_alive())

    def test_background_runs_are_isolated_and_release_only_finished_writer(self):
        with self.writer_factory():
            self.reader.start_turn("a", "first")
            self.reader.start_turn("b", "second")
        a, b = self.writers
        self.complete(a, "a")
        a.close.assert_called_once()
        b.close.assert_not_called()
        self.assertEqual(self.reader.managed_run("a")["status"], "completed")
        self.assertEqual(self.reader.managed_run("b")["status"], "inProgress")
        self.reader.request.assert_not_called()

    def test_interrupted_and_failed_turns_release_their_writer(self):
        for status in ("interrupted", "failed"):
            with self.writer_factory():
                self.reader.start_turn(status, "test")
            writer = self.writers[-1]
            self.complete(writer, status, status)
            writer.close.assert_called_once()

    def test_active_writer_is_not_replaced_or_closed(self):
        with self.writer_factory():
            self.reader.start_turn("a", "first")
            with self.assertRaises(ManagedTurnConflict):
                self.reader.start_turn("a", "second")
        self.assertEqual(len(self.writers), 1)
        self.writers[0].close.assert_not_called()

    def test_next_turn_uses_fresh_writer_and_reapplies_selected_settings(self):
        self.reader._settings_overrides["a"] = {
            "model": "m", "effort": "low", "serviceTier": "priority",
        }
        with self.writer_factory():
            self.reader.start_turn("a", "first")
            self.complete(self.writers[0], "a")
            self.reader.start_turn("a", "second", resume=False)
        self.assertEqual(len(self.writers), 2)
        params = self.writers[1].request.call_args.args[1]
        self.assertEqual(params["serviceTier"], "priority")
        self.assertEqual(params["effort"], "low")
        self.assertEqual(self.writers[1].request.call_args_list[0].args[0], "thread/resume")
        self.writers[1].close.assert_not_called()

    def test_stop_and_approval_are_sent_to_owning_writer(self):
        with self.writer_factory():
            self.reader.start_turn("a", "first")
        writer = self.writers[0]
        writer._write = Mock()
        writer._handle_server_request({"id": 9, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "a", "turnId": "turn-1", "command": "test"}})
        key = self.reader.managed_run("a")["pendingRequest"]["id"]
        writer.close.assert_not_called()
        self.reader.respond_to_request("a", key, {"decision": "decline"})
        writer._write.assert_called_once_with({"id": 9, "result": {"decision": "decline"}})
        self.reader.interrupt_turn("a")
        self.assertEqual(writer.request.call_args.args[0], "turn/interrupt")
        writer.close.assert_not_called()  # Wait for turn/completed, not interrupt ack.

    def test_completion_before_start_response_does_not_close_transport_early(self):
        writer = self.new_writer()
        writer._retire_when_idle = True
        request = writer.request.side_effect
        def early_completion(method, params, timeout=15):
            if method == "turn/start":
                writer._handle_notification("turn/completed", {
                    "threadId": "a", "turn": {"id": "turn-1", "status": "completed"},
                })
                writer.close.assert_not_called()
            return request(method, params, timeout)
        writer.request.side_effect = early_completion
        result = writer.start_turn("a", "test")
        writer._retirement.join(timeout=2)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(writer.close.called)

    def test_lost_start_response_keeps_writer_for_late_events(self):
        writer = self.new_writer()
        writer.request.side_effect = [
            {"thread": {"id": "a"}}, AppServerError("transport timed out"),
        ]
        with patch("codex_app_server.CodexAppServerClient", return_value=writer):
            with self.assertRaises(AppServerError):
                self.reader.start_turn("a", "test")
        writer.close.assert_not_called()
        self.assertEqual(self.reader.managed_run("a")["status"], "starting")
        self.complete(writer, "a")
        writer.close.assert_called_once()

    def test_definite_start_rejection_releases_writer(self):
        writer = self.new_writer()
        writer.request.side_effect = [
            {"thread": {"id": "a"}}, AppServerRejected("invalid request"),
        ]
        with patch("codex_app_server.CodexAppServerClient", return_value=writer):
            with self.assertRaises(AppServerRejected):
                self.reader.start_turn("a", "test")
        writer.close.assert_called_once()

    def test_settings_writer_always_closes_even_if_update_fails(self):
        for failure in (False, True):
            writer = self.new_writer()
            writer.update_thread_settings = Mock(
                side_effect=AppServerRejected("locked") if failure else None,
                return_value={"model": "m", "effort": "high", "serviceTier": None},
            )
            with patch("codex_app_server.CodexAppServerClient", return_value=writer):
                if failure:
                    with self.assertRaises(AppServerRejected):
                        self.reader.update_thread_settings("a", model="m", effort="high", service_tier=None)
                else:
                    self.reader.update_thread_settings("a", model="m", effort="high", service_tier=None)
            writer.close.assert_called_once()
        self.reader.request.assert_not_called()

    def test_create_materializes_before_releasing_writer(self):
        writer = self.new_writer()
        writer.request.side_effect = [
            {"thread": {"id": "new"}, "model": "m", "reasoningEffort": "high"},
            {}, {"thread": {"id": "new"}},
        ]
        with patch("codex_app_server.CodexAppServerClient", return_value=writer):
            result = self.reader.create_thread(title="new task")
        self.assertEqual(result["thread"]["id"], "new")
        self.assertEqual([c.args[0] for c in writer.request.call_args_list],
                         ["thread/start", "thread/name/set", "thread/resume"])
        writer.close.assert_called_once()


@unittest.skipUnless(os.environ.get("CODEX_POCKET_TEST_BINARY"), "opt-in local Codex integration")
class RealWriterLifecycleTest(unittest.TestCase):
    def test_read_settings_and_create_leave_no_loaded_threads(self):
        # No account credentials, real tasks, or model inference in this fixture.
        binary = Path(os.environ["CODEX_POCKET_TEST_BINARY"])
        with tempfile.TemporaryDirectory(prefix="pocket-lifecycle-") as root:
            spawn = subprocess.Popen
            def isolated(*args, **kwargs):
                kwargs["env"] = {**os.environ, "CODEX_HOME": root}
                return spawn(*args, **kwargs)
            with patch("codex_app_server.subprocess.Popen", side_effect=isolated), \
                    patch.dict(os.environ, {"CODEX_HOME": root}):
                reader = CodexAppServerClient(binary)
                other = CodexAppServerClient(binary, dedicated=True)
                try:
                    reader.start()
                    created = reader.create_thread(title="isolated fixture", cwd=root)
                    tid = created["thread"]["id"]
                    self.assertEqual(reader.request("thread/loaded/list", {})["data"], [])
                    reader.read_thread_settings(tid)
                    self.assertEqual(reader.request("thread/loaded/list", {})["data"], [])
                    models = reader.list_models()["data"]
                    model = next(m for m in models if m["model"] == created["settings"]["model"])
                    effort = model["supportedReasoningEfforts"][0]["reasoningEffort"]
                    reader.update_thread_settings(
                        tid, model=model["model"], effort=effort, service_tier=None,
                    )
                    saved = reader.read_thread_settings(tid)
                    self.assertEqual((saved["model"], saved["effort"]), (model["model"], effort))
                    # A second process can immediately resume: the writer lock
                    # was actually released, not just hidden from the UI.
                    other.start()
                    result = other.request("thread/resume", {"threadId": tid})
                    self.assertEqual(result["thread"]["id"], tid)
                    self.assertEqual(result["reasoningEffort"], effort)
                    # Viewing still works while another app holds the writer.
                    reader.read_thread_settings(tid)
                    with self.assertRaises(AppServerRejected):
                        reader.update_thread_settings(
                            tid, model=model["model"], effort=effort, service_tier=None,
                        )
                    self.assertIn(tid, other.request("thread/loaded/list", {})["data"])
                finally:
                    other.close()
                    reader.close()


if __name__ == "__main__":
    unittest.main()
