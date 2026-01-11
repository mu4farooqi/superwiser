---
description: "Search your recorded rules"
---

# Search Rules

Search your recorded coding rules and decisions.

## Usage

```
/superwiser:search <query> [#tag1 #tag2]
```

## Examples

```
/superwiser:search typescript
/superwiser:search testing #python
/superwiser:search database #postgresql #backend
/superwiser:search error handling
```

## Instructions

Call the `search_rules` MCP tool from superwiser with the user's query.

If the user includes hashtags, extract them and pass as the `tags` parameter (comma-separated, without #).

**Important**: Do not display the raw tool result. Present the results in a clean, readable format with the rule text, context, confidence level, and ID.
