---
description: "Show captured rules"
arguments: "[N] [recent|important|hits] [preferences]"
---

Show captured rules with optional limit, sorting, and filtering.

## Instructions

Call the `list_rules` MCP tool from superwiser.

**Parameters:**
- `limit`: Number of rules to show (default: 10). If user says "top 5" or "list 20", extract the number.
- `sort_by`: Sorting option
- `preferences_only`: If True, only show universal rules without context

**Sorting options** (pass as `sort_by` parameter):
- `recent` (default) - newest rules first
- `important` - highest importance score first
- `hits` - most searched rules first

**Preferences filter:**
If user says "preferences", "preferences only", or "without context", pass `preferences_only=True`.

**Examples:**
- `/superwiser:list` → `list_rules()` (10 recent)
- `/superwiser:list 5` → `list_rules(limit=5)`
- `/superwiser:list important` → `list_rules(sort_by="important")`
- `/superwiser:list 20 hits` → `list_rules(limit=20, sort_by="hits")`
- `/superwiser:list preferences` → `list_rules(preferences_only=True)`
- `/superwiser:list 10 important preferences` → `list_rules(limit=10, sort_by="important", preferences_only=True)`

**Important**: Do not display the raw JSON result from the tool. Present the results in a clean table format with columns for ID, Rule, Context, Confidence, Date, and Score. Also show the total count of rules.
