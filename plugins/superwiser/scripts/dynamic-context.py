#!/usr/bin/env python3
"""
Inject context reminders to load preferences or search rules.

Runs on both PostToolUse and UserPromptSubmit hooks.
- First prompt: Remind to call load_preferences (gets global + contextual)
- Subsequent reminders: Remind to call search_rules (for new work phases)
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from config import get_runtime_config
from paths import get_project_root

MIN_RULES_FOR_REMINDERS = 10

FIRST_PROMPT_MESSAGE = """<superwiser_reminder>
You MUST call load_preferences with 2-3 sentences describing what you're working on.
This helps find relevant user coding/design/architecture preferences for better decisions.
</superwiser_reminder>"""

PERIODIC_MESSAGE = """<superwiser_reminder>
Consider calling search_rules if you're entering a different phase of work
(error handling, testing, component structure, API design). Describe your current
focus in 2-3 sentences—different phases may have relevant rules.
</superwiser_reminder>"""


def count_rules() -> int:
    """Count total rules in project database."""
    db_path = Path(get_project_root()) / '.claude' / 'superwiser' / 'context.db'
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT COUNT(*) FROM rules")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def read_stdin():
    """Read and parse stdin JSON. Returns (hook_event, input_data)."""
    try:
        input_data = json.load(sys.stdin)
        # Field is 'hook_event_name' per hooks spec
        return input_data.get('hook_event_name', 'UserPromptSubmit'), input_data
    except Exception:
        return 'UserPromptSubmit', {}


def atomic_write_json(path: Path, data: dict):
    """Write JSON atomically using temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (ensures same filesystem for rename)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp_path, path)  # Atomic on POSIX
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main():
    # Read stdin first (can only read once)
    hook_event, input_data = read_stdin()

    config = get_runtime_config()
    if not config.get('dynamic_context_enabled', True):
        return

    # Only send reminders if there are enough rules to make search worthwhile
    if count_rules() < MIN_RULES_FOR_REMINDERS:
        return

    # Get current session ID (field is 'session_id' per hooks spec)
    session_id = input_data.get('session_id', '')
    if not session_id:
        return  # No session ID, can't track state

    # Per-session state file for isolation
    state_dir = Path(get_project_root()) / '.claude' / 'superwiser' / 'sessions'
    state_file = state_dir / f'{session_id}.json'

    # Load state for THIS session
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    now = time.time()
    last = state.get('last_reminder_at', 0)
    is_first = (last == 0)  # First time seeing this session
    interval = config.get('dynamic_context_interval', 60)

    # Determine if we should remind
    if is_first:
        # First time this session: fire first prompt message
        message = FIRST_PROMPT_MESSAGE
        state['last_reminder_at'] = now
    elif (now - last) >= interval:
        # Same session, interval passed: fire periodic message
        message = PERIODIC_MESSAGE
        state['last_reminder_at'] = now
    else:
        # Same session, too soon: no reminder
        return

    # Write state atomically BEFORE outputting reminder
    try:
        atomic_write_json(state_file, state)
    except OSError:
        return  # Can't write state, skip reminder to avoid duplicates

    # Output reminder
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": message
        }
    }))


if __name__ == '__main__':
    main()
