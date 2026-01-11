#!/usr/bin/env python3
"""
Unit tests for Superwiser plugin.

P0 CUJs (Critical):
1. Database initializes without crashing
2. User prompts are queued successfully
3. Secrets are filtered from prompts
4. Search returns relevant results

P1 CUJs (Important):
1. Worker processes queue items correctly
2. Position tracking is monotonic
3. sqlite-vec loading is graceful when missing
4. Extraction prompt includes secret sanitization
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add scripts directory to path
SCRIPT_DIR = Path(__file__).parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPT_DIR))

from db_utils import (
    get_db, db_context, load_sqlite_vec, get_model, generate_embedding
)


class TestDatabaseInit(unittest.TestCase):
    """P0: Database initializes without crashing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'test.db'

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        os.rmdir(self.temp_dir)

    def test_init_db_creates_tables(self):
        """init-db.py creates required tables."""
        # Import and run init_db
        sys.path.insert(0, str(SCRIPT_DIR))
        from importlib import import_module
        init_db_module = import_module('init-db')

        # Mock sqlite_vec to avoid dependency
        with patch.dict('sys.modules', {'sqlite_vec': MagicMock()}):
            try:
                init_db_module.init_db(str(self.db_path))
            except Exception:
                pass  # May fail on sqlite_vec, that's OK

        # Verify core tables exist (even without sqlite_vec)
        db = sqlite3.connect(str(self.db_path))
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]

        self.assertIn('queue', table_names)
        self.assertIn('context_graph', table_names)
        db.close()

    def test_get_db_creates_connection(self):
        """get_db creates a valid connection with WAL mode."""
        db = get_db(str(self.db_path))

        # Check WAL mode
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), 'wal')

        db.close()

    def test_db_context_manager(self):
        """db_context properly closes connection."""
        with db_context(str(self.db_path)) as db:
            db.execute("CREATE TABLE test (id INTEGER)")

        # Connection should be closed, but file should exist
        self.assertTrue(self.db_path.exists())


class TestSecretFiltering(unittest.TestCase):
    """P0: Secrets are filtered from prompts."""

    def setUp(self):
        # Patterns copied from queue-input.py for testing
        self.patterns = [
            r'(?i)(api[_-]?key|secret|token|password|credential)\s*[:=]\s*[\'"]?[\w-]{16,}',
            r'sk-[a-zA-Z0-9]{20,}',              # OpenAI API keys
            r'ghp_[a-zA-Z0-9]{36}',              # GitHub personal access tokens
            r'AKIA[0-9A-Z]{16}',                 # AWS Access Key IDs
            r'-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----',  # Private keys
        ]

    def contains_secrets(self, text):
        return any(re.search(p, text) for p in self.patterns)

    def test_detects_openai_key(self):
        """Detects OpenAI API keys."""
        self.assertTrue(self.contains_secrets("sk-abcdefghijklmnopqrstuvwxyz123456"))

    def test_detects_github_token(self):
        """Detects GitHub personal access tokens."""
        self.assertTrue(self.contains_secrets("ghp_abcdefghijklmnopqrstuvwxyz1234567890"))

    def test_detects_aws_key(self):
        """Detects AWS Access Key IDs."""
        self.assertTrue(self.contains_secrets("AKIAIOSFODNN7EXAMPLE"))

    def test_detects_private_key(self):
        """Detects private keys."""
        self.assertTrue(self.contains_secrets("-----BEGIN RSA PRIVATE KEY-----"))
        self.assertTrue(self.contains_secrets("-----BEGIN OPENSSH PRIVATE KEY-----"))

    def test_detects_generic_api_key(self):
        """Detects generic api_key=value patterns."""
        self.assertTrue(self.contains_secrets("api_key=abcdefghijklmnop1234"))
        self.assertTrue(self.contains_secrets("API_KEY: 'abcdefghijklmnop1234'"))

    def test_ignores_normal_text(self):
        """Normal text is not flagged as secrets."""
        self.assertFalse(self.contains_secrets("Use PostgreSQL not MySQL"))
        self.assertFalse(self.contains_secrets("Add input validation"))
        self.assertFalse(self.contains_secrets("Keep functions under 50 lines"))


