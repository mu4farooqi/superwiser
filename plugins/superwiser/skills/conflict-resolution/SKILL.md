---
name: conflict-resolution
description: Handle Superwiser rule conflicts when search_rules, list_rules, or load_preferences shows "⚠️ CONFLICT [id]"
---

# Conflict Resolution

When any Superwiser tool returns results containing "⚠️ CONFLICT [id]", you must resolve the conflict before proceeding.

## What Is a Conflict?

A conflict occurs when the user has expressed contradictory preferences at different times. For example:
- "Always use tabs for indentation" vs "Use 2-space indentation"
- "Prefer functional components" vs "Use class components for state"

## How to Resolve

1. **Present the conflict clearly** to the user:
   ```
   SuperWiser Conflict [abc123]:
   I found conflicting preferences:
   1. "Always use tabs for indentation" (from when you were setting up the project)
   2. "Use 2-space indentation" (from your React work last week)
   
   Which should I follow? Or do they apply in different contexts?
   ```

2. **Wait for the user's response**. They can:
   - Pick a number (e.g., "1" or "use the first one")
   - Say "both" or "all" if both rules should be kept
   - Explain when each applies (e.g., "tabs for Python, spaces for JS")
   - Say "skip" to ignore for now

3. **After the user responds, continue with your work**. You do NOT need to:
   - Call any tools to resolve or delete rules
   - Process the response in any special way
   - The conflict resolution is handled automatically in the background

## Important

- Do NOT skip conflicts - they indicate important ambiguity in user preferences
- Do NOT guess which rule to follow - always ask the user
- Do NOT call any MCP tools to "resolve" the conflict - just continue after they respond

