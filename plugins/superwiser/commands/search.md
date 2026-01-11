---
description: "Search your recorded rules"
arguments: "<query> [#tag1 #tag2] [limit:N]"
---

# Search Rules

Search your recorded coding rules and decisions.

## Usage

```
/superwiser:search <query> [#tag1 #tag2] [limit:N]
```

## Examples

```
/superwiser:search typescript
/superwiser:search testing #python
/superwiser:search database #postgresql #backend limit:10
/superwiser:search error handling
```

## Instructions

Call the `search_rules` MCP tool from superwiser with the user's query.

**Parameters:**
- `query`: The search query (required)
- `tags`: Comma-separated tags to filter by (extracted from #hashtags)
- `limit`: Max results to return (default: 5)

If the user includes hashtags, extract them and pass as the `tags` parameter (comma-separated, without #).

If the user says "top 10" or "limit:10", pass the number as the `limit` parameter.

**Important**: Do not display the raw tool result. Present the results in a clean, readable format with the rule text, context, confidence level, and ID.
