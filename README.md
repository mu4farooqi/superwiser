# Superwiser

Superwiser captures your coding preferences and corrections from Claude Code sessions. It stores them in a searchable database that Claude can reference in future sessions.

## Why Superwiser?

AI already knows how to code. It can architect systems, write tests, and follow best practices. The remaining gap is your steering—the corrections and preferences that make the output match what you actually want.

Superwiser records that steering. Not another memory layer.

### Not a Typical Memory Layer

Most memory plugins store everything: conversation history, file contents, tool outputs. This burns tokens and often duplicates what the AI already handles.

Superwiser is different:

- **Only human prompts** are analyzed, not entire conversations
- **Sonnet extracts rules** in the background (fast, cheap)
- **Conflicts are detected** when you give contradictory guidance across sessions
- **Local storage** per project, no external services

### What We Track vs. Ignore

| Tracked | Ignored |
|---------|---------|
| "Use PostgreSQL for user data" | Claude reading files |
| "Split this into smaller functions" | Tool calls and outputs |
| "Always add tests for new features" | Claude's responses |
| Your correction after Claude's mistake | File edits and diffs |

### Lightweight

- **Extraction**: Sonnet processes prompts in background (fast, cheap)
- **Storage**: Local SQLite per project
- **Processing**: Background worker, never blocks your session
- **Tokens**: Only your prompts analyzed, not entire conversations

### CLAUDE.md vs Superwiser

Simple, static rules belong in your [CLAUDE.md](https://docs.anthropic.com/en/docs/claude-code/memory) file:
- "Use TypeScript"
- "Run prettier before committing"

Superwiser captures what emerges during sessions—contextual corrections with the reasoning behind them. These are harder to anticipate and write upfront.

## What It Captures

**Corrections** - When you redirect Claude after seeing its output:

> Claude creates an auth system with JWT stored in localStorage.
>
> *"Don't store tokens in localStorage—use httpOnly cookies. We had XSS issues before and this is a security requirement from the last audit."*

> Claude adds a try/catch around every database call, returning null on failure.
>
> *"Let database errors propagate. Swallowing them makes debugging impossible. Only catch at the API boundary where we can return proper error responses."*

> Claude builds a 300-line React component with inline state management.
>
> *"This needs to be split up. Extract the form validation into a custom hook, move the API calls to a separate service, and keep the component focused on rendering."*

> Claude sets up a new endpoint by copying an existing one and modifying it.
>
> *"Use the base controller class instead of copying. We have shared middleware for auth and validation that copied endpoints miss."*

## Installation

```bash
/plugin marketplace add mu4farooqi/superwiser
/plugin install superwiser
```

Initializes automatically on first session.

## Usage

Use Claude Code normally. Preferences are captured in the background.

**Automatic features:**
- Rule extraction from your prompts
- Conflict detection for contradictory guidance
- Importance scoring based on usage
- Claude checks your preferences before making decisions

### Slash Commands

| Command | Description |
|---------|-------------|
| `/superwiser:search <query> [#tags] [limit:N]` | Search rules by keyword, filter by tags |
| `/superwiser:list [N] [recent\|important\|hits]` | List rules with optional limit and sort |
| `/superwiser:tags` | Show all tags |
| `/superwiser:stats` | View statistics |
| `/superwiser:seed` | Import from historical transcripts |
| `/superwiser:delete <id>` | Delete a rule |
| `/superwiser:record` | Enable recording |
| `/superwiser:record-stop` | Pause recording |
| `/superwiser:init` | Manual initialization |

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Claude Session                      │
├─────────────────────────────────────────────────────────────┤
│  You: "Use PostgreSQL, not MySQL for this project"          │
│                          ↓                                   │
│  [UserPromptSubmit Hook] Queues prompt for extraction        │
│                          ↓                                   │
│  [Background Worker] Extracts rule with Claude               │
│                          ↓                                   │
│  [SQLite + Vector DB] Stores rule with embeddings            │
│                          ↓                                   │
│  [Future Sessions] Claude searches rules before decisions    │
└─────────────────────────────────────────────────────────────┘
```

### Conflict Resolution

When contradictory rules are detected:

```
⚠️ CONFLICT [x7k9m2]

1. Use Redux for state management
2. Use useState for state management

Pick a number, say 'both', explain when each applies, or 'skip'.
```

Your response resolves the conflict automatically.

### Importance Scoring

Rules are scored based on:
- Search hits (40%)
- Duplicate validation (30%)
- Confidence level (10%)
- Conflict survival (10%)
- Recency (10%)

## Seeding from History

Import preferences from existing Claude Code conversations:

```
/superwiser:seed
```

Processes historical transcripts for the current project.

## Data Storage

```
your-project/
└── .claude/
    └── superwiser/
        └── context.db
```

All data stays local.

## Requirements

- Claude Code 1.0.33+
- Python 3.10+

Dependencies auto-install on first run: `sentence-transformers`, `sqlite-vec`, `numpy`

## License

MIT

## Contributing

Issues and PRs welcome at [github.com/mu4farooqi/superwiser](https://github.com/mu4farooqi/superwiser)
