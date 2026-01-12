---
description: "Show rule and token usage statistics"
---

Show statistics about captured rules and API token usage.

## Instructions

Call the `get_stats` MCP tool from superwiser.

Present the results in a readable format:

- Show total rule count
- Display pending conflicts if any (only if non-zero)
- Show pending/processing/failed queue counts (only if non-zero)
- Show token usage: today and last 7 days with daily average
- Show breakdown by operation (discovery vs extraction)

Example output format:
```
Rule Statistics:
- Total rules: 47
- Pending conflicts: 2
- Queue: 3 pending, 1 processing

Token Usage:
Today: 57,730 tokens (8 calls)
  Input: 38,420 | Output: 6,810

Last 7 Days: 312,450 tokens (67 calls)
  Daily avg: 44,635 tokens

By Operation (7d):
  Discovery: 125,000 tokens (12 calls)
  Extraction: 187,450 tokens (55 calls)
```

If there are failed items, show: "- Failed: 2 (check worker logs)"
If token_usage has an error, show: "Token tracking: Not yet initialized"
