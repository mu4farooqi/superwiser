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
import threading
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
from db_utils import get_db, load_sqlite_vec, generate_embedding, generate_id, insert_context_with_retry, log_token_usage
from config import (
    EXTRACTION_PROMPT, POLL_INTERVAL, RATE_LIMIT, HEAL_INTERVAL,
    EXTRACTION_TIMEOUT, EXTRACTION_MAX_TURNS,
    DISCOVERY_PROMPT, DISCOVERY_INTERVAL, DISCOVERY_TIMEOUT, DISCOVERY_MODEL,
    DISCOVERY_CONTEXT_FILE,
    get_runtime_config  # Dynamic config loading
)
from paths import SUPERWISER_DIR, REGISTRY
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    for Read, Glob, Grep, and Bash access to registered project directories.
    """
    settings_file = TEMP_CLAUDE / 'settings.json'
    
    allow_rules = []
    for project in project_dirs:
        allow_rules.extend([
            f"Read({project}/**)",
            f"Glob({project}/**)",
            f"Grep({project}/**)",
            f"Bash(cat {project}/**)",
            f"Bash(head {project}/**)",
            f"Bash(tail {project}/**)",
            f"Bash(ls {project}/**)",
            f"Bash(find {project}/**)",
            f"Bash(wc {project}/**)",
            f"Bash(file {project}/**)",
        ])
    
    # Also allow some read-only system commands for discovery
    allow_rules.extend([
        "Bash(which *)",
        "Bash(echo *)",
        "Bash(pwd)",
        "Bash(date)",
    ])
    
    # Deny rules for sensitive files (Bash commands not needed - only allowed ones work)
    deny_rules = [
        "Read(**/.env)", "Read(**/.env.*)",
        "Read(**/.git/**)", "Read(**/.ssh/**)",
        "Read(**/id_rsa*)", "Read(**/id_ed25519*)",
        "Read(**/.aws/**)", "Read(**/credentials)",
        "Read(**/secrets/**)", "Read(**/.netrc)",
        "Glob(**/.git/**)", "Grep(**/.git/**)",
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


# =============================================================================
# PROJECT DISCOVERY
# =============================================================================

# Track discovery in progress to prevent concurrent runs (thread-safe)
_discovery_lock = threading.Lock()
_discovery_in_progress: set[str] = set()


def check_discovery_needed(project_path: str, config: dict = None) -> bool:
    """Check if project needs context discovery (missing or stale).
    
    Returns True if:
    - project-context.md doesn't exist
    - project-context.md is older than discovery_interval days
    """
    if config is None:
        config = get_runtime_config()
    
    interval_days = config.get('discovery_interval', DISCOVERY_INTERVAL)
    context_file = Path(project_path) / DISCOVERY_CONTEXT_FILE
    
    if not context_file.exists():
        return True
    
    try:
        age_days = (time.time() - context_file.stat().st_mtime) / 86400
        return age_days > interval_days
    except Exception:
        return True


def run_project_discovery(project_path: str, config: dict = None) -> bool:
    """Run project discovery to generate project-context.md.

    Uses Claude with Read/Glob/Grep tools to explore the project
    and generate a context document. Thread-safe - prevents concurrent
    discovery for the same project.

    Returns True if discovery succeeded, False otherwise.
    """
    # Thread-safe check and acquire
    with _discovery_lock:
        if project_path in _discovery_in_progress:
            return False
        _discovery_in_progress.add(project_path)

    try:
        if config is None:
            config = get_runtime_config()

        discovery_model = config.get('discovery_model', DISCOVERY_MODEL)
        discovery_timeout = config.get('discovery_timeout', DISCOVERY_TIMEOUT)

        log(f"Starting project discovery for {project_path}")

        # Prepare output path
        context_file = Path(project_path) / DISCOVERY_CONTEXT_FILE
        context_file.parent.mkdir(parents=True, exist_ok=True)

        # Format the discovery prompt
        prompt = DISCOVERY_PROMPT.format(
            project_dir=project_path
        )

        # Run claude -p with read-only tools
        # Run from /tmp/superwiser to avoid triggering project hooks
        # Use --max-turns 50 as safety limit (prevents runaway API costs)
        result = subprocess.run(
            ['claude', '-p', prompt, '--output-format', 'json',
             '--model', discovery_model,
             '--max-turns', '50',
             '--allowedTools', 'Read,Glob,Grep,Bash'],
            capture_output=True, text=True,
            timeout=discovery_timeout,
            cwd=str(TEMP_BASE)
        )

        if result.returncode != 0:
            log(f"Discovery failed for {project_path}: exit code {result.returncode}")
            return False

        # Parse the JSON output and extract markdown content
        try:
            parsed = json.loads(result.stdout)
            content = extract_discovery_content(parsed)

            # Log token usage
            db_path = str(Path(project_path) / '.claude' / 'superwiser' / 'context.db')
            log_token_usage(db_path, 'discovery', parsed, project_path, discovery_model)

            if not content or len(content) < 100:
                log(f"Discovery for {project_path} produced insufficient content")
                return False

            # Write the context file
            context_file.write_text(content)
            log(f"Discovery completed for {project_path}: wrote {len(content)} bytes")
            return True

        except json.JSONDecodeError as e:
            log(f"Discovery for {project_path} returned invalid JSON: {e}")
            return False

    except subprocess.TimeoutExpired:
        log(f"Discovery for {project_path} timed out after {discovery_timeout}s")
        return False
    except Exception as e:
        log(f"Discovery error for {project_path}: {type(e).__name__}: {str(e)[:100]}")
        return False
    finally:
        with _discovery_lock:
            _discovery_in_progress.discard(project_path)


def extract_discovery_content(parsed: dict) -> str:
    """Extract markdown content from Claude's JSON response.

    Handles various response formats and strips markdown code fences.
    """
    if parsed.get('type') == 'result':
        if parsed.get('subtype') == 'error_max_turns':
            return ''
        content = parsed.get('result', '')
    else:
        content = parsed.get('result', str(parsed))

    # Unwrap if still a dict
    if isinstance(content, dict):
        content = content.get('result', str(content))

    if not isinstance(content, str):
        content = str(content)

    # Strip markdown code fences if present
    content = content.strip()
    if content.startswith('```'):
        # Remove opening fence (```markdown or ```)
        content = re.sub(r'^```(?:markdown|md)?\n?', '', content)
        # Remove closing fence
        content = re.sub(r'\n?```$', '', content)

    return content.strip()


def extract_json_from_response(text: str) -> str:
    """Extract JSON from Claude's response, handling various formats.
    
    Claude may return:
    - Pure JSON: {"rule": ...}
    - Markdown wrapped: ```json\n{...}\n```
    - Explanation followed by JSON: "Based on analysis...\n```json\n{...}\n```"
    """
    text = text.strip()
    
    # If it's already pure JSON, return as-is
    if text.startswith('{'):
        return text
    
    # Try to find JSON wrapped in markdown code blocks (anywhere in text)
    match = re.search(r'```(?:json)?\s*\n(\{[\s\S]*?\})\s*```', text)
    if match:
        return match.group(1).strip()
    
    # Try to find a raw JSON object (last one in text, in case of multiple)
    # Use a greedy search for the last complete JSON object
    matches = list(re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text))
    if matches:
        return matches[-1].group(0)
    
    return text


def extract_with_claude(human_input: str, context_blob: bytes | None, db_path: str, project_dir: str, extraction_model: str = None) -> dict:
    """Extract preference/decision using Claude with Read/Glob/Grep tools and MCP search."""
    ensure_temp_dirs()
    context_text = decompress_context(context_blob)
    context_file = None
    mcp_config_file = None

    # Use provided model or get from config
    if extraction_model is None:
        extraction_model = get_runtime_config().get('extraction_model', 'sonnet')

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
             '--model', extraction_model,
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

        # Log token usage from the full response before extracting inner result
        log_token_usage(db_path, 'extraction', parsed, project_dir, extraction_model)

        if parsed.get('type') == 'result':
            if parsed.get('subtype') == 'error_max_turns':
                log("Claude extraction exceeded max turns")
                return {'skip': True, 'reason': 'Max turns exceeded'}
            inner = parsed.get('result', parsed)
            # result might be a string (Claude's text response) - try to parse as JSON
            if isinstance(inner, str):
                try:
                    # Extract JSON from Claude's response (handles markdown, explanations)
                    clean = extract_json_from_response(inner)
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


def delete_context(db, context_id: str) -> None:
    """Delete a context and all its rules (for override mode)."""
    rule_ids = [r[0] for r in db.execute(
        "SELECT id FROM rules WHERE context_id = ?", [context_id]
    ).fetchall()]

    if rule_ids:
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


def handle_resolution(db, result: dict, item: dict) -> tuple[str, str | None]:
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
    created_rule_ids = []
    for rule_data in new_rules:
        new_id = insert_context_with_retry(db, json.dumps(rule_data.get('tags', [])))
        rule_id = insert_rule(db, new_id, rule_data, item)
        created_rule_ids.append(rule_id)
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


def process_single_rule(db, rule_data: dict, item: dict, override_mode: bool = False) -> str:
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
            delete_context(db, conflicts_with)
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

    insert_embedding(db, rule_id, rule_text, rule_data.get('context'))

    return context_id


def process_extraction(db, result: dict, item: dict, override_mode: bool = False) -> tuple[str, str | None]:
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
        return handle_resolution(db, result, item)

    if 'rules' in result:
        rules = result['rules']
    elif 'rule' in result:
        rules = [result]
    else:
        return "skipped", "No rule in result"

    for rule_data in rules:
        if rule_data.get('rule'):
            process_single_rule(db, rule_data, item, override_mode)

    return "completed", None


def process_item(db, item: dict, db_path: str, project_dir: str, config: dict = None) -> None:
    """Process single queue item."""
    if config is None:
        config = get_runtime_config()

    human_input = item['human_input']
    context_blob = item.get('context_blob')
    min_prompt_length = config.get('min_prompt_length', 15)

    if len(human_input.strip()) < min_prompt_length:
        context_text = decompress_context(context_blob)
        if not context_has_conflict_id(context_text):
            reason = "Short prompt, no conflict in context"
            db.execute("UPDATE queue SET status = 'skipped', reason = ? WHERE id = ?",
                       [reason, item['id']])
            db.commit()
            log(f"Skipped: {reason}")
            return

    extraction_model = config.get('extraction_model', 'sonnet')
    result = extract_with_claude(human_input, context_blob, db_path, project_dir, extraction_model)
    override_mode = item.get('override_mode', False)
    status, reason = process_extraction(db, result, item, override_mode)

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


def fetch_pending_items(project_path: str, limit: int) -> list[dict]:
    """Fetch pending items from a project's queue."""
    db_path = Path(project_path) / '.claude' / 'superwiser' / 'context.db'
    if not db_path.exists():
        return []

    try:
        db = get_db(str(db_path), timeout=10.0)
        ensure_queue_columns(db)
        
        items = db.execute("""
            SELECT id, position, human_input, session_id, context_blob, override_mode
            FROM queue WHERE status = 'pending'
            ORDER BY created_at LIMIT ?
        """, [limit]).fetchall()
        
        if not items:
            db.close()
            return []
        
        # Mark items as processing
        ids = [item[0] for item in items]
        placeholders = ','.join(['?'] * len(ids))
        db.execute(f"UPDATE queue SET status = 'processing' WHERE id IN ({placeholders})", ids)
        db.commit()
        db.close()
        
        return [{
            'id': item[0],
            'position': item[1],
            'human_input': item[2],
            'session_id': item[3],
            'context_blob': item[4],
            'override_mode': bool(item[5]) if len(item) > 5 else False,
            'project_path': project_path,
            'db_path': str(db_path)
        } for item in items]
    except Exception as e:
        log(f"Error fetching items from {project_path}: {e}")
        return []


