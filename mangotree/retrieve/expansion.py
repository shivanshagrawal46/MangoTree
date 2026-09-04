"""Expansion — from the chunks that matched to the context that answers.

Baseline retrieval is chunk-level: one hit in a 385-page title package is one
1000-token chunk. Four expansions widen that, each for a specific failure:

* **neighbour** (every hit) — ordinal ±1 from the same document, for the fact
  split across a chunk boundary: a lien amount whose sentence continues on the
  next chunk.
* **parent** (2+ hits in one non-email document) — the rest of that document,
  within limits. Two hits say the document itself is what matters. Attachments
  and E-drive files both qualify; an email's chunks already are the whole email.
* **thread** (2+ hits in one conversation) — the emails' equivalent of a parent:
  the thread's other messages and their attachments.
* **full document** (the question names a file) — every chunk, in order.

Two counterweights keep it bounded: the cluster cap before expansion (in
rescoring) and a hard token ceiling after. Hit counts per artifact are taken
BEFORE diversification collapsed anything, or the "2+ hits" signal would never
fire.

Ordering is deliberate. Retrieved chunks keep relevance order; expanded chunks
are appended in document order so the document reads as written; then
interleave-for-attention moves the strongest material to the front and back,
because models attend least to the middle; then the ceiling trims — from the
middle, for the same reason.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Set

from mangotree.chunk.tokens import count_tokens
from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg
from mangotree.retrieve.channels import Channels
from mangotree.retrieve.hits import Hit
from mangotree.storage.mongo import Mongo


def _tokens(h: Hit) -> int:
    if h.token_count:
        return h.token_count
    h.token_count = count_tokens(f"{h.context}\n\n{h.text}")
    return h.token_count


class Expander:
    def __init__(self, mongo: Mongo, channels: Channels):
        self.mongo = mongo
        self.ch = channels
        self.trace: Dict[str, object] = {}

    # ---------------------------------------------------------------- neighbour
    def neighbours(self, hits: Sequence[Hit], *, filter: dict, seen: Set[str]) -> List[Hit]:
        if not cfg.NEIGHBOR_EXPAND_ENABLED or not hits:
            return []
        found = self.ch.neighbors(hits, filter=filter)
        added: List[Hit] = []
        for h in hits:
            for n in found.get(h.artifact_sha, []):
                if n.chunk_id in seen:
                    continue
                if abs(n.ordinal - h.ordinal) != 1:
                    continue
                n.origin = "neighbor"
                n.label = h.label
                seen.add(n.chunk_id)
                added.append(n)
                if len(added) >= cfg.NEIGHBOR_EXPAND_MAX_ADDED:
                    self.trace["neighbours"] = len(added)
                    return added
        self.trace["neighbours"] = len(added)
        return added

    # ------------------------------------------------------------------- parent
    def parents(self, hits: Sequence[Hit], hit_counts: Dict[str, int], *, filter: dict, seen: Set[str]) -> List[Hit]:
        hot = [
            (sha, n) for sha, n in hit_counts.items()
            if n >= cfg.PARENT_EXPAND_MIN_HITS
        ]
        # Emails are excluded: their chunks are already the whole message.
        by_sha = {h.artifact_sha: h for h in hits}
        hot = [(sha, n) for sha, n in hot if sha in by_sha and by_sha[sha].source_type != "email"]
        hot.sort(key=lambda x: -x[1])
        hot = hot[: cfg.PARENT_EXPAND_MAX_PARENTS]
        if not hot:
            self.trace["parents"] = 0
            return []
        budget = cfg.PARENT_EXPAND_BUDGET.get(len(hot), cfg.PARENT_EXPAND_BUDGET[5])
        added: List[Hit] = []
        for sha, _ in hot:
            label = by_sha[sha].label
            spent = 0
            count = 0
            for c in self.ch.chunks_of(sha, filter=filter):
                if c.chunk_id in seen:
                    continue
                t = _tokens(c)
                if spent + t > budget or count >= cfg.PARENT_EXPAND_MAX_CHUNKS:
                    break
                c.origin = "parent"
                c.label = label
                seen.add(c.chunk_id)
                added.append(c)
                spent += t
                count += 1
        self.trace["parents"] = {"artifacts": [s for s, _ in hot], "chunks": len(added)}
        return added

    # ------------------------------------------------------------------- thread
    def threads(self, hits: Sequence[Hit], *, filter: dict, seen: Set[str]) -> List[Hit]:
        email_shas = list({h.artifact_sha for h in hits if h.source_type == "email"})
        if not email_shas:
            self.trace["threads"] = 0
            return []
        arts = {a["sha256"]: a for a in self.mongo.artifacts.find(
            {"sha256": {"$in": email_shas}}, {"sha256": 1, "thread_key": 1})}
        by_thread: Dict[str, List[str]] = defaultdict(list)
        for sha in email_shas:
            key = (arts.get(sha) or {}).get("thread_key")
            if key:
                by_thread[key].append(sha)
        hot = [k for k, shas in by_thread.items() if len(shas) >= cfg.THREAD_EXPAND_MIN_HITS]
        if not hot:
            self.trace["threads"] = 0
            return []
        label_by_sha = {h.artifact_sha: h.label for h in hits}
        added: List[Hit] = []
        for key in hot[:4]:
            spent = 0
            siblings = list(self.mongo.artifacts.find(
                {"thread_key": key, "source_type": "email"},
                {"sha256": 1, "date": 1}).sort("date", 1).limit(cfg.THREAD_EXPAND_MAX_SIBLINGS + len(by_thread[key])))
            sibling_shas = [s["sha256"] for s in siblings if s["sha256"] not in by_thread[key]]
            attach_shas = [a["sha256"] for a in self.mongo.artifacts.find(
                {"source_types": "attachment", "parent_email_shas": {"$in": sibling_shas + by_thread[key]}},
                {"sha256": 1}).limit(20)]
            label = next((label_by_sha[s] for s in by_thread[key] if s in label_by_sha), "")
            for sha in sibling_shas[: cfg.THREAD_EXPAND_MAX_SIBLINGS] + attach_shas:
                chunks = self.ch.chunks_of(sha, filter=filter)
                # First chunk of each sibling / attachment carries the gist.
                for c in chunks[:2]:
                    if c.chunk_id in seen:
                        continue
                    t = _tokens(c)
                    if spent + t > cfg.THREAD_EXPAND_TOKEN_BUDGET:
                        break
                    c.origin = "thread"
                    c.label = label
                    seen.add(c.chunk_id)
                    added.append(c)
                    spent += t
        self.trace["threads"] = {"threads": hot[:4], "chunks": len(added)}
        return added

    def carrying_emails(self, attachment_sha: str, *, filter: dict, seen: Set[str], limit: int = 6) -> List[Hit]:
        """Attachment -> every email that carried it (first chunk of each)."""
        art = self.mongo.artifacts.find_one({"sha256": attachment_sha}, {"parent_email_shas": 1})
        parents = (art or {}).get("parent_email_shas") or []
        added: List[Hit] = []
        for sha in parents[:limit]:
            for c in self.ch.chunks_of(sha, filter=filter)[:1]:
                if c.chunk_id not in seen:
                    c.origin = "thread"
                    seen.add(c.chunk_id)
                    added.append(c)
        return added

    # ---------------------------------------------------------------- full doc
    def full_documents(self, filenames: Sequence[str], *, filter: dict, seen: Set[str], label: str = "") -> List[Hit]:
        shas = self.ch.resolve_filenames(filenames, filter=filter)[: cfg.FULLDOC_MAX_DOCS]
        added: List[Hit] = []
        for sha in shas:
            spent = 0
            for c in self.ch.chunks_of(sha, filter=filter):
                t = _tokens(c)
                if spent + t > cfg.FULLDOC_PER_DOC_TOKEN_BUDGET:
                    break
                if c.chunk_id in seen:
                    spent += t
                    continue
                c.origin = "fulldoc"
                c.label = label or c.label
                seen.add(c.chunk_id)
                added.append(c)
                spent += t
        self.trace["fulldoc"] = {"artifacts": shas, "chunks": len(added)}
        return added

    # ---------------------------------------------------------------- assemble
    @staticmethod
    def interleave_for_attention(retrieved: List[Hit], expanded: List[Hit]) -> List[Hit]:
        """Strongest material at the front and the back; the rest in between.

        Retrieved hits are in relevance order. Expanded chunks are in document
        order. Models attend least to the middle of a long context, so the top
        retrieved hits alternate between head and tail, with expansions in the
        middle, kept in their document order.
        """
        if not retrieved:
            return list(expanded)
        head: List[Hit] = []
        tail: List[Hit] = []
        for i, h in enumerate(retrieved):
            (head if i % 2 == 0 else tail).append(h)
        tail.reverse()
        return head + list(expanded) + tail

    @staticmethod
    def cap_tokens(hits: List[Hit], *, cap: int = cfg.TOTAL_EVIDENCE_CAP_TOKENS) -> List[Hit]:
        """Trim from the middle until under the ceiling."""
        total = sum(_tokens(h) for h in hits)
        out = list(hits)
        while total > cap and len(out) > 2:
            mid = len(out) // 2
            total -= _tokens(out[mid])
            del out[mid]
        return out

    def assemble(self, retrieved: List[Hit], expanded: List[Hit]) -> List[Hit]:
        # Expanded chunks in document order: by artifact (first appearance), then ordinal.
        first_pos = {}
        for i, h in enumerate(retrieved):
            first_pos.setdefault(h.artifact_sha, i)
        expanded_sorted = sorted(expanded, key=lambda h: (first_pos.get(h.artifact_sha, 10_000), h.artifact_sha, h.ordinal))
        ordered = self.interleave_for_attention(retrieved, expanded_sorted)
        capped = self.cap_tokens(ordered)
        self.trace["assembled"] = {"retrieved": len(retrieved), "expanded": len(expanded),
                                   "final": len(capped), "tokens": sum(_tokens(h) for h in capped)}
        return capped
