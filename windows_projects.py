"""Read Desktop's modern project snapshot; never write its database."""
from contextlib import closing
import sqlite3

from codex_app_server import load_codex_project_index
from windows_thread_identity import _latest_database

# Built-in section identity used by Desktop's section-backed pinning (verified
# in Windows Desktop 26.915.3509). Do not match the localized/editable name.
PINNED_SECTION_ID = '01984de2-8f74-7c91-a3b2-5c5e937cf318'


def load_windows_project_index(state_path):
    legacy = load_codex_project_index(state_path)
    database = _latest_database(state_path.parent)
    if database is None:
        return legacy
    # One read transaction prevents mixing projects and assignments from
    # different Desktop updates. Errors propagate: never silently use stale IDs.
    with closing(sqlite3.connect(database.absolute().as_uri() + '?mode=ro',
                                uri=True, timeout=1)) as db:
        db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Pins migrated independently of projects. An empty modern pin set is
        # authoritative too: merging stale JSON pins would undo an unpin.
        columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
        if 'thread_section_id' in columns and 'thread_sections' in tables:
            # New Desktop pins by section and leaves is_pinned at its old value.
            # Section membership must also win after unpin/move-to-section.
            legacy = {**legacy, 'authoritativePins': True, 'pinnedThreadIds': {
                tid for (tid,) in db.execute(
                    'SELECT id FROM threads WHERE archived=0 AND thread_section_id=?',
                    (PINNED_SECTION_ID,))
            }}
        elif 'is_pinned' in columns:
            legacy = {**legacy, 'authoritativePins': True, 'pinnedThreadIds': {
                tid for (tid,) in db.execute(
                    'SELECT id FROM threads WHERE archived=0 AND is_pinned=1')
            }}
        if 'projects' not in tables:
            return legacy
        projects = {}
        for pid, name, position in db.execute('SELECT id,name,position FROM projects'):
            roots = db.execute('SELECT path FROM project_roots WHERE project_id=? ORDER BY position', (pid,)).fetchall()
            projects[pid] = {'id': pid, 'name': name, 'order': position,
                             'path': roots[0][0] if roots else None}
        assignments, projectless = {}, set()
        for tid, pid in db.execute('SELECT id,project_id FROM threads WHERE archived=0'):
            if pid is None:
                projectless.add(tid)
            else:
                assignments[tid] = pid
    return {**legacy, 'projects': projects, 'assignments': assignments,
            'projectlessThreadIds': projectless, 'authoritativeProjects': True,
            'projectBackend': 'app-server'}
