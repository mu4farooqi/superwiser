---
description: "Search your recorded rules"
arguments: "<query> [limit:N]"
---

# Search Rules

Search your recorded coding rules and decisions.

## Usage

```
/superwiser:search <query> [limit:N]
```

## Examples

```
/superwiser:search typescript
/superwiser:search testing python
/superwiser:search database postgresql
/superwiser:search error handling limit:10
```

## Instructions

Call the `search_rules` MCP tool from superwiser with the user's query.

**Parameters:**
- `query`: The search query (required)
- `context`: Additional context for better semantic matching (optional)
- `limit`: Max results to return (default: 5)

If the user says "top 10" or "limit:10", pass the number as the `limit` parameter.

**Important**: Do not display the raw tool result. Present the results in a clean, readable format with the rule text, context, confidence level, and ID.
