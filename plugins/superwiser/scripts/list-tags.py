#!/usr/bin/env python3
"""List all tags with counts."""
import sqlite3
import json
import sys
from collections import Counter

db_path = sys.argv[1] if len(sys.argv) > 1 else '.claude/superwiser/context.db'

try:
    db = sqlite3.connect(db_path)
    tags = Counter()

    for (tag_json,) in db.execute("SELECT tags FROM context_graph WHERE tags IS NOT NULL"):
        try:
            for tag in json.loads(tag_json):
                tags[tag] += 1
        except Exception:
            pass

    db.close()

    if not tags:
        print("No tags found yet.")
    else:
        print("Tags:")
        for tag, count in tags.most_common(30):
            print(f"  {tag}: {count}")

except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)


