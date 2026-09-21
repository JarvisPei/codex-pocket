"""Account-scoped Desktop unread state; acknowledge via Desktop, never file writes.

The Windows Desktop 26.915 local coordination protocol is private. Keep the
adapter narrow and fail closed on schema/context changes. No tokens leave this
process; only the non-secret identity and host context go to the local pipe.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from codex_app_server import AppServerError

STATE_KEY = 'electron-thread-read-state-v1'
ROOT = Path(__file__).resolve().parent


def compact(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(compact(value).encode()).hexdigest()


def context_from_auth(result):
    """Match Desktop's identity derivation, without retaining the bearer token."""
    try:
        method = result.get('authMethod')
        if method in ('chatgpt', 'chatgptAuthTokens'):
            token = result['authToken']
            if not isinstance(token, str) or len(token) > 65536:
                raise ValueError()
            encoded = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            auth = claims['https://api.openai.com/auth']
            account = auth.get('chatgpt_account_id') or auth.get('account_id')
            user = auth.get('user_id') or auth.get('chatgpt_user_id')
            if not all(isinstance(v, str) and 0 < len(v) <= 256 for v in (account, user)):
                raise ValueError()
            if not isinstance(claims.get('exp'), (int, float)) or claims['exp'] <= time.time():
                raise ValueError()
            identity = {'kind': 'chatgpt', 'accountId': account, 'userId': user}
        elif method is not None or result.get('requiresOpenaiAuth') is False:
            if method is not None and (not isinstance(method, str) or len(method) > 80):
                raise ValueError()
            identity = {'kind': 'execution-storage', 'authMode': method or 'none'}
        else:
            raise ValueError()
        return {'identity': identity, 'executionHostKey': 'local:' + digest(['local', 'local', None])}
    except (KeyError, IndexError, TypeError, ValueError):
        raise AppServerError('Desktop read-state identity unavailable.') from None


def identity_key(context):
    identity = context['identity']
    values = ([identity['kind'], identity['accountId'], identity['userId']]
              if identity['kind'] == 'chatgpt' else [identity['kind'], identity['authMode']])
    return digest(values)


def scope_key(context):
    return digest([identity_key(context), context['executionHostKey']])


def read_state(path, client):
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if STATE_KEY not in state:
            return None
        root = state[STATE_KEY]
        if not isinstance(root, dict) or root.get('version') != 1:
            raise ValueError()
        # Resolve on each operation. Never union buckets or cache across logins.
        context = context_from_auth(client.request('getAuthStatus', {
            'includeToken': True, 'refreshToken': False,
        }, timeout=5))
        buckets = root['unreadByIdentity']
        bucket = buckets.get(identity_key(context))
        # Unknown host/account is not an empty, successfully synchronized set.
        if not isinstance(bucket, dict) or context['executionHostKey'] not in bucket:
            raise ValueError()
        ids = bucket[context['executionHostKey']]
        if not isinstance(ids, list) or any(not isinstance(t, str) for t in ids):
            raise ValueError()
        return {'context': context, 'scope': scope_key(context), 'ids': set(ids)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise AppServerError('Desktop unread state unavailable.') from None


def unread_index(path, client):
    try:
        state = read_state(path, client)
    except AppServerError:
        return {'unreadThreadIds': set(), 'readStateAuthoritative': True,
                'readStateAvailable': False, 'readStateScope': ''}
    if state is None:
        return {}
    return {'unreadThreadIds': state['ids'], 'readStateAuthoritative': True,
            'readStateAvailable': True, 'readStateScope': state['scope']}


def notify_desktop(thread_id, context):
    script = ROOT / 'scripts/windows-thread-read-state.ps1'
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'RemoteSigned', '-File', str(script)],
            input=compact({'threadId': thread_id, 'context': context}),
            capture_output=True, encoding='utf-8', timeout=12,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or json.loads(result.stdout).get('sent') is not True:
            raise ValueError()
    except (OSError, ValueError, subprocess.SubprocessError):
        # Never include transport input/output (which carries account identity).
        raise AppServerError('Desktop read acknowledgement unavailable.') from None


def mark_read(path, client, thread_id, payload, *, notify=notify_desktop, attempts=12):
    state = read_state(path, client)
    if state is None:
        return False
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', thread_id):
        raise AppServerError('Invalid read acknowledgement target.')
    if not isinstance(payload, dict) or payload.get('scope') != state['scope']:
        raise AppServerError('Read acknowledgement identity changed.')
    revision = payload.get('revision')
    thread = client.request('thread/read', {'threadId': thread_id, 'includeTurns': False}, timeout=5).get('thread', {})
    if thread.get('id') != thread_id or not isinstance(revision, str) or revision != str(thread.get('updatedAt')):
        raise AppServerError('Read acknowledgement revision changed.')
    if thread_id not in state['ids']:
        return True
    notify(thread_id, state['context'])
    for _ in range(attempts):
        current = read_state(path, client)
        if current is None or current['scope'] != state['scope']:
            raise AppServerError('Read acknowledgement identity changed.')
        if thread_id not in current['ids']:
            return True
        time.sleep(0.15)
    raise AppServerError('Desktop did not confirm the read acknowledgement.')
