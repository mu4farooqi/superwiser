#!/usr/bin/env python3
"""Initialize context.db with v2 schema (context_graph + rules tables)."""
import sqlite3
import sys
from pathlib import Path


def init_db(db_path: str):
    """Initialize database with v2 schema. Vector table created later when sqlite-vec available."""
    db = sqlite3.connect(db_path)

    db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=30000;
        PRAGMA synchronous=NORMAL;
        PRAGMA foreign_keys=ON;

        -- Queue for pending extractions
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transcript_path TEXT NOT NULL,
            position INTEGER NOT NULL,
            human_input TEXT NOT NULL,
            context_blob BLOB,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            reason TEXT,
            override_mode BOOLEAN DEFAULT FALSE
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_queue_session ON queue(session_id, position);

        -- Groups related/conflicting rules by semantic similarity
        -- id is 6-char alphanumeric like 'x7k9m2'
        CREATE TABLE IF NOT EXISTS context_graph (
            id TEXT PRIMARY KEY,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Individual rules, each with own context/backstory
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_id TEXT REFERENCES context_graph(id) ON DELETE CASCADE,
            rule TEXT NOT NULL,
            context TEXT,
            human_input TEXT,
            confidence TEXT DEFAULT 'normal',
            source_session TEXT,
            source_position INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- Importance tracking columns
            search_hit_count INTEGER DEFAULT 0,
            duplicate_skip_count INTEGER DEFAULT 0,
            importance_score REAL DEFAULT 0.0,
            survived_conflict BOOLEAN DEFAULT FALSE,
            last_hit_at TIMESTAMP  -- Used for recency in importance scoring
        );

        CREATE INDEX IF NOT EXISTS idx_rules_context ON rules(context_id);
        CREATE INDEX IF NOT EXISTS idx_rules_importance ON rules(importance_score DESC);

        -- Pending conflicts to show user (one per context)
        CREATE TABLE IF NOT EXISTS pending_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_id TEXT UNIQUE REFERENCES context_graph(id) ON DELETE CASCADE,
            shown BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_pending_shown ON pending_conflicts(shown, created_at);

        -- FTS for rules (primary search target)
        CREATE VIRTUAL TABLE IF NOT EXISTS rules_fts USING fts5(
            rule, context, human_input,
            content=rules, content_rowid=id,
            tokenize='porter unicode61'
        );

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS rules_ai AFTER INSERT ON rules BEGIN
            INSERT INTO rules_fts(rowid, rule, context, human_input)
            VALUES (NEW.id, NEW.rule, NEW.context, NEW.human_input);
        END;

        CREATE TRIGGER IF NOT EXISTS rules_ad AFTER DELETE ON rules BEGIN
            INSERT INTO rules_fts(rules_fts, rowid, rule, context, human_input)
            VALUES ('delete', OLD.id, OLD.rule, OLD.context, OLD.human_input);
        END;

        CREATE TRIGGER IF NOT EXISTS rules_au AFTER UPDATE ON rules BEGIN
            INSERT INTO rules_fts(rules_fts, rowid, rule, context, human_input)
            VALUES ('delete', OLD.id, OLD.rule, OLD.context, OLD.human_input);
            INSERT INTO rules_fts(rowid, rule, context, human_input)
            VALUES (NEW.id, NEW.rule, NEW.context, NEW.human_input);
        END;
    """)

    # Try to create vector table if sqlite-vec is available
    # If not, worker.py will create it later when dependencies are installed
    try:
        db.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(db)
        db.enable_load_extension(False)

        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS rules_vec USING vec0(
                id INTEGER PRIMARY KEY,
                embedding FLOAT[384]
            )
        """)
    except (ImportError, Exception):
        pass  # Vector table will be created by worker when deps available

    db.commit()
    db.close()
    print(f"Initialized {db_path}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: init-db.py <db_path>")
        sys.exit(1)

    db_path = sys.argv[1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
