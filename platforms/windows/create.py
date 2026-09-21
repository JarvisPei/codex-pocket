"""Opt-in empty-thread creation followed only by native Desktop delivery.

Creation uses a short-lived app-server writer (no turn/start). The durable claim
precedes allocation; ambiguous allocation is never repeated, even after restart.
"""
import hashlib
import json
from pathlib import Path
import re
import threading

from codex_app_server import assign_codex_thread_collection, summarize_thread
from mac_bridge import create_projectless_workspace
from platforms.windows.delivery import DeliveryJournal, UUID, validate_delivery
from platforms.windows.thread_identity import recent_assignment_matches


CREATE_SCOPE = '00000000-0000-0000-0000-000000000001'


def creation_input(payload):
    if not isinstance(payload, dict):
        raise ValueError('invalid_create_request')
    rid, message, project = (payload.get(k) for k in ('requestId', 'message', 'projectId'))
    validate_delivery(rid, CREATE_SCOPE, message)
    if payload.get('attachmentIds'):
        raise ValueError('attachments_unsupported')
    if project is not None and (not isinstance(project, str) or not project or len(project) > 256):
        raise ValueError('invalid_project')
    settings = {k: payload.get(k) for k in ('model', 'effort', 'serviceTier')}
    if any(v is not None for v in settings.values()):
        if any(not isinstance(settings[k], str) or not settings[k] or len(settings[k]) > n
               for k, n in (('model', 100), ('effort', 40))):
            raise ValueError('invalid_model_settings')
        tier = settings['serviceTier']
        if tier is not None and (not isinstance(tier, str) or not tier or len(tier) > 40):
            raise ValueError('invalid_model_settings')
    canonical = json.dumps([project, message, settings], sort_keys=True, ensure_ascii=True)
    return rid, message, project, settings, hashlib.sha256(canonical.encode()).hexdigest()


