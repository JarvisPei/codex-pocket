"""Opt-in native resume, bound to an interrupted turn and a durable request ID.

Uses only the Desktop Continue button. Never submits synthetic prompt text or
starts/resumes work through app-server. Ambiguous clicks are never replayed.
"""
import time

from platforms.windows.delivery import DeliveryJournal, validate_delivery
from platforms.windows.stop import latest_turn, validate_stop_payload


class NativeResumeController:
    def __init__(self, native, *, dispatch=None, attempts=12):
        self.native = native
        self.journal = DeliveryJournal(native.data / 'native-resume.sqlite')
        self.dispatch = dispatch or self._dispatch
        self.attempts = attempts

    def available(self):
        try:
            from platforms.windows.desktop_helper import request_action
            s = request_action(self.native.data / 'desktop-diagnostics', self.native.token, 'status')
            return s.get('reachable') is True and s.get('nativeResumeEnabled') is True
        except (OSError, ValueError, RuntimeError):
            return False

    def _dispatch(self, phase, payload):
        from platforms.windows.desktop_helper import request_action
        root = self.native.data / 'desktop-diagnostics'
        for _ in range(15):
            state = request_action(root, self.native.token, 'status')
            if not state.get('reachable') or not state.get('nativeResumeEnabled'):
                return {'status': 'refused', 'reason': 'native_resume_not_enabled'}
            if state.get('status') == 'ready':
                break
            time.sleep(.1)
        else:
            return {'status': 'refused', 'reason': 'native_helper_busy'}
        return request_action(root, self.native.token, 'native-resume-' + phase, payload).get(
            'nativeResume', {'status': 'uncertain'})

    def resume(self, request_id, thread_id, turn_id):
        if not isinstance(turn_id, str) or not 0 < len(turn_id) <= 200:
            raise ValueError('invalid_resume_turn')
        validate_delivery(request_id, thread_id, 'resume:' + turn_id)
        with self.native.lock:
            before = self.native._read(thread_id)
            baseline = {t['id'] for t in before.get('turns', []) if isinstance(t, dict) and isinstance(t.get('id'), str)}
            claim = self.journal.claim(request_id, thread_id, 'resume:' + turn_id, baseline)
            if claim['state'] == 'confirmed':
                return {'ok': True, 'mode': 'desktop', 'desktop': {
                    'ok': True, 'confirmedBy': 'threadHistory', 'duplicateRequest': True}}
            if claim['state'] == 'refused':
                return {'ok': False, 'error': 'native_resume_refused', 'retryAllowed': True}
            if claim['dispatch']:
                try:
                    title = before.get('name')
                    current = latest_turn(before)
                    if not current or current['id'] != turn_id or current.get('status') != 'interrupted':
                        raise ValueError('not_interrupted')
                    payload = {'threadId': thread_id, 'expectedTitle': title, 'guard': None}
                    validate_stop_payload(payload, commit=False)
                    if not self.native._unique_title(thread_id, title):
                        raise ValueError('ambiguous_title')
                    prepared = self.dispatch('prepare', payload)
                    if prepared.get('status') != 'prepared':
                        raise ValueError('prepare_refused')
                    fresh = self.native._read(thread_id)
                    current = latest_turn(fresh)
                    if (not current or current['id'] != turn_id or current.get('status') != 'interrupted'
                            or fresh.get('name') != title):
                        raise ValueError('turn_changed')
                    payload['guard'] = prepared.get('guard')
                    validate_stop_payload(payload, commit=True)
                except Exception:
                    self.journal.finish(request_id, 'refused')
                    return {'ok': False, 'error': 'native_resume_preflight_refused', 'retryAllowed': True}
                try:
                    result = self.dispatch('commit', payload)
                except Exception:
                    result = {'status': 'uncertain'}
                if result.get('status') == 'refused':
                    self.journal.finish(request_id, 'refused')
                    return {'ok': False, 'error': 'native_resume_refused', 'retryAllowed': True}
            for attempt in range(self.attempts):
                try:
                    after = self.native._read(thread_id)
                    current = latest_turn(after)
                    if (current and (current['id'] == turn_id or current['id'] not in claim['baseline'])
                            and current.get('status') in {'inProgress', 'completed'}):
                        self.journal.finish(request_id, 'confirmed', current['id'])
                        return {'ok': True, 'mode': 'desktop', 'desktop': {
                            'ok': True, 'taskTitle': after.get('name', ''), 'confirmedBy': 'threadHistory'}}
                except Exception:
                    pass
                if attempt + 1 < self.attempts:
                    time.sleep(.25)
            if claim['state'] == 'claimed':
                self.journal.finish(request_id, 'uncertain')
            return {'ok': False, 'error': 'native_resume_uncertain', 'retryAllowed': False}
