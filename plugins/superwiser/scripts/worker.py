#!/usr/bin/env python3
"""Global worker - polls all registered project queues.

Handles v2 schema with context_graph + rules tables, conflict detection,
and resolution handling.
"""
import gzip
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import get_db, load_sqlite_vec, generate_embedding, generate_id
from config import (
    EXTRACTION_PROMPT, POLL_INTERVAL, RATE_LIMIT, HEAL_INTERVAL,
    EXTRACTION_TIMEOUT, EXTRACTION_MAX_TURNS, MIN_PROMPT_LENGTH
)

SUPERWISER_DIR = Path.home() / '.superwiser'
REGISTRY = SUPERWISER_DIR / 'projects.txt'

running = True
last_heal = 0


def signal_handler(sig, frame) -> None:
    global running
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def check_dependencies() -> list[str]:
    """Check required dependencies. Returns list of missing package names."""
    deps = [
        ('numpy', 'numpy'),
        ('sentence_transformers', 'sentence-transformers'),
        ('sqlite_vec', 'sqlite-vec')
    ]
    missing = []
    for module, name in deps:
        try:
            __import__(module)
        except ImportError:
            missing.append(name)
    return missing


def get_registered_projects() -> list[str]:
    if not REGISTRY.exists():
        return []
    return [p.strip() for p in REGISTRY.read_text().split('\n') if p.strip()]


def decompress_context(context_blob: bytes | None) -> str | None:
    """Decompress gzipped context blob."""
    if not context_blob:
        return None
    try:
        return gzip.decompress(context_blob).decode('utf-8')
    except Exception:
        return None


def context_has_conflict_id(context_text: str | None, check_lines: int = 5) -> bool:
    """Check if recent context lines contain a conflict ID pattern [abc123]."""
    if not context_text:
        return False
    lines = context_text.strip().split('\n')
    recent = '\n'.join(lines[-check_lines:])
    return bool(re.search(r'\[[a-z0-9]{6}\]', recent))


