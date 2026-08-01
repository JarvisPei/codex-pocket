import gzip
import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_app_server import CodexAppServerClient, summarize_thread, summarize_thread_detail
from mac_bridge import (
    AttachmentStore,
    BridgeServer,
    DesktopDispatchError,
    DesktopRequestError,
    DesktopController,
    DeviceRegistry,
    StopCandidateError,
    TaskChangedError,
    desktop_message_landed,
    load_token,
    thread_user_message_fingerprints,
)


TOKEN = "t" * 32


class ThreadProjectInferenceTest(unittest.TestCase):
    def test_infers_desktop_project_from_thread_working_directory(self):
        summary = summarize_thread(
            {
                "id": "desktop-created-thread",
                "name": "Desktop 新任务",
                "cwd": "/workspace/mobile/project/subfolder",
            },
            {
                "projects": {
                    "parent": {
                        "id": "parent",
                        "name": "Workspace",
                        "path": "/workspace",
                        "order": 0,
                    },
                    "mobile": {
                        "id": "mobile",
                        "name": "Mobile Project",
                        "path": "/workspace/mobile/project",
                        "order": 1,
                    },
                },
                "assignments": {},
            },
        )

        self.assertEqual(summary["collection"], "project")
        self.assertEqual(summary["project"]["id"], "mobile")
        self.assertEqual(summary["project"]["name"], "Mobile Project")


class DesktopMessageConfirmationTest(unittest.TestCase):
    @patch("mac_bridge.time.sleep")
    def test_confirms_new_user_message_after_ui_switch(self, sleep):
        original = {
            "turns": [
                {
                    "items": [
                        {
                            "id": "old-message",
                            "type": "userMessage",
                            "content": [{"type": "text", "text": "旧消息"}],
                        }
                    ]
                }
            ]
        }
        updated = {
            "turns": [
                *original["turns"],
                {
                    "items": [
                        {
                            "id": "new-message",
                            "type": "userMessage",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "## My request for Codex:\n描述一下这张图片",
                                }
                            ],
                        }
                    ]
                },
            ]
        }

        class AppServer:
            def __init__(self):
                self.calls = 0

            def read_thread(self, thread_id):
                self.calls += 1
                return {"thread": original if self.calls == 1 else updated}

        app_server = AppServer()
        self.assertTrue(
            desktop_message_landed(
                app_server,
                "thread-1",
                thread_user_message_fingerprints(original),
                "描述一下这张图片",
                attempts=3,
            )
        )
        sleep.assert_called_once_with(0.1)


class CodexAppServerManagedTurnTest(unittest.TestCase):
    def setUp(self):
        self.client = CodexAppServerClient(Path("/private/tmp/fake-codex"))
        self.requests = []

        def fake_request(method, params, timeout=15):
            self.requests.append((method, params))
            if method == "thread/resume":
                return {"thread": {"id": params["threadId"], "turns": []}}
            if method == "turn/start":
                return {
                    "turn": {
                        "id": "turn-managed-1",
                        "status": "inProgress",
                        "items": [],
                    }
                }
            if method == "turn/interrupt":
                return {}
            raise AssertionError(f"unexpected request: {method}")

        self.client.request = fake_request

    def test_tracks_streaming_delta_and_completion(self):
        run = self.client.start_turn("thread-1", "继续开发")
        self.assertEqual(run["status"], "inProgress")
        self.client._handle_notification(
            "item/agentMessage/delta",
            {
                "threadId": "thread-1",
                "turnId": "turn-managed-1",
                "itemId": "agent-1",
                "delta": "正在处理",
            },
        )
        self.assertEqual(
            self.client.managed_run("thread-1")["agentText"],
            "正在处理",
        )
        self.client._handle_notification(
            "turn/completed",
            {
                "threadId": "thread-1",
                "turn": {"id": "turn-managed-1", "status": "completed"},
            },
        )
        self.assertEqual(
            self.client.managed_run("thread-1")["status"],
            "completed",
        )

    def test_continues_interrupted_thread_without_user_input(self):
        run = self.client.continue_turn("thread-1")
        self.assertEqual(run["status"], "inProgress")
        self.assertEqual(run["userText"], "")
        self.assertEqual(
            self.requests,
            [
                ("thread/resume", {"threadId": "thread-1"}),
                ("turn/start", {"threadId": "thread-1", "input": []}),
            ],
        )

    def test_routes_approval_response_to_server_request(self):
        self.client.start_turn("thread-1", "继续开发")
        writes = []
        self.client._write = writes.append
        self.client._handle_server_request(
            {
                "id": 91,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-managed-1",
                    "itemId": "command-1",
                    "command": "git status",
                    "cwd": "/workspace/project",
                    "startedAtMs": 1,
                },
            }
        )
        run = self.client.managed_run("thread-1")
        request_key = run["pendingRequest"]["id"]
        self.assertEqual(run["status"], "waitingForInput")
        self.assertEqual(run["pendingRequest"]["command"], "git status")
        self.client.respond_to_request(
            "thread-1",
            request_key,
            {"decision": "accept"},
        )
        self.assertEqual(writes, [{"id": 91, "result": {"decision": "accept"}}])
        self.assertIsNone(self.client.managed_run("thread-1")["pendingRequest"])

    def test_creates_named_idle_thread_before_desktop_dispatch(self):
        requests = []

        def fake_request(method, params, timeout=15):
            requests.append((method, params))
            if method == "model/list":
                return {
                    "data": [
                        {
                            "model": "gpt-5.6-sol",
                            "supportedReasoningEfforts": [
                                {"reasoningEffort": "high"}
                            ],
                            "serviceTiers": [{"id": "priority"}],
                        }
                    ]
                }
            if method == "thread/start":
                return {
                    "thread": {"id": "thread-created", "turns": []},
                    "model": "gpt-5.6-sol",
                    "reasoningEffort": "high",
                    "serviceTier": "priority",
                }
            if method == "thread/name/set":
                return {}
            if method == "thread/resume":
                return {"thread": {"id": "thread-created", "turns": []}}
            if method == "thread/settings/update":
                return {}
            raise AssertionError(f"unexpected request: {method}")

        self.client.request = fake_request
        result = self.client.create_thread(
            title="实现导出功能",
            cwd="/workspace/project",
            model="gpt-5.6-sol",
            effort="high",
            service_tier="priority",
        )
        self.assertEqual(result["thread"]["id"], "thread-created")
        self.assertEqual(result["thread"]["name"], "实现导出功能")
        self.assertIn(
            (
                "thread/start",
                {
                    "ephemeral": False,
                    "cwd": "/workspace/project",
                    "model": "gpt-5.6-sol",
                    "serviceTier": "priority",
                },
            ),
            requests,
        )
        self.assertIn(
            (
                "thread/name/set",
                {"threadId": "thread-created", "name": "实现导出功能"},
            ),
            requests,
        )


