# Superwiser

Superwiser captures your coding preferences and corrections from Claude Code sessions. It stores them in a searchable database that Claude can reference in future sessions.

## Installation

```bash
/plugin marketplace add mu4farooqi/superwiser
/plugin install superwiser
```

Initializes automatically on first session.

## Usage

Use Claude Code normally. Preferences are captured in the background.

**Loading your preferences:**

Say "use Superwiser" or "load my preferences" in your prompt. Claude will load your recorded coding preferences for the current task.

**Automatic features:**
- Rule extraction from your prompts
- Conflict detection for contradictory guidance
- Importance scoring based on usage
- Project context discovery (auto-generated)
- Dynamic context reminders (prompts Claude to check preferences)

### Slash Commands

| Command | Description |
|---------|-------------|
| `/superwiser:search <query> [limit:N] [preferences]` | Search rules |
| `/superwiser:list [N] [recent\|important\|hits] [preferences]` | List rules |
| `/superwiser:tags` | Show all tags |
| `/superwiser:stats` | View statistics and token usage |
| `/superwiser:config` | View configuration |
| `/superwiser:set-config <key> <value>` | Change a setting |
| `/superwiser:seed` | Import from history (interactive) |
| `/superwiser:delete <id>` | Delete a rule |
| `/superwiser:record` | Enable recording |
| `/superwiser:record-stop` | Pause recording |
| `/superwiser:init` | Manual initialization |

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

**Conflict resolution:** When contradictory rules are detected, you'll be prompted to pick one, merge them, or skip. Your response resolves the conflict automatically.

**Importance scoring:** Rules are scored based on search hits (40%), duplicate validation (30%), confidence level (10%), conflict survival (10%), and recency (10%). Higher-scored rules are prioritized when loading preferences.

**Project discovery:** The worker periodically explores your codebase to generate a project context document, helping Claude understand your architecture when extracting rules.

## Configuration

View settings with `/superwiser:config`. Change with `/superwiser:set-config <key> <value>`.

| Setting | Default | Description |
|---------|---------|-------------|
| `dynamic_context_enabled` | true | Periodic reminders to call search_rules |
| `dynamic_context_interval` | 60 | Seconds between reminders |
| `extraction_model` | sonnet | Model for extraction (sonnet/opus/haiku) |
| `extraction_concurrency` | 2 | Parallel workers (1-10) |
| `discovery_interval` | 14 | Days between project context refresh |
| `discovery_model` | sonnet | Model for project discovery |
| `semantic_weight` | 0.5 | Search balance (0=keyword, 1=semantic) |
| `preference_global_limit` | 5 | Max global preferences to load |
| `preference_contextual_limit` | 5 | Max task-specific preferences to load |

Settings are stored in `~/.config/superwiser/config.json` and take effect within seconds.

## Seeding from History

Import preferences from existing Claude Code conversations:

```
/superwiser:seed
```

Shows available transcripts and lets you choose:
- Latest N transcripts
- All after a specific date
- All transcripts

Processing happens in the background.

## Data Storage

```
your-project/
└── .claude/
    └── superwiser/
        ├── context.db          # Personal preferences (add to .gitignore)
        └── project-context.md  # Auto-generated project context (can be shared)
```

All data stays local. Add `context.db` to your project's `.gitignore`:

```gitignore
.claude/superwiser/context.db
```

## Requirements

- Claude Code 1.0.33+
- Python 3.10+

Dependencies auto-install on first run: `sentence-transformers`, `sqlite-vec`, `numpy`

## License

MIT

## Contributing

Issues and PRs welcome at [github.com/mu4farooqi/superwiser](https://github.com/mu4farooqi/superwiser)
