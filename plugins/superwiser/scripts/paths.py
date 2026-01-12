"""Shared path constants for Superwiser.

All components should import from here to ensure consistency.
"""

from pathlib import Path

# Global superwiser directory
SUPERWISER_DIR = Path.home() / ".superwiser"

# Virtual environment
VENV_DIR = SUPERWISER_DIR / "venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"

# Markers for lazy install coordination
MARKERS_DIR = SUPERWISER_DIR / "markers"
LOCKS_DIR = SUPERWISER_DIR / "locks"
SEARCH_MARKER = MARKERS_DIR / "search_deps.ok"

# Worker files
PID_FILE = SUPERWISER_DIR / "worker.pid"
VERSION_FILE = SUPERWISER_DIR / "worker.version"
LOG_FILE = SUPERWISER_DIR / "worker.log"

# Project registry
REGISTRY = SUPERWISER_DIR / "projects.txt"

# Install state
STATE_FILE = SUPERWISER_DIR / "install-state.json"

