"""Single-owner Windows tray runtime. Fixed stdin commands, no network control port.

The tray owns this process through an anonymous pipe. Closing that pipe requests
graceful shutdown, not a Codex Stop. Existing services are never adopted/killed.
"""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
from socketserver import ThreadingMixIn
import sys
import threading
import time


def read_commands(stream, stop):
    for line in stream:
        if line.strip() == 'stop':
            break
    stop.set()  # Parent exit is also a graceful stop request.


def prepare_drain(server):
    """Keep requests alive until finished, but bound idle keep-alive sockets."""
    original = server.RequestHandlerClass
    server.pocket_draining = threading.Event()

    class DrainableHandler(original):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def handle_one_request(self):
            if server.pocket_draining.is_set():
                self.close_connection = True
                return
            try:
                super().handle_one_request()
            finally:
                if server.pocket_draining.is_set():
                    self.close_connection = True

    server.RequestHandlerClass = DrainableHandler
    server.daemon_threads = False
    server.block_on_close = True


def close_requests(server):
    # BridgeServer.server_close closes its app-server *before* draining; the
    # tray must use the reverse order so accepted sends can obtain a receipt.
    ThreadingMixIn.server_close(server)


def run_runtime(binary, *, taskbar=False, stream=None, emit=None):
    from windows_bridge import make_server, windows_token, state_directory, open_first_install_pairing
    from windows_desktop_helper import serve, safe_entry

    stream = stream if stream is not None else sys.stdin
    emit = emit or (lambda state: print(json.dumps(state), flush=True))
    stop, helper_stop, helper_ready = (threading.Event() for _ in range(3))
    server = helper_thread = http_thread = None
    stage = 'credentials'
    try:
        data = state_directory()
        first_install = not (data / 'token.dpapi').exists()
        token = windows_token(initialize=True)  # Reuses existing DPAPI credential.
        if first_install:
            (data / 'first-pairing.pending').touch(mode=0o600)
        root = data / 'desktop-diagnostics'
        root.mkdir(exist_ok=True)
        safe_entry(root, directory=True)
        windows_token()  # Verify inherited ACL before publishing helper reports.

        def helper_worker():
            try:
                serve(root, token, enable_native_send=True, enable_native_stop=True,
                      enable_taskbar_activation=taskbar, stop_event=helper_stop,
                      ready_event=helper_ready, max_seconds=None)
            except Exception:
                pass  # Main thread observes failure without logging private exceptions.

        stage = 'helper'
        helper_thread = threading.Thread(target=helper_worker, name='pocket-helper')
        helper_thread.start()
        deadline = time.monotonic() + 10
        while not helper_ready.wait(0.1):
            if not helper_thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError('helper_unavailable')
        stage = 'bridge'
        server = make_server(binary, token, 4317, native_text_send=True,
                             native_new_tasks=True, attachment_paths=True)
        # Drain accepted requests before stopping the helper or its app-server.
        prepare_drain(server)
        http_thread = threading.Thread(target=server.serve_forever, name='pocket-http')
        http_thread.start()
        threading.Thread(target=read_commands, args=(stream, stop), daemon=True).start()
        open_first_install_pairing(4317)
        emit({'status': 'ready', 'port': 4317, 'taskbarActivation': taskbar})
        stage = 'running'
        while not stop.wait(1):
            if not helper_thread.is_alive() or not http_thread.is_alive():
                raise RuntimeError('component_stopped')
        emit({'status': 'stopping'})
    except Exception as error:
        hints = {
            'credentials': 'Could not read the current Windows user credential or protected data folder. Do not delete pairing; check permissions and script policy.',
            'helper': 'Desktop helper could not start. Use a regular signed-in desktop session and close any old Pocket helper when idle.',
            'bridge': 'Phone service could not start. Check the current Codex CLI/app-server compatibility and whether port 4317 is already occupied.',
            'running': 'A Pocket component stopped unexpectedly. Reopen Pocket after checking Codex and the helper.',
        }
        emit({'status': 'error', 'error': type(error).__name__, 'stage': stage,
              'hint': hints[stage] + ' No existing service was stopped.'})
        return 1
    finally:
        try:
            if server is not None:
                if hasattr(server, 'pocket_draining'):
                    server.pocket_draining.set()
                if http_thread is not None and http_thread.is_alive():
                    server.shutdown()
                    http_thread.join()
                close_requests(server)
        finally:
            helper_stop.set()
            if helper_thread is not None:
                helper_thread.join()  # Finish an in-flight UI operation; never force kill.
            if server is not None:
                server.app_server.close()
    emit({'status': 'stopped'})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-binary', required=True, type=Path)
    parser.add_argument('--taskbar-activation', action='store_true')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('Native Windows required')
    # stdout is a small status protocol, not a log. Existing bridge/helper
    # diagnostics go to stderr (the tray drains and discards it).
    protocol = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        return run_runtime(args.codex_binary, taskbar=args.taskbar_activation,
                           emit=lambda state: print(json.dumps(state), file=protocol, flush=True))


if __name__ == '__main__':
    raise SystemExit(main())
