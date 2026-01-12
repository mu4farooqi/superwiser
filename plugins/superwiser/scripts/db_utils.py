"""
Database and hook utilities with proper connection handling.
"""

import json
import secrets
import sqlite3
import string
import threading
from contextlib import contextmanager


def hook_output(msg: str | None = None, additional_context: str | None = None) -> None:
    """Output JSON response for Claude Code hooks.

    Args:
        msg: System message (shown in UI, may not reach Claude directly)
        additional_context: Context to inject into Claude's conversation (UserPromptSubmit only)
    """
    # DEBUG: Always block to see what's happening
    debug_info = f"additional_context={'YES: ' + additional_context[:500] if additional_context else 'None'}"
    print(json.dumps({
        "continue": False,
        "systemMessage": f"DEBUG: {debug_info}"
    }))


def generate_id() -> str:
    """Generate 6-char alphanumeric ID like 'x7k9m2'."""
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(6))


def insert_context_with_retry(db: sqlite3.Connection, tags_json: str, max_retries: int = 5) -> str:
    """Insert into context_graph with collision retry.
    
    Generates a unique ID and inserts. If collision occurs (unlikely),
    retries with a new ID up to max_retries times.
    
    Args:
        db: SQLite connection
        tags_json: JSON string of tags
        max_retries: Maximum retry attempts on collision
        
    Returns:
        The generated context_id
        
    Raises:
        sqlite3.IntegrityError: If all retries fail (extremely unlikely)
    """
    for attempt in range(max_retries):
        context_id = generate_id()
        try:
            db.execute(
                "INSERT INTO context_graph (id, tags) VALUES (?, ?)",
                [context_id, tags_json]
            )
            return context_id
        except sqlite3.IntegrityError:
            if attempt == max_retries - 1:
                raise  # All retries exhausted
            continue  # Try again with new ID
    
    # Should never reach here, but just in case
    raise sqlite3.IntegrityError(f"Failed to generate unique ID after {max_retries} attempts")


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


def load_sqlite_vec(db: sqlite3.Connection, ensure_table: bool = False) -> None:
    """Load sqlite-vec extension and optionally ensure vector table exists.

    Args:
        db: SQLite connection
        ensure_table: If True, create rules_vec table if it doesn't exist

    Raises:
        ImportError: If sqlite-vec is not installed
        RuntimeError: If sqlite-vec fails to load
    """
    try:
        import sqlite_vec
    except ImportError:
        raise ImportError(
            "sqlite-vec is required but not installed. "
            "Make sure you're using the superwiser venv: ~/.superwiser/venv/bin/python"
        )

    try:
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
    except Exception as e:
        raise RuntimeError(f"Failed to load sqlite-vec extension: {e}")

    if ensure_table:
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS rules_vec USING vec0(
                id INTEGER PRIMARY KEY,
                embedding FLOAT[384]
            )
        """)


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
