"""
Write quoted-alteration (tamper) candidates to the findings ledger.

These are quoted/forwarded passages that are 88-97% similar to a stored
original message body — i.e. the SAME message but not identical. MOST are
benign (reformatting / whitespace / clipped signature); a few may be
material edits made before forwarding, which in a fraud file are worth a
look. We therefore write them as LOW-severity, LOW-confidence review
candidates with a word-level diff snippet so a human can confirm/dismiss
in seconds.

Idempotent (deterministic finding _id via fingerprint). Reversible:
delete_many({"detector": "quoted_tamper_scan_v1"}).

Usage:  python scripts/write_tamper_findings.py [--dry-run]
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import process, fuzz

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.cleaner.quoted_text import (
    split_quoted_tail, iter_quoted_segments, normalize_quote, quote_fingerprint,
)
from scripts.analyze_quoted import clean_segment, is_boilerplate, MIN_BODY_CHARS, DUP_AT, EDIT_AT, MAXLEN
from src.detect.findings import (
    Finding, Evidence, upsert_finding, ensure_indexes, SEV_INFO, SEV_MEDIUM,
)
from src.rag.v2.verifier import _CURRENCY_RE, _DATE_RE

DETECTOR = "quoted_tamper_scan_v1"

# Ignore changed spans that are just links/phones — their digits are not
# forensically meaningful amounts/dates.
_NOISE_CTX = ("http", "tel:", "link.edgepilot", "mailto:", "www.")


def _meaningful_tokens(span: str):
    if any(n in span.lower() for n in _NOISE_CTX):
        return []
    return _CURRENCY_RE.findall(span) + _DATE_RE.findall(span)


def _material_change(original: str, quoted: str) -> bool:
    """True only if a CURRENCY amount or a real DATE was added/removed/changed
    between the two (ignoring phone/URL digit noise) — a substantive edit."""
    o = " ".join(original.split())[:1500].split()
    q = " ".join(quoted.split())[:1500].split()
    sm = difflib.SequenceMatcher(a=o, b=q)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        was = " ".join(o[i1:i2])
        now = " ".join(q[j1:j2])
        if _meaningful_tokens(was) or _meaningful_tokens(now):
            return True
    return False


def _diff_snippet(original: str, quoted: str, width: int = 3) -> str:
    o = " ".join(original.split())[:1500].split()
    q = " ".join(quoted.split())[:1500].split()
    sm = difflib.SequenceMatcher(a=o, b=q)
    parts = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        was = " ".join(o[i1:i2])[:80]
        now = " ".join(q[j1:j2])[:80]
        parts.append(f"[{tag}] '{was}' -> '{now}'")
        if len(parts) >= 4:
            break
    return " ; ".join(parts) or "(whitespace/formatting only)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db = m.db
    emails = db["emails"]
    findings = db["findings"]

    docs = list(emails.find({}, {"body_text": 1, "body_text_raw": 1, "subject": 1,
                                 "date": 1, "from": 1}))
    originals, orig_ids, orig_body = [], [], {}
    known = set()
    for d in docs:
        bt = (d.get("body_text") or "").strip()
        if bt:
            norm = normalize_quote(bt)[:MAXLEN]
            if len(norm) >= 40:
                originals.append(norm)
                orig_ids.append(str(d["_id"]))
                orig_body[str(d["_id"])] = bt
                known.add(quote_fingerprint(bt))

    seen = set()
    candidates = []
    for d in docs:
        raw = d.get("body_text_raw") or d.get("body_text") or ""
        _, tail = split_quoted_tail(raw)
        if not tail.strip():
            continue
        for seg in iter_quoted_segments(tail):
            cleaned = clean_segment(seg)
            norm = normalize_quote(cleaned)[:MAXLEN]
            if len(norm) < MIN_BODY_CHARS or is_boilerplate(norm):
                continue
            fp = quote_fingerprint(cleaned)
            if fp in known or fp in seen:
                continue
            best = process.extractOne(norm, originals, scorer=fuzz.ratio, score_cutoff=EDIT_AT)
            if best is None or best[1] >= DUP_AT:
                continue  # novel or duplicate -> not an alteration candidate
            seen.add(fp)
            candidates.append({
                "fp": fp, "sim": round(best[1], 1),
                "orig_id": orig_ids[best[2]],
                "found_in": str(d["_id"]),
                "quoted": cleaned,
            })

    # Split into MATERIAL (changed number/date/amount) vs formatting-only.
    material = []
    for c in candidates:
        obody = orig_body.get(c["orig_id"], "")
        if _material_change(obody, c["quoted"]):
            c["diff"] = _diff_snippet(obody, c["quoted"])
            material.append(c)
    print(f"quoted-alteration candidates: {len(candidates):,}")
    print(f"  of which MATERIAL (changed number/date/amount): {len(material):,}")
    print(f"  formatting-only (not written): {len(candidates) - len(material):,}")

    if args.dry_run:
        for c in material[:12]:
            print(f"  [MATERIAL] sim={c['sim']} found_in={c['found_in']}\n     {c['diff'][:200]}")
        if not material:
            print("  -> No material alterations detected. (Nothing to write.)")
        m.close()
        return 0

    ensure_indexes(findings)
    written = 0
    for c in material:
        f = Finding(
            finding_type="quoted_alteration",
            title=f"MATERIAL difference between quoted copy and original ({c['sim']}% match)",
            detail=("A forwarded/quoted copy of a message differs from the stored "
                    "original in a NUMBER, DATE, or AMOUNT — a possible edit-before-"
                    f"forward. Review both. Change: {c['diff']}"),
            severity=SEV_MEDIUM,
            confidence=0.5,
            evidence=[Evidence(chunk_id=c["found_in"], quote=c["quoted"][:300],
                               note=f"matched original email {c['orig_id']} @ {c['sim']}% similarity")],
            detector=DETECTOR,
            key=c["fp"],
        )
        upsert_finding(findings, f)
        written += 1
    print(f"wrote {written:,} MATERIAL quoted_alteration findings "
          f"(detector={DETECTOR}, severity=medium).")
    print("  Undo: db.findings.delete_many({detector:'quoted_tamper_scan_v1'})")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
