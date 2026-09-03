"""Why are timeline quotes failing verification?

A 43% rejection rate is either the guardrail working (the model paraphrased) or
the guardrail misfiring (the quote is verbatim but my comparison is too literal).
Those two have opposite fixes, so this measures which it is before anything is
loosened: for each rejected quote it reports whether a progressively more
forgiving normalisation would have found it in the source.
"""
from __future__ import annotations

import difflib
import re
import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo
from mangotree.timeline.extractor import _BLOCK, _FIELD, _SYSTEM, _normalise


def fold(text: str) -> str:
    """Aggressive fold: unify quote/dash variants, then drop punctuation."""
    text = (text or "").lower()
    for a, b in (
        ("\u2018", "'"), ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
        ("\u2013", "-"), ("\u2014", "-"), ("\u2212", "-"), ("\u00a0", " "),
        ("\ufb01", "fi"), ("\ufb02", "fl"),
    ):
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def best_window_ratio(needle: str, haystack: str) -> float:
    """Closest match ratio for the needle anywhere in the haystack."""
    if not needle:
        return 0.0
    size = len(needle)
    best = 0.0
    step = max(1, size // 4)
    for start in range(0, max(1, len(haystack) - size + 1), step):
        window = haystack[start : start + size + 20]
        ratio = difflib.SequenceMatcher(None, needle, window).quick_ratio()
        if ratio > best:
            best = ratio
            if best > 0.98:
                break
    return best


def main() -> None:
    import anthropic

    mongo = get_mongo()
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key)

    artifacts = list(mongo.artifacts.find(
        {"source_type": "disk_file", "property_ids": "varnum",
         "text": {"$exists": True, "$ne": ""}},
        {"sha256": 1, "filename": 1, "text": 1},
    ).limit(4))

    exact_ok = strict_fail_fold_ok = fold_fail_fuzzy_ok = genuine = 0
    samples = []

    for artifact in artifacts:
        body = (artifact.get("text") or "")[:60_000]
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=8000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"<document>\n{body}\n</document>"},
                {"type": "text", "text": "Extract the dated events."},
            ]}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")

        hay_strict = _normalise(body)
        hay_fold = fold(body)

        for block in _BLOCK.finditer(raw):
            fields = {k: v for k, v in _FIELD.findall(block.group(1))}
            quote = (fields.get("quote") or "").strip().strip('"')
            if not quote:
                continue

            if _normalise(quote) in hay_strict:
                exact_ok += 1
                continue
            if fold(quote) in hay_fold:
                strict_fail_fold_ok += 1
                samples.append(("FOLD-RECOVERABLE", artifact["filename"], quote))
                continue
            ratio = best_window_ratio(fold(quote), hay_fold)
            if ratio >= 0.90:
                fold_fail_fuzzy_ok += 1
                samples.append((f"FUZZY {ratio:.2f}", artifact["filename"], quote))
            else:
                genuine += 1
                samples.append((f"NOT FOUND {ratio:.2f}", artifact["filename"], quote))

    total = exact_ok + strict_fail_fold_ok + fold_fail_fuzzy_ok + genuine
    print(f"\n=== QUOTE VERIFICATION AUDIT ({total} quotes, 4 docs) ===")
    print(f"  exact under current matcher      {exact_ok}")
    print(f"  recovered by punctuation fold    {strict_fail_fold_ok}")
    print(f"  recovered by fuzzy >= 0.90       {fold_fail_fuzzy_ok}")
    print(f"  genuinely not in document        {genuine}")

    print("\n--- rejected samples ---")
    for verdict, name, quote in samples[:25]:
        print(f"\n[{verdict}] {name}")
        print(f"  {quote[:220]}")


if __name__ == "__main__":
    main()
