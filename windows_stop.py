"""Opt-in native Stop. Navigate/prepare, re-read turn, then commit once.

No background turn/interrupt calls. A durable claim keyed by thread+turn prevents
replaying an uncertain click, even with a different browser request or restart.
UIA identity remains a compatibility guard, not an atomic Desktop turn API.
"""
from __future__ import annotations

import time
import uuid

from windows_delivery import DeliveryJournal, UUID
from codex_app_server import _rollout_activity_snapshot


def validate_stop_payload(data, *, commit):
    if not isinstance(data, dict) or set(data) != {"threadId", "expectedTitle", "guard"}:
        raise ValueError("invalid_stop_payload")
    if not isinstance(data["threadId"], str) or not UUID.fullmatch(data["threadId"]):
        raise ValueError("invalid_stop_payload")
    if not isinstance(data["expectedTitle"], str) or not 0 < len(data["expectedTitle"]) <= 1000:
        raise ValueError("invalid_stop_payload")
    g = data["guard"]
    if not commit:
        if g is not None:
            raise ValueError("invalid_stop_guard")
        return
    if not isinstance(g, dict) or set(g) != {"buttonId", "documentId", "issuedAt"}:
        raise ValueError("invalid_stop_guard")
    for key in ("buttonId", "documentId"):
        if (not isinstance(g[key], list) or not 1 <= len(g[key]) <= 32
                or any(type(n) is not int or not -(2**31) <= n < 2**31 for n in g[key])):
            raise ValueError("invalid_stop_guard")
    if type(g["issuedAt"]) is not int or not 0 <= time.time() * 1000 - g["issuedAt"] <= 8000:
        raise ValueError("stop_guard_expired")


def latest_turn(thread):
    turns = thread.get("turns")
    if not isinstance(turns, list) or not turns or not isinstance(turns[-1], dict):
        return None
    turn = turns[-1]
    if not isinstance(turn.get('id'), str) or not turn['id']:
        return None
    if thread.get('path'):
        snapshot = _rollout_activity_snapshot(thread)
        active = snapshot.get('activeTurnId')
        # A separate app-server reader often reconstructs Desktop-owned work
        # as interrupted/notLoaded. Use actual persisted lifecycle events.
        if active:
            if active != turn['id']:
                return None
            return {**turn, 'status': 'inProgress'}
        return {**turn, 'status': snapshot.get('status') or 'unknown'}
    return turn


class NativeStopController:
    def __init__(self, native, *, dispatch=None, attempts=12):
        self.native = native
        self.journal = DeliveryJournal(native.data / 'native-stop.sqlite')
        self.dispatch = dispatch or self._dispatch
        self.attempts = attempts

    def available(self):
        try:
            from windows_desktop_helper import request_action
            state = request_action(self.native.data / 'desktop-diagnostics', self.native.token, 'status')
            return state.get('reachable') is True and state.get('nativeStopEnabled') is True
        except (OSError, ValueError, RuntimeError):
            return False

    def _dispatch(self, phase, payload):
        from windows_desktop_helper import request_action
        root = self.native.data / 'desktop-diagnostics'
        # The response may be visible a fraction before the worker publishes
        # ready. Wait without queuing a second action into a busy mailbox.
        for _ in range(15):
            state = request_action(root, self.native.token, 'status')
            if not state.get('reachable') or not state.get('nativeStopEnabled'):
                return {'status': 'refused', 'reason': 'native_stop_not_enabled'}
            if state.get('status') == 'ready':
                break
            time.sleep(0.1)
        else:
            return {'status': 'refused', 'reason': 'native_helper_busy'}
        return request_action(root, self.native.token,
            'native-stop-' + phase, payload).get('nativeStop', {'status': 'uncertain'})

    def stop(self, thread_id, turn_id, expected_title):
        validate_stop_payload({'threadId': thread_id, 'expectedTitle': expected_title, 'guard': None}, commit=False)
        if not isinstance(turn_id, str) or not 0 < len(turn_id) <= 200:
            raise ValueError('invalid_stop_turn')
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'pocket-stop:' + thread_id + ':' + turn_id))
        with self.native.lock:
            before = self.native._read(thread_id)
            turn = latest_turn(before)
            if not turn or turn['id'] != turn_id:
                return {'ok': False, 'error': 'native_stop_turn_changed'}
            if turn.get('status') in {'completed', 'interrupted', 'failed'}:
                claim = self.journal.claim(request_id, thread_id, turn_id, set())
                if claim['state'] in {'claimed', 'uncertain'}:
                    self.journal.finish(request_id, 'confirmed', turn_id)
                return {'ok': True, 'interrupted': False, 'alreadyFinished': True}
            if turn.get('status') != 'inProgress' or before.get('name') != expected_title:
                return {'ok': False, 'error': 'native_stop_state_unverified'}
            claim = self.journal.claim(request_id, thread_id, turn_id, set())
            if claim['state'] == 'refused':
                return {'ok': False, 'error': 'native_stop_refused'}
            if claim['dispatch']:
                try:
                    if not self.native._unique_title(thread_id, expected_title):
                        raise ValueError('task_identity_mismatch')
                    payload = {'threadId': thread_id, 'expectedTitle': expected_title, 'guard': None}
                    prepared = self.dispatch('prepare', payload)
                    if prepared.get('status') != 'prepared':
                        raise ValueError('native_stop_prepare_refused')
                    # Recheck AFTER navigation. Never use the phone's cached state
                    # or a stop button alone to authorize ending another turn.
                    fresh = self.native._read(thread_id)
                    current = latest_turn(fresh)
                    if (not current or current['id'] != turn_id or current.get('status') != 'inProgress'
                            or fresh.get('name') != expected_title):
                        raise ValueError('native_stop_turn_changed')
                    payload['guard'] = prepared.get('guard')
                    validate_stop_payload(payload, commit=True)
                except Exception:
                    self.journal.finish(request_id, 'refused')
                    return {'ok': False, 'error': 'native_stop_preflight_refused'}
                try:
                    result = self.dispatch('commit', payload)
                except Exception:
                    result = {'status': 'uncertain'}
                if result.get('status') == 'refused':
                    self.journal.finish(request_id, 'refused')
                    return {'ok': False, 'error': 'native_stop_refused'}
            for attempt in range(self.attempts):
                try:
                    after = self.native._read(thread_id)
                    target = latest_turn(after)
                    if target and target['id'] != turn_id:
                        target = None
                    if target and target.get('status') in {'completed', 'interrupted', 'failed'}:
                        if claim['state'] != 'confirmed':
                            self.journal.finish(request_id, 'confirmed', turn_id)
                        return {'ok': True, 'interrupted': target['status'] == 'interrupted',
                                'alreadyFinished': target['status'] != 'interrupted', 'confirmedBy': 'threadHistory'}
                except Exception:
                    pass
                if attempt + 1 < self.attempts:
                    time.sleep(0.25)
            if claim['state'] == 'claimed':
                self.journal.finish(request_id, 'uncertain')
            return {'ok': False, 'error': 'native_stop_uncertain'}