def extract_with_claude(human_input: str, context_blob: bytes | None, db_path: str) -> dict:
    """Extract preference/decision using Claude with Read tool and MCP search."""
    context_text = decompress_context(context_blob)
    context_file = None
    mcp_config_file = None

    try:
        if context_text:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl',
                                              delete=False, prefix='superwiser_ctx_') as f:
                f.write(context_text)
                context_file = f.name
            context_lines = len(context_text.splitlines())
        else:
            context_file = '/dev/null'
            context_lines = 0

        mcp_config = {
            "mcpServers": {
                "superwiser": {
                    "command": "python3",
                    "args": [str(SCRIPT_DIR / 'mcp-server.py')]
                }
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                          delete=False, prefix='superwiser_mcp_') as f:
            json.dump(mcp_config, f)
            mcp_config_file = f.name

        prompt = EXTRACTION_PROMPT.format(
            human_input=human_input,
            context_file=context_file,
            context_lines=context_lines
        )

        env = {**os.environ, 'SUPERWISER_DB_PATH': db_path}

        result = subprocess.run(
            ['claude', '-p', prompt, '--output-format', 'json',
             '--max-turns', str(EXTRACTION_MAX_TURNS),
             '--allowedTools', 'Read,mcp__superwiser__search_preferences,mcp__superwiser__get_context',
             '--mcp-config', mcp_config_file],
            capture_output=True, text=True, timeout=EXTRACTION_TIMEOUT, env=env
        )

        if result.returncode != 0:
            print(f"Claude failed: {result.stderr[:500]}", file=sys.stderr)
            return {'skip': True, 'reason': f'Claude failed: {result.stderr[:200]}'}

        parsed = json.loads(result.stdout)

        if parsed.get('type') == 'result':
            if parsed.get('subtype') == 'error_max_turns':
                return {'skip': True, 'reason': 'Max turns exceeded'}
            inner = parsed.get('result', parsed)
            # result might be a string (Claude's text response) - try to parse as JSON
            if isinstance(inner, str):
                try:
                    parsed = json.loads(inner)
                except json.JSONDecodeError:
                    return {'skip': True, 'reason': 'Response not JSON'}
            else:
                parsed = inner

        return parsed
    except Exception as e:
        return {'skip': True, 'reason': str(e)[:50]}
    finally:
        for f in [context_file, mcp_config_file]:
            if f and f != '/dev/null':
                try:
                    Path(f).unlink()
                except Exception:
                    pass


def insert_rule(db, context_id: str, rule_data: dict, item: dict) -> int:
    """Insert a single rule into the rules table."""
    db.execute("""
        INSERT INTO rules (context_id, rule, context, human_input, confidence, source_session, source_position)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        context_id,
        rule_data['rule'],
        rule_data.get('context'),
        item['human_input'],
        rule_data.get('confidence', 'normal'),
        item.get('session_id'),
        item['position']
    ])
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def mark_pending_conflict(db, context_id: str) -> None:
    """Mark a context as having a pending conflict."""
    db.execute("INSERT OR IGNORE INTO pending_conflicts (context_id) VALUES (?)", [context_id])


def insert_embedding(db, rule_id: int, rule_text: str, context_text: str | None) -> None:
    """Generate and insert embedding for a rule. Fails silently."""
    try:
        import numpy as np
        embed_text = f"{rule_text} {context_text or ''}"
        embedding = generate_embedding(embed_text)
        if embedding is not None:
            db.execute("INSERT INTO rules_vec (id, embedding) VALUES (?, ?)",
                       [rule_id, embedding.astype(np.float32).tobytes()])
    except Exception:
        pass


def handle_resolution(db, result: dict, item: dict, vec_loaded: bool) -> tuple[str, str | None]:
    """Handle conflict resolution: delete old group, optionally create new ones."""
    context_id = result["resolve"]

    rule_ids = [r[0] for r in db.execute(
        "SELECT id FROM rules WHERE context_id = ?", [context_id]
    ).fetchall()]

    if rule_ids:
        placeholders = ','.join(['?'] * len(rule_ids))
        db.execute(f"DELETE FROM rules_vec WHERE id IN ({placeholders})", rule_ids)

    db.execute("DELETE FROM rules WHERE context_id = ?", [context_id])
    db.execute("DELETE FROM context_graph WHERE id = ?", [context_id])
    db.execute("DELETE FROM pending_conflicts WHERE context_id = ?", [context_id])

    new_rules = result.get("new_rules", [])
    for rule_data in new_rules:
        new_id = generate_id()
        db.execute("INSERT INTO context_graph (id, tags) VALUES (?, ?)",
                   [new_id, json.dumps(rule_data.get('tags', []))])
        rule_id = insert_rule(db, new_id, rule_data, item)
        if vec_loaded:
            insert_embedding(db, rule_id, rule_data['rule'], rule_data.get('context'))

    action = f"created {len(new_rules)} new rules" if new_rules else "deleted all"
    print(f"Resolution [{context_id}]: {action}")
    return "completed", None


def process_single_rule(db, rule_data: dict, item: dict, vec_loaded: bool) -> str:
    """Process a single rule - create new or add to existing context."""
    rule_text = rule_data['rule']
    tags = rule_data.get('tags', [])
    conflicts_with = rule_data.get('conflicts_with')

    if conflicts_with:
        context_id = conflicts_with
        existing = db.execute("SELECT id FROM context_graph WHERE id = ?", [context_id]).fetchone()

        if existing:
            mark_pending_conflict(db, context_id)
        else:
            context_id = generate_id()
            db.execute("INSERT INTO context_graph (id, tags) VALUES (?, ?)",
                       [context_id, json.dumps(tags)])

        rule_id = insert_rule(db, context_id, rule_data, item)
        print(f"Added conflicting rule to context {context_id}")
    else:
        context_id = generate_id()
        db.execute("INSERT INTO context_graph (id, tags) VALUES (?, ?)",
                   [context_id, json.dumps(tags)])
        rule_id = insert_rule(db, context_id, rule_data, item)
        print(f"Created new context {context_id}: {rule_text[:50]}...")

    if vec_loaded:
        insert_embedding(db, rule_id, rule_text, rule_data.get('context'))

    return context_id


def process_extraction(db, result: dict, item: dict, vec_loaded: bool) -> tuple[str, str | None]:
    """Process extraction result - handles all output formats."""
    if result.get('skip'):
        return "skipped", result.get('reason', 'unknown')

    if 'resolve' in result:
        return handle_resolution(db, result, item, vec_loaded)

    if 'rules' in result:
        rules = result['rules']
    elif 'rule' in result:
        rules = [result]
    else:
        return "skipped", "No rule in result"

    for rule_data in rules:
        if rule_data.get('rule'):
            process_single_rule(db, rule_data, item, vec_loaded)

    return "completed", None


def process_item(db, item: dict, vec_loaded: bool, db_path: str) -> None:
    """Process single queue item."""
    human_input = item['human_input']
    context_blob = item.get('context_blob')

    if len(human_input.strip()) < MIN_PROMPT_LENGTH:
        context_text = decompress_context(context_blob)
        if not context_has_conflict_id(context_text):
            db.execute("UPDATE queue SET status = 'skipped' WHERE id = ?", [item['id']])
            db.commit()
            print("Skipped: short prompt, no conflict in context")
            return

    result = extract_with_claude(human_input, context_blob, db_path)
    status, reason = process_extraction(db, result, item, vec_loaded)

    new_status = 'skipped' if status == "skipped" else 'completed'
    db.execute("UPDATE queue SET status = ? WHERE id = ?", [new_status, item['id']])
    db.commit()

    if status == "skipped":
        print(f"Skipped: {reason}")


def process_project_queue(project_path: str) -> bool:
    db_path = Path(project_path) / '.claude' / 'superwiser' / 'context.db'
    if not db_path.exists():
        return False

    db = get_db(str(db_path), timeout=10.0)
    vec_loaded = load_sqlite_vec(db, ensure_table=True)

    item = db.execute("""
        SELECT id, position, human_input, session_id, context_blob
        FROM queue WHERE status = 'pending'
        ORDER BY created_at LIMIT 1
    """).fetchone()

    if not item:
        db.close()
        return False

    item_dict = {
        'id': item[0],
        'position': item[1],
        'human_input': item[2],
        'session_id': item[3],
        'context_blob': item[4]
    }

    db.execute("UPDATE queue SET status = 'processing' WHERE id = ?", [item[0]])
    db.commit()

    try:
        process_item(db, item_dict, vec_loaded, str(db_path))
        return True
    except Exception as e:
        print(f"Error: {e}")
        db.execute("UPDATE queue SET status = 'failed' WHERE id = ?", [item[0]])
        db.commit()
        return False
    finally:
        db.close()


def self_heal() -> None:
    """Clean up stale entries and reset deferred items if deps available."""
    global last_heal

    if time.time() - last_heal < HEAL_INTERVAL:
        return
    last_heal = time.time()

    projects = get_registered_projects()
    valid = [p for p in projects if Path(p).exists()]
    if len(valid) != len(projects):
        REGISTRY.write_text('\n'.join(valid) + '\n')

    missing = check_dependencies()

    for project in valid:
        db_path = Path(project) / '.claude' / 'superwiser' / 'context.db'
        if not db_path.exists():
            continue
        try:
            db = get_db(str(db_path), timeout=5.0)
            db.execute("""
                UPDATE queue SET status = 'pending'
                WHERE status = 'processing'
                AND created_at < datetime('now', '-10 minutes')
            """)
            if not missing:
                db.execute("UPDATE queue SET status = 'pending' WHERE status = 'deferred'")
            # Clean up old skipped/failed records (older than 7 days)
            db.execute("""
                DELETE FROM queue
                WHERE status IN ('skipped', 'failed')
                AND created_at < datetime('now', '-7 days')
            """)
            db.commit()
            db.close()
        except Exception:
            pass


def defer_pending_items() -> None:
    """Defer all pending items when dependencies are missing."""
    for project in get_registered_projects():
        db_path = Path(project) / '.claude' / 'superwiser' / 'context.db'
        if not db_path.exists():
            continue
        try:
            db = get_db(str(db_path), timeout=5.0)
            db.execute("UPDATE queue SET status = 'deferred' WHERE status = 'pending'")
            db.commit()
            db.close()
        except Exception:
            pass


def worker_loop() -> None:
    print("Global worker started")

    missing = check_dependencies()
    if missing:
        print(f"Warning: Missing dependencies: {missing}")
        print("Items will be deferred until dependencies are installed.")

    while running:
        self_heal()

        if check_dependencies():
            defer_pending_items()
            time.sleep(POLL_INTERVAL)
            continue

        projects = get_registered_projects()
        processed_any = False

        for project in projects:
            if not running:
                break
            if process_project_queue(project):
                processed_any = True
                time.sleep(RATE_LIMIT)

        if not processed_any:
            time.sleep(POLL_INTERVAL)

    print("Worker stopped")


if __name__ == '__main__':
    worker_loop()
