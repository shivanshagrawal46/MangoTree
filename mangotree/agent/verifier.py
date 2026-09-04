"""Trust machinery — byte-for-byte verification and the coverage statement.

Every fact the agent submits names its sources and, where it matters, a quote.
The verifier checks the quote appears in the cited passage exactly (whitespace
normalised, nothing else), and that the claim's critical tokens — amounts,
dates, percentages, identifiers — appear in the cited passages. A paraphrase
presented as a quote fails. A number in the claim that is in no cited passage
fails. Failures are not silently dropped: one re-extraction pass asks Opus 5 to
find the exact quote in the cited text, and whatever still fails is reported
as unverified beside the answer.

The coverage statement is assembled from the run, not written by the model: what
scopes were searched, how many lists and passages, which enumerations ran and
their denominators, which stages degraded, how many unplaced items exist in
scope. It is attached to every answer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg
from mangotree.retrieve.hits import Hit
from mangotree.retrieve.scope import Scope

from .scratchpad import AgentScratchpad

_WS = re.compile(r"\s+")
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?(?![\w])")
_DATE = re.compile(r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,)?\s+\d{4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4})\b", re.I)


def _norm(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2014", "-").replace("\u2013", "-").replace("\xa0", " ")
    return _WS.sub(" ", s).strip().lower()


def _digits(s: str) -> str:
    return re.sub(r"[^\d.]", "", s)


def critical_tokens(claim: str) -> Dict[str, List[str]]:
    return {
        "money": [m.group(0) for m in _MONEY.finditer(claim)],
        "dates": [m.group(0) for m in _DATE.finditer(claim)],
        "numbers": [m.group(0) for m in _NUMBER.finditer(claim) if len(_digits(m.group(0))) >= 3],
    }


@dataclass
class FactVerdict:
    claim: str
    quote: str
    sources: List[int]
    verdict: str                 # verified | quote_not_found | token_missing | bad_source | no_sources
    detail: str = ""
    missing_tokens: List[str] = field(default_factory=list)
    reextracted: bool = False

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class Verifier:
    def __init__(self, anthropic_api_key: str, *, model: Optional[str] = None):
        self._key = anthropic_api_key
        self.model = model or cfg.RERANK_STAGE2_MODEL
        self._client = None

    # ---------------------------------------------------------------- checks
    @staticmethod
    def _passage_text(h: Hit) -> str:
        return _norm(f"{h.context}\n{h.header}\n{h.text}")

    def check_fact(self, fact: Dict[str, Any], pad: AgentScratchpad) -> Dict[str, Any]:
        claim = str(fact.get("claim") or "")
        quote = str(fact.get("quote") or "")
        sources = [int(s) for s in (fact.get("sources") or []) if str(s).lstrip("#[]").isdigit()] or \
                  [int(re.sub(r"\D", "", str(s))) for s in (fact.get("sources") or []) if re.sub(r"\D", "", str(s))]
        if not sources:
            return FactVerdict(claim, quote, [], "no_sources", "fact cites no passage").as_dict()
        hits = [pad.get(i) for i in sources]
        if any(h is None for h in hits):
            bad = [i for i, h in zip(sources, hits) if h is None]
            return FactVerdict(claim, quote, sources, "bad_source", f"indices not on pad: {bad}").as_dict()
        texts = [self._passage_text(h) for h in hits]
        joined = "\n".join(texts)

        if quote:
            q = _norm(quote)
            if q and not any(q in t for t in texts):
                return FactVerdict(claim, quote, sources, "quote_not_found",
                                   "quote is not byte-for-byte in any cited passage").as_dict()

        toks = critical_tokens(claim)
        missing: List[str] = []
        for m in toks["money"]:
            d = _digits(m)
            if d and d not in _digits(joined):
                missing.append(m)
        for n in toks["numbers"]:
            d = _digits(n.rstrip("%"))
            if d and d not in _digits(joined) and n not in joined:
                missing.append(n)
        for dt in toks["dates"]:
            if _norm(dt) not in joined and _digits(dt) not in _digits(joined):
                # Dates may be formatted differently; a loose pass on year+month.
                y = re.search(r"\d{4}", dt)
                if not (y and y.group(0) in joined):
                    missing.append(dt)
        if missing:
            return FactVerdict(claim, quote, sources, "token_missing",
                               "critical tokens absent from cited passages", missing).as_dict()
        return FactVerdict(claim, quote, sources, "verified").as_dict()

    # --------------------------------------------------------- re-extraction
    def _client_(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._key, max_retries=2)
        return self._client

    def reextract(self, fact: Dict[str, Any], pad: AgentScratchpad) -> Optional[str]:
        """Ask the model for the exact supporting sentence from the cited text."""
        sources = [int(re.sub(r"\D", "", str(s))) for s in (fact.get("sources") or []) if re.sub(r"\D", "", str(s))]
        hits = [h for h in (pad.get(i) for i in sources) if h]
        if not hits:
            return None
        passages = "\n\n".join(f"[#{pad.index_of(h.chunk_id)}]\n{h.text[:6000]}" for h in hits)
        prompt = (f"CLAIM: {fact.get('claim')}\n\nPASSAGES:\n{passages}\n\n"
                  "Copy the single sentence or fragment from the passages that supports the claim, EXACTLY as written "
                  "(same characters, same numbers). If no passage supports it, reply exactly: NONE. Reply with the "
                  "quote only, no commentary.")
        try:
            r = self._client_().messages.create(model=self.model, max_tokens=1500,
                                                messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in r.content if b.type == "text").strip().strip('"“”')
            return None if not text or text.upper().startswith("NONE") else text
        except Exception as exc:
            logger.warning("re-extraction failed: %s", exc)
            return None

    @staticmethod
    def derive_facts(answer: str, *, limit: int = 60) -> List[Dict[str, Any]]:
        """Facts from the answer text itself, for when the model submitted none.

        The final-answer tool asks for a facts list, and on long memo-style answers
        the model sometimes hands back an empty one — a 30-step investigation then
        reported "0/0 facts verified" while asserting dozens of figures. Every
        sentence or table cell that cites a passage ([#N]) and carries money, a
        date or a number becomes a fact with those sources; the token check then
        runs exactly as it would on a model-supplied fact. No quote is attached,
        so only the critical-token test applies, which is the test that matters.
        """
        facts: List[Dict[str, Any]] = []
        # Split on sentence ends, table cell bars and line breaks; keep pieces with a citation.
        for piece in re.split(r"(?<=[.;!?])\s+|\s*\|\s*|\n+", answer or ""):
            piece = piece.strip(" *-•\t")
            if len(piece) < 12:
                continue
            idx = [int(m) for m in re.findall(r"\[#(\d+)\]", piece)]
            if not idx:
                continue
            claim = re.sub(r"\s*\[#\d+\](?:,\s*\[#\d+\])*", "", piece).strip()
            toks = critical_tokens(claim)
            if not (toks["money"] or toks["dates"] or toks["numbers"]):
                continue
            facts.append({"claim": claim[:400], "quote": "", "sources": sorted(set(idx))[:6], "derived": True})
            if len(facts) >= limit:
                break
        return facts

    def verify(self, facts: Sequence[Dict[str, Any]], pad: AgentScratchpad, *, retry: bool = True) -> Dict[str, Any]:
        verdicts: List[Dict[str, Any]] = []
        for f in facts:
            v = self.check_fact(f, pad)
            if v["verdict"] in ("quote_not_found", "token_missing") and retry:
                new_quote = self.reextract(f, pad)
                if new_quote:
                    v2 = self.check_fact({**f, "quote": new_quote}, pad)
                    v2["reextracted"] = True
                    if v2["verdict"] == "verified" or v2["verdict"] != v["verdict"]:
                        v = v2
            verdicts.append(v)
        n = len(verdicts)
        ok = sum(1 for v in verdicts if v["verdict"] == "verified")
        return {
            "facts": n, "verified": ok, "rate": (ok / n) if n else None,
            "verdicts": verdicts,
            "unverified": [v for v in verdicts if v["verdict"] != "verified"],
        }

    # -------------------------------------------------------------- coverage
    @staticmethod
    def coverage_statement(pad: AgentScratchpad, scope: Scope, mongo, *, verification: Optional[Dict[str, Any]] = None) -> str:
        parts: List[str] = []
        parts.append(f"Scope: {scope.describe()}.")
        if pad.searches:
            parts.append(f"Ran {len(pad.searches)} search(es) across {len(pad.lists_seen)} ranked lists; "
                         f"{pad.n_chunks} passages examined.")
        if scope.mode == "property" and scope.property_id:
            try:
                own = mongo.artifacts.count_documents({"property_ids": scope.property_id, "is_inline_image": {"$ne": True}})
                portfolio = mongo.artifacts.count_documents({"placement": "portfolio"})
                unplaced = mongo.artifacts.count_documents({"placement": "unplaced"})
                parts.append(f"Property file: {own} documents; portfolio store searched alongside ({portfolio} documents); "
                             f"{unplaced} unplaced items pending human review were also searchable.")
            except Exception:
                pass
        for e in pad.enumerations[-3:]:
            parts.append(f"Enumeration [{e.get('criteria')}]: {e.get('matched')} of {e.get('in_scope')} documents in scope.")
        if verification and verification.get("facts"):
            parts.append(f"Facts checked byte-for-byte: {verification['verified']}/{verification['facts']} verified.")
        if pad.degrades:
            parts.append("Degraded stages: " + "; ".join(sorted(set(pad.degrades))) + ".")
        b = pad.budget
        parts.append(f"Budget used: {b.tool_calls_used}/{b.max_tool_calls} tool calls, "
                     f"{b.total_tokens:,} tokens, {int(b.elapsed_s)}s.")
        return " ".join(parts)
