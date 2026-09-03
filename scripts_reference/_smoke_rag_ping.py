"""
End-to-end smoke test of the RAG stack:

  1. Voyage  embed_query    (voyage-3)
  2. Atlas   $vectorSearch  (RETRIEVAL_TOP_K candidates)
  3. Voyage  rerank          (rerank-2.5 → RERANK_TOP_K)
  4. Claude  messages.create (claude-sonnet-4-6) with citations

Prints the full pipeline numbers + a small answer so we can confirm
every layer is alive before going live in chat.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.embedder import VoyageEmbedder
from src.rag.reranker import VoyageReranker
from src.rag.retriever import Retriever
from src.rag.chat import LegalAdvisorChat

QUESTION = "What court hearings or filings happened in Dec 2025?"


def main() -> int:
    s = Settings.load()
    mongo = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    mongo.ping()

    embedder = VoyageEmbedder(api_key=s.voyage_api_key, model=s.embedding_model)
    reranker = VoyageReranker(api_key=s.voyage_api_key, model=s.rerank_model)

    retr = Retriever(
        mongo=mongo,
        embedder=embedder,
        reranker=reranker,
        vector_index_name=s.vector_index_name,
        retrieval_top_k=s.retrieval_top_k,
        rerank_top_k=s.rerank_top_k,
    )

    print("=" * 70)
    print("RAG SMOKE TEST")
    print("=" * 70)
    print(f"Embedding model:   {s.embedding_model}  (dim={s.embedding_dim})")
    print(f"Reranker model:    {s.rerank_model}")
    print(f"Claude model:      {s.claude_model}")
    print(f"Vector index:      {s.vector_index_name}")
    print(f"Initial top-K:     {s.retrieval_top_k}")
    print(f"After rerank:      {s.rerank_top_k}")
    print(f"Question:          {QUESTION}")
    print("-" * 70)

    chunks = retr.retrieve(QUESTION)
    print(f"\nRetrieved {len(chunks)} chunks after rerank.\n")
    for i, c in enumerate(chunks, 1):
        date_str = c.date.strftime("%Y-%m-%d") if c.date else "no-date"
        kind = c.source_type
        fn = c.filename or c.subject or "(email body)"
        score = f"{c.rerank_score:.3f}" if c.rerank_score is not None else "-"
        print(f"  [{i}] {date_str}  {kind:10s} rr={score}  {fn[:60]}")

    print("-" * 70)
    print("Calling Claude...")
    chat = LegalAdvisorChat(
        anthropic_api_key=s.anthropic_api_key,
        retriever=retr,
        model=s.claude_model,
    )
    turn = chat.ask(QUESTION)
    print()
    print("=" * 70)
    print("ANSWER")
    print("=" * 70)
    # Force ASCII-safe console output (Windows cp1252 chokes on some chars).
    safe_answer = turn.answer.encode("ascii", errors="replace").decode("ascii")
    print(safe_answer)
    print()
    print(f"Chunks used in prompt:  {len(turn.chunks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
