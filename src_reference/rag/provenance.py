"""Sprint 5 · 5.5 + 5.7 — privilege/clean-mode retrieval filter + provenance footer.

clean_mode_filter(): the Atlas filter that EXCLUDES privileged content at the
retrieval layer, so a shareable/trustee output structurally cannot leak
privileged strategy. Apply to any retrieval when mode == "clean".

provenance_footer(): a structured + human-readable footer summarizing what
evidence an answer rests on — corpora used, privilege posture, source-type mix,
date span, and verification status — appended to every answer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

PRIVILEGED = "privileged"


def clean_mode_filter() -> Dict[str, Any]:
    """Atlas $vectorSearch / find filter excluding privileged chunks."""
    return {"privilege_status": {"$ne": PRIVILEGED}}


def is_clean_safe(chunk: Dict[str, Any]) -> bool:
    return (chunk.get("privilege_status") or PRIVILEGED) != PRIVILEGED


def provenance_footer(chunks: Sequence[Any], *, mode: str = "analysis",
                      fact_verdicts: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build the provenance/confidence footer from the evidence actually used."""
    corpora: Dict[str, int] = {}
    sources: Dict[str, int] = {}
    privileged_n = 0
    dates: List[Any] = []
    from src.rag.evidence_schema import corpus_for
    for c in chunks:
        get = (c.get if isinstance(c, dict) else lambda k, d=None: getattr(c, k, d))
        corp = corpus_for(c)
        corpora[corp] = corpora.get(corp, 0) + 1
        st = get("doc_source_type") or get("source_type") or "unknown"
        sources[st] = sources.get(st, 0) + 1
        if (get("privilege_status") or "") == PRIVILEGED:
            privileged_n += 1
        d = get("latest_date") or get("doc_date") or get("date")
        if d:
            dates.append(d)
    verified = unverified = 0
    for v in (fact_verdicts or []):
        st = (v.get("status") or v.get("verdict") or "").upper()
        if st == "VERIFIED":
            verified += 1
        elif st:
            unverified += 1
    low_ocr = 0
    for c in chunks:
        get = (c.get if isinstance(c, dict) else lambda k, d=None: getattr(c, k, d))
        if get("ocr_low_confidence"):
            low_ocr += 1
    span = None
    try:
        ds = sorted(d for d in dates if hasattr(d, "strftime"))
        if ds:
            span = f"{ds[0].strftime('%Y-%m-%d')} to {ds[-1].strftime('%Y-%m-%d')}"
    except Exception:  # noqa: BLE001
        pass

    # Human-readable formatting — NEVER interpolate raw dicts (they land in
    # court-facing exports). Render as "key: n, key: n" and keep ASCII so
    # downstream PDF fonts don't garble it.
    def _fmt(d: Dict[str, int]) -> str:
        return ", ".join(f"{k}: {v}" for k, v in
                         sorted(d.items(), key=lambda kv: -kv[1])) or "none"

    n_sources = len(list(chunks))
    text = (f"Provenance: {n_sources} sources ({_fmt(sources)}) "
            f"| corpora: {_fmt(corpora)} | mode={mode}"
            + (f" | privileged sources used: {privileged_n}" if privileged_n else "")
            + (f" | date span {span}" if span else "")
            + (f" | facts verified {verified}/{verified+unverified}" if (verified+unverified) else "")
            + (f" | {low_ocr} low-OCR-confidence sources" if low_ocr else ""))
    return {"mode": mode, "n_sources": len(list(chunks)), "corpora": corpora,
            "source_types": sources, "privileged_sources": privileged_n,
            "date_span": span, "verified": verified, "unverified": unverified,
            "low_ocr_sources": low_ocr,
            "clean_mode_leak": (mode == "clean" and privileged_n > 0), "text": text}
