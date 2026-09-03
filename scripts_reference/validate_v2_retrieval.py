"""
Sprint 3 Step 2 — quick smoke-validation of the Option B v2 retriever.

Runs a small fixed set of representative queries through the v2 hybrid
retriever and prints the top-K results, showing:

  • where each chunk came from (sha256, primary date, primary filename)
  • how many occurrences each chunk has (Option B fan-out)
  • the rerank score and the cluster key the diversifier saw

This is NOT a benchmark — just a fast sanity test that the new corpus
is queryable and that the fan-out is being surfaced. Run AFTER:

  1. Collapse + build complete
  2. Atlas Vector Search index is READY on email_chunks_v2

Usage:
  python scripts/validate_v2_retrieval.py
  python scripts/validate_v2_retrieval.py --query "your question"
  python scripts/validate_v2_retrieval.py --top-k 10
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings


# A representative slice of query intents — covers timeline, person
# narrowing, document lookup, fact lookup, and fan-out provenance.
_DEFAULT_QUERIES = [
    # (label, query)
    ("timeline",   "Summarize the events of the Mango Tree settlement over time"),
    ("person",     "What did Mike Wheuer send about escrow?"),
    ("document",   "What does the Global Stipulation say about appeals?"),
    ("money",      "Find every reference to $450,000"),
    ("provenance", "When was the 9019 motion first sent and by whom?"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", help="Run one custom query instead of the default set")
    ap.add_argument("--top-k", type=int, default=5,
                    help="How many results to print per query")
    args = ap.parse_args()

    s = Settings.load()
    if not s.rag_v2_enabled:
        print("WARN: RAG_V2_ENABLED is false. v2 retriever will return nothing.")
        return 2

    # Lazy import — avoids loading the v2 module when v2 is off.
    from api.rag_singleton import get_retriever
    retriever = get_retriever()

    queries = (
        [("custom", args.query)]
        if args.query else _DEFAULT_QUERIES
    )
    print(f"v2 corpus: {s.rag_v2_chunks_collection}  "
          f"vector index: {s.rag_v2_vector_index_name}")
    print(f"flags: hybrid={s.rag_v2_hybrid_search} hyde={s.rag_v2_hyde} "
          f"multi_query={s.rag_v2_multi_query} adaptive_k={s.rag_v2_adaptive_k}")
    print("=" * 80)

    for label, q in queries:
        print(f"\n[{label}] {q}")
        print("-" * 80)
        try:
            chunks = retriever.retrieve(q)
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            continue
        if not chunks:
            print("  (no results)")
            continue
        for i, c in enumerate(chunks[: args.top_k], start=1):
            try:
                dt = c.date.strftime("%Y-%m-%d") if c.date else "—"
            except AttributeError:
                dt = str(c.date)
            try:
                latest = c.latest_date.strftime("%Y-%m-%d") if c.latest_date else "—"
            except AttributeError:
                latest = str(c.latest_date)
            n_occ = len(c.occurrences or [])
            fname = c.filename or "(email)"
            rscore = f"{c.rerank_score:.3f}" if c.rerank_score is not None else "—"
            print(f"  #{i:>2}  rerank={rscore}  "
                  f"src={c.source_type}  occ={n_occ}  "
                  f"primary={dt}  latest={latest}")
            print(f"        file={fname!r}")
            if c.subject:
                print(f"        subj={c.subject!r}")
            # Surface the OTHER occurrences (max 2) for fan-out visibility
            if n_occ > 1:
                extras = (c.occurrences or [])[1:3]
                for e in extras:
                    e_dt = (e.get("date").strftime("%Y-%m-%d")
                            if e.get("date") else "—")
                    print(f"        ALSO: {e_dt}  from={e.get('from_email')}  "
                          f"subj={(e.get('subject') or '')[:60]!r}")
                if n_occ > 3:
                    print(f"        ... and {n_occ - 3} more")
            # First 200 chars of body for context
            body_snip = (c.body or c.text or "").strip().replace("\n", " ")[:200]
            print(f"        body: {body_snip}…")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
