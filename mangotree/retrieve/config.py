"""Retrieval knobs, in one place.

Every value here shapes what a search returns. They are module constants rather
than scattered defaults so that a change is a one-line diff with a visible
history, and so a question like "why did that document not come back" has one
file to open. Comment sits beside value; there is no second copy to drift.

Starting values are the reference project's where one existed. Evaluation is
deferred, so there is no measured basis yet for moving them; when a gold set
exists, this is the file it tunes.
"""
from __future__ import annotations

from mangotree.config.models import Seat, model_for

# =============================================================================
# Scope — what a chat is allowed to see
# =============================================================================

#: Opus 5 sorted the confident-common store into two kinds. ``portfolio`` items
#: (master guaranties, entity documents, legal invoices, lender-wide notices,
#: and every unnamed invoice or wire that *might* concern a registered property)
#: are always searched in property mode as a lower-weighted extra list, labelled
#: in the answer. ``business`` items (other-property deals, marketing, calendar
#: traffic) are global-chat only unless this is flipped.
INCLUDE_BUSINESS_COMMON_IN_PROPERTY_CHAT = False

#: Relative weight of the common-store list against the property's own lists in
#: fusion. Below 1.0 so a portfolio-level document surfaces when it is genuinely
#: the best answer and loses ties otherwise. The reranker judges on content, so
#: this breaks ties; it never suppresses.
COMMON_LIST_WEIGHT = 0.6

#: The 365 unplaced items (``needs_review``, no property yet) stay searchable in
#: every property chat until a human decides them. Same weight as common.
UNPLACED_LIST_WEIGHT = 0.6

#: Labels the reranker and the answer model see beside each passage, so the AI
#: knows what it is reading and the answer can say so.
LABEL_PROPERTY = "property file"
LABEL_PORTFOLIO = "common store — portfolio-level, may bear on this property"
LABEL_UNPLACED = "unplaced — pending human review"
LABEL_LOW_CONFIDENCE = "low-confidence placement"
LABEL_BUSINESS = "common store — business / other property"

# =============================================================================
# Channels and fusion
# =============================================================================

#: Candidates pulled per ranked list before fusion. Vector lists are per query
#: embedding (original + HyDE + rewrites); BM25 lists are per phrasing.
VECTOR_TOP_K = 150
VECTOR_NUM_CANDIDATES = 1500
BM25_TOP_K = 100
PHRASE_TOP_K = 60
SUBSTRING_TOP_K = 60
FILENAME_TOP_K = 40
GRAPH_TOP_K = 80
TIMELINE_TOP_K = 80
DOCLEVEL_TOP_K = 30      # documents, each then contributes its chunks
DOCLEVEL_CHUNKS_PER_DOC = 6

#: Query-side expansion. Alternative phrasings and one hypothetical answer, each
#: embedded and searched as its own list.
MAX_ALT_QUERIES = 3
HYDE_ENABLED = True

#: RRF damping (the paper's value) and the size of the fused candidate pool that
#: proceeds to rescoring.
RRF_K = 60
FUSED_CAP = 200

#: Per-channel weights in fusion. 1.0 is neutral. Exact-match channels are
#: trusted a little more because when they fire they are rarely wrong; the
#: document-level channel a little less because it is coarse.
CHANNEL_WEIGHTS = {
    "vector": 1.0,
    "vector_hyde": 0.9,
    "vector_alt": 0.8,
    "bm25": 1.0,
    "bm25_alt": 0.8,
    "phrase": 1.3,
    "substring": 1.3,
    "filename": 1.5,
    "graph": 0.9,
    "timeline": 0.9,
    "doclevel": 0.7,
    # No separate "question" channel: by decision (2026-09-03) the questions each
    # chunk answers are folded into its one embedding, so the vector lists above
    # already carry the question signal once the night job has run.
}

# =============================================================================
# Rescoring and diversification
# =============================================================================

#: Recency: a document from the last N days gets up to this multiplier, decaying
#: linearly to 1.0 at the horizon. Lending files are read chronologically; the
#: most recent draw request is usually the one being asked about.
RECENCY_HORIZON_DAYS = 120
RECENCY_MAX_BOOST = 1.15

#: Authority tiers by doc_class. An executed instrument outranks a draft, which
#: outranks correspondence about it. Unknown classes are neutral.
AUTHORITY_TIERS = {
    # executed / recorded instruments
    "deed_of_trust": 1.20, "note": 1.20, "assignment": 1.20, "guaranty": 1.20,
    "title_policy": 1.15, "settlement_statement": 1.15, "payoff_statement": 1.15,
    "recorded": 1.15,
    # formal but not instruments
    "loan_agreement": 1.10, "title_commitment": 1.10, "appraisal": 1.10,
    "inspection_report": 1.05, "invoice": 1.05, "draw_request": 1.05,
    # drafts and chatter
    "draft": 0.90, "email": 1.00, "calendar": 0.80,
}

