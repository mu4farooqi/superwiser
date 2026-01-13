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
from db_utils import db_context, get_token_usage_stats
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
def search_rules(context: str, limit: int = 5, preferences_only: bool = False) -> str:
    """Search user's recorded rules and coding decisions.

    WHEN TO USE:
    - Before important decisions (architecture, libraries, patterns, tech choices)
    - When unsure about coding style, conventions, or approaches
    - When you need more specific preferences beyond what was auto-loaded

    Note: Relevant preferences are automatically loaded on the first prompt of each session.
    Use this tool for deeper searches or when making specific decisions mid-session.

    CONFLICT HANDLING:
    If results show "\u26a0\ufe0f CONFLICT [id]", you must ask user which rule to follow before proceeding.
    Format your question as: "SuperWiser (Conflict) [id]: <describe the conflicting rules and ask which to follow>"
    After the user responds to resolve the conflict, you do not need to call any tools to resolve or delete rules. This is handled automatically in the background.

    Args:
        context: Explain in 2-3 sentences what you're currently working on. More context
                 helps find relevant rules. Include: the feature/task, what decisions
                 you're making, and any specific technologies involved.
        limit: Maximum number of results to return (default: 5)
        preferences_only: If True, only return universal rules without context (default: False)

    Examples:
        search_rules("Building user authentication. Deciding between JWT and session cookies for token storage.")
        search_rules("Adding error handling to the payment API. Need to decide how to structure error responses and what to log.")
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
        results = search(db_path, context, limit, preferences_only=preferences_only)

        if not results:
            msg = f"No rules found matching '{context}'."
            if preferences_only:
                msg += " (preferences only)"
            return msg
        return format_results(results)
    except Exception as e:
        return f"Error searching rules: {e}"


@mcp.tool()
def load_preferences(task_context: str = "") -> str:
    """Load coding preferences for the current task.

    WHEN TO USE:
    - When user says "use Superwiser", "load preferences", or "load my rules"
    - When starting significant work on a feature
    - When you need to understand the user's coding conventions

    Retrieves both global preferences (apply everywhere) and contextual
    preferences (specific to the task at hand).

    Args:
        task_context: Description of what you're working on (e.g., "building authentication",
                     "fixing database queries", "adding React components")

    Returns:
        Formatted list of relevant coding preferences with context.
    """
    from config import PREFERENCE_GLOBAL_LIMIT, PREFERENCE_CONTEXTUAL_LIMIT, PREFERENCE_MIN_SCORE

    db_path = get_db_path()
    if not Path(db_path).exists():
        return "No preferences recorded yet. Start coding and I'll learn your preferences over time!"

    results = {"global": [], "contextual": []}

    # 1. Get global preferences (most important, ordered by importance_score)
    try:
        with db_context(db_path, timeout=5.0) as db:
            rows = db.execute("""
                SELECT r.id, r.rule, r.context, r.importance_score
                FROM rules r
                ORDER BY COALESCE(r.importance_score, 0) DESC, r.created_at DESC
                LIMIT ?
            """, [PREFERENCE_GLOBAL_LIMIT]).fetchall()
            results["global"] = [
                {"id": row[0], "rule": row[1], "context": row[2], "importance_score": row[3]}
                for row in rows
            ]
    except Exception:
        pass

    # 2. Get contextual matches if task provided
    if task_context and len(task_context.strip()) >= 10:
        # Lazy install search dependencies
        ok, err = ensure_search_deps()
        if ok:
            try:
                from search import search
                contextual = search(db_path, task_context, top_k=PREFERENCE_CONTEXTUAL_LIMIT)
                # Filter by score threshold and exclude already-included global rules
                global_ids = {r['id'] for r in results["global"]}
                results["contextual"] = [
                    r for r in contextual
                    if r.get('hybrid_score', 0) >= PREFERENCE_MIN_SCORE
                    and r['id'] not in global_ids
                ]
            except Exception:
                pass

    return _format_preferences(results)


def _format_preferences(results: dict) -> str:
    """Format preferences for display."""
    def format_rule(r: dict) -> str:
        ctx = r.get('context', '')
        suffix = f" - {ctx[:60]}..." if ctx and len(ctx) > 60 else (f" - {ctx}" if ctx else "")
        return f"- {r['rule']}{suffix}"

    lines = []
    if results["global"]:
        lines.append("## Global Preferences (apply everywhere)")
        lines.extend(format_rule(r) for r in results["global"])

    if results["contextual"]:
        lines.append("\n## Contextual Preferences (for this task)")
        lines.extend(format_rule(r) for r in results["contextual"])

    return "\n".join(lines) if lines else "No preferences found. Start coding and I'll learn your preferences over time!"


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
def list_rules(limit: int = 10, sort_by: str = "recent", preferences_only: bool = False) -> str:
    """List captured rules with total count.

    WHEN TO USE:
    - When user explicitly asks to see their rules
    - When browsing rules by importance or recency
    - Use preferences_only=True to see only universal rules (no context)

    Note: Relevant preferences are automatically loaded on first prompt.
    This tool is for explicit browsing, not routine session initialization.

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
        preferences_only: If True, only show universal rules without context (default: False)

    Returns:
        JSON with rules, total count, and importance info
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return json.dumps({"rules": [], "total": 0, "showing": 0})

    try:
        with db_context(db_path, timeout=5.0) as db:
            # Build WHERE clause for preferences_only filter
            where_clause = "WHERE (r.context IS NULL OR r.context = '')" if preferences_only else ""

            # Get total count (respecting filter)
            count_query = f"SELECT COUNT(*) FROM rules r {where_clause}"
            total = db.execute(count_query).fetchone()[0]

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
                FROM rules r {where_clause} ORDER BY {order_clause} LIMIT ?
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
                "sorted_by": sort_by,
                "preferences_only": preferences_only
            }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_stats() -> str:
    """Get statistics about captured rules and token usage.

    Returns:
        JSON with rule counts, queue status, and token usage statistics
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return json.dumps({"error": "No rules recorded yet"})

    try:
        with db_context(db_path, timeout=5.0) as db:
            # Total rules
            total_rules = db.execute("SELECT COUNT(*) FROM rules").fetchone()[0]

            # Pending conflicts
            pending_conflicts = db.execute(
                "SELECT COUNT(*) FROM pending_conflicts WHERE shown = FALSE"
            ).fetchone()[0]

            # Queue status (prompts being analyzed for rules)
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

            # Get token usage stats
            token_usage = get_token_usage_stats(db_path)

            return json.dumps({
                "total_rules": total_rules,
                "pending_conflicts": pending_conflicts,
                "pending_analysis": pending_analysis,
                "processing_now": processing_now,
                "failed_analysis": failed_analysis,
                "token_usage": token_usage
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


@mcp.tool()
def get_config() -> str:
    """View all Superwiser configuration settings.

    Shows current values, defaults, and descriptions for each setting.
    Use this to understand what can be configured and current values.

    Returns:
        Formatted list of all settings with their values and descriptions
    """
    from config import get_config_with_metadata

    config = get_config_with_metadata()

    lines = ["# Superwiser Configuration\n"]

    for key, info in config.items():
        # Show current value and whether it differs from default
        current = info['value']
        default = info['default']
        is_default = current == default

        lines.append(f"## {key}")
        lines.append(f"**Current value:** {current}" + (" (default)" if is_default else ""))
        lines.append(f"**Description:** {info['description']}")

        if info['type'] == 'enum':
            lines.append(f"**Options:** {', '.join(info['options'])}")
        elif info['type'] in ('int', 'float'):
            lines.append(f"**Range:** {info['min']} - {info['max']}")

        if not is_default:
            lines.append(f"**Default:** {default}")

        lines.append("")  # blank line between settings

    lines.append("---")
    lines.append("Use `set_config(key, value)` to change a setting.")

    return "\n".join(lines)


@mcp.tool()
def set_config(key: str, value: str) -> str:
    """Change a Superwiser configuration setting.

    Changes take effect within a few seconds (worker re-reads config each cycle).

    Args:
        key: The setting name (e.g., 'extraction_model', 'extraction_concurrency')
        value: The new value to set

    Examples:
        set_config("extraction_model", "opus")  # Use opus for better extraction
        set_config("extraction_concurrency", "4")  # More parallel workers
        set_config("preference_global_limit", "10")  # Load more global preferences

    Returns:
        Confirmation message or error
    """
    from config import save_config

    success, message = save_config(key, value)
    if success:
        return f"{message}\n\nChanges take effect within a few seconds."
    return f"Error: {message}"


@mcp.tool()
def reset_config(key: str = None) -> str:
    """Reset configuration settings to defaults.

    Args:
        key: Specific setting to reset, or None to reset all settings

    Returns:
        Confirmation message
    """
    from config import CONFIGURABLE_SETTINGS, CONFIG_FILE

    if key and key not in CONFIGURABLE_SETTINGS:
        return f"Unknown setting: '{key}'. Use get_config() to see valid settings."

    if not CONFIG_FILE.exists():
        return "Already using defaults."

    try:
        if key:
            config = json.loads(CONFIG_FILE.read_text())
            if key not in config:
                return f"{key} is already using the default."
            del config[key]
            if config:
                CONFIG_FILE.write_text(json.dumps(config, indent=2))
            else:
                CONFIG_FILE.unlink()
            return f"Reset {key} to default: {CONFIGURABLE_SETTINGS[key]['default']}"
        else:
            CONFIG_FILE.unlink()
            return "All settings reset to defaults."
    except (json.JSONDecodeError, OSError) as e:
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run()