class NativeCreateController:
    def __init__(self, server):
        self.server = server
        self.native = server.native_text_controller
        self.journal = DeliveryJournal(self.native.data / 'native-create.sqlite')
        self.lock = threading.Lock()

    def available(self):
        try:
            from platforms.windows.desktop_helper import request_action
            state = request_action(self.native.data / 'desktop-diagnostics', self.native.token, 'status')
            return (state.get('reachable') is True and state.get('nativeSendEnabled') is True
                    and state.get('status') == 'ready')
        except (OSError, RuntimeError, ValueError):
            return False

    def _title(self, message):
        # Native delivery requires a unique visible title. Never assume that a
        # repeated first message gives a unique task name.
        names, cursor, seen = set(), None, set()
        for _ in range(20):
            result = self.native.client.request('thread/list', {
                'limit': 100, 'sortKey': 'updated_at', 'sortDirection': 'desc',
                'useStateDbOnly': True, 'archived': False,
                **({'cursor': cursor} if cursor else {}),
            })
            rows = result.get('data')
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError('task_identity_mismatch')
            names.update(r.get('name') or r.get('preview') for r in rows
                         if isinstance(r.get('name') or r.get('preview'), str))
            cursor = result.get('nextCursor')
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError('task_identity_mismatch')
            seen.add(cursor)
        else:
            raise ValueError('task_identity_mismatch')
        base = re.sub(r'\s+', ' ', message.strip().splitlines()[0]).strip()[:80]
        for number in range(1, 2002):
            title = base if number == 1 else f'{base} ({number})'
            if title not in names:
                return title
        raise ValueError('task_identity_mismatch')

    def _known_failure(self, thread_id, error):
        return {'ok': False, 'error': error, 'threadCreated': True,
                'threadId': thread_id, 'retryAllowed': False}

    def create(self, payload, *, file_handoff=None, receipt_only=False):
        rid, message, project_id, settings, digest = creation_input(payload)
        if file_handoff is not None:
            from platforms.windows.attachments import file_message
            # Internal server context only, never an HTTP payload field.
            original, paths = file_handoff
            if file_message(original, paths) != message:
                raise ValueError('invalid_create_request')
        with self.lock:
            # A single namespace blocks another allocation after an unknown
            # outcome. Changing the request ID cannot create a duplicate.
            claim = (self.journal.lookup(rid, CREATE_SCOPE, digest) if receipt_only
                     else self.journal.claim(rid, CREATE_SCOPE, digest, set()))
            if claim is None:
                return {'ok': False, 'error': 'native_receipt_pending', 'retryAllowed': False}
            if claim['state'] == 'refused':
                return {'ok': False, 'error': 'native_create_refused', 'retryAllowed': True}
            if not claim['dispatch'] and claim['state'] != 'confirmed':
                return {'ok': False, 'error': 'native_create_uncertain', 'retryAllowed': False}
            if claim['dispatch']:
                try:
                    if not self.available():
                        raise ValueError('native_helper_unavailable')
                    index = self.server.project_index()
                    project = index.get('projects', {}).get(project_id)
                    if project_id is not None and not isinstance(project, dict):
                        raise ValueError('invalid_project')
                    cwd = project.get('path') if project_id is not None else None
                    if project_id is not None and (not isinstance(cwd, str) or
                            not Path(cwd).is_absolute() or not Path(cwd).is_dir()):
                        raise ValueError('project_path_missing')
                    title = self._title(message)
                    if project_id is None:
                        cwd = str(create_projectless_workspace(self.server.projectless_root, title))
                except Exception as error:
                    self.journal.finish(rid, 'refused')
                    known = str(error) if str(error) in {
                        'native_helper_unavailable', 'invalid_project', 'project_path_missing',
                        'task_identity_mismatch',
                    } else 'native_create_preflight_failed'
                    return {'ok': False, 'error': known, 'retryAllowed': True}
                try:
                    created = self.native.client.create_thread(
                        title=title, cwd=cwd, model=settings['model'], effort=settings['effort'],
                        service_tier=settings['serviceTier'],
                        **({'project_id': project_id} if index.get('projectBackend') == 'app-server'
                           and project_id is not None else {}),
                    )
                    thread_id = created['thread']['id']
                    if not isinstance(thread_id, str) or not UUID.fullmatch(thread_id):
                        raise ValueError('invalid_created_thread')
                    # Persist the known ID before touching sidebar metadata.
                    self.journal.finish(rid, 'confirmed', json.dumps({'id': thread_id, 'cwd': cwd}))
                except Exception:
                    # Allocation/name/materialization can fail after creating a
                    # task. Never call create_thread again on an ambiguous error.
                    self.journal.finish(rid, 'uncertain')
                    return {'ok': False, 'error': 'native_create_uncertain', 'retryAllowed': False}
                try:
                    if index.get('projectBackend') != 'app-server':
                        assign_codex_thread_collection(self.server.codex_state_path, thread_id, project_id)
                except Exception:
                    return self._known_failure(thread_id, 'project_assignment_failed')
            else:
                receipt = json.loads(claim['receipt'])
                thread_id, cwd = receipt['id'], receipt['cwd']
            try:
                thread = self.native._read(thread_id)
                index = self.server.project_index()
                summary = summarize_thread(thread, index)
                actual_project = (summary.get('project') or {}).get('id')
                assigned = (thread_id in index.get('projectlessThreadIds', set()) if project_id is None
                            else index.get('assignments', {}).get(thread_id) == project_id)
                if project_id is None and not assigned and actual_project is None:
                    # Modern Desktop may replace legacy JSON flags. Require its
                    # actual persisted unassigned project and exact cwd instead;
                    # never recreate flags or move a user-reassigned task.
                    assigned = recent_assignment_matches(thread_id, str(cwd))
                if not assigned or actual_project != project_id or Path(thread.get('cwd', '')).resolve() != Path(cwd).resolve():
                    return self._known_failure(thread_id, 'project_assignment_failed')
                if file_handoff is not None:
                    try:
                        copies = self.server.attachment_store.workspace_handoff(paths, str(cwd))
                        message = file_message(original, copies)
                    except (ValueError, OSError, KeyError):
                        return self._known_failure(thread_id, 'attachment_handoff_failed')
                # Do not reassign metadata on a replay: a user may have moved it.
                result = self.native.deliver(rid, thread_id, message,
                                             **({'receipt_only': True} if receipt_only else {}))
                self.server.invalidate_thread_detail(thread_id)
                return {**result, 'threadCreated': True, 'threadId': thread_id, 'thread': summary}
            except Exception:
                return self._known_failure(thread_id, 'native_delivery_uncertain')
