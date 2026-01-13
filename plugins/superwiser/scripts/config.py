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

EXTRACTION_PROMPT = '''
<role>
You are a preference extraction specialist for Claude Code sessions. Your task is to identify and extract reusable coding rules from human feedback, corrections, and directions. You analyze conversations to capture developer preferences that can guide future coding decisions.
</role>

<task>
Your goal is to process a human message and determine if it contains an extractable coding preference:
1. First, classify whether the message is extractable or should be skipped
2. If extractable, check for existing similar rules to avoid duplicates
3. Extract the guidance as a reusable rule with appropriate metadata

When the human message or context file references a file (e.g., "check utils.py", "the auth file", "config.ts"), use Glob to find it in the project_directory and Read if relevant.
</task>

<input>
<project_directory>
{project_dir}
</project_directory>

<project_context_reference>
Project context document: {project_dir}/.claude/superwiser/project-context.md
Read this file if you need to understand project architecture, tech stack, conventions, or key abstractions to properly contextualize a rule. Not all extractions need this - simple preferences like "use const not var" don't require project context. If the file doesn't exist, proceed without it.
</project_context_reference>

<human_message>
{human_input}
</human_message>

<context_file>
Path: {context_file}
Lines: {context_lines}
Format: JSONL (most recent at end)
</context_file>
</input>

<classification>
FIRST classify the human message. Default to SKIP unless clearly extractable.
Most human messages are NOT corrective feedback - they are questions, context, or delegation.

NON-EXTRACTABLE (skip immediately):
- Research requests: "can you check", "please read", "look into", "investigate"
  Why: Asking agent to learn something, not correcting agent behavior
- Uncertainty/exploration: "I'm not sure", "I think maybe", "what if we", "what do you think"
  Why: Exploring options without making a decision
- Questions without decisions: "how does X work?", "is it possible to", "what's the best way"
  Why: Asking for information, not establishing a preference
- Documentation/URL references: links to external docs to read
  Why: Delegation of research, not correction
- Code/config shared as context: sharing files for agent to understand
  Why: Providing information, not giving feedback
- Acknowledgments: "thanks", "looks good", "ok go ahead", "got it"
  Why: Approval or acknowledgment, not a new preference
- Explanations requested: "can you explain?", "why did you do X?"
  Why: Seeking understanding, not correcting

EXTRACTABLE (look for these signals):
- Human is CORRECTING or REJECTING something the agent did or proposed
- Human is ESTABLISHING a reusable preference or standard (not just a one-time instruction)
- Human is giving FEEDBACK on the agent's work or proposed solution
- The guidance is ACTIONABLE for future similar situations


DEFAULT: When uncertain, lean towards skipping with a reason.
The cost of a false skip is low (user will express the preference again). The cost of a false extract is higher (adds noise to the rule database).
</classification>

<process>
If the message is extractable, follow these steps:

Step 1 - Read Context:
Read context file from end to understand what agent was doing when human intervened.

Step 2 - Check for Duplicates:
Use search_rules to check for existing similar rules before creating new ones.
- SAME thing (different wording): {{"skip": true, "reason": "Duplicate of [context_id]"}}
- DIFFERENT thing on same topic: {{"rule": "...", "conflicts_with": "context_id", ...}}
- No similar rule: extract normally

Step 3 - Handle Conflict Resolution (if applicable):
If the context contains a conflict ID like [x7k9m2], the user is resolving a conflict.
Call get_rule("x7k9m2") to see the conflicting rules, then interpret user's guidance.
User can: pick one, merge/clarify, or say "neither".
</process>

<output_format>
Respond with valid JSON only. Write your entire response as a single JSON object.

For extracted rules:
- rule: what to do and why
- context: 2-3 sentences describing (1) what feature/task agent was working on, (2) what agent did, (3) why human intervened. Omit only for truly universal rules like "use const not var"
- tags: 2-4 lowercase tags
- confidence: strong/normal/weak/tentative (from language like "NEVER" vs "maybe")
- conflicts_with: context_id if conflicts with existing rule

For skipped messages:
{{"skip": true, "reason": "..."}}

For conflict resolution (both delete the conflict group):
- With new_rules: Delete conflict group AND create these new rules (user merged or clarified)
  {{"resolve": "x7k9m2", "new_rules": [{{"rule": "...", "tags": [...]}}]}}
- Without new_rules: Delete conflict group only (user rejected both rules)
  {{"resolve": "x7k9m2"}}
</output_format>

<examples>
Extraction examples:

Agent used var. Human says "const"
{{"rule": "Use const instead of var - prevents accidental reassignment", "tags": ["javascript", "style"]}}

Agent started setting up MongoDB for user auth. Human says "postgres, need foreign keys"
{{"rule": "Use PostgreSQL for user data - relational data needs foreign key constraints", "context": "Building user authentication system. Agent chose MongoDB for user storage, but the user-role relationships require foreign key constraints that MongoDB doesn't support.", "tags": ["database", "postgresql"]}}

Agent wrapped every fetch in try/catch returning null. Human says "let it crash"
{{"rule": "Let errors propagate - don't swallow with try/catch and null", "context": "Implementing API client layer. Agent wrapped every fetch call in try/catch blocks that swallowed errors and returned null, making debugging impossible when requests failed.", "tags": ["error-handling"]}}

Agent set up Redux for settings page. Human says "just useState"
{{"rule": "Use useState for local state - Redux is overkill for small features", "context": "Adding user preferences page with theme toggle and notification settings. Agent set up Redux store, actions, and reducers for state that only lives on one page.", "tags": ["react", "state"]}}

Human says "In this project, always write tests before implementation"
{{"rule": "Write tests before implementation - TDD approach for this project", "tags": ["testing", "workflow"]}}

Skip examples (equally important - these should NOT produce rules):

Human says "can you check how other plugins handle this?"
{{"skip": true, "reason": "Research request - asking agent to investigate, not correcting behavior"}}

Human says "I'm not sure, maybe we could use redis? What do you think?"
{{"skip": true, "reason": "Exploration - expressing uncertainty and asking for input, not establishing preference"}}

Human says "please read the docs at https://example.com/api"
{{"skip": true, "reason": "Documentation request - asking agent to learn, not correcting a decision"}}

Human shares a config file or code block for context
{{"skip": true, "reason": "Context sharing - providing information, not giving corrective feedback"}}

Human says "looks good, go ahead"
{{"skip": true, "reason": "Acknowledgment - approval to proceed, not establishing a new preference"}}
</examples>

<security>
Describe secrets generically: write "uses OpenAI API" rather than including actual keys.
</security>

<investigation>
Read the context file and use search_rules before deciding. Base your classification on evidence from the conversation, not assumptions about what the human might mean.
</investigation>
'''

