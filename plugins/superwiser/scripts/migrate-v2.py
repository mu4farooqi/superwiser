#!/usr/bin/env python3
"""Migrate from v1 schema to v2 schema.

V1 schema: Single context_graph table with id, human_input, rule, context, tags
V2 schema: Split into context_graph (grouping) + rules (individual rules)

Usage: python3 migrate-v2.py /path/to/context.db
"""
import sqlite3
import json
import secrets
import string
import sys
from pathlib import Path


def generate_id():
    """Generate 6-char alphanumeric ID like 'x7k9m2'"""
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(6))


def is_v1_schema(db):
    """Check if database has v1 schema (context_graph with 'rule' column)."""
    try:
        # V1 has 'rule' column directly in context_graph
        db.execute("SELECT rule FROM context_graph LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def is_v2_schema(db):
    """Check if database has v2 schema (separate rules table)."""
    try:
        db.execute("SELECT id FROM rules LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def migrate(db_path: str):
    """Migrate v1 schema to v2 schema."""
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys=OFF")  # Disable during migration

    # Check current schema
    if is_v2_schema(db):
        print("Already on v2 schema, nothing to migrate")
        db.close()
        return

    if not is_v1_schema(db):
        print("Unknown schema, cannot migrate")
        db.close()
        sys.exit(1)

    print("Migrating from v1 to v2 schema...")

    # Read all existing data
    old_rows = db.execute("""
        SELECT human_input, rule, context, tags, source_session, source_position, created_at
        FROM context_graph
    """).fetchall()
    print(f"Found {len(old_rows)} rules to migrate")

    # Create new tables
    db.executescript("""
        -- New context_graph with TEXT id
        CREATE TABLE context_graph_new (
            id TEXT PRIMARY KEY,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- New rules table
        CREATE TABLE rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_id TEXT REFERENCES context_graph_new(id) ON DELETE CASCADE,
            rule TEXT NOT NULL,
            context TEXT,
            human_input TEXT,
            confidence TEXT DEFAULT 'normal',
            source_session TEXT,
            source_position INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX idx_rules_context ON rules(context_id);

        -- Pending conflicts table (one per context)
        CREATE TABLE pending_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_id TEXT UNIQUE,
            shown BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX idx_pending_shown ON pending_conflicts(shown, created_at);
    """)

    # Migrate data
    for row in old_rows:
        human_input, rule, context, tags, source_session, source_position, created_at = row
        context_id = generate_id()

        db.execute(
            "INSERT INTO context_graph_new (id, tags, created_at) VALUES (?, ?, ?)",
            (context_id, tags, created_at)
        )
        db.execute("""
            INSERT INTO rules (context_id, rule, context, human_input, source_session, source_position, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (context_id, rule, context, human_input, source_session, source_position, created_at))

    # Drop old tables
    db.execute("DROP TABLE IF EXISTS context_graph")
    db.execute("DROP TABLE IF EXISTS context_fts")
    db.execute("DROP TABLE IF EXISTS context_vec")

    # Rename new table
    db.execute("ALTER TABLE context_graph_new RENAME TO context_graph")

    # Create new FTS
    db.executescript("""
        CREATE VIRTUAL TABLE rules_fts USING fts5(
            rule, context, human_input,
            content=rules, content_rowid=id,
            tokenize='porter unicode61'
        );

        -- Populate FTS from existing rules
        INSERT INTO rules_fts(rowid, rule, context, human_input)
        SELECT id, rule, context, human_input FROM rules;

        -- Triggers for FTS sync
        CREATE TRIGGER rules_ai AFTER INSERT ON rules BEGIN
            INSERT INTO rules_fts(rowid, rule, context, human_input)
            VALUES (NEW.id, NEW.rule, NEW.context, NEW.human_input);
        END;

        CREATE TRIGGER rules_ad AFTER DELETE ON rules BEGIN
            INSERT INTO rules_fts(rules_fts, rowid, rule, context, human_input)
            VALUES ('delete', OLD.id, OLD.rule, OLD.context, OLD.human_input);
        END;

        CREATE TRIGGER rules_au AFTER UPDATE ON rules BEGIN
            INSERT INTO rules_fts(rules_fts, rowid, rule, context, human_input)
            VALUES ('delete', OLD.id, OLD.rule, OLD.context, OLD.human_input);
            INSERT INTO rules_fts(rowid, rule, context, human_input)
            VALUES (NEW.id, NEW.rule, NEW.context, NEW.human_input);
        END;
    """)

    # Try to create vector table if sqlite-vec available
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
        print("Created rules_vec table")
    except (ImportError, Exception):
        print("sqlite-vec not available, skipping vector table")

    db.execute("PRAGMA foreign_keys=ON")
    db.commit()
    db.close()

    print(f"Migration complete: {len(old_rows)} rules migrated to v2 schema")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: migrate-v2.py <db_path>")
        print("Example: migrate-v2.py .claude/superwiser/context.db")
        sys.exit(1)

    migrate(sys.argv[1])
