---
description: "Star Superwiser on GitHub"
arguments: "[never]"
---

# Star Superwiser

Star the Superwiser repository on GitHub to show your support.

## Arguments

- No argument: Star the repo
- `never`: Stop showing star reminders

## Instructions

### If argument is "never"

Update `~/.config/superwiser/onboarding.json` to set `star_never_ask` to `true`:

```python
import json
from pathlib import Path

config_file = Path.home() / '.config' / 'superwiser' / 'onboarding.json'
config = json.loads(config_file.read_text()) if config_file.exists() else {}
config['star_never_ask'] = True
config_file.parent.mkdir(parents=True, exist_ok=True)
config_file.write_text(json.dumps(config, indent=2))
```

Respond: "No problem! Star reminders are now disabled."

### If no argument (star the repo)

1. Check if `gh` CLI is installed and authenticated:
   ```bash
   gh auth status
   ```

2. If authenticated, check if already starred:
   ```bash
   gh api user/starred/mu4farooqi/superwiser
   ```
   - Exit code 0 = already starred
   - Exit code 1 = not starred

3. If not starred, star the repo:
   ```bash
   gh api -X PUT user/starred/mu4farooqi/superwiser
   ```

4. Report result to user:
   - **Success**: "Thanks for starring Superwiser! Your support helps the project grow."
   - **Already starred**: "You've already starred Superwiser. Thanks for your support!"
   - **No gh CLI**: "Please visit https://github.com/mu4farooqi/superwiser to star manually."
   - **Not authenticated**: "GitHub CLI not authenticated. Run `gh auth login` first, or visit https://github.com/mu4farooqi/superwiser"
