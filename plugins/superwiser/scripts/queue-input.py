#!/usr/bin/env python3
"""Queue single user prompt - UserPromptSubmit hook.

Also checks for and displays pending conflicts to the user.
"""
import gzip
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import db_context
from config import CONTEXT_MAX_LINES
from conflicts import check_pending_conflicts
from ensure_init import ensure_ready

SECRET_PATTERNS = [
    r'(?i)(api[_-]?key|secret|token|password|credential)\s*[:=]\s*[\'"]?[\w-]{16,}',
    r'sk-[a-zA-Z0-9]{20,}',              # OpenAI API keys
    r'ghp_[a-zA-Z0-9]{36}',              # GitHub personal access tokens
    r'AKIA[0-9A-Z]{16}',                 # AWS Access Key IDs
    r'-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----',  # Private keys
]


def contains_secrets(text: str) -> bool:
    return any(re.search(p, text) for p in SECRET_PATTERNS)


def read_and_compress_context(transcript_path: str, max_lines: int) -> tuple[bytes | None, int]:
    """Read last N lines from transcript and compress them."""
    if not transcript_path or not Path(transcript_path).exists():
        return None, 0

    try:
        with open(transcript_path, 'r', errors='ignore') as f:
            lines = f.readlines()

        total_lines = len(lines)
        context_lines = lines[-max_lines:] if len(lines) > max_lines else lines
        context_text = ''.join(context_lines)
        compressed = gzip.compress(context_text.encode('utf-8'))

        return compressed, total_lines
    except Exception:
        return None, 0


def output_continue(msg: str | None = None) -> None:
    """Output JSON response for hook."""
    response = {"continue": True}
    if msg:
        response["systemMessage"] = msg
    print(json.dumps(response))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        output_continue()
        return

    user_prompt = data.get('prompt', '') or data.get('user_prompt', '')
    session_id = data.get('session_id', '')
    cwd = data.get('cwd', '.')
    transcript_path = data.get('transcript_path', '')

    if not ensure_ready(cwd):
        output_continue()
        return

    db_path = str(Path(cwd).resolve() / '.claude' / 'superwiser' / 'context.db')

    conflict_msg = check_pending_conflicts(db_path)
    if conflict_msg:
        output_continue(conflict_msg)

    prompt_stripped = user_prompt.strip()
    if not prompt_stripped:
        if not conflict_msg:
            output_continue()
        return
    if prompt_stripped.startswith('/superwiser:'):
        if not conflict_msg:
            output_continue()
        return
    if contains_secrets(user_prompt):
        if not conflict_msg:
            output_continue()
        return

    context_blob, total_lines = read_and_compress_context(transcript_path, CONTEXT_MAX_LINES)

    try:
        with db_context(db_path, timeout=5.0) as db:
            db.execute(
                """INSERT INTO queue
                   (transcript_path, position, human_input, context_blob, session_id)
                   VALUES (?, ?, ?, ?, ?)""",
                [transcript_path, total_lines, user_prompt, context_blob, session_id]
            )
            db.commit()
    except Exception:
        pass

    if not conflict_msg:
        output_continue()


if __name__ == '__main__':
    main()