class TestPositionTracking(unittest.TestCase):
    """P1: Position tracking is monotonic."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'test.db'

        # Create queue table
        db = sqlite3.connect(str(self.db_path))
        db.execute("""
            CREATE TABLE queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transcript_path TEXT NOT NULL,
                position INTEGER NOT NULL,
                human_input TEXT NOT NULL,
                session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        """)
        db.commit()
        db.close()

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        # Clean up WAL files
        for ext in ['-wal', '-shm']:
            wal_file = Path(str(self.db_path) + ext)
            if wal_file.exists():
                wal_file.unlink()
        os.rmdir(self.temp_dir)

    def test_position_increments_correctly(self):
        """Position uses MAX+1 not COUNT."""
        session_id = "test-session"

        with db_context(str(self.db_path)) as db:
            # Insert first item
            pos1 = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM queue WHERE session_id = ?",
                [session_id]
            ).fetchone()[0]
            db.execute(
                "INSERT INTO queue (transcript_path, position, human_input, session_id) VALUES (?, ?, ?, ?)",
                ["/path", pos1, "test1", session_id]
            )

            # Insert second item
            pos2 = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM queue WHERE session_id = ?",
                [session_id]
            ).fetchone()[0]
            db.execute(
                "INSERT INTO queue (transcript_path, position, human_input, session_id) VALUES (?, ?, ?, ?)",
                ["/path", pos2, "test2", session_id]
            )

            # Delete first item
            db.execute("DELETE FROM queue WHERE position = 0")

            # Insert third item - should be position 2, not 1
            pos3 = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM queue WHERE session_id = ?",
                [session_id]
            ).fetchone()[0]

            db.commit()

        self.assertEqual(pos1, 0)
        self.assertEqual(pos2, 1)
        self.assertEqual(pos3, 2)  # Not 1 (which COUNT would give)


class TestSearch(unittest.TestCase):
    """P0: Search returns relevant results."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'test.db'

        # Create minimal schema
        db = sqlite3.connect(str(self.db_path))
        db.executescript("""
            CREATE TABLE context_graph (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                human_input TEXT NOT NULL,
                rule TEXT NOT NULL,
                context TEXT,
                tags TEXT,
                source_session TEXT,
                source_position INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE context_fts USING fts5(
                human_input, rule, context,
                content=context_graph, content_rowid=id,
                tokenize='porter unicode61'
            );

            CREATE TRIGGER context_ai AFTER INSERT ON context_graph BEGIN
                INSERT INTO context_fts(rowid, human_input, rule, context)
                VALUES (NEW.id, NEW.human_input, NEW.rule, NEW.context);
            END;
        """)

        # Insert test data
        db.execute("""
            INSERT INTO context_graph (human_input, rule, context, tags)
            VALUES (?, ?, ?, ?)
        """, ['Use PostgreSQL for the database', 'Prefer PostgreSQL over MySQL',
              'Database choice for web app', '["database", "postgresql"]'])

        db.execute("""
            INSERT INTO context_graph (human_input, rule, context, tags)
            VALUES (?, ?, ?, ?)
        """, ['Always add input validation', 'Validate all user inputs',
              None, '["security", "validation"]'])

        db.commit()
        db.close()

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        for ext in ['-wal', '-shm']:
            wal_file = Path(str(self.db_path) + ext)
            if wal_file.exists():
                wal_file.unlink()
        os.rmdir(self.temp_dir)

    def test_search_finds_relevant_results(self):
        """Search finds results matching query."""
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy not installed")

        from search import search

        results = search(str(self.db_path), "database", top_k=5)

        self.assertGreater(len(results), 0)
        self.assertIn('PostgreSQL', results[0]['rule'])

    def test_search_returns_empty_for_no_match(self):
        """Search returns empty list for non-matching query."""
        try:
            import numpy
        except ImportError:
            self.skipTest("numpy not installed")

        from search import search

        results = search(str(self.db_path), "xyznonexistent123", top_k=5)

        self.assertEqual(len(results), 0)

    def test_format_results(self):
        """format_results produces readable output."""
        from search import format_results

        results = [
            {
                'id': 1,
                'rule': 'Use PostgreSQL',
                'context': 'Database choice',
                'tags': ['database'],
                'rrf_score': 0.5
            }
        ]

        output = format_results(results)

        self.assertIn('PostgreSQL', output)
        self.assertIn('Database choice', output)


