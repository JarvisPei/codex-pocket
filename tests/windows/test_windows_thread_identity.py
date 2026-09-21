from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from platforms.windows.native import NativeTextController
from platforms.windows.thread_identity import unique_empty_thread_name, recent_assignment_matches


class EmptyThreadIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(self.root / 'state_5.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, preview TEXT, archived INTEGER, has_user_event INTEGER)')
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', ('target', 'Exact name', '', '', 0, 0))
        self.db.commit()

    def check(self):
        return unique_empty_thread_name('target', 'Exact name', codex_home=self.root)

    def test_exact_empty_name_is_unique_and_read_only(self):
        before = (self.root / 'state_5.sqlite').read_bytes()
        self.assertTrue(self.check())
        self.assertFalse(unique_empty_thread_name('other', 'Exact name', codex_home=self.root))
        self.assertFalse(unique_empty_thread_name('target', 'exact name', codex_home=self.root))
        self.assertEqual(before, (self.root / 'state_5.sqlite').read_bytes())

    def test_duplicate_hidden_or_visible_names_are_refused(self):
        for field in ('name', 'title', 'preview'):
            with self.subTest(field=field):
                self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', ('other', '', '', '', 0, 0))
                self.db.execute('UPDATE threads SET ' + field + '=? WHERE id=?', ('Exact name', 'other'))
                self.db.commit()
                self.assertFalse(self.check())
                self.db.execute('DELETE FROM threads WHERE id=?', ('other',))
                self.db.commit()

    def test_nonempty_or_archived_target_is_not_a_fallback(self):
        for archived, event in ((1, 0), (0, 1)):
            self.db.execute('UPDATE threads SET archived=?,has_user_event=?', (archived, event))
            self.db.commit()
            self.assertFalse(self.check())

    def test_renamed_tasks_historical_title_is_not_a_visible_name_collision(self):
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)',
                        ('renamed', 'Different visible name', 'Exact name', 'Exact name', 0, 0))
        self.db.commit()
        self.assertTrue(self.check())
        self.db.execute('UPDATE threads SET name=? WHERE id=?', ('Exact name', 'renamed'))
        self.db.commit()
        self.assertFalse(self.check())

    def test_unnamed_task_with_null_name_still_blocks_historical_name(self):
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)',
                        ('unnamed', None, 'Exact name', '', 0, 0))
        self.db.commit()
        self.assertFalse(self.check())

    def test_missing_or_newer_unknown_schema_fails_closed(self):
        self.assertFalse(unique_empty_thread_name('target', 'Exact name', codex_home=self.root / 'missing'))
        self.assertFalse((self.root / 'missing').exists())
        with closing(sqlite3.connect(self.root / 'state_6.sqlite')) as db:
            db.execute('CREATE TABLE unrelated (id TEXT)')
            db.commit()
        self.assertFalse(self.check())

    def test_api_errors_duplicates_and_incomplete_scans_cannot_fallback(self):
        client = Mock()
        native = NativeTextController(client, self.root, 'test', dispatch=Mock())
        with patch('platforms.windows.native.unique_empty_thread_name', return_value=True) as local:
            client.request.return_value = {'data': [], 'nextCursor': None}
            self.assertTrue(native._unique_title('target', 'Exact name'))
            local.assert_called_once_with('target', 'Exact name')
            local.reset_mock()
            for result in ({'data': None}, {'data': [{'id': 'other', 'name': 'Exact name'}]},
                           {'data': [], 'nextCursor': 'loop'}):
                client.request.return_value = result
                self.assertFalse(native._unique_title('target', 'Exact name'))
            local.assert_not_called()

    def test_recents_requires_persisted_null_project_and_exact_workspace(self):
        self.db.execute('ALTER TABLE threads ADD COLUMN cwd TEXT')
        self.db.execute('ALTER TABLE threads ADD COLUMN project_id TEXT')
        self.db.execute('UPDATE threads SET cwd=?', (r'\\?\C:\Users\Test\Task',))
        self.db.commit()
        check = lambda path: recent_assignment_matches('target', path, codex_home=self.root)
        self.assertTrue(check('C:/Users/Test/Task'))
        self.assertFalse(check('C:/Users/Test/Other'))
        self.assertFalse(check('relative'))
        self.db.execute('UPDATE threads SET project_id=?', ('user-moved-project',))
        self.db.commit()
        self.assertFalse(check('C:/Users/Test/Task'))
        self.db.execute('UPDATE threads SET project_id=NULL,archived=1')
        self.db.commit()
        self.assertFalse(check('C:/Users/Test/Task'))

    def test_recents_unknown_schema_does_not_guess(self):
        self.assertFalse(recent_assignment_matches('target', 'C:/Task', codex_home=self.root))
