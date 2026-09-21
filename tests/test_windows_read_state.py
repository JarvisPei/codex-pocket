import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from codex_app_server import AppServerError, summarize_thread
from windows_read_state import (STATE_KEY, context_from_auth, identity_key, scope_key,
                                read_state, unread_index, mark_read, notify_desktop)

THREAD = '33333333-3333-4333-8333-333333333333'  # Synthetic fixture.


def auth(account='one', user='user', exp=None):
    claims = {'exp': exp or time.time() + 3600, 'https://api.openai.com/auth': {
        'chatgpt_account_id': account, 'user_id': user}}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return {'authMethod': 'chatgpt', 'authToken': 'test.' + encoded + '.test'}


class WindowsReadStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.auth = auth()
        self.context = context_from_auth(self.auth)
        self.client = Mock()
        def request(method, params, **kwargs):
            if method == 'getAuthStatus': return self.auth
            if method == 'thread/read': return {'thread': {'id': THREAD, 'updatedAt': 123}}
            raise AssertionError(method)
        self.client.request.side_effect = request
        self.data = {STATE_KEY: {'version': 1, 'unreadByIdentity': {
            identity_key(self.context): {self.context['executionHostKey']: [THREAD], 'ssh:other': ['remote']},
            'another-account': {self.context['executionHostKey']: ['other-account-task']},
        }}, 'unrelated': {'keep': True}}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data), encoding='utf8')

    def payload(self):
        return {'scope': scope_key(self.context), 'revision': '123'}

    def test_reads_only_current_account_and_local_host_without_writes(self):
        before = self.path.read_bytes()
        index = unread_index(self.path, self.client)
        self.assertEqual(index['unreadThreadIds'], {THREAD})
        self.assertTrue(summarize_thread({'id': THREAD}, index)['isUnread'])
        self.assertTrue(index['readStateAuthoritative'])
        self.assertTrue(index['readStateAvailable'])
        self.assertNotIn('authToken', str(index))
        self.assertEqual(before, self.path.read_bytes())

    def test_unknown_account_fails_closed_not_union_or_legacy(self):
        self.auth = auth('new-login')
        self.data['electron-persisted-atom-state'] = {'unread-thread-ids-by-host-v1': {'local': [THREAD]}}
        self.save()
        index = unread_index(self.path, self.client)
        self.assertFalse(index['readStateAvailable'])
        self.assertEqual(index['unreadThreadIds'], set())

    def test_unknown_host_and_future_schema_are_unavailable(self):
        del self.data[STATE_KEY]['unreadByIdentity'][identity_key(self.context)][self.context['executionHostKey']]
        self.save()
        self.assertFalse(unread_index(self.path, self.client)['readStateAvailable'])
        self.data[STATE_KEY]['version'] = 2
        self.save()
        self.assertFalse(unread_index(self.path, self.client)['readStateAvailable'])

    def test_auth_errors_do_not_expose_token(self):
        for bad in ({}, auth(exp=1), {'authMethod': 'chatgpt', 'authToken': 'secret-invalid'}):
            with self.assertRaises(AppServerError) as e: context_from_auth(bad)
            self.assertNotIn('secret', str(e.exception))

    def test_legacy_missing_key_leaves_old_reader_in_charge(self):
        self.data.pop(STATE_KEY)
        self.save()
        self.assertEqual(unread_index(self.path, self.client), {})
        self.assertFalse(mark_read(self.path, self.client, 'legacy-id', {}))
        self.client.request.assert_not_called()

    def test_acknowledge_requires_desktop_persistence(self):
        before = self.path.read_bytes()
        send = Mock()
        with self.assertRaises(AppServerError):
            mark_read(self.path, self.client, THREAD, self.payload(), notify=send, attempts=0)
        send.assert_called_once_with(THREAD, self.context)
        self.assertEqual(before, self.path.read_bytes())

    def test_acknowledge_verifies_desktop_change_and_preserves_other_buckets(self):
        def desktop(tid, context):
            self.data[STATE_KEY]['unreadByIdentity'][identity_key(context)][context['executionHostKey']] = []
            self.save()
        self.assertTrue(mark_read(self.path, self.client, THREAD, self.payload(), notify=desktop))
        self.assertEqual(self.data[STATE_KEY]['unreadByIdentity']['another-account'][self.context['executionHostKey']], ['other-account-task'])
        self.assertTrue(self.data['unrelated']['keep'])

    def test_stale_revision_or_identity_never_notifies(self):
        for payload in ({'scope': 'old', 'revision': '123'}, {'scope': scope_key(self.context), 'revision': '122'}, {}):
            send = Mock()
            with self.assertRaises(AppServerError):
                mark_read(self.path, self.client, THREAD, payload, notify=send)
            send.assert_not_called()

    def test_account_switch_during_ack_is_not_success(self):
        def switch(*args): self.auth = auth('new-login')
        with self.assertRaises(AppServerError):
            mark_read(self.path, self.client, THREAD, self.payload(), notify=switch)

    def test_already_read_is_idempotent_without_broadcast(self):
        self.data[STATE_KEY]['unreadByIdentity'][identity_key(self.context)][self.context['executionHostKey']] = []
        self.save()
        send = Mock()
        self.assertTrue(mark_read(self.path, self.client, THREAD, self.payload(), notify=send))
        send.assert_not_called()

    def test_subprocess_payload_has_no_bearer_and_output_is_not_relayed(self):
        with patch('windows_read_state.subprocess.run', return_value=Mock(returncode=0, stdout='{"sent":true}')) as run:
            notify_desktop(THREAD, self.context)
            self.assertNotIn(self.auth['authToken'], run.call_args.kwargs['input'])
            self.assertNotIn(THREAD, str(run.call_args.args))
        with patch('windows_read_state.subprocess.run', return_value=Mock(returncode=1, stdout='secret')):
            with self.assertRaises(AppServerError) as e: notify_desktop(THREAD, self.context)
            self.assertNotIn('secret', str(e.exception))
