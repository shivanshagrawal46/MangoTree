"""Per-property context assembly — what the analyst model is allowed to see.

This is the boundary that makes property-wise analysis honest. Everything placed
in the context comes from chunks tagged with **this** property, so the model
cannot attribute another property's facts even if it wanted to. There is no
"related properties" section and no global corpus fallback, because those are
exactly the doors contamination walks through.

Three tiers, mirroring `docs/03-CONTEXT-AND-MEMORY.md`:

1. **Pinned** — registry facts and Remember notes. Injected **verbatim**, never
   paraphrased and never embedded-then-retrieved, because a summary of an
   instruction is not the instruction. This is what makes Remember notes
   hallucination-proof: the model reads the exact bytes the user wrote.
2. **Retrieved** — chunks for this property, ranked for the question at hand.
3. **Recent** — the newest activity, so "what's happening now" does not depend on
   a semantic query happening to match.

Every block carries a ``[C#]`` handle. The analyst must cite those handles, which
is what lets the verifier mechanically check each claim against its source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.retrieve.retriever import Hit, Retriever
from mangotree.storage.mongo import Mongo

#: Characters of evidence given to the analyst. Roughly 30k tokens.
CONTEXT_CHAR_BUDGET = 120_000


@dataclass
class ContextBlock:
    handle: str                # "C1"
    text: str
    citation: str
    chunk_id: str
    artifact_sha: str
    source_ref: str
    date: object = None
    doc_class: Optional[str] = None
    tier: str = "retrieved"

    def render(self) -> str:
        header = f"[{self.handle}] {self.citation}"
        if self.date and hasattr(self.date, "strftime"):
            header += f" ({self.date.strftime('%Y-%m-%d')})"
        return f"{header}\n{self.text}"


@dataclass
class PropertyContext:
    property_id: str
    canonical_address: str
    question: str
    pinned: List[str] = field(default_factory=list)
    blocks: List[ContextBlock] = field(default_factory=list)
    excluded_privileged: int = 0
    truncated: int = 0

    @property
    def by_handle(self) -> Dict[str, ContextBlock]:
        return {b.handle: b for b in self.blocks}

    def render(self) -> str:
        parts: List[str] = [
            f"PROPERTY: {self.canonical_address} (id: {self.property_id})",
        ]
        if self.pinned:
            parts.append(
                "\n=== PINNED FACTS AND STANDING INSTRUCTIONS (verbatim, authoritative) ==="
            )
            parts.extend(self.pinned)

        parts.append(
            "\n=== EVIDENCE ===\n"
            "Every block below comes from documents or emails about THIS property only.\n"
            "Cite the [C#] handle for every claim you make."
        )
        parts.extend(block.render() for block in self.blocks)

        if self.excluded_privileged:
            parts.append(
                f"\n[NOTE] {self.excluded_privileged} privileged/legal item(s) exist for this "
                "property and were withheld from this context."
            )
        return "\n\n".join(parts)

    def stats(self) -> dict:
        return {
            "property_id": self.property_id,
            "blocks": len(self.blocks),
            "chars": sum(len(b.text) for b in self.blocks),
            "pinned": len(self.pinned),
            "excluded_privileged": self.excluded_privileged,
            "truncated": self.truncated,
        }


class ContextBuilder:
    def __init__(self, mongo: Mongo, retriever: Retriever):
        self.mongo = mongo
        self.retriever = retriever

    # ------------------------------------------------------------------
    def _pinned(self, property_id: str) -> List[str]:
        """Registry facts plus Remember notes — verbatim, never summarised."""
        out: List[str] = []
        prop = PROPERTY_INDEX.get(property_id)
        if prop:
            out.append(
                f"Canonical address: {prop.canonical_address}\n"
                f"Known aliases: {', '.join(prop.aliases)}"
            )
            # The standing 904/910 trap, stated explicitly in context.
            siblings = [
                p for p in PROPERTY_INDEX.values()
                if p.property_id != property_id
                and p.canonical_address.split(" ", 1)[-1] == prop.canonical_address.split(" ", 1)[-1]
            ]
            if siblings:
                names = ", ".join(s.canonical_address for s in siblings)
                out.append(
                    f"WARNING: {names} is a DIFFERENT property on the same street. "
                    f"Never merge facts between them."
                )

        for note in self.mongo.db["remember_notes"].find(
            {"property_id": property_id, "active": True}
        ).sort("created_at", -1):
            author = note.get("author", "admin")
            out.append(f"REMEMBER (from {author}, verbatim):\n{note['text']}")

        return out

    # ------------------------------------------------------------------
    def _recent(self, property_id: str, limit: int) -> List[dict]:
        return list(
            self.mongo.chunks.find(
                {"property_ids": property_id, "privileged": {"$ne": True},
                 "date": {"$ne": None}},
                {"chunk_id": 1, "text": 1, "context": 1, "display_name": 1,
                 "source_ref": 1, "artifact_sha": 1, "date": 1, "doc_class": 1, "_id": 0},
            ).sort("date", -1).limit(limit)
        )

    # ------------------------------------------------------------------
    def build(
        self,
        property_id: str,
        question: str,
        *,
        top_k: int = 30,
        recent_k: int = 8,
        include_privileged: bool = False,
        char_budget: int = CONTEXT_CHAR_BUDGET,
    ) -> PropertyContext:
        prop = PROPERTY_INDEX.get(property_id)
        context = PropertyContext(
            property_id=property_id,
            canonical_address=prop.canonical_address if prop else property_id,
            question=question,
            pinned=self._pinned(property_id),
        )

        if not include_privileged:
            context.excluded_privileged = self.mongo.chunks.count_documents(
                {"property_ids": property_id, "privileged": True}
            )

        hits: List[Hit] = self.retriever.search(
            question, property_id=property_id, top_k=top_k, pool=max(60, top_k * 2),
            include_privileged=include_privileged,
        )

        seen: set = set()
        used = 0
        handle = 1

        for hit in hits:
            if hit.chunk_id in seen:
                continue
            if used + len(hit.text) > char_budget:
                context.truncated += 1
                continue
            seen.add(hit.chunk_id)
            used += len(hit.text)
            context.blocks.append(ContextBlock(
                handle=f"C{handle}", text=hit.text, citation=hit.citation,
                chunk_id=hit.chunk_id, artifact_sha=hit.artifact_sha,
                source_ref=hit.source_ref, date=hit.date, doc_class=hit.doc_class,
                tier="retrieved",
            ))
            handle += 1

        # Recency is added separately because "what is happening now" must not
        # depend on the question happening to be semantically similar to it.
        for doc in self._recent(property_id, recent_k):
            if doc["chunk_id"] in seen:
                continue
            if used + len(doc.get("text", "")) > char_budget:
                context.truncated += 1
                continue
            seen.add(doc["chunk_id"])
            used += len(doc.get("text", ""))
            citation = doc.get("display_name", "")
            ref = doc.get("source_ref", "")
            if ref and ref not in {"document", "email body"}:
                citation = f"{citation} — {ref}"
            context.blocks.append(ContextBlock(
                handle=f"C{handle}", text=doc.get("text", ""), citation=citation,
                chunk_id=doc["chunk_id"], artifact_sha=doc.get("artifact_sha", ""),
                source_ref=ref, date=doc.get("date"), doc_class=doc.get("doc_class"),
                tier="recent",
            ))
            handle += 1

        return context
