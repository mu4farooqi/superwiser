#!/usr/bin/env python3
"""Search rules using BM25 + RRF re-ranking (v2 schema)."""

import sqlite3
import json
import sys
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import load_sqlite_vec, get_model
from config import DEFAULT_SEARCH_LIMIT, BM25_RETRIEVAL_LIMIT, RRF_K


def escape_fts5(query: str) -> str:
    """Escape query for FTS5 MATCH."""
    words = [f'"{re.sub(r"[^\w\s-]", "", w)}"' for w in query.split() if w.strip()]
    return ' OR '.join(words) if words else '""'


def search(db_path: str, query: str, top_k: int = DEFAULT_SEARCH_LIMIT) -> list:
    """BM25 retrieve + RRF re-rank on rules table."""
    import numpy as np
    from db_utils import db_context

    with db_context(db_path, timeout=10.0) as db:
        use_embeddings = load_sqlite_vec(db)

        # BM25 retrieval on rules_fts (weights: rule=1.0, context=0.5, human_input=0.25)
        try:
            bm25_results = db.execute(f"""
                SELECT rowid, bm25(rules_fts, 1.0, 0.5, 0.25) as score
                FROM rules_fts WHERE rules_fts MATCH ?
                ORDER BY score LIMIT {BM25_RETRIEVAL_LIMIT}
            """, [escape_fts5(query)]).fetchall()
        except sqlite3.OperationalError:
            return []

        if not bm25_results:
            return []

        bm25_ranks = {row[0]: rank for rank, row in enumerate(bm25_results)}
        candidate_ids = list(bm25_ranks.keys())

        # Semantic re-ranking (if available)
        semantic_ranks = {}
        if use_embeddings and candidate_ids:
            try:
                query_emb = get_model().encode(query, normalize_embeddings=True)
                placeholders = ','.join(['?'] * len(candidate_ids))
                embeddings = db.execute(
                    f"SELECT id, embedding FROM rules_vec WHERE id IN ({placeholders})",
                    candidate_ids
                ).fetchall()

                scores = {}
                for row_id, emb_blob in embeddings:
                    if emb_blob:
                        scores[row_id] = float(np.dot(query_emb, np.frombuffer(emb_blob, dtype=np.float32)))

                if scores:
                    semantic_ranks = {rid: rank for rank, (rid, _) in enumerate(sorted(scores.items(), key=lambda x: -x[1]))}
            except Exception:
                pass

        # RRF combination
        rrf_scores = {}
        for rid in candidate_ids:
            rrf_scores[rid] = 1/(RRF_K + bm25_ranks.get(rid, 999))
            if semantic_ranks:
                rrf_scores[rid] += 1/(RRF_K + semantic_ranks.get(rid, 999))

        final_ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])[:top_k]

        # Fetch results from rules table with context_id
        top_ids = [rid for rid, _ in final_ranked]
        if not top_ids:
            return []

        # Track search hits for importance scoring
        try:
            placeholders = ','.join(['?'] * len(top_ids))
            db.execute(
                f"UPDATE rules SET search_hit_count = search_hit_count + 1, last_hit_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                top_ids
            )
            db.commit()
        except sqlite3.OperationalError:
            pass  # Columns may not exist yet (pre-migration)

        placeholders = ','.join(['?'] * len(top_ids))
        rows = {r[0]: r for r in db.execute(
            f"""SELECT r.id, r.context_id, r.rule, r.context, r.human_input,
                       r.confidence, r.created_at, cg.tags,
                       COALESCE(r.importance_score, 0) as importance_score
                FROM rules r
                JOIN context_graph cg ON r.context_id = cg.id
                WHERE r.id IN ({placeholders})""",
            top_ids
        ).fetchall()}

        # Check for conflicts (contexts with >1 rule)
        context_ids = list(set(r[1] for r in rows.values()))
        conflict_contexts = set()
        if context_ids:
            placeholders = ','.join(['?'] * len(context_ids))
            conflicts = db.execute(f"""
                SELECT context_id FROM rules
                WHERE context_id IN ({placeholders})
                GROUP BY context_id HAVING COUNT(*) > 1
            """, context_ids).fetchall()
            conflict_contexts = {c[0] for c in conflicts}

        results = []
        for rid, score in final_ranked:
            if rid in rows:
                r = rows[rid]
                results.append({
                    'id': r[0],
                    'context_id': r[1],
                    'rule': r[2],
                    'context': r[3],
                    'human_input': r[4],
                    'confidence': r[5],
                    'created_at': r[6],
                    'tags': json.loads(r[7]) if r[7] else [],
                    'importance_score': r[8] if len(r) > 8 else 0,
                    'has_conflict': r[1] in conflict_contexts,
                    'rrf_score': score
                })

        return results


def format_results(results: list) -> str:
    """Format search results for display."""
    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        context_id = r['context_id']
        confidence = r.get('confidence', 'normal')
        importance = r.get('importance_score', 0)

        # Importance tier indicators
        if importance >= 70:
            importance_marker = " ***"
        elif importance >= 40:
            importance_marker = " **"
        elif importance >= 20:
            importance_marker = " *"
        else:
            importance_marker = ""

        conf_marker = "" if confidence == "normal" else f" [{confidence}]"

        if r.get('has_conflict'):
            # Prominent conflict marker with ID
            lines.append(f"\n{i}. CONFLICT [{context_id}]{conf_marker}{importance_marker} {r['rule']}")
        else:
            lines.append(f"\n{i}. [{context_id}]{conf_marker}{importance_marker} {r['rule']}")

        if r.get('context'):
            lines.append(f"   Context: {r['context']}")
        if r.get('tags'):
            lines.append(f"   Tags: {', '.join(r['tags'])}")
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Search the context graph')
    parser.add_argument('query', nargs='?', help='Search query')
    parser.add_argument('--db', default='.claude/superwiser/context.db')
    parser.add_argument('--top-k', '-n', type=int, default=10)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if not args.query:
        parser.print_help()
        sys.exit(1)

    if not Path(args.db).exists():
        print(f"Database not found: {args.db}")
        sys.exit(1)

    results = search(args.db, args.query, args.top_k)
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_results(results))