#: Exact-match cap: a chunk that literally contains a money amount, ID or quoted
#: phrase from the question is multiplied by up to this, never more.
EXACT_MATCH_CAP = 1.5

#: Before expansion, no single artifact may hold more than this many slots in
#: the candidate list, so one long document cannot crowd out the rest.
CLUSTER_CAP_PER_PARENT = 5
#: Maximal-marginal-relevance trade-off between relevance and novelty.
MMR_LAMBDA = 0.7
#: Adaptive-K: how many candidates go to the reranker, by question complexity.
RERANK_POOL = {"simple": 60, "moderate": 100, "complex": 150}

# =============================================================================
# Reranking
# =============================================================================

RERANK_STAGE1_MODEL = "rerank-2.5"          # Voyage, cross-encoder
RERANK_STAGE1_MAX_DOCS = 100                # per call; larger pools are half-split
RERANK_STAGE1_KEEP = 30           # what stage 2 reads; 40 cost ~40s per Opus pass, 30 ~28s
RERANK_STAGE2_MODEL = model_for(Seat.ANALYST)  # Opus 5, listwise
RERANK_STAGE2_KEEP = 20
#: 40 passages × (index, score, reason) is ~3k tokens on its own; with any
#: preamble the 4k cap was hit and the stage was skipped.
RERANK_STAGE2_MAX_OUTPUT = 12000

# =============================================================================
# Expansion — reference values
# =============================================================================

#: Neighbour ±1 on every hit, bounded in total.
NEIGHBOR_EXPAND_ENABLED = True
NEIGHBOR_EXPAND_MAX_ADDED = 40
#: Parent expansion on 2+ hits from one non-email artifact.
PARENT_EXPAND_MIN_HITS = 2
PARENT_EXPAND_MAX_CHUNKS = 20
PARENT_EXPAND_MAX_PARENTS = 5
#: Adaptive per-parent token budget by how many parents are hot.
PARENT_EXPAND_BUDGET = {1: 8000, 2: 5000, 3: 4000, 4: 3500, 5: 3000}
#: Thread expansion on 2+ hits in one conversation (emails' equivalent of a parent).
THREAD_EXPAND_MIN_HITS = 2
THREAD_EXPAND_MAX_SIBLINGS = 8
THREAD_EXPAND_TOKEN_BUDGET = 6000
#: Full-document mode when the question names a file.
FULLDOC_PER_DOC_TOKEN_BUDGET = 50_000
FULLDOC_MAX_DOCS = 4
#: Hard ceiling on evidence handed to the answer model, after interleaving.
TOTAL_EVIDENCE_CAP_TOKENS = 500_000
#: Whole documents for the finalists at answer time (E3).
FINALIST_FULL_READ_MAX_DOCS = 6
FINALIST_FULL_READ_TOKEN_BUDGET = 120_000

# =============================================================================
# Agent budget — one profile
# =============================================================================

AGENT_MAX_TOOL_CALLS = 30
AGENT_MAX_TOTAL_TOKENS = 10_000_000
AGENT_MAX_WALL_CLOCK_S = 15 * 60
#: Consecutive reasoning-only turns (no tool call) before a finish is forced.
AGENT_MAX_REASONING_STREAK = 3
#: Output allowance for the forced-finalise call, so a memo is never a stub.
AGENT_FINALIZE_MAX_OUTPUT = 64_000
AGENT_PLANNER_MODEL = model_for(Seat.ANALYST)
AGENT_PLANNER_MAX_OUTPUT = 16_000
#: "Opus 5 high" (admin, 2026-09-03): highest effort plus adaptive thinking on
#: every planner and reconciliation call. Not sent on the forced-finalise call,
#: where tool_choice is pinned and thinking is API-incompatible with that.
OPUS_HIGH_KWARGS = {"output_config": {"effort": "high"}, "thinking": {"type": "adaptive"}}

# =============================================================================
# Answer style — what the user reads
# =============================================================================

#: The final answer is short, plain, and structured: a one-line headline, then
#: points each carrying an urgency the UI colours. Long memos live behind a
#: "details" disclosure; they are never the first thing shown.
#: Five, down from seven (admin directive 2026-09-04): the reader wants the answer
#: in a glance; anything beyond five points belongs in "details".
ANSWER_MAX_POINTS = 5
ANSWER_URGENCIES = ("critical", "high", "normal", "info", "good")
CRITIC_MODEL = "gpt-6-astra"

# =============================================================================
# Query understanding
# =============================================================================

QUERY_REWRITE_MODEL = model_for(Seat.ANALYST)
QUERY_REWRITE_MAX_OUTPUT = 3000
