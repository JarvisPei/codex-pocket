from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    tk = None
    

APP_TITLE = "移动 Codex 控制台"
APP_PORT = 3001
PROXY_PORT = 8080
PHONE_ACTIVITY_WINDOW_MINUTES = 10
LOCAL_PANEL_URL = f"http://127.0.0.1:{APP_PORT}"
APP_HEALTH_URL = f"{LOCAL_PANEL_URL}/health"
PROXY_HEALTH_URL = f"http://127.0.0.1:{PROXY_PORT}/health"
REMOTE_TARGET = f"http://127.0.0.1:{PROXY_PORT}"


def resolve_workspace() -> Path:
    candidates = []
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend([executable_dir, executable_dir.parent, executable_dir.parent.parent])
    else:
        source_dir = Path(__file__).resolve().parent
        candidates.extend([source_dir, source_dir.parent])

    for candidate in candidates:
        if (candidate / "scripts" / "start-mobile-codex-stack.ps1").exists():
            return candidate

    return Path(__file__).resolve().parent


WORKSPACE = resolve_workspace()
SCRIPTS_DIR = WORKSPACE / "scripts"
APP_STDERR_LOG = WORKSPACE / "tmp" / "logs" / "mobile-codex-app.stderr.log"
MOBILE_USER_AGENT = re.compile(r"android|iphone|ipad|mobile|ios|harmony", re.IGNORECASE)
MOBILE_OS = {"android", "ios"}
NGINX_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def resolve_tailscale_path() -> Path:
    configured = os.environ.get("MOBILE_CODEX_TAILSCALE")
    if configured:
        return Path(configured)
    return Path(r"C:\Program Files\Tailscale\tailscale.exe")


def resolve_ascii_alias_path() -> Path:
    configured = os.environ.get("MOBILE_CODEX_ASCII_ALIAS")
    if configured:
        return Path(configured)

    system_drive = os.environ.get("SystemDrive", "C:")
    return Path(system_drive) / "mobileCodexHelper_ascii"


ASCII_ALIAS_PATH = resolve_ascii_alias_path()
TAILSCALE = resolve_tailscale_path()
NGINX_ACCESS_LOG = ASCII_ALIAS_PATH / ".runtime" / "nginx" / "logs" / "mobile-codex.access.log"
NGINX_ERROR_LOG = ASCII_ALIAS_PATH / ".runtime" / "nginx" / "logs" / "mobile-codex.error.log"


def inspect_auth_db(path: Path) -> tuple[int, set[str]]:
    if not path.exists():
        return -1, set()

    try:
        connection = sqlite3.connect(path)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            score = 0
            if "users" in tables:
                score += 1
            if "trusted_devices" in tables:
                score += 3
            if "device_approval_requests" in tables:
                score += 3
            if "users" in tables:
                user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if user_count:
                    score += 2
            return score, tables
        finally:
            connection.close()
    except sqlite3.Error:
        return -1, set()


def resolve_auth_db_path() -> Path:
    candidates = []
    env_database_path = os.environ.get("DATABASE_PATH")
    if env_database_path:
        candidates.append(Path(env_database_path))

    candidates.extend(
        [
            WORKSPACE / "vendor" / "claudecodeui-1.25.2" / "server" / "database" / "auth.db",
            Path.home() / ".cloudcli" / "auth.db",
            Path.home() / ".codex" / "auth.db",
        ]
    )

    best_path = candidates[0]
    best_score = -1
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        score, _tables = inspect_auth_db(candidate)
        if score > best_score:
            best_path = candidate
            best_score = score

    return best_path


AUTH_DB_PATH = resolve_auth_db_path()


@dataclass
class StatusBlock:
    label: str
    ok: bool
    headline: str
    detail: str
    level: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ok": self.ok,
            "headline": self.headline,
            "detail": self.detail,
            "level": self.level,
        }


@dataclass
class ListenerInfo:
    port: int
    pid: int
    name: str
    path: str

    def summary(self) -> str:
        parts = [f"端口 {self.port}", f"PID {self.pid}"]
        if self.name:
            parts.append(self.name)
        return " | ".join(parts)


def ensure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except ValueError:
                pass


ensure_stdio_utf8()


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.year <= 1:
            return None
        return parsed
    except ValueError:
        return None


def format_datetime(value: str | None) -> str:
    dt = parse_datetime(value)
    if not dt:
        return "暂无"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def minutes_since(value: str | None) -> float | None:
    dt = parse_datetime(value)
    if not dt:
        return None
    return (now_local() - dt.astimezone()).total_seconds() / 60


def format_age_text(value: str | None) -> str:
    delta = minutes_since(value)
    if delta is None:
        return "时间未知"
    if delta < 1:
        return "刚刚"
    return f"{int(delta)} 分钟前"


def is_recent(value: str | None, minutes: int = PHONE_ACTIVITY_WINDOW_MINUTES) -> bool:
    delta = minutes_since(value)
    return delta is not None and delta <= minutes


def summarize_connection_error(message: str) -> str:
    if "10061" in message:
        return "端口未监听，服务未启动"
    return message


def subprocess_window_options() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def run_command(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        cwd=str(WORKSPACE),
        **subprocess_window_options(),
    )


def wait_for(predicate: Callable[[], bool], timeout: float, interval: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def powershell_file(script_name: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    script_path = SCRIPTS_DIR / script_name
    return run_command(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        timeout=timeout,
    )


def run_powershell_json(command: str, timeout: int = 12) -> Any | None:
    result = run_command(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def http_health(url: str, timeout: float = 2.5) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(200).decode("utf-8", errors="replace")
            return True, f"{response.status} {response.reason} | {body[:80]}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, summarize_connection_error(str(exc))


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.8) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def normalize_dns_name(value: str | None) -> str | None:
    if not value:
        return None
    return value[:-1] if value.endswith(".") else value


def parse_nginx_timestamp(value: str) -> str | None:
    match = re.match(
        r"^(?P<day>\d{2})/(?P<month>[A-Za-z]{3})/(?P<year>\d{4}):"
        r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}) (?P<offset>[+-]\d{4})$",
        value,
    )
    if not match:
        return None
    month = NGINX_MONTHS.get(match.group("month"))
    if month is None:
        return None
    offset = match.group("offset")
    iso = (
        f"{match.group('year')}-{month:02d}-{match.group('day')}T"
        f"{match.group('hour')}:{match.group('minute')}:{match.group('second')}"
        f"{offset[:3]}:{offset[3:]}"
    )
    try:
        return datetime.fromisoformat(iso).isoformat()
    except ValueError:
        return None


