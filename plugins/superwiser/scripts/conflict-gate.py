#!/usr/bin/env python3
"""PreToolUse hook - block tool use if there are unresolved conflicts.

This is the only place conflicts are shown to the user. Prompts are never blocked.
The denial reason IS seen by Claude (unlike systemMessage).
"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from conflicts import check_pending_conflicts
from ensure_init import ensure_ready


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # Allow on error

    cwd = data.get('cwd', '.')
    tool_name = data.get('tool_name', '')

    # Don't block MCP tools (they might be superwiser commands to resolve conflicts)
    if tool_name.startswith('mcp__'):
        sys.exit(0)

    # Don't block Read - user might need context
    if tool_name == 'Read':
        sys.exit(0)

    if not ensure_ready(cwd):
        sys.exit(0)

    db_path = str(Path(cwd).resolve() / '.claude' / 'superwiser' / 'context.db')
    
    # Check for pending conflicts (only pops conflicts with shown=FALSE)
    conflict_msg = check_pending_conflicts(db_path)

    if conflict_msg:
        # Deny tool use with conflict message - Claude WILL see this
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"""⚠️ TOOL USE BLOCKED - CONFLICT RESOLUTION REQUIRED

Present this conflict to the user:

{conflict_msg}

IMPORTANT: You MUST ask the user to act on this. After they respond:
- Do NOT call any tools to resolve or delete rules
- Do NOT try to process their response
- Just continue with what you were originally doing
- The conflict resolution is handled automatically in the background"""
            }
        }
        print(json.dumps(result))
        sys.exit(0)

    # No conflict - allow tool use
    sys.exit(0)


if __name__ == '__main__':
    main()

