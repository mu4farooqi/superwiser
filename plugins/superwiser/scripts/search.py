#!/usr/bin/env python3
"""Search rules using BM25 + hybrid scoring with pre-filtering (v2 schema)."""

import sqlite3
import json
import sys
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_utils import load_sqlite_vec, get_model
from config import (
    DEFAULT_SEARCH_LIMIT, BM25_CANDIDATES,
    MIN_RAW_BM25, MIN_RAW_COSINE, SEMANTIC_WEIGHT
)


def escape_fts5(query: str) -> str:
    """Escape query for FTS5 MATCH."""
    words = [f'"{re.sub(r"[^\w\s-]", "", w)}"' for w in query.split() if w.strip()]
    return ' OR '.join(words) if words else '""'


def minmax_normalize(scores: dict) -> dict:
    """Normalize scores to [0, 1] using min-max within the set."""
    if not scores:
        return {}
    min_s, max_s = min(scores.values()), max(scores.values())
    if max_s == min_s:
        return {k: 1.0 for k in scores}
    return {k: (v - min_s) / (max_s - min_s) for k, v in scores.items()}


def search(db_path: str, query: str, top_k: int = DEFAULT_SEARCH_LIMIT) -> list:
    """Two-stage hybrid search: BM25 retrieve + pre-filter + min-max normalize + combine."""
    import numpy as np
    from db_utils import db_context

    with db_context(db_path, timeout=10.0) as db:
        use_embeddings = load_sqlite_vec(db)

        # Stage 1a: BM25 retrieval (weights: rule=1.0, context=0.5, human_input=0.25)
        try:
            bm25_results = db.execute(f"""
                SELECT rowid, bm25(rules_fts, 1.0, 0.5, 0.25) as score
                FROM rules_fts WHERE rules_fts MATCH ?
                ORDER BY score LIMIT {BM25_CANDIDATES}
            """, [escape_fts5(query)]).fetchall()
        except sqlite3.OperationalError:
            return []

        if not bm25_results:
            return []

        # Store actual BM25 scores (not just ranks)
        bm25_scores = {row[0]: row[1] for row in bm25_results}
        candidate_ids = list(bm25_scores.keys())

        # Stage 1b: Compute semantic scores for BM25 candidates
        semantic_scores = {}
        if use_embeddings and candidate_ids:
            try:
                query_emb = get_model().encode(query, normalize_embeddings=True)
                placeholders = ','.join(['?'] * len(candidate_ids))
                embeddings = db.execute(
                    f"SELECT id, embedding FROM rules_vec WHERE id IN ({placeholders})",
                    candidate_ids
                ).fetchall()

                for row_id, emb_blob in embeddings:
                    if emb_blob:
                        semantic_scores[row_id] = float(np.dot(query_emb, np.frombuffer(emb_blob, dtype=np.float32)))
            except Exception:
                pass

        # Stage 2: Pre-filter with absolute thresholds (OR logic)
        filtered_ids = [
            rid for rid in candidate_ids
            if bm25_scores[rid] > MIN_RAW_BM25 or semantic_scores.get(rid, 0) > MIN_RAW_COSINE
        ]

        if not filtered_ids:
            return []

        # Stage 3: Min-max normalize both signals within survivors
        filtered_bm25 = {rid: bm25_scores[rid] for rid in filtered_ids}
        filtered_semantic = {rid: semantic_scores.get(rid, 0) for rid in filtered_ids}

        norm_bm25 = minmax_normalize(filtered_bm25)
        norm_semantic = minmax_normalize(filtered_semantic) if semantic_scores else {}

        # Stage 4: Weighted combination
        hybrid_scores = {}
        for rid in filtered_ids:
            if norm_semantic:
                hybrid_scores[rid] = (1 - SEMANTIC_WEIGHT) * norm_bm25[rid] + SEMANTIC_WEIGHT * norm_semantic.get(rid, 0)
            else:
                # No semantic embeddings available, use BM25 only
                hybrid_scores[rid] = norm_bm25[rid]

        # Sort by hybrid score and apply limit
        final_ranked = sorted(hybrid_scores.items(), key=lambda x: -x[1])[:top_k]

        # Get final IDs (after threshold AND limit)
        top_ids = [rid for rid, _ in final_ranked]
        if not top_ids:
            return []

        # Track search hits ONLY for final results (after threshold + limit)
        try:
            placeholders = ','.join(['?'] * len(top_ids))
            db.execute(
                f"UPDATE rules SET search_hit_count = search_hit_count + 1, last_hit_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                top_ids
            )
            db.commit()
        except sqlite3.OperationalError:
            pass  # Columns may not exist yet (pre-migration)

        # Fetch full results
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
                    'hybrid_score': score
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
