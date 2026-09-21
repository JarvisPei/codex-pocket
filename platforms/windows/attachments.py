"""Opt-in local-file handoff, not native image chips or app-server image input.

Uploads inherit Pocket's protected same-user directory ACL. Handoff snapshots
are retained separately so upload expiry/removal cannot break a submitted path.
No file is executed, and filenames never become shell arguments.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from mac_bridge import AttachmentStore, MAX_ATTACHMENT_BYTES


def safe_path(path):
    """Check lexical ancestors before resolve(), including Windows junctions."""
    path = Path(path)
    for entry in (*reversed(path.parents), path):
        if entry.exists() or entry.is_symlink():
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('attachment_reparse_point_refused')


class WindowsAttachmentStore(AttachmentStore):
    @staticmethod
    def validate_filename(filename):
        name = AttachmentStore.validate_filename(filename)
        if (any(c in name for c in '<>:"|?*') or name.endswith((' ', '.'))
                or name.lower() == 'metadata.json'
                or re.fullmatch(r'(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])', name.split('.')[0].rstrip(' '), re.I)):
            raise ValueError('invalid_attachment_name')
        return name

    def _ensure_root(self):
        safe_path(self.root)
        if sys.platform == 'win32':
            # Python 3.13's special 0700 DACL adds Owner Rights/Administrators.
            # Inherit the already validated Pocket parent ACL instead.
            self.root.mkdir(parents=True, exist_ok=True)
        else:
            super()._ensure_root()

    def _create_upload_directory(self, directory):
        safe_path(directory)
        if sys.platform == 'win32':
            directory.mkdir()
        else:
            super()._create_upload_directory(directory)

    def _load_unlocked(self, attachment_id):
        if not isinstance(attachment_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', attachment_id):
            raise KeyError(attachment_id)
        safe_path(self.root / attachment_id / 'metadata.json')
        metadata = super()._load_unlocked(attachment_id)
        name = self.validate_filename(str(metadata.get('name', '')))
        safe_path(self.root / attachment_id / name)
        return metadata

    def _remove_unlocked(self, attachment_id):
        safe_path(self.root / attachment_id)
        # Refuse suspicious entries instead of following a junction at cleanup.
        directory = self.root / attachment_id
        if directory.is_dir():
            for entry in directory.iterdir():
                safe_path(entry)
                if not entry.is_file():
                    raise ValueError('invalid_attachment_storage')
        super()._remove_unlocked(attachment_id)

    def purge_expired(self):
        safe_path(self.root)
        super().purge_expired()

    def handoff(self, attachments):
        """Retain immutable snapshots. Caller already resolved device ownership."""
        paths = []
        with self._lock:
            retained = self.root.parent / 'attachment-handoffs'
            safe_path(retained)
            if sys.platform == 'win32':
                retained.mkdir(exist_ok=True)
            else:
                retained.mkdir(mode=0o700, exist_ok=True)
            for item in attachments:
                source = Path(item['path'])
                expected = self.root / item['id'] / self.validate_filename(item['name'])
                safe_path(expected)
                if source != expected.resolve(strict=True):
                    raise ValueError('attachment_not_found')
                info = source.stat()
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ATTACHMENT_BYTES:
                    raise ValueError('attachment_not_found')
                with source.open('rb') as stream:
                    content = stream.read(MAX_ATTACHMENT_BYTES + 1)
                if len(content) != item['size'] or len(content) > MAX_ATTACHMENT_BYTES:
                    raise ValueError('attachment_not_found')
                digest = hashlib.sha256(content).hexdigest()
                # Preserve only a safe format extension; title/path injection
                # through the original display name is never possible here.
                suffix = source.suffix.lower()
                if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
                    suffix = '.bin'
                path = retained / (item['id'] + '-' + digest + suffix)
                safe_path(path)
                if path.exists():
                    if not path.is_file() or path.stat().st_size != len(content):
                        raise ValueError('attachment_snapshot_changed')
                    with path.open('rb') as stream:
                        if hashlib.sha256(stream.read(MAX_ATTACHMENT_BYTES + 1)).hexdigest() != digest:
                            raise ValueError('attachment_snapshot_changed')
                else:
                    # Exclusive creation; a crash leaves an unconfirmed snapshot
                    # that fails size/hash validation, never a silently bad path.
                    with path.open('xb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                paths.append(path.resolve(strict=True).as_posix())
        return paths

    def workspace_handoff(self, paths, cwd):
        """Copy verified retained uploads into a server-verified task workspace.

        Neither cwd nor paths may come from client-supplied file paths. Inherit
        the workspace ACL, not Pocket's credential ACL; never modify either.
        Files remain local/untracked and are not automatically deleted.
        """
        if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
            raise ValueError('attachment_workspace_unavailable')
        workspace = Path(cwd)
        safe_path(workspace)
        if not workspace.is_dir() or workspace.resolve().is_relative_to(self.root.parent.resolve()):
            raise ValueError('attachment_workspace_unavailable')
        retained = self.root.parent / 'attachment-handoffs'
        target = workspace / '.codex-pocket-attachments'
        copies = []
        with self._lock:
            safe_path(target)
            # Deliberately inherit the task workspace permissions on Windows.
            # mode=0700 on Python 3.13 would exclude sandbox users again.
            target.mkdir(exist_ok=True)
            safe_path(target)
            for value in paths:
                source = Path(value)
                match = re.fullmatch(r'[A-Za-z0-9_-]{16,64}-([0-9a-f]{64})(\.[a-z0-9]{1,12})', source.name)
                safe_path(source)
                if not match or source != (retained / source.name).resolve(strict=True):
                    raise ValueError('attachment_snapshot_changed')
                if not source.is_file() or not 0 < source.stat().st_size <= MAX_ATTACHMENT_BYTES:
                    raise ValueError('attachment_snapshot_changed')
                with source.open('rb') as stream:
                    content = stream.read(MAX_ATTACHMENT_BYTES + 1)
                if len(content) > MAX_ATTACHMENT_BYTES or hashlib.sha256(content).hexdigest() != match[1]:
                    raise ValueError('attachment_snapshot_changed')
                destination = target / source.name
                safe_path(destination)
                if destination.exists():
                    if not destination.is_file() or destination.stat().st_size != len(content):
                        raise ValueError('attachment_workspace_copy_changed')
                    with destination.open('rb') as stream:
                        if hashlib.sha256(stream.read(MAX_ATTACHMENT_BYTES + 1)).hexdigest() != match[1]:
                            raise ValueError('attachment_workspace_copy_changed')
                else:
                    with destination.open('xb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                copies.append(destination.resolve(strict=True).as_posix())
        return copies


def file_message(message, paths):
    if not isinstance(message, str) or len(message) > 20_000 or '\0' in message:
        raise ValueError('invalid_delivery_message')
    if not paths:
        return message
    body = message if message.strip() else '请查看我上传的文件。'
    body += '\n\n已上传到本机的文件（路径列表，不是原生图片附件）：\n'
    body += json.dumps(paths, ensure_ascii=False)
    body += '\n请按我的问题读取这些文件；如果当前模型或工具不能读取，请明确告知。'
    if len(body) > 20_000:
        raise ValueError('invalid_delivery_message')
    return body
