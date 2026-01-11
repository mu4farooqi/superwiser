---
description: "Initialize superwiser (use after mid-session install)"
---

Initialize the superwiser plugin manually. Use this if you installed the plugin mid-session.

## Instructions

Call the `initialize` MCP tool from superwiser.

This will:
- Install any missing dependencies
- Start the background worker if not running
- Register the current project
- Initialize the project database

After running, confirm to the user that superwiser is ready.
