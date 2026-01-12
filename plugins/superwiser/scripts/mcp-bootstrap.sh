#!/bin/bash
# Bootstrap script for Superwiser MCP server
# Installs uv, creates venv, installs minimal deps (mcp only), then starts server

set -e

SUPERWISER_DIR="$HOME/.superwiser"
VENV="$SUPERWISER_DIR/venv"
SCRIPT_DIR="$(dirname "$0")"

# Ensure directories exist
mkdir -p "$SUPERWISER_DIR/locks"
mkdir -p "$SUPERWISER_DIR/markers"

# Install uv if missing (cross-platform)
if ! command -v uv &> /dev/null; then
    echo "Installing uv..." >&2
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    # Add to PATH for this session (uv installs to these locations)
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# Create venv if missing
if [ ! -f "$VENV/bin/python" ]; then
    echo "Creating venv..." >&2
    uv venv "$VENV" || exit 1
fi

# Install mcp if missing (fast check via import)
if ! "$VENV/bin/python" -c "import mcp" 2>/dev/null; then
    echo "Installing mcp..." >&2
    uv pip install -p "$VENV/bin/python" -q mcp || exit 1
fi

# Run the MCP server using venv Python
exec "$VENV/bin/python" "$SCRIPT_DIR/mcp-server.py"
