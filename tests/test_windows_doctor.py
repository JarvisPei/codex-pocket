import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

from windows_bridge import doctor_report, main, parse_args, print_doctor_report


class WindowsDoctorTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.data = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.data / 'token.dpapi').touch()
        self.stack.enter_context(patch('windows_bridge.state_directory', return_value=self.data))
        self.binary = self.stack.enter_context(patch('windows_bridge.codex_binary_path', return_value=Path('codex.exe')))
        self.client_type = self.stack.enter_context(patch('windows_bridge.CodexAppServerClient'))
        self.client = self.client_type.return_value
        self.token = self.stack.enter_context(patch('windows_bridge.windows_token', return_value='SECRET-DO-NOT-PRINT'))
        self.helper = self.stack.enter_context(patch('windows_desktop_helper.request_action', return_value={
            'reachable': True, 'sessionId': 1, 'status': 'ready',
            'nativeSendEnabled': True, 'nativeStopEnabled': True,
        }))
        self.http = self.stack.enter_context(patch('windows_bridge.local_request', return_value={
            'devices': [],
            'capabilities': {'platform': 'windows', 'nativeTextSend': True},
            'taskTitle': 'PRIVATE-TITLE',
        }))

    def check(self, report, name):
        return next(c for c in report['checks'] if c['id'] == name)

    def run_native(self):
        return doctor_report('native.exe', 4317, native=True)

    def test_protocol_mode_does_not_touch_credentials_helper_or_bridge(self):
        report = doctor_report('native.exe', 4317)
        self.assertTrue(report['ok'])
        self.client.start.assert_called_once_with()
        self.client.close.assert_called_once_with()
        self.client.request.assert_not_called()
        self.token.assert_not_called()
        self.helper.assert_not_called()
        self.http.assert_not_called()

    def test_ready_native_checks_only_status_and_never_initializes_secret(self):
        report = self.run_native()
        self.assertTrue(report['ok'])
        self.token.assert_called_once_with()
        self.helper.assert_called_once_with(self.data / 'desktop-diagnostics', 'SECRET-DO-NOT-PRINT', 'status')
        self.assertEqual(self.http.call_args_list, [
            call(4317, 'SECRET-DO-NOT-PRINT', '/api/devices'),
            call(4317, 'SECRET-DO-NOT-PRINT', '/health'),
        ])
        self.client.request.assert_not_called()
        self.assertNotIn('SECRET-DO-NOT-PRINT', json.dumps(report))
        self.assertNotIn('PRIVATE-TITLE', json.dumps(report))

    def test_invalid_binary_does_not_start_backend(self):
        self.binary.side_effect = RuntimeError('private path')
        report = doctor_report(None, 4317)
        self.assertFalse(report['ok'])
        self.assertEqual(self.check(report, 'protocol')['status'], 'skipped')
        self.client_type.assert_not_called()
        self.assertNotIn('private path', json.dumps(report))

    def test_failed_handshake_closes_backend_and_redacts_exception(self):
        self.client.start.side_effect = RuntimeError('SECRET-DO-NOT-PRINT')
        report = self.run_native()
        self.assertFalse(report['ok'])
        self.client.close.assert_called_once_with()
        self.assertNotIn('SECRET-DO-NOT-PRINT', json.dumps(report))

    def test_uninitialized_credential_is_not_created(self):
        (self.data / 'token.dpapi').unlink()
        report = self.run_native()
        self.assertFalse(report['ok'])
        self.assertIn('init', self.check(report, 'credential')['action'])
        self.token.assert_not_called()
        self.helper.assert_not_called()
        self.http.assert_not_called()
        self.assertFalse((self.data / 'token.dpapi').exists())

    def test_dpapi_failure_skips_authenticated_checks_and_keeps_secret(self):
        self.token.side_effect = RuntimeError('SECRET-DO-NOT-PRINT')
        report = self.run_native()
        self.assertFalse(report['ok'])
        self.assertIn('SSH', self.check(report, 'credential')['action'])
        self.assertEqual(self.check(report, 'helper')['status'], 'skipped')
        self.helper.assert_not_called()
        self.http.assert_not_called()
        self.assertTrue((self.data / 'token.dpapi').exists())
        self.assertNotIn('SECRET-DO-NOT-PRINT', json.dumps(report))

    def test_absent_stale_noninteractive_or_unenabled_helper_fails(self):
        for state in ({}, {'reachable': False}, {'reachable': True, 'sessionId': 0},
                      {'reachable': True, 'sessionId': 1, 'nativeSendEnabled': False}, None):
            with self.subTest(state=state):
                self.helper.return_value = state
                report = self.run_native()
                self.assertFalse(report['ok'])
                self.assertEqual(self.check(report, 'helper')['status'], 'failed')

    def test_busy_stop_disabled_and_experimental_resume_are_warnings(self):
        self.helper.return_value.update(status='busy', nativeStopEnabled=False, nativeResumeEnabled=True)
        report = self.run_native()
        self.assertTrue(report['ok'])
        for name in ('helper', 'native_stop', 'native_resume'):
            self.assertEqual(self.check(report, name)['status'], 'warning')

    def test_helper_auth_failure_is_redacted(self):
        self.helper.side_effect = ValueError('SECRET-DO-NOT-PRINT')
        report = self.run_native()
        self.assertFalse(report['ok'])
        self.assertNotIn('SECRET-DO-NOT-PRINT', json.dumps(report))

    def test_wrong_mode_and_invalid_bridge_reply_are_not_ready(self):
        for result in ({'capabilities': {'platform': 'windows'}},
                       {'capabilities': {'platform': 'macos', 'nativeTextSend': True}},
                       {}, [], {'capabilities': None}):
            with self.subTest(result=result):
                self.http.side_effect = [{'devices': []}, result]
                self.assertEqual(self.check(self.run_native(), 'bridge')['status'], 'failed')

    def test_paired_master_never_uses_device_control_route(self):
        def paired_route(port, token, path):
            if path == '/api/devices':
                return {'devices': [{'name': 'PRIVATE-DEVICE'}]}
            if path == '/health':
                return {'capabilities': {'platform': 'windows', 'nativeTextSend': True}}
            raise OSError('Master control disabled after enrollment')
        self.http.side_effect = paired_route
        report = self.run_native()
        self.assertTrue(report['ok'])
        self.assertNotIn('PRIVATE-DEVICE', json.dumps(report))

    def test_bad_admin_reply_does_not_fall_back_to_public_health(self):
        self.http.return_value = {'devices': None}
        self.assertFalse(self.run_native()['ok'])
        self.http.assert_called_once_with(4317, 'SECRET-DO-NOT-PRINT', '/api/devices')

    def test_unreachable_bridge_is_redacted(self):
        self.http.side_effect = OSError('SECRET-DO-NOT-PRINT')
        report = self.run_native()
        self.assertFalse(report['ok'])
        self.assertNotIn('SECRET-DO-NOT-PRINT', json.dumps(report))

    def test_json_output_and_exit_status(self):
        args = parse_args(['doctor', '--native', '--json', '--port', '4321'])
        self.assertTrue(args.native and args.as_json)
        for fails in (False, True):
            self.binary.side_effect = RuntimeError('invalid') if fails else None
            output = io.StringIO()
            with patch('windows_bridge.sys.platform', 'win32'), contextlib.redirect_stdout(output):
                code = main(['doctor', '--codex-binary', 'native.exe', '--native', '--json'])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 1 if fails else 0)
            self.assertEqual(report['ok'], not fails)

    def test_readable_output_contains_next_action(self):
        self.binary.side_effect = RuntimeError('invalid')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_doctor_report(doctor_report(None, 4317))
        self.assertIn('[FAILED] binary', output.getvalue())
        self.assertIn('Next:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
