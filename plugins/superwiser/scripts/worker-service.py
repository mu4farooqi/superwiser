#!/usr/bin/env python3
"""
DEPRECATED: Use session-init.py for 'start' functionality.
This script is kept for backwards compatibility and manual stop/status commands.

Worker service manager - global daemon.
"""
import os
import sys
import json
import signal
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SUPERWISER_DIR = Path.home() / '.superwiser'
PID_FILE = SUPERWISER_DIR / 'worker.pid'
LOG_FILE = SUPERWISER_DIR / 'worker.log'


def output(msg=None):
    print(json.dumps({"continue": True, **({"systemMessage": msg} if msg else {})}))


def is_running():
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return False


def start():
    # Consume stdin
    try:
        json.load(sys.stdin)
    except Exception:
        pass

    if is_running():
        output()
        return

    SUPERWISER_DIR.mkdir(parents=True, exist_ok=True)

    with open(LOG_FILE, 'a') as log:
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / 'worker.py')],
            stdout=log, stderr=log,
            start_new_session=True
        )

    PID_FILE.write_text(str(process.pid))
    output()


def stop():
    if not PID_FILE.exists():
        output("Worker not running")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        output("Worker stopped")
    except Exception:
        output("Worker not running")
    PID_FILE.unlink(missing_ok=True)


def status():
    if is_running():
        pid = PID_FILE.read_text().strip()
        output(f"Worker running (PID: {pid})")
    else:
        output("Worker not running")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'start'
    actions = {'start': start, 'stop': stop, 'status': status}
    actions.get(cmd, start)()
