"""Loopback-only pairing display. Master credential never leaves this process.

This is a separate local server, NOT a route on the Tailscale-exposed Bridge.
The ephemeral display capability can only create/check pairing tickets, never
read tasks or operate Desktop. It expires when this process closes (30 min max).
"""
from __future__ import annotations
import argparse
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

from vendor.qrcodegen import QrCode

ROOT = Path(__file__).resolve().parent


def origin_from_serve(config, port):
    """Accept only an unambiguous private HTTPS root proxy to this Bridge."""
    candidates = set()
    for host, web in config.get('Web', {}).items():
        origin = validate_origin('https://' + host)
        parsed = urllib.parse.urlsplit(origin)
        proxy = web.get('Handlers', {}).get('/', {}).get('Proxy')
        if proxy not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}',
                         f'http://127.0.0.1:{port}/', f'http://localhost:{port}/'):
            continue
        if config.get('TCP', {}).get(str(parsed.port or 443), {}).get('HTTPS') is not True:
            continue
        if config.get('AllowFunnel', {}).get(host):
            continue
        candidates.add(urllib.parse.urlunsplit(('https', parsed.hostname if parsed.port in (None, 443)
            else parsed.netloc, '', '', '')))
    if len(candidates) != 1:
        raise RuntimeError('tailscale_not_ready')
    return candidates.pop()


