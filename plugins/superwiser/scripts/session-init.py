#!/usr/bin/env python3
"""
Unified SessionStart hook - handles all session initialization:
- Dependency installation
- Worker daemon startup
- Project registration
- Database initialization
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from ensure_init import is_recording_disabled, is_worker_running
from db_utils import hook_output

SUPERWISER_DIR = Path.home() / '.superwiser'
VENV_DIR = SUPERWISER_DIR / 'venv'
VENV_PYTHON = VENV_DIR / 'bin' / 'python'
PID_FILE = SUPERWISER_DIR / 'worker.pid'
VERSION_FILE = SUPERWISER_DIR / 'worker.version'
LOG_FILE = SUPERWISER_DIR / 'worker.log'
REGISTRY = SUPERWISER_DIR / 'projects.txt'
STATE_FILE = SUPERWISER_DIR / 'install-state.json'

PACKAGES = [
    ('numpy', 'numpy'),
    ('sentence_transformers', 'sentence-transformers'),
    ('sqlite_vec', 'sqlite-vec'),
    ('mcp', 'mcp')
]
VERSION = "1.2.2"


# ============== Dependency Installation ==============

def is_installed(name: str) -> bool:
    """Check if package is installed in venv."""
    if not VENV_PYTHON.exists():
        return False
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), '-c', f'import {name}'],
            capture_output=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    SUPERWISER_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def ensure_uv() -> bool:
    """Ensure uv is installed."""
    try:
        subprocess.run(['uv', '--version'], check=True, capture_output=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            print("Installing uv...", file=sys.stderr)
            subprocess.run(
                ['sh', '-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'],
                check=True, capture_output=True, timeout=60
            )
            return True
        except Exception:
            return False


def ensure_venv() -> bool:
    """Ensure virtual environment exists."""
    if VENV_PYTHON.exists():
        return True
    try:
        SUPERWISER_DIR.mkdir(parents=True, exist_ok=True)
        print("Creating virtual environment...", file=sys.stderr)
        subprocess.run(['uv', 'venv', str(VENV_DIR)],
                       check=True, capture_output=True, timeout=30)
        return VENV_PYTHON.exists()
    except Exception:
        return False


def install_pkg(pkg: str) -> bool:
    try:
        subprocess.run(['uv', 'pip', 'install', '-p', str(VENV_PYTHON), '-q', pkg],
                       check=True, capture_output=True, timeout=120)
        return True
    except Exception:
        return False


def install_dependencies() -> tuple[bool, str | None]:
    """Install missing dependencies. Returns (success, message)."""
    state = load_state()

    if state.get('version') == VERSION and state.get('status') == 'complete':
        return True, None

    missing = [(imp, pip) for imp, pip in PACKAGES if not is_installed(imp)]

    if not missing:
        save_state({'version': VERSION, 'status': 'complete', 'missing': []})
        return True, None

    if not ensure_uv():
        return False, "**Superwiser**: Failed to install uv package manager."

    if not ensure_venv():
        return False, "**Superwiser**: Failed to create virtual environment."

    failed = []
    for imp, pip in missing:
        print(f"Installing {pip}...", file=sys.stderr)
        if not install_pkg(pip):
            failed.append(pip)

    if failed:
        save_state({'version': VERSION, 'status': 'partial', 'missing': failed})
        msg = f"""**Superwiser**: Some dependencies failed: `{', '.join(failed)}`

Plugin running in **limited mode**. To fix:
```bash
uv pip install -p ~/.superwiser/venv/bin/python {' '.join(failed)}
```"""
        return False, msg

    save_state({'version': VERSION, 'status': 'complete', 'missing': []})
    return True, None


# ============== Worker Management ==============

def kill_old_worker() -> None:
    """Kill existing worker if running."""
    if not PID_FILE.exists():
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 15)  # SIGTERM
    except (ProcessLookupError, ValueError, OSError):
        pass
    PID_FILE.unlink(missing_ok=True)
    VERSION_FILE.unlink(missing_ok=True)


def get_worker_version() -> str | None:
    """Get version of running worker."""
    if not VERSION_FILE.exists():
        return None
    try:
        return VERSION_FILE.read_text().strip()
    except Exception:
        return None


def start_worker() -> None:
    """Start worker daemon, killing old one if version changed."""
    current_version = get_worker_version()

    # Kill old worker if version mismatch
    if is_worker_running() and current_version != VERSION:
        kill_old_worker()
    elif is_worker_running():
        return  # Same version already running

    SUPERWISER_DIR.mkdir(parents=True, exist_ok=True)
    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

    with open(LOG_FILE, 'a') as log:
        process = subprocess.Popen(
            [python_exe, str(SCRIPT_DIR / 'worker.py')],
            stdout=log, stderr=log,
            start_new_session=True
        )

    PID_FILE.write_text(str(process.pid))
    VERSION_FILE.write_text(VERSION)


# ============== Project Registration ==============

def register_project(cwd: str) -> None:
    """Register project with global worker."""
    if not cwd:
        return

    project_path = str(Path(cwd).resolve())
    SUPERWISER_DIR.mkdir(parents=True, exist_ok=True)

    existing = set()
    if REGISTRY.exists():
        existing = {p.strip() for p in REGISTRY.read_text().split('\n') if p.strip()}

    if project_path not in existing:
        existing.add(project_path)
        REGISTRY.write_text('\n'.join(sorted(existing)) + '\n')


# ============== Database Initialization ==============

def init_project_db(cwd: str) -> None:
    """Initialize project database if it doesn't exist."""
    if not cwd:
        return

    db_path = Path(cwd).resolve() / '.claude' / 'superwiser' / 'context.db'
    if db_path.exists():
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'init-db.py'), str(db_path)],
            capture_output=True, timeout=30
        )
    except Exception:
        pass


# ============== Main ==============

def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    cwd = data.get('cwd', '')
    recording_disabled = is_recording_disabled(cwd)

    deps_ok, deps_msg = install_dependencies()

    if not recording_disabled:
        start_worker()

    register_project(cwd)
    init_project_db(cwd)

    if recording_disabled:
        msg = "**Superwiser**: Recording paused. Use `/record` to resume."
    elif deps_msg:
        msg = deps_msg
    else:
        msg = "**Superwiser** active - learning your coding preferences."

    hook_output(msg)


if __name__ == '__main__':
    main()
