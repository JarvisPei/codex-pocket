"""Native turn delivery; optional empty-thread creation never starts a backend turn."""
from __future__ import annotations

from pathlib import Path
import threading
import time

from codex_app_server import AppServerError
from mac_bridge import BridgeHandler, thread_turn_is_active
from windows_delivery import DeliveryJournal, UUID, user_receipts, validate_delivery
from windows_thread_identity import unique_empty_thread_name


class NativeTextController:
    def __init__(self, client, data: Path, token: str, *, dispatch=None, attempts=32):
        self.client, self.data, self.token = client, data, token
        self.journal = DeliveryJournal(data / "native-delivery.sqlite")
        self.lock = threading.Lock()
        self.dispatch = dispatch or self._dispatch
        self.attempts = attempts

    def _dispatch(self, payload):
        # Lazy import prevents a cycle with the Windows entry point.
        from windows_desktop_helper import request_action
        root = self.data / "desktop-diagnostics"
        state = request_action(root, self.token, "status")
        if not state.get("reachable") or not state.get("nativeSendEnabled"):
            return {"status": "refused", "reason": "native_helper_unavailable"}
        return request_action(root, self.token, "native-send", payload).get("nativeSend", {"status": "uncertain"})

    def _read(self, thread_id):
        thread = self.client.read_thread(thread_id).get("thread")
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise AppServerError("Invalid target thread")
        return thread

    def _unique_title(self, thread_id, title):
        cursor, seen, found = None, set(), False
        for _ in range(20):
            result = self.client.request("thread/list", {
                "limit": 100, "sortKey": "updated_at", "sortDirection": "desc",
                "useStateDbOnly": True, "archived": False, **({"cursor": cursor} if cursor else {}),
            })
            if not isinstance(result.get("data"), list):
                return False
            for candidate in result["data"]:
                if not isinstance(candidate, dict):
                    return False
                name = candidate.get("name") or candidate.get("preview")
                if name == title:
                    if candidate.get("id") != thread_id:
                        return False
                    found = True
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                # Desktop 26.915 omits persisted tasks without a user event.
                # Retain full list collision checks, then require exact local
                # empty-task metadata instead of blindly accepting a missing ID.
                return found or unique_empty_thread_name(thread_id, title)
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                return False
            seen.add(next_cursor)
            cursor = next_cursor
        return False  # No guessing if the complete title namespace is too large.

    def deliver(self, request_id, thread_id, message, *, receipt_only=False):
        validate_delivery(request_id, thread_id, message)
        with self.lock:
            if receipt_only:
                claim = self.journal.lookup(request_id, thread_id, message)
                if claim is None:
                    return {'ok': False, 'error': 'native_receipt_pending', 'retryAllowed': False}
            else:
                before = self._read(thread_id)
                claim = self.journal.claim(request_id, thread_id, message, user_receipts(before, thread_id, message))
            if claim["state"] == "confirmed":
                return {"ok": True, "mode": "desktop", "desktop": {"ok": True, "confirmedBy": "threadHistory", "duplicateRequest": True}}
            if claim["state"] == "refused":
                return {"ok": False, "error": "native_request_refused", "retryAllowed": True}
            if claim["dispatch"]:
                title = before.get("name")
                try:
                    if thread_turn_is_active(before):
                        reason = "desktop_turn_active"
                    elif not isinstance(title, str) or not title or not self._unique_title(thread_id, title):
                        reason = "task_identity_mismatch"
                    else:
                        reason = None
                    if reason:
                        self.journal.finish(request_id, "refused")
                        return {"ok": False, "error": reason, "retryAllowed": True}
                except Exception:
                    # No UI write has been requested yet.
                    self.journal.finish(request_id, "refused")
                    return {"ok": False, "error": "native_preflight_unavailable", "retryAllowed": True}
                try:
                    result = self.dispatch({"threadId": thread_id, "expectedTitle": title, "message": message})
                except Exception:
                    result = {"status": "uncertain"}
                if result.get("status") == "refused":
                    self.journal.finish(request_id, "refused")
                    return {"ok": False, "error": "native_" + str(result.get("reason", "refused")), "retryAllowed": True}
            # A repeated ID only reconciles; it can never issue another UI write.
            attempts = 1 if receipt_only else self.attempts
            for attempt in range(attempts):
                try:
                    after = self._read(thread_id)
                    fresh = user_receipts(after, thread_id, message) - claim["baseline"]
                    if len(fresh) == 1:
                        self.journal.finish(request_id, "confirmed", next(iter(fresh)))
                        return {"ok": True, "mode": "desktop", "desktop": {
                            "ok": True, "taskTitle": after.get("name", ""), "confirmedBy": "threadHistory"}}
                except AppServerError:
                    pass
                if attempt + 1 < attempts:
                    time.sleep(0.25)
            if claim["state"] == "claimed":
                self.journal.finish(request_id, "uncertain")
            return {"ok": False, "error": "native_delivery_uncertain", "retryAllowed": False}


