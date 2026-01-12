#!/usr/bin/env python3
"""MCP server for Superwiser - exposes search and context tools to Claude Code."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from mcp.server.fastmcp import FastMCP
from db_utils import db_context
from paths import SUPERWISER_DIR, VENV_PYTHON, LOCKS_DIR, MARKERS_DIR, SEARCH_MARKER

mcp = FastMCP("superwiser")

# Pinned versions for reproducibility (must match session-init.py)
SEARCH_DEPS = ["sentence-transformers==3.4.1", "sqlite-vec==0.1.6"]


def ensure_search_deps() -> tuple[bool, str]:
    """Lazily install search deps with locking using uv. Returns (success, error_msg)."""
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)

    lock_file = LOCKS_DIR / "search_deps.lock"

    # Fast path: already installed
    if SEARCH_MARKER.exists():
        return True, ""

    # Acquire lock to serialize installs
    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)

        # Double-check after acquiring lock
        if SEARCH_MARKER.exists():
            return True, ""

        try:
            # Set cache location to avoid tmp issues
            env = {**os.environ, "HF_HOME": str(SUPERWISER_DIR / "cache")}

            # Use uv for faster, consistent installs
            result = subprocess.run(
                ["uv", "pip", "install", "-p", str(VENV_PYTHON), "-q"] + SEARCH_DEPS,
                capture_output=True, text=True, timeout=120, env=env
            )

            if result.returncode != 0:
                return False, f"uv pip install failed: {result.stderr[:200]}"

            # Write success marker
            SEARCH_MARKER.write_text("ok")
            return True, ""

        except subprocess.TimeoutExpired:
            return False, "Install timed out (120s). Please retry."
        except Exception as e:
            return False, str(e)


def get_db_path() -> str:
    """Get database path from env var or current working directory."""
    return os.environ.get('SUPERWISER_DB_PATH') or str(Path.cwd() / '.claude' / 'superwiser' / 'context.db')


@mcp.tool()
def search_rules(query: str, tags: str = "", context: str = "", limit: int = 5) -> str:
    """Search user's recorded rules and coding decisions.

    WHEN TO USE:
    - Before important decisions (architecture, libraries, patterns, tech choices)
    - When starting a new task to understand relevant preferences
    - When unsure about coding style, conventions, or approaches
    - Periodically during longer tasks to stay aligned with user preferences

    This helps you understand how the user likes things done - their coding style,
    preferred libraries, architectural patterns, and past decisions.

    CONFLICT HANDLING:
    If results show "⚠️ CONFLICT [id]", you must ask user which rule to follow before proceeding.
    Format your question as: "SuperWiser (Conflict) [id]: <describe the conflicting rules and ask which to follow>"
    After the user responds to resolve the conflict, you do not need to call any tools to resolve or delete rules. This is handled automatically in the background.

    Args:
        query: What to search for (e.g., 'error handling', 'testing', 'database')
        tags: Optional comma-separated tags to filter by (e.g., 'javascript,react')
        context: HIGHLY RECOMMENDED - describe what you're deciding for better results
                 (e.g., 'Setting up user authentication with JWT')
        limit: Maximum number of results to return (default: 5)

    Examples:
        search_rules("database", context="Choosing database for user data")
        search_rules("testing", tags="python")
        search_rules("error handling", context="Setting up API error responses")
    """
    # Lazy install search dependencies (sentence-transformers, sqlite-vec)
    ok, err = ensure_search_deps()
    if not ok:
        return json.dumps({"error": f"Search deps not ready: {err}. Try again in a moment."})

    # Now safe to import search module (needs sentence-transformers)
    from search import search, format_results

    db_path = get_db_path()

    if not Path(db_path).exists():
        return "No rules recorded yet. The user needs to use Claude Code for a while first."

    try:
        # Build search query combining query, tags, and context
        search_query = query
        if tags:
            # Add tag filters to search
            tag_list = [t.strip() for t in tags.split(',') if t.strip()]
            if tag_list:
                search_query = f"{query} {' '.join(f'#{t}' for t in tag_list)}"
        if context:
            # Append context for semantic matching
            search_query = f"{search_query} {context}"

        results = search(db_path, search_query, limit)
        if not results:
            return f"No rules found matching '{query}'." + (f" (tags: {tags})" if tags else "")
        return format_results(results)
    except Exception as e:
        return f"Error searching rules: {e}"


@mcp.tool()
def get_rule(id: str) -> str:
    """Get a specific rule by its ID.

    Args:
        id: The 6-character rule ID (e.g., 'x7k9m2')

    Returns:
        Rule details including context and tags, or error message
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return f"Rule {id} not found"

    try:
        with db_context(db_path, timeout=5.0) as db:
            ctx = db.execute("SELECT id, tags FROM context_graph WHERE id = ?", [id]).fetchone()
            if not ctx:
                return f"Rule {id} not found"

            rules = db.execute("""
                SELECT rule, context, confidence FROM rules
                WHERE context_id = ? ORDER BY created_at
            """, [id]).fetchall()

            return json.dumps({
                "id": ctx[0],
                "tags": json.loads(ctx[1]) if ctx[1] else [],
                "rules": [{"index": i, "rule": r[0], "context": r[1], "confidence": r[2]}
                          for i, r in enumerate(rules, 1)],
                "conflict": len(rules) > 1
            }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def initialize() -> str:
    """Initialize Superwiser for the current project.

    Use this after a mid-session plugin install or to manually trigger initialization.
    This will install dependencies, start the worker, and register the project.

    Returns:
        Status message
    """
    cwd = os.getcwd()

    try:
        input_data = json.dumps({"cwd": cwd})
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'session-init.py')],
            input=input_data,
            capture_output=True,
            text=True,
            timeout=300
        )

        # Check for errors
        if result.returncode != 0 and result.stderr:
            return f"Initialization failed: {result.stderr[:200]}"

        if result.stdout:
            try:
                response = json.loads(result.stdout)
                return response.get('systemMessage', 'Superwiser initialized successfully.')
            except json.JSONDecodeError:
                return result.stdout

        return "Superwiser initialized successfully."
    except subprocess.TimeoutExpired:
        return "Initialization timed out. Dependencies may still be installing in the background."
    except Exception as e:
        return f"Error initializing: {e}"


