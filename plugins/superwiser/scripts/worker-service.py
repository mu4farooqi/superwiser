#!/usr/bin/env python3
"""Worker service manager - stop and status commands.

Start functionality is handled by session-init.py.
"""
import signal
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from ensure_init import is_worker_running

SUPERWISER_DIR = Path.home() / '.superwiser'
PID_FILE = SUPERWISER_DIR / 'worker.pid'


def stop() -> None:
    if not PID_FILE.exists():
        print("Worker not running")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print("Worker stopped")
    except Exception:
        print("Worker not running")
    PID_FILE.unlink(missing_ok=True)


def status() -> None:
    if is_worker_running():
        pid = PID_FILE.read_text().strip()
        print(f"Worker running (PID: {pid})")
    else:
        print("Worker not running")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: worker-service.py <stop|status>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'stop':
        stop()
    elif cmd == 'status':
        status()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: worker-service.py <stop|status>")
        sys.exit(1)


if __name__ == '__main__':
    main()
