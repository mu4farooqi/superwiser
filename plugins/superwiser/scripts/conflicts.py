#!/usr/bin/env python3
"""Shared conflict detection utilities."""

from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
import sys
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import db_context


def format_context_preview(context: str) -> str:
    """Format context for display in conflict message."""
    if not context:
        return ""
    if len(context) > 50:
        return f" ({context[:50]}...)"
    return f" ({context})"


def check_pending_conflicts(db_path: str) -> str | None:
    """Check for pending conflicts to show user. Returns formatted message or None."""
    if not Path(db_path).exists():
        return None

    try:
        with db_context(db_path, timeout=2.0) as db:
            pending = db.execute("""
                SELECT context_id FROM pending_conflicts
                WHERE shown = FALSE ORDER BY created_at LIMIT 1
            """).fetchone()

            if not pending:
                return None

            context_id = pending[0]

            rules = db.execute("""
                SELECT rule, context FROM rules
                WHERE context_id = ? ORDER BY created_at
            """, [context_id]).fetchall()

            if len(rules) < 2:
                db.execute("DELETE FROM pending_conflicts WHERE context_id = ?", [context_id])
                db.commit()
                return None

            db.execute("UPDATE pending_conflicts SET shown = TRUE WHERE context_id = ?", [context_id])
            db.commit()

            lines = [f"\nSuperWiser (Conflict) [{context_id}]:\n"]
            for i, (rule, context) in enumerate(rules, 1):
                preview = format_context_preview(context)
                lines.append(f"[{i}]{preview}: \"{rule}\"")
            lines.append("\nWhich should I follow? (e.g., '1', '2', 'both', or explain when each applies)\n")
            return "\n".join(lines)
    except Exception:
        return None
