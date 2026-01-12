---
name: superwiser
description: Load your coding preferences and project conventions. Use when the user says "use Superwiser", "load preferences", "load my rules", "check coding conventions", or when starting significant work on a feature.
---

# Superwiser - Load Coding Preferences

When this skill is activated, call the `load_preferences` MCP tool to retrieve the user's coding preferences.

## Usage

Call the tool with context about what the user is working on:

```
load_preferences(task_context="<describe what user is working on>")
```

## What Gets Loaded

1. **Global preferences** - Universal rules that apply everywhere (style, patterns, conventions)
2. **Contextual preferences** - Rules specific to the current task (matching semantic similarity)

## After Loading

Present the loaded preferences to the user in a clear format, then apply them throughout the session.
