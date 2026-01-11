# Superwiser

Superwiser captures your coding preferences and corrections from Claude Code sessions. It builds a searchable knowledge base that Claude can reference in future sessions.

## What It Captures

Only human prompts are analyzed. Claude's responses, tool calls, and file operations are ignored.

**Preferences** - Direct statements about how you want things done:
- "Always use TypeScript, never plain JavaScript"
- "Format dates as ISO 8601"
- "Keep functions under 50 lines"

**Corrections** - When you redirect Claude after seeing its output:
- Claude sets up MongoDB → "Use PostgreSQL—we need foreign key constraints"
- Claude wraps every fetch in try/catch → "Let errors propagate"
- Claude creates a 200-line utility → "Split this into smaller functions"

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
