import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from bridge_runtime import WINDOWS_NATIVE_TEXT
from codex_app_server import load_codex_project_index, summarize_thread
from windows_bridge import parse_args, make_server
from windows_create import NativeCreateController, creation_input
from windows_native import NativeTextController, WindowsNativeHandler
import test_mac_bridge as fixtures


class NativeCreationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'state.json'
        self.state.write_text(json.dumps({'local-projects': {
            'project-1': {'name': 'Example', 'rootPaths': [str(self.project)]},
        }}), encoding='utf-8')
        self.threads = {}
        self.client = Mock()
        self.client.read_thread.side_effect = lambda tid: {'thread': copy.deepcopy(self.threads[tid])}
        self.client.request.side_effect = lambda method, params: {
            'data': list(self.threads.values()), 'nextCursor': None,
        }
        self.client.create_thread.side_effect = self.allocate
        self.dispatch = Mock(side_effect=self.send)
        self.native = NativeTextController(self.client, self.root, 'secret', dispatch=self.dispatch, attempts=1)
        self.server = SimpleNamespace(native_text_controller=self.native,
            projectless_root=self.root / 'Recents', codex_state_path=self.state,
            project_index=lambda: load_codex_project_index(self.state), invalidate_thread_detail=Mock())
        self.controller = NativeCreateController(self.server)
        self.controller.available = Mock(return_value=True)
        self.payload = {'requestId': str(uuid.uuid4()), 'projectId': 'project-1', 'message': 'Task text'}

    def allocate(self, title, cwd, **settings):
        tid = str(uuid.uuid4())
        thread = {'id': tid, 'name': title, 'cwd': cwd, 'turns': [], 'status': {'type': 'idle'}}
        self.threads[tid] = thread
        return {'thread': copy.deepcopy(thread), 'settings': settings}

    def send(self, payload):
        self.threads[payload['threadId']]['turns'] = [{
            'id': str(uuid.uuid4()), 'status': 'inProgress', 'items': [{
                'id': str(uuid.uuid4()), 'type': 'userMessage',
                'content': [{'type': 'text', 'text': payload['message']}],
            }],
        }]
        return {'status': 'invoke_requested'}

    def test_project_allocation_assignment_then_native_delivery(self):
        result = self.controller.create(self.payload)
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'desktop')
        self.assertEqual(result['thread']['project']['id'], 'project-1')
        self.assertEqual(self.client.create_thread.call_args.kwargs['cwd'], str(self.project))
        self.assertEqual(self.server.project_index()['assignments'][result['threadId']], 'project-1')
        self.client.start_turn.assert_not_called()
        self.client.continue_turn.assert_not_called()
        self.dispatch.assert_called_once()

    def test_receipt_only_cannot_allocate_unknown_creation(self):
        result = self.controller.create(self.payload, receipt_only=True)
        self.assertEqual(result['error'], 'native_receipt_pending')
        self.client.create_thread.assert_not_called()
        self.dispatch.assert_not_called()
        self.assertTrue(self.controller.create(self.payload)['ok'])

    def test_receipt_only_reconciles_known_creation_without_second_send(self):
        self.dispatch.side_effect = lambda _: {'status': 'invoke_requested'}
        first = self.controller.create(self.payload)
        self.assertFalse(first['ok'])
        self.send({'threadId': first['threadId'], 'message': self.payload['message']})
        second = self.controller.create(self.payload, receipt_only=True)
        self.assertTrue(second['ok'])
        self.assertEqual(second['threadId'], first['threadId'])
        self.client.create_thread.assert_called_once()
        self.dispatch.assert_called_once()

    def test_modern_creation_assigns_in_start_and_never_writes_legacy(self):
        original = self.state.read_bytes()
        def index():
            return {**load_codex_project_index(self.state), 'projectBackend': 'app-server',
                    'authoritativeProjects': True,
                    'assignments': {tid: 'project-1' for tid in self.threads}}
        self.server.project_index = index
        with patch('windows_create.assign_codex_thread_collection') as legacy:
            first = self.controller.create(self.payload)
            self.assertTrue(first['ok'])
            self.assertTrue(self.controller.create(self.payload)['ok'])
            legacy.assert_not_called()
        self.assertEqual(self.client.create_thread.call_args.kwargs['project_id'], 'project-1')
        self.assertEqual(self.client.create_thread.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)
        self.assertEqual(self.state.read_bytes(), original)

    def test_modern_assignment_missing_refuses_delivery_without_reallocation(self):
        self.server.project_index = lambda: {**load_codex_project_index(self.state),
            'projectBackend': 'app-server', 'authoritativeProjects': True}
        for _ in range(2):
            self.assertEqual(self.controller.create(self.payload)['error'], 'project_assignment_failed')
        self.dispatch.assert_not_called()
        self.client.create_thread.assert_called_once()

    def test_recents_stays_projectless_even_under_a_saved_project_root(self):
        self.server.projectless_root = self.project / 'Recents'
        result = self.controller.create({**self.payload, 'projectId': None})
        self.assertTrue(result['ok'])
        self.assertEqual(result['thread']['collection'], 'recent')
        self.assertIsNone(result['thread']['project'])
        tid = result['threadId']
        index = self.server.project_index()
        self.assertIn(tid, index['projectlessThreadIds'])
        self.assertNotIn(tid, index['assignments'])
        self.assertIsNone(summarize_thread(self.threads[tid], index)['project'])

    def test_recents_replay_checks_modern_metadata_if_legacy_flag_disappears(self):
        payload = {**self.payload, 'projectId': None}
        first = self.controller.create(payload)
        state = json.loads(self.state.read_text())
        state['projectless-thread-ids'] = []
        self.state.write_text(json.dumps(state))
        with patch('windows_create.recent_assignment_matches', return_value=False):
            self.assertEqual(self.controller.create(payload)['error'], 'project_assignment_failed')
        with patch('windows_create.recent_assignment_matches', return_value=True) as current:
            replay = self.controller.create(payload)
            self.assertTrue(replay['desktop']['duplicateRequest'])
            current.assert_called_once_with(first['threadId'], self.threads[first['threadId']]['cwd'])
        self.assertEqual(self.client.create_thread.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)
        self.assertEqual(json.loads(self.state.read_text())['projectless-thread-ids'], [])

    def test_replay_after_restart_neither_allocates_nor_sends_twice(self):
        first = self.controller.create(self.payload)
        again = NativeCreateController(self.server).create(self.payload)
        self.assertTrue(again['ok'])
        self.assertTrue(again['desktop']['duplicateRequest'])
        self.assertEqual(first['threadId'], again['threadId'])
        self.assertEqual(self.client.create_thread.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)

    def test_new_task_file_handoff_uses_verified_cwd_and_replays(self):
        from windows_attachments import file_message
        original, paths = 'Read uploaded file', ['C:/private/retained.txt']
        self.server.attachment_store = Mock()
        copied = [(self.project / '.codex-pocket-attachments' / 'retained.txt').as_posix()]
        self.server.attachment_store.workspace_handoff.return_value = copied
        payload = {**self.payload, 'message': file_message(original, paths)}
        first = self.controller.create(payload, file_handoff=(original, paths))
        self.assertTrue(first['ok'])
        self.server.attachment_store.workspace_handoff.assert_called_with(paths, str(self.project))
        self.assertEqual(self.dispatch.call_args.args[0]['message'], file_message(original, copied))
        again = self.controller.create(payload, file_handoff=(original, paths))
        self.assertTrue(again['desktop']['duplicateRequest'])
        self.assertEqual(self.client.create_thread.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)

    def test_file_handoff_failure_preserves_created_thread(self):
        from windows_attachments import file_message
        original, paths = 'Read file', ['C:/private/retained.txt']
        self.server.attachment_store = Mock()
        self.server.attachment_store.workspace_handoff.side_effect = OSError('private path')
        payload = {**self.payload, 'message': file_message(original, paths)}
        result = self.controller.create(payload, file_handoff=(original, paths))
        self.assertEqual(result['error'], 'attachment_handoff_failed')
        self.assertTrue(result['threadCreated'])
        self.dispatch.assert_not_called()
        self.controller.create(payload, file_handoff=(original, paths))
        self.assertEqual(self.client.create_thread.call_count, 1)

    def test_modern_project_multiple_files_receipt_poll_preserves_one_allocation(self):
        from windows_attachments import file_message
        original = 'Read both files'
        paths = ['C:/private/first.txt', 'C:/private/second.json']
        copied = [(self.project / '.codex-pocket-attachments' / name).as_posix()
                  for name in ('first.txt', 'second.json')]
        self.server.attachment_store = Mock()
        self.server.attachment_store.workspace_handoff.return_value = copied
        self.server.project_index = lambda: {**load_codex_project_index(self.state),
            'projectBackend': 'app-server', 'authoritativeProjects': True,
            'assignments': {tid: 'project-1' for tid in self.threads}}
        payload = {**self.payload, 'message': file_message(original, paths)}
        self.dispatch.side_effect = lambda _: {'status': 'invoke_requested'}
        first = self.controller.create(payload, file_handoff=(original, paths))
        self.assertFalse(first['ok'])
        self.send({'threadId': first['threadId'], 'message': file_message(original, copied)})
        again = self.controller.create(payload, file_handoff=(original, paths), receipt_only=True)
        self.assertTrue(again['ok'])
        self.assertEqual(again['thread']['project']['id'], 'project-1')
        self.assertEqual(again['threadId'], first['threadId'])
        self.assertEqual(self.client.create_thread.call_args.kwargs['project_id'], 'project-1')
        self.dispatch.assert_called_once()
        self.assertEqual(self.dispatch.call_args.args[0]['message'], file_message(original, copied))
        self.client.create_thread.assert_called_once()

    def test_unknown_allocation_blocks_same_and_new_ids(self):
        self.client.create_thread.side_effect = TimeoutError()
        result = self.controller.create(self.payload)
        self.assertEqual(result['error'], 'native_create_uncertain')
        self.assertFalse(result['retryAllowed'])
        self.assertFalse(self.controller.create(self.payload)['ok'])
        with self.assertRaisesRegex(ValueError, 'confirmation_pending'):
            self.controller.create({**self.payload, 'requestId': str(uuid.uuid4())})
        self.client.create_thread.assert_called_once()
        self.dispatch.assert_not_called()

    def test_same_id_cannot_change_destination_message_or_settings(self):
        self.controller.create(self.payload)
        for changes in ({'projectId': None}, {'message': 'Other'}, {'model': 'x', 'effort': 'low'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'different_content'):
                self.controller.create({**self.payload, **changes})
        self.assertEqual(self.client.create_thread.call_count, 1)

    def test_helper_or_invalid_project_prevents_allocation(self):
        self.controller.available.return_value = False
        self.assertTrue(self.controller.create(self.payload)['retryAllowed'])
        self.client.create_thread.assert_not_called()
        self.controller.available.return_value = True
        result = self.controller.create({**self.payload, 'requestId': str(uuid.uuid4()), 'projectId': 'missing'})
        self.assertEqual(result['error'], 'invalid_project')
        self.client.create_thread.assert_not_called()

    def test_missing_project_directory_refuses(self):
        self.project.rmdir()
        self.assertEqual(self.controller.create(self.payload)['error'], 'project_path_missing')
        self.client.create_thread.assert_not_called()

    def test_duplicate_title_gets_suffix_without_touching_existing_task(self):
        existing = self.allocate('Task text', str(self.project))['thread']['id']
        self.threads[existing]['status'] = {'type': 'active'}
        result = self.controller.create(self.payload)
        self.assertTrue(result['ok'])
        self.assertEqual(result['thread']['title'], 'Task text (2)')
        self.assertEqual(self.threads[existing]['status']['type'], 'active')

    def test_assignment_failure_returns_empty_task_never_recreates(self):
        with patch('windows_create.assign_codex_thread_collection', side_effect=OSError()):
            result = self.controller.create(self.payload)
        self.assertTrue(result['threadCreated'])
        self.assertEqual(result['error'], 'project_assignment_failed')
        self.dispatch.assert_not_called()
        replay = self.controller.create(self.payload)
        self.assertEqual(replay['threadId'], result['threadId'])
        self.assertEqual(replay['error'], 'project_assignment_failed')
        self.assertEqual(self.client.create_thread.call_count, 1)

    def test_existing_draft_failure_keeps_known_thread(self):
        self.dispatch.side_effect = lambda _: {'status': 'refused', 'reason': 'desktop_draft_present'}
        result = self.controller.create(self.payload)
        self.assertFalse(result['ok'])
        self.assertTrue(result['threadCreated'])
        self.assertEqual(result['error'], 'native_desktop_draft_present')

    def test_late_native_receipt_reconciles_without_reallocation_or_second_send(self):
        self.dispatch.side_effect = TimeoutError()
        first = self.controller.create(self.payload)
        self.assertEqual(first['error'], 'native_delivery_uncertain')
        self.send({'threadId': first['threadId'], 'message': self.payload['message']})
        self.assertTrue(self.controller.create(self.payload)['ok'])
        self.assertEqual(self.client.create_thread.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)

    def test_replay_does_not_overwrite_user_moved_project(self):
        result = self.controller.create(self.payload)
        from codex_app_server import assign_codex_thread_collection
        assign_codex_thread_collection(self.state, result['threadId'], None)
        self.assertEqual(self.controller.create(self.payload)['error'], 'project_assignment_failed')
        self.assertIn(result['threadId'], self.server.project_index()['projectlessThreadIds'])

    def test_invalid_inputs_and_attachments_do_not_allocate(self):
        for change in ({'requestId': 'bad'}, {'message': ' '}, {'message': 'a\0b'},
                       {'model': 'x'}, {'projectId': []}, {'attachmentIds': ['x']}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.controller.create({**self.payload, **change})
        self.client.create_thread.assert_not_called()

    def test_no_prompt_in_creation_journal(self):
        self.controller.create(self.payload)
        self.assertNotIn(self.payload['message'].encode(), (self.root / 'native-create.sqlite').read_bytes())

    def test_feature_is_explicit_and_requires_native_send(self):
        self.assertFalse(WINDOWS_NATIVE_TEXT.capabilities()['newTasks'])
        self.assertTrue(replace(WINDOWS_NATIVE_TEXT, native_new_tasks=True).capabilities()['newTasks'])
        with patch('sys.stderr', new=io.StringIO()), self.assertRaises(SystemExit):
            parse_args(['serve', '--native-new-tasks'])
        with self.assertRaises(ValueError):
            make_server(Path('unused.exe'), 'unused', 0, native_new_tasks=True)


class NativeCreationApiTest(unittest.TestCase):
    request = fixtures.BridgeApiTest.request
    tearDown = fixtures.BridgeApiTest.tearDown

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        self.server.RequestHandlerClass = WindowsNativeHandler
        self.server.runtime = WINDOWS_NATIVE_TEXT
        self.server.native_create_controller = Mock()
        self.server.native_create_controller.create.return_value = {'ok': True, 'mode': 'desktop'}

    def test_authorized_creation_uses_only_create_controller(self):
        payload = {'requestId': str(uuid.uuid4()), 'message': 'new', 'projectId': None}
        status, result = self.request('POST', '/api/codex/threads', payload)
        self.assertEqual(status, 202)
        self.assertEqual(result['mode'], 'desktop')
        self.server.native_create_controller.create.assert_called_once_with(payload)
        self.assertEqual(self.app_server.started, [])

    def test_unauthorized_creation_never_reaches_controller(self):
        status, _ = self.request('POST', '/api/codex/threads', {}, authorized=False)
        self.assertEqual(status, 401)
        self.server.native_create_controller.create.assert_not_called()

    def test_unknown_failure_and_bad_replay_do_not_claim_success(self):
        self.server.native_create_controller.create.side_effect = TimeoutError()
        status, result = self.request('POST', '/api/codex/threads', {})
        self.assertEqual(status, 502)
        self.assertFalse(result['retryAllowed'])
        self.server.native_create_controller.create.side_effect = ValueError('delivery_confirmation_pending')
        status, result = self.request('POST', '/api/codex/threads', {})
        self.assertEqual(status, 409)
        self.assertEqual(result['error'], 'delivery_confirmation_pending')
