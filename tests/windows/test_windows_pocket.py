import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from platforms.windows import pocket as pocket
from platforms.windows import bridge as bridge
from platforms.windows import desktop_helper as helper
class PocketRuntimeTests(unittest.TestCase):
    def test_only_fixed_stop_or_eof_controls_lifetime(self):
        for text in ('stop\n', '', 'run arbitrary command\n'):
            event = threading.Event()
            pocket.read_commands(io.StringIO(text), event)
            self.assertTrue(event.is_set())

    def exercise(self, *, old_helper=False, occupied=False, existing=True):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            if existing:
                (data / 'token.dpapi').write_bytes(b'unchanged ciphertext')
            server = Mock()
            server.RequestHandlerClass = type('Handler', (), {})
            http_stop = threading.Event()
            server.serve_forever.side_effect = lambda: http_stop.wait(3)
            server.shutdown.side_effect = http_stop.set
            events = []

            def worker(root, token, **kwargs):
                self.assertIsNone(kwargs['max_seconds'])
                self.assertTrue(kwargs['enable_native_send'])
                self.assertTrue(kwargs['enable_native_stop'])
                self.assertNotIn('enable_native_resume', kwargs)
                if old_helper:
                    raise RuntimeError('helper_already_running')
                kwargs['ready_event'].set()
                kwargs['stop_event'].wait(3)
                events.append('helper_drained')

            with patch.object(bridge, 'state_directory', return_value=data), \
                 patch.object(bridge, 'windows_token', return_value='private-key') as token, \
                 patch.object(helper, 'serve', side_effect=worker) as serve, \
                 patch.object(bridge, 'make_server', return_value=server) as make, \
                 patch.object(bridge, 'open_first_install_pairing') as pairing, \
                 patch.object(pocket, 'close_requests', side_effect=lambda _: events.append('requests_drained')):
                if occupied:
                    make.side_effect = OSError('port in use')
                server.app_server.close.side_effect = lambda: events.append('app_server_closed')
                states = []
                result = pocket.run_runtime(Path('codex.exe'), stream=io.StringIO('stop\n'), emit=states.append)
                self.assertNotIn('private-key', str(states))
                token.assert_any_call(initialize=True)
                if existing:
                    self.assertEqual((data / 'token.dpapi').read_bytes(), b'unchanged ciphertext')
                    self.assertFalse((data / 'first-pairing.pending').exists())
                else:
                    self.assertTrue((data / 'first-pairing.pending').exists())
                if old_helper:
                    make.assert_not_called()
                    self.assertEqual(result, 1)
                    self.assertEqual(states[-1]['stage'], 'helper')
                    self.assertIn('old Pocket helper', states[-1]['hint'])
                elif occupied:
                    self.assertEqual(events, ['helper_drained'])
                    self.assertEqual(result, 1)
                    self.assertEqual(states[-1]['stage'], 'bridge')
                    self.assertIn('port 4317', states[-1]['hint'])
                    pairing.assert_not_called()
                else:
                    self.assertEqual(result, 0)
                    self.assertEqual([x['status'] for x in states], ['ready', 'stopping', 'stopped'])
                    self.assertEqual(events, ['requests_drained', 'helper_drained', 'app_server_closed'])
                    self.assertFalse(server.daemon_threads)
                    self.assertTrue(server.block_on_close)
                    server.server_close.assert_not_called()
                    make.assert_called_once_with(Path('codex.exe'), 'private-key', 4317,
                        native_text_send=True, native_new_tasks=True, attachment_paths=True)

    def test_graceful_exit_preserves_credentials_and_drains_before_app_server_close(self):
        self.exercise()

    def test_existing_helper_is_not_stopped_or_adopted(self):
        self.exercise(old_helper=True)

    def test_foreign_port_does_not_kill_other_service(self):
        self.exercise(occupied=True)

    def test_first_install_schedules_pairing(self):
        self.exercise(existing=False)

    def test_helper_unlimited_mode_exits_cooperatively(self):
        with tempfile.TemporaryDirectory() as directory:
            stop, ready = threading.Event(), threading.Event()
            worker = threading.Thread(target=helper.worker_loop,
                args=(Path(directory), 'key', 1), kwargs={
                    'max_seconds': None, 'stop_event': stop, 'ready_event': ready})
            worker.start()
            try:
                self.assertTrue(ready.wait(2))
                self.assertTrue(worker.is_alive())
            finally:
                stop.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(helper.read_signed(Path(directory) / 'state.json', 'key')['status'], 'stopped')

    def test_legacy_helper_keeps_bounded_default(self):
        import inspect
        self.assertEqual(inspect.signature(helper.worker_loop).parameters['max_seconds'].default, 14400)

    def test_draining_stops_keepalive_from_accepting_more_requests(self):
        handled = []

        class Handler:
            def setup(self):
                pass

            def handle_one_request(self):
                handled.append('request')

        server = Mock(RequestHandlerClass=Handler)
        pocket.prepare_drain(server)
        request = server.RequestHandlerClass()
        request.connection = Mock()
        request.setup()
        request.connection.settimeout.assert_called_once_with(30)
        request.handle_one_request()
        server.pocket_draining.set()
        request.handle_one_request()
        self.assertEqual(handled, ['request'])
        self.assertTrue(request.close_connection)


if __name__ == '__main__':
    unittest.main()
