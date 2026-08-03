"""Version-matched Codex app-server client over private stdio JSONL."""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional


_ROLLOUT_ACTIVITY_LOCK = threading.Lock()
_ROLLOUT_ACTIVITY_CACHE: dict[str, dict[str, Any]] = {}
_PROJECT_STATE_LOCK = threading.Lock()
_READ_ONLY_COMMAND = re.compile(
    r"^(?:/[^\s]+/)?(?:rg|grep|find|ls|pwd|head|tail|wc|stat|file)\b"
    r"|^(?:/[^\s]+/)?sed\s+-n\b"
    r"|^git\s+(?:status|diff|log|show|branch|rev-parse)\b",
)


class AppServerError(RuntimeError):
    pass


class ManagedTurnConflict(AppServerError):
    pass


class ManagedRequestError(AppServerError):
    pass


class CodexAppServerClient:
    def __init__(self, codex_binary: Path) -> None:
        self.codex_binary = codex_binary
        self._process: Optional[subprocess.Popen[str]] = None
        self._reader: Optional[threading.Thread] = None
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._managed_lock = threading.Lock()
        self._settings_lock = threading.RLock()
        self._managed_runs: dict[str, dict[str, Any]] = {}
        self._server_requests: dict[str, dict[str, Any]] = {}
        self._next_id = 1
        self._closed = False

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                [str(self.codex_binary), "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as error:
            raise AppServerError("Unable to start Codex app-server.") from error
        self._reader = threading.Thread(
            target=self._read_loop,
            name="codex-app-server-reader",
            daemon=True,
        )
        self._reader.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "mobile_codex_desktop_mac",
                        "title": "Codex Pocket Bridge",
                        "version": "0.2.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            if isinstance(method, str):
                if "id" in message:
                    self._handle_server_request(message)
                else:
                    self._handle_notification(method, message.get("params"))
                continue
            request_id = message.get("id")
            if not isinstance(request_id, int):
                continue
            with self._pending_lock:
                response_queue = self._pending.get(request_id)
            if response_queue is not None:
                response_queue.put(message)
        self._fail_pending("Codex app-server connection closed.")

    @staticmethod
    def _is_active_status(status: Any) -> bool:
        return status in {"starting", "inProgress", "waitingForInput", "interrupting"}

    def _touch_run(self, run: dict[str, Any]) -> None:
        run["revision"] = int(run.get("revision", 0)) + 1
        run["updatedAt"] = time.time()

    def _handle_notification(self, method: str, params: Any) -> None:
        if not isinstance(params, dict):
            return
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            return
        with self._managed_lock:
            run = self._managed_runs.get(thread_id)
            if run is None:
                return
            if method == "turn/started":
                turn = params.get("turn")
                if isinstance(turn, dict):
                    run["turnId"] = str(turn.get("id", run.get("turnId", "")))
                run["status"] = "inProgress"
            elif method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    run["agentText"] = (run.get("agentText", "") + delta)[-100_000:]
            elif method in {"item/started", "item/completed"}:
                safe_item = _safe_item(params.get("item"))
                if safe_item is not None:
                    items = run.setdefault("items", [])
                    existing = next(
                        (
                            index
                            for index, item in enumerate(items)
                            if item.get("id") == safe_item.get("id")
                        ),
                        None,
                    )
                    if existing is None:
                        items.append(safe_item)
                    else:
                        items[existing] = safe_item
                    del items[:-100]
                    if safe_item.get("type") == "agentMessage":
                        run["agentText"] = safe_item.get("text", run.get("agentText", ""))
            elif method == "turn/completed":
                turn = params.get("turn")
                if isinstance(turn, dict):
                    run["turnId"] = str(turn.get("id", run.get("turnId", "")))
                    run["status"] = str(turn.get("status", "completed"))
                    error = turn.get("error")
                    run["error"] = (
                        _bounded_text(error.get("message"), 2_000)
                        if isinstance(error, dict)
                        else None
                    )
                else:
                    run["status"] = "completed"
                run["pendingRequest"] = None
                stale = [
                    key
                    for key, request in self._server_requests.items()
                    if request.get("threadId") == thread_id
                ]
                for key in stale:
                    self._server_requests.pop(key, None)
            elif method == "error":
                error = params.get("error")
                run["error"] = _bounded_text(
                    error.get("message") if isinstance(error, dict) else params.get("message"),
                    2_000,
                )
            else:
                return
            self._touch_run(run)

    def _safe_server_request(
        self,
        key: str,
        method: str,
        params: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        base = {
            "id": key,
            "threadId": str(params.get("threadId", "")),
            "turnId": str(params.get("turnId", "")),
        }
        if method == "item/commandExecution/requestApproval":
            return {
                **base,
                "kind": "commandApproval",
                "command": _bounded_text(params.get("command"), 4_000),
                "cwd": _bounded_text(params.get("cwd"), 1_000),
                "reason": _bounded_text(params.get("reason"), 2_000),
            }
        if method == "item/fileChange/requestApproval":
            return {
                **base,
                "kind": "fileApproval",
                "reason": _bounded_text(params.get("reason"), 2_000),
                "grantRoot": _bounded_text(params.get("grantRoot"), 1_000),
            }
        if method == "item/tool/requestUserInput":
            questions = params.get("questions")
            safe_questions = []
            if isinstance(questions, list):
                for question in questions[:3]:
                    if not isinstance(question, dict):
                        continue
                    options = question.get("options")
                    safe_options = []
                    if isinstance(options, list):
                        for option in options[:8]:
                            if isinstance(option, dict):
                                safe_options.append(
                                    {
                                        "label": _bounded_text(option.get("label"), 160),
                                        "description": _bounded_text(
                                            option.get("description"),
                                            500,
                                        ),
                                    }
                                )
                    safe_questions.append(
                        {
                            "id": _bounded_text(question.get("id"), 160),
                            "header": _bounded_text(question.get("header"), 160),
                            "question": _bounded_text(question.get("question"), 1_000),
                            "isSecret": bool(question.get("isSecret", False)),
                            "options": safe_options,
                        }
                    )
            return {**base, "kind": "userInput", "questions": safe_questions}
        if method == "mcpServer/elicitation/request":
            return {
                **base,
                "kind": "mcpElicitation",
                "message": _bounded_text(params.get("message"), 2_000),
            }
        return None

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        request_id = message.get("id")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            self._write(
                {
                    "id": request_id,
                    "error": {"code": -32602, "message": "threadId is required"},
                }
            )
            return
        key = secrets.token_urlsafe(12)
        safe_request = self._safe_server_request(key, method, params)
        if safe_request is None:
            self._write(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "Unsupported remote interaction request",
                    },
                }
            )
            return
        with self._managed_lock:
            run = self._managed_runs.get(thread_id)
            if run is None:
                self._write(
                    {
                        "id": request_id,
                        "error": {"code": -32600, "message": "Thread is not managed"},
                    }
                )
                return
            self._server_requests[key] = {
                "requestId": request_id,
                "method": method,
                "threadId": thread_id,
                "safe": safe_request,
            }
            run["pendingRequest"] = safe_request
            run["status"] = "waitingForInput"
            self._touch_run(run)

    def _fail_pending(self, message: str) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            response_queue.put({"error": {"message": message}})

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if (
            process is None
            or process.poll() is not None
            or process.stdin is None
            or self._closed
        ):
            raise AppServerError("Codex app-server is not running.")
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        try:
            with self._write_lock:
                process.stdin.write(f"{encoded}\n")
                process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise AppServerError("Codex app-server connection failed.") from error

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float = 15,
    ) -> dict[str, Any]:
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._write({"id": request_id, "method": method, "params": params})
            try:
                response = response_queue.get(timeout=timeout)
            except queue.Empty as error:
                raise AppServerError(f"Codex app-server timed out on {method}.") from error
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            error_payload = response.get("error")
            detail = (
                error_payload.get("message", "request failed")
                if isinstance(error_payload, dict)
                else "request failed"
            )
            raise AppServerError(f"Codex app-server rejected {method}: {detail}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise AppServerError(f"Codex app-server returned invalid data for {method}.")
        return result

    def list_threads(self, limit: int = 30) -> dict[str, Any]:
        return self.request(
            "thread/list",
            {
                "limit": limit,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "useStateDbOnly": True,
            },
        )

    def read_thread(self, thread_id: str) -> dict[str, Any]:
        return self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
            timeout=25,
        )

    def list_models(self) -> dict[str, Any]:
        return self.request(
            "model/list",
            {"limit": 100, "includeHidden": False},
        )

    def read_rate_limits(self) -> dict[str, Any]:
        return self.request("account/rateLimits/read", {})

    @staticmethod
    def _thread_settings_from_resume(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": str(result.get("model", "")),
            "effort": str(result.get("reasoningEffort", "")),
            "serviceTier": (
                str(result["serviceTier"])
                if isinstance(result.get("serviceTier"), str)
                else None
            ),
        }

    def read_thread_settings(self, thread_id: str) -> dict[str, Any]:
        with self._settings_lock:
            result = self.request(
                "thread/resume",
                {"threadId": thread_id},
                timeout=30,
            )
            return self._thread_settings_from_resume(result)

    def _validate_thread_settings(
        self,
        model: str,
        effort: str,
        service_tier: Optional[str],
    ) -> None:
        model_result = self.list_models()
        models = model_result.get("data")
        selected = next(
            (
                candidate
                for candidate in models
                if isinstance(candidate, dict) and candidate.get("model") == model
            ),
            None,
        ) if isinstance(models, list) else None
        if selected is None:
            raise ManagedRequestError("Unknown model.")
        efforts = selected.get("supportedReasoningEfforts")
        supported_efforts = {
            option.get("reasoningEffort")
            for option in efforts
            if isinstance(option, dict)
        } if isinstance(efforts, list) else set()
        if effort not in supported_efforts:
            raise ManagedRequestError("Unsupported reasoning effort.")
        tiers = selected.get("serviceTiers")
        supported_tiers = {
            tier.get("id")
            for tier in tiers
            if isinstance(tier, dict)
        } if isinstance(tiers, list) else set()
        if service_tier is not None and service_tier not in supported_tiers:
            raise ManagedRequestError("Unsupported service tier.")

    def update_thread_settings(
        self,
        thread_id: str,
        *,
        model: str,
        effort: str,
        service_tier: Optional[str],
    ) -> dict[str, Any]:
        with self._settings_lock:
            self._validate_thread_settings(model, effort, service_tier)

            # Settings updates only address threads loaded into this app-server.
            # Resume loads the persisted thread but does not start or continue a turn.
            self.request(
                "thread/resume",
                {"threadId": thread_id},
                timeout=30,
            )
            self.request(
                "thread/settings/update",
                {
                    "threadId": thread_id,
                    "model": model,
                    "effort": effort,
                    "serviceTier": service_tier,
                },
            )
            return {
                "model": model,
                "effort": effort,
                "serviceTier": service_tier,
            }

    def create_thread(
        self,
        *,
        title: str,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        service_tier: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create an idle persisted thread for Codex Desktop to take over."""
        params: dict[str, Any] = {"ephemeral": False}
        if cwd:
            params["cwd"] = cwd
        if model:
            params["model"] = model
        if service_tier:
            params["serviceTier"] = service_tier
        with self._settings_lock:
            if model or effort or service_tier:
                if not model or not effort:
                    raise ManagedRequestError("Incomplete model settings.")
                self._validate_thread_settings(model, effort, service_tier)
            result = self.request("thread/start", params, timeout=30)
            thread = result.get("thread")
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                raise AppServerError("Invalid created thread.")
            thread_id = thread["id"]
            self.request(
                "thread/name/set",
                {"threadId": thread_id, "name": title},
                timeout=30,
            )
            settings = self._thread_settings_from_resume(result)
            if model and effort:
                settings = self.update_thread_settings(
                    thread_id,
                    model=model,
                    effort=effort,
                    service_tier=service_tier,
                )
            thread["name"] = title
            return {"thread": thread, "settings": settings}

    def start_turn(
        self,
        thread_id: str,
        text: str,
        *,
        resume: bool = True,
    ) -> dict[str, Any]:
        message = text.strip()
        if not message or len(message) > 20_000:
            raise ManagedRequestError("Message must contain 1 to 20000 characters.")
        return self._start_turn(
            thread_id,
            input_items=[{"type": "text", "text": message}],
            user_text=message,
            resume=resume,
        )

    def continue_turn(
        self,
        thread_id: str,
        *,
        resume: bool = True,
    ) -> dict[str, Any]:
        """Continue an interrupted thread without adding a user message."""
        return self._start_turn(
            thread_id,
            input_items=[],
            user_text="",
            resume=resume,
        )

    def _start_turn(
        self,
        thread_id: str,
        *,
        input_items: list[dict[str, Any]],
        user_text: str,
        resume: bool,
    ) -> dict[str, Any]:
        with self._managed_lock:
            existing = self._managed_runs.get(thread_id)
            if existing is not None and self._is_active_status(existing.get("status")):
                raise ManagedTurnConflict("A managed turn is already active.")
            run = {
                "threadId": thread_id,
                "turnId": "",
                "status": "starting",
                "userText": user_text,
                "agentText": "",
                "items": [],
                "pendingRequest": None,
                "error": None,
                "revision": 1,
                "startedAt": time.time(),
                "updatedAt": time.time(),
            }
            self._managed_runs[thread_id] = run
        try:
            if resume:
                self.request("thread/resume", {"threadId": thread_id}, timeout=30)
            result = self.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": input_items,
                },
                timeout=30,
            )
            turn = result.get("turn")
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                raise AppServerError("Codex app-server returned an invalid turn.")
            with self._managed_lock:
                run = self._managed_runs[thread_id]
                run["turnId"] = turn["id"]
                if run.get("status") == "starting":
                    run["status"] = str(turn.get("status", "inProgress"))
                self._touch_run(run)
                return self._managed_run_snapshot(run)
        except Exception as error:
            with self._managed_lock:
                run = self._managed_runs[thread_id]
                run["status"] = "failed"
                run["error"] = _bounded_text(str(error), 2_000)
                self._touch_run(run)
            raise

    @staticmethod
    def _managed_run_snapshot(run: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(run, ensure_ascii=False))

    def managed_run(self, thread_id: str) -> Optional[dict[str, Any]]:
        with self._managed_lock:
            run = self._managed_runs.get(thread_id)
            return self._managed_run_snapshot(run) if run is not None else None

    def interrupt_turn(self, thread_id: str) -> dict[str, Any]:
        with self._managed_lock:
            run = self._managed_runs.get(thread_id)
            if run is None or not self._is_active_status(run.get("status")):
                raise ManagedTurnConflict("No managed turn is active.")
            turn_id = run.get("turnId")
            if not isinstance(turn_id, str) or not turn_id:
                raise ManagedTurnConflict("The managed turn has not started yet.")
            run["status"] = "interrupting"
            self._touch_run(run)
        try:
            self.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
        except Exception:
            with self._managed_lock:
                run = self._managed_runs[thread_id]
                run["status"] = "inProgress"
                self._touch_run(run)
            raise
        return self.managed_run(thread_id) or {}

    def respond_to_request(
        self,
        thread_id: str,
        request_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._managed_lock:
            pending = self._server_requests.get(request_key)
            if pending is None or pending.get("threadId") != thread_id:
                raise ManagedRequestError("Pending request was not found.")
            method = pending["method"]
            request_id = pending["requestId"]
            safe = pending["safe"]

        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            decision = payload.get("decision")
            if decision not in {"accept", "decline", "cancel"}:
                raise ManagedRequestError("Invalid approval decision.")
            result = {"decision": decision}
        elif method == "item/tool/requestUserInput":
            supplied_answers = payload.get("answers")
            if not isinstance(supplied_answers, dict):
                raise ManagedRequestError("Answers are required.")
            question_ids = {
                question["id"]
                for question in safe.get("questions", [])
                if question.get("id")
            }
            answers: dict[str, dict[str, list[str]]] = {}
            for question_id in question_ids:
                value = supplied_answers.get(question_id)
                values = value if isinstance(value, list) else [value]
                if not values or any(
                    not isinstance(answer, str) or len(answer) > 2_000
                    for answer in values
                ):
                    raise ManagedRequestError("Invalid user input answer.")
                answers[question_id] = {"answers": values}
            if set(answers) != question_ids:
                raise ManagedRequestError("Every question requires an answer.")
            result = {"answers": answers}
        elif method == "mcpServer/elicitation/request":
            decision = payload.get("decision")
            if decision not in {"decline", "cancel"}:
                raise ManagedRequestError("Only decline or cancel is supported.")
            result = {"action": decision}
        else:
            raise ManagedRequestError("Unsupported pending request.")

        self._write({"id": request_id, "result": result})
        with self._managed_lock:
            self._server_requests.pop(request_key, None)
            run = self._managed_runs.get(thread_id)
            if run is not None:
                run["pendingRequest"] = None
                if self._is_active_status(run.get("status")):
                    run["status"] = "inProgress"
                self._touch_run(run)
                return self._managed_run_snapshot(run)
        return {}

    def close(self) -> None:
        self._closed = True
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._process = None


def _bounded_text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return text if len(text) <= limit else f"{text[:limit]}\n…"


def _command_from_tool_payload(payload: dict[str, Any]) -> str:
    arguments = payload.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = None
    if isinstance(arguments, dict) and isinstance(arguments.get("cmd"), str):
        return arguments["cmd"]
    source = payload.get("input")
    if not isinstance(source, str):
        return ""
    match = re.search(r"\bcmd\s*:\s*(\"(?:\\.|[^\"\\])*\")", source)
    if match is None:
        return ""
    try:
        command = json.loads(match.group(1))
    except json.JSONDecodeError:
        return ""
    return command if isinstance(command, str) else ""


def _command_activity_kind(command: str) -> str:
    segments = [
        segment.strip()
        for segment in re.split(r"(?:&&|\|\||;|\|)", command)
        if segment.strip()
    ]
    if segments and all(_READ_ONLY_COMMAND.search(segment) for segment in segments):
        return "readFiles"
    return "ranCommands"


def _activity_kinds_from_rollout_item(payload: dict[str, Any]) -> set[str]:
    payload_type = payload.get("type")
    name = str(payload.get("name", ""))
    if payload_type == "function_call":
        if name == "view_image":
            return {"readFiles"}
        if name == "exec_command":
            return {_command_activity_kind(_command_from_tool_payload(payload))}
        return set()
    if payload_type != "custom_tool_call" or name != "exec":
        return set()
    source = payload.get("input")
    if not isinstance(source, str):
        return set()
    kinds = set()
    if "view_image" in source or "read_mcp_resource" in source:
        kinds.add("readFiles")
    if "exec_command" in source:
        kinds.add(_command_activity_kind(_command_from_tool_payload(payload)))
    return kinds


def _rollout_activity_snapshot(thread: dict[str, Any]) -> dict[str, Any]:
    thread_id = str(thread.get("id", ""))
    raw_path = thread.get("path")
    if not thread_id or not isinstance(raw_path, str):
        return {"turns": {}, "activeTurnId": "", "activeStartedAt": None, "status": ""}
    path = Path(raw_path)
    if (
        not path.is_absolute()
        or path.suffix != ".jsonl"
        or not path.name.endswith(f"-{thread_id}.jsonl")
    ):
        return {"turns": {}, "activeTurnId": "", "activeStartedAt": None, "status": ""}
    try:
        size = path.stat().st_size
    except OSError:
        return {"turns": {}, "activeTurnId": "", "activeStartedAt": None, "status": ""}
    cache_key = str(path)
    with _ROLLOUT_ACTIVITY_LOCK:
        state = _ROLLOUT_ACTIVITY_CACHE.get(cache_key)
        if state is None or size < state["offset"]:
            state = {
                "offset": 0,
                "currentTurn": "",
                "currentStartedAt": None,
                "lastStatus": "",
                "counts": {},
            }
            _ROLLOUT_ACTIVITY_CACHE[cache_key] = state
        if size > state["offset"]:
            try:
                with path.open("rb") as handle:
                    handle.seek(state["offset"])
                    chunk = handle.read(size - state["offset"])
            except OSError:
                return {"turns": {}, "activeTurnId": "", "activeStartedAt": None, "status": ""}
            final_newline = chunk.rfind(b"\n")
            if final_newline >= 0:
                complete = chunk[: final_newline + 1]
                state["offset"] += final_newline + 1
                for raw_line in complete.splitlines():
                    try:
                        record = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    record_type = record.get("type")
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    event_type = payload.get("type")
                    if record_type == "event_msg" and event_type == "task_started":
                        state["currentTurn"] = str(payload.get("turn_id", ""))
                        state["currentStartedAt"] = payload.get("started_at") or record.get("timestamp")
                        state["lastStatus"] = "inProgress"
                        continue
                    if record_type == "event_msg" and event_type in {
                        "task_complete", "turn_aborted",
                    }:
                        if str(payload.get("turn_id", "")) == state["currentTurn"]:
                            state["currentTurn"] = ""
                            state["currentStartedAt"] = None
                        state["lastStatus"] = (
                            "completed" if event_type == "task_complete" else "interrupted"
                        )
                        continue
                    if record_type != "response_item" or not state["currentTurn"]:
                        continue
                    kinds = _activity_kinds_from_rollout_item(payload)
                    if not kinds:
                        continue
                    turn_counts = state["counts"].setdefault(state["currentTurn"], {})
                    for kind in kinds:
                        turn_counts[kind] = int(turn_counts.get(kind, 0)) + 1
        while len(_ROLLOUT_ACTIVITY_CACHE) > 8:
            _ROLLOUT_ACTIVITY_CACHE.pop(next(iter(_ROLLOUT_ACTIVITY_CACHE)))
        return {
            "turns": {
                turn_id: dict(counts)
                for turn_id, counts in state["counts"].items()
            },
            "activeTurnId": state["currentTurn"],
            "activeStartedAt": state["currentStartedAt"],
            "status": state["lastStatus"],
        }


def _status(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        result = {"type": str(value.get("type", "unknown"))}
        flags = value.get("activeFlags")
        if isinstance(flags, list):
            result["activeFlags"] = [str(flag) for flag in flags[:12]]
        return result
    return {"type": str(value or "unknown")}


def load_codex_project_index(state_path: Path) -> dict[str, Any]:
    """Read the small set of Codex Desktop sidebar metadata we mirror."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "projects": {},
            "assignments": {},
            "pinnedThreadIds": set(),
            "unreadThreadIds": set(),
        }
    if not isinstance(state, dict):
        return {
            "projects": {},
            "assignments": {},
            "pinnedThreadIds": set(),
            "unreadThreadIds": set(),
        }

    raw_projects = state.get("local-projects")
    raw_order = state.get("project-order")
    raw_assignments = state.get("thread-project-assignments")
    raw_pinned_thread_ids = state.get("pinned-thread-ids")
    persisted_atoms = state.get("electron-persisted-atom-state")
    unread_by_host = (
        persisted_atoms.get("unread-thread-ids-by-host-v1")
        if isinstance(persisted_atoms, dict)
        else None
    )
    raw_unread_thread_ids = (
        unread_by_host.get("local")
        if isinstance(unread_by_host, dict)
        else None
    )
    order = {
        str(project_id): index
        for index, project_id in enumerate(raw_order)
    } if isinstance(raw_order, list) else {}

    projects: dict[str, dict[str, Any]] = {}
    if isinstance(raw_projects, dict):
        for project_id, value in raw_projects.items():
            if not isinstance(value, dict):
                continue
            name = value.get("name")
            root_paths = value.get("rootPaths")
            root_path = next(
                (
                    path
                    for path in root_paths
                    if isinstance(path, str) and path.strip()
                ),
                None,
            ) if isinstance(root_paths, list) else None
            projects[str(project_id)] = {
                "id": str(project_id),
                "name": (
                    name.strip()
                    if isinstance(name, str) and name.strip()
                    else Path(root_path).name if root_path else "未命名项目"
                ),
                "path": root_path,
                "order": order.get(str(project_id), len(order)),
            }

    assignments: dict[str, str] = {}
    if isinstance(raw_assignments, dict):
        for thread_id, value in raw_assignments.items():
            if not isinstance(value, dict) or value.get("projectKind") != "local":
                continue
            project_id = str(value.get("projectId", ""))
            if project_id in projects:
                assignments[str(thread_id)] = project_id
    pinned_thread_ids = {
        str(thread_id)
        for thread_id in raw_pinned_thread_ids
        if isinstance(thread_id, str) and thread_id
    } if isinstance(raw_pinned_thread_ids, list) else set()
    unread_thread_ids = {
        str(thread_id)
        for thread_id in raw_unread_thread_ids
        if isinstance(thread_id, str) and thread_id
    } if isinstance(raw_unread_thread_ids, list) else set()
    return {
        "projects": projects,
        "assignments": assignments,
        "pinnedThreadIds": pinned_thread_ids,
        "unreadThreadIds": unread_thread_ids,
    }