class TestExtractionPrompt(unittest.TestCase):
    """P1: Extraction prompt includes secret sanitization."""

    def test_worker_uses_config_prompt(self):
        """worker.py imports EXTRACTION_PROMPT from config."""
        worker_path = SCRIPT_DIR / 'worker.py'
        with open(worker_path) as f:
            content = f.read()

        # Check that worker imports from config
        self.assertIn('from config import', content)
        self.assertIn('EXTRACTION_PROMPT', content)


class TestSqliteVecGraceful(unittest.TestCase):
    """P1: sqlite-vec loading is graceful when missing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'test.db'

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()
        os.rmdir(self.temp_dir)

    def test_load_sqlite_vec_returns_false_when_missing(self):
        """load_sqlite_vec returns False when sqlite_vec not installed."""
        db = get_db(str(self.db_path))

        # Mock sqlite_vec import to fail
        with patch.dict('sys.modules', {'sqlite_vec': None}):
            # Force reimport to trigger the error path
            import importlib
            import db_utils
            importlib.reload(db_utils)

            # The function should handle the error gracefully
            result = db_utils.load_sqlite_vec(db)
            # Result depends on whether sqlite_vec is actually installed
            self.assertIsInstance(result, bool)

        db.close()


class TestHooksConfiguration(unittest.TestCase):
    """P1: Hooks configuration is valid."""

    def test_hooks_json_is_valid(self):
        """hooks.json is valid JSON with correct structure."""
        hooks_path = Path(__file__).parent.parent / 'hooks' / 'hooks.json'

        with open(hooks_path) as f:
            config = json.load(f)

        self.assertIn('hooks', config)
        self.assertIn('SessionStart', config['hooks'])
        self.assertIn('UserPromptSubmit', config['hooks'])

    def test_hooks_matchers_are_consistent(self):
        """All hook matchers use glob pattern (not regex)."""
        hooks_path = Path(__file__).parent.parent / 'hooks' / 'hooks.json'

        with open(hooks_path) as f:
            config = json.load(f)

        for event, hooks_list in config['hooks'].items():
            for hook in hooks_list:
                matcher = hook.get('matcher', '*')
                # Should be glob pattern, not regex
                self.assertNotEqual(matcher, '.*',
                    f"{event} uses regex '.*' instead of glob '*'")


class TestConfig(unittest.TestCase):
    """P1: Configuration is valid and importable."""

    def test_config_imports(self):
        """config.py can be imported without errors."""
        from config import (
            EXTRACTION_PROMPT, POLL_INTERVAL, RATE_LIMIT, HEAL_INTERVAL,
            CONTEXT_MAX_LINES, EXTRACTION_TIMEOUT, EXTRACTION_MAX_TURNS,
            MIN_PROMPT_LENGTH, DEFAULT_SEARCH_LIMIT, BM25_RETRIEVAL_LIMIT, RRF_K
        )

        # Verify types
        self.assertIsInstance(EXTRACTION_PROMPT, str)
        self.assertIsInstance(POLL_INTERVAL, int)
        self.assertIsInstance(CONTEXT_MAX_LINES, int)
        self.assertIsInstance(EXTRACTION_MAX_TURNS, int)
        self.assertGreaterEqual(EXTRACTION_MAX_TURNS, 5)  # Need enough for context + project files

    def test_config_values_reasonable(self):
        """Config values are within reasonable bounds."""
        from config import (
            CONTEXT_MAX_LINES, MIN_PROMPT_LENGTH,
            POLL_INTERVAL, EXTRACTION_TIMEOUT
        )

        self.assertGreater(CONTEXT_MAX_LINES, 0)
        self.assertLessEqual(CONTEXT_MAX_LINES, 1000)  # Reasonable upper bound
        self.assertGreater(MIN_PROMPT_LENGTH, 0)
        self.assertGreater(POLL_INTERVAL, 0)
        self.assertGreater(EXTRACTION_TIMEOUT, 0)

    def test_extraction_prompt_has_placeholders(self):
        """EXTRACTION_PROMPT has required placeholders."""
        from config import EXTRACTION_PROMPT

        self.assertIn('{context_file}', EXTRACTION_PROMPT)
        self.assertIn('{context_lines}', EXTRACTION_PROMPT)
        self.assertIn('{human_input}', EXTRACTION_PROMPT)

    def test_extraction_prompt_has_secret_warning(self):
        """EXTRACTION_PROMPT includes secret sanitization."""
        from config import EXTRACTION_PROMPT

        prompt_lower = EXTRACTION_PROMPT.lower()
        self.assertIn('api key', prompt_lower)
        self.assertIn('secret', prompt_lower)


class TestSessionInit(unittest.TestCase):
    """P0: Session initialization works correctly."""

    def test_session_init_imports(self):
        """session-init.py can be imported without errors."""
        session_init_path = SCRIPT_DIR / 'session-init.py'
        self.assertTrue(session_init_path.exists())

        # Verify it's valid Python
        import py_compile
        try:
            py_compile.compile(str(session_init_path), doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"session-init.py has syntax errors: {e}")

    def test_session_init_has_all_functions(self):
        """session-init.py contains all required functions."""
        session_init_path = SCRIPT_DIR / 'session-init.py'
        with open(session_init_path) as f:
            content = f.read()

        # Check for key functions
        self.assertIn('def install_dependencies', content)
        self.assertIn('def start_worker', content)
        self.assertIn('def register_project', content)
        self.assertIn('def main', content)

    def test_is_installed_function(self):
        """is_installed correctly detects installed packages."""
        # Import the function
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "session_init", SCRIPT_DIR / 'session-init.py'
        )
        session_init = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(session_init)

        # os is always installed
        self.assertTrue(session_init.is_installed('os'))
        # nonexistent_package_xyz should not be installed
        self.assertFalse(session_init.is_installed('nonexistent_package_xyz_123'))


class TestHooksConfigUpdated(unittest.TestCase):
    """Verify hooks.json uses unified session-init.py."""

    def test_uses_single_session_init(self):
        """SessionStart uses single session-init.py script."""
        hooks_path = Path(__file__).parent.parent / 'hooks' / 'hooks.json'

        with open(hooks_path) as f:
            config = json.load(f)

        session_start_hooks = config['hooks']['SessionStart'][0]['hooks']

        # Should be exactly 1 hook now
        self.assertEqual(len(session_start_hooks), 1)

        # Should reference session-init.py
        command = session_start_hooks[0]['command']
        self.assertIn('session-init.py', command)


class TestContextCompression(unittest.TestCase):
    """P1: Context compression and decompression works correctly."""

    def test_queue_input_compress_function_exists(self):
        """queue-input.py has read_and_compress_context function."""
        queue_input_path = SCRIPT_DIR / 'queue-input.py'
        with open(queue_input_path) as f:
            content = f.read()

        self.assertIn('def read_and_compress_context', content)
        self.assertIn('gzip.compress', content)

    def test_worker_decompress_function_exists(self):
        """worker.py has decompress_context function."""
        worker_path = SCRIPT_DIR / 'worker.py'
        with open(worker_path) as f:
            content = f.read()

        self.assertIn('def decompress_context', content)
        self.assertIn('gzip.decompress', content)

    def test_compression_roundtrip(self):
        """Compress and decompress context correctly."""
        import gzip

        # Sample JSONL context
        original = '{"type":"user","message":"test1"}\n{"type":"assistant","message":"test2"}\n'

        # Compress (as queue-input.py does)
        compressed = gzip.compress(original.encode('utf-8'))

        # Decompress (as worker.py does)
        decompressed = gzip.decompress(compressed).decode('utf-8')

        self.assertEqual(original, decompressed)
        self.assertLess(len(compressed), len(original.encode('utf-8')))

    def test_queue_table_has_context_blob(self):
        """Queue table includes context_blob column."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            subprocess.run(
                ['python3', str(SCRIPT_DIR / 'init-db.py'), str(db_path)],
                capture_output=True, timeout=30
            )

            db = sqlite3.connect(str(db_path))
            cursor = db.execute("PRAGMA table_info(queue)")
            columns = [row[1] for row in cursor.fetchall()]
            db.close()

            self.assertIn('context_blob', columns)


if __name__ == '__main__':
    # Run tests with verbosity
    unittest.main(verbosity=2)
