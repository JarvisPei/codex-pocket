#!/usr/bin/env python3
"""Local-only HTTP bridge for safely interrupting the active Codex Desktop turn."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from collections import OrderedDict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from codex_app_server import (
    AppServerError,
    CodexAppServerClient,
    ManagedRequestError,
    ManagedTurnConflict,
    assign_codex_thread_collection,
    load_codex_project_index,
    summarize_thread,
    summarize_thread_detail,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4317
MAX_BODY_BYTES = 32_768
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_PER_TURN = 4
ATTACHMENT_TTL_SECONDS = 60 * 60
MAX_DEVICES = 32
PAIRING_TICKET_TTL_SECONDS = 300
THREAD_DETAIL_CACHE_TTL_SECONDS = 60
THREAD_DETAIL_CACHE_MAX_ENTRIES = 24


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def same_text(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def read_mac_battery() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["/usr/bin/pmset", "-g", "batt"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False}
    if result.returncode != 0:
        return {"available": False}
    match = re.search(r"\b(\d{1,3})%;\s*([^;\n]+)", result.stdout)
    if match is None:
        return {"available": False}
    percent = min(100, max(0, int(match.group(1))))
    raw_state = match.group(2).strip().lower()
    if "discharg" in raw_state:
        state = "discharging"
    elif "charg" in raw_state and "charged" not in raw_state:
        state = "charging"
    elif "charged" in raw_state or percent >= 100:
        state = "full"
    else:
        state = "unknown"
    power_match = re.search(r"drawing from '([^']+)'", result.stdout, re.IGNORECASE)
    power_source = power_match.group(1).strip() if power_match else ""
    return {
        "available": True,
        "percent": percent,
        "state": state,
        "powerSource": power_source[:40],
    }


def thread_user_message_fingerprints(thread: dict[str, Any]) -> set[str]:
    fingerprints: set[str] = set()
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return fingerprints
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
            continue
        for item in turn["items"]:
            if not isinstance(item, dict) or item.get("type") != "userMessage":
                continue
            content = item.get("content")
            content_items = content if isinstance(content, list) else []
            text = "\n".join(
                part.get("text", "")
                for part in content_items
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            )
            item_id = item.get("id")
            identity = (
                f"id:{item_id}"
                if isinstance(item_id, str) and item_id
                else f"text:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
            )
            fingerprints.add(identity)
    return fingerprints


def desktop_message_landed(
    app_server: CodexAppServerClient,
    thread_id: str,
    previous_fingerprints: set[str],
    expected_message: str,
    attempts: int = 15,
) -> bool:
    expected = expected_message.strip()
    for attempt in range(attempts):
        try:
            result = app_server.read_thread(thread_id)
            thread = result.get("thread")
        except AppServerError:
            thread = None
        if isinstance(thread, dict):
            turns = thread.get("turns")
            if isinstance(turns, list):
                for turn in turns:
                    if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
                        continue
                    for item in turn["items"]:
                        if not isinstance(item, dict) or item.get("type") != "userMessage":
                            continue
                        content = item.get("content")
                        content_items = content if isinstance(content, list) else []
                        text = "\n".join(
                            part.get("text", "")
                            for part in content_items
                            if isinstance(part, dict)
                            and part.get("type") == "text"
                            and isinstance(part.get("text"), str)
                        )
                        item_id = item.get("id")
                        identity = (
                            f"id:{item_id}"
                            if isinstance(item_id, str) and item_id
                            else f"text:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
                        )
                        if identity not in previous_fingerprints and (
                            not expected or expected in text
                        ):
                            return True
        if attempt + 1 < attempts:
            time.sleep(0.1)
    return False


def create_projectless_workspace(root: Path, title: str) -> Path:
    """Create the dated standalone workspace shape used by Codex Desktop."""
    dated_root = root / datetime.now().astimezone().date().isoformat()
    dated_root.mkdir(parents=True, exist_ok=True)
    words = re.findall(r"[a-z0-9]+", title.lower())
    base = "-".join(words)[:48].strip("-") or "new-chat"
    for index in range(1, 10_000):
        name = base if index == 1 else f"{base}-{index}"
        candidate = dated_root / name
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise OSError("unable to allocate a standalone Codex workspace")


def safe_model_catalog(result: dict[str, Any]) -> list[dict[str, Any]]:
    models = result.get("data")
    if not isinstance(models, list):
        raise AppServerError("Invalid model catalog.")
    catalog = []
    for model in models:
        if not isinstance(model, dict):
            continue
        model_id = model.get("model")
        display_name = model.get("displayName")
        if not isinstance(model_id, str) or not isinstance(display_name, str):
            continue
        efforts = []
        for option in model.get("supportedReasoningEfforts", []):
            if not isinstance(option, dict):
                continue
            effort_id = option.get("reasoningEffort")
            if not isinstance(effort_id, str):
                continue
            efforts.append(
                {
                    "id": effort_id[:40],
                    "description": str(option.get("description", ""))[:500],
                }
            )
        service_tiers = []
        for tier in model.get("serviceTiers", []):
            if not isinstance(tier, dict) or not isinstance(tier.get("id"), str):
                continue
            service_tiers.append(
                {
                    "id": tier["id"][:40],
                    "name": str(tier.get("name", ""))[:100],
                    "description": str(tier.get("description", ""))[:500],
                }
            )
        catalog.append(
            {
                "id": model_id[:100],
                "displayName": display_name[:100],
                "description": str(model.get("description", ""))[:500],
                "efforts": efforts,
                "defaultEffort": str(model.get("defaultReasoningEffort", ""))[:40],
                "serviceTiers": service_tiers,
                "defaultServiceTier": (
                    str(model["defaultServiceTier"])[:40]
                    if isinstance(model.get("defaultServiceTier"), str)
                    else None
                ),
                "isDefault": bool(model.get("isDefault", False)),
            }
        )
    return catalog


def safe_project_catalog(project_index: dict[str, Any]) -> list[dict[str, Any]]:
    projects = project_index.get("projects")
    if not isinstance(projects, dict):
        return []
    result = []
    for project in projects.values():
        if not isinstance(project, dict):
            continue
        project_id = project.get("id")
        name = project.get("name")
        path = project.get("path")
        if not isinstance(project_id, str) or not isinstance(name, str):
            continue
        result.append(
            {
                "id": project_id[:256],
                "name": name[:240],
                "path": path[:1_000] if isinstance(path, str) else None,
                "order": int(project.get("order", 0)),
            }
        )
    return sorted(result, key=lambda project: project["order"])


def _safe_rate_window(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    try:
        used = min(100.0, max(0.0, float(value.get("usedPercent", 0))))
        duration = max(0, int(value.get("windowDurationMins", 0)))
        resets_at = max(0, int(value.get("resetsAt", 0)))
    except (TypeError, ValueError):
        return None
    return {
        "usedPercent": used,
        "remainingPercent": max(0.0, 100.0 - used),
        "windowDurationMins": duration,
        "resetsAt": resets_at,
    }


def safe_rate_limits(result: dict[str, Any]) -> dict[str, Any]:
    default = result.get("rateLimits")
    by_id = result.get("rateLimitsByLimitId")
    snapshots = []
    if isinstance(by_id, dict):
        snapshots.extend(
            snapshot for snapshot in by_id.values() if isinstance(snapshot, dict)
        )
    if not snapshots and isinstance(default, dict):
        snapshots.append(default)
    default_id = str(default.get("limitId", "")) if isinstance(default, dict) else ""
    limits = []
    seen = set()
    for snapshot in snapshots:
        limit_id = str(snapshot.get("limitId", ""))[:100]
        if not limit_id or limit_id in seen:
            continue
        seen.add(limit_id)
        primary = _safe_rate_window(snapshot.get("primary"))
        secondary = _safe_rate_window(snapshot.get("secondary"))
        if primary is None and secondary is None:
            continue
        limits.append(
            {
                "id": limit_id,
                "name": str(snapshot.get("limitName") or "")[:100],
                "primary": primary,
                "secondary": secondary,
                "isDefault": limit_id == default_id,
            }
        )
    return {
        "planType": str(default.get("planType", ""))[:40]
        if isinstance(default, dict)
        else "",
        "limits": limits,
    }


class DeviceRegistry:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._memory_data: dict[str, Any] = {
            "version": 1,
            "masterControlMigrated": False,
            "devices": [],
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if self.path is None:
            return self._memory_data
        if not self.path.exists():
            return {
                "version": 1,
                "masterControlMigrated": False,
                "devices": [],
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Device registry is unreadable.") from error
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("devices"), list)
        ):
            raise RuntimeError("Device registry has an unsupported format.")
        return payload

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        if self.path is None:
            self._memory_data = payload
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".devices.",
            dir=self.path.parent,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def ensure_ready(self) -> None:
        with self._lock:
            payload = self._read_unlocked()
            if self.path is not None and not self.path.exists():
                self._write_unlocked(payload)

    def count(self) -> int:
        with self._lock:
            return len(self._read_unlocked()["devices"])

    def master_control_migrated(self) -> bool:
        with self._lock:
            return bool(self._read_unlocked().get("masterControlMigrated", False))

    def enroll(self, name: str, user_agent: str) -> dict[str, str]:
        with self._lock:
            payload = self._read_unlocked()
            devices = payload["devices"]
            if len(devices) >= MAX_DEVICES:
                raise RuntimeError("Device limit reached.")
            device_id = secrets.token_urlsafe(12)
            device_secret = secrets.token_urlsafe(32)
            device_token = f"mcb1.{device_id}.{device_secret}"
            created_at = utc_timestamp()
            devices.append(
                {
                    "id": device_id,
                    "name": name,
                    "userAgent": user_agent,
                    "createdAt": created_at,
                    "tokenHash": hashlib.sha256(device_token.encode("utf-8")).hexdigest(),
                }
            )
            payload["masterControlMigrated"] = True
            self._write_unlocked(payload)
            return {
                "id": device_id,
                "name": name,
                "createdAt": created_at,
                "deviceToken": device_token,
            }

    def authenticate(self, token: str) -> Optional[dict[str, str]]:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "mcb1":
            return None
        device_id = parts[1]
        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            devices = self._read_unlocked()["devices"]
            for device in devices:
                if device.get("id") == device_id and hmac.compare_digest(
                    str(device.get("tokenHash", "")),
                    supplied_hash,
                ):
                    return {
                        key: str(device.get(key, ""))
                        for key in ("id", "name", "createdAt")
                    }
        return None

    def list_devices(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {
                    key: str(device.get(key, ""))
                    for key in ("id", "name", "userAgent", "createdAt")
                }
                for device in self._read_unlocked()["devices"]
            ]

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            payload = self._read_unlocked()
            original_count = len(payload["devices"])
            payload["devices"] = [
                device for device in payload["devices"] if device.get("id") != device_id
            ]
            if len(payload["devices"]) == original_count:
                return False
            self._write_unlocked(payload)
            return True


class AttachmentStore:
    def __init__(
        self,
        root: Path,
        ttl_seconds: int = ATTACHMENT_TTL_SECONDS,
    ) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()

    @staticmethod
    def validate_filename(filename: str) -> str:
        name = filename.strip()
        if (
            not name
            or name in {".", ".."}
            or len(name) > 180
            or len(name.encode("utf-8")) > 240
            or "/" in name
            or "\\" in name
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ValueError("invalid_attachment_name")
        return name

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _remove_unlocked(self, attachment_id: str) -> None:
        directory = self.root / attachment_id
        try:
            directory.relative_to(self.root)
        except ValueError:
            return
        if directory.is_dir() and not directory.is_symlink():
            shutil.rmtree(directory)

    def _load_unlocked(self, attachment_id: str) -> dict[str, Any]:
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", attachment_id)
            or not self.root.exists()
        ):
            raise KeyError(attachment_id)
        metadata_path = self.root / attachment_id / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise KeyError(attachment_id) from error
        if not isinstance(metadata, dict) or metadata.get("id") != attachment_id:
            raise KeyError(attachment_id)
        return metadata

    def purge_expired(self) -> None:
        with self._lock:
            if not self.root.exists():
                return
            now = time.time()
            for directory in self.root.iterdir():
                if not directory.is_dir() or directory.is_symlink():
                    continue
                try:
                    metadata = self._load_unlocked(directory.name)
                    expired = float(metadata.get("expiresAt", 0)) <= now
                except (KeyError, TypeError, ValueError):
                    expired = True
                if expired:
                    self._remove_unlocked(directory.name)

    def create(
        self,
        device_id: str,
        filename: str,
        content_type: str,
        source: Any,
        length: int,
    ) -> dict[str, Any]:
        name = self.validate_filename(filename)
        if length <= 0 or length > MAX_ATTACHMENT_BYTES:
            raise ValueError("invalid_attachment_size")
        media_type = content_type.strip()[:120] or "application/octet-stream"
        if any(ord(character) < 32 for character in media_type):
            raise ValueError("invalid_attachment_type")
        with self._lock:
            self.purge_expired()
            self._ensure_root()
            attachment_id = secrets.token_urlsafe(18)
            directory = self.root / attachment_id
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
            file_path = directory / name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(file_path, flags, 0o600)
            written = 0
            try:
                with os.fdopen(descriptor, "wb") as output:
                    remaining = length
                    while remaining:
                        chunk = source.read(min(64 * 1024, remaining))
                        if not chunk:
                            raise ValueError("incomplete_attachment")
                        output.write(chunk)
                        written += len(chunk)
                        remaining -= len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                now = time.time()
                metadata = {
                    "id": attachment_id,
                    "deviceId": device_id,
                    "name": name,
                    "size": written,
                    "contentType": media_type,
                    "createdAt": now,
                    "expiresAt": now + self.ttl_seconds,
                }
                metadata_path = directory / "metadata.json"
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.chmod(metadata_path, 0o600)
                return self.public_metadata(metadata)
            except Exception:
                self._remove_unlocked(attachment_id)
                raise

    @staticmethod
    def public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            key: metadata[key]
            for key in ("id", "name", "size", "contentType", "expiresAt")
        }

    def resolve(self, attachment_ids: list[str], device_id: str) -> list[dict[str, Any]]:
        if (
            len(attachment_ids) > MAX_ATTACHMENTS_PER_TURN
            or len(set(attachment_ids)) != len(attachment_ids)
        ):
            raise ValueError("invalid_attachments")
        with self._lock:
            self.purge_expired()
            resolved = []
            root = self.root.resolve()
            for attachment_id in attachment_ids:
                try:
                    metadata = self._load_unlocked(attachment_id)
                except KeyError as error:
                    raise ValueError("attachment_not_found") from error
                if metadata.get("deviceId") != device_id:
                    raise PermissionError("attachment_not_owned")
                try:
                    name = self.validate_filename(str(metadata.get("name", "")))
                    path = (self.root / attachment_id / name).resolve(strict=True)
                    path.relative_to(root)
                    file_stat = path.lstat()
                except (OSError, ValueError) as error:
                    raise ValueError("attachment_not_found") from error
                if (
                    not stat.S_ISREG(file_stat.st_mode)
                    or path.is_symlink()
                    or file_stat.st_size != metadata.get("size")
                    or file_stat.st_size > MAX_ATTACHMENT_BYTES
                ):
                    raise ValueError("attachment_not_found")
                resolved.append({**self.public_metadata(metadata), "path": str(path)})
            return resolved

    def delete(self, attachment_id: str, device_id: str) -> bool:
        with self._lock:
            self.purge_expired()
            try:
                metadata = self._load_unlocked(attachment_id)
            except KeyError:
                return False
            if metadata.get("deviceId") != device_id:
                raise PermissionError("attachment_not_owned")
            self._remove_unlocked(attachment_id)
            return True


class PairingTicketStore:
    def __init__(self, ttl_seconds: int = PAIRING_TICKET_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._tickets: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(ticket: str) -> str:
        return hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    def create(self) -> str:
        ticket = f"pair1.{secrets.token_urlsafe(32)}"
        now = time.monotonic()
        with self._lock:
            self._tickets = {
                digest: expiry
                for digest, expiry in self._tickets.items()
                if expiry > now
            }
            self._tickets[self._digest(ticket)] = now + self.ttl_seconds
        return ticket

    def consume(self, ticket: str) -> bool:
        now = time.monotonic()
        digest = self._digest(ticket)
        with self._lock:
            expiry = self._tickets.pop(digest, None)
            return expiry is not None and expiry > now


class DesktopController:
    def __init__(
        self,
        ax_script: Path,
        swift: Path = Path("/usr/bin/swift"),
        ax_helper: Optional[Path] = None,
    ) -> None:
        self.ax_script = ax_script
        self.swift = swift
        self.ax_helper = ax_helper
        self._interrupt_lock = threading.Lock()

    def _run(
        self,
        action: str,
        *extra_arguments: str,
        input_text: Optional[str] = None,
    ) -> subprocess.CompletedProcess[str]:
        command = (
            [str(self.ax_helper), action, *extra_arguments]
            if self.ax_helper is not None
            else [str(self.swift), str(self.ax_script), action, *extra_arguments]
        )
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=25,
        )

    def stop_candidate_count(self) -> int:
        result = self._run("--check-stop")
        if result.returncode != 0:
            detail = result.stderr.strip() or "Accessibility probe failed"
            raise RuntimeError(detail)
        try:
            payload = json.loads(result.stdout)
            return int(payload["stopCandidates"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Accessibility probe returned invalid output") from error

    def current_task_title(self) -> str:
        result = self._run("--current-task")
        if result.returncode != 0:
            detail = result.stderr.strip() or "Accessibility task probe failed"
            raise RuntimeError(detail)
        try:
            payload = json.loads(result.stdout)
            titles = payload["taskTitles"]
            if not isinstance(titles, list) or len(titles) != 1:
                raise TaskIdentityError(len(titles) if isinstance(titles, list) else 0)
            title = titles[0]
            if not isinstance(title, str) or not title.strip():
                raise TaskIdentityError(0)
            return title.strip()
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Accessibility task probe returned invalid output") from error

    def status(self) -> dict[str, Any]:
        result = self._run("--desktop-state")
        if result.returncode != 0:
            detail = result.stderr.strip() or "Accessibility state probe failed"
            raise RuntimeError(detail)
        try:
            payload = json.loads(result.stdout)
            titles = payload["taskTitles"]
            if not isinstance(titles, list) or len(titles) != 1:
                raise TaskIdentityError(len(titles) if isinstance(titles, list) else 0)
            title = titles[0]
            count = int(payload["stopCandidates"])
            request = payload.get("request")
            if not isinstance(title, str) or not title.strip() or count < 0:
                raise ValueError("invalid desktop state")
            if request is not None and not isinstance(request, dict):
                raise ValueError("invalid desktop request")
            return {
                "taskTitle": title.strip(),
                "stopCandidates": count,
                "request": request,
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Accessibility state probe returned invalid output") from error

    def foreground_status(self, expected_task_title: str) -> dict[str, Any]:
        with self._interrupt_lock:
            result = self._run("--activate")
            if result.returncode != 0:
                detail = result.stderr.strip() or "Unable to activate ChatGPT"
                raise RuntimeError(detail)
            first_title = self.current_task_title()
            if not same_text(first_title, expected_task_title):
                raise TaskChangedError(first_title)
            count = self.stop_candidate_count()
            second_title = self.current_task_title()
            if not same_text(second_title, expected_task_title):
                raise TaskChangedError(second_title)
            return {"taskTitle": second_title, "stopCandidates": count}

    def managed_takeover_status(self, expected_task_title: str) -> dict[str, Any]:
        """Bring Desktop forward and safely determine whether it owns this task."""
        with self._interrupt_lock:
            result = self._run("--activate")
            if result.returncode != 0:
                detail = result.stderr.strip() or "Unable to activate ChatGPT"
                raise RuntimeError(detail)
            first_title = self.current_task_title()
            if same_text(first_title, expected_task_title):
                count = self.stop_candidate_count()
                second_title = self.current_task_title()
                if not same_text(second_title, expected_task_title):
                    raise TaskChangedError(second_title)
                return {
                    "sameThread": True,
                    "taskTitle": second_title,
                    "stopCandidates": count,
                }
            second_title = self.current_task_title()
            if not same_text(first_title, second_title):
                raise TaskChangedError(second_title)
            return {
                "sameThread": False,
                "taskTitle": second_title,
                "stopCandidates": None,
            }

    def interrupt(self, expected_task_title: str) -> None:
        with self._interrupt_lock:
            actual_task_title = self.current_task_title()
            if not same_text(actual_task_title, expected_task_title):
                raise TaskChangedError(actual_task_title)
            count = self.stop_candidate_count()
            if count != 1:
                raise StopCandidateError(count)
            result = self._run(
                "--stop",
                f"--expected-task-title={expected_task_title}",
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or "Accessibility interrupt failed"
                raise RuntimeError(detail)

    def interrupt_thread(self, thread_id: str, expected_task_title: str) -> None:
        """Navigate Desktop to a thread, verify its identity, then press Stop."""
        thread_url = f"codex://threads/{urllib.parse.quote(thread_id, safe='')}"
        with self._interrupt_lock:
            navigation = subprocess.run(
                ["/usr/bin/open", "-b", "com.openai.codex", thread_url],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if navigation.returncode != 0:
                detail = navigation.stderr.strip() or "Unable to open Codex task"
                raise ThreadNavigationError(detail)

            navigation_deadline = time.monotonic() + 8
            actual_task_title = ""
            while time.monotonic() < navigation_deadline:
                try:
                    actual_task_title = self.current_task_title()
                except (RuntimeError, TaskIdentityError):
                    actual_task_title = ""
                if same_text(actual_task_title, expected_task_title):
                    break
                time.sleep(0.2)
            else:
                raise ThreadNavigationError(
                    "Codex Desktop did not navigate to the requested task"
                )

            stop_deadline = time.monotonic() + 4
            count = 0
            while time.monotonic() < stop_deadline:
                first_title = self.current_task_title()
                if not same_text(first_title, expected_task_title):
                    raise TaskChangedError(first_title)
                count = self.stop_candidate_count()
                second_title = self.current_task_title()
                if not same_text(second_title, expected_task_title):
                    raise TaskChangedError(second_title)
                if count == 1:
                    break
                if count > 1:
                    raise StopCandidateError(count)
                time.sleep(0.2)
            if count != 1:
                raise StopCandidateError(count)

            result = self._run(
                "--stop",
                f"--expected-task-title={expected_task_title}",
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or "Accessibility interrupt failed"
                raise RuntimeError(detail)

    def send_to_desktop(
        self,
        thread_id: str,
        expected_task_title: str,
        message: str,
        *,
        continue_only: bool = False,
        attachment_paths: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        payload = json.dumps(
            {
                "threadId": thread_id,
                "expectedTaskTitle": expected_task_title,
                "message": message,
                "continueOnly": continue_only,
                "attachmentPaths": attachment_paths or [],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._interrupt_lock:
            result = self._run("--desktop-send", input_text=payload)
            if result.returncode in {28, 43} and attachment_paths:
                # Attaching a preview briefly replaces the Electron composer
                # text area. Some image previews also become observable only
                # after Electron finishes its asynchronous layout pass. A
                # bounded retry reuses the attachment after the UI settles.
                time.sleep(3.0)
                result = self._run("--desktop-send", input_text=payload)
        if result.returncode != 0:
            reasons = {
                2: "desktop_accessibility_unavailable",
                26: "task_identity_mismatch",
                27: "desktop_turn_active",
                28: "desktop_composer_unavailable",
                29: "desktop_draft_present",
                30: "desktop_composer_write_failed",
                31: "desktop_send_unavailable",
                32: "foreground_task_changed",
                33: "desktop_send_failed",
                34: "desktop_send_unconfirmed",
                35: "desktop_composer_focus_failed",
                36: "desktop_matching_draft_replace_failed",
                37: "desktop_keyboard_input_failed",
                38: "desktop_keyboard_input_unconfirmed",
                39: "desktop_mouse_fallback_refused",
                40: "desktop_mouse_click_failed",
                41: "desktop_attachment_invalid",
                42: "desktop_attachment_paste_failed",
                43: "desktop_attachment_unconfirmed",
                44: "desktop_attachment_conflict",
            }
            raise DesktopDispatchError(
                reasons.get(result.returncode, "desktop_dispatch_failed"),
                result.stderr.strip() or "Desktop dispatch failed",
            )
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DesktopDispatchError(
                "desktop_dispatch_failed",
                "Desktop dispatch returned invalid output",
            ) from error
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise DesktopDispatchError(
                "desktop_dispatch_failed",
                "Desktop dispatch did not confirm success",
            )
        return response

    def respond_to_desktop_request(
        self,
        expected_task_title: str,
        fingerprint: str,
        action: str,
        *,
        answer: Optional[str] = None,
        option_label: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = json.dumps(
            {
                "expectedTaskTitle": expected_task_title,
                "fingerprint": fingerprint,
                "action": action,
                "answer": answer,
                "optionLabel": option_label,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._interrupt_lock:
            result = self._run("--desktop-request-respond", input_text=payload)
        if result.returncode != 0:
            reasons = {
                51: "foreground_task_changed",
                52: "desktop_request_unavailable",
                53: "desktop_request_changed",
                54: "desktop_request_response_invalid",
                55: "desktop_answer_failed",
                56: "desktop_request_action_failed",
            }
            raise DesktopRequestError(
                reasons.get(result.returncode, "desktop_request_response_failed"),
                result.stderr.strip() or "Desktop request response failed",
            )
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DesktopRequestError(
                "desktop_request_response_failed",
                "Desktop request response returned invalid output",
            ) from error
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise DesktopRequestError(
                "desktop_request_response_failed",
                "Desktop request response was not confirmed",
            )
        return response


class StopCandidateError(RuntimeError):
    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f"expected one semantic Stop button, found {count}")


class DesktopDispatchError(RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


class DesktopRequestError(RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


class TaskIdentityError(RuntimeError):
    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f"expected one foreground task title, found {count}")


class TaskChangedError(RuntimeError):
    def __init__(self, actual_title: str) -> None:
        self.actual_title = actual_title
        super().__init__("foreground task changed")


class ThreadNavigationError(RuntimeError):
    pass


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        token: str,
        controller: DesktopController,
        web_root: Optional[Path] = None,
        device_registry: Optional[DeviceRegistry] = None,
        pairing_tickets: Optional[PairingTicketStore] = None,
        app_server: Optional[CodexAppServerClient] = None,
        codex_state_path: Optional[Path] = None,
        attachment_store: Optional[AttachmentStore] = None,
        projectless_root: Optional[Path] = None,
    ) -> None:
        self.token = token
        self.controller = controller
        self.web_root = web_root or Path(__file__).resolve().parent / "web"
        self.device_registry = device_registry or DeviceRegistry()
        self.pairing_tickets = pairing_tickets or PairingTicketStore()
        self.app_server = app_server
        self.attachment_store = attachment_store or AttachmentStore(
            Path.home()
            / "Library"
            / "Application Support"
            / "MobileCodexBridge"
            / "uploads"
        )
        self.codex_state_path = (
            codex_state_path
            or Path.home() / ".codex" / ".codex-global-state.json"
        )
        self.projectless_root = (
            projectless_root
            or Path.home() / "Documents" / "Codex"
        )
        self._project_index_lock = threading.RLock()
        self._project_index = load_codex_project_index(self.codex_state_path)
        self._project_index_shrink_seen_at: Optional[float] = None
        self._thread_detail_cache: OrderedDict[
            tuple[str, str, int], tuple[float, dict[str, Any]]
        ] = OrderedDict()
        self._thread_detail_cache_lock = threading.RLock()
        super().__init__(address, BridgeHandler)

    def project_index(self) -> dict[str, Any]:
        """Keep a last-known complete sidebar while Desktop rewrites its state."""
        candidate = load_codex_project_index(self.codex_state_path)
        with self._project_index_lock:
            cached_projects = self._project_index.get("projects", {})
            candidate_projects = candidate.get("projects", {})
            cached_count = len(cached_projects) if isinstance(cached_projects, dict) else 0
            candidate_count = (
                len(candidate_projects) if isinstance(candidate_projects, dict) else 0
            )
            if candidate_count >= cached_count:
                self._project_index = candidate
                self._project_index_shrink_seen_at = None
            else:
                now = time.monotonic()
                if self._project_index_shrink_seen_at is None:
                    self._project_index_shrink_seen_at = now
                elif now - self._project_index_shrink_seen_at >= 30:
                    # A stable shrink most likely represents an intentional
                    # project removal rather than a transient Desktop rewrite.
                    self._project_index = candidate
                    self._project_index_shrink_seen_at = None
            return self._project_index

    def cached_thread_detail(
        self,
        thread_id: str,
        revision: str,
        turn_limit: int,
    ) -> Optional[dict[str, Any]]:
        key = (thread_id, revision, turn_limit)
        with self._thread_detail_cache_lock:
            cached = self._thread_detail_cache.get(key)
            if cached is None:
                return None
            cached_at, detail = cached
            if time.monotonic() - cached_at > THREAD_DETAIL_CACHE_TTL_SECONDS:
                self._thread_detail_cache.pop(key, None)
                return None
            self._thread_detail_cache.move_to_end(key)
            return detail

    def cache_thread_detail(
        self,
        thread_id: str,
        revision: str,
        turn_limit: int,
        detail: dict[str, Any],
    ) -> None:
        key = (thread_id, revision, turn_limit)
        with self._thread_detail_cache_lock:
            self._thread_detail_cache[key] = (time.monotonic(), detail)
            self._thread_detail_cache.move_to_end(key)
            while len(self._thread_detail_cache) > THREAD_DETAIL_CACHE_MAX_ENTRIES:
                self._thread_detail_cache.popitem(last=False)

    def invalidate_thread_detail(self, thread_id: str) -> None:
        with self._thread_detail_cache_lock:
            for key in list(self._thread_detail_cache):
                if key[0] == thread_id:
                    self._thread_detail_cache.pop(key, None)

    def server_close(self) -> None:
        try:
            if self.app_server is not None:
                self.app_server.close()
        finally:
            super().server_close()


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep logs metadata-only. Never print Authorization or request bodies.
        print(f"{self.client_address[0]} {format % args}")

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
    ) -> None:
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        compressible = (
            content_type.startswith("text/")
            or content_type.startswith("application/json")
            or content_type.startswith("application/javascript")
        )
        content_encoding = None
        if accepts_gzip and compressible and len(body) >= 1_024:
            body = gzip.compress(body, compresslevel=5)
            content_encoding = "gzip"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if content_encoding is not None:
            self.send_header("Content-Encoding", content_encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; worker-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Mobile radios and VPN route changes can close a response while it
            # is being written. The request is safe to retry and needs no stack trace.
            return

    def _serve_asset(self, relative_path: str, content_type: str) -> None:
        path = (self.server.web_root / relative_path).resolve()
        try:
            path.relative_to(self.server.web_root.resolve())
            body = path.read_bytes()
        except (OSError, ValueError):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def _bearer_token(self) -> str:
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return supplied[len(prefix) :] if supplied.startswith(prefix) else ""

    def _master_authorized(self) -> bool:
        expected = f"Bearer {self.server.token}"
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, expected)

    def _device_identity(self) -> Optional[dict[str, str]]:
        return self.server.device_registry.authenticate(self._bearer_token())

    def _require_control_auth(self) -> bool:
        if self._device_identity() is not None:
            return True
        # Permit the original QR/session flow only until the first persistent
        # device is enrolled. This provides a safe in-place migration path.
        if (
            not self.server.device_registry.master_control_migrated()
            and self._master_authorized()
        ):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def _require_master_auth(self) -> bool:
        if self._master_authorized():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def _read_json(self) -> Optional[dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_body_size"})
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return None
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_object"})
            return None
        return payload

    def _resolve_attachments(
        self,
        payload: dict[str, Any],
    ) -> tuple[bool, list[dict[str, Any]]]:
        attachment_ids = payload.get("attachmentIds", [])
        if (
            not isinstance(attachment_ids, list)
            or len(attachment_ids) > MAX_ATTACHMENTS_PER_TURN
            or any(
                not isinstance(attachment_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", attachment_id)
                for attachment_id in attachment_ids
            )
        ):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_attachments"})
            return False, []
        if not attachment_ids:
            return True, []
        device = self._device_identity()
        if device is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "device_required"})
            return False, []
        try:
            attachments = self.server.attachment_store.resolve(
                attachment_ids,
                device["id"],
            )
        except PermissionError:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "attachment_not_owned"})
            return False, []
        except ValueError as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error) or "invalid_attachments"},
            )
            return False, []
        return True, attachments

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self._serve_asset("index.html", "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._serve_asset("app.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._serve_asset("app.js", "text/javascript; charset=utf-8")
            return
        if path == "/sw.js":
            self._serve_asset("sw.js", "text/javascript; charset=utf-8")
            return
        if path == "/manifest.webmanifest":
            self._serve_asset(
                "manifest.webmanifest",
                "application/manifest+json; charset=utf-8",
            )
            return
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "service": "mac-codex-bridge"})
            return
        if path == "/api/devices":
            if not self._require_master_auth():
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "devices": self.server.device_registry.list_devices()},
            )
            return
        if path == "/api/devices/self":
            device = self._device_identity()
            if device is None:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "device": device})
            return
        if path == "/api/system/metrics":
            if not self._require_control_auth():
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "battery": read_mac_battery()},
            )
            return
        if path == "/api/codex/models":
            if not self._require_control_auth():
                return
            if self.server.app_server is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "codex_app_server_unavailable"},
                )
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            thread_id = str(query.get("threadId", [""])[0])
            if not thread_id or "/" in thread_id or len(thread_id) > 128:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_thread_id"})
                return
            try:
                catalog = safe_model_catalog(self.server.app_server.list_models())
                settings = self.server.app_server.read_thread_settings(thread_id)
            except AppServerError:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": "codex_model_settings_failed"},
                )
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "models": catalog, "settings": settings},
            )
            return
        if path == "/api/codex/usage":
            if not self._require_control_auth():
                return
            if self.server.app_server is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "codex_app_server_unavailable"},
                )
                return
            try:
                usage = safe_rate_limits(self.server.app_server.read_rate_limits())
            except AppServerError:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": "codex_usage_failed"},
                )
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "usage": usage})
            return
        if path == "/api/codex/threads":
            if not self._require_control_auth():
                return
            if self.server.app_server is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "codex_app_server_unavailable"},
                )
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                limit = min(50, max(1, int(query.get("limit", ["30"])[0])))
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_limit"})
                return
            try:
                result = self.server.app_server.list_threads(limit)
                threads = result.get("data", [])
                if not isinstance(threads, list):
                    raise AppServerError("Invalid thread list.")
            except AppServerError:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": "codex_app_server_failed"},
                )
                return
            project_index = self.server.project_index()
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "projects": safe_project_catalog(project_index),
                    "threads": [
                        summarize_thread(thread, project_index)
                        for thread in threads
                        if isinstance(thread, dict)
                    ],
                    "nextCursor": result.get("nextCursor"),
                },
            )
            return
        thread_prefix = "/api/codex/threads/"
        run_suffix = "/run"
        if path.startswith(thread_prefix) and path.endswith(run_suffix):
            if not self._require_control_auth():
                return
            if self.server.app_server is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "codex_app_server_unavailable"},
                )
                return
            encoded_thread_id = path[len(thread_prefix) : -len(run_suffix)]
            thread_id = urllib.parse.unquote(encoded_thread_id.rstrip("/"))
            if not thread_id or "/" in thread_id or len(thread_id) > 128:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_thread_id"})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "run": self.server.app_server.managed_run(thread_id),
                },
            )
            return
        if path.startswith(thread_prefix):
            if not self._require_control_auth():
                return
            if self.server.app_server is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "codex_app_server_unavailable"},
                )
                return
            thread_id = urllib.parse.unquote(path[len(thread_prefix) :])
            if not thread_id or "/" in thread_id or len(thread_id) > 128:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_thread_id"})
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                turn_limit = min(60, max(1, int(query.get("turns", ["60"])[0])))
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_turn_limit"})
                return
            revision = str(query.get("revision", [""])[0])[:128]
            force_fresh = query.get("fresh", ["0"])[0] == "1"
            detail = None if force_fresh else self.server.cached_thread_detail(
                thread_id,
                revision,
                turn_limit,
            )
            if detail is None:
                try:
                    result = self.server.app_server.read_thread(thread_id)
                    thread = result.get("thread")
                    if not isinstance(thread, dict):
                        raise AppServerError("Invalid thread.")
                except AppServerError:
                    self._send_json(
                        HTTPStatus.BAD_GATEWAY,
                        {"ok": False, "error": "codex_app_server_failed"},
                    )
                    return
                detail = summarize_thread_detail(
                    thread,
                    self.server.project_index(),
                    max_turns=turn_limit,
                )
                self.server.cache_thread_detail(
                    thread_id,
                    revision,
                    turn_limit,
                    detail,
                )
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "thread": detail,
                },
            )
            return
        if path == "/api/desktop/interrupt/status":
            if not self._require_control_auth():
                return
            try:
                status = self.server.controller.status()
            except (RuntimeError, TaskIdentityError) as error:
                print(
                    f"Accessibility status probe failed: {type(error).__name__}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": "accessibility_probe_failed"},
                )
                return
            count = status["stopCandidates"]
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "taskTitle": status["taskTitle"],
                    "interruptible": count <= 1,
                    "stopCandidates": count,
                    "request": status.get("request"),
                },
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path == "/api/attachments":
            device = self._device_identity()
            if device is None:
                self.close_connection = True
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "device_required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.close_connection = True
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_content_length"},
                )
                return
            if length <= 0:
                self.close_connection = True
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "empty_attachment"})
                return
            if length > MAX_ATTACHMENT_BYTES:
                self.close_connection = True
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "attachment_too_large"},
                )
                return
            encoded_name = self.headers.get("X-Codex-Filename", "")
            try:
                filename = urllib.parse.unquote(encoded_name, errors="strict")
                metadata = self.server.attachment_store.create(
                    device["id"],
                    filename,
                    self.headers.get("Content-Type", "application/octet-stream"),
                    self.rfile,
                    length,
                )
            except (UnicodeError, ValueError) as error:
                self.close_connection = True
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": str(error) or "invalid_attachment"},
                )
                return
            except OSError:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "attachment_storage_failed"},
                )
                return
            self._send_json(
                HTTPStatus.CREATED,
                {"ok": True, "attachment": metadata},
            )
            return
        if self.path == "/api/devices/pairing-ticket":
            if not self._require_master_auth():
                return
            payload = self._read_json()
            if payload is None:
                return
            ticket = self.server.pairing_tickets.create()
            self._send_json(
                HTTPStatus.CREATED,
                {
                    "ok": True,
                    "pairingTicket": ticket,
                    "expiresIn": self.server.pairing_tickets.ttl_seconds,
                },
            )
            return
        if self.path == "/api/devices/enroll":
            payload = self._read_json()
            if payload is None:
                return
            name = payload.get("name", "")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_device_name"})
                return
            pairing_ticket = payload.get("pairingTicket", "")
            ticket_authorized = (
                isinstance(pairing_ticket, str)
                and bool(pairing_ticket)
                and self.server.pairing_tickets.consume(pairing_ticket)
            )
            if not self._master_authorized() and not ticket_authorized:
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "invalid_or_expired_pairing"},
                )
                return
            try:
                device = self.server.device_registry.enroll(
                    name.strip(),
                    self.headers.get("User-Agent", "")[:256],
                )
            except RuntimeError:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "device_enrollment_failed"},
                )
                return
            self._send_json(HTTPStatus.CREATED, {"ok": True, "device": device})
            return
        if self.path == "/api/codex/threads":
            if not self._require_control_auth():
                return
            if self.server.app_server is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "codex_app_server_unavailable"},
                )
                return
            payload = self._read_json()
            if payload is None:
                return
            attachments_ok, attachments = self._resolve_attachments(payload)
            if not attachments_ok:
                return
            message = payload.get("message")
            project_id = payload.get("projectId")
            model = payload.get("model")
            effort = payload.get("effort")
            service_tier = payload.get("serviceTier")
            if (
                not isinstance(message, str)
                or len(message.strip()) > 20_000
                or (not message.strip() and not attachments)
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_message"})
                return
            if project_id is not None and (
                not isinstance(project_id, str)
                or not project_id
                or len(project_id) > 256
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_project"})
                return
            supplied_settings = any(
                value is not None for value in (model, effort, service_tier)
            )
            if supplied_settings and (
                not isinstance(model, str)
                or not model
                or len(model) > 100
                or not isinstance(effort, str)
                or not effort
                or len(effort) > 40
                or (
                    service_tier is not None
                    and (
                        not isinstance(service_tier, str)
                        or not service_tier
                        or len(service_tier) > 40
                    )
                )
            ):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "invalid_model_settings"},
                )
                return

            project_index = self.server.project_index()
            projects = project_index.get("projects")
            project = projects.get(project_id) if isinstance(projects, dict) else None
            if project_id is not None and not isinstance(project, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_project"})
                return
            cwd = project.get("path") if isinstance(project, dict) else None
            if project_id is not None and not isinstance(cwd, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "project_path_missing"})
                return
            normalized_lines = [
                re.sub(r"\s+", " ", line).strip()
                for line in message.strip().splitlines()
                if line.strip()
            ]
            title = (
                normalized_lines[0]
                if normalized_lines
                else f"分析 {attachments[0]['name']}"
            )[:80]
            if project_id is None:
                try:
                    cwd = str(create_projectless_workspace(
                        self.server.projectless_root,
                        title,
                    ))
                except OSError:
                    self._send_json(
                        HTTPStatus.BAD_GATEWAY,
                        {"ok": False, "error": "projectless_directory_failed"},
                    )
                    return
            try:
                created = self.server.app_server.create_thread(
                    title=title,
                    cwd=cwd,
                    model=model if supplied_settings else None,
                    effort=effort if supplied_settings else None,
                    service_tier=service_tier if supplied_settings else None,
                )
                thread = created.get("thread")
                if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                    raise AppServerError("Invalid created thread.")
                thread_id = thread["id"]
            except ManagedRequestError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "invalid_model_settings"},
                )
                return
            except AppServerError:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": "thread_create_failed"},
                )
                return
            try:
                assign_codex_thread_collection(
                    self.server.codex_state_path,
                    thread_id,
                    project_id,
                )
            except ManagedRequestError:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "ok": False,
                        "error": "project_assignment_failed",
                        "threadCreated": True,
                        "threadId": thread_id,
                    },
                )
                return
            except AppServerError:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "ok": False,
                        "error": "project_assignment_failed",
                        "threadCreated": True,
                        "threadId": thread_id,
                    },
                )
                return
            previous_user_messages = thread_user_message_fingerprints(thread)
            try:
                desktop = self.server.controller.send_to_desktop(
                    thread_id,
                    title,
                    message.strip(),
                    attachment_paths=[
                        attachment["path"] for attachment in attachments
                    ],
                )
            except DesktopDispatchError as error:
                recovered = (
                    error.reason in {
                        "desktop_send_unconfirmed",
                        "desktop_mouse_fallback_refused",
                    }
                    and desktop_message_landed(
                        self.server.app_server,
                        thread_id,
                        previous_user_messages,
                        message,
                    )
                )
                if recovered:
                    desktop = {
                        "ok": True,
                        "mode": "message",
                        "taskTitle": title,
                        "confirmedBy": "threadHistory",
                    }
                else:
                    print(
                        f"New Desktop task dispatch failed: reason={error.reason}; "
                        f"detail={error.detail}",
                        file=sys.stderr,
                        flush=True,
                    )
                    status = (
                        HTTPStatus.CONFLICT
                        if error.reason in {
                            "desktop_turn_active",
                            "desktop_draft_present",
                            "foreground_task_changed",
                            "task_identity_mismatch",
                        }
                        else HTTPStatus.BAD_GATEWAY
                    )
                    refreshed_index = self.server.project_index()
                    self._send_json(
                        status,
                        {
                            "ok": False,
                            "error": error.reason,
                            "threadCreated": True,
                            "threadId": thread_id,
                            "thread": summarize_thread(thread, refreshed_index),
                        },
                    )
                    return
            refreshed_index = self.server.project_index()
            summary = summarize_thread(thread, refreshed_index)
            self._send_json(
                HTTPStatus.ACCEPTED,
                {
                    "ok": True,
                    "mode": "desktop",
                    "thread": summary,
                    "settings": created.get("settings"),
                    "desktop": desktop,
                },
            )
            return
        thread_prefix = "/api/codex/threads/"
        if self.path.startswith(thread_prefix):
            if not self._require_control_auth():
                return
            if self.server.app_server is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "codex_app_server_unavailable"},
                )
                return
            route = urllib.parse.urlsplit(self.path).path[len(thread_prefix) :]
            parts = [urllib.parse.unquote(part) for part in route.split("/") if part]
            if len(parts) not in {2, 3}:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            thread_id = parts[0]
            if not thread_id or "/" in thread_id or len(thread_id) > 128:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_thread_id"})
                return
            payload = self._read_json()
            if payload is None:
                return
            if len(parts) == 2 and parts[1] == "settings":
                model = payload.get("model")
                effort = payload.get("effort")
                service_tier = payload.get("serviceTier")
                if (
                    not isinstance(model, str)
                    or not model
                    or len(model) > 100
                    or not isinstance(effort, str)
                    or not effort
                    or len(effort) > 40
                    or (
                        service_tier is not None
                        and (
                            not isinstance(service_tier, str)
                            or not service_tier
                            or len(service_tier) > 40
                        )
                    )
                ):
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "invalid_model_settings"},
                    )
                    return
                try:
                    thread_result = self.server.app_server.read_thread(thread_id)
                    thread = thread_result.get("thread")
                    if not isinstance(thread, dict):
                        raise AppServerError("Invalid thread.")
                    turns = thread.get("turns")
                    last_turn = turns[-1] if isinstance(turns, list) and turns else None
                    if isinstance(last_turn, dict) and last_turn.get("status") in {
                        "starting",
                        "inProgress",
                        "waitingForInput",
                        "interrupting",
                    }:
                        self._send_json(
                            HTTPStatus.CONFLICT,
                            {"ok": False, "error": "model_settings_locked"},
                        )
                        return
                    settings = self.server.app_server.update_thread_settings(
                        thread_id,
                        model=model,
                        effort=effort,
                        service_tier=service_tier,
                    )
                except ManagedRequestError:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "invalid_model_settings"},
                    )
                    return
                except AppServerError:
                    self._send_json(
                        HTTPStatus.BAD_GATEWAY,
                        {"ok": False, "error": "model_settings_update_failed"},
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "settings": settings},
                )
                return
            if len(parts) == 2 and parts[1] in {"turn", "continue"}:
                is_continue = parts[1] == "continue"
                message = payload.get("message")
                attachments_ok, attachments = self._resolve_attachments(payload)
                if not attachments_ok:
                    return
                if is_continue and attachments:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_attachments"},
                    )
                    return
                if not is_continue:
                    if not isinstance(message, str) or (
                        not message.strip() and not attachments
                    ):
                        self._send_json(
                            HTTPStatus.BAD_REQUEST,
                            {"error": "message_required"},
                        )
                        return
                    if len(message.strip()) > 20_000:
                        self._send_json(
                            HTTPStatus.BAD_REQUEST,
                            {"error": "message_too_long"},
                        )
                        return
                try:
                    thread_result = self.server.app_server.read_thread(thread_id)
                    thread = thread_result.get("thread")
                    if not isinstance(thread, dict):
                        raise AppServerError("Invalid thread.")
                    summary = summarize_thread(thread)
                except AppServerError:
                    self._send_json(
                        HTTPStatus.BAD_GATEWAY,
                        {"ok": False, "error": "codex_thread_unavailable"},
                    )
                    return
                if is_continue:
                    turns = thread.get("turns")
                    last_turn = turns[-1] if isinstance(turns, list) and turns else None
                    if (
                        not isinstance(last_turn, dict)
                        or last_turn.get("status") != "interrupted"
                    ):
                        self._send_json(
                            HTTPStatus.CONFLICT,
                            {"ok": False, "error": "thread_not_interrupted"},
                        )
                        return
                previous_user_messages = thread_user_message_fingerprints(thread)
                try:
                    desktop = self.server.controller.send_to_desktop(
                        thread_id,
                        summary["title"],
                        "" if is_continue else message.strip(),
                        continue_only=is_continue,
                        attachment_paths=[
                            attachment["path"] for attachment in attachments
                        ],
                    )
                except DesktopDispatchError as error:
                    recovered = (
                        not is_continue
                        and error.reason in {
                            "desktop_send_unconfirmed",
                            "desktop_mouse_fallback_refused",
                        }
                        and desktop_message_landed(
                            self.server.app_server,
                            thread_id,
                            previous_user_messages,
                            message,
                        )
                    )
                    if recovered:
                        desktop = {
                            "ok": True,
                            "mode": "message",
                            "taskTitle": summary["title"],
                            "confirmedBy": "threadHistory",
                        }
                    else:
                        print(
                            f"Desktop dispatch failed: reason={error.reason}; "
                            f"detail={error.detail}",
                            file=sys.stderr,
                            flush=True,
                        )
                        status = (
                            HTTPStatus.CONFLICT
                            if error.reason in {
                                "desktop_turn_active",
                                "desktop_draft_present",
                                "foreground_task_changed",
                                "task_identity_mismatch",
                            }
                            else HTTPStatus.BAD_GATEWAY
                        )
                        self._send_json(
                            status,
                            {
                                "ok": False,
                                "error": error.reason,
                            },
                        )
                        return
                self.server.invalidate_thread_detail(thread_id)
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    {"ok": True, "mode": "desktop", "desktop": desktop},
                )
                return
            if len(parts) == 2 and parts[1] == "interrupt":
                if payload.get("confirm") is not True:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "confirmation_required"},
                    )
                    return
                try:
                    run = self.server.app_server.interrupt_turn(thread_id)
                except ManagedTurnConflict:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {"ok": False, "error": "managed_turn_not_active"},
                    )
                    return
                except AppServerError:
                    self._send_json(
                        HTTPStatus.BAD_GATEWAY,
                        {"ok": False, "error": "managed_interrupt_failed"},
                    )
                    return
                self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "run": run})
                return
            if len(parts) == 3 and parts[1] == "requests":
                request_key = parts[2]
                if not request_key or len(request_key) > 128:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_request_id"},
                    )
                    return
                try:
                    run = self.server.app_server.respond_to_request(
                        thread_id,
                        request_key,
                        payload,
                    )
                except ManagedRequestError:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "invalid_pending_response"},
                    )
                    return
                except AppServerError:
                    self._send_json(
                        HTTPStatus.BAD_GATEWAY,
                        {"ok": False, "error": "pending_response_failed"},
                    )
                    return
                self._send_json(HTTPStatus.OK, {"ok": True, "run": run})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if self.path == "/api/desktop/request":
            if not self._require_control_auth():
                return
            payload = self._read_json()
            if payload is None:
                return
            expected_task_title = payload.get("expectedTaskTitle")
            fingerprint = payload.get("fingerprint")
            action = payload.get("action")
            answer = payload.get("answer")
            option_label = payload.get("optionLabel")
            if (
                not isinstance(expected_task_title, str)
                or not expected_task_title.strip()
                or len(expected_task_title.strip()) > 1_000
                or not isinstance(fingerprint, str)
                or len(fingerprint) != 64
                or any(character not in "0123456789abcdef" for character in fingerprint)
                or action not in {"approve_once", "deny", "answer", "skip"}
                or (answer is not None and not isinstance(answer, str))
                or (isinstance(answer, str) and len(answer) > 8_000)
                or (option_label is not None and not isinstance(option_label, str))
                or (isinstance(option_label, str) and len(option_label) > 1_000)
            ):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "invalid_desktop_request_response"},
                )
                return
            try:
                response = self.server.controller.respond_to_desktop_request(
                    expected_task_title.strip(),
                    fingerprint,
                    action,
                    answer=answer,
                    option_label=option_label,
                )
            except DesktopRequestError as error:
                conflict_reasons = {
                    "foreground_task_changed",
                    "desktop_request_unavailable",
                    "desktop_request_changed",
                    "desktop_request_response_invalid",
                }
                self._send_json(
                    HTTPStatus.CONFLICT
                    if error.reason in conflict_reasons
                    else HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": error.reason},
                )
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "response": response})
            return
        if self.path != "/api/desktop/interrupt":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._require_control_auth():
            return
        payload = self._read_json()
        if payload is None:
            return
        if payload.get("confirm") is not True:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "confirmation_required"})
            return
        expected_task_title = payload.get("expectedTaskTitle")
        if not isinstance(expected_task_title, str) or not expected_task_title.strip():
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "task_identity_required"})
            return
        thread_id = payload.get("threadId")
        if (
            not isinstance(thread_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{5,255}", thread_id)
        ):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "thread_identity_required"})
            return
        try:
            self.server.controller.interrupt_thread(
                thread_id,
                expected_task_title.strip(),
            )
        except ThreadNavigationError:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"ok": False, "error": "thread_navigation_failed"},
            )
            return
        except TaskChangedError:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"ok": False, "error": "foreground_task_changed"},
            )
            return
        except TaskIdentityError:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"ok": False, "error": "foreground_task_unknown"},
            )
            return
        except StopCandidateError as error:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "ok": False,
                    "error": "active_stop_button_not_unique",
                    "stopCandidates": error.count,
                },
            )
            return
        except RuntimeError:
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "error": "accessibility_interrupt_failed"},
            )
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "interrupted": True})

    def do_DELETE(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        attachment_prefix = "/api/attachments/"
        if path.startswith(attachment_prefix):
            device = self._device_identity()
            if device is None:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "device_required"})
                return
            attachment_id = urllib.parse.unquote(path[len(attachment_prefix) :])
            if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", attachment_id):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_attachment_id"},
                )
                return
            try:
                removed = self.server.attachment_store.delete(
                    attachment_id,
                    device["id"],
                )
            except PermissionError:
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "attachment_not_owned"},
                )
                return
            if not removed:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "attachment_not_found"},
                )
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "deleted": attachment_id})
            return
        prefix = "/api/devices/"
        if not path.startswith(prefix) or path == "/api/devices/self":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._require_master_auth():
            return
        device_id = urllib.parse.unquote(path[len(prefix) :])
        if not device_id or "/" in device_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_device_id"})
            return
        if not self.server.device_registry.revoke(device_id):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "device_not_found"})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "revoked": device_id})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--token-file",
        type=Path,
        help="Read the bridge token from a user-only file instead of the environment.",
    )
    parser.add_argument(
        "--keychain-service",
        help="Read the bridge token from a macOS Keychain generic-password service.",
    )
    parser.add_argument(
        "--ax-script",
        type=Path,
        default=Path(__file__).resolve().parent / "scripts" / "codex-ax.swift",
    )
    parser.add_argument(
        "--ax-helper",
        type=Path,
        help="Run a compiled, Accessibility-authorized helper instead of swift.",
    )
    parser.add_argument(
        "--device-registry",
        type=Path,
        default=(
            Path.home()
            / "Library"
            / "Application Support"
            / "MobileCodexBridge"
            / "devices.json"
        ),
        help="Persistent registry of individually revocable paired devices.",
    )
    parser.add_argument(
        "--codex-binary",
        type=Path,
        default=Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        help="Version-matched Codex binary used for the private stdio app-server.",
    )
    return parser.parse_args()


def load_token(
    token_file: Optional[Path],
    keychain_service: Optional[str] = None,
) -> str:
    if token_file is not None and keychain_service is not None:
        raise SystemExit("Choose either --token-file or --keychain-service, not both.")
    if keychain_service is not None:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-w",
                "-s",
                keychain_service,
                "-a",
                os.environ.get("USER", ""),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise SystemExit("Unable to read bridge token from macOS Keychain.")
        return result.stdout.strip()
    if token_file is None:
        return os.environ.get("MOBILE_CODEX_BRIDGE_TOKEN", "")
    resolved = token_file.expanduser().resolve()
    try:
        metadata = resolved.stat()
    except OSError as error:
        raise SystemExit(f"Unable to read bridge token file: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit("Bridge token path must be a regular file.")
    if metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit("Bridge token file must not be accessible by group or other users.")
    try:
        return resolved.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise SystemExit(f"Unable to read bridge token file: {error}") from error


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("Refusing non-loopback bind; use a local reverse proxy or Tailscale Serve.")
    token = load_token(args.token_file, args.keychain_service)
    if len(token) < 32:
        raise SystemExit("MOBILE_CODEX_BRIDGE_TOKEN must contain at least 32 characters.")
    ax_helper = args.ax_helper.resolve() if args.ax_helper is not None else None
    if ax_helper is not None and not os.access(ax_helper, os.X_OK):
        raise SystemExit("Accessibility helper is missing or not executable.")
    controller = DesktopController(args.ax_script.resolve(), ax_helper=ax_helper)
    device_registry = DeviceRegistry(args.device_registry.expanduser().resolve())
    try:
        device_registry.ensure_ready()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    app_server: Optional[CodexAppServerClient] = None
    if os.access(args.codex_binary, os.X_OK):
        candidate = CodexAppServerClient(args.codex_binary.resolve())
        try:
            candidate.start()
            app_server = candidate
            print("managed Codex app-server connected over private stdio")
        except AppServerError:
            print("managed Codex app-server is unavailable; task browsing is disabled")
    server = BridgeServer(
        (args.host, args.port),
        token,
        controller,
        device_registry=device_registry,
        app_server=app_server,
    )
    print(f"mac-codex-bridge listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
