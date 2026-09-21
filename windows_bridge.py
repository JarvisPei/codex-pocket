#!/usr/bin/env python3
"""Codex Pocket Windows preview: explicit text-only, private app-server mode."""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.parse
import urllib.request

from bridge_runtime import WINDOWS_PREVIEW, WINDOWS_NATIVE_TEXT
from codex_app_server import AppServerError, CodexAppServerClient
from mac_bridge import BridgeServer, DeviceRegistry


ROOT = Path(__file__).resolve().parent

CREDENTIAL_ERROR_CODES = frozenset({
    'environment', 'reparse_path', 'unexpected_owner', 'unexpected_principal',
    'missing_user_access', 'create_directory', 'validate_acl', 'protect',
    'write_credential', 'read_credential', 'unprotect',
})


def state_directory() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if not value or not Path(value).is_absolute():
        raise RuntimeError("LOCALAPPDATA must be an absolute per-user directory.")
    return Path(value) / "CodexPocket"


def windows_token(*, initialize: bool = False) -> str:
    """DPAPI CurrentUser secret over a private pipe, never a CLI argument."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "RemoteSigned", "-File",
         str(ROOT / "scripts/windows-pocket-secret.ps1"),
         "-Action", "Initialize" if initialize else "Read"],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if result.returncode != 0:
        # Only allowlisted error codes may leave the credential subprocess.
        code = 'unknown'
        for line in (result.stderr or '').splitlines():
            if line.startswith('POCKET_CREDENTIAL_ERROR:'):
                candidate = line.removeprefix('POCKET_CREDENTIAL_ERROR:')
                if candidate in CREDENTIAL_ERROR_CODES:
                    code = candidate
        raise RuntimeError(
            f"Windows credential initialization/read failed [{code}]. Run "
            "scripts/windows-pocket-secret.ps1 -Action Check in PowerShell; "
            "check script policy and the current user's data-directory ACL."
        )
    token = result.stdout.strip()
    if len(token) < 32 or len(token) > 256 or any(c.isspace() for c in token):
        raise RuntimeError("Invalid Windows bridge credential.")
    return token


def open_first_install_pairing(port=4317):
    pending = state_directory() / 'first-pairing.pending'
    if not pending.is_file():
        return
    try:
        from pairing_ui import launch_pairing_display
        launch_pairing_display(port)
        pending.unlink()
        print('First-install pairing page scheduled; manual fallback: py -3 scripts/pair-device.py')
    except OSError:
        print('Could not open pairing page. Run: py -3 scripts/pair-device.py', file=sys.stderr)


def read_windows_battery() -> dict:
    if sys.platform != "win32":
        return {"available": False}

    class PowerStatus(ctypes.Structure):
        _fields_ = [
            ("ac", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
            ("percent", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte),
            ("seconds", ctypes.c_uint32), ("full_seconds", ctypes.c_uint32),
        ]

    status = PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return {"available": False}
    if status.flags == 255 or status.flags & 128 or status.percent > 100:
        return {"available": False}
    return {
        "available": True, "percent": int(status.percent),
        "state": "charging" if status.flags & 8 else (
            "full" if status.percent == 100 else "discharging" if status.ac == 0 else "unknown"
        ),
        "powerSource": "AC Power" if status.ac == 1 else "Battery Power",
    }


def codex_binary_path(value: str | None) -> Path:
    if not value:
        raise RuntimeError(
            "Specify --codex-binary with an absolute native codex.exe path "
            "from your Codex installation (not Codex Desktop.exe, WSL, or a .cmd shim)."
        )
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file():
        raise RuntimeError("--codex-binary must name an existing absolute .exe file.")
    return path.resolve()


def pairing_url(origin: str, ticket: str) -> str:
    parsed = urllib.parse.urlsplit(origin)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
    ):
        raise ValueError("Use the HTTPS origin from tailscale serve status, with no path or query.")
    # Only the expiring ticket enters this URL, never the master credential.
    return urllib.parse.urlunsplit(("https", parsed.netloc, "/", "",
                                   urllib.parse.urlencode({"pairing": ticket})))


def local_request(port: int, token: str, path: str, *, method="GET", body=None) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    # Local credential requests must not pass through HTTP_PROXY or redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=15) as response:
        return json.load(response)


def doctor_report(binary_value: str | None, port: int, *, native: bool = False) -> dict:
    """Read-only readiness checks; never initialize credentials or send a turn.

    Only fixed messages enter the report. Backend exceptions, helper payloads,
    credentials, task titles, and paths are deliberately not relayed.
    """
    checks = []

    def add(name, status, message, action=""):
        checks.append({"id": name, "status": status, "message": message, "action": action})

    binary = None
    try:
        binary = codex_binary_path(binary_value)
        add("binary", "ok", "Native CLI path exists.")
    except (OSError, RuntimeError, ValueError):
        add("binary", "failed", "Native CLI path is missing or invalid.",
            "Set --codex-binary to the current absolute native codex.exe path, not the Desktop GUI or a .cmd shim.")
    if binary is not None:
        client = None
        try:
            client = CodexAppServerClient(binary)
            client.start()
            add("protocol", "ok", "Private app-server handshake succeeded; no task was started.")
        except (OSError, RuntimeError, ValueError, AppServerError, subprocess.SubprocessError):
            add("protocol", "failed", "Private app-server handshake failed.",
                "Check that this CLI belongs to your current native Codex installation and supports app-server.")
        finally:
            if client is not None:
                try:
                    client.close()
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    add("protocol_cleanup", "failed", "Diagnostic backend did not close cleanly.")
    else:
        add("protocol", "skipped", "Handshake requires a valid CLI path.")

    if native:
        token = None
        data = None
        try:
            data = state_directory()
            if not (data / "token.dpapi").is_file():
                add("credential", "failed", "Pocket credential has not been initialized.",
                    "Run py -3 windows_bridge.py init in your regular signed-in desktop PowerShell.")
            else:
                token = windows_token()
                add("credential", "ok", "Existing DPAPI credential and directory ACL checks passed.")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            add("credential", "failed", "Existing credential could not be read in this session.",
                "Run scripts/windows-pocket-secret.ps1 -Action Check in your regular desktop PowerShell. "
                "SSH may not share the DPAPI context. Do not delete/reset the credential or loosen its ACL.")
        if token is None:
            add("helper", "skipped", "Authenticated helper checks require the existing credential.")
            add("bridge", "skipped", "Authenticated bridge checks require the existing credential.")
        else:
            try:
                from windows_desktop_helper import request_action
                state = request_action(data / "desktop-diagnostics", token, "status")
                if not isinstance(state, dict):
                    raise ValueError("Invalid helper status")
                if state.get("reachable") is not True or not isinstance(state.get("sessionId"), int) or state["sessionId"] <= 0:
                    add("helper", "failed", "No fresh helper heartbeat in an interactive desktop session.",
                        "Start scripts/windows-start-desktop-helper.ps1 with -PythonBinary, -EnableNativeSend and -EnableNativeStop; keep Windows signed in.")
                elif state.get("nativeSendEnabled") is not True:
                    add("helper", "failed", "Desktop helper is running without native sending enabled.",
                        "Start the helper with -EnableNativeSend -EnableNativeStop.")
                else:
                    busy = state.get("status") == "busy"
                    add("helper", "warning" if busy else "ok",
                        "Desktop helper is busy; retry your operation later." if busy else "Native-send helper heartbeat is fresh.")
                    if state.get("nativeStopEnabled") is not True:
                        add("native_stop", "warning", "Native Stop is not enabled.", "Add -EnableNativeStop when starting the helper.")
                    else:
                        add("native_stop", "ok", "Helper advertises native Stop; no button was clicked.")
                    if state.get("nativeResumeEnabled") is True:
                        add("native_resume", "warning", "Experimental native Resume is enabled; empty final replies are a known unresolved case.",
                            "Omit -EnableNativeResume for the basic preview setup.")
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                add("helper", "failed", "Helper status is unavailable or failed authentication.",
                    "Start the desktop helper under the same Windows account; do not remove its authentication checks.")
            try:
                # After device enrollment, the master credential deliberately
                # cannot authorize Desktop control. Use a read-only admin route
                # to verify the credential, then inspect public capabilities.
                devices = local_request(port, token, "/api/devices")
                if not isinstance(devices, dict) or not isinstance(devices.get("devices"), list):
                    raise ValueError("Invalid authenticated Bridge response")
                result = local_request(port, token, "/health")
                if not isinstance(result, dict) or not isinstance(result.get("capabilities"), dict):
                    raise ValueError("Invalid Bridge capabilities")
                capabilities = result.get("capabilities", {})
                if capabilities.get("platform") == "windows" and capabilities.get("nativeTextSend") is True:
                    add("bridge", "ok", "Authenticated local Bridge is in native-text mode.")
                else:
                    add("bridge", "failed", "Local Bridge is not in Windows native-text mode.",
                        "Start windows_bridge.py serve with --native-text-send; do not run a second server on the occupied port.")
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                add("bridge", "failed", "Authenticated loopback Bridge is unavailable.",
                    "Keep windows_bridge.py serve --native-text-send running under the same account; check --port.")
        add("scope", "info", "Read-only local checks only: phone/Tailscale reachability, screen lock, UI controls and model execution were not tested.")
    else:
        add("scope", "info", "Handshake only. Use --native after starting Bridge/helper for native-mode readiness checks.")
    return {"ok": not any(c["status"] == "failed" for c in checks),
            "mode": "native" if native else "protocol", "checks": checks}


def print_doctor_report(report: dict, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return
    for check in report["checks"]:
        print(f'[{check["status"].upper()}] {check["id"]}: {check["message"]}')
        if check["action"]:
            print(f'  Next: {check["action"]}')


class NoDesktopController:
    """There is intentionally no keyboard/mouse fallback in this preview."""

    def __getattr__(self, name):
        raise RuntimeError("Native Windows Desktop control is not implemented in this preview.")


class WindowsBridgeServer(BridgeServer):
    def project_index(self):
        from windows_projects import load_windows_project_index
        from windows_read_state import unread_index
        return {**load_windows_project_index(self.codex_state_path),
                **unread_index(self.codex_state_path, self.app_server)}

    def mark_thread_read(self, thread_id, payload):
        from windows_read_state import mark_read
        if not mark_read(self.codex_state_path, self.app_server, thread_id, payload):
            super().mark_thread_read(thread_id, payload)

    def server_bind(self) -> None:
        # Windows SO_REUSEADDR can permit a second listener to steal requests.
        self.allow_reuse_address = False
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(binary: Path, token: str, port: int, *, native_text_send: bool = False,
                native_new_tasks: bool = False, attachment_paths: bool = False) -> WindowsBridgeServer:
    if native_new_tasks and not native_text_send:
        raise ValueError('native_new_tasks_requires_native_text_send')
    if attachment_paths and not native_text_send:
        raise ValueError('attachment_paths_requires_native_text_send')
    from windows_attachments import WindowsAttachmentStore
    data = state_directory()
    registry = DeviceRegistry(data / "devices.json")
    registry.ensure_ready()
    client = CodexAppServerClient(binary)
    server = None
    try:
        client.start()
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        server = WindowsBridgeServer(
            ("127.0.0.1", port), token, NoDesktopController(),
            device_registry=registry, app_server=client,
            codex_state_path=codex_home / ".codex-global-state.json",
            attachment_store=WindowsAttachmentStore(data / "uploads"),
            projectless_root=Path.home() / "Documents/Codex",
            screen_lock_probe=lambda: None,
            runtime=replace(WINDOWS_NATIVE_TEXT, native_new_tasks=native_new_tasks,
                attachments=attachment_paths, attachment_paths=attachment_paths) if native_text_send else WINDOWS_PREVIEW,
            battery_probe=read_windows_battery,
        )
        if native_text_send:
            from windows_native import NativeTextController, WindowsNativeHandler
            server.native_text_controller = NativeTextController(client, data, token)
            from windows_stop import NativeStopController
            server.native_stop_controller = NativeStopController(server.native_text_controller)
            from windows_resume import NativeResumeController
            server.native_resume_controller = NativeResumeController(server.native_text_controller)
            if native_new_tasks:
                from windows_create import NativeCreateController
                server.native_create_controller = NativeCreateController(server)
            server.RequestHandlerClass = WindowsNativeHandler
        return server
    except Exception:
        if server is not None:
            server.server_close()
        client.close()
        raise


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create a per-user DPAPI credential and protected data folder.")
    for name in ("serve", "doctor", "pair", "devices", "revoke"):
        command = commands.add_parser(name)
        command.add_argument("--port", type=int, default=4317)
        if name in {"serve", "doctor"}:
            command.add_argument("--codex-binary", default=os.environ.get("CODEX_POCKET_CODEX_BINARY"))
        if name == "doctor":
            command.add_argument("--native", action="store_true", help="Read-only credential, helper and local native Bridge readiness checks")
            command.add_argument("--json", action="store_true", dest="as_json", help="Print a sanitized JSON readiness report")
        if name == "serve":
            command.add_argument("--native-text-send", action="store_true", help="Opt-in existing-task native text delivery; requires desktop helper")
            command.add_argument("--native-new-tasks", action="store_true", help="Experimental empty task creation + native first-message delivery (requires --native-text-send)")
            command.add_argument("--attachment-paths", action="store_true", help="Experimental uploads as local file paths, NOT native image attachments (requires --native-text-send)")
        if name == "pair":
            command.add_argument("--url", required=True, help="Your Tailscale HTTPS origin.")
        if name == "revoke":
            command.add_argument("device_id")
    args = parser.parse_args(argv)
    if hasattr(args, "port") and not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if getattr(args, 'native_new_tasks', False) and not args.native_text_send:
        parser.error('--native-new-tasks requires --native-text-send')
    if getattr(args, 'attachment_paths', False) and not args.native_text_send:
        parser.error('--attachment-paths requires --native-text-send')
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("This entry point requires native Windows Python 3.11+. Mac uses mac_bridge.py.")
    server = None
    try:
        if args.command == "init":
            first_install = not (state_directory() / 'token.dpapi').exists()
            windows_token(initialize=True)
            if first_install:
                (state_directory() / 'first-pairing.pending').touch(mode=0o600)
            print("Windows preview credential ready (DPAPI CurrentUser); no service installed.")
            return 0
        if args.command == "doctor":
            report = doctor_report(args.codex_binary, args.port, native=args.native)
            print_doctor_report(report, as_json=args.as_json)
            return 0 if report["ok"] else 1
        if args.command == "serve":
            binary = codex_binary_path(args.codex_binary)
            token = windows_token()
            server = make_server(binary, token, args.port, native_text_send=args.native_text_send,
                                 native_new_tasks=args.native_new_tasks, attachment_paths=args.attachment_paths)
            mode = "native existing-task text" if args.native_text_send else "text-only background execution"
            print(f"Windows PREVIEW: {mode} on http://127.0.0.1:{args.port}")
            print("Keep this process running. Native mode never falls back to background execution.")
            open_first_install_pairing(args.port)
            server.serve_forever()
        else:
            if args.command == "pair":
                pairing_url(args.url, "validation")
            token = windows_token()
            if args.command == "pair":
                result = local_request(args.port, token, "/api/devices/pairing-ticket", method="POST", body={})
                print("Single-use pairing link (expires in five minutes; keep private):")
                print(pairing_url(args.url, result["pairingTicket"]))
            elif args.command == "devices":
                print(json.dumps(local_request(args.port, token, "/api/devices"), ensure_ascii=True, indent=2))
            else:
                device_id = urllib.parse.quote(args.device_id, safe="")
                result = local_request(args.port, token, f"/api/devices/{device_id}", method="DELETE")
                print(json.dumps(result))
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, ValueError, AppServerError, subprocess.SubprocessError) as error:
        print(f"Windows preview: {error}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
