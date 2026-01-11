# Superwiser

**Your steering, remembered.**

Claude Code does the heavy lifting. You steer it—through prompts, corrections, and instructions. Superwiser captures how you steer and builds a searchable knowledge base of your coding preferences.

## What It Captures

Superwiser learns from two types of guidance:

### Preferences
Direct statements about how you want things done:

- *"Always use TypeScript, never plain JavaScript"*
- *"Format dates as ISO 8601"*
- *"Keep functions under 50 lines"*
- *"Use Tailwind for styling, not CSS modules"*

### Corrections
When you redirect Claude after seeing what it did:

- Claude sets up MongoDB → *"Use PostgreSQL—we need foreign key constraints for user data"*
- Claude wraps every fetch in try/catch → *"Let errors propagate, don't swallow them with null returns"*
- Claude adds Redux for a settings page → *"Just useState here, Redux is overkill for local state"*
- Claude creates a 200-line utility → *"Split this into smaller functions, each doing one thing"*

These corrections are gold. They capture not just *what* you prefer, but *when* and *why*—the context that makes rules actionable in future sessions.

## Installation

```bash
# Add the marketplace
/plugin marketplace add github:mu4farooqi/superwiser

# Install the plugin
/plugin install @superwiser/superwiser
```

Superwiser initializes automatically on your first session.

## Usage

Just use Claude Code normally. Your steering is captured in the background.

### Automatic Features

- **Rule Extraction**: Your corrections and instructions are automatically queued and processed
- **Conflict Detection**: When you give contradictory guidance, Superwiser asks which rule to follow
- **Importance Scoring**: Rules are ranked by how often they're referenced and validated
- **Session Awareness**: Claude checks your preferences before making decisions

### Slash Commands

| Command | Description |
|---------|-------------|
| `/superwiser:search <query>` | Search your rules by keyword or context |
| `/superwiser:list` | List recent rules with importance scores |
| `/superwiser:tags` | Show all tags and their counts |
| `/superwiser:stats` | View statistics about captured rules |
| `/superwiser:seed` | Import rules from historical transcripts |
| `/superwiser:delete <id>` | Delete a specific rule |
| `/superwiser:record` | Enable preference recording |
| `/superwiser:record-stop` | Pause preference recording |
| `/superwiser:init` | Manual initialization (if needed) |

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

### Architecture

1. **SessionStart Hook**: Auto-installs dependencies, starts background worker
2. **UserPromptSubmit Hook**: Queues your inputs for extraction, shows pending conflicts
3. **Background Worker**: Processes queue, extracts rules using Claude, generates embeddings
4. **MCP Server**: Exposes search and management tools to Claude
5. **Conflict Resolution**: Detects contradictory rules and prompts you to choose

### Importance Scoring

Rules are automatically scored based on:
- **Search hits (40%)**: How often the rule appears in searches
- **Duplicate validation (30%)**: Prompts skipped as duplicates of this rule
- **Confidence (10%)**: strong > normal > weak > tentative
- **Conflict survival (10%)**: User explicitly chose this rule over alternatives
- **Recency (10%)**: Recently used rules score higher

## Conflict Resolution

When Superwiser detects contradictory rules:

```
⚠️ CONFLICT [x7k9m2]

1. Use Redux for state management
2. Use useState for state management

Pick a number, say 'all'/'both', explain when each applies, or 'skip' to ignore.
```

Your response is captured and the conflict is automatically resolved in the background.

## Seeding from History

Have existing Claude Code conversations? Import your preferences:

```
/superwiser:seed
```

This processes all historical transcripts for the current project, extracting rules with "override mode" (newer rules replace conflicting older ones).

## Data Storage

Rules are stored locally per-project:
```
your-project/
└── .claude/
    └── superwiser/
        └── context.db    # SQLite database with rules, embeddings, queue
```

## Requirements

- Claude Code 1.0.33+
- Python 3.10+

Dependencies are auto-installed on first run:
- `sentence-transformers` (embeddings)
- `sqlite-vec` (vector search)
- `numpy`

## Privacy

- All data stays local in your project directory
- No external API calls except to Claude for extraction
- Secrets are filtered out before processing

## License

MIT License

## Contributing

Issues and pull requests welcome at [github.com/mu4farooqi/superwiser](https://github.com/mu4farooqi/superwiser)

---

**Your steering, remembered.** 🧠
