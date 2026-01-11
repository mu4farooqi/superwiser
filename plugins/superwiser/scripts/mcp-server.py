#!/usr/bin/env python3
"""MCP server for Superwiser - exposes search and context tools to Claude Code."""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from mcp.server.fastmcp import FastMCP
from db_utils import db_context
from search import search, format_results

mcp = FastMCP("superwiser")


def get_db_path() -> str:
    """Get database path from env var or current working directory."""
    return os.environ.get('SUPERWISER_DB_PATH') or str(Path.cwd() / '.claude' / 'superwiser' / 'context.db')


@mcp.tool()
def search_preferences(query: str, limit: int = 5) -> str:
    """Search user's recorded preferences and coding decisions.

    Use this to understand how the user likes things done - their coding style,
    preferred libraries, architectural patterns, and past decisions.

    Results include a context_id (like 'x7k9m2') for each rule.

    IMPORTANT - Conflict Handling:
    If any result shows "⚠️ CONFLICT [id]", there are conflicting rules for that topic.
    You MUST ask the user which rule to follow before proceeding. Format your question as:
    "SuperWiser (Conflict) [id]: <describe the conflicting rules and ask which to follow>"

    Args:
        query: What to search for (e.g., 'error handling', 'testing', 'naming conventions')
        limit: Maximum number of results to return (default: 5)

    Returns:
        Formatted list of relevant user preferences and decisions
    """
    db_path = get_db_path()

    if not Path(db_path).exists():
        return "No preferences recorded yet. The user needs to use Claude Code for a while first."

    try:
        results = search(db_path, query, limit)
        if not results:
            return f"No preferences found matching '{query}'."
        return format_results(results)
    except Exception as e:
        return f"Error searching preferences: {e}"


