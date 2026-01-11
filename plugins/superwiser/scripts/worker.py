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
import time
from pathlib import Path

# Ensure stdout is unbuffered so logs appear immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def log(msg: str) -> None:
    """Log message with timestamp."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import get_db, load_sqlite_vec, generate_embedding, generate_id, insert_context_with_retry
from config import (
    EXTRACTION_PROMPT, POLL_INTERVAL, RATE_LIMIT, HEAL_INTERVAL,
    EXTRACTION_TIMEOUT, EXTRACTION_MAX_TURNS, MIN_PROMPT_LENGTH
)

SUPERWISER_DIR = Path.home() / '.superwiser'
REGISTRY = SUPERWISER_DIR / 'projects.txt'

# Temp directory structure for worker operations
TEMP_BASE = Path('/tmp/superwiser')
TEMP_CONTEXTS = TEMP_BASE / 'contexts'
TEMP_MCP = TEMP_BASE / 'mcp'
TEMP_FILES = TEMP_BASE / 'files'
TEMP_CLAUDE = TEMP_BASE / '.claude'

running = True
last_heal = 0


def ensure_temp_dirs() -> None:
    """Ensure temp directories exist."""
    TEMP_CONTEXTS.mkdir(parents=True, exist_ok=True)
    TEMP_MCP.mkdir(parents=True, exist_ok=True)
    TEMP_FILES.mkdir(parents=True, exist_ok=True)
    TEMP_CLAUDE.mkdir(parents=True, exist_ok=True)


def setup_project_permissions(project_dirs: list[str]) -> None:
    """Generate permission settings for project file access.
    
    Creates .claude/settings.json in /tmp/superwiser with allow/deny rules
    for Read, Glob, and Grep access to registered project directories.
    """
    settings_file = TEMP_CLAUDE / 'settings.json'
    
    allow_rules = []
    for project in project_dirs:
        allow_rules.extend([
            f"Read({project}/**)",
            f"Glob({project}/**)",
            f"Grep({project}/**)"
        ])
    
    deny_rules = [
        "Read(**/.env)", "Read(**/.env.*)",
        "Read(**/.git/**)", "Read(**/.ssh/**)",
        "Read(**/id_rsa*)", "Read(**/id_ed25519*)",
        "Read(**/.aws/**)", "Read(**/credentials)",
        "Read(**/secrets/**)", "Read(**/.netrc)",
        "Glob(**/.git/**)", "Grep(**/.git/**)"
    ]
    
    settings = {"permissions": {"allow": allow_rules, "deny": deny_rules}}
    settings_file.write_text(json.dumps(settings, indent=2))


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


def strip_markdown_json(text: str) -> str:
    """Strip markdown code block wrapper from JSON if present."""
    text = text.strip()
    # Handle ```json ... ``` or ``` ... ```
    if text.startswith('```'):
        # Remove opening fence (with optional language tag)
        first_newline = text.find('\n')
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Remove closing fence
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3].rstrip()
    return text


def extract_with_claude(human_input: str, context_blob: bytes | None, db_path: str, project_dir: str) -> dict:
    """Extract preference/decision using Claude with Read/Glob/Grep tools and MCP search."""
    ensure_temp_dirs()
    context_text = decompress_context(context_blob)
    context_file = None
    mcp_config_file = None

    try:
        if context_text:
            # Write context to /tmp/superwiser/contexts/
            context_file = str(TEMP_CONTEXTS / f'ctx_{os.getpid()}_{int(time.time() * 1000)}.jsonl')
            Path(context_file).write_text(context_text)
            context_lines = len(context_text.splitlines())
        else:
            context_file = '/dev/null'
            context_lines = 0

        mcp_config = {
            "mcpServers": {
                "superwiser": {
                    "command": sys.executable,
                    "args": [str(SCRIPT_DIR / 'mcp-server.py')]
                }
            }
        }
        # Write MCP config to /tmp/superwiser/mcp/
        mcp_config_file = str(TEMP_MCP / f'config_{os.getpid()}_{int(time.time() * 1000)}.json')
        Path(mcp_config_file).write_text(json.dumps(mcp_config))

        prompt = EXTRACTION_PROMPT.format(
            human_input=human_input,
            context_file=context_file,
            context_lines=context_lines,
            project_dir=project_dir
        )

        env = {**os.environ, 'SUPERWISER_DB_PATH': db_path}

        # Run from /tmp/superwiser to avoid triggering project hooks and auto-cleanup history
        result = subprocess.run(
            ['claude', '-p', prompt, '--output-format', 'json',
             '--max-turns', str(EXTRACTION_MAX_TURNS),
             '--allowedTools', 'Read,Glob,Grep,mcp__superwiser__search_rules,mcp__superwiser__get_rule',
             '--mcp-config', mcp_config_file],
            capture_output=True, text=True, timeout=EXTRACTION_TIMEOUT, env=env,
            cwd=str(TEMP_BASE)
        )

        if result.returncode != 0:
            reason = f"Claude exit code {result.returncode}: {result.stderr[:150]}"
            log(f"Claude command failed: {reason}")
            return {'skip': True, 'reason': reason}

        parsed = json.loads(result.stdout)

        if parsed.get('type') == 'result':
            if parsed.get('subtype') == 'error_max_turns':
                log("Claude extraction exceeded max turns")
                return {'skip': True, 'reason': 'Max turns exceeded'}
            inner = parsed.get('result', parsed)
            # result might be a string (Claude's text response) - try to parse as JSON
            if isinstance(inner, str):
                try:
                    # Strip markdown code blocks if Claude wrapped the JSON
                    clean = strip_markdown_json(inner)
                    parsed = json.loads(clean)
                except json.JSONDecodeError:
                    log(f"Claude returned non-JSON: {inner[:100]}...")
                    return {'skip': True, 'reason': 'Response not JSON'}
            else:
                parsed = inner

        return parsed
    except subprocess.TimeoutExpired:
        reason = f"Claude timed out after {EXTRACTION_TIMEOUT}s"
        log(reason)
        return {'skip': True, 'reason': reason}
    except json.JSONDecodeError as e:
        reason = f"Invalid JSON from Claude: {e}"
        log(reason)
        return {'skip': True, 'reason': reason}
    except Exception as e:
        reason = f"Extraction error: {type(e).__name__}: {str(e)[:100]}"
        log(reason)
        return {'skip': True, 'reason': reason}
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


def delete_context(db, context_id: str, vec_loaded: bool) -> None:
    """Delete a context and all its rules (for override mode)."""
    rule_ids = [r[0] for r in db.execute(
        "SELECT id FROM rules WHERE context_id = ?", [context_id]
    ).fetchall()]

    if rule_ids and vec_loaded:
        placeholders = ','.join(['?'] * len(rule_ids))
        db.execute(f"DELETE FROM rules_vec WHERE id IN ({placeholders})", rule_ids)

    db.execute("DELETE FROM rules WHERE context_id = ?", [context_id])
    db.execute("DELETE FROM context_graph WHERE id = ?", [context_id])
    db.execute("DELETE FROM pending_conflicts WHERE context_id = ?", [context_id])


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

    if rule_ids and vec_loaded:
        placeholders = ','.join(['?'] * len(rule_ids))
        db.execute(f"DELETE FROM rules_vec WHERE id IN ({placeholders})", rule_ids)

    db.execute("DELETE FROM rules WHERE context_id = ?", [context_id])
    db.execute("DELETE FROM context_graph WHERE id = ?", [context_id])
    db.execute("DELETE FROM pending_conflicts WHERE context_id = ?", [context_id])

    new_rules = result.get("new_rules", [])
    created_rule_ids = []
    for rule_data in new_rules:
        new_id = insert_context_with_retry(db, json.dumps(rule_data.get('tags', [])))
        rule_id = insert_rule(db, new_id, rule_data, item)
        created_rule_ids.append(rule_id)
        if vec_loaded:
            insert_embedding(db, rule_id, rule_data['rule'], rule_data.get('context'))

    # Mark new rules as having survived conflict resolution (importance bonus)
    if created_rule_ids:
        try:
            placeholders = ','.join(['?'] * len(created_rule_ids))
            db.execute(
                f"UPDATE rules SET survived_conflict = TRUE WHERE id IN ({placeholders})",
                created_rule_ids
            )
        except Exception:
            pass  # Column may not exist yet

    action = f"created {len(new_rules)} new rules" if new_rules else "deleted all"
    log(f"Resolution [{context_id}]: {action}")
    return "completed", None


def process_single_rule(db, rule_data: dict, item: dict, vec_loaded: bool, override_mode: bool = False) -> str:
    """Process a single rule - create new or add to existing context.
    
    If override_mode=True and there's a conflict, the old rule is deleted
    and a new one is created (last writer wins, no user prompt needed).
    """
    rule_text = rule_data['rule']
    tags = rule_data.get('tags', [])
    conflicts_with = rule_data.get('conflicts_with')

    if conflicts_with:
        existing = db.execute("SELECT id FROM context_graph WHERE id = ?", [conflicts_with]).fetchone()

        if existing and override_mode:
            # Override mode: delete old context/rules, create new
            delete_context(db, conflicts_with, vec_loaded)
            context_id = insert_context_with_retry(db, json.dumps(tags))
            rule_id = insert_rule(db, context_id, rule_data, item)
            log(f"Override: replaced {conflicts_with} with {context_id}: {rule_text[:50]}...")
        elif existing:
            # Normal mode: add to existing context, mark pending conflict
            context_id = conflicts_with
            mark_pending_conflict(db, context_id)
            rule_id = insert_rule(db, context_id, rule_data, item)
            log(f"Added conflicting rule to context {context_id}")
        else:
            # Referenced context doesn't exist, create new one
            context_id = insert_context_with_retry(db, json.dumps(tags))
            rule_id = insert_rule(db, context_id, rule_data, item)
            log(f"Created new context {context_id}: {rule_text[:50]}...")
    else:
        context_id = insert_context_with_retry(db, json.dumps(tags))
        rule_id = insert_rule(db, context_id, rule_data, item)
        log(f"Created new context {context_id}: {rule_text[:50]}...")

    if vec_loaded:
        insert_embedding(db, rule_id, rule_text, rule_data.get('context'))

    return context_id


def process_extraction(db, result: dict, item: dict, vec_loaded: bool, override_mode: bool = False) -> tuple[str, str | None]:
    """Process extraction result - handles all output formats.

    If override_mode=True, conflicts are auto-resolved (last writer wins).
    """
    if result.get('skip'):
        reason = result.get('reason', 'unknown')
        # Track duplicate skip for importance scoring
        # Parse context_id from "Duplicate of [x7k9m2]" or "Similar to x7k9m2"
        match = re.search(r'\[?([a-z0-9]{6})\]?', reason, re.IGNORECASE)
        if match:
            context_id = match.group(1)
            try:
                # Only credit if context has exactly 1 rule (no pending conflict)
                rule_count = db.execute(
                    "SELECT COUNT(*) FROM rules WHERE context_id = ?", [context_id]
                ).fetchone()[0]
                if rule_count == 1:
                    db.execute("""
                        UPDATE rules SET duplicate_skip_count = duplicate_skip_count + 1
                        WHERE context_id = ?
                    """, [context_id])
                    db.commit()
            except Exception:
                pass  # Columns may not exist yet
        return "skipped", reason

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
            process_single_rule(db, rule_data, item, vec_loaded, override_mode)

    return "completed", None


def process_item(db, item: dict, vec_loaded: bool, db_path: str, project_dir: str) -> None:
    """Process single queue item."""
    human_input = item['human_input']
    context_blob = item.get('context_blob')

    if len(human_input.strip()) < MIN_PROMPT_LENGTH:
        context_text = decompress_context(context_blob)
        if not context_has_conflict_id(context_text):
            reason = "Short prompt, no conflict in context"
            db.execute("UPDATE queue SET status = 'skipped', reason = ? WHERE id = ?",
                       [reason, item['id']])
            db.commit()
            log(f"Skipped: {reason}")
            return

    result = extract_with_claude(human_input, context_blob, db_path, project_dir)
    override_mode = item.get('override_mode', False)
    status, reason = process_extraction(db, result, item, vec_loaded, override_mode)

    new_status = 'skipped' if status == "skipped" else 'completed'
    db.execute("UPDATE queue SET status = ?, reason = ? WHERE id = ?",
               [new_status, reason, item['id']])
    db.commit()

    if status == "skipped":
        log(f"Skipped: {reason}")


def ensure_queue_columns(db) -> None:
    """Add missing columns to queue table (migration)."""
    try:
        cursor = db.execute("PRAGMA table_info(queue)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'reason' not in columns:
            db.execute("ALTER TABLE queue ADD COLUMN reason TEXT")
            db.commit()
            log("Migrated queue table: added reason column")
        
        if 'override_mode' not in columns:
            db.execute("ALTER TABLE queue ADD COLUMN override_mode BOOLEAN DEFAULT FALSE")
            db.commit()
            log("Migrated queue table: added override_mode column")
    except Exception:
        pass  # Column might already exist or other error


def process_project_queue(project_path: str) -> bool:
    db_path = Path(project_path) / '.claude' / 'superwiser' / 'context.db'
    if not db_path.exists():
        return False

    db = get_db(str(db_path), timeout=10.0)
    ensure_queue_columns(db)
    vec_loaded = load_sqlite_vec(db, ensure_table=True)

    item = db.execute("""
        SELECT id, position, human_input, session_id, context_blob, override_mode
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
        'context_blob': item[4],
        'override_mode': bool(item[5]) if len(item) > 5 else False
    }

    db.execute("UPDATE queue SET status = 'processing' WHERE id = ?", [item[0]])
    db.commit()

    try:
        process_item(db, item_dict, vec_loaded, str(db_path), project_path)
        return True
    except Exception as e:
        reason = f"Processing error: {type(e).__name__}: {str(e)[:150]}"
        log(f"Error: {reason}")
        db.execute("UPDATE queue SET status = 'failed', reason = ? WHERE id = ?",
                   [reason, item[0]])
        db.commit()
        return False
    finally:
        db.close()


