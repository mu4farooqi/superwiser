---
description: "Seed rules from historical conversation transcripts"
---

Seed the Superwiser rule database from your past Claude Code conversations.

## Instructions

**Step 1: Show transcript preview**

Call `seed_preview` MCP tool from superwiser.

Present the results to the user like this:
```
Found [count] transcripts spanning [oldest_date] to [newest_date].

How would you like to seed?
1. Latest N transcripts (e.g., "latest 5")
2. All after a date (e.g., "after 2025-06-01")
3. All [count] transcripts
```

**Step 2: Wait for user choice**

The user will respond with one of:
- "latest N" (e.g., "latest 5", "latest 10")
- "after YYYY-MM-DD" (e.g., "after 2025-06-01")
- "all"

**Step 3: Call seed_from_history with their filter**

Based on the user's choice:
- For "latest N": `seed_from_history(latest_n=N)`
- For "after DATE": `seed_from_history(after_date="YYYY-MM-DD")`
- For "all": `seed_from_history()` (no arguments)

**Step 4: Report results**

Tell the user:
- How many prompts were queued
- That processing happens in the background
- They can use `/superwiser:list` to check progress