def get_listener_map(ports: list[int] | None = None) -> dict[int, ListenerInfo]:
    target_ports = ports or [APP_PORT, PROXY_PORT]
    ports_literal = ",".join(str(port) for port in target_ports)
    command = f"""
$ports = @({ports_literal})
$listeners = foreach ($item in Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {{ $ports -contains $_.LocalPort }}) {{
    $proc = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue
    [PSCustomObject]@{{
        port = [int]$item.LocalPort
        pid = [int]$item.OwningProcess
        name = if ($proc) {{ $proc.ProcessName }} else {{ '' }}
        path = if ($proc -and $proc.Path) {{ $proc.Path }} else {{ '' }}
    }}
}}
if ($listeners) {{
    $listeners | ConvertTo-Json -Compress
}}
"""
    data = run_powershell_json(command)
    if not data:
        return {}
    items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []

    listener_map: dict[int, ListenerInfo] = {}
    for item in items:
        try:
            port = int(item.get("port"))
            listener_map[port] = ListenerInfo(
                port=port,
                pid=int(item.get("pid")),
                name=str(item.get("name") or ""),
                path=str(item.get("path") or ""),
            )
        except (TypeError, ValueError):
            continue
    return listener_map


def describe_listener(listener: ListenerInfo | None) -> str:
    if not listener:
        return "端口未监听"
    return listener.summary()


def describe_service(health_ok: bool, health_detail: str, listener: ListenerInfo | None) -> str:
    listener_text = describe_listener(listener)
    if health_ok:
        return f"{listener_text} | {health_detail}" if listener else health_detail
    if listener:
        return f"{listener_text} | 健康检查未通过：{health_detail}"
    return health_detail


def normalize_remote_health_detail(detail: str) -> str:
    lowered = detail.lower()
    if "handshake operation timed out" in lowered or "timed out" in lowered:
        return "本机自检超时，手机端可能仍可访问"
    if "10061" in detail:
        return "远程入口未监听"
    return f"本机自检失败：{detail}"


def build_remote_block(remote: dict[str, Any], app_ok: bool, proxy_ok: bool) -> tuple[StatusBlock, dict[str, Any]]:
    if not remote["published"]:
        block = StatusBlock("远程发布", False, "未开启", "远程发布未开启", "error")
        return block, {"value": "未开启", "detail": block.detail, "level": "error"}

    if not app_ok or not proxy_ok:
        detail = f"{remote['url']} | 已发布，但本地服务未启动" if remote["url"] else "已发布，但本地服务未启动"
        block = StatusBlock("远程发布", False, "已发布，待服务启动", detail, "warning")
        return block, {"value": "已发布", "detail": detail, "level": "warning"}

    if remote["health_ok"]:
        detail = f"{remote['url']} | 远程健康正常" if remote["url"] else remote["detail"]
        block = StatusBlock("远程发布", True, "可访问", detail, "success")
        return block, {"value": "可访问", "detail": detail, "level": "success"}

    health_summary = normalize_remote_health_detail(remote["health_detail"])
    detail = f"{remote['url']} | {health_summary}" if remote["url"] else health_summary
    block = StatusBlock("远程发布", True, "已发布，待验证", detail, "warning")
    return block, {"value": "已发布", "detail": detail, "level": "warning"}


def load_tailscale_status() -> dict[str, Any]:
    if not TAILSCALE.exists():
        return {"ok": False, "error": f"Tailscale CLI 未找到：{TAILSCALE}"}
    result = run_command([str(TAILSCALE), "status", "--json"])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip() or "读取 Tailscale 状态失败"}
    try:
        return {"ok": True, "data": json.loads(result.stdout)}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Tailscale 返回的 JSON 无法解析: {exc}"}


def load_serve_status() -> dict[str, Any]:
    if not TAILSCALE.exists():
        return {"ok": False, "error": f"Tailscale CLI 未找到：{TAILSCALE}"}
    result = run_command([str(TAILSCALE), "serve", "status", "--json"])
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip() or "读取远程发布状态失败"}
    try:
        return {"ok": True, "data": json.loads(result.stdout)}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"远程发布状态 JSON 无法解析: {exc}"}


def build_remote_status(tailscale_status: dict[str, Any], serve_status: dict[str, Any]) -> dict[str, Any]:
    serve_data = serve_status.get("data") if serve_status.get("ok") else {}
    web_entries = list((serve_data or {}).get("Web", {}).items())
    if not web_entries:
        return {
            "published": False,
            "url": None,
            "target": None,
            "detail": "远程发布未开启",
            "health_ok": False,
            "health_detail": "未执行远程健康检查",
        }

    host_and_port, config = web_entries[0]
    host = str(host_and_port).replace(":443", "")
    target = (((config or {}).get("Handlers") or {}).get("/") or {}).get("Proxy")
    tailscale_data = tailscale_status.get("data") if tailscale_status.get("ok") else {}
    fallback_dns = normalize_dns_name((((tailscale_data or {}).get("Self") or {}).get("DNSName")))
    url = f"https://{host or fallback_dns}" if (host or fallback_dns) else None
    health_ok = False
    health_detail = "未执行远程健康检查"
    if url:
        health_ok, health_detail = http_health(f"{url}/health", timeout=2.5)
    return {
        "published": True,
        "url": url,
        "target": target,
        "detail": f"已发布到 {target}" if target else "远程发布已开启",
        "health_ok": health_ok,
        "health_detail": health_detail,
    }