class WindowsNativeHandler(BridgeHandler):
    def do_GET(self):
        if self.path.split('?')[0] == '/api/desktop/interrupt/status':
            if not self._require_control_auth():
                return
            controller = self.server.native_stop_controller
            available = controller.available()
            resume = getattr(self.server, 'native_resume_controller', None)
            create = getattr(self.server, 'native_create_controller', None)
            self._send_json(200, {'ok': True, 'taskTitle': '', 'stopCandidates': 0,
                'interruptible': available, 'request': None,
                'capabilities': {**self.server.runtime.capabilities(), 'nativeStop': available,
                    'nativeResume': bool(resume and resume.available()),
                    'newTasks': bool(create and create.available())}})
            return
        return super().do_GET()

    def do_POST(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        target = len(parts) == 5 and parts[:3] == ["api", "codex", "threads"] and parts[4] in {"turn", "continue"}
        creating = parts == ["api", "codex", "threads"]
        stopping = parts == ['api', 'desktop', 'interrupt']
        if not target and not creating and not stopping:
            return super().do_POST()
        previous_close = self.close_connection
        self.close_connection = True  # Include Connection: close in an auth refusal.
        if not self._require_control_auth():
            # Windows can reset the socket (losing the 401) if unread POST data
            # remains at close. Drain only a small, bounded body, never parse it.
            previous_timeout = self.connection.gettimeout()
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 65536 and not self.headers.get("Transfer-Encoding"):
                    self.connection.settimeout(1)
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            finally:
                self.connection.settimeout(previous_timeout)
            return
        self.close_connection = previous_close
        payload = self._read_json()
        if payload is None:
            return
        receipt_only = payload.get('receiptOnly', False)
        if not isinstance(receipt_only, bool) or (receipt_only and not (creating or (target and parts[-1] == 'turn'))):
            self._send_json(400, {'ok': False, 'error': 'invalid_receipt_request'})
            return
        file_handoff = None
        # Resolve IDs with device ownership before any creation or UI operation.
        # Client-supplied paths are never accepted. Only this opt-in route turns
        # uploaded files into an ordinary, receipted native text message.
        if (creating or (target and parts[-1] == 'turn')) and payload.get('attachmentIds'):
            if not self.server.runtime.attachment_paths:
                self._send_json(501, {'ok': False, 'error': 'attachments_unsupported', 'retryAllowed': True})
                return
            ok, attachments = self._resolve_attachments(payload)
            if not ok:
                return
            try:
                from windows_attachments import file_message
                # Validate before retaining a file or allocating a task. Empty
                # text is allowed only because a nonempty file message follows.
                original = payload.get('message', '')
                probe = file_message(original, ['local-file'])
                if creating:
                    from windows_create import creation_input
                    creation_input({**payload, 'message': probe, 'attachmentIds': []})
                else:
                    validate_delivery(payload.get('requestId'), parts[3], probe)
                paths = self.server.attachment_store.handoff(attachments)
                if creating:
                    # Allocation chooses/verifies the final cwd before copying.
                    # Keep the retained-path message as the stable create claim.
                    file_handoff = (original, paths)
                else:
                    thread = self.server.native_text_controller._read(parts[3])
                    paths = self.server.attachment_store.workspace_handoff(paths, thread.get('cwd'))
                payload = {**payload, 'message': file_message(payload.get('message', ''), paths), 'attachmentIds': []}
            except (ValueError, OSError, KeyError, AppServerError):
                self._send_json(409, {'ok': False, 'error': 'attachment_handoff_failed', 'retryAllowed': False})
                return
        create = getattr(self.server, 'native_create_controller', None)
        if creating and create is not None:
            try:
                result = create.create(payload, **({'file_handoff': file_handoff} if file_handoff else {}),
                                       **({'receipt_only': True} if receipt_only else {}))
                self._send_json(202 if result.get('ok') else 409, result)
            except ValueError as error:
                known = str(error) if str(error) in {
                    'invalid_delivery_id', 'invalid_delivery_message', 'invalid_project',
                    'invalid_model_settings', 'attachments_unsupported',
                    'delivery_id_reused_with_different_content', 'delivery_confirmation_pending',
                } else 'native_create_uncertain'
                self._send_json(409, {'ok': False, 'error': known, 'retryAllowed': False})
            except Exception:
                self._send_json(502, {'ok': False, 'error': 'native_create_uncertain', 'retryAllowed': False})
            return
        if stopping:
            if payload.get('confirm') is not True:
                self._send_json(400, {'ok': False, 'error': 'confirmation_required'})
                return
            try:
                result = self.server.native_stop_controller.stop(payload.get('threadId'),
                    payload.get('expectedTurnId'), payload.get('expectedTaskTitle'))
            except ValueError:
                self._send_json(400, {'ok': False, 'error': 'invalid_stop_target'})
                return
            except Exception:
                self._send_json(409, {'ok': False, 'error': 'native_stop_uncertain'})
                return
            self.server.invalidate_thread_detail(payload.get('threadId'))
            self._send_json(200 if result.get('ok') else 409, result)
            return
        if not creating and parts[-1] == 'continue' and getattr(self.server, 'native_resume_controller', None):
            if payload.get('attachmentIds') or payload.get('message'):
                self._send_json(400, {'ok': False, 'error': 'resume_requires_empty_composer', 'retryAllowed': True})
                return
            try:
                result = self.server.native_resume_controller.resume(payload.get('requestId'), parts[3], payload.get('expectedTurnId'))
                if result.get('ok'):
                    self.server.invalidate_thread_detail(parts[3])
                self._send_json(202 if result.get('ok') else 409, result)
            except ValueError:
                self._send_json(409, {'ok': False, 'error': 'native_resume_invalid_or_pending', 'retryAllowed': False})
            except Exception:
                self._send_json(502, {'ok': False, 'error': 'native_resume_uncertain', 'retryAllowed': False})
            return
        if creating or parts[-1] == "continue":
            self._send_json(501, {"ok": False, "error": "native_preview_existing_text_only", "retryAllowed": True})
            return
        thread_id = parts[3]
        if payload.get("attachmentIds"):
            self._send_json(501, {"ok": False, "error": "attachments_unsupported", "retryAllowed": True})
            return
        try:
            if not UUID.fullmatch(thread_id):
                raise ValueError("invalid_delivery_id")
            result = self.server.native_text_controller.deliver(payload.get("requestId"), thread_id, payload.get("message"),
                                                               **({'receipt_only': True} if receipt_only else {}))
        except ValueError as error:
            known = str(error) if str(error) in {
                "invalid_delivery_id", "invalid_delivery_message", "delivery_id_reused_with_different_content",
                "delivery_confirmation_pending",
            } else "native_delivery_uncertain"
            self._send_json(409, {"ok": False, "error": known, "retryAllowed": False})
            return
        except Exception:
            self._send_json(502, {"ok": False, "error": "native_delivery_uncertain", "retryAllowed": False})
            return
        if result.get("ok"):
            self.server.invalidate_thread_detail(thread_id)
        self._send_json(202 if result.get("ok") else 409, result)