def process_single_item(item: dict, config: dict = None) -> bool:
    """Process a single queue item. Called by thread pool."""
    db_path = item['db_path']
    project_path = item['project_path']

    try:
        db = get_db(db_path, timeout=10.0)
        load_sqlite_vec(db, ensure_table=True)  # Required - raises if not available
        process_item(db, item, db_path, project_path, config)
        db.close()
        return True
    except Exception as e:
        reason = f"Processing error: {type(e).__name__}: {str(e)[:150]}"
        log(f"Error: {reason}")
        try:
            db = get_db(db_path, timeout=5.0)
            db.execute("UPDATE queue SET status = 'failed', reason = ? WHERE id = ?",
                       [reason, item['id']])
            db.commit()
            db.close()
        except Exception:
            pass
        return False


def process_project_queue(project_path: str, config: dict = None) -> bool:
    """Legacy single-item processing. Used when concurrency=1."""
    items = fetch_pending_items(project_path, 1)
    if not items:
        return False
    return process_single_item(items[0], config)


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

    # Track config for change detection
    prev_concurrency = None
    prev_model = None

    while running:
        self_heal()

        if check_dependencies():
            defer_pending_items()
            time.sleep(POLL_INTERVAL)
            continue

        # Read config each cycle so changes take effect dynamically
        config = get_runtime_config()
        extraction_concurrency = config.get('extraction_concurrency', 2)
        extraction_model = config.get('extraction_model', 'sonnet')

        # Log config changes
        if prev_concurrency is not None and extraction_concurrency != prev_concurrency:
            log(f"Config changed: extraction_concurrency {prev_concurrency} -> {extraction_concurrency}")
        if prev_model is not None and extraction_model != prev_model:
            log(f"Config changed: extraction_model {prev_model} -> {extraction_model}")
        prev_concurrency = extraction_concurrency
        prev_model = extraction_model

        projects = get_registered_projects()

        # Update permissions for all registered projects
        setup_project_permissions(projects)

        # Collect pending items from all projects
        all_items = []
        for project in projects:
            if not running:
                break
            items = fetch_pending_items(project, extraction_concurrency)
            all_items.extend(items)

        # Check which projects need discovery (limit to 1 per cycle)
        discovery_project = None
        for project in projects:
            if not running:
                break
            if check_discovery_needed(project, config):
                discovery_project = project
                break  # Only one discovery per cycle

        if not all_items and not discovery_project:
            time.sleep(POLL_INTERVAL)
            continue

        # Process extractions and discovery concurrently in shared pool
        with ThreadPoolExecutor(max_workers=extraction_concurrency) as executor:
            futures = {}

            # Submit extraction items
            for item in all_items:
                futures[executor.submit(process_single_item, item, config)] = ('extraction', item)

            # Submit discovery (uses one worker slot from the pool)
            if discovery_project:
                futures[executor.submit(run_project_discovery, discovery_project, config)] = ('discovery', discovery_project)

            if all_items:
                log(f"Processing {len(all_items)} extractions" +
                    (f" + 1 discovery" if discovery_project else "") +
                    f" with concurrency {extraction_concurrency}")
            elif discovery_project:
                log(f"Running discovery for {discovery_project}")

            for future in as_completed(futures):
                if not running:
                    break
                try:
                    future.result()
                except Exception as e:
                    task_type, task_info = futures[future]
                    log(f"Thread error ({task_type}): {e}")

        time.sleep(RATE_LIMIT)

    log("Worker stopped")


if __name__ == '__main__':
    worker_loop()