def compute_importance_score(
    search_hits: int,
    duplicate_skip_count: int,
    age_days: float,
    confidence: str,
    survived_conflict: bool,
    days_since_last_hit: float | None
) -> float:
    """Compute composite importance score for a rule.

    Components (simplified):
    - Search relevance (40%): log-scaled total search hit count
    - Duplicate validation (30%): prompts skipped as duplicates
    - Confidence weight (10%): strong > normal > weak > tentative
    - Conflict survival bonus (10%): user explicitly validated this rule
    - Recency/decay (10%): based on last_hit_at and age
    """
    import math

    # Search relevance (40% weight) - log scale prevents outlier dominance
    search_component = min(40, math.log1p(search_hits) * 12)

    # Duplicate skip validation (30% weight)
    duplicate_component = min(30, math.log1p(duplicate_skip_count) * 12)

    # Confidence weight (10% weight)
    confidence_weights = {'strong': 10, 'normal': 5, 'weak': 2, 'tentative': 0}
    confidence_component = confidence_weights.get(confidence, 5)

    # Conflict survival bonus (10% weight)
    conflict_component = 10 if survived_conflict else 0

    # Recency component (10% weight) - combines age and last hit
    # New rules get bonus, old unused rules get penalty
    recency_component = max(0, 1 - age_days / 90) * 5  # Up to 5 pts for new rules
    if days_since_last_hit is not None:
        if days_since_last_hit < 30:
            recency_component += 5  # Recently used bonus
        elif days_since_last_hit > 60:
            recency_component -= min(5, (days_since_last_hit - 60) / 30)  # Decay penalty

    return max(0, search_component + duplicate_component + confidence_component +
               conflict_component + recency_component)