class FakeController:
    def __init__(self, count=1, task_title="继续项目开发"):
        self.count = count
        self.task_title = task_title
        self.interrupted = False
        self.interrupted_thread = None
        self.fail_status = False
        self.desktop_sent = []
        self.desktop_attachments = []
        self.dispatch_error = None
        self.desktop_request = None
        self.desktop_request_responses = []
        self.desktop_request_error = None

    def stop_candidate_count(self):
        return self.count

    def status(self):
        if self.fail_status:
            raise RuntimeError("background Accessibility probe unavailable")
        return {
            "taskTitle": self.task_title,
            "stopCandidates": self.count,
            "request": self.desktop_request,
        }

    def managed_takeover_status(self, expected_task_title):
        return {
            "sameThread": expected_task_title == self.task_title,
            "taskTitle": self.task_title,
            "stopCandidates": (
                self.count if expected_task_title == self.task_title else None
            ),
        }

    def foreground_status(self, expected_task_title):
        if expected_task_title != self.task_title:
            raise TaskChangedError(self.task_title)
        return {"taskTitle": self.task_title, "stopCandidates": self.count}

    def interrupt(self, expected_task_title):
        if expected_task_title != self.task_title:
            raise TaskChangedError(self.task_title)
        if self.count != 1:
            raise StopCandidateError(self.count)
        self.interrupted = True

    def interrupt_thread(self, thread_id, expected_task_title):
        if expected_task_title != self.task_title:
            raise TaskChangedError(self.task_title)
        if self.count != 1:
            raise StopCandidateError(self.count)
        self.interrupted = True
        self.interrupted_thread = thread_id

    def send_to_desktop(
        self,
        thread_id,
        expected_task_title,
        message,
        *,
        continue_only=False,
        attachment_paths=None,
    ):
        if self.dispatch_error:
            raise DesktopDispatchError(self.dispatch_error, "dispatch refused")
        self.desktop_sent.append(
            (thread_id, expected_task_title, message, continue_only)
        )
        self.desktop_attachments.append(list(attachment_paths or []))
        self.task_title = expected_task_title
        self.count = 1
        return {
            "ok": True,
            "taskTitle": expected_task_title,
            "stopCandidates": 1,
            "mode": "continue" if continue_only else "message",
        }

    def respond_to_desktop_request(
        self,
        expected_task_title,
        fingerprint,
        action,
        *,
        answer=None,
        option_label=None,
    ):
        if self.desktop_request_error:
            raise DesktopRequestError(
                self.desktop_request_error,
                "desktop request changed",
            )
        if expected_task_title != self.task_title:
            raise DesktopRequestError("foreground_task_changed", "task changed")
        self.desktop_request_responses.append(
            (expected_task_title, fingerprint, action, answer, option_label)
        )
        self.desktop_request = None
        return {"ok": True, "action": action}


