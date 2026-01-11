---
description: "Show captured rules"
---

Show captured rules with optional sorting.

## Instructions

Call the `list_rules` MCP tool from superwiser.

**Sorting options** (pass as `sort_by` parameter):
- `recent` (default) - newest rules first
- `important` - highest importance score first
- `hits` - most searched rules first

If the user asks for "important rules" or "top rules", use `sort_by="important"`.

**Important**: Do not display the raw JSON result from the tool. Present the results in a clean table format with columns for ID, Rule, Context, Confidence, Date, and Score. Also show the total count of rules.

