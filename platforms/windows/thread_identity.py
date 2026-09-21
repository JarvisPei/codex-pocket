"""Read-only identity check for persisted empty tasks omitted by thread/list."""
import os
import ntpath
from pathlib import Path
import re
import sqlite3
from contextlib import closing


def _latest_database(codex_home):
    root = Path(codex_home) if codex_home is not None else Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    databases = sorted((p for p in root.glob('state_*.sqlite')
                        if re.fullmatch(r'state_\d+\.sqlite', p.name)),
                       key=lambda p: int(p.stem.split('_')[1]), reverse=True)
    return databases[0] if databases else None


def _windows_path(value):
    if not isinstance(value, str) or not ntpath.isabs(value):
        return None
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def recent_assignment_matches(thread_id, cwd, *, codex_home=None):
    """Verify modern Desktop's unassigned collection without writing metadata."""
    try:
        database = _latest_database(codex_home)
        expected = _windows_path(cwd)
        if database is None or expected is None:
            return False
        with closing(sqlite3.connect(database.absolute().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            rows = db.execute('SELECT cwd,project_id,archived FROM threads WHERE id=? LIMIT 2', (thread_id,)).fetchall()
        return (len(rows) == 1 and rows[0][1:] == (None, 0)
                and _windows_path(rows[0][0]) == expected)
    except (OSError, sqlite3.Error, ValueError):
        return False


def unique_empty_thread_name(thread_id, title, *, codex_home=None):
    """Fail closed unless the current local metadata proves an exact unique name.

    Never creates/repairs databases, resumes a task, or treats missing metadata
    as permission. Only supplements a successfully completed API list scan.
    """
    try:
        database = _latest_database(codex_home)
        if database is None:
            return False
        # Newest schema only: never trust an older leftover database on failure.
        with closing(sqlite3.connect(database.absolute().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            rows = db.execute(
                'SELECT id,name,archived,has_user_event FROM threads WHERE id=? '
                # A renamed task retains its original title/preview. Only use
                # those fallback fields when it has no current explicit name,
                # matching the API/UI name precedence used for navigation.
                'OR (archived=0 AND (name=? OR '
                "((name IS NULL OR name='') AND (title=? OR preview=?)))) LIMIT 3",
                (thread_id, title, title, title),
            ).fetchall()
        return rows == [(thread_id, title, 0, 0)]
    except (OSError, sqlite3.Error, ValueError):
        return False
