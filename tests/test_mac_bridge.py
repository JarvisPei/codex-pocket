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

from codex_app_server import CodexAppServerClient, summarize_thread_detail
from mac_bridge import (
    BridgeServer,
    DesktopDispatchError,
    DesktopRequestError,
    DesktopController,
    DeviceRegistry,
    StopCandidateError,
    TaskChangedError,
    load_token,
)


TOKEN = "t" * 32


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
        self.fail_status = False
        self.desktop_sent = []
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

    def send_to_desktop(
        self,
        thread_id,
        expected_task_title,
        message,
        *,
        continue_only=False,
    ):
        if self.dispatch_error:
            raise DesktopDispatchError(self.dispatch_error, "dispatch refused")
        self.desktop_sent.append(
            (thread_id, expected_task_title, message, continue_only)
        )
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
                                "content": [{"type": "text", "text": "继续开发"}],
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
        self.assertIn("modelSettingsButton", body)
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
        self.assertIsNone(self.app_server.created_threads[0]["thread"]["cwd"])
        state = json.loads(self.codex_state_path.read_text(encoding="utf-8"))
        self.assertIn("thread-new-1", state["projectless-thread-ids"])
        self.assertNotIn(
            "thread-new-1",
            state["thread-project-assignments"],
        )

    def test_rejects_new_task_before_creation_when_desktop_is_running(self):
        self.controller.count = 1
        status, payload = self.request(
            "POST",
            "/api/codex/threads",
            {"projectId": "project-id", "message": "不要创建半成品"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "desktop_turn_active")
        self.assertEqual(self.app_server.created_threads, [])

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

    def test_interrupt_presses_only_unique_stop_button(self):
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {"confirm": True, "expectedTaskTitle": "继续项目开发"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["interrupted"])
        self.assertTrue(self.controller.interrupted)

    def test_interrupt_refuses_ambiguous_state(self):
        self.controller.count = 2
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {"confirm": True, "expectedTaskTitle": "继续项目开发"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["stopCandidates"], 2)
        self.assertFalse(self.controller.interrupted)

    def test_interrupt_refuses_if_foreground_task_changed(self):
        status, payload = self.request(
            "POST",
            "/api/desktop/interrupt",
            {"confirm": True, "expectedTaskTitle": "另一个任务"},
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
