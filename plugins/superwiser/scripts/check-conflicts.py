#!/usr/bin/env python3
"""PostToolUse hook - check and display pending conflicts."""
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
        data = {}

    cwd = data.get('cwd', '.')
    ensure_ready(cwd)

    db_path = str(Path(cwd).resolve() / '.claude' / 'superwiser' / 'context.db')
    conflict_msg = check_pending_conflicts(db_path)
    if conflict_msg:
        print(conflict_msg)


if __name__ == '__main__':
    main()
