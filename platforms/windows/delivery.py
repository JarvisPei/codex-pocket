"""Durable, at-most-once dispatch bookkeeping for the Windows native adapter.

Does not operate UI or start an app-server turn. Store under Pocket's protected
per-user directory. Only request hashes/receipt IDs are persisted, never prompts.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import stat
import time


UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def validate_delivery(request_id: str, thread_id: str, message: str) -> str:
    if not all(isinstance(v, str) and UUID.fullmatch(v) for v in (request_id, thread_id)):
        raise ValueError("invalid_delivery_id")
    if (not isinstance(message, str) or not message.strip() or len(message) > 20_000
            or "\0" in message):
        raise ValueError("invalid_delivery_message")
    canonical = json.dumps([thread_id, message], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def user_receipts(thread: dict, thread_id: str, message: str) -> set[str]:
    """Exact message AND stable turn/item ID from the exact requested thread.

    No substring matches and no text-only fingerprint: repeated identical user
    messages are legitimate, and an old one must never confirm a new dispatch.
    """
    if not isinstance(thread, dict) or thread.get("id") != thread_id:
        return set()
    expected = {message, message + "\n"}
    # Desktop 26.915 escapes underscores and serializes editor line breaks as
    # Markdown hard breaks. Construct observed whole-message representations;
    # never unescape arbitrary history or normalize whitespace/backslashes.
    # Literal backslashes/CR are deliberately unsupported by this alternative.
    if "\\" not in message and "\r" not in message:
        serialized = message.replace("_", "\\_")
        expected.update((serialized, serialized + "\n"))
        if "\n" in message:
            hard_breaks = serialized.replace("\n", "\\\n")
            expected.update((hard_breaks, hard_breaks + "\n"))
    receipts = set()
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return receipts
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]:
            continue
        if not isinstance(turn.get("items"), list):
            continue
        for item in turn["items"]:
            if (not isinstance(item, dict) or item.get("type") != "userMessage"
                    or not isinstance(item.get("id"), str) or not item["id"]):
                continue
            content = item.get("content")
            if not isinstance(content, list) or not content:
                continue
            # This first adapter is text-only; attachments or unknown parts
            # must not accidentally confirm a different request.
            if any(not isinstance(p, dict) or p.get("type") != "text"
                   or not isinstance(p.get("text"), str) for p in content):
                continue
            text = "\n".join(p["text"] for p in content)
            # Desktop 26.915 serializes one terminal LF after the submitted
            # composer text. Do not strip arbitrary whitespace or use substrings.
            if text in expected:
                receipts.add(json.dumps([turn["id"], item["id"]], separators=(",", ":")))
    return receipts


class DeliveryJournal:
    def __init__(self, path: Path):
        self.path = path
        # Parent ACL is established/validated by windows_token in the adapter.
        # Never accept a symlink/junction as the database or its sidecars.
        for entry in (path.parent, path, Path(str(path) + "-journal"),
                      Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            if entry.exists() or entry.is_symlink():
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("delivery_reparse_point_refused")
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                thread_id TEXT NOT NULL, state TEXT NOT NULL,
                receipt TEXT, updated REAL NOT NULL, baseline TEXT NOT NULL)""")

    def connect(self):
        # FULL + an explicit transaction commits the claim before any UI write.
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def claim(self, request_id: str, thread_id: str, message: str, baseline: set[str]) -> dict:
        fingerprint = validate_delivery(request_id, thread_id, message)
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT fingerprint,state,receipt,baseline FROM deliveries WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError("delivery_id_reused_with_different_content")
                return {"dispatch": False, "state": row[1], "receipt": row[2], "baseline": set(json.loads(row[3]))}
            # A pending/uncertain dispatch blocks a fresh ID too. The operator
            # must reconcile history first, rather than retry into the same task.
            pending = db.execute("SELECT 1 FROM deliveries WHERE thread_id=? AND state IN ('claimed','uncertain') LIMIT 1", (thread_id,)).fetchone()
            if pending:
                raise ValueError("delivery_confirmation_pending")
            db.execute("INSERT INTO deliveries VALUES (?,?,?,'claimed',NULL,?,?)", (request_id, fingerprint, thread_id, time.time(), json.dumps(sorted(baseline))))
            db.commit()
            return {"dispatch": True, "state": "claimed", "receipt": None, "baseline": baseline}
        finally:
            db.close()

    def lookup(self, request_id: str, thread_id: str, message: str):
        """Receipt-only lookup: never claim a missing request or permit dispatch."""
        fingerprint = validate_delivery(request_id, thread_id, message)
        with closing(self.connect()) as db:
            row = db.execute('SELECT fingerprint,state,receipt,baseline FROM deliveries WHERE request_id=?',
                             (request_id,)).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise ValueError('delivery_id_reused_with_different_content')
        return {'dispatch': False, 'state': row[1], 'receipt': row[2],
                'baseline': set(json.loads(row[3]))}

    def finish(self, request_id: str, state: str, receipt: str | None = None) -> None:
        if state not in {"refused", "uncertain", "confirmed"}:
            raise ValueError("invalid_delivery_state")
        if state == "confirmed" and (not isinstance(receipt, str) or not receipt):
            raise ValueError("receipt_required")
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM deliveries WHERE request_id=?", (request_id,)).fetchone()
            if not row or row[0] not in {"claimed", "uncertain"}:
                raise ValueError("delivery_transition_refused")
            if row[0] == "uncertain" and state != "confirmed":
                raise ValueError("uncertain_delivery_requires_receipt")
            db.execute("UPDATE deliveries SET state=?,receipt=?,updated=? WHERE request_id=?", (state, receipt, time.time(), request_id))
            db.commit()
        finally:
            db.close()