def discover_origin(port):
    executable = shutil.which('tailscale')
    if not executable:
        known = (Path('/Applications/Tailscale.app/Contents/MacOS/Tailscale')
                 if sys.platform == 'darwin' else
                 Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale/tailscale.exe')
        if known.is_file():
            executable = str(known)
    if not executable:
        raise RuntimeError('tailscale_not_ready')
    try:
        result = subprocess.run([executable, 'serve', 'status', '--json'],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError()
        return origin_from_serve(json.loads(result.stdout), port)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        raise RuntimeError('tailscale_not_ready') from None


def launch_pairing_display(port=4317):
    """One-shot installer hook, never a login/startup registration."""
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    return subprocess.Popen([sys.executable, str(ROOT / 'scripts/pair-device.py'),
        '--port', str(port), '--wait-ready', '30'], **options)


def wait_for_bridge(port, seconds):
    deadline = time.monotonic() + seconds
    while True:
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/health')
            response = connection.getresponse()
            result = json.loads(response.read(65536))
            if response.status == 200 and result.get('ok') and result.get('service') in ('mac-codex-bridge', 'codex-pocket-bridge'):
                return
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        if time.monotonic() >= deadline:
            raise RuntimeError('Bridge is not ready. Start it, then run scripts/pair-device.py again.')
        time.sleep(0.5)


def validate_origin(value):
    p = urllib.parse.urlsplit(value)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.path not in ('', '/') or p.query or p.fragment
            or any(c.isspace() for c in value)):
        raise ValueError('Use the HTTPS origin shown by tailscale serve status.')
    _ = p.port  # Validate the port too.
    return urllib.parse.urlunsplit(('https', p.netloc, '', '', ''))


def qr_matrix(value):
    qr = QrCode.encode_text(value, QrCode.Ecc.MEDIUM)
    return [[qr.get_module(x, y) for x in range(qr.get_size())] for y in range(qr.get_size())]


class PairingDisplay(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, origin, token, bridge_port=4317):
        self.remote_origin = validate_origin(origin) if origin else None
        self.master = token
        self.bridge_port = bridge_port
        self.capability = secrets.token_urlsafe(32)
        self.ticket = ''
        self.lock = threading.Lock()
        super().__init__(('127.0.0.1', 0), PairingHandler)
        self.origin = f'http://127.0.0.1:{self.server_port}'

    def bridge(self, path, body):
        connection = http.client.HTTPConnection('127.0.0.1', self.bridge_port, timeout=8)
        try:
            connection.request('POST', path, json.dumps(body), {
                'Authorization': f'Bearer {self.master}', 'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = json.loads(response.read(65536))
            if response.status >= 400:
                raise RuntimeError('bridge_update_required' if response.status == 404 else 'bridge_refused')
            return result
        finally:
            connection.close()


class PairingHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log capabilities or tickets.

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def send(self, code, body, mime='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': 'local_only'})
        assets = {'/': ('pair.html', 'text/html; charset=utf-8'),
                  '/pair.js': ('pair.js', 'text/javascript; charset=utf-8'),
                  '/pair.css': ('pair.css', 'text/css; charset=utf-8')}
        asset = assets.get(self.path)
        if not asset:
            return self.send(404, {'error': 'not_found'})
        self.send(200, (ROOT / 'web' / 'pairing' / asset[0]).read_bytes(), asset[1])

    def do_POST(self):
        self.close_connection = True
        supplied = self.headers.get('X-Pocket-Pairing', '')
        if (not self.valid_host() or self.headers.get('Origin') != self.server.origin
                or not hmac.compare_digest(supplied, self.server.capability)):
            return self.send(403, {'error': 'local_authorization_required'})
        if self.path not in ('/new', '/status'):
            return self.send(404, {'error': 'not_found'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 or self.headers.get('Transfer-Encoding'):
                raise ValueError()
            if json.loads(self.rfile.read(length)) != {}:
                raise ValueError()
        except (ValueError, OSError):
            return self.send(400, {'error': 'invalid_request'})
        with self.server.lock:
            try:
                if self.path == '/new':
                    origin = self.server.remote_origin or discover_origin(self.server.bridge_port)
                    # Ensure the installed Bridge supports status before issuing a ticket.
                    self.server.bridge('/api/devices/pairing-status', {'pairingTicket': 'probe'})
                    r = self.server.bridge('/api/devices/pairing-ticket', {'replaceTicket': self.server.ticket})
                    self.server.ticket = r['pairingTicket']
                    url = origin + '/#' + urllib.parse.urlencode({'pairing': self.server.ticket})
                    return self.send(200, {'status': 'valid', 'origin': origin,
                        'expiresIn': r['expiresIn'], 'matrix': qr_matrix(url), 'ticket': self.server.ticket})
                r = self.server.bridge('/api/devices/pairing-status', {'pairingTicket': self.server.ticket or 'none'})
                self.send(200, r)
            except (OSError, ValueError, RuntimeError, KeyError) as error:
                self.send(503, {'error': str(error) if str(error) in ('bridge_update_required', 'tailscale_not_ready') else 'bridge_unavailable',
                    'bridgePort': self.server.bridge_port})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='HTTPS origin; omitted means detect the private Tailscale Serve root proxy')
    parser.add_argument('--port', type=int, default=4317)
    parser.add_argument('--launch', action='store_true', help='Installer: open once in a separate, time-limited process')
    parser.add_argument('--wait-ready', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--no-open', action='store_true', help='Print the local display URL without opening a browser')
    args = parser.parse_args(argv)
    try:
        origin = validate_origin(args.url) if args.url else None
        if not 1 <= args.port <= 65535:
            raise ValueError('Invalid Bridge port.')
        if not 0 <= args.wait_ready <= 30:
            raise ValueError('Invalid readiness timeout.')
        if args.launch:
            if args.url or args.no_open:
                raise ValueError('--launch uses automatic detection and opens the browser.')
            launch_pairing_display(args.port)
            print('First-install pairing page scheduled. If it does not open, run scripts/pair-device.py manually.')
            return 0
        wait_for_bridge(args.port, args.wait_ready)
        if sys.platform == 'win32':
            from windows_bridge import windows_token
            token = windows_token()
        elif sys.platform == 'darwin':
            result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-w', '-s',
                'mobile-codex-bridge', '-a', os.environ.get('USER', '')], capture_output=True, text=True, timeout=8)
            if result.returncode or len(result.stdout.strip()) < 32:
                raise RuntimeError('Cannot read Bridge credential from Keychain.')
            token = result.stdout.strip()
        else:
            raise RuntimeError('Run on the Mac or Windows computer hosting the Bridge.')
        server = PairingDisplay(origin, token, args.port)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f'{error}\n')
    url = server.origin + '/#' + server.capability
    print('Local pairing display (private; do not forward):', url, flush=True)
    print('Scan the QR on your phone. Ctrl+C closes this display; it also closes after 30 minutes.', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    timer = threading.Timer(1800, server.shutdown)
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()
    return 0