def update_importance_scores(db) -> int:
    """Recompute importance scores for all rules. Returns count updated."""
    try:
        rules = db.execute("""
            SELECT id,
                   COALESCE(search_hit_count, 0),
                   COALESCE(duplicate_skip_count, 0),
                   julianday('now') - julianday(created_at) as age_days,
                   confidence,
                   COALESCE(survived_conflict, FALSE),
                   CASE WHEN last_hit_at IS NOT NULL
                        THEN julianday('now') - julianday(last_hit_at)
                        ELSE NULL END as days_since_last_hit
            FROM rules
        """).fetchall()
    except Exception:
        return 0  # Columns may not exist yet

    if not rules:
        return 0

    # Batch compute and update scores
    updates = []
    for rule in rules:
        score = compute_importance_score(
            search_hits=rule[1],
            duplicate_skip_count=rule[2],
            age_days=rule[3] or 0,
            confidence=rule[4] or 'normal',
            survived_conflict=bool(rule[5]),
            days_since_last_hit=rule[6]
        )
        updates.append((score, rule[0]))

    db.executemany("UPDATE rules SET importance_score = ? WHERE id = ?", updates)
    db.commit()
    return len(updates)


def cleanup_temp_files(max_age_seconds: int = 3600) -> None:
    """Remove old temp files from /tmp/superwiser (older than max_age_seconds)."""
    now = time.time()
    for temp_dir in [TEMP_CONTEXTS, TEMP_MCP, TEMP_FILES]:
        if not temp_dir.exists():
            continue
        try:
            for f in temp_dir.iterdir():
                if f.is_file() and (now - f.stat().st_mtime) > max_age_seconds:
                    f.unlink(missing_ok=True)
        except Exception:
            pass


def self_heal() -> None:
    """Clean up stale entries, temp files, update importance scores, and reset deferred items."""
    global last_heal

    if time.time() - last_heal < HEAL_INTERVAL:
        return
    last_heal = time.time()

    # Clean up old temp files (older than 1 hour)
    cleanup_temp_files(max_age_seconds=3600)

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

            # Update importance scores (silent - no logging needed)
            update_importance_scores(db)

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
    log("Global worker started")
    ensure_temp_dirs()

    missing = check_dependencies()
    if missing:
        log(f"Warning: Missing dependencies: {missing}")
        log("Items will be deferred until dependencies are installed.")

    while running:
        self_heal()

        if check_dependencies():
            defer_pending_items()
            time.sleep(POLL_INTERVAL)
            continue

        projects = get_registered_projects()
        
        # Update permissions for all registered projects
        setup_project_permissions(projects)
        
        processed_any = False

        for project in projects:
            if not running:
                break
            if process_project_queue(project):
                processed_any = True
                time.sleep(RATE_LIMIT)

        if not processed_any:
            time.sleep(POLL_INTERVAL)

    log("Worker stopped")


if __name__ == '__main__':
    worker_loop()