# Markers to detect our own extraction prompts (prevents infinite recursion)
# Keep these in sync with EXTRACTION_PROMPT above
EXTRACTION_PROMPT_MARKERS = [
    'preference extraction specialist',
    '<human_message>',
    '<classification>'
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
POLL_INTERVAL = 3

# Number of parallel extraction calls (concurrent claude -p processes)
EXTRACTION_CONCURRENCY = 2

# Model for extraction (sonnet is fast and cost-effective)
# Options: sonnet, opus, haiku, opusplan, or full model name
EXTRACTION_MODEL = "sonnet"

# Delay between processing items (rate limiting, seconds)
RATE_LIMIT = 1

# How often to run self-healing (seconds)
HEAL_INTERVAL = 60

# Timeout for Claude CLI extraction call (seconds)
EXTRACTION_TIMEOUT = 120

# Maximum agentic turns for extraction (each tool call = 1 turn)
EXTRACTION_MAX_TURNS = 10


# =============================================================================
# PROJECT DISCOVERY SETTINGS
# =============================================================================

# How often to regenerate project context (days)
DISCOVERY_INTERVAL = 14

# Timeout for discovery (seconds) - 5 minutes to allow thorough exploration
DISCOVERY_TIMEOUT = 300

# Model for discovery (sonnet is good at exploration)
DISCOVERY_MODEL = "sonnet"

# Output file path (relative to project root)
DISCOVERY_CONTEXT_FILE = ".claude/superwiser/project-context.md"

# Discovery prompt - thorough project exploration
# Follows Claude 4 best practices: explicit instructions, XML tags, positive framing, parallel tools
DISCOVERY_PROMPT = '''
<context>
You are creating a project context document that will help a coding assistant write code that fits this project's patterns and conventions. This document will be referenced during preference extraction to properly contextualize coding rules.
</context>

<project_path>{project_dir}</project_path>

<task>
Explore the project thoroughly using Read, Glob, and Grep tools. Read multiple files in parallel when possible to build context faster.

Follow this exploration sequence:
1. Read dependency files (package.json, requirements.txt, Cargo.toml, go.mod, pyproject.toml) to identify tech stack
2. Read README.md for project purpose (skip CLAUDE.md to avoid duplication)
3. Use Glob to list top-level directories and identify structure
4. Read 2-3 representative source files to identify patterns and conventions
5. Check for build/test configuration (.github/workflows, Makefile, jest.config, pytest.ini)
</task>

<output_format>
Write your response as clean markdown prose. Use the exact structure below, replacing bracketed placeholders with discovered information.

# Project Context: [folder name from {project_dir}]
Generated: [today's date as YYYY-MM-DD]

## Overview
[1-2 sentences describing what this project does]

## Tech Stack
- Language: [primary language and version if detectable]
- Framework: [main framework if any]
- Key dependencies: [5-10 most important dependencies]

## Architecture
[Describe directory structure and what each key directory contains]

## Key Patterns
[Describe common patterns: error handling approach, state management, abstractions used]

## Conventions
[Describe naming conventions (snake_case/camelCase), file organization, code style]

## Build/Test
[List commands to build, run, and test - note any Makefile targets or npm scripts]

## Notes
[Any other context relevant for coding assistance]
</output_format>

<guidelines>
- Keep the document under 1500 words
- Focus on actionable information that helps write code matching project style
- Write in clear prose, using bullet points only for discrete lists
- If you cannot determine something, write "Unknown" rather than guessing
</guidelines>
'''


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

# Hybrid search scoring (two-stage: pre-filter + min-max normalize + combine)
BM25_CANDIDATES = 100        # Candidates to retrieve from BM25 before filtering
MIN_RAW_BM25 = -25.0         # Absolute BM25 floor (scores worse than this filtered out)
MIN_RAW_COSINE = 0.15        # Absolute cosine floor (scores lower than this filtered out)
SEMANTIC_WEIGHT = 0.5        # Alpha for combination (0 = pure BM25, 1 = pure semantic)


# =============================================================================
# PREFERENCE LOADING SETTINGS (for load_preferences tool / skill)
# =============================================================================

# Max global preferences to load (most important rules, ordered by importance_score)
PREFERENCE_GLOBAL_LIMIT = 5

# Max contextual preferences to load (task-specific semantic matches)
PREFERENCE_CONTEXTUAL_LIMIT = 5

# Minimum score threshold for contextual matches (0.0-1.0)
PREFERENCE_MIN_SCORE = 0.3


# =============================================================================
# CONFIGURABLE SETTINGS SYSTEM
# =============================================================================

import json
from pathlib import Path

# Global config location
CONFIG_DIR = Path.home() / '.config' / 'superwiser'
CONFIG_FILE = CONFIG_DIR / 'config.json'

# Metadata for user-configurable settings
# Each entry: (default_value, type, description, validation_info)
CONFIGURABLE_SETTINGS = {
    'extraction_model': {
        'default': 'sonnet',
        'type': 'enum',
        'options': ['sonnet', 'opus', 'haiku'],
        'description': 'Model for preference extraction (sonnet=balanced, opus=quality, haiku=fast/cheap)'
    },
    'extraction_concurrency': {
        'default': 2,
        'type': 'int',
        'min': 1,
        'max': 10,
        'description': 'Number of parallel extraction workers (higher = faster but more API cost)'
    },
    'preference_global_limit': {
        'default': 5,
        'type': 'int',
        'min': 1,
        'max': 20,
        'description': 'Max global preferences to load when using /superwiser skill'
    },
    'preference_contextual_limit': {
        'default': 5,
        'type': 'int',
        'min': 1,
        'max': 20,
        'description': 'Max task-specific preferences to load'
    },
    'preference_min_score': {
        'default': 0.3,
        'type': 'float',
        'min': 0.0,
        'max': 1.0,
        'description': 'Minimum similarity score for contextual preference matches (0.0-1.0)'
    },
    'default_search_limit': {
        'default': 10,
        'type': 'int',
        'min': 1,
        'max': 50,
        'description': 'Default number of results for search queries'
    },
    'context_max_lines': {
        'default': 500,
        'type': 'int',
        'min': 100,
        'max': 2000,
        'description': 'Max transcript lines to capture for context (500 ≈ 50-80 exchanges)'
    },
    'min_prompt_length': {
        'default': 15,
        'type': 'int',
        'min': 5,
        'max': 100,
        'description': 'Minimum prompt length to process (filters trivial inputs)'
    },
    'semantic_weight': {
        'default': 0.5,
        'type': 'float',
        'min': 0.0,
        'max': 1.0,
        'description': 'Hybrid search balance (0.0=pure keyword, 1.0=pure semantic)'
    },
    'discovery_interval': {
        'default': 14,
        'type': 'int',
        'min': 1,
        'max': 90,
        'description': 'Days between project context regeneration (14 = every 2 weeks)'
    },
    'discovery_timeout': {
        'default': 300,
        'type': 'int',
        'min': 60,
        'max': 600,
        'description': 'Timeout in seconds for project discovery (300 = 5 minutes)'
    },
    'discovery_model': {
        'default': 'sonnet',
        'type': 'enum',
        'options': ['sonnet', 'opus', 'haiku'],
        'description': 'Model for project discovery (sonnet=balanced exploration)'
    },
    'dynamic_context_enabled': {
        'default': True,
        'type': 'bool',
        'description': 'Enable periodic reminders to check user preferences via search_rules'
    },
    'dynamic_context_interval': {
        'default': 60,
        'type': 'int',
        'min': 30,
        'max': 900,
        'description': 'Seconds between preference check reminders (60 = 1 minute)'
    }
}


def get_runtime_config() -> dict:
    """Load config from file, merged with defaults.

    Returns dict with all configurable settings, using saved values
    where available and defaults otherwise.
    """
    config = {key: meta['default'] for key, meta in CONFIGURABLE_SETTINGS.items()}

    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            # Only use keys that are valid configurable settings
            for key, value in saved.items():
                if key in CONFIGURABLE_SETTINGS:
                    config[key] = value
        except (json.JSONDecodeError, OSError):
            pass  # Use defaults on error

    return config


def validate_config_value(key: str, value) -> tuple[bool, str]:
    """Validate a config value against its metadata. Returns (is_valid, error_message)."""
    if key not in CONFIGURABLE_SETTINGS:
        return False, f"Unknown setting: {key}. Valid: {', '.join(CONFIGURABLE_SETTINGS.keys())}"

    meta = CONFIGURABLE_SETTINGS[key]

    if meta['type'] == 'enum':
        if value not in meta['options']:
            return False, f"Invalid value for {key}. Must be one of: {', '.join(meta['options'])}"

    elif meta['type'] in ('int', 'float'):
        try:
            num = int(value) if meta['type'] == 'int' else float(value)
            if not (meta['min'] <= num <= meta['max']):
                return False, f"Value for {key} must be between {meta['min']} and {meta['max']}"
        except (ValueError, TypeError):
            return False, f"Value for {key} must be {'an integer' if meta['type'] == 'int' else 'a number'}"

    elif meta['type'] == 'bool':
        if isinstance(value, bool):
            pass  # Already valid
        elif isinstance(value, str):
            if value.lower() not in ('true', '1', 'yes', 'on', 'false', '0', 'no', 'off'):
                return False, f"Value for {key} must be true or false"
        else:
            return False, f"Value for {key} must be true or false"

    return True, ""


def save_config(key: str, value) -> tuple[bool, str]:
    """Save a single config value.

    Returns (success, message).
    """
    # Validate first
    is_valid, error = validate_config_value(key, value)
    if not is_valid:
        return False, error

    # Convert to proper type
    meta = CONFIGURABLE_SETTINGS[key]
    if meta['type'] == 'int':
        value = int(value)
    elif meta['type'] == 'float':
        value = float(value)
    elif meta['type'] == 'bool':
        if isinstance(value, str):
            value = value.lower() in ('true', '1', 'yes', 'on')

    # Load existing config
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Update and save
    config[key] = value

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
        return True, f"Set {key} = {value}"
    except OSError as e:
        return False, f"Failed to save config: {e}"


def get_config_with_metadata() -> dict:
    """Get all config settings with metadata for display."""
    current = get_runtime_config()
    return {
        key: {**meta, 'value': current[key]}
        for key, meta in CONFIGURABLE_SETTINGS.items()
    }
