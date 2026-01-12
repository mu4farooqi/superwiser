---
description: "Change a Superwiser setting (e.g., /superwiser:set-config extraction_model opus)"
---

Change a Superwiser configuration setting.

## Instructions

Parse the user's input to extract the setting name and value, then call `set_config` with those arguments.

Example inputs:
- `/superwiser:set-config extraction_model opus`
- `/superwiser:set-config extraction_concurrency 4`

If no arguments provided, call `get_config` to show available settings.

## Available Settings

| Setting | Values |
|---------|--------|
| extraction_model | sonnet, opus, haiku |
| extraction_concurrency | 1-10 |
| preference_global_limit | 1-20 |
| preference_contextual_limit | 1-20 |
| preference_min_score | 0.0-1.0 |
| default_search_limit | 1-50 |
| context_max_lines | 100-2000 |
| min_prompt_length | 5-100 |
| semantic_weight | 0.0-1.0 |
