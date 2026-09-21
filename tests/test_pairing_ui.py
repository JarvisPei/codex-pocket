import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import test_mac_bridge as fixtures
from mac_bridge import PairingTicketStore
from pairing_ui import PairingDisplay, validate_origin, qr_matrix, origin_from_serve, discover_origin, wait_for_bridge, launch_pairing_display


class TicketTest(unittest.TestCase):
    def test_status_does_not_consume_and_used_is_distinct(self):
        store = PairingTicketStore()
        ticket = store.create()
        self.assertEqual(store.status(ticket)['status'], 'valid')
        self.assertTrue(store.consume(ticket))
        self.assertEqual(store.status(ticket)['status'], 'used')
        self.assertFalse(store.consume(ticket))

    def test_expired_unknown_and_revoked(self):
        store = PairingTicketStore(ttl_seconds=10)
        with patch('mac_bridge.time.monotonic', return_value=100): ticket = store.create()
        with patch('mac_bridge.time.monotonic', return_value=111):
            self.assertEqual(store.consume_status(ticket), 'expired')
        store.revoke(ticket)
        self.assertEqual(store.status(ticket)['status'], 'invalid')

    def test_only_one_concurrent_consumer(self):
        store = PairingTicketStore()
        ticket = store.create()
        results = []
        threads = [threading.Thread(target=lambda: results.append(store.consume(ticket))) for _ in range(12)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sum(results), 1)


