"""
RAG v2 — drop-in upgrades for the Legal Advisor.

This package contains Sprint 1 + Sprint 2 features that improve retrieval
accuracy and answer quality WITHOUT requiring re-embedding the corpus.

All features are gated behind feature flags in `config.settings` (default
OFF). Production behavior is preserved exactly when flags are off.

Modules:
  query_understanding  — extract dates / amounts / names / filenames / intent
  query_rewriter       — HyDE + multi-query generation (Sonnet 4.6)
  hybrid_search        — BM25 + vector + RRF + filename direct lookup
  temporal             — temporal diversification + recency/authority scoring
  parent_doc           — parent document expansion when chunks cluster
  memory               — conversation summary memory for long chats
  prompts              — enhanced system prompt with self-critique block
  contextual_summary   — per-chunk situating summaries (Anthropic Contextual
                         Retrieval) for the v2 corpus build (Sprint 3 Step 2)

Design rules:
  • Every public function is fail-safe: on any internal error, log and
    return the v1 fallback so production can never go down because of a
    v2 bug.
  • All LLM calls use Sonnet 4.6 minimum (configurable). NEVER Haiku.
  • Pure functions where possible; side effects isolated.
"""

__all__ = [
    "query_understanding",
    "query_rewriter",
    "hybrid_search",
    "temporal",
    "parent_doc",
    "memory",
    "prompts",
    "contextual_summary",
]
