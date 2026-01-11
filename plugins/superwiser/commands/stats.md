---
description: "Show rule importance statistics"
---

Show statistics about captured rules and which ones are most important.

## Instructions

Call the `get_stats` MCP tool from superwiser.

**Important**: Present the results in a readable format:
- Show total rule count, search hit count, and duplicate skip count
- Display pending conflicts if any
- Show pending/processing/failed analysis counts (only if non-zero)
- Show the top 5 most important rules with their scores
- Briefly explain what makes a rule "important":
  - High search hits = frequently referenced
  - High duplicate skips = user validated by repeating similar prompts
  - Conflict survivor = explicitly kept during conflict resolution

Example output format:
```
Rule Statistics:
- Total rules: 47
- Search hits: 234
- Duplicate validations: 12
- Conflict survivors: 5
- Analyzing: 3 pending, 1 processing

Most Important Rules:
1. [x7k9m2] (score: 78.3) Use const for loop variables
   - 45 search hits, 3 duplicate validations
2. [a1b2c3] (score: 52.1) ...
```

If there are failed items, show: "- Failed analysis: 2 (check worker logs)"
