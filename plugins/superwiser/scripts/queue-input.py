"""Queue single user prompt - UserPromptSubmit hook.

Queues user prompts for rule extraction. Does NOT block on conflicts;
conflict resolution is handled by PreToolUse hook (conflict-gate.py).
"""
import gzip
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import db_context
from config import CONTEXT_MAX_LINES, is_extraction_prompt
from ensure_init import ensure_ready
from transcript_utils import contains_secrets, filter_transcript_entry, is_system_message


def hook_output(msg: str | None = None) -> None:
    """Output JSON response for Claude Code hooks.

    Args:
        msg: System message (shown in UI)
    """
    if msg:
        print(json.dumps({"continue": True, "systemMessage": msg}))
    else:
        print(json.dumps({"continue": True}))


def read_and_compress_context(transcript_path: str, max_lines: int) -> tuple[bytes | None, int]:
    """Read last N lines from transcript, filter, and compress them."""
    if not transcript_path or not Path(transcript_path).exists():
        return None, 0

    try:
        with open(transcript_path, 'r', errors='ignore') as f:
            lines = f.readlines()

        total_lines = len(lines)

        # Take last N lines and filter them
        context_lines = lines[-max_lines:] if len(lines) > max_lines else lines

        filtered_entries = []
        seen_texts = set()  # Deduplicate repeated assistant messages

        for line in context_lines:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
                filtered = filter_transcript_entry(entry)

                if filtered:
                    # Deduplicate assistant text responses (streaming can cause duplicates)
                    if filtered.get('role') == 'assistant':
                        content = filtered.get('content', [])
                        if content and isinstance(content, list):
                            text_items = [c.get('text', '') for c in content if c.get('type') == 'text']
                            text_key = ''.join(text_items)[:100]
                            if text_key and text_key in seen_texts:
                                continue
                            if text_key:
                                seen_texts.add(text_key)

                    filtered_entries.append(filtered)
            except json.JSONDecodeError:
                continue

        if not filtered_entries:
            return None, 0

        # Convert back to compact JSON lines
        context_text = '\n'.join(json.dumps(e, ensure_ascii=False) for e in filtered_entries)
        compressed = gzip.compress(context_text.encode('utf-8'))

        return compressed, total_lines
    except Exception:
        return None, 0


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        hook_output()
        return

    user_prompt = data.get('prompt', '') or data.get('user_prompt', '')
    session_id = data.get('session_id', '')
    cwd = data.get('cwd', '.')
    transcript_path = data.get('transcript_path', '')

    # Skip extraction sessions running in /tmp/superwiser (prevents infinite recursion)
    if cwd.startswith('/tmp/superwiser'):
        hook_output()
        return
    # Fallback: also check prompt content in case cwd check fails
    if is_extraction_prompt(user_prompt):
        hook_output()
        return

    if not ensure_ready(cwd):
        hook_output()
        return

    db_path = str(Path(cwd).resolve() / '.claude' / 'superwiser' / 'context.db')

    prompt_stripped = user_prompt.strip()
    if not prompt_stripped:
        hook_output()
        return
    if prompt_stripped.startswith('/superwiser:'):
        hook_output()
        return
    # Skip system messages (command output, task notifications, etc)
    if is_system_message(user_prompt):
        hook_output()
        return
    if contains_secrets(user_prompt):
        hook_output()
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

    hook_output()


if __name__ == '__main__':
    main()