@mcp.tool()
def list_rules(limit: int = 10, sort_by: str = "recent") -> str:
    """List captured rules with total count.

    WHEN TO USE:
    - At the start of a session to understand user's key preferences
    - Use sort_by="important" or "hits" to see most relevant rules
    - Complements search_rules which is for specific decisions

    SORT OPTIONS:
    - "recent": Newest rules first (default)
    - "hits": Most searched rules first (raw search hit count)
    - "important": Composite score combining:
        * Search hits (40%): How often this rule appears in searches
        * Duplicate validation (30%): Prompts skipped as duplicates of this rule
        * Confidence (10%): strong > normal > weak > tentative
        * Conflict survival (10%): User explicitly chose this rule over alternatives
        * Recency (10%): Recently used rules score higher

    CONFLICT HANDLING:
    If results show "CONFLICT [id]", you must ask user which rule to follow before proceeding.
    Format your question as: "SuperWiser (Conflict) [id]: <describe the conflicting rules and ask which to follow>"
    After the user responds, conflict resolution is handled automatically in the background.

    Args:
        limit: Maximum number of rules to show (default: 10)
        sort_by: Sort order - "recent" (default), "important", or "hits"

    Returns:
        JSON with rules, total count, and importance info
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return json.dumps({"rules": [], "total": 0, "showing": 0})

    try:
        with db_context(db_path, timeout=5.0) as db:
            # Get total count
            total = db.execute("SELECT COUNT(*) FROM rules").fetchone()[0]

            # Determine sort order
            order_clause = {
                "recent": "r.created_at DESC",
                "important": "COALESCE(r.importance_score, 0) DESC",
                "hits": "COALESCE(r.search_hit_count, 0) DESC"
            }.get(sort_by, "r.created_at DESC")

            # Get rules with importance info
            rules = db.execute(f"""
                SELECT r.context_id, r.rule, r.context, r.confidence, date(r.created_at) as date,
                       COALESCE(r.importance_score, 0) as importance_score,
                       COALESCE(r.search_hit_count, 0) as search_hits
                FROM rules r ORDER BY {order_clause} LIMIT ?
            """, [limit]).fetchall()

            return json.dumps({
                "rules": [
                    {
                        "id": r[0], "rule": r[1], "context": r[2], "confidence": r[3],
                        "date": r[4], "importance_score": round(r[5], 1), "search_hits": r[6]
                    }
                    for r in rules
                ],
                "total": total,
                "showing": len(rules),
                "sorted_by": sort_by
            }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_stats() -> str:
    """Get statistics about captured rules and their importance.

    Returns rule counts, search statistics, and the most important rules
    based on search frequency, duplicate validation, and conflict survival.

    Returns:
        JSON with rule statistics including most important rules
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return json.dumps({"error": "No rules recorded yet"})

    try:
        with db_context(db_path, timeout=5.0) as db:
            # Total counts
            total_rules = db.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
            total_contexts = db.execute("SELECT COUNT(*) FROM context_graph").fetchone()[0]

            # Pending conflicts
            pending_conflicts = db.execute(
                "SELECT COUNT(*) FROM pending_conflicts WHERE shown = FALSE"
            ).fetchone()[0]

            # Usage stats
            try:
                total_search_hits = db.execute(
                    "SELECT SUM(COALESCE(search_hit_count, 0)) FROM rules"
                ).fetchone()[0] or 0
            except Exception:
                total_search_hits = 0

            # Duplicate skip count
            try:
                total_duplicate_skips = db.execute(
                    "SELECT SUM(COALESCE(duplicate_skip_count, 0)) FROM rules"
                ).fetchone()[0] or 0
            except Exception:
                total_duplicate_skips = 0

            # Conflict survivors
            try:
                conflict_survivors = db.execute(
                    "SELECT COUNT(*) FROM rules WHERE survived_conflict = TRUE"
                ).fetchone()[0]
            except Exception:
                conflict_survivors = 0

            # Top 5 most important rules
            try:
                top_rules = db.execute("""
                    SELECT r.context_id, r.rule, COALESCE(r.importance_score, 0) as score,
                           COALESCE(r.search_hit_count, 0) as hits,
                           COALESCE(r.duplicate_skip_count, 0) as dups
                    FROM rules r
                    ORDER BY score DESC
                    LIMIT 5
                """).fetchall()
            except Exception:
                top_rules = []

            # Pending work (prompts being analyzed for rules)
            try:
                pending_analysis = db.execute(
                    "SELECT COUNT(*) FROM queue WHERE status = 'pending'"
                ).fetchone()[0]
                processing_now = db.execute(
                    "SELECT COUNT(*) FROM queue WHERE status = 'processing'"
                ).fetchone()[0]
                failed_analysis = db.execute(
                    "SELECT COUNT(*) FROM queue WHERE status = 'failed'"
                ).fetchone()[0]
            except Exception:
                pending_analysis = processing_now = failed_analysis = 0

            return json.dumps({
                "total_rules": total_rules,
                "total_contexts": total_contexts,
                "pending_conflicts": pending_conflicts,
                "total_search_hits": total_search_hits,
                "total_duplicate_skips": total_duplicate_skips,
                "conflict_survivors": conflict_survivors,
                "pending_analysis": pending_analysis,
                "processing_now": processing_now,
                "failed_analysis": failed_analysis,
                "most_important_rules": [
                    {
                        "id": r[0],
                        "rule": r[1][:100] + "..." if len(r[1]) > 100 else r[1],
                        "importance_score": round(r[2], 1),
                        "search_hits": r[3],
                        "duplicate_skips": r[4]
                    }
                    for r in top_rules
                ]
            }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_tags() -> str:
    """List all tags and their counts from captured preferences.

    Returns:
        JSON list of tags with their counts, sorted by count descending
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return "No preferences recorded yet."

    try:
        with db_context(db_path, timeout=5.0) as db:
            rows = db.execute("SELECT tags FROM context_graph WHERE tags IS NOT NULL").fetchall()

            tag_counts: dict[str, int] = {}
            for (tags_json,) in rows:
                try:
                    tags = json.loads(tags_json)
                    for tag in tags:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass

            if not tag_counts:
                return "No tags found."

            sorted_tags = sorted(tag_counts.items(), key=lambda x: -x[1])
            return json.dumps([{"tag": t, "count": c} for t, c in sorted_tags], indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def delete_rule(rule_id: str) -> str:
    """Delete a rule by its ID.

    Args:
        rule_id: The 6-character rule ID (e.g., 'x7k9m2')

    Returns:
        Confirmation or error message
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return f"Rule {rule_id} not found"

    try:
        with db_context(db_path, timeout=5.0) as db:
            # Check if rule exists
            exists = db.execute(
                "SELECT id FROM context_graph WHERE id = ?", [rule_id]
            ).fetchone()

            if not exists:
                return f"Rule {rule_id} not found"

            # Delete from all tables
            db.execute("DELETE FROM rules WHERE context_id = ?", [rule_id])
            db.execute("DELETE FROM context_graph WHERE id = ?", [rule_id])
            db.execute("DELETE FROM pending_conflicts WHERE context_id = ?", [rule_id])
            db.commit()

            return f"Rule {rule_id} has been deleted."
    except Exception as e:
        return f"Error deleting rule: {e}"


@mcp.tool()
def enable_recording() -> str:
    """Enable Superwiser preference recording for the current project.

    Call this when the user wants to resume recording their coding preferences.
    This will start capturing new preferences from user messages.

    Returns:
        Confirmation message
    """
    from ensure_init import set_recording_state, run_init

    cwd = os.getcwd()
    set_recording_state(cwd, enabled=True)

    # Ensure worker is running
    run_init(cwd)

    return "Recording enabled. New preferences will be captured from your messages."


@mcp.tool()
def disable_recording() -> str:
    """Disable Superwiser preference recording for the current project.

    Call this when the user wants to pause recording their coding preferences.
    The setting persists across sessions.

    Returns:
        Confirmation message
    """
    from ensure_init import set_recording_state

    cwd = os.getcwd()
    set_recording_state(cwd, enabled=False)

    return "Recording disabled. This setting persists across sessions. Use enable_recording to resume."


@mcp.tool()
def seed_preview() -> str:
    """Preview available transcripts before seeding.

    IMPORTANT: Call this FIRST before seed_from_history to show the user
    what transcripts are available and let them choose which to process.

    Returns transcript count, date range, and options for filtering.

    Returns:
        JSON with transcript statistics and filtering options
    """
    from seed import get_transcript_stats

    cwd = os.getcwd()
    stats = get_transcript_stats(cwd)

    if stats['count'] == 0:
        return json.dumps({
            'count': 0,
            'message': 'No transcripts found for this project'
        })

    return json.dumps({
        'count': stats['count'],
        'oldest_date': stats['oldest_date'],
        'newest_date': stats['newest_date']
    }, indent=2)


@mcp.tool()
def seed_from_history(latest_n: int = None, after_date: str = None) -> str:
    """Seed rules from historical conversation transcripts.

    IMPORTANT: Call seed_preview FIRST to show the user options, then call
    this with their chosen filter. Only call when explicitly requested.

    Args:
        latest_n: Only process the N most recent transcripts
        after_date: Only process transcripts after this date (YYYY-MM-DD format)
                   Note: latest_n and after_date are mutually exclusive

    Uses override mode where newer conflicting rules automatically replace older ones.
    The worker processes queued items in the background.

    Returns:
        Status message with count of queued prompts
    """
    from seed import seed_project

    cwd = os.getcwd()
    db_path = str(Path(cwd) / '.claude' / 'superwiser' / 'context.db')

    if not Path(db_path).exists():
        return "Superwiser not initialized. Run /superwiser:init first."

    result = seed_project(cwd, db_path, latest_n=latest_n, after_date=after_date)

    if 'error' in result:
        return f"Seeding failed: {result['error']}"

    return result.get('message', f"Queued {result['queued']} prompts from {result['transcripts']} transcripts")


if __name__ == "__main__":
    mcp.run()
