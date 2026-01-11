#!/usr/bin/env python3
"""Seed rules from historical transcripts.

Finds all transcript files for a project, extracts user prompts,
and queues them for extraction with override_mode=TRUE (last writer wins).
"""
import gzip
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import db_context
from config import CONTEXT_MAX_LINES
from transcript_utils import contains_secrets, filter_transcript_entry


def get_project_transcripts(project_path: str) -> list[Path]:
    """Find main transcript files for a project, sorted oldest-first.
    
    Transcripts are stored in ~/.claude/projects/{encoded-path}/
    where encoded-path is the project path with / replaced by -.
    """
    # Encode: /root/fanwick -> -root-fanwick
    encoded = project_path.lstrip('/').replace('/', '-')
    transcripts_dir = Path.home() / '.claude' / 'projects' / encoded
    
    if not transcripts_dir.exists():
        return []
    
    # Get main .jsonl files (not in subagents/ subdirectories)
    files = [f for f in transcripts_dir.glob('*.jsonl')
             if f.is_file() and 'subagents' not in f.parts]
    
    # Sort by modification time (oldest first)
    return sorted(files, key=lambda f: f.stat().st_mtime)


def extract_prompts_with_context(transcript_path: Path) -> list[dict]:
    """Extract user prompts with their preceding context from a transcript.
    
    Returns list of dicts with: prompt, position, context_blob, session_id
    """
    try:
        lines = transcript_path.read_text(errors='ignore').splitlines()
    except Exception:
        return []
    
    prompts = []
    all_entries = []
    
    # Parse all entries
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            all_entries.append((i, entry))
        except json.JSONDecodeError:
            continue
    
    # Find user prompts
    for idx, (line_num, entry) in enumerate(all_entries):
        if entry.get('type') != 'user':
            continue

        message = entry.get('message', {})
        content = message.get('content', '')

        # Skip non-string content (tool results) and command prefixes
        if not isinstance(content, str):
            continue
        stripped = content.strip()
        if not stripped or stripped.startswith(('/', '<command-')) or contains_secrets(content):
            continue
        
        # Build context from preceding entries
        context_entries = []
        seen_texts = set()
        
        # Take up to CONTEXT_MAX_LINES entries before this one
        start_idx = max(0, idx - CONTEXT_MAX_LINES)
        for prev_idx in range(start_idx, idx):
            _, prev_entry = all_entries[prev_idx]
            filtered = filter_transcript_entry(prev_entry)
            if not filtered:
                continue

            # Deduplicate assistant text by first 100 chars
            if filtered.get('role') == 'assistant':
                fc = filtered.get('content', [])
                if isinstance(fc, list):
                    text_key = ''.join(c.get('text', '') for c in fc if c.get('type') == 'text')[:100]
                    if text_key:
                        if text_key in seen_texts:
                            continue
                        seen_texts.add(text_key)

            context_entries.append(filtered)
        
        # Compress context
        context_blob = None
        if context_entries:
            context_text = '\n'.join(json.dumps(e, ensure_ascii=False) for e in context_entries)
            context_blob = gzip.compress(context_text.encode('utf-8'))
        
        prompts.append({
            'prompt': content,
            'position': line_num,
            'context_blob': context_blob,
            'session_id': entry.get('sessionId', '')
        })
    
    return prompts


def seed_project(project_path: str, db_path: str) -> dict:
    """Queue all historical prompts for extraction with override_mode=TRUE.

    Returns dict with queued count and transcript count.
    """
    transcripts = get_project_transcripts(project_path)

    if not transcripts:
        return {'queued': 0, 'transcripts': 0, 'message': 'No transcripts found'}

    try:
        with db_context(db_path, timeout=30.0) as db:
            # Get existing (transcript_path, position) pairs to avoid duplicates
            existing = set(db.execute(
                "SELECT transcript_path, position FROM queue"
            ).fetchall())

            # Collect all prompts for batch insert
            to_insert = []
            for transcript in transcripts:
                prompts = extract_prompts_with_context(transcript)
                transcript_str = str(transcript)

                for prompt_data in prompts:
                    key = (transcript_str, prompt_data['position'])
                    if key not in existing:
                        to_insert.append((
                            transcript_str,
                            prompt_data['position'],
                            prompt_data['prompt'],
                            prompt_data['context_blob'],
                            prompt_data['session_id']
                        ))

            if to_insert:
                db.executemany("""
                    INSERT INTO queue
                    (transcript_path, position, human_input, context_blob, session_id, override_mode)
                    VALUES (?, ?, ?, ?, ?, TRUE)
                """, to_insert)
                db.commit()

            queued = len(to_insert)
    except Exception as e:
        return {'queued': 0, 'transcripts': len(transcripts), 'error': str(e)}

    return {
        'queued': queued,
        'transcripts': len(transcripts),
        'message': f'Queued {queued} prompts from {len(transcripts)} transcripts for extraction'
    }


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: seed.py <project_path> <db_path>")
        sys.exit(1)
    
    result = seed_project(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2))

