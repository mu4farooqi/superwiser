#!/usr/bin/env python3
"""Shared transcript parsing utilities.

Used by queue-input.py (real-time capture) and seed.py (historical seeding).
"""
import json
import re

# Pre-compiled patterns to detect secrets (avoids re-compilation per call)
SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|secret|token|password|credential)\s*[:=]\s*[\'"]?[\w-]{16,}'),
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),              # OpenAI API keys
    re.compile(r'ghp_[a-zA-Z0-9]{36}'),              # GitHub personal access tokens
    re.compile(r'AKIA[0-9A-Z]{16}'),                 # AWS Access Key IDs
    re.compile(r'-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----'),
]


def contains_secrets(text: str) -> bool:
    """Check if text contains potential secrets."""
    return any(p.search(text) for p in SECRET_PATTERNS)


def filter_transcript_entry(entry: dict) -> dict | None:
    """Filter and transform a transcript entry to keep only useful content.

    Returns None if entry should be skipped entirely.
    Returns simplified entry if it contains useful content.
    """
    entry_type = entry.get('type', '')

    # Skip internal state entries
    if entry_type == 'file-history-snapshot':
        return None

    # Skip meta entries (slash command instructions)
    if entry.get('isMeta'):
        return None

    # Handle user messages
    if entry_type == 'user':
        message = entry.get('message', {})
        content = message.get('content', '')

        # Skip empty or command-only messages
        if not content or (isinstance(content, str) and content.startswith('<command-')):
            return None

        # Handle tool results (keep them, they're important context)
        if isinstance(content, list):
            tool_results = [c for c in content if isinstance(c, dict) and c.get('type') == 'tool_result']
            if tool_results:
                results = []
                for r in tool_results:
                    raw = r.get('content', '')
                    # Handle non-string content (lists, dicts)
                    if not isinstance(raw, str):
                        raw = json.dumps(raw, ensure_ascii=False)
                    results.append({'tool_id': r.get('tool_use_id'), 'content': raw[:500]})
                return {'role': 'user', 'type': 'tool_result', 'results': results}
            return None

        return {'role': 'user', 'content': content}

    # Handle assistant messages
    if entry_type == 'assistant':
        message = entry.get('message', {})
        content = message.get('content', [])

        if not isinstance(content, list):
            return None

        # Extract only text and tool_use, skip thinking blocks
        filtered_content = []
        for item in content:
            if not isinstance(item, dict):
                continue

            item_type = item.get('type', '')

            if item_type == 'text':
                text = item.get('text', '')
                if text:
                    filtered_content.append({'type': 'text', 'text': text})

            elif item_type == 'tool_use':
                # Keep tool use but truncate large inputs
                tool_input = item.get('input', {})
                if isinstance(tool_input, dict):
                    tool_input = {
                        k: (v[:200] + '...' if isinstance(v, str) and len(v) > 200 else v)
                        for k, v in tool_input.items()
                    }
                filtered_content.append({
                    'type': 'tool_use',
                    'name': item.get('name', ''),
                    'input': tool_input
                })

        if not filtered_content:
            return None

        return {'role': 'assistant', 'content': filtered_content}

    return None
