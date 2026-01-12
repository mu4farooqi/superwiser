#!/bin/bash
# Shared bootstrap to ensure uv and the Superwiser venv exist.
# Idempotent and safe to call from multiple entry points.

set -e

SUPERWISER_DIR="$HOME/.superwiser"
VENV="$SUPERWISER_DIR/venv"

mkdir -p "$SUPERWISER_DIR"

# Install uv if missing (cross-platform)
if ! command -v uv &> /dev/null; then
    echo "Installing uv..." >&2
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# Create venv if missing
if [ ! -f "$VENV/bin/python" ]; then
    echo "Creating venv..." >&2
    uv venv "$VENV"
fi