def set_codex_thread_unread_state(
    state_path: Path,
    thread_id: str,
    unread: bool,
) -> None:
    """Update the local-host unread set persisted by Codex Desktop."""
    with _PROJECT_STATE_LOCK:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AppServerError("Unable to read Codex unread state.") from error
        if not isinstance(state, dict):
            raise AppServerError("Invalid Codex unread state.")
        persisted_atoms = state.get("electron-persisted-atom-state")
        if not isinstance(persisted_atoms, dict):
            persisted_atoms = {}
            state["electron-persisted-atom-state"] = persisted_atoms
        unread_by_host = persisted_atoms.get("unread-thread-ids-by-host-v1")
        if not isinstance(unread_by_host, dict):
            unread_by_host = {}
            persisted_atoms["unread-thread-ids-by-host-v1"] = unread_by_host
        local_ids = unread_by_host.get("local")
        normalized = [
            str(value)
            for value in local_ids
            if isinstance(value, str) and value and value != thread_id
        ] if isinstance(local_ids, list) else []
        if unread:
            normalized.append(thread_id)
        unread_by_host["local"] = normalized

        temporary = state_path.with_name(
            f".{state_path.name}.mobile-codex-{secrets.token_hex(6)}"
        )
        try:
            original_mode = state_path.stat().st_mode & 0o777
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.chmod(temporary, original_mode)
            os.replace(temporary, state_path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise AppServerError("Unable to update Codex unread state.") from error


def assign_codex_thread_collection(
    state_path: Path,
    thread_id: str,
    project_id: Optional[str],
) -> Optional[dict[str, Any]]:
    """Persist the same project/projectless metadata used by Codex Desktop."""
    with _PROJECT_STATE_LOCK:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AppServerError("Unable to read Codex project state.") from error
        if not isinstance(state, dict):
            raise AppServerError("Invalid Codex project state.")
        index = load_codex_project_index(state_path)
        projects = index.get("projects")
        project = projects.get(project_id) if isinstance(projects, dict) else None
        if project_id is not None and not isinstance(project, dict):
            raise ManagedRequestError("Unknown project.")

        assignments = state.get("thread-project-assignments")
        if not isinstance(assignments, dict):
            assignments = {}
            state["thread-project-assignments"] = assignments
        projectless = state.get("projectless-thread-ids")
        if not isinstance(projectless, list):
            projectless = []
        projectless = [str(value) for value in projectless if str(value) != thread_id]

        if project_id is None:
            assignments.pop(thread_id, None)
            projectless.append(thread_id)
        else:
            assignments[thread_id] = {
                "projectKind": "local",
                "projectId": project_id,
                "cwd": project.get("path"),
            }
        state["projectless-thread-ids"] = projectless

        try:
            temporary = state_path.with_name(
                f".{state_path.name}.mobile-codex-{secrets.token_hex(6)}"
            )
            original_mode = state_path.stat().st_mode & 0o777
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.chmod(temporary, original_mode)
            os.replace(temporary, state_path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise AppServerError("Unable to update Codex project state.") from error
        return project


def summarize_thread(
    thread: dict[str, Any],
    project_index: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    name = thread.get("name")
    preview = thread.get("preview")
    title = name if isinstance(name, str) and name.strip() else preview
    cwd = _bounded_text(thread.get("cwd"), 1_000)
    thread_id = str(thread.get("id", ""))
    project = None
    if isinstance(project_index, dict):
        assignments = project_index.get("assignments")
        projects = project_index.get("projects")
        if isinstance(assignments, dict) and isinstance(projects, dict):
            project_id = assignments.get(thread_id)
            candidate = projects.get(project_id)
            if not isinstance(candidate, dict) and cwd:
                try:
                    normalized_cwd = os.path.normcase(os.path.realpath(cwd))
                except (OSError, TypeError, ValueError):
                    normalized_cwd = ""
                matches = []
                for candidate_id, value in projects.items():
                    if not isinstance(value, dict) or not isinstance(value.get("path"), str):
                        continue
                    try:
                        normalized_root = os.path.normcase(
                            os.path.realpath(value["path"])
                        )
                        inside_root = os.path.commonpath(
                            [normalized_cwd, normalized_root]
                        ) == normalized_root
                    except (OSError, TypeError, ValueError):
                        continue
                    if normalized_cwd and normalized_root and inside_root:
                        matches.append((len(normalized_root), str(candidate_id), value))
                if matches:
                    _, project_id, candidate = max(matches, key=lambda match: match[0])
            if isinstance(candidate, dict):
                project = {
                    "id": str(candidate.get("id", project_id)),
                    "name": _bounded_text(candidate.get("name"), 240),
                    "path": _bounded_text(candidate.get("path"), 1_000) or None,
                    "order": candidate.get("order", 0),
                }
    activity_snapshot = _rollout_activity_snapshot(thread)
    pinned_thread_ids = (
        project_index.get("pinnedThreadIds", set())
        if isinstance(project_index, dict)
        else set()
    )
    unread_thread_ids = (
        project_index.get("unreadThreadIds", set())
        if isinstance(project_index, dict)
        else set()
    )
    return {
        "id": thread_id,
        "title": _bounded_text(title or "未命名任务", 240),
        "preview": _bounded_text(preview, 500),
        "cwd": cwd or None,
        "collection": "project" if project is not None else "recent",
        "project": project,
        "createdAt": thread.get("createdAt"),
        "updatedAt": thread.get("updatedAt"),
        # Current Codex Desktop persists pins in global UI state, while some
        # app-server builds also expose isPinned directly. Accept both.
        "isPinned": bool(
            thread.get("isPinned", False)
            or thread_id in pinned_thread_ids
        ),
        "isUnread": bool(thread_id in unread_thread_ids),
        "status": _status(thread.get("status")),
        "activityStatus": activity_snapshot["status"],
        "source": thread.get("source"),
    }


def _user_message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    ]
    text = "\n".join(parts)
    request_marker = "## My request for Codex:\n"
    if text.lstrip().startswith("# Files mentioned by the user:") and request_marker in text:
        text = text.split(request_marker, 1)[1]
    return _bounded_text(text.strip(), 20_000)


def _user_message_attachments(item: dict[str, Any]) -> list[dict[str, Any]]:
    content = item.get("content")
    if not isinstance(content, list):
        return []
    attachments = []
    for part in content:
        if (
            not isinstance(part, dict)
            or part.get("type") != "localImage"
            or not isinstance(part.get("path"), str)
        ):
            continue
        name = Path(part["path"]).name
        attachments.append(
            {
                "type": "image",
                "name": _bounded_text(name or "图片", 240),
                "path": _bounded_text(part["path"], 2_000),
            }
        )
    return attachments[:4]


def _safe_item(item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    base = {"id": str(item.get("id", "")), "type": item_type}
    if item_type == "userMessage":
        base["text"] = _user_message_text(item)
        base["attachments"] = _user_message_attachments(item)
        return base
    if item_type == "agentMessage":
        base["text"] = _bounded_text(item.get("text"), 30_000)
        phase = item.get("phase")
        base["phase"] = (
            "final_answer"
            if phase in {"final_answer", "finalAnswer", "final"}
            else phase
        )
        return base
    if item_type == "plan":
        base["text"] = _bounded_text(item.get("text"), 12_000)
        return base
    if item_type == "commandExecution":
        base["command"] = _bounded_text(item.get("command"), 2_000)
        base["status"] = item.get("status")
        base["output"] = _bounded_text(item.get("aggregatedOutput"), 12_000)
        base["exitCode"] = item.get("exitCode")
        return base
    if item_type == "fileChange":
        changes = item.get("changes")
        base["status"] = item.get("status")
        base["changes"] = [
            {
                "path": _bounded_text(change.get("path"), 1_000),
                "kind": (
                    change.get("kind", {}).get("type", "change")
                    if isinstance(change.get("kind"), dict)
                    else str(change.get("kind") or "change")
                ),
            }
            for change in changes[:100]
            if isinstance(change, dict)
        ] if isinstance(changes, list) else []
        return base
    if item_type == "contextCompaction":
        base["label"] = "Context compacted"
        return base
    if item_type in {"mcpToolCall", "dynamicToolCall", "collabToolCall", "webSearch"}:
        base["status"] = item.get("status")
        arguments = item.get("arguments")
        result = item.get("result")
        metadata = result.get("_meta") if isinstance(result, dict) else None
        if (
            item_type == "mcpToolCall"
            and isinstance(metadata, dict)
            and metadata.get("codex/browserUse") is True
        ):
            base["activityKind"] = "browser"
        base["label"] = _bounded_text(
            (
                arguments.get("title")
                if isinstance(arguments, dict) and isinstance(arguments.get("title"), str)
                else None
            ) or item.get("tool") or item.get("query") or item.get("server"),
            1_000,
        )
        return base
    return None


def summarize_thread_detail(
    thread: dict[str, Any],
    project_index: Optional[dict[str, Any]] = None,
    max_turns: int = 60,
) -> dict[str, Any]:
    turns = thread.get("turns")
    rollout_snapshot = _rollout_activity_snapshot(thread)
    rollout_activities = rollout_snapshot["turns"]
    safe_turns = []
    turn_limit = min(60, max(1, max_turns))
    if isinstance(turns, list):
        for turn in turns[-turn_limit:]:
            if not isinstance(turn, dict):
                continue
            turn_id = str(turn.get("id", ""))
            turn_is_active = turn_id == str(rollout_snapshot["activeTurnId"] or "")
            items = turn.get("items")
            safe_items = []
            if isinstance(items, list):
                for item in items[:200]:
                    safe_item = _safe_item(item)
                    if safe_item is not None:
                        if (
                            safe_item.get("type") == "userMessage"
                            and turn.get("startedAt") is not None
                        ):
                            safe_item["timestamp"] = turn.get("startedAt")
                        safe_items.append(safe_item)
            activity_items = [
                {
                    "id": f"rollout-{turn.get('id', '')}-{kind}",
                    "type": "desktopActivity",
                    "activityKind": kind,
                    "count": count,
                }
                for kind, count in rollout_activities.get(
                    turn_id,
                    {},
                ).items()
                if count > 0
            ]
            if (
                turn_is_active
                and not activity_items
                and not any(
                    item.get("type") != "userMessage"
                    for item in safe_items
                )
            ):
                activity_items.append(
                    {
                        "id": f"rollout-{turn_id}-working",
                        "type": "desktopActivity",
                        "activityKind": "working",
                        "count": 1,
                    }
                )
            final_agent_index = next(
                (
                    index
                    for index in range(len(safe_items) - 1, -1, -1)
                    if safe_items[index].get("type") == "agentMessage"
                ),
                len(safe_items),
            )
            safe_items[final_agent_index:final_agent_index] = activity_items
            if not turn_is_active and str(turn.get("status", "")) == "completed":
                for safe_item in reversed(safe_items):
                    if safe_item.get("type") == "agentMessage":
                        safe_item["phase"] = "final_answer"
                        if turn.get("completedAt") is not None:
                            safe_item["timestamp"] = turn.get("completedAt")
                        break
            safe_turns.append(
                {
                    "id": turn_id,
                    "status": "inProgress" if turn_is_active else turn.get("status"),
                    "startedAt": turn.get("startedAt"),
                    "completedAt": None if turn_is_active else turn.get("completedAt"),
                    "error": (
                        {"message": _bounded_text(turn["error"].get("message"), 2_000)}
                        if isinstance(turn.get("error"), dict)
                        else None
                    ),
                    "items": safe_items,
                }
            )
    active_turn_id = str(rollout_snapshot["activeTurnId"] or "")
    if active_turn_id and not any(turn["id"] == active_turn_id for turn in safe_turns):
        active_items = [
            {
                "id": f"rollout-{active_turn_id}-{kind}",
                "type": "desktopActivity",
                "activityKind": kind,
                "count": count,
            }
            for kind, count in rollout_activities.get(active_turn_id, {}).items()
            if count > 0
        ]
        if not active_items:
            active_items.append(
                {
                    "id": f"rollout-{active_turn_id}-working",
                    "type": "desktopActivity",
                    "activityKind": "working",
                    "count": 1,
                }
            )
        safe_turns.append(
            {
                "id": active_turn_id,
                "status": "inProgress",
                "startedAt": rollout_snapshot["activeStartedAt"],
                "completedAt": None,
                "error": None,
                "items": active_items,
            }
        )
    result = summarize_thread(thread, project_index)
    result["turns"] = safe_turns
    result["historyLimit"] = turn_limit
    result["totalTurns"] = len(turns) if isinstance(turns, list) else 0
    result["historyTruncated"] = (
        isinstance(turns, list) and len(turns) > turn_limit
    )
    return result
