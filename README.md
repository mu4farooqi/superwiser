# Superwiser

**Your steering, remembered.**

Claude Code does the heavy lifting. You steer it—through prompts, corrections, and instructions. Superwiser captures how you steer.

## What It Captures

Every time you guide Claude Code:

- "Use PostgreSQL, not MySQL"
- "Make sure errors are logged to Sentry"
- "Keep functions under 50 lines"
- "Always add input validation"

These instructions reveal your preferences. Superwiser builds a context graph of them.

## Install

```bash
claude plugin add /path/to/superwiser
```

That's it. Everything runs automatically.

## Usage

Just use Claude Code normally. Your steering is captured in the background.

Claude can look up your preferences anytime. Or search manually:

```
/search database
/search error handling
/search testing
```

## How It Works

1. **SessionStart**: Dependencies auto-install, worker starts in background
2. **During session**: You work normally with Claude Code
3. **SessionEnd**: Your inputs are queued for processing
4. **Background**: Worker extracts preferences, generates embeddings
5. **Anytime**: Claude can search your preferences via MCP tool

## Commands

| Command | What it does |
|---------|--------------|
| `/search <query>` | Find your past instructions |
| `/context:stats` | See what's been captured |

---

MIT License
