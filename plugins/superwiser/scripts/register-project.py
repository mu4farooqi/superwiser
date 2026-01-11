#!/usr/bin/env python3
"""
DEPRECATED: Use session-init.py instead.
This script is kept for backwards compatibility only.

Register project with global worker.
"""
import json
import sys
from pathlib import Path

REGISTRY = Path.home() / '.superwiser' / 'projects.txt'


def main():
    try:
        data = json.load(sys.stdin)
        cwd = data.get('cwd', '')
    except Exception:
        cwd = ''

    if not cwd:
        print(json.dumps({"continue": True}))
        return

    project_path = str(Path(cwd).resolve())
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)

    existing = set()
    if REGISTRY.exists():
        existing = set(p.strip() for p in REGISTRY.read_text().split('\n') if p.strip())

    if project_path not in existing:
        existing.add(project_path)
        REGISTRY.write_text('\n'.join(sorted(existing)) + '\n')

    print(json.dumps({"continue": True}))


if __name__ == '__main__':
    main()


