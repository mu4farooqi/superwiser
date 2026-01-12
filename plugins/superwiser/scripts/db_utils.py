#!/usr/bin/env python3
"""
Database and hook utilities with proper connection handling.
"""

import json
import secrets
import sqlite3
import string
import threading
from contextlib import contextmanager


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


def log_token_usage(
    db_path: str,
    operation: str,
    parsed_response: dict,
    project_path: str = None,
    model: str = None
) -> None:
    """Log token usage from Claude JSON response to the database.
    
    Args:
        db_path: Path to the SQLite database
        operation: 'discovery' or 'extraction'
        parsed_response: The parsed JSON response from claude -p
        project_path: Optional project path for context
        model: Optional model name override
    """
    try:
        # Extract usage data from response
        usage = parsed_response.get('usage', {})
        model_usage = parsed_response.get('modelUsage', {})
        
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        cache_creation = usage.get('cache_creation_input_tokens', 0)
        cache_read = usage.get('cache_read_input_tokens', 0)
        total_cost = parsed_response.get('total_cost_usd', 0)
        duration = parsed_response.get('duration_ms', 0)
        
        # If no usage block at all, aggregate from modelUsage
        if not usage and model_usage:
            for model_data in model_usage.values():
                input_tokens += model_data.get('inputTokens', 0)
                output_tokens += model_data.get('outputTokens', 0)
                cache_creation += model_data.get('cacheCreationInputTokens', 0)
                cache_read += model_data.get('cacheReadInputTokens', 0)
        
        # Determine model from modelUsage if not provided
        if not model and model_usage:
            model = list(model_usage.keys())[0]
        
        with db_context(db_path, timeout=5.0) as db:
            # Ensure table and indexes exist (for databases created before this feature)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    project_path TEXT,
                    model TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_creation_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    total_cost_usd REAL DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_token_usage_date ON token_usage(created_at);
            """)
            db.execute("""
                INSERT INTO token_usage (
                    operation, project_path, model, input_tokens, output_tokens,
                    cache_creation_tokens, cache_read_tokens, total_cost_usd, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                operation, project_path, model,
                input_tokens, output_tokens, cache_creation, cache_read,
                total_cost, duration
            ])
            db.commit()
    except Exception:
        # Don't let logging failures affect the main workflow
        pass


def get_token_usage_stats(db_path: str) -> dict:
    """Get token usage statistics for the last 7 and 30 days.
    
    Returns:
        Dictionary with daily averages, weekly totals, and breakdowns
    """
    try:
        with db_context(db_path, timeout=5.0) as db:
            # Check if table exists
            table_exists = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
            ).fetchone()
            
            if not table_exists:
                return {"error": "Token usage tracking not yet initialized"}
            
            # Today's stats
            today_stats = db.execute("""
                SELECT 
                    SUM(input_tokens) as input,
                    SUM(output_tokens) as output,
                    SUM(cache_creation_tokens) as cache_create,
                    SUM(cache_read_tokens) as cache_read,
                    COUNT(*) as calls
                FROM token_usage 
                WHERE date(created_at) = date('now')
            """).fetchone()
            
            # Last 7 days stats
            week_stats = db.execute("""
                SELECT 
                    SUM(input_tokens) as input,
                    SUM(output_tokens) as output,
                    SUM(cache_creation_tokens) as cache_create,
                    COUNT(*) as calls,
                    COUNT(DISTINCT date(created_at)) as days
                FROM token_usage 
                WHERE created_at >= datetime('now', '-7 days')
            """).fetchone()
            
            # By operation type (last 7 days)
            by_operation = db.execute("""
                SELECT 
                    operation,
                    SUM(input_tokens + output_tokens + cache_creation_tokens) as total_tokens,
                    COUNT(*) as calls
                FROM token_usage 
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY operation
            """).fetchall()
            
            # Helper to safely convert None to int
            def si(val): return int(val) if val else 0
            
            # Pre-compute totals
            today_total = si(today_stats[0]) + si(today_stats[1]) + si(today_stats[2])
            week_total = si(week_stats[0]) + si(week_stats[1]) + si(week_stats[2])
            week_days = si(week_stats[4]) or 1
            
            return {
                "today": {
                    "input_tokens": si(today_stats[0]),
                    "output_tokens": si(today_stats[1]),
                    "total_tokens": today_total,
                    "api_calls": si(today_stats[4])
                },
                "last_7_days": {
                    "total_tokens": week_total,
                    "api_calls": si(week_stats[3]),
                    "daily_avg_tokens": round(week_total / week_days)
                },
                "by_operation": {
                    row[0]: {
                        "total_tokens": si(row[1]),
                        "calls": si(row[2])
                    }
                    for row in by_operation
                }
            }
    except Exception as e:
        return {"error": str(e)}
