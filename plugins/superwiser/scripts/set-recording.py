#!/usr/bin/env python3
"""Set recording state for a project."""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from ensure_init import set_recording_state


def main():
    if len(sys.argv) < 3:
        print("Usage: set-recording.py <cwd> <enable|disable>")
        sys.exit(1)

    cwd = sys.argv[1]
    action = sys.argv[2].lower()

    if action == 'enable':
        set_recording_state(cwd, enabled=True)
        print("Recording enabled.")
    elif action == 'disable':
        set_recording_state(cwd, enabled=False)
        print("Recording disabled. Will persist across sessions.")
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == '__main__':
    main()
