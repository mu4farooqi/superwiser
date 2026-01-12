#!/bin/bash
# Bootstrap script for Superwiser MCP server
# Installs uv, creates venv, installs minimal deps (mcp only), then starts server

set -e

SCRIPT_DIR="$(dirname "$0")"

# Ensure env (uv + venv) - defines SUPERWISER_DIR and VENV
source "$SCRIPT_DIR/ensure-env.sh"

# Install mcp if missing
if ! uv pip show -p "$VENV/bin/python" mcp >/dev/null 2>&1; then
    echo "Installing mcp..." >&2
    uv pip install -p "$VENV/bin/python" -q mcp || exit 1
fi

# Run the MCP server using venv Python
exec "$VENV/bin/python" "$SCRIPT_DIR/mcp-server.py"
