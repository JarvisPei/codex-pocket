import io
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from tests import test_mac_bridge as fixtures
from bridge_runtime import WINDOWS_NATIVE_TEXT, WINDOWS_PREVIEW
from platforms.windows.attachments import WindowsAttachmentStore, file_message, safe_path
from platforms.windows.bridge import parse_args, make_server
from platforms.windows.native import WindowsNativeHandler


class FileHandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = WindowsAttachmentStore(self.root / 'private' / 'uploads')

    def upload(self, name='图片.png', device='owner', data=b'example'):
        return self.store.create(device, name, 'application/octet-stream', io.BytesIO(data), len(data))

    def test_windows_names_refuse_devices_ads_metadata_and_traversal(self):
        for name in ('CON', 'nul.txt', 'COM1.png', 'lpt².txt', 'bad:name.png', 'x.',
                     'bad?.png', '../x', 'a\\b', 'metadata.json', 'MetaData.JSON'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.upload(name)
        self.assertFalse(self.store.root.exists())

    def test_windows_directories_inherit_without_special_0700_dacl(self):
        mkdir = Path.mkdir
        modes = []
        def track(path, mode=0o777, parents=False, exist_ok=False):
            modes.append((path, mode))
            return mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        with patch('platforms.windows.attachments.sys.platform', 'win32'), patch.object(Path, 'mkdir', track):
            item = self.upload('note.txt')
            self.store.handoff(self.store.resolve([item['id']], 'owner'))
        for path, mode in modes:
            self.assertNotEqual(mode, 0o700, str(path))
        self.assertIn(self.store.root, [path for path, _ in modes])
        self.assertIn(self.store.root / item['id'], [path for path, _ in modes])
        self.assertIn(self.store.root.parent / 'attachment-handoffs', [path for path, _ in modes])

    def test_unicode_spaces_and_multiple_files_retained_after_upload_deletion(self):
        a, b = self.upload('中文 空格.jpg'), self.upload('notes.txt')
        items = self.store.resolve([a['id'], b['id']], 'owner')
        paths = self.store.handoff(items)
        self.assertEqual(paths, self.store.handoff(items))
        self.assertEqual(len(paths), 2)
        self.assertTrue(paths[0].endswith('.jpg'))
        for item, path in zip((a, b), paths):
            self.assertTrue(self.store.delete(item['id'], 'owner'))
            self.assertEqual(Path(path).read_bytes(), b'example')

    def test_ownership_duplicates_and_missing_ids(self):
        a = self.upload()
        with self.assertRaises(PermissionError):
            self.store.resolve([a['id']], 'other')
        with self.assertRaises(ValueError):
            self.store.resolve([a['id'], a['id']], 'owner')
        with self.assertRaises(ValueError):
            self.store.resolve(['a' * 24], 'owner')

    def test_snapshot_tampering_is_not_silently_reused(self):
        a = self.upload()
        items = self.store.resolve([a['id']], 'owner')
        path = Path(self.store.handoff(items)[0])
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'snapshot_changed'):
            self.store.handoff(items)

    def test_workspace_copy_retains_content_and_is_idempotent(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        a = self.upload('中文 文件.txt')
        retained = self.store.handoff(self.store.resolve([a['id']], 'owner'))
        paths = self.store.workspace_handoff(retained, str(workspace))
        self.assertEqual(paths, self.store.workspace_handoff(retained, str(workspace)))
        self.assertEqual(Path(paths[0]).parent, workspace / '.codex-pocket-attachments')
        self.assertEqual(Path(paths[0]).read_bytes(), b'example')
        self.store.delete(a['id'], 'owner')
        self.assertEqual(Path(paths[0]).read_bytes(), b'example')
        Path(paths[0]).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'workspace_copy_changed'):
            self.store.workspace_handoff(retained, str(workspace))
        self.assertEqual(Path(paths[0]).read_bytes(), b'changed')

    def test_workspace_rejects_unknown_cwd_private_root_and_untrusted_source(self):
        a = self.upload()
        retained = self.store.handoff(self.store.resolve([a['id']], 'owner'))
        for cwd in (None, '', 'relative', str(self.root / 'missing'), str(self.root / 'private')):
            with self.subTest(cwd=cwd), self.assertRaises(ValueError):
                self.store.workspace_handoff(retained, cwd)
        workspace = self.root / 'workspace'
        workspace.mkdir()
        outside = self.root / Path(retained[0]).name
        outside.write_bytes(b'example')
        with self.assertRaisesRegex(ValueError, 'snapshot_changed'):
            self.store.workspace_handoff([str(outside)], str(workspace))
        Path(retained[0]).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'snapshot_changed'):
            self.store.workspace_handoff(retained, str(workspace))

    def test_workspace_reparse_directory_is_refused(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        a = self.upload()
        retained = self.store.handoff(self.store.resolve([a['id']], 'owner'))
        with patch('platforms.windows.attachments.safe_path', side_effect=ValueError('attachment_reparse_point_refused')):
            with self.assertRaisesRegex(ValueError, 'reparse'):
                self.store.workspace_handoff(retained, str(workspace))
        self.assertFalse((workspace / '.codex-pocket-attachments').exists())

    def test_expired_upload_does_not_remove_submitted_snapshot(self):
        a = self.upload()
        path = Path(self.store.handoff(self.store.resolve([a['id']], 'owner'))[0])
        with patch('mac_bridge.time.time', return_value=a['expiresAt'] + 1):
            self.store.purge_expired()
        self.assertTrue(path.is_file())
        self.assertFalse((self.store.root / a['id']).exists())

    def test_symlink_and_reparse_ancestors_refused(self):
        with patch.object(Path, 'lstat') as lstat, patch.object(Path, 'exists', return_value=True):
            import stat
            lstat.return_value = type('Stat', (), {'st_mode': stat.S_IFDIR, 'st_file_attributes': 0x400})()
            with self.assertRaisesRegex(ValueError, 'reparse'):
                safe_path(self.root / 'junction' / 'file')

    def test_symlink_snapshot_refused(self):
        a = self.upload()
        items = self.store.resolve([a['id']], 'owner')
        path = Path(self.store.handoff(items)[0])
        path.unlink()
        target = self.root / 'outside'
        target.write_bytes(b'example')
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('symlink creation unavailable')
        with self.assertRaisesRegex(ValueError, 'reparse'):
            self.store.handoff(items)
        self.assertEqual(target.read_bytes(), b'example')

    def test_message_labels_paths_not_native_images_and_bounds(self):
        paths = ['C:/Users/Test/attachment-handoffs/file.png']
        self.assertIn('不是原生图片附件', file_message('查看图片', paths))
        self.assertIn(json.dumps(paths, ensure_ascii=False), file_message('', paths))
        self.assertEqual(file_message('plain', []), 'plain')
        for message in (None, 'x\0', 'a' * 20_000):
            with self.assertRaises(ValueError):
                file_message(message, paths)

    def test_feature_requires_native_opt_in(self):
        self.assertFalse(WINDOWS_NATIVE_TEXT.attachments)
        self.assertTrue(parse_args(['serve', '--native-text-send', '--attachment-paths']).attachment_paths)
        with self.assertRaises(SystemExit):
            parse_args(['serve', '--attachment-paths'])
        with self.assertRaises(ValueError):
            make_server(Path('unused.exe'), 'unused', 0, attachment_paths=True)
        with self.assertRaises(ValueError):
            replace(WINDOWS_PREVIEW, attachment_paths=True, attachments=True)


class FileHandoffApiTest(unittest.TestCase):
    request = fixtures.BridgeApiTest.request
    raw_request = fixtures.BridgeApiTest.raw_request
    tearDown = fixtures.BridgeApiTest.tearDown

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        self.server.RequestHandlerClass = WindowsNativeHandler
        self.server.runtime = replace(WINDOWS_NATIVE_TEXT, attachment_paths=True, attachments=True)
        self.server.attachment_store = WindowsAttachmentStore(self.server.attachment_store.root.resolve())
        self.server.native_text_controller = Mock()
        self.server.native_text_controller.deliver.return_value = {'ok': True, 'mode': 'desktop'}
        self.server.native_create_controller = Mock()
        self.server.native_create_controller.create.return_value = {'ok': True, 'mode': 'desktop'}
        self.device = self.server.device_registry.enroll('test', 'test')
        self.a = self.server.attachment_store.create(self.device['id'], 'image.png', 'image/png', io.BytesIO(b'image'), 5)
        self.payload = {'message': '看图', 'attachmentIds': [self.a['id']], 'requestId': str(uuid.uuid4())}
        self.tid = str(uuid.uuid4())
        self.workspace_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace_temp.cleanup)
        self.server.native_text_controller._read.return_value = {'id': self.tid, 'cwd': str(Path(self.workspace_temp.name).resolve())}
        self.url = '/api/codex/threads/' + self.tid + '/turn'

    def test_existing_and_new_task_receive_same_stable_explicit_file_message(self):
        status, _ = self.request('POST', self.url, self.payload, bearer=self.device['deviceToken'])
        self.assertEqual(status, 202)
        actual = self.server.native_text_controller.deliver.call_args.args
        self.assertEqual(actual[:2], (self.payload['requestId'], self.tid))
        self.assertIn('不是原生图片附件', actual[2])
        status, _ = self.request('POST', '/api/codex/threads', self.payload, bearer=self.device['deviceToken'])
        self.assertEqual(status, 202)
        created = self.server.native_create_controller.create.call_args.args[0]
        self.assertIn('attachment-handoffs', created['message'])
        self.assertIn('.codex-pocket-attachments', actual[2])
        handoff = self.server.native_create_controller.create.call_args.kwargs['file_handoff']
        self.assertEqual(handoff[0], self.payload['message'])
        self.assertIn('attachment-handoffs', handoff[1][0])
        self.assertEqual(created['attachmentIds'], [])
        self.assertEqual(self.app_server.started, [])

    def test_cross_device_or_master_without_device_cannot_handoff(self):
        other = self.server.device_registry.enroll('other', 'test')
        for token, expected in ((other['deviceToken'], 403), (None, 401)):
            status, _ = self.request('POST', self.url, self.payload, bearer=token)
            self.assertEqual(status, expected)
        self.server.native_text_controller.deliver.assert_not_called()
        self.assertFalse((self.server.attachment_store.root.parent / 'attachment-handoffs').exists())

    def test_disabled_or_unauthorized_never_handoffs(self):
        self.server.runtime = WINDOWS_NATIVE_TEXT
        status, _ = self.request('POST', self.url, self.payload, bearer=self.device['deviceToken'])
        self.assertEqual(status, 501)
        status, _ = self.request('POST', self.url, self.payload, authorized=False)
        self.assertEqual(status, 401)
        self.server.native_text_controller.deliver.assert_not_called()

    def test_missing_or_invalid_files_never_allocate_or_send(self):
        for ids in (['missing_attachment_12345'], [self.a['id'], self.a['id']], 'bad', [123]):
            status, _ = self.request('POST', '/api/codex/threads', {**self.payload, 'attachmentIds': ids}, bearer=self.device['deviceToken'])
            self.assertEqual(status, 400)
        self.server.native_create_controller.create.assert_not_called()

    def test_handoff_failure_is_sanitized_and_preserves_draft(self):
        with patch.object(self.server.attachment_store, 'handoff', side_effect=OSError('secret path')):
            status, result = self.request('POST', self.url, self.payload, bearer=self.device['deviceToken'])
        self.assertEqual(status, 409)
        self.assertEqual(result['error'], 'attachment_handoff_failed')
        self.assertNotIn('secret', json.dumps(result))
        self.assertFalse(result['retryAllowed'])
        self.server.native_text_controller.deliver.assert_not_called()

    def test_upload_http_requires_device_and_validates_windows_filename(self):
        from urllib.parse import quote
        status, result = self.raw_request('POST', '/api/attachments', b'example',
            headers={'X-Codex-Filename': quote('中文 照片.png'), 'Content-Type': 'image/png'},
            bearer=self.device['deviceToken'])
        self.assertEqual(status, 201)
        self.assertEqual(result['attachment']['name'], '中文 照片.png')
        self.assertNotIn('path', result['attachment'])
        status, _ = self.raw_request('POST', '/api/attachments', b'example',
            headers={'X-Codex-Filename': 'file:stream'}, bearer=self.device['deviceToken'])
        self.assertEqual(status, 400)

    def test_attachment_only_message_is_sent_as_explicit_file_request(self):
        status, _ = self.request('POST', self.url, {**self.payload, 'message': ''}, bearer=self.device['deviceToken'])
        self.assertEqual(status, 202)
        self.assertIn('请查看我上传的文件', self.server.native_text_controller.deliver.call_args.args[2])

    def test_invalid_delivery_does_not_retain_or_send(self):
        status, _ = self.request('POST', self.url, {**self.payload, 'requestId': 'invalid'}, bearer=self.device['deviceToken'])
        self.assertEqual(status, 409)
        self.assertFalse((self.server.attachment_store.root.parent / 'attachment-handoffs').exists())
        self.server.native_text_controller.deliver.assert_not_called()

    def test_http_cannot_select_attachment_destination(self):
        status, _ = self.request('POST', self.url, {**self.payload,
            'cwd': 'C:/untrusted', 'workspace': 'C:/untrusted',
            'file_handoff': ['injected', ['C:/secret']]}, bearer=self.device['deviceToken'])
        self.assertEqual(status, 202)
        message = self.server.native_text_controller.deliver.call_args.args[2]
        self.assertNotIn('untrusted', message)
        self.assertNotIn('secret', message)
        self.assertIn(Path(self.workspace_temp.name).resolve().as_posix(), message)

    def test_missing_verified_workspace_refuses_before_native_send(self):
        self.server.native_text_controller._read.return_value = {'id': self.tid}
        status, result = self.request('POST', self.url, self.payload, bearer=self.device['deviceToken'])
        self.assertEqual(status, 409)
        self.assertEqual(result['error'], 'attachment_handoff_failed')
        self.server.native_text_controller.deliver.assert_not_called()