class FakeAppServer:
    def __init__(self):
        self.closed = False
        self.started = []
        self.continued = []
        self.runs = {}
        self.read_count = 0
        self.last_turn_status = "completed"
        self.settings = {
            "model": "gpt-5.6-sol",
            "effort": "high",
            "serviceTier": "priority",
        }
        self.updated_settings = []
        self.created_threads = []

    def list_threads(self, limit):
        return {
            "data": [
                {
                    "id": "thread-1",
                    "name": "修复移动页面",
                    "preview": "检查移动页面",
                    "cwd": "/workspace/project",
                    "updatedAt": 123,
                    "status": {"type": "notLoaded"},
                },
                {
                    "id": "thread-2",
                    "name": "临时聊天",
                    "preview": "不属于正式项目",
                    "cwd": "/workspace/scratch",
                    "updatedAt": 122,
                    "status": {"type": "notLoaded"},
                }
            ][:limit],
            "nextCursor": None,
        }

    def read_thread(self, thread_id):
        self.read_count += 1
        if thread_id != "thread-1":
            raise RuntimeError("missing")
        return {
            "thread": {
                "id": "thread-1",
                "name": "修复移动页面",
                "status": {"type": "notLoaded"},
                "turns": [
                    {
                        "id": "turn-1",
                        "status": self.last_turn_status,
                        "items": [
                            {
                                "id": "user-1",
                                "type": "userMessage",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "# Files mentioned by the user:\n\n"
                                            "## screenshot.png\n\n"
                                            "## My request for Codex:\n"
                                            "继续开发"
                                        ),
                                    },
                                    {
                                        "type": "localImage",
                                        "path": str(
                                            Path(tempfile.gettempdir())
                                            / "codex-clipboard-test-image.png"
                                        ),
                                    },
                                ],
                            },
                            {
                                "id": "reasoning-1",
                                "type": "reasoning",
                                "content": ["private"],
                            },
                            {
                                "id": "agent-1",
                                "type": "agentMessage",
                                "text": "已经完成。",
                            },
                            {
                                "id": "files-1",
                                "type": "fileChange",
                                "status": "completed",
                                "changes": [
                                    {
                                        "path": "/workspace/project/new.py",
                                        "kind": {"type": "add"},
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        }

    def list_models(self):
        return {
            "data": [
                {
                    "model": "gpt-5.6-sol",
                    "displayName": "GPT-5.6-Sol",
                    "description": "Frontier coding model",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fast"},
                        {"reasoningEffort": "high", "description": "Deep"},
                    ],
                    "defaultReasoningEffort": "low",
                    "serviceTiers": [
                        {
                            "id": "priority",
                            "name": "Fast",
                            "description": "Priority processing",
                        }
                    ],
                    "defaultServiceTier": None,
                    "isDefault": True,
                }
            ]
        }

    def read_thread_settings(self, thread_id):
        if thread_id != "thread-1":
            raise RuntimeError("missing")
        return dict(self.settings)

    def update_thread_settings(
        self,
        thread_id,
        *,
        model,
        effort,
        service_tier,
    ):
        self.settings = {
            "model": model,
            "effort": effort,
            "serviceTier": service_tier,
        }
        self.updated_settings.append((thread_id, dict(self.settings)))
        return dict(self.settings)

    def create_thread(
        self,
        *,
        title,
        cwd=None,
        model=None,
        effort=None,
        service_tier=None,
    ):
        thread_id = f"thread-new-{len(self.created_threads) + 1}"
        created = {
            "id": thread_id,
            "name": title,
            "preview": "",
            "cwd": cwd,
            "updatedAt": 124,
            "status": {"type": "idle"},
        }
        self.created_threads.append(
            {
                "thread": created,
                "model": model,
                "effort": effort,
                "serviceTier": service_tier,
            }
        )
        return {
            "thread": created,
            "settings": {
                "model": model or self.settings["model"],
                "effort": effort or self.settings["effort"],
                "serviceTier": service_tier,
            },
        }

    def read_rate_limits(self):
        return {
            "rateLimits": {
                "limitId": "codex",
                "limitName": None,
                "primary": {
                    "usedPercent": 65,
                    "windowDurationMins": 10080,
                    "resetsAt": 1785902986,
                },
                "secondary": None,
                "planType": "pro",
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "limitName": None,
                    "primary": {
                        "usedPercent": 65,
                        "windowDurationMins": 10080,
                        "resetsAt": 1785902986,
                    },
                },
                "codex_bengalfox": {
                    "limitId": "codex_bengalfox",
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {
                        "usedPercent": 0,
                        "windowDurationMins": 10080,
                        "resetsAt": 1786108716,
                    },
                },
            },
        }

    def start_turn(self, thread_id, message):
        run = {
            "threadId": thread_id,
            "turnId": "turn-managed-1",
            "status": "inProgress",
            "userText": message,
            "agentText": "",
            "items": [],
            "pendingRequest": None,
            "revision": 1,
        }
        self.started.append((thread_id, message))
        self.runs[thread_id] = run
        return run

    def continue_turn(self, thread_id):
        run = {
            "threadId": thread_id,
            "turnId": "turn-managed-continue",
            "status": "inProgress",
            "userText": "",
            "agentText": "",
            "items": [],
            "pendingRequest": None,
            "revision": 1,
        }
        self.continued.append(thread_id)
        self.runs[thread_id] = run
        return run

    def managed_run(self, thread_id):
        return self.runs.get(thread_id)

    def interrupt_turn(self, thread_id):
        run = self.runs[thread_id]
        run["status"] = "interrupting"
        return run

    def respond_to_request(self, thread_id, request_key, payload):
        run = self.runs[thread_id]
        run["pendingRequest"] = None
        run["status"] = "inProgress"
        run["response"] = {"requestKey": request_key, **payload}
        return run

    def close(self):
        self.closed = True


class StoredActivitySummaryTest(unittest.TestCase):
    def test_active_existing_turn_keeps_working_after_user_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-test-thread-live-order.jsonl"
            path.write_text(
                f"{json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'live-order', 'started_at': 123}})}\n",
                encoding="utf-8",
            )
            thread = {
                "id": "live-order",
                "path": str(path),
                "turns": [
                    {
                        "id": "live-order",
                        "status": "inProgress",
                        "startedAt": 123,
                        "items": [
                            {
                                "id": "user",
                                "type": "userMessage",
                                "content": [{"type": "text", "text": "继续"}],
                            }
                        ],
                    }
                ],
            }

            detail = summarize_thread_detail(thread)
            live_turn = detail["turns"][0]
            self.assertEqual(live_turn["status"], "inProgress")
            self.assertEqual(
                [item["type"] for item in live_turn["items"]],
                ["userMessage", "desktopActivity"],
            )
            self.assertEqual(
                live_turn["items"][1]["activityKind"],
                "working",
            )
            self.assertEqual(live_turn["items"][0]["timestamp"], 123)

    def test_includes_desktop_activity_categories_without_command_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout-test-thread-activity.jsonl"
            records = [
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "activity"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg -n TODO web/app.js"}),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "python3 -m unittest"}),
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "activity"},
                },
            ]
            path.write_text(
                "".join(f"{json.dumps(record)}\n" for record in records),
                encoding="utf-8",
            )
            thread = {
                "id": "activity",
                "path": str(path),
                "turns": [
                    {
                        "id": "activity",
                        "status": "completed",
                        "startedAt": 100,
                        "completedAt": 200,
                        "items": [
                            {"id": "compact", "type": "contextCompaction"},
                            {
                                "id": "browser",
                                "type": "mcpToolCall",
                                "tool": "js",
                                "status": "completed",
                                "arguments": {"title": "检查页面"},
                                "result": {"_meta": {"codex/browserUse": True}},
                            },
                            {"id": "answer", "type": "agentMessage", "text": "完成"},
                        ],
                    }
                ],
            }

            detail = summarize_thread_detail(thread)
            items = detail["turns"][0]["items"]
            activities = {
                item.get("activityKind"): item.get("count")
                for item in items
                if item["type"] == "desktopActivity"
            }
            self.assertEqual(activities, {"readFiles": 1, "ranCommands": 1})
            self.assertIn("contextCompaction", [item["type"] for item in items])
            browser = next(item for item in items if item.get("id") == "browser")
            self.assertEqual(browser["activityKind"], "browser")
            self.assertEqual(browser["label"], "检查页面")
            answer = next(item for item in items if item.get("id") == "answer")
            self.assertEqual(answer["phase"], "final_answer")
            self.assertEqual(answer["timestamp"], 200)
            self.assertNotIn("rg -n TODO", json.dumps(detail))

            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'live', 'started_at': 123}})}\n"
                )
                handle.write(
                    f"{json.dumps({'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command', 'arguments': json.dumps({'cmd': 'python3 build.py'})}})}\n"
                )
            live_detail = summarize_thread_detail(thread)
            live_turn = live_detail["turns"][-1]
            self.assertEqual(live_detail["activityStatus"], "inProgress")
            self.assertEqual(live_turn["id"], "live")
            self.assertEqual(live_turn["status"], "inProgress")
            self.assertEqual(live_turn["items"][0]["activityKind"], "ranCommands")

            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{json.dumps({'type': 'event_msg', 'payload': {'type': 'turn_aborted', 'turn_id': 'live'}})}\n"
                )
                handle.write(
                    f"{json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'activity', 'started_at': 124}})}\n"
                )
            resumed_detail = summarize_thread_detail(thread)
            self.assertEqual(resumed_detail["turns"][0]["status"], "inProgress")
            self.assertIsNone(resumed_detail["turns"][0]["completedAt"])


