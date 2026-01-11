#!/usr/bin/env python3
"""
DEPRECATED: Use session-init.py instead.
This script is kept for backwards compatibility only.

Smart install with graceful degradation - never blocks session.
"""

import sys
import json
import subprocess
from pathlib import Path

PACKAGES = [
    ('numpy', 'numpy'),
    ('sentence_transformers', 'sentence-transformers'),
    ('sqlite_vec', 'sqlite-vec'),
    ('mcp', 'mcp')
]
SUPERWISER_DIR = Path.home() / '.superwiser'
STATE_FILE = SUPERWISER_DIR / 'install-state.json'
VERSION = "1.1.0"  # Bump when deps change


def output(msg=None):
    print(json.dumps({"continue": True, **({"systemMessage": msg} if msg else {})}))


def is_installed(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    SUPERWISER_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def install(pkg):
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', pkg],
                       check=True, capture_output=True, timeout=120)
        return True
    except Exception:
        return False


def main():
    try:
        json.load(sys.stdin)  # Consume stdin
    except Exception:
        pass

    state = load_state()

    # Skip if already installed this version and all packages present
    if state.get('version') == VERSION and state.get('status') == 'complete':
        if all(is_installed(imp) for imp, _ in PACKAGES):
            output()
            return

    missing = [(imp, pip) for imp, pip in PACKAGES if not is_installed(imp)]

    if not missing:
        save_state({'version': VERSION, 'status': 'complete', 'missing': []})
        output()
        return

    # Try to install missing packages
    failed = []
    for imp, pip in missing:
        print(f"Installing {pip}...", file=sys.stderr)
        if not install(pip):
            failed.append(pip)

    if failed:
        save_state({'version': VERSION, 'status': 'partial', 'missing': failed})
        msg = f"""⚠️ **Superwiser**: Some dependencies failed to install: `{', '.join(failed)}`

Plugin running in **limited mode** - preferences won't be extracted until fixed.

**To fix, run in terminal:**
```bash
pip install {' '.join(failed)}
```
Then restart Claude Code."""
        output(msg)
    else:
        save_state({'version': VERSION, 'status': 'complete', 'missing': []})
        output("✅ **Superwiser**: Dependencies installed successfully")


if __name__ == '__main__':
    main()
