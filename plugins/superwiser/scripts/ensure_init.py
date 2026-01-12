"""Lightweight init check - ensures superwiser is initialized.

Call ensure_ready() at the start of any hook to auto-initialize if needed.
Returns False if recording is disabled, True otherwise.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from paths import SUPERWISER_DIR, PID_FILE


def is_worker_running() -> bool:
    """Check if worker daemon is running."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return False


def is_recording_disabled(cwd: str) -> bool:
    """Check if recording is disabled for this project."""
    if not cwd:
        return False
    disabled_file = Path(cwd).resolve() / '.claude' / 'superwiser' / 'disabled'
    return disabled_file.exists()


def run_init(cwd: str) -> None:
    """Run full initialization."""
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / 'session-init.py')],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        proc.communicate(input=json.dumps({'cwd': cwd}), timeout=30)
    except Exception:
        pass


def ensure_ready(cwd: str, check_db: bool = True) -> bool:
    """Ensure superwiser is initialized.

    Returns False if recording is disabled, True otherwise.
    """
    if is_recording_disabled(cwd):
        return False

    needs_init = not is_worker_running()

    if check_db and cwd:
        db_path = Path(cwd).resolve() / '.claude' / 'superwiser' / 'context.db'
        if not db_path.exists():
            needs_init = True

    if needs_init:
        run_init(cwd)

    return True


def set_recording_state(cwd: str, enabled: bool) -> None:
    """Set recording enabled/disabled for a project."""
    if not cwd:
        return

    disabled_file = Path(cwd).resolve() / '.claude' / 'superwiser' / 'disabled'

    if enabled:
        disabled_file.unlink(missing_ok=True)
    else:
        disabled_file.parent.mkdir(parents=True, exist_ok=True)
        disabled_file.write_text('Recording disabled by user\n')
