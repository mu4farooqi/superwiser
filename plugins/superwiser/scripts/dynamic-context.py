#!/usr/bin/env python3
"""
Inject context reminders to call search_rules.

Runs on both PostToolUse and UserPromptSubmit hooks.
Fires reminder on first prompt, then after configured interval.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from config import get_runtime_config

FIRST_PROMPT_MESSAGE = """<superwiser_reminder>
Call search_rules with 2-3 sentences describing what you're working on.
This helps find relevant user coding preferences for better decisions.
</superwiser_reminder>"""

PERIODIC_MESSAGE = """<superwiser_reminder>
If you have new context since your last call to search_rules (new feature area,
different decisions to make), call it again with 2-3 sentences describing your current work.
Skip if you're still working on the same thing.
</superwiser_reminder>"""


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

    # Get current session ID (field is 'session_id' per hooks spec)
    session_id = input_data.get('session_id', '')
    if not session_id:
        return  # No session ID, can't track state

    # Per-session state file for isolation
    cwd = os.environ.get('PWD', os.getcwd())
    state_dir = Path(cwd) / '.claude' / 'superwiser' / 'sessions'
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
