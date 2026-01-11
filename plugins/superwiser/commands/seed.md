---
description: "Seed rules from historical conversation transcripts"
---

Seed the Superwiser rule database from your past Claude Code conversations.

## Instructions

Call the `seed_from_history` MCP tool from superwiser.

This will:
- Find all conversation transcripts for this project
- Extract user prompts and their context
- Queue them for rule extraction
- Use override mode (newer rules replace conflicting older ones)

The worker processes items in the background, so this may take a while for projects with many conversations.

After calling, tell the user:
- How many prompts were queued
- That processing happens in the background
- They can use `/superwiser:list` to check progress