@mcp.tool()
def get_context(id: str) -> str:
    """Get a specific context with all its rules by ID.

    Args:
        id: The 6-character context ID (e.g., 'x7k9m2')

    Returns:
        Context details with all rules, or error message
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return f"Context {id} not found"

    try:
        with db_context(db_path, timeout=5.0) as db:
            ctx = db.execute("SELECT id, tags FROM context_graph WHERE id = ?", [id]).fetchone()
            if not ctx:
                return f"Context {id} not found"

            rules = db.execute("""
                SELECT rule, context, confidence FROM rules
                WHERE context_id = ? ORDER BY created_at
            """, [id]).fetchall()

            return json.dumps({
                "id": ctx[0],
                "tags": json.loads(ctx[1]) if ctx[1] else [],
                "rules": [{"index": i, "rule": r[0], "context": r[1], "confidence": r[2]}
                          for i, r in enumerate(rules, 1)],
                "has_conflict": len(rules) > 1
            }, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_conflicts() -> str:
    """List all unresolved conflicts (contexts with multiple rules).

    Returns:
        List of conflicting contexts with their rules, or message if none
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return "No conflicts found"

    try:
        with db_context(db_path, timeout=5.0) as db:
            conflicts = db.execute("""
                SELECT cg.id, cg.tags, COUNT(r.id) as rule_count
                FROM context_graph cg JOIN rules r ON r.context_id = cg.id
                GROUP BY cg.id HAVING rule_count > 1
                ORDER BY cg.created_at DESC LIMIT 20
            """).fetchall()

            if not conflicts:
                return "No conflicts found"

            results = []
            for ctx_id, tags, count in conflicts:
                rules = db.execute(
                    "SELECT rule, confidence FROM rules WHERE context_id = ? ORDER BY created_at",
                    [ctx_id]
                ).fetchall()
                results.append({
                    "id": ctx_id,
                    "tags": json.loads(tags) if tags else [],
                    "rule_count": count,
                    "rules": [{"index": i, "rule": r[0], "confidence": r[1]} for i, r in enumerate(rules, 1)]
                })
            return json.dumps(results, indent=2)
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
def list_recent_rules(limit: int = 20) -> str:
    """List the most recently captured rules and decisions.

    Args:
        limit: Maximum number of rules to return (default: 20)

    Returns:
        JSON list of recent rules with id, rule text, confidence, and date
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return "No rules recorded yet."

    try:
        with db_context(db_path, timeout=5.0) as db:
            rules = db.execute("""
                SELECT r.context_id, r.rule, r.confidence, date(r.created_at) as date
                FROM rules r ORDER BY r.created_at DESC LIMIT ?
            """, [limit]).fetchall()

            if not rules:
                return "No rules recorded yet."

            return json.dumps([
                {"id": r[0], "rule": r[1], "confidence": r[2], "date": r[3]}
                for r in rules
            ], indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_stats() -> str:
    """Get statistics about recorded rules.

    Returns:
        JSON with total rules, context groups, confidence breakdown, queue status, and pending conflicts
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return json.dumps({"error": "No database found. Start using Superwiser to record preferences."})

    try:
        with db_context(db_path, timeout=5.0) as db:
            # Rule stats
            rule_stats = db.execute("""
                SELECT
                    COUNT(*) as total_rules,
                    COUNT(DISTINCT context_id) as context_groups,
                    SUM(CASE WHEN confidence='strong' THEN 1 ELSE 0 END) as strong,
                    SUM(CASE WHEN confidence='normal' THEN 1 ELSE 0 END) as normal,
                    SUM(CASE WHEN confidence='weak' THEN 1 ELSE 0 END) as weak
                FROM rules
            """).fetchone()

            # Queue status
            queue_stats = db.execute("""
                SELECT status, COUNT(*) FROM queue GROUP BY status
            """).fetchall()

            # Pending conflicts
            conflicts = db.execute("""
                SELECT COUNT(*) FROM pending_conflicts WHERE shown = FALSE
            """).fetchone()

            return json.dumps({
                "rules": {
                    "total": rule_stats[0],
                    "context_groups": rule_stats[1],
                    "by_confidence": {
                        "strong": rule_stats[2],
                        "normal": rule_stats[3],
                        "weak": rule_stats[4]
                    }
                },
                "queue": {s[0]: s[1] for s in queue_stats} if queue_stats else {},
                "pending_conflicts": conflicts[0] if conflicts else 0
            }, indent=2)
    except Exception as e:
        return f"Error: {e}"


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
def export_preferences(output_format: str = "json") -> str:
    """Export all captured preferences.

    Args:
        output_format: Output format - "json" or "text" (default: "json")

    Returns:
        All preferences in the requested format
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return "No preferences recorded yet."

    try:
        results = search(db_path, "", limit=9999)
        if not results:
            return "No preferences recorded yet."

        if output_format == "json":
            return json.dumps([
                {
                    "id": r.get("context_id"),
                    "rule": r.get("rule"),
                    "context": r.get("context"),
                    "confidence": r.get("confidence"),
                    "tags": r.get("tags", [])
                }
                for r in results
            ], indent=2)
        else:
            return format_results(results)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def delete_context(context_id: str) -> str:
    """Delete a rule group by context ID.

    Args:
        context_id: The 6-character context ID (e.g., 'x7k9m2')

    Returns:
        Confirmation or error message
    """
    db_path = get_db_path()
    if not Path(db_path).exists():
        return f"Context {context_id} not found"

    try:
        with db_context(db_path, timeout=5.0) as db:
            # Check if context exists
            exists = db.execute(
                "SELECT id FROM context_graph WHERE id = ?", [context_id]
            ).fetchone()

            if not exists:
                return f"Context {context_id} not found"

            # Delete from all tables
            db.execute("DELETE FROM rules WHERE context_id = ?", [context_id])
            db.execute("DELETE FROM context_graph WHERE id = ?", [context_id])
            db.execute("DELETE FROM pending_conflicts WHERE context_id = ?", [context_id])
            db.commit()

            return f"Context {context_id} and all its rules have been deleted."
    except Exception as e:
        return f"Error deleting context: {e}"


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


if __name__ == "__main__":
    mcp.run()