class DisplayTest(unittest.TestCase):
    request = fixtures.BridgeApiTest.request

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        self.display = PairingDisplay('https://computer.test/', fixtures.TOKEN, self.server.server_port)
        self.worker = threading.Thread(target=self.display.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.display.shutdown(); self.display.server_close(); self.worker.join()
        fixtures.BridgeApiTest.tearDown(self)

    def display_request(self, path, *, capability=True, origin=None, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.display.server_port)
        headers = {'Origin': origin or self.display.origin, 'Content-Type': 'application/json'}
        if capability: headers['X-Pocket-Pairing'] = self.display.capability
        if host: headers['Host'] = host
        try:
            conn.request('POST', path, '{}', headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally: conn.close()

    def test_qr_issue_check_consume_regenerate(self):
        status, data = self.display_request('/new')
        self.assertEqual(status, 200)
        self.assertNotIn(fixtures.TOKEN, json.dumps(data))
        self.assertGreater(len(data['matrix']), 20)
        self.assertEqual(self.display_request('/status')[1]['status'], 'valid')
        _, second = self.display_request('/new')
        self.assertNotEqual(data['ticket'], second['ticket'])
        self.assertFalse(self.server.pairing_tickets.consume(data['ticket']))
        status, _ = self.request('POST', '/api/devices/enroll', {'name':'phone','pairingTicket':second['ticket']}, authorized=False)
        self.assertEqual(status, 201)
        self.assertEqual(self.display_request('/status')[1]['status'], 'used')

    def test_display_api_requires_local_host_origin_and_capability(self):
        for kwargs in ({'capability':False},{'origin':'https://evil.test'},{'host':'evil.test'}):
            self.assertEqual(self.display_request('/new', **kwargs)[0], 403)
        self.assertEqual(self.display_request('/unknown')[0], 404)
        self.assertEqual(self.display.ticket, '')

    def test_status_endpoint_requires_master(self):
        status, _ = self.request('POST','/api/devices/pairing-status',{'pairingTicket':'unknown'},authorized=False)
        self.assertEqual(status, 401)

    def test_first_install_wait_accepts_mac_health(self):
        wait_for_bridge(self.server.server_port, 0)

    def test_missing_tailscale_does_not_issue_ticket_then_retry_works(self):
        self.display.remote_origin = None
        with patch('pairing_ui.discover_origin', side_effect=RuntimeError('tailscale_not_ready')):
            status, data = self.display_request('/new')
        self.assertEqual(status, 503)
        self.assertEqual(data['error'], 'tailscale_not_ready')
        self.assertEqual(data['bridgePort'], self.server.server_port)
        self.assertEqual(self.display.ticket, '')
        with patch('pairing_ui.discover_origin', return_value='https://computer.test'):
            status, data = self.display_request('/new')
        self.assertEqual(status, 200)
        self.assertEqual(data['origin'], 'https://computer.test')

    def test_enrollment_reports_used_without_breaking_old_error_code(self):
        _, data = self.display_request('/new')
        self.server.pairing_tickets.consume(data['ticket'])
        status, result = self.request('POST','/api/devices/enroll',{'name':'phone','pairingTicket':data['ticket']},authorized=False)
        self.assertEqual(status, 401)
        self.assertEqual(result['error'], 'invalid_or_expired_pairing')
        self.assertEqual(result['pairingStatus'], 'used')


class OriginTest(unittest.TestCase):
    def config(self, proxy='http://127.0.0.1:4317'):
        return {'TCP': {'443': {'HTTPS': True}}, 'Web': {
            'computer.test:443': {'Handlers': {'/': {'Proxy': proxy}}}}}

    def test_discovery_requires_private_matching_https_root(self):
        self.assertEqual(origin_from_serve(self.config(), 4317), 'https://computer.test')
        cases = [self.config('http://127.0.0.1:9999'), self.config('http://evil.test:4317'), {}]
        funnel = self.config(); funnel['AllowFunnel'] = {'computer.test:443': True}; cases.append(funnel)
        no_https = self.config(); no_https['TCP'] = {}; cases.append(no_https)
        subpath = self.config(); subpath['Web']['computer.test:443']['Handlers'] = {'/pocket': {'Proxy': 'http://127.0.0.1:4317'}}; cases.append(subpath)
        multiple = self.config(); multiple['Web']['other.test:443'] = multiple['Web']['computer.test:443']; cases.append(multiple)
        for config in cases:
            with self.subTest(config=config), self.assertRaises(RuntimeError):
                origin_from_serve(config, 4317)

    def test_discovery_is_read_only(self):
        with patch('pairing_ui.shutil.which', return_value='/test/tailscale'), patch('pairing_ui.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stdout=json.dumps(self.config()))
            self.assertEqual(discover_origin(4317), 'https://computer.test')
            self.assertEqual(run.call_args.args[0], ['/test/tailscale', 'serve', 'status', '--json'])

    def test_origin_validation(self):
        self.assertEqual(validate_origin('https://computer.test/'), 'https://computer.test')
        for value in ['http://computer.test','https://user:pass@computer.test','https://computer.test/path','https://computer.test/#x','https://computer.test/?secret=x','https://computer.test:bad','https://computer.test\n']:
            with self.assertRaises(ValueError): validate_origin(value)

    def test_qr_has_square_boolean_matrix(self):
        matrix = qr_matrix('https://computer.test/#pairing=pair1.test')
        self.assertTrue(all(len(row) == len(matrix) for row in matrix))
        self.assertTrue(all(type(cell) is bool for row in matrix for cell in row))


class FirstInstallTest(unittest.TestCase):
    def test_launch_is_detached_and_contains_no_credentials(self):
        with patch('pairing_ui.subprocess.Popen') as popen:
            launch_pairing_display(5432)
        command = popen.call_args.args[0]
        self.assertEqual(command[-4:], ['--port', '5432', '--wait-ready', '30'])
        self.assertNotIn('--url', command)
        self.assertIsNotNone(popen.call_args.kwargs['stdout'])

    def test_windows_hook_only_runs_once_and_keeps_pending_on_failure(self):
        from windows_bridge import open_first_install_pairing
        with tempfile.TemporaryDirectory() as directory, patch('windows_bridge.state_directory', return_value=Path(directory)), patch('pairing_ui.launch_pairing_display') as launch:
            marker = Path(directory) / 'first-pairing.pending'
            open_first_install_pairing(); launch.assert_not_called()
            marker.touch()
            launch.side_effect = OSError('test')
            open_first_install_pairing(); self.assertTrue(marker.exists())
            launch.side_effect = None
            open_first_install_pairing(); self.assertFalse(marker.exists())
            open_first_install_pairing(); self.assertEqual(launch.call_count, 2)

    def test_windows_init_marks_only_new_installations(self):
        import windows_bridge
        with tempfile.TemporaryDirectory() as directory, patch('windows_bridge.sys.platform', 'win32'), patch('windows_bridge.state_directory', return_value=Path(directory)), patch('windows_bridge.windows_token'):
            marker = Path(directory) / 'first-pairing.pending'
            self.assertEqual(windows_bridge.main(['init']), 0)
            self.assertTrue(marker.exists())
            marker.unlink()
            (Path(directory) / 'token.dpapi').touch()
            self.assertEqual(windows_bridge.main(['init']), 0)
            self.assertFalse(marker.exists())

    def test_mac_hook_is_installer_only(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / 'scripts/install-mac-bridge-launch-agent.sh').read_text()
        self.assertIn('if [[ "${first_install}" == true ]]', installer)
        self.assertIn('! -e "${agent_path}"', installer)
        self.assertIn('MobileCodexBridge/devices.json', installer)
        self.assertNotIn('pair-device', (root / 'launchd/com.local.mobile-codex-bridge.plist').read_text())
