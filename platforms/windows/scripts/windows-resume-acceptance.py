"""Explicit native resume test for one user-designated disposable task."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from codex_app_server import CodexAppServerClient
from platforms.windows.bridge import state_directory, windows_token
from platforms.windows.desktop_helper import request_action
from platforms.windows.native import NativeTextController
from platforms.windows.resume import NativeResumeController
from platforms.windows.stop import latest_turn


def completion_evidence(turn, expected_text=None):
    """A lifecycle receipt alone must never pass functional acceptance."""
    status = turn.get('status')
    if status != 'completed':
        return {'verified': False, 'reason': 'turn_not_completed', 'status': status}
    final_text = '\n'.join(
        item.get('text', '').strip()
        for item in turn.get('items', [])
        if isinstance(item, dict) and item.get('type') == 'agentMessage'
        and item.get('phase') == 'final_answer' and isinstance(item.get('text'), str)
    ).strip()
    if not final_text:
        return {'verified': False, 'reason': 'empty_final_reply', 'status': status}
    if expected_text is not None and final_text != expected_text:
        return {'verified': False, 'reason': 'unexpected_final_reply', 'status': status}
    return {'verified': True, 'reason': 'nonempty_final_reply', 'status': status,
            'expectedTextMatched': expected_text is not None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thread-id',required=True)
    parser.add_argument('--codex-binary',type=Path,required=True)
    parser.add_argument('--confirm-resume',action='store_true',required=True)
    parser.add_argument('--expect-final-text', default=None,
                        help='Require this exact final reply for functional acceptance')
    parser.add_argument('--completion-timeout', type=int, default=120)
    args=parser.parse_args()
    if sys.platform!='win32':parser.error('Requires native Windows')
    if not 1 <= args.completion_timeout <= 300:
        parser.error('completion-timeout must be 1..300 seconds')
    token=windows_token()
    client=CodexAppServerClient(args.codex_binary);client.start()
    try:
        native=NativeTextController(client,state_directory(),token)
        turn=latest_turn(native._read(args.thread_id))
        if not turn or turn.get('status')!='interrupted':
            print(json.dumps({'ok':False,'reason':'test_target_not_paused'}));return 1
        resume=NativeResumeController(native)
        rid=str(uuid.uuid4())
        result=resume.resume(rid,args.thread_id,turn['id'])
        print(json.dumps({'requestId':rid,'expectedTurnId':turn['id'],'resume':result,
                          'completionVerified':False}),flush=True)
        if not result.get('ok'):
            return 1
        replay=resume.resume(rid,args.thread_id,turn['id'])
        print(json.dumps({'replay':replay}),flush=True)
        if not replay.get('desktop',{}).get('duplicateRequest'):
            raise RuntimeError('Replay was not confirmed without another click')
        state=request_action(state_directory()/'desktop-diagnostics',token,'status')
        print(json.dumps({'nativeResumeEnabled':state.get('nativeResumeEnabled')}))
        deadline = time.monotonic() + args.completion_timeout
        while time.monotonic() < deadline:
            current = latest_turn(native._read(args.thread_id))
            if current and current.get('status') in {'completed', 'failed', 'interrupted'}:
                if current['id'] != turn['id'] or current.get('status') != 'interrupted':
                    evidence = completion_evidence(current, args.expect_final_text)
                    print(json.dumps({'turnId': current['id'], 'completion': evidence}), flush=True)
                    return 0 if evidence['verified'] else 1
            time.sleep(1)
        print(json.dumps({'completion': {'verified': False, 'reason': 'completion_timeout'},
                          'note': 'No automatic retry or additional UI action'}), flush=True)
        return 1
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