def pick_mobile_display_name(peer: dict[str, Any]) -> str:
    host_name = str(peer.get("HostName") or "").strip()
    dns_name = normalize_dns_name(peer.get("DNSName")) or ""
    tail_ip = (peer.get("TailscaleIPs") or [""])[0]
    if host_name and host_name.lower() != "localhost":
        return host_name
    if dns_name:
        return dns_name
    if tail_ip:
        return tail_ip
    return "未命名手机"


def extract_mobile_peers(tailscale_status: dict[str, Any]) -> list[dict[str, Any]]:
    if not tailscale_status.get("ok"):
        return []
    peers = []
    for peer_id, peer in (tailscale_status["data"].get("Peer") or {}).items():
        os_name = str(peer.get("OS") or "").lower()
        if os_name not in MOBILE_OS:
            continue
        peers.append(
            {
                "id": peer_id,
                "display_name": pick_mobile_display_name(peer),
                "host_name": peer.get("HostName") or "",
                "dns_name": normalize_dns_name(peer.get("DNSName")) or "",
                "os": os_name,
                "online": bool(peer.get("Online")),
                "active": bool(peer.get("Active")),
                "last_handshake": peer.get("LastHandshake") or "",
                "last_seen": peer.get("LastSeen") or "",
                "tail_ip": (peer.get("TailscaleIPs") or [""])[0],
                "relay": peer.get("Relay") or "",
            }
        )
    peers.sort(key=lambda item: (not item["online"], not item["active"], item["display_name"]))
    return peers


def tail_lines(file_path: Path, max_lines: int = 200) -> list[str]:
    if not file_path.exists():
        return []
    lines: deque[str] = deque(maxlen=max_lines)
    with file_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.rstrip()
            if stripped:
                lines.append(stripped)
    return list(lines)


