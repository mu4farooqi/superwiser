#!/bin/bash
# Bootstrap script for Superwiser MCP server
# Installs uv, creates venv, installs minimal deps (mcp only), then starts server

set -e

SUPERWISER_DIR="$HOME/.superwiser"
VENV="$SUPERWISER_DIR/venv"
SCRIPT_DIR="$(dirname "$0")"

# Ensure env (uv + venv)
source "$SCRIPT_DIR/ensure-env.sh"

# Install mcp if missing (fast check via import)
if ! "$VENV/bin/python" -c "import mcp" 2>/dev/null; then
    echo "Installing mcp..." >&2
    uv pip install -p "$VENV/bin/python" -q mcp || exit 1
fi

# Run the MCP server using venv Python
exec "$VENV/bin/python" "$SCRIPT_DIR/mcp-server.py"
