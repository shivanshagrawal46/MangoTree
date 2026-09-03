"""
Fuzzy novel-vs-edited analysis of quoted passages (READ-ONLY).

Turns the coarse "17k non-duplicate" number into the three real buckets:
  duplicate   - reformatted copy of an email we already have (skip)
  edited      - high-similarity copy with changed words (TAMPER candidate)
  novel       - matches nothing we have (the true MUST-INDEX evidence)

Method: for each quoted segment, exact-fingerprint check first; else fuzzy
match against every existing email body using rapidfuzz.process.extractOne
with a score cutoff (fast pruning). Verdicts are cached by fingerprint
because the same quoted block repeats all over a thread.

Writes NOTHING to the DB. Produces counts + samples.

Usage: python scripts/analyze_quoted.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import re

from rapidfuzz import process, fuzz

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.cleaner.quoted_text import (
    split_quoted_tail, iter_quoted_segments,
    normalize_quote, quote_fingerprint,
)
from src.cleaner.text_cleaner import strip_signatures_and_quotes

# Strip quoted-message headers / attribution / boilerplate so we compare
# MESSAGE BODIES against the (also-cleaned) originals — not header+signature
# noise, which never matches a cleaned body and falsely reads as "novel".
_HEADER_LINE = re.compile(
    r"^\s*\**(from|sent|to|cc|bcc|subject|date|reply-to)\**\s*:.*$",
    re.IGNORECASE | re.MULTILINE)
_ATTR_LINE = re.compile(r"^\s*On .{1,200} wrote:\s*$", re.IGNORECASE | re.MULTILINE)
_BOILERPLATE = (
    "note to public access users", "external sender", "confidentiality",
    "irs circular 230", "this e-mail", "this email", "unsubscribe",
    "judicial conference", "electronic case filing",
)
MIN_BODY_CHARS = 80   # after cleaning, shorter than this = fragment (not evidence)


def clean_segment(seg: str) -> str:
    s = _HEADER_LINE.sub("", seg)
    s = _ATTR_LINE.sub("", s)
    s = strip_signatures_and_quotes(s)
    return s.strip()


def is_boilerplate(norm: str) -> bool:
    return any(mark in norm for mark in _BOILERPLATE)

# Similarity thresholds (0-100) over normalized text.
DUP_AT = 97       # >= -> essentially the same message, just reformatted
EDIT_AT = 88      # EDIT_AT..DUP_AT -> same message, words changed (tamper)
                  # < EDIT_AT      -> novel (new content)
MAXLEN = 1500     # truncate normalized text for identity comparison


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str, default="_quoted_analysis.json")
    args = ap.parse_args()

    print("READ-ONLY fuzzy quoted-text analysis. No DB writes.")
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db = m.db
    emails = db["emails"]

    docs = list(emails.find({}, {"body_text": 1, "body_text_raw": 1}))
    print(f"loaded {len(docs):,} emails")

    # Build the pool of "originals we already have": normalized bodies.
    originals = []       # normalized (truncated) text
    orig_ids = []        # parallel _id list
    known_fps = set()
    for d in docs:
        bt = (d.get("body_text") or "").strip()
        if not bt:
            continue
        norm = normalize_quote(bt)[:MAXLEN]
        if len(norm) >= 40:
            originals.append(norm)
            orig_ids.append(str(d["_id"]))
            known_fps.add(quote_fingerprint(bt))
    print(f"original-body pool: {len(originals):,}")

    counts = {"duplicate": 0, "edited": 0, "novel": 0, "fragment": 0, "boilerplate": 0}
    seg_total = 0
    emails_with_tail = 0
    cache: dict = {}                 # fingerprint -> (bucket, score, match_id)
    novel_samples = []
    tamper_samples = []

    for i, d in enumerate(docs):
        if i % 1000 == 0:
            print(f"  ...scanned {i:,}/{len(docs):,} emails  "
                  f"(novel={counts['novel']}, edited={counts['edited']}, dup={counts['duplicate']})")
        raw = d.get("body_text_raw") or d.get("body_text") or ""
        _, tail = split_quoted_tail(raw)
        if not tail.strip():
            continue
        segs = iter_quoted_segments(tail)
        if segs:
            emails_with_tail += 1
        for seg in segs:
            seg_total += 1
            # Clean the quoted passage down to its MESSAGE BODY before
            # comparing (strip headers/attribution/signatures/boilerplate).
            cleaned = clean_segment(seg)
            fp = quote_fingerprint(cleaned)
            if fp in cache:
                bucket, score, match_id = cache[fp]
                counts[bucket] += 1
                continue
            norm = normalize_quote(cleaned)[:MAXLEN]
            if len(norm) < MIN_BODY_CHARS:
                cache[fp] = ("fragment", 0.0, None)   # header/sig-only scrap
                counts["fragment"] += 1
                continue
            if is_boilerplate(norm):
                cache[fp] = ("boilerplate", 0.0, None)  # disclaimers/ECF notices
                counts["boilerplate"] += 1
                continue
            if fp in known_fps:
                cache[fp] = ("duplicate", 100.0, None)
                counts["duplicate"] += 1
                continue
            best = process.extractOne(
                norm, originals, scorer=fuzz.ratio, score_cutoff=EDIT_AT)
            if best is None:
                bucket, score, match_id = "novel", 0.0, None
                if len(novel_samples) < 12:
                    novel_samples.append({"email_id": str(d["_id"]), "text": cleaned[:280]})
            else:
                score = best[1]
                match_id = orig_ids[best[2]]
                if score >= DUP_AT:
                    bucket = "duplicate"
                else:
                    bucket = "edited"
                    if len(tamper_samples) < 12:
                        tamper_samples.append({
                            "email_id": str(d["_id"]),
                            "similarity": round(score, 1),
                            "matched_original_id": match_id,
                            "text": seg[:280],
                        })
            cache[fp] = (bucket, score, match_id)
            counts[bucket] += 1

    m.close()

    unique_segments = len(cache)
    report = {
        "emails_scanned": len(docs),
        "emails_with_quoted_tail": emails_with_tail,
        "quoted_segments_total": seg_total,
        "unique_segments": unique_segments,
        "buckets_over_all_occurrences": counts,
        "thresholds": {"duplicate_at": DUP_AT, "edited_at": EDIT_AT},
        "novel_samples": novel_samples,
        "tamper_samples": tamper_samples,
    }
    # De-duplicated bucket view (unique passages, not per-occurrence).
    uniq_buckets = {"duplicate": 0, "edited": 0, "novel": 0, "fragment": 0, "boilerplate": 0}
    for bucket, _, _ in cache.values():
        uniq_buckets[bucket] += 1
    report["buckets_unique_passages"] = uniq_buckets

    print("\n" + "=" * 64)
    print("QUOTED-TEXT FUZZY ANALYSIS — FINAL")
    print("=" * 64)
    print(f"  emails with quoted history..... {emails_with_tail:,}")
    print(f"  quoted passages (occurrences).. {seg_total:,}")
    print(f"  unique passages................ {unique_segments:,}")
    print("\n  --- unique passages by bucket ---")
    print(f"  fragment (header/sig scrap).... {uniq_buckets['fragment']:,}")
    print(f"  boilerplate (ECF/disclaimer)... {uniq_buckets['boilerplate']:,}")
    print(f"  duplicate (skip)............... {uniq_buckets['duplicate']:,}")
    print(f"  edited / TAMPER candidate...... {uniq_buckets['edited']:,}")
    print(f"  NOVEL (true must-index)........ {uniq_buckets['novel']:,}")
    print(f"\n  >>> MUST-INDEX COUNT (unique novel): {uniq_buckets['novel']:,}")
    print(f"  >>> TAMPER CANDIDATES to review:     {uniq_buckets['edited']:,}")

    Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n  full report + samples -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