def recent_mobile_requests(limit: int = 6) -> list[dict[str, Any]]:
    pattern = re.compile(
        r'^(?P<ip>\S+) - \S+ \[(?P<time>[^\]]+)\] "(?P<method>\S+) (?P<path>\S+) [^"]+" '
        r'(?P<status>\d{3}) (?P<bytes>\d+) "(?P<referrer>[^"]*)" "(?P<ua>.*)"$'
    )
    parsed: list[dict[str, Any]] = []
    for line in tail_lines(NGINX_ACCESS_LOG):
        match = pattern.match(line)
        if not match:
            continue
        user_agent = match.group("ua")
        if not MOBILE_USER_AGENT.search(user_agent):
            continue
        parsed.append(
            {
                "ip": match.group("ip"),
                "time": parse_nginx_timestamp(match.group("time")) or match.group("time"),
                "method": match.group("method"),
                "path": match.group("path"),
                "status": int(match.group("status")),
                "user_agent": user_agent,
            }
        )
    parsed.sort(key=lambda item: str(item["time"]), reverse=True)

    unique: list[dict[str, Any]] = []
    seen = set()
    for item in parsed:
        key = (item["time"], item["method"], item["path"], item["status"], item["user_agent"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def tail_error_lines(limit: int = 8) -> list[str]:
    combined = []
    for label, path in (("后端", APP_STDERR_LOG), ("nginx", NGINX_ERROR_LOG)):
        lines = tail_lines(path, max_lines=40)
        interesting = [line for line in lines if re.search(r"error|warn|fail|502|trace|deprecat", line, re.I)]
        if interesting:
            combined.extend([f"[{label}] {line}" for line in interesting[-4:]])
    return combined[-limit:]


def open_auth_db() -> sqlite3.Connection:
    connection = sqlite3.connect(AUTH_DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def list_pending_device_approvals(limit: int = 20) -> list[dict[str, Any]]:
    if not AUTH_DB_PATH.exists():
        return []

    try:
        with open_auth_db() as connection:
            rows = connection.execute(
                """
                SELECT
                    dar.request_token,
                    dar.device_id,
                    dar.device_name,
                    dar.platform,
                    dar.app_type,
                    dar.requested_ip,
                    dar.requested_user_agent,
                    dar.created_at,
                    u.username
                FROM device_approval_requests dar
                LEFT JOIN users u ON u.id = dar.user_id
                WHERE dar.status = 'pending'
                ORDER BY dar.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []

    approvals = []
    for row in rows:
        item = dict(row)
        item["display_name"] = item.get("device_name") or item.get("device_id") or "未命名设备"
        approvals.append(item)
    return approvals


def list_approved_devices(limit: int = 50) -> list[dict[str, Any]]:
    if not AUTH_DB_PATH.exists():
        return []

    try:
        with open_auth_db() as connection:
            rows = connection.execute(
                """
                SELECT
                    td.device_id,
                    td.device_name,
                    td.platform,
                    td.app_type,
                    td.first_approved_at,
                    td.last_seen,
                    td.last_login,
                    td.last_ip,
                    u.username
                FROM trusted_devices td
                LEFT JOIN users u ON u.id = td.user_id
                WHERE td.is_active = 1
                ORDER BY COALESCE(td.last_login, td.last_seen, td.first_approved_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []

    devices = []
    for row in rows:
        item = dict(row)
        item["display_name"] = item.get("device_name") or item.get("device_id") or "未命名设备"
        devices.append(item)
    return devices


def resolve_device_request(request_token: str, approved: bool) -> bool:
    if not AUTH_DB_PATH.exists():
        return False

    try:
        with open_auth_db() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM device_approval_requests
                WHERE request_token = ? AND status = 'pending'
                LIMIT 1
                """,
                (request_token,),
            ).fetchone()
            if row is None:
                return False

            if approved:
                connection.execute(
                    """
                    INSERT INTO trusted_devices (
                        user_id,
                        device_id,
                        device_name,
                        platform,
                        app_type,
                        first_approved_at,
                        last_seen,
                        last_login,
                        last_ip,
                        last_user_agent,
                        is_active
                    )
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, 1)
                    ON CONFLICT(user_id, device_id)
                    DO UPDATE SET
                        device_name = excluded.device_name,
                        platform = excluded.platform,
                        app_type = excluded.app_type,
                        last_seen = CURRENT_TIMESTAMP,
                        last_login = CURRENT_TIMESTAMP,
                        last_ip = excluded.last_ip,
                        last_user_agent = excluded.last_user_agent,
                        is_active = 1
                    """,
                    (
                        row["user_id"],
                        row["device_id"],
                        row["device_name"],
                        row["platform"],
                        row["app_type"],
                        row["requested_ip"],
                        row["requested_user_agent"],
                    ),
                )

            connection.execute(
                f"""
                UPDATE device_approval_requests
                SET
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    {"approved_at" if approved else "rejected_at"} = CURRENT_TIMESTAMP,
                    resolved_note = ?
                WHERE request_token = ? AND status = 'pending'
                """,
                ("approved" if approved else "rejected", "Desktop tool action", request_token),
            )
            connection.commit()
        return True
    except sqlite3.OperationalError:
        return False


def collect_status() -> dict[str, Any]:
    listener_map = get_listener_map()
    app_listener = listener_map.get(APP_PORT)
    proxy_listener = listener_map.get(PROXY_PORT)

    app_ok, app_health_detail = http_health(APP_HEALTH_URL)
    proxy_ok, proxy_health_detail = http_health(PROXY_HEALTH_URL)
    app_detail = describe_service(app_ok, app_health_detail, app_listener)
    proxy_detail = describe_service(proxy_ok, proxy_health_detail, proxy_listener)
    tailscale_status = load_tailscale_status()
    serve_status = load_serve_status()
    remote = build_remote_status(tailscale_status, serve_status)
    peers = extract_mobile_peers(tailscale_status)
    approved_devices = list_approved_devices()
    pending_approvals = list_pending_device_approvals()
    mobile_online = sum(1 for peer in peers if peer["online"])
    recent_requests = recent_mobile_requests()
    latest_request_time = recent_requests[0]["time"] if recent_requests else None
    recent_phone_activity = is_recent(latest_request_time)
    active_phone_websockets = sum(
        1 for request in recent_requests if request["path"] == "/ws" and request["status"] == 101 and is_recent(request["time"])
    )

    tailscale_data = tailscale_status.get("data") if tailscale_status.get("ok") else {}
    backend_state = (tailscale_data.get("BackendState") if tailscale_data else None) or "不可用"
    dns_name = normalize_dns_name((((tailscale_data or {}).get("Self") or {}).get("DNSName")))
    current_device = peers[0]["display_name"] if peers else "暂未发现手机设备"
    latest_activity_summary = (
        f"{recent_requests[0]['path']} · {format_datetime(latest_request_time)}"
        if recent_requests
        else "暂无手机访问记录"
    )
    remote_block, remote_summary = build_remote_block(remote, app_ok, proxy_ok)
    remote_available = remote["published"] and remote["health_ok"] and app_ok and proxy_ok

    blocks = [
        StatusBlock("PC 应用服务", app_ok, "运行中" if app_ok else "未启动", app_detail, "success" if app_ok else "error"),
        StatusBlock("nginx 代理", proxy_ok, "运行中" if proxy_ok else "未启动", proxy_detail, "success" if proxy_ok else "error"),
        StatusBlock(
            "Tailscale",
            bool(tailscale_status.get("ok") and backend_state == "Running"),
            "运行中" if bool(tailscale_status.get("ok") and backend_state == "Running") else backend_state,
            dns_name or tailscale_status.get("error", "未获取到域名"),
            "success" if bool(tailscale_status.get("ok") and backend_state == "Running") else "error",
        ),
        remote_block,
        StatusBlock(
            "手机连接状态",
            mobile_online > 0,
            f"{mobile_online}/{len(peers)} 在线",
            current_device,
            "success" if mobile_online > 0 else "error",
        ),
        StatusBlock(
            "手机最近访问",
            recent_phone_activity,
            "最近 10 分钟内有访问" if recent_phone_activity else "最近 10 分钟内无访问",
            latest_activity_summary,
            "success" if recent_phone_activity else "error",
        ),
    ]

    return {
        "checked_at": now_local().strftime("%Y-%m-%d %H:%M:%S"),
        "local_url": LOCAL_PANEL_URL,
        "remote_url": remote["url"],
        "blocks": [block.to_dict() for block in blocks],
        "mobile_peers": peers,
        "approved_devices": approved_devices,
        "pending_device_approvals": pending_approvals,
        "recent_mobile_requests": recent_requests,
        "diagnostics": [f"[本地] 认证数据库：{AUTH_DB_PATH}"] + tail_error_lines(),
        "summary": {
            "app_running": app_ok,
            "nginx_running": proxy_ok,
            "tailscale_running": bool(tailscale_status.get("ok") and backend_state == "Running"),
            "remote_enabled": remote["published"],
            "remote_reachable": remote["health_ok"],
            "remote_available": remote_available,
            "remote_level": remote_summary["level"],
            "remote_value": remote_summary["value"],
            "remote_detail": remote_summary["detail"],
            "approved_devices": len(approved_devices),
            "pending_approvals": len(pending_approvals),
            "mobile_online": mobile_online,
            "mobile_total": len(peers),
            "recent_phone_requests": len(recent_requests),
            "recent_phone_websockets": active_phone_websockets,
            "recent_phone_activity": recent_phone_activity,
            "healthy_local_services": int(app_ok) + int(proxy_ok),
            "listener_summary": {
                str(APP_PORT): app_listener.summary() if app_listener else "端口未监听",
                str(PROXY_PORT): proxy_listener.summary() if proxy_listener else "端口未监听",
            },
        },
    }


def stack_is_running() -> bool:
    app_ok, _ = http_health(APP_HEALTH_URL, timeout=1.5)
    proxy_ok, _ = http_health(PROXY_HEALTH_URL, timeout=1.5)
    return app_ok and proxy_ok


def remote_publish_is_enabled() -> bool:
    tailscale_status = load_tailscale_status()
    serve_status = load_serve_status()
    remote = build_remote_status(tailscale_status, serve_status)
    return bool(remote["published"])


def stack_is_stopped() -> bool:
    if is_port_open(APP_PORT, timeout=0.4) or is_port_open(PROXY_PORT, timeout=0.4):
        return False
    app_ok, _ = http_health(APP_HEALTH_URL, timeout=0.8)
    proxy_ok, _ = http_health(PROXY_HEALTH_URL, timeout=0.8)
    return not app_ok and not proxy_ok


def wait_for_remote_reachable(timeout: float = 8.0) -> bool:
    def _remote_ok() -> bool:
        status = collect_status()
        return bool(status["summary"]["remote_reachable"])

    return wait_for(_remote_ok, timeout=timeout, interval=1.0)


def perform_action(action: str) -> str:
    if action == "start":
        result = powershell_file("start-mobile-codex-stack.ps1", timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "启动整套服务失败")
        if not wait_for(stack_is_running, timeout=20, interval=1.5):
            listeners = get_listener_map()
            detail = "；".join(describe_listener(listeners.get(port)) for port in (APP_PORT, PROXY_PORT))
            raise RuntimeError(f"服务启动命令已执行，但本地健康检查仍未通过：{detail}")
        return "整套服务已启动"

    if action == "stop":
        result = powershell_file("stop-mobile-codex-stack.ps1", timeout=20)
        if TAILSCALE.exists():
            run_command([str(TAILSCALE), "serve", "reset"], timeout=10)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "停止整套服务失败")
        if not wait_for(stack_is_stopped, timeout=15, interval=1.0):
            powershell_file("stop-mobile-codex-stack.ps1", timeout=20)
            if not wait_for(stack_is_stopped, timeout=10, interval=1.0):
                listeners = get_listener_map()
                remaining = [listeners.get(port) for port in (APP_PORT, PROXY_PORT) if listeners.get(port)]
                detail = "；".join(item.summary() for item in remaining) if remaining else "端口探测仍显示服务未完全退出"
                raise RuntimeError(f"停止命令已执行，但本地端口仍未完全释放：{detail}")
        return "整套服务已停止"

    if action == "enable_remote":
        result = run_command([str(TAILSCALE), "serve", "--bg", REMOTE_TARGET], timeout=20)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "开启远程发布失败")
        if not wait_for(remote_publish_is_enabled, timeout=12, interval=1.0):
            raise RuntimeError("远程发布命令已执行，但 Tailscale Serve 状态仍未生效")
        if wait_for_remote_reachable(timeout=8):
            return "远程发布已开启，远程地址可访问"
        return "远程发布已开启，若手机端暂时打不开请等待几秒后刷新"

    if action == "disable_remote":
        result = run_command([str(TAILSCALE), "serve", "reset"], timeout=10)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "关闭远程发布失败")
        if not wait_for(lambda: not remote_publish_is_enabled(), timeout=8, interval=0.8):
            raise RuntimeError("远程发布关闭命令已执行，但 Serve 状态仍存在")
        return "远程发布已关闭"

    if action == "open_local":
        webbrowser.open(LOCAL_PANEL_URL)
        return "已打开本地控制面板"

    raise ValueError(f"不支持的操作：{action}")


class ControlApp:
    REFRESH_MS = 15000

    def __init__(self) -> None:
        if tk is None:
            raise RuntimeError("当前 Python 环境缺少 tkinter，无法显示桌面界面")

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1260x900")
        self.root.minsize(1120, 800)
        self.root.configure(bg="#eef3f8")

        self.status_text = tk.StringVar(value="就绪")
        self.last_refresh_text = tk.StringVar(value="尚未刷新")
        self.local_url_text = tk.StringVar(value=f"本地面板：{LOCAL_PANEL_URL}")
        self.remote_url_text = tk.StringVar(value="远程地址：未开启")
        self.block_labels: list[dict[str, tk.Label]] = []
        self.metric_widgets: dict[str, dict[str, Any]] = {}
        self.pending_approval_items: list[dict[str, Any]] = []
        self.selected_approval_token: str | None = None
        self._busy = False

        self._build_ui()
        self.refresh_status()

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, bg="#eef3f8")
        container.pack(fill="both", expand=True, padx=14, pady=14)

        title = tk.Label(
            container,
            text=APP_TITLE,
            font=("Microsoft YaHei UI", 22, "bold"),
            bg="#eef3f8",
            fg="#112033",
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            container,
            text="用于在电脑端一键控制 Codex 服务，并集中查看本机服务、远程发布与手机连接状态。",
            font=("Microsoft YaHei UI", 10),
            bg="#eef3f8",
            fg="#4f6072",
        )
        subtitle.pack(anchor="w", pady=(4, 10))

        endpoint_bar = tk.Frame(container, bg="#dbe8f6", bd=1, relief="solid")
        endpoint_bar.pack(fill="x", pady=(0, 12))
        tk.Label(
            endpoint_bar,
            textvariable=self.local_url_text,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="#dbe8f6",
            fg="#17324d",
        ).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(
            endpoint_bar,
            textvariable=self.remote_url_text,
            font=("Microsoft YaHei UI", 10),
            bg="#dbe8f6",
            fg="#17324d",
        ).pack(anchor="w", padx=12, pady=(0, 10))

        summary_strip = tk.Frame(container, bg="#eef3f8")
        summary_strip.pack(fill="x", pady=(0, 12))
        for index, (key, title_text, hint) in enumerate(
            [
                ("services", "本机服务", "健康服务数量"),
                ("remote", "远程发布", "Tailscale 私有地址"),
                ("mobile", "手机在线", "手机设备在线情况"),
                ("whitelist", "设备白名单", "已批准设备数量"),
                ("approvals", "待审批", "首次登录待电脑授权"),
                ("activity", "最近访问", "近 10 分钟活跃度"),
            ]
        ):
            card = tk.Frame(summary_strip, bg="white", bd=1, relief="solid")
            card.grid(row=0, column=index, padx=6, sticky="nsew")
            summary_strip.grid_columnconfigure(index, weight=1)
            tk.Label(
                card,
                text=title_text,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg="white",
                fg="#17324d",
            ).pack(anchor="w", padx=12, pady=(12, 2))
            value_label = tk.Label(card, text="--", font=("Microsoft YaHei UI", 18, "bold"), bg="white", fg="#0f9d58")
            value_label.pack(anchor="w", padx=12)
            detail_label = tk.Label(
                card,
                text=hint,
                font=("Microsoft YaHei UI", 9),
                bg="white",
                fg="#5a6c7f",
                wraplength=180,
                justify="left",
            )
            detail_label.pack(anchor="w", padx=12, pady=(2, 12))
            self.metric_widgets[key] = {"frame": card, "value": value_label, "detail": detail_label}

        actions = tk.Frame(container, bg="#eef3f8")
        actions.pack(fill="x", pady=(0, 12))

        buttons = [
            ("刷新状态", "#1f6feb", lambda: self.run_background("正在刷新状态...", self._refresh_action)),
            ("启动服务", "#0f9d58", lambda: self.run_background("正在启动整套服务...", lambda: self._action_and_refresh("start"))),
            ("停止服务", "#d93025", lambda: self.run_background("正在停止整套服务...", lambda: self._action_and_refresh("stop"))),
            ("开启远程发布", "#0b7285", lambda: self.run_background("正在开启远程发布...", lambda: self._action_and_refresh("enable_remote"))),
            ("关闭远程发布", "#b26a00", lambda: self.run_background("正在关闭远程发布...", lambda: self._action_and_refresh("disable_remote"))),
            ("打开本地面板", "#5f3dc4", lambda: self.run_background("正在打开本地面板...", lambda: perform_action("open_local"))),
        ]
        for text, color, callback in buttons:
            tk.Button(
                actions,
                text=text,
                command=callback,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=color,
                fg="white",
                activebackground=color,
                activeforeground="white",
                relief="flat",
                padx=12,
                pady=8,
                cursor="hand2",
            ).pack(side="left", padx=(0, 8))

        info_row = tk.Frame(container, bg="#eef3f8")
        info_row.pack(fill="x", pady=(0, 12))
        tk.Label(
            info_row,
            textvariable=self.status_text,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="#eef3f8",
            fg="#112033",
        ).pack(side="left")
        tk.Label(
            info_row,
            textvariable=self.last_refresh_text,
            font=("Microsoft YaHei UI", 10),
            bg="#eef3f8",
            fg="#4f6072",
        ).pack(side="right")

        grid = tk.Frame(container, bg="#eef3f8")
        grid.pack(fill="x", pady=(0, 12))
        for index in range(6):
            frame = tk.Frame(grid, bg="white", bd=1, relief="solid", highlightthickness=0)
            row, col = divmod(index, 3)
            frame.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            grid.grid_columnconfigure(col, weight=1)

            title_label = tk.Label(frame, text="--", font=("Microsoft YaHei UI", 11, "bold"), bg="white", fg="#10243d", anchor="w")
            title_label.pack(fill="x", padx=12, pady=(12, 4))
            state_label = tk.Label(frame, text="--", font=("Microsoft YaHei UI", 16, "bold"), bg="white", fg="#4f6072", anchor="w")
            state_label.pack(fill="x", padx=12)
            detail_label = tk.Label(
                frame,
                text="--",
                font=("Microsoft YaHei UI", 9),
                bg="white",
                fg="#4f6072",
                justify="left",
                anchor="w",
                wraplength=330,
            )
            detail_label.pack(fill="x", padx=12, pady=(4, 12))
            self.block_labels.append({"frame": frame, "title": title_label, "state": state_label, "detail": detail_label})

        approval_frame = tk.Frame(container, bg="white", bd=1, relief="solid")
        approval_frame.pack(fill="x", pady=(0, 12))
        tk.Label(
            approval_frame,
            text="首次登录电脑授权",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg="white",
            fg="#10243d",
        ).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(
            approval_frame,
            text="新设备首次登录会进入待审批列表。请在电脑上核对设备信息后，再决定是否加入白名单。",
            font=("Microsoft YaHei UI", 9),
            bg="white",
            fg="#5a6c7f",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        approval_actions = tk.Frame(approval_frame, bg="white")
        approval_actions.pack(fill="x", padx=12, pady=(0, 10))
        for text, color, callback in [
            ("刷新审批", "#1f6feb", lambda: self.run_background("正在刷新审批列表...", self._refresh_action)),
            ("批准所选", "#0f9d58", lambda: self._resolve_selected_request(True)),
            ("拒绝所选", "#d93025", lambda: self._resolve_selected_request(False)),
        ]:
            tk.Button(
                approval_actions,
                text=text,
                command=callback,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=color,
                fg="white",
                activebackground=color,
                activeforeground="white",
                relief="flat",
                padx=12,
                pady=7,
                cursor="hand2",
            ).pack(side="left", padx=(0, 8))

        approval_body = tk.Frame(approval_frame, bg="white")
        approval_body.pack(fill="x", padx=12, pady=(0, 12))

        approval_list_frame = tk.Frame(approval_body, bg="white")
        approval_list_frame.pack(side="left", fill="both", expand=False)
        tk.Label(
            approval_list_frame,
            text="待审批设备",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="white",
            fg="#17324d",
        ).pack(anchor="w", pady=(0, 6))
        approval_list_inner = tk.Frame(approval_list_frame, bg="white")
        approval_list_inner.pack(fill="both", expand=True)
        approval_scroll = tk.Scrollbar(approval_list_inner)
        approval_scroll.pack(side="right", fill="y")
        self.approval_listbox = tk.Listbox(
            approval_list_inner,
            width=42,
            height=8,
            exportselection=False,
            font=("Microsoft YaHei UI", 10),
            bg="#f7f9fc",
            fg="#16263a",
            relief="flat",
            activestyle="none",
            yscrollcommand=approval_scroll.set,
        )
        self.approval_listbox.pack(side="left", fill="both", expand=True)
        self.approval_listbox.bind("<<ListboxSelect>>", self._on_approval_selected)
        approval_scroll.config(command=self.approval_listbox.yview)

        approval_detail_frame = tk.Frame(approval_body, bg="white")
        approval_detail_frame.pack(side="left", fill="both", expand=True, padx=(12, 0))
        tk.Label(
            approval_detail_frame,
            text="设备详情",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="white",
            fg="#17324d",
        ).pack(anchor="w", pady=(0, 6))
        self.approval_detail_text = self._build_text_panel_body(approval_detail_frame, height=8)

        bottom = tk.PanedWindow(container, orient="horizontal", bg="#eef3f8", sashrelief="flat")
        bottom.pack(fill="both", expand=True)

        self.devices_text = self._build_text_panel(bottom, "手机在线设备")
        self.whitelist_text = self._build_text_panel(bottom, "设备白名单")
        self.requests_text = self._build_text_panel(bottom, "最近手机访问")
        self.diagnostics_text = self._build_text_panel(bottom, "诊断信息")

    def _build_text_panel(self, parent: tk.PanedWindow, title: str) -> tk.Text:
        frame = tk.Frame(parent, bg="white", bd=1, relief="solid")
        parent.add(frame, stretch="always")

        tk.Label(frame, text=title, font=("Microsoft YaHei UI", 11, "bold"), bg="white", fg="#10243d").pack(anchor="w", padx=10, pady=(10, 6))
        return self._build_text_panel_body(frame)

    def _build_text_panel_body(self, parent: tk.Frame, height: int = 18) -> tk.Text:
        text_frame = tk.Frame(parent, bg="white")
        text_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(
            text_frame,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            height=height,
            bg="#f7f9fc",
            fg="#16263a",
            relief="flat",
            yscrollcommand=scrollbar.set,
        )
        text.pack(fill="both", expand=True)
        scrollbar.config(command=text.yview)
        return text

    def _render_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _on_approval_selected(self, _event: Any = None) -> None:
        selection = self.approval_listbox.curselection()
        if not selection:
            self.selected_approval_token = None
            self._render_text(self.approval_detail_text, "请选择一条待审批设备，查看详情并执行批准或拒绝。")
            return

        index = selection[0]
        if index >= len(self.pending_approval_items):
            self.selected_approval_token = None
            self._render_text(self.approval_detail_text, "当前选择的设备信息已失效，请先刷新。")
            return

        item = self.pending_approval_items[index]
        self.selected_approval_token = str(item["request_token"])
        self._render_pending_approval_detail()

    def _render_pending_approval_list(self, items: list[dict[str, Any]]) -> None:
        previous_token = self.selected_approval_token
        self.pending_approval_items = items

        self.approval_listbox.delete(0, "end")
        for item in items:
            line = (
                f"{item.get('username') or '未知账号'} | "
                f"{item.get('display_name') or '未命名设备'} | "
                f"{format_age_text(item.get('created_at'))}"
            )
            self.approval_listbox.insert("end", line)

        if not items:
            self.selected_approval_token = None
            self._render_text(self.approval_detail_text, "当前没有待审批设备。")
            return

        selected_index = next(
            (index for index, item in enumerate(items) if str(item["request_token"]) == previous_token),
            0,
        )
        self.approval_listbox.selection_clear(0, "end")
        self.approval_listbox.selection_set(selected_index)
        self.approval_listbox.activate(selected_index)
        self.selected_approval_token = str(items[selected_index]["request_token"])
        self._render_pending_approval_detail()

    def _render_pending_approval_detail(self) -> None:
        if not self.selected_approval_token:
            self._render_text(self.approval_detail_text, "请选择一条待审批设备，查看详情并执行批准或拒绝。")
            return

        item = next(
            (entry for entry in self.pending_approval_items if str(entry["request_token"]) == self.selected_approval_token),
            None,
        )
        if item is None:
            self._render_text(self.approval_detail_text, "当前选择的设备信息已失效，请先刷新。")
            return

        content = "\n".join(
            [
                f"账号：{item.get('username') or '未知'}",
                f"设备名称：{item.get('display_name') or '未命名设备'}",
                f"设备 ID：{item.get('device_id') or '暂无'}",
                f"平台：{item.get('platform') or '暂无'}",
                f"客户端类型：{item.get('app_type') or '暂无'}",
                f"请求 IP：{item.get('requested_ip') or '暂无'}",
                f"申请时间：{format_datetime(item.get('created_at'))}",
                "",
                "请求 UA：",
                item.get("requested_user_agent") or "暂无",
                "",
                "确认无误后，可在上方点击“批准所选”加入白名单。",
            ]
        )
        self._render_text(self.approval_detail_text, content)

    def _resolve_selected_request(self, approved: bool) -> None:
        if not self.selected_approval_token:
            self.status_text.set("请先选择一条待审批设备。")
            return

        action_text = "批准" if approved else "拒绝"
        token = self.selected_approval_token

        def task() -> str:
            current = next(
                (entry for entry in self.pending_approval_items if str(entry["request_token"]) == token),
                None,
            )
            if not current:
                raise RuntimeError("待审批设备已不存在，请先刷新。")
            if not resolve_device_request(token, approved):
                raise RuntimeError("审批未生效，请刷新后重试。")

            status = collect_status()
            self.root.after(0, lambda: self.apply_status(status))
            device_name = current.get("display_name") or current.get("device_id") or "该设备"
            return f"已{action_text}设备：{device_name}"

        self.run_background(f"正在{action_text}所选设备...", task)

    @staticmethod
    def _level_theme(level: str) -> tuple[str, str, str]:
        if level == "success":
            return "#ecfff3", "#0f9d58", "#0f9d58"
        if level == "warning":
            return "#fff7e8", "#b26a00", "#b26a00"
        return "#fff2f0", "#d93025", "#d93025"

    def _refresh_action(self) -> str:
        status = collect_status()
        self.root.after(0, lambda: self.apply_status(status))
        return "状态已刷新"

    def _action_and_refresh(self, action: str) -> str:
        message = perform_action(action)
        status = collect_status()
        self.root.after(0, lambda: self.apply_status(status))
        return message

    def run_background(
        self,
        pending_message: str,
        task: Callable[[], str],
        skip_if_busy: bool = False,
        show_pending: bool = True,
    ) -> None:
        if self._busy:
            if not skip_if_busy:
                self.status_text.set("已有任务进行中，请稍候...")
            return

        self._busy = True
        if show_pending:
            self.status_text.set(pending_message)

        def worker() -> None:
            try:
                message = task()
                self.root.after(0, lambda: self.status_text.set(message))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.status_text.set(f"操作失败：{exc}"))
            finally:
                self.root.after(0, self._mark_idle)

        threading.Thread(target=worker, daemon=True).start()

    def _mark_idle(self) -> None:
        self._busy = False

    def refresh_status(self) -> None:
        self.run_background("正在刷新状态...", self._refresh_action, skip_if_busy=True, show_pending=False)
        self.root.after(self.REFRESH_MS, self.refresh_status)

    def apply_status(self, status: dict[str, Any]) -> None:
        summary = status["summary"]
        self.last_refresh_text.set(f"最近刷新：{status['checked_at']}")
        self.local_url_text.set(f"本地面板：{status['local_url']}")
        self.remote_url_text.set(f"远程地址：{status['remote_url'] or '未开启'}")

        metric_values = {
            "services": (
                f"{summary['healthy_local_services']}/2 正常",
                f"3001：{summary['listener_summary'][str(APP_PORT)]} | 8080：{summary['listener_summary'][str(PROXY_PORT)]}",
                "success" if summary["healthy_local_services"] == 2 else "error",
            ),
            "remote": (
                summary["remote_value"],
                summary["remote_detail"],
                summary["remote_level"],
            ),
            "mobile": (
                f"{summary['mobile_online']}/{summary['mobile_total']}",
                "至少有一台手机在线" if summary["mobile_online"] else "当前没有手机在线",
                "success" if summary["mobile_online"] > 0 else "error",
            ),
            "whitelist": (
                f"{summary['approved_devices']} 台",
                "已加入白名单的登录设备",
                "success" if summary["approved_devices"] > 0 else "warning",
            ),
            "approvals": (
                f"{summary['pending_approvals']} 条",
                "新设备首次登录需在电脑端批准",
                "warning" if summary["pending_approvals"] > 0 else "success",
            ),
            "activity": (
                f"{summary['recent_phone_requests']} 条",
                f"WebSocket {summary['recent_phone_websockets']} 条 | {'最近有活跃访问' if summary['recent_phone_activity'] else '最近无活跃访问'}",
                "success" if summary["recent_phone_activity"] else "error",
            ),
        }
        for key, (value, detail, level) in metric_values.items():
            widgets = self.metric_widgets[key]
            background, color, _ = self._level_theme(level)
            widgets["frame"].configure(bg=background)
            widgets["value"].configure(text=value, fg=color, bg=background)
            for child in widgets["frame"].winfo_children():
                child.configure(bg=background)
            widgets["detail"].configure(text=detail, bg=background)

        for labels, block in zip(self.block_labels, status["blocks"], strict=False):
            frame_color, text_color, _ = self._level_theme(block.get("level", "error"))
            labels["frame"].configure(bg=frame_color)
            labels["title"].configure(text=block["label"], bg=frame_color)
            labels["state"].configure(text=block["headline"], fg=text_color, bg=frame_color)
            labels["detail"].configure(text=block["detail"], bg=frame_color)

        devices_lines = []
        for peer in status["mobile_peers"]:
            devices_lines.append(
                f"{peer['display_name']}\n"
                f"  系统：{peer['os']}\n"
                f"  在线：{'是' if peer['online'] else '否'}\n"
                f"  活跃：{'是' if peer['active'] else '否'}\n"
                f"  Tail IP：{peer['tail_ip'] or '暂无'}\n"
                f"  最近握手：{format_datetime(peer['last_handshake'])}\n"
                f"  最近出现：{format_datetime(peer['last_seen'])}\n"
                f"  中继区域：{peer['relay'] or '暂无'}\n"
            )
        self._render_text(self.devices_text, "\n".join(devices_lines) if devices_lines else "暂未检测到手机设备。")

        whitelist_lines = []
        for item in status["approved_devices"]:
            whitelist_lines.append(
                f"{item['display_name']}\n"
                f"  账号：{item.get('username') or '未知'}\n"
                f"  设备 ID：{item.get('device_id') or '暂无'}\n"
                f"  平台：{item.get('platform') or '暂无'}\n"
                f"  类型：{item.get('app_type') or '暂无'}\n"
                f"  首次批准：{format_datetime(item.get('first_approved_at'))}\n"
                f"  最近登录：{format_datetime(item.get('last_login'))}\n"
                f"  最近来源 IP：{item.get('last_ip') or '暂无'}\n"
            )
        self._render_text(self.whitelist_text, "\n".join(whitelist_lines) if whitelist_lines else "当前设备白名单为空。")

        request_lines = []
        for item in status["recent_mobile_requests"]:
            request_lines.append(
                f"{format_datetime(item['time'])}（{format_age_text(item['time'])}）\n"
                f"  请求：{item['method']} {item['path']}\n"
                f"  状态码：{item['status']}  来源 IP：{item['ip']}\n"
                f"  UA：{item['user_agent']}\n"
            )
        self._render_text(self.requests_text, "\n".join(request_lines) if request_lines else "最近没有检测到手机访问。")
        self._render_text(self.diagnostics_text, "\n".join(status["diagnostics"]) if status["diagnostics"] else "最近没有诊断告警。")
        self._render_pending_approval_list(status["pending_device_approvals"])

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="移动 Codex 本地控制工具")
    parser.add_argument("--json", action="store_true", help="输出当前状态 JSON 后退出")
    parser.add_argument(
        "--action",
        choices=["start", "stop", "enable_remote", "disable_remote", "open_local"],
        help="执行一次控制操作后退出",
    )
    args = parser.parse_args()

    if args.action:
        message = perform_action(args.action)
        print(message)
        if args.json:
            print(json.dumps(collect_status(), ensure_ascii=False, indent=2))
        return 0

    if args.json:
        print(json.dumps(collect_status(), ensure_ascii=False, indent=2))
        return 0

    if tk is None:
        print("当前 Python 安装缺少 tkinter，无法启动图形界面。", file=sys.stderr)
        return 1

    app = ControlApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
