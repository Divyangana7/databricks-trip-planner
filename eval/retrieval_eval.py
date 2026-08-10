"""
Retrieval-quality evaluation.

Measures recall@k of the pgvector index against an exact (brute-force) top-k
baseline, over a set of natural-language probe queries. This is the experiment
behind the ivfflat -> HNSW switch: ivfflat with lists=100 under the default
probes=1 scanned ~1% of vectors and returned too few rows; HNSW recovers recall.

recall@k = |index_topk ∩ exact_topk| / k, averaged over probes.

Usage (notebook):
    from eval import retrieval_eval
    report = retrieval_eval.evaluate(k=5)
    display(pd.DataFrame(report["per_query"]))
    print(report["summary"])
"""

import logging

import lakebase
import models

logger = logging.getLogger("trip-planner.eval.retrieval")

PROBES = [
    "rainy day indoor activity, art and history",
    "challenging mountain hike with great views",
    "relaxing spot to unwind after a long day",
    "family-friendly walk that is easy and short",
    "cultural landmark to learn local history",
    "water activity on a lake or river",
    "food and local cuisine experience",
    "photography spot with scenery",
]


def _exact_topk(qvec, k):
    """Brute-force top-k with the index disabled for this query (ground truth)."""
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            # Force a sequential scan so we get the true nearest neighbours.
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute("SET LOCAL enable_bitmapscan = off")
            cur.execute(
                """
                SELECT activity_id
                FROM activities
                WHERE requirements_embedding IS NOT NULL
                ORDER BY requirements_embedding <=> %s::vector
                LIMIT %s
                """,
                (qvec, k),
            )
            return [r["activity_id"] for r in cur.fetchall()]


def _index_topk(qvec, k):
    """Top-k using whatever vector index is installed (index scan allowed)."""
    rows = lakebase.run_query(
        """
        SELECT activity_id
        FROM activities
        WHERE requirements_embedding IS NOT NULL
        ORDER BY requirements_embedding <=> %s::vector
        LIMIT %s
        """,
        (qvec, k),
    )
    return [r["activity_id"] for r in rows]


def current_index() -> str:
    rows = lakebase.run_query(
        "SELECT indexdef FROM pg_indexes WHERE tablename='activities' AND indexname='idx_act_req_vec'")
    if not rows:
        return "none (exact scan)"
    d = rows[0]["indexdef"].lower()
    return "hnsw" if "hnsw" in d else ("ivfflat" if "ivfflat" in d else "other")


def evaluate(k: int = 5, probes: list[str] | None = None) -> dict:
    probes = probes or PROBES
    per_query = []
    recalls = []
    returned_counts = []
    for q in probes:
        qvec = models.embed_text(q)
        exact = _exact_topk(qvec, k)
        idx = _index_topk(qvec, k)
        hits = len(set(exact) & set(idx))
        recall = hits / max(len(exact), 1)
        recalls.append(recall)
        returned_counts.append(len(idx))
        per_query.append({"query": q, "exact_k": len(exact), "index_returned": len(idx),
                          "overlap": hits, "recall_at_k": round(recall, 3)})
    mean_recall = round(sum(recalls) / len(recalls), 3)
    mean_returned = round(sum(returned_counts) / len(returned_counts), 2)
    return {
        "index": current_index(),
        "k": k,
        "summary": {"mean_recall_at_k": mean_recall,
                    "mean_rows_returned": mean_returned,
                    "n_probes": len(probes)},
        "per_query": per_query,
    }
