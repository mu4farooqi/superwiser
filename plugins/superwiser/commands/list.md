---
description: "Show captured rules"
arguments: "[N] [recent|important|hits]"
---

Show captured rules with optional limit and sorting.

## Instructions

Call the `list_rules` MCP tool from superwiser.

**Parameters:**
- `limit`: Number of rules to show (default: 10). If user says "top 5" or "list 20", extract the number.
- `sort_by`: Sorting option

**Sorting options** (pass as `sort_by` parameter):
- `recent` (default) - newest rules first
- `important` - highest importance score first
- `hits` - most searched rules first

**Examples:**
- `/superwiser:list` → `list_rules()` (10 recent)
- `/superwiser:list 5` → `list_rules(limit=5)`
- `/superwiser:list important` → `list_rules(sort_by="important")`
- `/superwiser:list 20 hits` → `list_rules(limit=20, sort_by="hits")`

**Important**: Do not display the raw JSON result from the tool. Present the results in a clean table format with columns for ID, Rule, Context, Confidence, Date, and Score. Also show the total count of rules.

