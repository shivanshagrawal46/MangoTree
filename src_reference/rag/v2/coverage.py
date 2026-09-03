"""
Answer coverage checker (Sprint 4 — verification integrity).

The verifier proves that each `facts[]` entry's quote is grounded. It does
NOT prove the converse: that every hard claim in the PROSE is backed by a
fact. A model can slip an uncited number/date into a sentence and the
verifier never looks at it.

This checker closes that gap, deterministically (no API):

  For every critical token (currency / date / percentage / large number)
  that appears in the answer prose, require that the SAME token is present
  in the `facts[]` pool (claim + verbatim_quote), UNLESS the token sits in
  a paragraph explicitly labelled as analysis/derivation.

Currency tokens are reconciled by value (so "$1.4M" in prose is covered by
a fact quoting "$1,400,000"), reusing the same money logic as the verifier
so the two never disagree.

Output is a structured report the pipeline uses to bounce an answer back to
the agent (same contract as a verifier failure).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from src.rag.v2.verifier import (
    _extract_critical_tokens,
    _normalize,
)
from src.rag.normalize_values import all_money, money_matches, normalize_money

# Paragraphs containing any of these markers are treated as labelled
# analysis/opinion and are EXEMPT from token coverage (the prompt requires
# analysis paragraphs to carry no fabricated citations, but they may reason
# over figures already established elsewhere).
_ANALYSIS_MARKERS = (
    "based on legal analysis",
    "the pattern suggests",
    "(derived)",
    "derived:",
    "investigator's assessment",
    "a defense",
    "defense-counsel",
    "defence",
    "my read",
    "in my assessment",
)

# Sentence splitter: good enough for legal prose (split on . ! ? followed by
# space + capital, plus newlines/bullets).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])|[\n\r]+")


@dataclass(frozen=True)
class CoverageGap:
    token: str
    kind: str                # "currency" | "date" | "percent" | "number"
    sentence: str

    def as_dict(self) -> Dict[str, Any]:
        return {"token": self.token, "kind": self.kind, "sentence": self.sentence[:200]}


@dataclass
class CoverageReport:
    gaps: List[CoverageGap] = field(default_factory=list)
    n_tokens_checked: int = 0
    n_sentences: int = 0

    @property
    def ok(self) -> bool:
        return not self.gaps

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "n_tokens_checked": self.n_tokens_checked,
            "n_sentences": self.n_sentences,
            "n_gaps": len(self.gaps),
            "gaps": [g.as_dict() for g in self.gaps],
        }


def _kind_of(tok: str) -> str:
    if "$" in tok:
        return "currency"
    if "%" in tok:
        return "percent"
    if re.search(r"[A-Za-z]", tok) or "/" in tok or "-" in tok or "," in tok:
        return "date"
    return "number"


def _strip_references_section(answer: str) -> str:
    """Drop the References/Sources/Provenance trailer so we only check the
    model's own prose, not the citation list."""
    for marker in ("REFERENCES & SOURCES", "\n— Provenance", "\nProvenance:",
                   "Every factual claim above is grounded"):
        idx = answer.find(marker)
        if idx != -1:
            answer = answer[:idx]
    return answer


def _build_fact_pool(facts: Sequence[Dict[str, Any]]) -> "tuple[set[str], list[float]]":
    """Normalized critical tokens + parsed money values across all facts."""
    tokens: set[str] = set()
    money_vals: List[float] = []
    for f in facts:
        for field_name in ("verbatim_quote", "claim", "corrected_claim"):
            val = f.get(field_name)
            if not val:
                continue
            for tok in _extract_critical_tokens(str(val)):
                tokens.add(_normalize(tok))
            money_vals.extend(all_money(_normalize(str(val))))
    return tokens, money_vals


def check_coverage(
    answer: str,
    facts: Sequence[Dict[str, Any]],
) -> CoverageReport:
    """Verify every hard token in `answer` prose is backed by `facts[]`."""
    report = CoverageReport()
    if not answer:
        return report

    prose = _strip_references_section(answer)
    fact_tokens, fact_money = _build_fact_pool(facts)

    paragraphs = re.split(r"\n\s*\n", prose)
    for para in paragraphs:
        low = para.lower()
        if any(mark in low for mark in _ANALYSIS_MARKERS):
            continue  # labelled analysis — exempt
        for sentence in _SENTENCE_SPLIT.split(para):
            sentence = sentence.strip()
            if not sentence:
                continue
            report.n_sentences += 1
            for tok in _extract_critical_tokens(sentence):
                # Skip bare 4-digit years — too noisy, and almost always
                # part of a fuller date that IS covered.
                if re.fullmatch(r"(?:19|20)\d{2}", tok):
                    continue
                report.n_tokens_checked += 1
                tok_norm = _normalize(tok)
                if tok_norm in fact_tokens:
                    continue
                # Currency value reconciliation (formatting-tolerant).
                if "$" in tok:
                    v = normalize_money(tok)
                    if v is not None and any(
                        money_matches(v, a, rel_tol=0.0, abs_tol=1.0) for a in fact_money
                    ):
                        continue
                report.gaps.append(
                    CoverageGap(token=tok, kind=_kind_of(tok), sentence=sentence)
                )
    return report


__all__ = ["CoverageGap", "CoverageReport", "check_coverage"]
