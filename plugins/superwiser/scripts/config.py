#!/usr/bin/env python3
"""
Superwiser configuration - prompts and settings.

Edit this file to customize extraction behavior.
"""

# =============================================================================
# CONTEXT CAPTURE SETTINGS
# =============================================================================

# Maximum transcript lines to capture (from end, before human message)
# Each line is a JSONL entry (user message, assistant response, or tool call)
# 500 lines ≈ 50-80 conversation exchanges
CONTEXT_MAX_LINES = 500


# =============================================================================
# EXTRACTION PROMPT
# =============================================================================

EXTRACTION_PROMPT = '''You extract reusable rules from human feedback, corrections and directions in Claude Code sessions.

CRITICAL: Your response must be ONLY valid JSON. No explanation, no markdown, no text before or after. Just the JSON object.

<project_directory>
{project_dir}
</project_directory>

<human_message>
{human_input}
</human_message>

<context_file>
Path: {context_file}
Lines: {context_lines}
Format: JSONL (most recent at end)
</context_file>

When the user references files (e.g., "check utils.py", "as shown in config.ts"),
search for them in the project directory above using Glob, then Read.
The context file above is a temp copy - read it directly at the given path.

<task>
1. Read context file from end to understand what agent was doing when human intervened
2. Use search_rules to check for existing similar rules
3. Extract guidance as reusable rule, or handle conflict resolution

IMPORTANT: When writing the "context" field, include:
- What feature/task the agent was working on
- What specific action/approach the agent took
- Why the human intervened (what was wrong with the agent's approach)
</task>

<similarity_check>
Before extracting, search for similar existing rules.

- SAME thing (different wording): {{"skip": true, "reason": "Duplicate of [context_id]"}}
- DIFFERENT thing on same topic: {{"rule": "...", "conflicts_with": "context_id", ...}}
- No similar rule: extract normally
</similarity_check>

<resolution>
If the context contains a conflict ID like [x7k9m2], the user is resolving a conflict.
Call get_rule("x7k9m2") to see the conflicting rules, then interpret user's guidance.

User can do one of the following but not limited to:
- Pick one
- Merge/clarify
- Say "neither"

Output format - resolution deletes the conflict group and optionally creates new rules:
{{"resolve": "x7k9m2", "new_rules": [{{"rule": "...", "tags": [...]}}]}}
{{"resolve": "x7k9m2"}}
</resolution>

<output_format>
Return JSON:
- rule: what to do and why
- context: 2-3 sentences describing (1) what feature/task agent was working on, (2) what agent did, (3) why human intervened. Omit only for truly universal rules like "use const not var"
- tags: 2-4 lowercase tags
- confidence: strong/normal/weak/tentative (from language like "NEVER" vs "maybe")
- conflicts_with: context_id if conflicts with existing rule

Examples:

Agent used var. Human says "const"
{{"rule": "Use const instead of var - prevents accidental reassignment", "tags": ["javascript", "style"]}}

Agent started setting up MongoDB for user auth. Human says "postgres, need foreign keys"
{{"rule": "Use PostgreSQL for user data - relational data needs foreign key constraints", "context": "Building user authentication system. Agent chose MongoDB for user storage, but the user-role relationships require foreign key constraints that MongoDB doesn't support.", "tags": ["database", "postgresql"]}}

Agent wrapped every fetch in try/catch returning null. Human says "let it crash"
{{"rule": "Let errors propagate - don't swallow with try/catch and null", "context": "Implementing API client layer. Agent wrapped every fetch call in try/catch blocks that swallowed errors and returned null, making debugging impossible when requests failed.", "tags": ["error-handling"]}}

Agent set up Redux for settings page. Human says "just useState"
{{"rule": "Use useState for local state - Redux is overkill for small features", "context": "Adding user preferences page with theme toggle and notification settings. Agent set up Redux store, actions, and reducers for state that only lives on one page.", "tags": ["react", "state"]}}
</output_format>

<skip_conditions>
Skip if not actionable guidance:
- "thanks, looks good" -> acknowledgment
- "what do you think about X?" -> question without decision
- "ok go ahead" -> approval to proceed, not endorsing a decision
- "can you explain?" -> asking for explanation

{{"skip": true, "reason": "..."}}
</skip_conditions>

<security>
Never include secrets. Describe generically: "uses OpenAI API" not the actual key.
</security>

REMINDER: Output ONLY the JSON object. No other text.
'''

# Markers to detect our own extraction prompts (prevents infinite recursion)
# Keep these in sync with EXTRACTION_PROMPT above
EXTRACTION_PROMPT_MARKERS = [
    'You extract reusable rules from human feedback',
    '<human_message>',
    '<similarity_check>'
]


def is_extraction_prompt(text: str) -> bool:
    """Check if text is our own extraction prompt (prevents infinite recursion).
    
    Uses markers from EXTRACTION_PROMPT to detect recursive prompts.
    """
    return all(marker in text for marker in EXTRACTION_PROMPT_MARKERS)


# =============================================================================
# WORKER SETTINGS
# =============================================================================

# How often the worker polls for new items (seconds)
POLL_INTERVAL = 5

# Number of parallel extraction calls (concurrent claude -p processes)
EXTRACTION_CONCURRENCY = 2

# Model for extraction (sonnet is fast and cost-effective)
# Options: sonnet, opus, haiku, opusplan, or full model name
EXTRACTION_MODEL = "sonnet"

# Delay between processing items (rate limiting, seconds)
RATE_LIMIT = 2

# How often to run self-healing (seconds)
HEAL_INTERVAL = 60

# Timeout for Claude CLI extraction call (seconds)
EXTRACTION_TIMEOUT = 120

# Maximum agentic turns for extraction (each tool call = 1 turn)
EXTRACTION_MAX_TURNS = 10


# =============================================================================
# QUEUE SETTINGS
# =============================================================================

# Minimum prompt length to queue (filters trivial inputs)
MIN_PROMPT_LENGTH = 15


# =============================================================================
# SEARCH SETTINGS
# =============================================================================

# Default number of results to return
DEFAULT_SEARCH_LIMIT = 10

# BM25 retrieval limit before re-ranking
BM25_RETRIEVAL_LIMIT = 500

# RRF constant (standard is 60)
RRF_K = 60