class BridgeApiTest(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.app_server = FakeAppServer()
        self.state_directory = tempfile.TemporaryDirectory()
        self.codex_state_path = Path(self.state_directory.name) / "state.json"
        self.codex_state_path.write_text(
            json.dumps(
                {
                    "local-projects": {
                        "project-id": {
                            "id": "project-id",
                            "name": "正式项目",
                            "rootPaths": ["/workspace/project"],
                        }
                    },
                    "project-order": ["project-id"],
                    "thread-project-assignments": {
                        "thread-1": {
                            "projectKind": "local",
                            "projectId": "project-id",
                            "cwd": "/workspace/project",
                        }
                    },
                    "projectless-thread-ids": ["thread-2"],
                }
            ),
            encoding="utf-8",
        )
        self.server = BridgeServer(
            ("127.0.0.1", 0),
            TOKEN,
            self.controller,
            app_server=self.app_server,
            codex_state_path=self.codex_state_path,
            attachment_store=AttachmentStore(
                Path(self.state_directory.name) / "uploads"
            ),
            projectless_root=Path(self.state_directory.name) / "Codex",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=2
        )

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.state_directory.cleanup()

    def request(self, method, path, body=None, authorized=True, bearer=None):
        headers = {}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        elif authorized:
            headers["Authorization"] = f"Bearer {TOKEN}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body)
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        payload = json.loads(response.read())
        return response.status, payload

    def raw_request(self, method, path, body, headers=None, bearer=None):
        request_headers = dict(headers or {})
        if bearer is not None:
            request_headers["Authorization"] = f"Bearer {bearer}"
        self.connection.request(method, path, body=body, headers=request_headers)
        response = self.connection.getresponse()
        payload = json.loads(response.read())
        return response.status, payload

    def enroll_device(self, name="Android test"):
        status, payload = self.request(
            "POST",
            "/api/devices/enroll",
            {"name": name},
        )
        self.assertEqual(status, 201)
        return payload["device"]

    def upload_attachment(self, device_token, name="notes.txt", body=b"hello"):
        return self.raw_request(
            "POST",
            "/api/attachments",
            body,
            headers={
                "Content-Type": "text/plain",
                "X-Codex-Filename": name,
            },
            bearer=device_token,
        )

    def test_health_is_local_and_unauthenticated(self):
        status, payload = self.request("GET", "/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_mobile_page_is_served_with_security_policy(self):
        self.connection.request("GET", "/")
        response = self.connection.getresponse()
        body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("Codex Pocket", body)
        self.assertIn("refreshConversationButton", body)
        self.assertIn("newContentButton", body)
        self.assertIn("latestButtonLabel", body)
        self.assertIn("modelSettingsButton", body)
        self.assertIn("attachmentButton", body)
        self.assertIn("attachmentInput", body)
        self.assertIn("newTaskAttachmentButton", body)
        self.assertIn("newTaskAttachmentInput", body)
        self.assertIn("newTaskDialog", body)
        self.assertNotIn("新建任务（即将开放）", body)
        self.assertIn("Usage remaining", body)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))

    def test_large_assets_are_gzip_compressed_when_supported(self):
        self.connection.request(
            "GET",
            "/app.js",
            headers={"Accept-Encoding": "gzip"},
        )
        response = self.connection.getresponse()
        body = response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertIn(b"openThread", gzip.decompress(body))

    def test_attachment_upload_requires_a_paired_device(self):
        status, payload = self.raw_request(
            "POST",
            "/api/attachments",
            b"private",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "text/plain",
                "X-Codex-Filename": "notes.txt",
            },
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "device_required")

    def test_uploads_and_dispatches_device_owned_attachment(self):
        device = self.enroll_device()
        status, payload = self.upload_attachment(device["deviceToken"])
        self.assertEqual(status, 201)
        attachment = payload["attachment"]
        self.assertEqual(attachment["name"], "notes.txt")
        self.assertEqual(attachment["size"], 5)
        self.assertNotIn("path", attachment)

        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/turn",
            {"message": "", "attachmentIds": [attachment["id"]]},
            bearer=device["deviceToken"],
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            self.controller.desktop_sent,
            [("thread-1", "修复移动页面", "", False)],
        )
        attachment_path = Path(self.controller.desktop_attachments[0][0])
        self.assertEqual(attachment_path.name, "notes.txt")
        self.assertEqual(attachment_path.read_bytes(), b"hello")
        self.assertTrue(
            str(attachment_path.resolve()).startswith(
                f"{(Path(self.state_directory.name) / 'uploads').resolve()}{os.sep}"
            )
        )

    def test_attachment_ownership_is_enforced(self):
        first_device = self.enroll_device("First Android")
        second_device = self.server.device_registry.enroll(
            "Second Android",
            "test-agent",
        )
        status, payload = self.upload_attachment(first_device["deviceToken"])
        self.assertEqual(status, 201)
        attachment_id = payload["attachment"]["id"]

        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/turn",
            {"message": "steal", "attachmentIds": [attachment_id]},
            bearer=second_device["deviceToken"],
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "attachment_not_owned")
        self.assertEqual(self.controller.desktop_sent, [])

    def test_rejects_attachment_path_names_and_allows_owner_delete(self):
        device = self.enroll_device()
        status, payload = self.upload_attachment(
            device["deviceToken"],
            name="..%2Fsecret.txt",
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_attachment_name")

        status, payload = self.upload_attachment(device["deviceToken"])
        attachment_id = payload["attachment"]["id"]
        status, payload = self.request(
            "DELETE",
            f"/api/attachments/{attachment_id}",
            bearer=device["deviceToken"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["deleted"], attachment_id)

    def test_status_requires_bearer_token(self):
        status, payload = self.request(
            "GET", "/api/desktop/interrupt/status", authorized=False
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_lists_codex_threads_through_allowlisted_api(self):
        status, payload = self.request("GET", "/api/codex/threads?limit=20")
        self.assertEqual(status, 200)
        self.assertEqual(payload["threads"][0]["id"], "thread-1")
        self.assertEqual(payload["threads"][0]["title"], "修复移动页面")
        self.assertEqual(payload["threads"][0]["collection"], "project")
        self.assertEqual(payload["threads"][0]["project"]["id"], "project-id")
        self.assertEqual(payload["threads"][0]["project"]["name"], "正式项目")
        self.assertEqual(
            payload["threads"][0]["project"]["path"],
            "/workspace/project",
        )
        self.assertEqual(payload["threads"][1]["collection"], "recent")
        self.assertIsNone(payload["threads"][1]["project"])
        self.assertNotIn("turns", payload["threads"][0])
        self.assertEqual(
            payload["projects"],
            [
                {
                    "id": "project-id",
                    "name": "正式项目",
                    "path": "/workspace/project",
                    "order": 0,
                }
            ],
        )

    def test_thread_list_preserves_last_complete_project_index(self):
        status, payload = self.request("GET", "/api/codex/threads?limit=20")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in payload["projects"]], ["project-id"])

        self.codex_state_path.write_text(
            json.dumps(
                {
                    "local-projects": {},
                    "project-order": [],
                    "thread-project-assignments": {},
                    "projectless-thread-ids": ["thread-1", "thread-2"],
                }
            ),
            encoding="utf-8",
        )
        status, payload = self.request("GET", "/api/codex/threads?limit=20")

        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in payload["projects"]], ["project-id"])
        self.assertEqual(payload["threads"][0]["collection"], "project")

    def test_creates_project_task_and_dispatches_it_through_desktop(self):
        self.controller.count = 0
        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {
                "projectId": "project-id",
                "message": "实现新的导出功能\n并补测试",
                "model": "gpt-5.6-sol",
                "effort": "high",
                "serviceTier": "priority",
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["thread"]["collection"], "project")
        self.assertEqual(payload["thread"]["project"]["id"], "project-id")
        self.assertEqual(
            self.app_server.created_threads[0]["thread"]["cwd"],
            "/workspace/project",
        )
        self.assertEqual(
            self.controller.desktop_sent,
            [
                (
                    "thread-new-1",
                    "实现新的导出功能",
                    "实现新的导出功能\n并补测试",
                    False,
                )
            ],
        )
        state = json.loads(self.codex_state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            state["thread-project-assignments"]["thread-new-1"]["projectId"],
            "project-id",
        )
        self.assertNotIn("thread-new-1", state["projectless-thread-ids"])

    def test_creates_recent_task_without_project_assignment(self):
        self.controller.count = 0
        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {"projectId": None, "message": "解释这段报错"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["thread"]["collection"], "recent")
        self.assertIsNone(payload["thread"]["project"])
        standalone_cwd = Path(
            self.app_server.created_threads[0]["thread"]["cwd"]
        )
        self.assertTrue(standalone_cwd.is_dir())
        self.assertEqual(standalone_cwd.name, "new-chat")
        self.assertEqual(standalone_cwd.parent.parent.name, "Codex")
        state = json.loads(self.codex_state_path.read_text(encoding="utf-8"))
        self.assertIn("thread-new-1", state["projectless-thread-ids"])
        self.assertNotIn(
            "thread-new-1",
            state["thread-project-assignments"],
        )

    def test_creates_attachment_only_task_and_dispatches_file(self):
        device = self.enroll_device()
        status, upload = self.upload_attachment(
            device["deviceToken"],
            name="requirements.pdf",
            body=b"%PDF-test",
        )
        self.assertEqual(status, 201)
        attachment = upload["attachment"]

        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {
                "projectId": "project-id",
                "message": "",
                "attachmentIds": [attachment["id"]],
            },
            bearer=device["deviceToken"],
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["thread"]["title"], "分析 requirements.pdf")
        self.assertEqual(
            self.controller.desktop_sent,
            [("thread-new-1", "分析 requirements.pdf", "", False)],
        )
        self.assertEqual(
            Path(self.controller.desktop_attachments[0][0]).name,
            "requirements.pdf",
        )

    def test_creates_new_task_while_another_desktop_task_is_running(self):
        self.controller.count = 1
        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {"projectId": "project-id", "message": "并行创建新任务"},
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(self.app_server.created_threads), 1)
        self.assertEqual(
            self.controller.desktop_sent,
            [("thread-new-1", "并行创建新任务", "并行创建新任务", False)],
        )

    def test_new_task_does_not_depend_on_background_status_probe(self):
        self.controller.fail_status = True
        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {"projectId": None, "message": "状态探测失败时仍创建"},
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])

    def test_new_task_reports_created_thread_when_accessibility_is_unavailable(self):
        self.controller.dispatch_error = "desktop_accessibility_unavailable"
        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {"projectId": None, "message": "保留这条指令"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"], "desktop_accessibility_unavailable")
        self.assertTrue(payload["threadCreated"])
        self.assertEqual(payload["thread"]["id"], "thread-new-1")
        self.assertEqual(payload["thread"]["collection"], "recent")

    def test_rejects_unknown_project_for_new_task(self):
        self.controller.count = 0
        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {"projectId": "missing", "message": "测试"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_project")
        self.assertEqual(self.app_server.created_threads, [])

    def test_reads_safe_thread_history_without_reasoning(self):
        status, payload = self.request("GET", "/api/codex/threads/thread-1")
        self.assertEqual(status, 200)
        items = payload["thread"]["turns"][0]["items"]
        self.assertEqual(
            [item["type"] for item in items],
            ["userMessage", "agentMessage", "fileChange"],
        )
        self.assertEqual(items[1]["text"], "已经完成。")
        self.assertEqual(items[1]["phase"], "final_answer")
        self.assertEqual(items[2]["changes"][0]["kind"], "add")
        self.assertEqual(items[0]["text"], "继续开发")
        self.assertEqual(
            items[0]["attachments"],
            [
                {
                    "type": "image",
                    "name": "codex-clipboard-test-image.png",
                    "path": str(
                        Path(tempfile.gettempdir())
                        / "codex-clipboard-test-image.png"
                    ),
                }
            ],
        )

    def test_reads_model_catalog_and_current_thread_settings(self):
        status, payload = self.request(
            "GET", "/api/codex/models?threadId=thread-1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["model"], "gpt-5.6-sol")
        self.assertEqual(payload["settings"]["effort"], "high")
        self.assertEqual(payload["models"][0]["displayName"], "GPT-5.6-Sol")
        self.assertEqual(
            [option["id"] for option in payload["models"][0]["efforts"]],
            ["low", "high"],
        )

    def test_updates_idle_thread_model_settings(self):
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/settings",
            {
                "model": "gpt-5.6-sol",
                "effort": "low",
                "serviceTier": None,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["effort"], "low")
        self.assertEqual(
            self.app_server.updated_settings,
            [
                (
                    "thread-1",
                    {
                        "model": "gpt-5.6-sol",
                        "effort": "low",
                        "serviceTier": None,
                    },
                )
            ],
        )

    def test_rejects_model_update_while_thread_is_running(self):
        self.app_server.last_turn_status = "inProgress"
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/settings",
            {
                "model": "gpt-5.6-sol",
                "effort": "low",
                "serviceTier": None,
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "model_settings_locked")
        self.assertEqual(self.app_server.updated_settings, [])

    def test_reads_remaining_usage_without_account_secrets(self):
        status, payload = self.request("GET", "/api/codex/usage")
        self.assertEqual(status, 200)
        self.assertEqual(payload["usage"]["planType"], "pro")
        self.assertEqual(
            payload["usage"]["limits"][0]["primary"]["remainingPercent"],
            35.0,
        )
        self.assertEqual(
            payload["usage"]["limits"][1]["name"],
            "GPT-5.3-Codex-Spark",
        )
        self.assertNotIn("credits", json.dumps(payload))

    def test_thread_history_uses_revision_cache_and_turn_limit(self):
        path = "/api/codex/threads/thread-1?turns=30&revision=123"
        first_status, first_payload = self.request("GET", path)
        second_status, second_payload = self.request("GET", path)
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(self.app_server.read_count, 1)
        self.assertEqual(first_payload["thread"]["historyLimit"], 30)
        self.assertEqual(first_payload, second_payload)

        fresh_status, _ = self.request("GET", f"{path}&fresh=1")
        self.assertEqual(fresh_status, 200)
        self.assertEqual(self.app_server.read_count, 2)

    def test_dispatches_turn_to_codex_desktop(self):
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/turn",
            {"message": "继续修复"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["mode"], "desktop")
        self.assertEqual(payload["desktop"]["taskTitle"], "修复移动页面")
        self.assertEqual(
            self.controller.desktop_sent,
            [("thread-1", "修复移动页面", "继续修复", False)],
        )

        status, payload = self.request("GET", "/api/codex/threads/thread-1/run")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["run"])

    def test_interrupts_legacy_managed_turn(self):
        self.app_server.runs["thread-1"] = {
            "threadId": "thread-1",
            "turnId": "turn-managed-1",
            "status": "inProgress",
        }

        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/interrupt",
            {"confirm": True},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["run"]["status"], "interrupting")

    def test_continues_interrupted_turn_without_user_message(self):
        self.app_server.last_turn_status = "interrupted"
        self.controller.task_title = "修复移动页面"
        self.controller.count = 0
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/continue",
            {},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["mode"], "desktop")
        self.assertEqual(payload["desktop"]["mode"], "continue")
        self.assertEqual(
            self.controller.desktop_sent,
            [("thread-1", "修复移动页面", "", True)],
        )

    def test_refuses_continue_when_latest_turn_is_not_interrupted(self):
        self.controller.task_title = "修复移动页面"
        self.controller.count = 0
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/continue",
            {},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "thread_not_interrupted")
        self.assertEqual(self.app_server.continued, [])

    def test_refuses_dispatch_when_desktop_turn_is_active(self):
        self.controller.dispatch_error = "desktop_turn_active"
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/turn",
            {"message": "不要并发"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "desktop_turn_active")
        self.assertEqual(self.controller.desktop_sent, [])

    def test_dispatches_to_selected_desktop_thread(self):
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/turn",
            {"message": "空闲后接管"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["desktop"]["stopCandidates"], 1)
        self.assertEqual(
            self.controller.desktop_sent,
            [("thread-1", "修复移动页面", "空闲后接管", False)],
        )

    def test_desktop_dispatch_does_not_depend_on_background_title_probe(self):
        self.controller.fail_status = True
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/turn",
            {"message": "恢复项目任务"},
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            self.controller.desktop_sent,
            [("thread-1", "修复移动页面", "恢复项目任务", False)],
        )

    def test_reports_desktop_dispatch_identity_failure(self):
        self.controller.fail_status = True
        self.controller.dispatch_error = "task_identity_mismatch"
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/turn",
            {"message": "不要并发"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "task_identity_mismatch")
        self.assertEqual(self.controller.desktop_sent, [])

    def test_responds_to_managed_pending_request(self):
        self.app_server.runs["thread-1"] = {
            "threadId": "thread-1",
            "turnId": "turn-managed-1",
            "status": "waitingForInput",
            "pendingRequest": {"id": "request-1", "kind": "commandApproval"},
        }
        status, payload = self.request(
            "POST",
            "/api/codex/threads/thread-1/requests/request-1",
            {"decision": "decline"},
        )
        self.assertEqual(status, 200)
        self.assertIsNone(payload["run"]["pendingRequest"])
        self.assertEqual(
            payload["run"]["response"],
            {"requestKey": "request-1", "decision": "decline"},
        )

    def test_status_reports_unique_stop_button(self):
        status, payload = self.request("GET", "/api/desktop/interrupt/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["interruptible"])
        self.assertEqual(payload["stopCandidates"], 1)
        self.assertEqual(payload["taskTitle"], "继续项目开发")

    def test_status_exposes_safe_desktop_request(self):
        self.controller.desktop_request = {
            "kind": "approval",
            "prompt": "Run command\ngit status",
            "fingerprint": "a" * 64,
            "actions": [
                {"id": "approve_once", "label": "Allow once"},
                {"id": "deny", "label": "Deny"},
            ],
            "options": [],
            "allowsFreeform": False,
        }
        status, payload = self.request("GET", "/api/desktop/interrupt/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["request"]["kind"], "approval")
        self.assertNotIn("always_allow", str(payload["request"]))

    def test_responds_to_desktop_request_with_identity(self):
        fingerprint = "b" * 64
        self.controller.desktop_request = {"fingerprint": fingerprint}
        status, payload = self.request(
            "POST",
            "/api/desktop/request",
            {
                "expectedTaskTitle": "继续项目开发",
                "fingerprint": fingerprint,
                "action": "approve_once",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["response"]["ok"])
        self.assertEqual(
            self.controller.desktop_request_responses,
            [("继续项目开发", fingerprint, "approve_once", None, None)],
        )

    def test_desktop_request_rejects_stale_fingerprint(self):
        self.controller.desktop_request_error = "desktop_request_changed"
        status, payload = self.request(
            "POST",
            "/api/desktop/request",
            {
                "expectedTaskTitle": "继续项目开发",
                "fingerprint": "c" * 64,
                "action": "deny",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "desktop_request_changed")

    def test_desktop_request_rejects_persistent_approval_action(self):
        status, payload = self.request(
            "POST",
            "/api/desktop/request",
            {
                "expectedTaskTitle": "继续项目开发",
                "fingerprint": "d" * 64,
                "action": "always_allow",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_desktop_request_response")

    def test_status_allows_safe_attempt_when_window_is_occluded(self):
        self.controller.count = 0
        status, payload = self.request("GET", "/api/desktop/interrupt/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["interruptible"])
        self.assertEqual(payload["stopCandidates"], 0)

    def test_interrupt_requires_explicit_confirmation(self):
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {"expectedTaskTitle": "继续项目开发"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "confirmation_required")
        self.assertFalse(self.controller.interrupted)

    def test_interrupt_requires_task_identity(self):
        status, payload = self.request(
            "POST", "/api/desktop/interrupt", {"confirm": True}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "task_identity_required")
        self.assertFalse(self.controller.interrupted)

    def test_interrupt_requires_thread_identity(self):
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {"confirm": True, "expectedTaskTitle": "继续项目开发"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "thread_identity_required")
        self.assertFalse(self.controller.interrupted)

    def test_interrupt_presses_only_unique_stop_button(self):
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {
                "confirm": True,
                "threadId": "thread-1",
                "expectedTaskTitle": "继续项目开发",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["interrupted"])
        self.assertTrue(self.controller.interrupted)
        self.assertEqual(self.controller.interrupted_thread, "thread-1")

    def test_interrupt_refuses_ambiguous_state(self):
        self.controller.count = 2
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {
                "confirm": True,
                "threadId": "thread-1",
                "expectedTaskTitle": "继续项目开发",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["stopCandidates"], 2)
        self.assertFalse(self.controller.interrupted)

    def test_interrupt_refuses_if_foreground_task_changed(self):
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {
                "confirm": True,
                "threadId": "thread-1",
                "expectedTaskTitle": "另一个任务",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "foreground_task_changed")
        self.assertFalse(self.controller.interrupted)

    def test_pairing_ticket_enrolls_persistent_device(self):
        status, payload = self.request(
            "POST", "/api/devices/pairing-ticket", {}
        )
        self.assertEqual(status, 201)
        ticket = payload["pairingTicket"]

        status, payload = self.request(
            "POST",
            "/api/devices/enroll",
            {"name": "Android test", "pairingTicket": ticket},
            authorized=False,
        )
        self.assertEqual(status, 201)
        device_token = payload["device"]["deviceToken"]

        status, payload = self.request(
            "GET",
            "/api/desktop/interrupt/status",
            bearer=device_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["taskTitle"], "继续项目开发")

    def test_pairing_ticket_is_single_use(self):
        _, payload = self.request("POST", "/api/devices/pairing-ticket", {})
        body = {"name": "Android test", "pairingTicket": payload["pairingTicket"]}
        first_status, _ = self.request(
            "POST", "/api/devices/enroll", body, authorized=False
        )
        second_status, second_payload = self.request(
            "POST", "/api/devices/enroll", body, authorized=False
        )
        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 401)
        self.assertEqual(second_payload["error"], "invalid_or_expired_pairing")

    def test_master_control_is_disabled_after_device_migration(self):
        status, payload = self.request(
            "POST",
            "/api/devices/enroll",
            {"name": "Existing Android"},
        )
        self.assertEqual(status, 201)
        device = payload["device"]

        status, payload = self.request("GET", "/api/desktop/interrupt/status")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

        status, payload = self.request("GET", "/api/devices")
        self.assertEqual(status, 200)
        self.assertEqual(payload["devices"][0]["id"], device["id"])
        self.assertNotIn("deviceToken", payload["devices"][0])

        status, payload = self.request("DELETE", f"/api/devices/{device['id']}")
        self.assertEqual(status, 200)

        status, _ = self.request(
            "GET",
            "/api/desktop/interrupt/status",
            bearer=device["deviceToken"],
        )
        self.assertEqual(status, 401)
        status, _ = self.request("GET", "/api/desktop/interrupt/status")
        self.assertEqual(status, 401)


class TokenFileTest(unittest.TestCase):
    def test_reads_user_only_token_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text(TOKEN, encoding="utf-8")
            os.chmod(path, 0o600)
            self.assertEqual(load_token(path), TOKEN)

    def test_refuses_group_readable_token_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text(TOKEN, encoding="utf-8")
            os.chmod(path, 0o640)
            with self.assertRaises(SystemExit):
                load_token(path)

    def test_refuses_file_and_keychain_together(self):
        with self.assertRaises(SystemExit):
            load_token(Path("/tmp/token"), "mobile-codex-bridge")


class DeviceRegistryTest(unittest.TestCase):
    def test_registry_persists_only_token_hash_with_user_only_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "devices.json"
            registry = DeviceRegistry(path)
            registry.ensure_ready()
            enrolled = registry.enroll("Android test", "test-agent")

            contents = path.read_text(encoding="utf-8")
            self.assertNotIn(enrolled["deviceToken"], contents)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            restored = DeviceRegistry(path)
            self.assertIsNotNone(restored.authenticate(enrolled["deviceToken"]))


class DesktopControllerCommandTest(unittest.TestCase):
    @patch("mac_bridge.subprocess.run")
    def test_desktop_state_uses_single_helper_snapshot(self, run):
        request = {
            "kind": "approval",
            "fingerprint": "a" * 64,
            "prompt": "Run command",
            "actions": [],
        }
        run.return_value = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "taskTitles": ["继续项目开发"],
                        "stopCandidates": 1,
                        "request": request,
                    }
                ),
                "stderr": "",
            },
        )()
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        self.assertEqual(controller.status()["request"], request)
        self.assertEqual(
            run.call_args.args[0],
            [
                "/Applications/MobileCodexBridgeHelper/mobile-codex-ax",
                "--desktop-state",
            ],
        )

    @patch("mac_bridge.subprocess.run")
    def test_desktop_request_response_passes_identity_over_stdin(self, run):
        run.return_value = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"ok":true,"action":"answer"}',
                "stderr": "",
            },
        )()
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        response = controller.respond_to_desktop_request(
            "继续项目开发",
            "e" * 64,
            "answer",
            option_label="推荐选项",
        )

        self.assertTrue(response["ok"])
        self.assertEqual(
            run.call_args.args[0],
            [
                "/Applications/MobileCodexBridgeHelper/mobile-codex-ax",
                "--desktop-request-respond",
            ],
        )
        stdin_payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(stdin_payload["fingerprint"], "e" * 64)
        self.assertEqual(stdin_payload["optionLabel"], "推荐选项")

    @patch("mac_bridge.subprocess.run")
    def test_desktop_send_passes_message_over_stdin(self, run):
        run.return_value = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "taskTitle": "继续项目开发",
                        "stopCandidates": 1,
                        "mode": "message",
                    }
                ),
                "stderr": "",
            },
        )()
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        result = controller.send_to_desktop(
            "019fb6fd-68d6-71f1-8d60-ea75a658d0ab",
            "继续项目开发",
            "手机指令",
            attachment_paths=[
                "/Users/test/Library/Application Support/MobileCodexBridge/uploads/id/file.txt"
            ],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            run.call_args.args[0],
            [
                "/Applications/MobileCodexBridgeHelper/mobile-codex-ax",
                "--desktop-send",
            ],
        )
        stdin_payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(stdin_payload["message"], "手机指令")
        self.assertFalse(stdin_payload["continueOnly"])
        self.assertEqual(
            stdin_payload["attachmentPaths"],
            [
                "/Users/test/Library/Application Support/MobileCodexBridge/uploads/id/file.txt"
            ],
        )

    @patch("mac_bridge.subprocess.run")
    def test_desktop_send_maps_active_turn_conflict(self, run):
        run.return_value = type(
            "Result",
            (),
            {"returncode": 27, "stdout": "", "stderr": "already running"},
        )()
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        with self.assertRaises(DesktopDispatchError) as raised:
            controller.send_to_desktop(
                "019fb6fd-68d6-71f1-8d60-ea75a658d0ab",
                "继续项目开发",
                "不要并发",
            )
        self.assertEqual(raised.exception.reason, "desktop_turn_active")

    @patch("mac_bridge.time.sleep")
    @patch("mac_bridge.subprocess.run")
    def test_desktop_send_retries_transient_composer_after_attachment(
        self,
        run,
        sleep,
    ):
        run.side_effect = [
            type(
                "Result",
                (),
                {
                    "returncode": 28,
                    "stdout": "",
                    "stderr": "composer replaced after attachment",
                },
            )(),
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "ok": True,
                            "taskTitle": "描述一下这张图片",
                            "stopCandidates": 1,
                            "mode": "message",
                        }
                    ),
                    "stderr": "",
                },
            )(),
        ]
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridge/mobile-codex-ax"),
        )

        response = controller.send_to_desktop(
            "thread-1",
            "描述一下这张图片",
            "描述一下这张图片",
            attachment_paths=["/Users/test/uploads/photo.jpg"],
        )

        self.assertTrue(response["ok"])
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(3.0)

    @patch("mac_bridge.time.sleep")
    @patch("mac_bridge.subprocess.run")
    def test_desktop_send_rechecks_delayed_attachment_confirmation(
        self,
        run,
        sleep,
    ):
        run.side_effect = [
            type(
                "Result",
                (),
                {
                    "returncode": 43,
                    "stdout": "",
                    "stderr": "attachment preview was not observable yet",
                },
            )(),
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "ok": True,
                            "taskTitle": "分析这张图片",
                            "stopCandidates": 1,
                            "mode": "message",
                        }
                    ),
                    "stderr": "",
                },
            )(),
        ]
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridge/mobile-codex-ax"),
        )

        response = controller.send_to_desktop(
            "thread-1",
            "分析这张图片",
            "请直接看图",
            attachment_paths=["/Users/test/uploads/photo.jpg"],
        )

        self.assertTrue(response["ok"])
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(3.0)

    @patch("mac_bridge.subprocess.run")
    def test_desktop_send_maps_missing_accessibility_permission(self, run):
        run.return_value = type(
            "Result",
            (),
            {
                "returncode": 2,
                "stdout": "",
                "stderr": "Accessibility permission is not granted",
            },
        )()
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        with self.assertRaises(DesktopDispatchError) as raised:
            controller.send_to_desktop(
                "019fb6fd-68d6-71f1-8d60-ea75a658d0ab",
                "继续项目开发",
                "手机指令",
            )
        self.assertEqual(
            raised.exception.reason,
            "desktop_accessibility_unavailable",
        )

    @patch("mac_bridge.subprocess.run")
    def test_uses_compiled_helper_when_configured(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = '{"stopCandidates":1}'
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        self.assertEqual(controller.stop_candidate_count(), 1)
        self.assertEqual(
            run.call_args.args[0],
            [
                "/Applications/MobileCodexBridgeHelper/mobile-codex-ax",
                "--check-stop",
            ],
        )

    @patch("mac_bridge.subprocess.run")
    def test_uses_swift_script_without_compiled_helper(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = '{"stopCandidates":0}'
        controller = DesktopController(Path("/repo/scripts/codex-ax.swift"))

        self.assertEqual(controller.stop_candidate_count(), 0)
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/swift", "/repo/scripts/codex-ax.swift", "--check-stop"],
        )

    @patch("mac_bridge.subprocess.run")
    def test_interrupt_thread_navigates_by_id_before_stopping(self, run):
        opened = type(
            "Result", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()
        title = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"taskTitles":["继续项目开发"]}',
                "stderr": "",
            },
        )()
        stop = type(
            "Result",
            (),
            {"returncode": 0, "stdout": '{"stopCandidates":1}', "stderr": ""},
        )()
        pressed = type(
            "Result", (), {"returncode": 0, "stdout": "stopped", "stderr": ""}
        )()
        run.side_effect = [opened, title, title, stop, title, pressed]
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        controller.interrupt_thread("thread-1", "继续项目开发")

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands[0],
            [
                "/usr/bin/open",
                "-b",
                "com.openai.codex",
                "codex://threads/thread-1",
            ],
        )
        self.assertEqual(commands[-1][-2:], ["--stop", "--expected-task-title=继续项目开发"])

    @patch("mac_bridge.subprocess.run")
    def test_foreground_status_activates_and_rechecks_task_identity(self, run):
        activated = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})
        title = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"taskTitles":["继续项目开发"]}',
                "stderr": "",
            },
        )
        stop = type(
            "Result",
            (),
            {"returncode": 0, "stdout": '{"stopCandidates":0}', "stderr": ""},
        )
        run.side_effect = [activated, title, stop, title]
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        self.assertEqual(
            controller.foreground_status("继续项目开发"),
            {"taskTitle": "继续项目开发", "stopCandidates": 0},
        )
        self.assertEqual(
            [call.args[0][-1] for call in run.call_args_list],
            ["--activate", "--current-task", "--check-stop", "--current-task"],
        )

    @patch("mac_bridge.subprocess.run")
    def test_takeover_status_double_checks_a_different_desktop_task(self, run):
        activated = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})
        other_title = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"taskTitles":["桌面上的另一个任务"]}',
                "stderr": "",
            },
        )
        run.side_effect = [activated, other_title, other_title]
        controller = DesktopController(
            Path("/repo/scripts/codex-ax.swift"),
            ax_helper=Path("/Applications/MobileCodexBridgeHelper/mobile-codex-ax"),
        )

        self.assertEqual(
            controller.managed_takeover_status("手机准备恢复的项目任务"),
            {
                "sameThread": False,
                "taskTitle": "桌面上的另一个任务",
                "stopCandidates": None,
            },
        )
        self.assertEqual(
            [call.args[0][-1] for call in run.call_args_list],
            ["--activate", "--current-task", "--current-task"],
        )


if __name__ == "__main__":
    unittest.main()
