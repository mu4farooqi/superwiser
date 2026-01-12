"""
Unified SessionStart hook - handles all session initialization:
- Dependency installation (using uv)
- Worker daemon startup
- Project registration
- Database initialization
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from ensure_init import is_recording_disabled, is_worker_running
from db_utils import hook_output
from paths import (
    SUPERWISER_DIR, VENV_DIR, VENV_PYTHON,
    PID_FILE, VERSION_FILE, LOG_FILE,
    REGISTRY, STATE_FILE, MARKERS_DIR, SEARCH_MARKER,
    SESSION_MARKERS_DIR
)
ENSURE_ENV = SCRIPT_DIR / 'ensure-env.sh'

# Packages to install for worker
# Version pins should match mcp-server.py SEARCH_DEPS for consistency
PACKAGES = [
    ('numpy', 'numpy'),
    ('sentence_transformers', 'sentence-transformers==3.4.1'),
    ('sqlite_vec', 'sqlite-vec==0.1.6'),
    ('mcp', 'mcp')
]
VERSION = "1.3.0"


# ============== Dependency Installation ==============

def is_installed(pip_spec: str) -> bool:
    """Check if package is installed in venv using uv pip show (no Python import).
    
    Accepts a pip spec (e.g., 'sentence-transformers==3.4.1') but only uses
    the base name for the presence check.
    """
    if not VENV_PYTHON.exists():
        return False
    try:
        base = pip_spec.split('==')[0]
        result = subprocess.run(
            ['uv', 'pip', 'show', '-p', str(VENV_PYTHON), base],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
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


def install_pkg(pkg: str) -> bool:
    """Install package using uv. Assumes uv and venv already exist (from bootstrap)."""
    try:
        subprocess.run(
            ['uv', 'pip', 'install', '-p', str(VENV_PYTHON), '-q', pkg],
            check=True, capture_output=True, timeout=120
        )
        return True
    except Exception:
        return False


def install_dependencies() -> tuple[bool, str | None]:
    """Install missing dependencies. Returns (success, message).
    
    Assumes bootstrap has already:
    1. Installed uv
    2. Created the venv
    3. Installed mcp package
    """
    try:
        subprocess.run(["bash", str(ENSURE_ENV)], check=True, timeout=120)
    except Exception:
        return False, "**Superwiser**: Failed to prepare environment (uv/venv)."

    state = load_state()

    if state.get('version') == VERSION and state.get('status') == 'complete':
        # Ensure search marker exists (for MCP lazy install coherence)
        write_search_marker_if_installed()
        return True, None

    # Check if venv exists (bootstrap should have created it)
    if not VENV_PYTHON.exists():
        return False, "**Superwiser**: Venv not found. Please restart the session."

    missing = [(imp, pip) for imp, pip in PACKAGES if not is_installed(pip)]

    if not missing:
        save_state({'version': VERSION, 'status': 'complete', 'missing': []})
        write_search_marker_if_installed()
        return True, None

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
    write_search_marker_if_installed()
    return True, None


def write_search_marker_if_installed() -> None:
    """Write search deps marker if sentence-transformers is installed.
    
    This ensures MCP lazy install skips redundant installs.
    """
    if SEARCH_MARKER.exists():
        return
    if is_installed('sentence-transformers==3.4.1') and is_installed('sqlite-vec==0.1.6'):
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        SEARCH_MARKER.write_text("ok")


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
    # Clean up any orphan workers from previous installs (missing PID file)
    kill_orphan_workers()

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
            [str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable,
             str(SCRIPT_DIR / 'init-db.py'), str(db_path)],
            capture_output=True, timeout=30
        )
    except Exception:
        pass


def kill_orphan_workers() -> None:
    """Kill any running worker.py processes that lack our PID file."""
    if PID_FILE.exists():
        return  # Regular path handles PID-managed workers
    try:
        output = subprocess.check_output(['ps', '-eo', 'pid,args'], text=True, timeout=5)
        for line in output.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            pid_str, args = parts
            if 'plugins/superwiser/scripts/worker.py' in args:
                try:
                    pid = int(pid_str)
                    os.kill(pid, 15)
                except Exception:
                    continue
    except Exception:
        pass


# ============== Session Marker Cleanup ==============

def cleanup_session_markers() -> None:
    """Remove session markers older than 24 hours.

    Session markers track which sessions have had their first-prompt search.
    Old markers are cleaned up to avoid accumulating stale files.
    """
    if not SESSION_MARKERS_DIR.exists():
        return
    cutoff = time.time() - 86400  # 24 hours
    for marker in SESSION_MARKERS_DIR.glob("*.searched"):
        try:
            if marker.stat().st_mtime < cutoff:
                marker.unlink(missing_ok=True)
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
    cleanup_session_markers()

    if recording_disabled:
        msg = "**Superwiser**: Recording paused. Use `/record` to resume."
    elif deps_msg:
        msg = deps_msg
    else:
        msg = "**Superwiser** active - learning your coding preferences."


    print(json.dumps({ "continue": True, "systemMessage": msg }))


if __name__ == '__main__':
    main()