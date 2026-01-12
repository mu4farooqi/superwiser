#!/bin/bash
# Generic hook runner - runs Python scripts with venv
# Usage: hook-runner.sh <script-name.py>
SCRIPT_DIR="$(dirname "$0")"
source "$SCRIPT_DIR/ensure-env.sh"
exec "$VENV/bin/python" "$SCRIPT_DIR/$1"
