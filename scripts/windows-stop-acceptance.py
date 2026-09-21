"""Explicit disposable-task native send/Stop test; never part of CI."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_bridge import make_server, windows_token
from windows_stop import latest_turn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thread-id', required=True)
    parser.add_argument('--codex-binary', type=Path, required=True)
    parser.add_argument('--confirm-send-stop', action='store_true', required=True)
    args = parser.parse_args()
    server = make_server(args.codex_binary, windows_token(), 0, native_text_send=True)
    try:
        native = server.native_text_controller
        stop = server.native_stop_controller
        if not stop.available():
            raise RuntimeError('EnableNativeStop helper required')
        before = native._read(args.thread_id)
        old_turn = latest_turn(before)
        if old_turn and old_turn.get('status') == 'inProgress':
            raise RuntimeError('Test task already active; refusing to touch it')
        message = ('Pocket Windows native Stop acceptance test. This is a disposable test task. '
            'Use one read-only wait of 90 seconds, then reply STOP TEST FINISHED. '
            'Do not modify files, send messages, browse, or start other tasks. The bridge will stop this test.')
        result = native.deliver(str(uuid.uuid4()), args.thread_id, message)
        print(json.dumps({'send': result}), flush=True)
        if not result.get('ok'):
            return 1
        for _ in range(20):
            thread = native._read(args.thread_id)
            turn = latest_turn(thread)
            if turn and (not old_turn or turn['id'] != old_turn['id']):
                if turn.get('status') != 'inProgress':
                    raise RuntimeError('Test already finished; no Stop attempted')
                result = stop.stop(args.thread_id, turn['id'], thread['name'])
                print(json.dumps({'turnId': turn['id'], 'stop': result}), flush=True)
                if result.get('ok'):
                    print(json.dumps({'replay': stop.stop(args.thread_id, turn['id'], thread['name'])}), flush=True)
                return 0 if result.get('interrupted') else 1
            time.sleep(0.25)
        raise RuntimeError('New test turn not observed; no Stop attempted')
    finally:
        server.server_close()
        server.app_server.close()


if __name__ == '__main__':
    raise SystemExit(main())
