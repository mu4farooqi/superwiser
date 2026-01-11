#!/usr/bin/env python3
"""
Database utilities with proper connection handling.
"""

import secrets
import sqlite3
import string
import threading
from contextlib import contextmanager


def generate_id() -> str:
    """Generate 6-char alphanumeric ID like 'x7k9m2'."""
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(6))


def get_db(db_path: str, timeout: float = 30.0) -> sqlite3.Connection:
    """Create a SQLite connection with WAL mode for concurrent access."""
    db = sqlite3.connect(db_path, timeout=timeout)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


@contextmanager
def db_context(db_path: str, timeout: float = 30.0):
    """Context manager for database access with automatic cleanup."""
    db = get_db(db_path, timeout)
    try:
        yield db
    finally:
        db.close()


def load_sqlite_vec(db: sqlite3.Connection, ensure_table: bool = False) -> bool:
    """Load sqlite-vec extension and optionally ensure vector table exists.

    Args:
        db: SQLite connection
        ensure_table: If True, create rules_vec table if it doesn't exist

    Returns:
        True if sqlite-vec loaded successfully
    """
    try:
        db.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(db)
        db.enable_load_extension(False)

        if ensure_table:
            db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS rules_vec USING vec0(
                    id INTEGER PRIMARY KEY,
                    embedding FLOAT[384]
                )
            """)
        return True
    except (ImportError, Exception):
        return False


# Lazy-loaded sentence transformer model with thread safety
_MODEL = None
_MODEL_LOCK = threading.Lock()


def get_model():
    """Get sentence transformer model (lazy loaded, thread-safe)."""
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:  # Double-check after acquiring lock
                from sentence_transformers import SentenceTransformer
                _MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    return _MODEL


def generate_embedding(text: str):
    """Generate normalized embedding for text."""
    return get_model().encode(text, normalize_embeddings=True)
