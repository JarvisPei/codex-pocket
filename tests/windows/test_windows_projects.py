import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from codex_app_server import summarize_thread
from platforms.windows.projects import PINNED_SECTION_ID, load_windows_project_index


class WindowsProjectsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / '.codex-global-state.json'
        self.state.write_text(json.dumps({'local-projects': {
            'old': {'name': 'Pocket', 'rootPaths': [str(self.root)]}}}))
        self.db = sqlite3.connect(self.root / 'state_5.sqlite')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE projects (id TEXT,name TEXT,position INTEGER);
            CREATE TABLE project_roots (project_id TEXT,position INTEGER,path TEXT);
            CREATE TABLE threads (id TEXT,project_id TEXT,archived INTEGER);
            INSERT INTO projects VALUES ('new','Pocket',0);
            INSERT INTO threads VALUES ('member','new',0),('recent',NULL,0),('orphan','deleted',0);
        ''')
        self.db.execute('INSERT INTO project_roots VALUES (?,?,?)', ('new', 0, str(self.root)))
        self.db.commit()

    def test_modern_ids_replace_legacy_and_recents_do_not_infer_by_path(self):
        before = self.state.read_bytes()
        index = load_windows_project_index(self.state)
        self.assertEqual(set(index['projects']), {'new'})
        for tid, expected in [('member', 'new'), ('recent', None), ('orphan', None)]:
            summary = summarize_thread({'id': tid, 'cwd': str(self.root)}, index)
            self.assertEqual((summary['project'] or {}).get('id'), expected)
        self.assertEqual(before, self.state.read_bytes())

    def test_empty_modern_projects_do_not_resurrect_legacy(self):
        self.db.execute('DELETE FROM projects')
        self.db.commit()
        self.assertEqual(load_windows_project_index(self.state)['projects'], {})

    def test_incomplete_modern_schema_fails_closed(self):
        self.db.execute('DROP TABLE project_roots')
        self.db.commit()
        with self.assertRaises(sqlite3.Error):
            load_windows_project_index(self.state)

    def test_legacy_database_remains_supported(self):
        self.db.execute('DROP TABLE projects')
        self.db.commit()
        self.assertEqual(set(load_windows_project_index(self.state)['projects']), {'old'})

    def add_modern_pins(self):
        self.db.execute('ALTER TABLE threads ADD COLUMN is_pinned INTEGER DEFAULT 0')
        self.db.commit()

    def test_database_pins_include_project_and_recent_but_not_archived(self):
        self.add_modern_pins()
        self.db.execute('UPDATE threads SET is_pinned=1')
        self.db.execute("UPDATE threads SET archived=1 WHERE id='orphan'")
        self.db.commit()
        before_state = self.state.read_bytes()
        before_db = self.db.execute('SELECT * FROM threads').fetchall()
        index = load_windows_project_index(self.state)
        self.assertEqual(index['pinnedThreadIds'], {'member', 'recent'})
        self.assertTrue(index['authoritativePins'])
        for tid, expected in [('member', True), ('recent', True), ('orphan', False)]:
            self.assertEqual(summarize_thread({'id': tid}, index)['isPinned'], expected)
        self.assertEqual(self.state.read_bytes(), before_state)
        self.assertEqual(self.db.execute('SELECT * FROM threads').fetchall(), before_db)

    def test_modern_unpin_overrides_stale_json_and_api(self):
        self.add_modern_pins()
        state = json.loads(self.state.read_text())
        state['pinned-thread-ids'] = ['member']
        self.state.write_text(json.dumps(state))
        self.db.execute("UPDATE threads SET is_pinned=1 WHERE id='member'")
        self.db.commit()
        self.assertTrue(summarize_thread({'id': 'member'},
                        load_windows_project_index(self.state))['isPinned'])
        self.db.execute("UPDATE threads SET is_pinned=0 WHERE id='member'")
        self.db.commit()
        index = load_windows_project_index(self.state)
        self.assertEqual(index['pinnedThreadIds'], set())
        self.assertFalse(summarize_thread({'id': 'member', 'isPinned': True}, index)['isPinned'])

    def test_legacy_pins_and_api_remain_supported_without_column(self):
        state = json.loads(self.state.read_text())
        state['pinned-thread-ids'] = ['member']
        self.state.write_text(json.dumps(state))
        index = load_windows_project_index(self.state)
        self.assertFalse(index.get('authoritativePins', False))
        self.assertTrue(summarize_thread({'id': 'member'}, index)['isPinned'])
        self.assertTrue(summarize_thread({'id': 'recent', 'isPinned': True}, index)['isPinned'])

    def test_pin_migration_does_not_require_project_migration(self):
        self.add_modern_pins()
        self.db.execute('DROP TABLE projects')
        self.db.execute("UPDATE threads SET is_pinned=1 WHERE id='recent'")
        self.db.commit()
        index = load_windows_project_index(self.state)
        self.assertEqual(set(index['projects']), {'old'})
        self.assertEqual(index['pinnedThreadIds'], {'recent'})
        self.assertTrue(index['authoritativePins'])

    def add_sections(self):
        self.add_modern_pins()
        self.db.execute('ALTER TABLE threads ADD COLUMN thread_section_id TEXT')
        self.db.execute('CREATE TABLE thread_sections (id TEXT PRIMARY KEY,name TEXT)')
        self.db.execute('INSERT INTO thread_sections VALUES (?,?)', (PINNED_SECTION_ID, '置顶'))
        self.db.execute("INSERT INTO thread_sections VALUES ('custom','Pinned')")
        self.db.commit()

    def test_desktop_section_pin_with_zero_legacy_flag(self):
        # Real Desktop observation: is_pinned=0, section=Pinned, API lacks isPinned.
        self.add_sections()
        self.db.execute("UPDATE threads SET thread_section_id=? WHERE id='member'", (PINNED_SECTION_ID,))
        self.db.execute("UPDATE threads SET thread_section_id='custom' WHERE id='recent'")
        self.db.execute("UPDATE threads SET thread_section_id=?,archived=1 WHERE id='orphan'", (PINNED_SECTION_ID,))
        self.db.commit()
        before = self.db.execute('SELECT * FROM threads').fetchall()
        before_state = self.state.read_bytes()
        index = load_windows_project_index(self.state)
        self.assertEqual(index['pinnedThreadIds'], {'member'})
        self.assertTrue(summarize_thread({'id': 'member'}, index)['isPinned'])
        self.assertFalse(summarize_thread({'id': 'recent'}, index)['isPinned'])
        self.assertEqual(self.db.execute('SELECT * FROM threads').fetchall(), before)
        self.assertEqual(self.state.read_bytes(), before_state)

    def test_section_unpin_overrides_stale_database_json_and_api_flags(self):
        self.add_sections()
        state = json.loads(self.state.read_text())
        state['pinned-thread-ids'] = ['member']
        self.state.write_text(json.dumps(state))
        self.db.execute("UPDATE threads SET is_pinned=1,thread_section_id=? WHERE id='member'", (PINNED_SECTION_ID,))
        self.db.commit()
        self.assertTrue(summarize_thread({'id': 'member'}, load_windows_project_index(self.state))['isPinned'])
        for section in (None, 'custom'):
            self.db.execute("UPDATE threads SET thread_section_id=? WHERE id='member'", (section,))
            self.db.commit()
            index = load_windows_project_index(self.state)
            self.assertEqual(index['pinnedThreadIds'], set())
            self.assertFalse(summarize_thread({'id': 'member', 'isPinned': True}, index)['isPinned'])
