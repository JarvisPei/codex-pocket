"""Manually started, same-user desktop diagnostic worker. No network listener.

SSH submits authenticated mailbox requests; only the desktop worker runs UIA.
No arbitrary command or script path is accepted. Separate explicit startup
opt-ins enable fixed smoke-send, ordinary text delivery, and guarded task Stop.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time

from windows_bridge import ROOT, state_directory, windows_token
from windows_desktop_controls import inspect_composer
from windows_delivery import validate_delivery

OPERATIONS = {"diagnose", "stop", "smoke-send", "native-send", "native-stop-prepare", "native-stop-commit", "native-resume-prepare", "native-resume-commit"}  # stop terminates only this helper.
LIMIT = 2 * 1024 * 1024


def sign(payload: dict, token: str) -> dict:
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {"body": body, "mac": hmac.new(token.encode(), body.encode(), hashlib.sha256).hexdigest()}


def verify(envelope: dict, token: str) -> dict:
    if not isinstance(envelope, dict) or set(envelope) != {"body", "mac"}:
        raise ValueError("invalid_envelope")
    body, mac = envelope["body"], envelope["mac"]
    if (not isinstance(body, str) or not isinstance(mac, str) or len(body) > LIMIT
            or len(mac) != 64 or any(c not in "0123456789abcdef" for c in mac)):
        raise ValueError("invalid_envelope")
    expected = hmac.new(token.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise ValueError("invalid_signature")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    return payload


def safe_entry(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("reparse_point_refused")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid_directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
        raise ValueError("invalid_file")


def read_signed(path: Path, token: str) -> dict:
    safe_entry(path)
    with path.open("rb") as source:
        raw = source.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("oversized_file")
    return verify(json.loads(raw), token)


def write_signed(path: Path, payload: dict, token: str) -> None:
    safe_entry(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        safe_entry(path)
    encoded = json.dumps(sign(payload, token), ensure_ascii=True).encode()
    if len(encoded) > LIMIT:
        raise ValueError("oversized_response")
    temporary = path.with_name(".pending-" + secrets.token_hex(16))
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_request(request: dict, instance: str, now: float) -> None:
    if not isinstance(request.get('action'), str):
        raise ValueError('unsupported_request')
    fields = {"id", "instance", "action", "createdAt"}
    if request.get("action") in {"native-send", "native-stop-prepare", "native-stop-commit", "native-resume-prepare", "native-resume-commit"}:
        fields.add("delivery")
    if set(request) != fields:
        raise ValueError("invalid_request_fields")
    identifier = request["id"]
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("invalid_request_id")
    if request["instance"] != instance or not isinstance(request["action"], str) or request["action"] not in OPERATIONS:
        raise ValueError("unsupported_request")
    created = request["createdAt"]
    if type(created) not in (int, float) or not 0 <= now - created <= 45:
        raise ValueError("expired_request")
    if request["action"] == "native-send":
        data = request["delivery"]
        if not isinstance(data, dict) or set(data) != {"threadId", "expectedTitle", "message"}:
            raise ValueError("invalid_delivery")
        validate_delivery("00000000-0000-0000-0000-000000000000", data["threadId"], data["message"])
        if not isinstance(data["expectedTitle"], str) or not 0 < len(data["expectedTitle"]) <= 1000:
            raise ValueError("invalid_title")
    if request["action"] in {"native-stop-prepare", "native-stop-commit", "native-resume-prepare", "native-resume-commit"}:
        from windows_stop import validate_stop_payload
        validate_stop_payload(request["delivery"], commit=request["action"].endswith('-commit'))


def session_id() -> int:
    import ctypes
    result = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(result)):
        raise RuntimeError("cannot_identify_session")
    return result.value


def run_diagnostic() -> dict:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
             "-File", str(ROOT / "scripts/windows-desktop-diagnostic.ps1"), "-View", "Raw"],
            capture_output=True, encoding="utf-8", timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"error": "diagnostic_timeout"}
    except (OSError, UnicodeError):
        return {"error": "diagnostic_failed"}
    if result.returncode or len(result.stdout.encode("utf-8")) > LIMIT // 2:
        return {"error": "diagnostic_failed"}
    try:
        report = json.loads(result.stdout.lstrip("\ufeff"))
        if not isinstance(report, dict) or report.get("readOnly") is not True:
            raise ValueError()
        return {"report": report}
    except ValueError:
        return {"error": "invalid_diagnostic_report"}


def run_smoke_send() -> dict:
    """Fixed script, fixed draft, no request-supplied UI input or script path."""
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        completed = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
             "-File", str(ROOT / "scripts/windows-desktop-smoke-send.ps1")],
            capture_output=True, encoding="utf-8", timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode or len(completed.stdout) > 4096:
            raise ValueError()
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        if (not isinstance(result, dict) or result.get("action") != "smoke-send"
                or result.get("status") not in {"refused", "invoke_requested", "outcome_unknown"}):
            raise ValueError()
        return {"smokeSend": result}
    except (ValueError, OSError, subprocess.SubprocessError):
        return {"smokeSend": {"status": "outcome_unknown", "reason": "do_not_retry"}}


def run_native_send(delivery: dict, *, allow_display_off: bool = False, allow_taskbar_activation: bool = False) -> dict:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        completed = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
             "-File", str(ROOT / "scripts/windows-desktop-send.ps1"),
             *(["-AllowDisplayOff"] if allow_display_off else []),
             *(["-AllowTaskbarActivation"] if allow_taskbar_activation else [])],
            input=json.dumps(delivery, ensure_ascii=True), capture_output=True, encoding="utf-8", timeout=55,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode or len(completed.stdout) > 4096:
            raise ValueError()
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        if (not isinstance(result, dict) or result.get("targetThreadId") != delivery["threadId"]
                or result.get("status") not in {"refused", "uncertain", "invoke_requested"}):
            raise ValueError()
        return {"nativeSend": result}
    except (ValueError, OSError, subprocess.SubprocessError):
        return {"nativeSend": {"status": "uncertain", "reason": "do_not_retry"}}


def run_native_stop(delivery: dict, phase: str, *, resume: bool = False) -> dict:
    from windows_stop import validate_stop_payload
    validate_stop_payload(delivery, commit=phase == "commit")
    if phase not in {"prepare", "commit"}:
        raise ValueError("invalid_stop_phase")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result_key = 'nativeResume' if resume else 'nativeStop'
    try:
        completed = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "RemoteSigned", "-File", str(ROOT / "scripts/windows-desktop-send.ps1"),
            "-ResumePhase" if resume else "-StopPhase", phase], input=json.dumps(delivery, ensure_ascii=True),
            capture_output=True, encoding="utf-8", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode or len(completed.stdout) > 4096:
            raise ValueError()
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        if (not isinstance(result, dict) or result.get("targetThreadId") != delivery["threadId"]
                or result.get("status") not in {"refused", "prepared", "uncertain", "invoke_requested"}):
            raise ValueError()
        return {result_key: result}
    except (ValueError, OSError, subprocess.SubprocessError):
        return {result_key: {"status": "uncertain", "reason": "do_not_retry"}}


def acquire_lock(root: Path):
    import msvcrt
    path = root / "worker.lock"
    if path.exists() or path.is_symlink():
        safe_entry(path)
    handle = path.open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        handle.close()
        raise RuntimeError("helper_already_running") from None


def serve(root: Path, token: str, *, enable_smoke_send: bool = False, enable_native_send: bool = False, enable_native_stop: bool = False, enable_native_resume: bool = False, enable_taskbar_activation: bool = False, stop_event=None, ready_event=None, max_seconds=4 * 60 * 60) -> None:
    session = session_id()
    if session == 0:
        raise RuntimeError("start_helper_in_desktop_powershell")
    import ctypes
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("start_helper_without_administrator_elevation")
    lock = acquire_lock(root)
    try:
        worker_loop(root, token, session, enable_smoke_send=enable_smoke_send, enable_native_send=enable_native_send, enable_native_stop=enable_native_stop, enable_native_resume=enable_native_resume, enable_taskbar_activation=enable_taskbar_activation, stop_event=stop_event, ready_event=ready_event, max_seconds=max_seconds)
    finally:
        lock.close()


def worker_loop(root: Path, token: str, session: int, *, enable_smoke_send: bool = False, enable_native_send: bool = False, enable_native_stop: bool = False, enable_native_resume: bool = False, enable_taskbar_activation: bool = False, stop_event=None, ready_event=None, max_seconds=4 * 60 * 60) -> None:
    instance = secrets.token_hex(16)
    state = {"instance": instance, "pid": os.getpid(), "sessionId": session, "status": "ready",
             "smokeSendAvailable": enable_smoke_send, "nativeSendEnabled": enable_native_send, "nativeStopEnabled": enable_native_stop, "nativeResumeEnabled": enable_native_resume,
             "taskbarActivationEnabled": enable_taskbar_activation and enable_native_send}
    seen = set()
    started, heartbeat = time.monotonic(), float('-inf')
    print("Desktop helper ready. " + (
        "One fixed-draft smoke-send attempt enabled; no automatic send. " if enable_smoke_send
        else "Native text sending enabled. " if enable_native_send
        else "Read-only diagnostics; no clicks or typing. ") + "Ctrl+C stops this helper.", flush=True)
    try:
        # Legacy test entry stays bounded. Only the tray owner opts into an
        # unlimited lifetime and supplies a cooperative shutdown event.
        while (max_seconds is None or time.monotonic() - started < max_seconds) and not (stop_event and stop_event.is_set()):
            if time.monotonic() - heartbeat > 2:
                write_signed(root / "state.json", {**state, "updatedAt": time.time()}, token)
                if ready_event is not None:
                    ready_event.set()
                heartbeat = time.monotonic()
            request_path = root / "request.json"
            if request_path.exists():
                try:
                    request = read_signed(request_path, token)
                    validate_request(request, instance, time.time())
                    if request["id"] in seen:
                        raise ValueError("replayed_request")
                except (ValueError, OSError):
                    # Do not follow/delete untrusted entries or print their content.
                    time.sleep(0.25)
                    continue
                seen.add(request["id"])
                request_path.unlink()
                action = request["action"]
                write_signed(root / "state.json", {**state, "status": "busy", "updatedAt": time.time()}, token)
                if action == "stop":
                    outcome = {"stopped": True}
                elif action == "smoke-send":
                    if not state["smokeSendAvailable"]:
                        outcome = {"error": "smoke_send_not_enabled_or_already_attempted"}
                    else:
                        # Consume before calling UIA; refusal/timeout still consumes
                        # the attempt. An uncertain Invoke must never be repeated.
                        state["smokeSendAvailable"] = False
                        write_signed(root / "state.json", {**state, "status": "busy", "updatedAt": time.time()}, token)
                        outcome = run_smoke_send()
                elif action == "native-send":
                    outcome = (run_native_send(request["delivery"], **({"allow_taskbar_activation": True} if enable_taskbar_activation else {})) if enable_native_send else
                               {"nativeSend": {"status": "refused", "reason": "native_send_not_enabled"}})
                elif action in {"native-stop-prepare", "native-stop-commit"}:
                    outcome = (run_native_stop(request["delivery"], action.rsplit('-', 1)[-1]) if enable_native_stop else
                               {"nativeStop": {"status": "refused", "reason": "native_stop_not_enabled"}})
                elif action in {"native-resume-prepare", "native-resume-commit"}:
                    outcome = (run_native_stop(request["delivery"], action.rsplit('-', 1)[-1], resume=True) if enable_native_resume else
                               {"nativeResume": {"status": "refused", "reason": "native_resume_not_enabled"}})
                else:
                    outcome = run_diagnostic()
                write_signed(root / "response.json", {
                    "id": request["id"], "instance": instance, "finishedAt": time.time(), **outcome,
                }, token)
                if action == "stop":
                    break
                heartbeat = float('-inf')
            time.sleep(0.25)
    finally:
        write_signed(root / "state.json", {**state, "status": "stopped", "updatedAt": time.time()}, token)


def request_action(root: Path, token: str, action: str, delivery: dict | None = None) -> dict:
    state = read_signed(root / "state.json", token)
    age = time.time() - state.get("updatedAt", 0)
    fresh = 0 <= age <= (65 if state.get("status") == "busy" else 8)
    if action == "status":
        return {**state, "reachable": fresh and state.get("status") in {"ready", "busy"}}
    if not fresh or state.get("status") != "ready":
        raise RuntimeError("helper_not_ready")
    # Serialize clients, including SSH processes from different sessions.
    lock = acquire_client_lock(root)
    try:
        identifier = secrets.token_hex(16)
        request = {"id": identifier, "instance": state["instance"], "action": action, "createdAt": time.time()}
        if action in {"native-send", "native-stop-prepare", "native-stop-commit", "native-resume-prepare", "native-resume-commit"}:
            request["delivery"] = delivery
        validate_request(request, state["instance"], time.time())
        write_signed(root / "request.json", request, token)
        deadline = time.monotonic() + (70 if action == "native-send" else 35)
        while time.monotonic() < deadline:
            try:
                result = read_signed(root / "response.json", token)
                if result.get("id") == identifier and result.get("instance") == state["instance"]:
                    if action == "diagnose" and "report" in result:
                        result = {**result, "composerInspection": inspect_composer(result["report"])}
                    return result
            except FileNotFoundError:
                pass
            time.sleep(0.2)
        raise RuntimeError("helper_response_timeout")
    finally:
        lock.close()


def acquire_client_lock(root: Path):
    # Same byte-lock protocol but a separate file; no worker-lock interference.
    import msvcrt
    path = root / "client.lock"
    if path.exists() or path.is_symlink():
        safe_entry(path)
    handle = path.open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        handle.close()
        raise RuntimeError("another_diagnostic_request_is_pending") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "status", "diagnose", "stop", "smoke-send"))
    parser.add_argument("--enable-smoke-send", action="store_true",
                        help="Opt in to one fixed-draft UI send attempt for this helper instance")
    parser.add_argument("--enable-native-send", action="store_true", help="Opt in to authenticated native text delivery")
    parser.add_argument("--enable-native-stop", action="store_true", help="Opt in to guarded native Desktop Stop")
    parser.add_argument("--enable-native-resume", action="store_true", help="Opt in to guarded native Desktop Continue")
    parser.add_argument("--enable-taskbar-activation", action="store_true", help="Allow one verified physical taskbar click when native text activation fails")
    args = parser.parse_args(argv)
    if (args.enable_smoke_send or args.enable_native_send or args.enable_native_stop or args.enable_native_resume or args.enable_taskbar_activation) and args.action != "serve":
        parser.error("send opt-ins apply only to serve")
    if args.enable_taskbar_activation and not args.enable_native_send:
        parser.error("taskbar activation requires native send")
    if sys.platform != "win32":
        parser.error("requires native Windows")
    try:
        token = windows_token(initialize=args.action == "serve")
        root = state_directory() / "desktop-diagnostics"
        if args.action == "serve":
            root.mkdir(exist_ok=True)
        safe_entry(root, directory=True)
        # Re-check the new child's inherited DACL before publishing any reports.
        windows_token()
        if args.action == "serve":
            serve(root, token, enable_smoke_send=args.enable_smoke_send, enable_native_send=args.enable_native_send, enable_native_stop=args.enable_native_stop, enable_native_resume=args.enable_native_resume, enable_taskbar_activation=args.enable_taskbar_activation)
        else:
            print(json.dumps(request_action(root, token, args.action), ensure_ascii=True))
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # No raw OS/script exceptions: paths or provider messages may be private.
        print("Desktop helper unavailable or request refused; check its desktop window.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
