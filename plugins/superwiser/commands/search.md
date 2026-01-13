---
description: "Search your recorded rules"
arguments: "<query> [limit:N] [preferences]"
---

# Search Rules

Search your recorded coding rules and decisions.

## Usage

```
/superwiser:search <query> [limit:N] [preferences]
```

## Examples

```
/superwiser:search database choice for user sessions
/superwiser:search error handling patterns for REST APIs
/superwiser:search typescript strict mode configuration
/superwiser:search python testing conventions limit:10
/superwiser:search coding style preferences
```

## Instructions

Call the `search_rules` MCP tool from superwiser with the user's search terms.

**Parameters:**
- `context`: What to search for (required). Use a detailed 1-2 sentence description for best results.
- `limit`: Max results to return (default: 5)
- `preferences_only`: If True, only return universal rules without context (default: False)

If the user says "top 10" or "limit:10", pass the number as the `limit` parameter.

If the user says "preferences", "preferences only", or "without context", pass `preferences_only=True`.

**Important**: Do not display the raw tool result. Present the results in a clean, readable format with the rule text, context, confidence level, and ID.
