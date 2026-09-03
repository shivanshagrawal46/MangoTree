"""
Live query runner — for development / verification only.

Hits the real RAG stack (MongoDB Atlas, Voyage, Anthropic). Use sparingly:
each invocation costs a few cents.

Usage:
  python scripts/run_query.py "your question here"
"""
from __future__ import annotations

import io
import sys
import time
from datetime import datetime, timezone

# Force UTF-8 stdout on Windows so Claude's smart quotes / em-dashes print cleanly.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Make repo root importable when running from anywhere.
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force the env file to load before anything imports settings.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)

from api.rag_singleton import get_settings, make_chat  # noqa: E402
from src.rag.v2.query_understanding import extract_signals  # noqa: E402


def _hr(label: str = "") -> None:
    bar = "-" * 78
    if label:
        print(f"\n{bar}\n  {label}\n{bar}")
    else:
        print(bar)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/run_query.py \"your question\"")
        return 2
    question = " ".join(sys.argv[1:])

    s = get_settings()

    _hr("CONFIG (Sprint 2.5)")
    print(f"  RAG_V2_ENABLED            = {s.rag_v2_enabled}")
    print(f"  RAG_V2_HYBRID_SEARCH      = {s.rag_v2_hybrid_search}")
    print(f"  RAG_V2_FILENAME_LOOKUP    = {s.rag_v2_filename_lookup}")
    print(f"  RAG_V2_HYDE / MULTIQ      = {s.rag_v2_hyde} / {s.rag_v2_multi_query}")
    print(f"  RAG_V2_DATE_FILTERS       = {s.rag_v2_date_filters}")
    print(f"  RAG_V2_RESCORING          = {s.rag_v2_rescoring}")
    print(f"  RAG_V2_TEMPORAL_DIVERSITY = {s.rag_v2_temporal_diversity}")
    print(f"  RAG_V2_PARENT_DOC         = {s.rag_v2_parent_doc}")
    print(f"  RAG_V2_ADAPTIVE_K         = {s.rag_v2_adaptive_k}")
    print(f"  RAG_V2_ENHANCED_PROMPT    = {s.rag_v2_enhanced_prompt}")
    print(f"  RAG_V2_SUMMARY_MEMORY     = {s.rag_v2_summary_memory}")
    print(f"  RAG_V2_FULL_DOC_MODE      = {s.rag_v2_full_doc_mode}")
    print(f"  RAG_V2_INTERLEAVED_ORDER  = {s.rag_v2_interleaved_ordering}")
    print(f"  RAG_V2_XML_SOURCES        = {s.rag_v2_xml_sources}")
    print(f"  Adaptive K (sim/cmp/comp) = {s.rag_v2_adaptive_k_simple}/"
          f"{s.rag_v2_adaptive_k_complex}/{s.rag_v2_adaptive_k_comprehensive}")
    print(f"  RRF fused cap             = {s.rag_v2_rrf_fused_cap}")
    print(f"  Vector top-K              = {s.rag_v2_vector_top_k}")
    print(f"  BM25 top-K                = {s.rag_v2_bm25_top_k}")
    print(f"  Total evidence cap (tok)  = {s.rag_v2_total_evidence_cap_tokens}")
    print(f"  Claude model              = {s.claude_model}")
    print(f"  Query rewriter model      = {s.rag_v2_query_rewriter_model}")
    print(f"  Embedding model           = {s.embedding_model}")
    print(f"  Vector index              = {s.vector_index_name}")

    _hr("QUESTION")
    print(f"  {question}")

    _hr("EXTRACTED SIGNALS (v2 query understanding)")
    sigs = extract_signals(question)
    print(f"  intent           = {sigs.primary_intent()}")
    print(f"  is_complex       = {sigs.is_complex()}")
    print(f"  is_comprehensive = {sigs.is_comprehensive()}")
    print(f"  money_terms     = {sigs.money_terms}")
    print(f"  dates           = {[d.date().isoformat() for d in sigs.explicit_dates]}")
    print(f"  date_from..to   = {sigs.date_from} .. {sigs.date_to}")
    print(f"  filenames       = {sigs.filenames}")
    print(f"  quoted/proper   = {sigs.quoted_strings}")
    print(f"  emails          = {sigs.emails}")
    print(f"  case_numbers    = {sigs.case_numbers}")
    print(f"  docket_numbers  = {sigs.docket_numbers}")
    print(f"  boost_terms     = {sigs.keyword_boost_terms}")

    _hr(f"RUNNING (MongoDB + Voyage + Anthropic {s.claude_model})")
    chat = make_chat()
    t0 = time.perf_counter()
    turn = chat.ask(question)
    elapsed = time.perf_counter() - t0

    _hr("RETRIEVED CHUNKS")
    if not turn.chunks:
        print("  (none)")
    else:
        for i, c in enumerate(turn.chunks, start=1):
            date_s = c.date.isoformat() if isinstance(c.date, datetime) else "-"
            fname = c.filename or "(email body)"
            sender = c.from_email or "-"
            score_s = (f"{c.rerank_score:.3f}" if c.rerank_score is not None else "-")
            print(
                f"  [{i:>2}] {date_s}  {sender:<35}  {fname[:50]}  rerank={score_s}"
            )

    _hr("ANSWER")
    print(turn.answer)

    _hr("STATS")
    print(f"  elapsed                   = {elapsed:.2f}s")
    print(f"  chunks retrieved          = {len(turn.chunks)}")
    print(f"  answer length             = {len(turn.answer)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
