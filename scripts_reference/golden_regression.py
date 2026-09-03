"""Sprint 8 · 8.3 — golden-answer regression tests. Locks KNOWN-CORRECT facts
(established from the deep manual test + DB ground truth) so any future change
that silently breaks them is caught. Each case: a question + substrings that
MUST appear in the answer (case-insensitive, OCR/format tolerant on digits).

  python -m scripts.golden_regression
Exit 0 if all pass.
"""
import re
import sys
from collections import Counter
import api.rag_singleton as S

GOLDEN = [
    {"q": "Who owns 59 Beecher Avenue, East Islip?",
     "must": ["IPA Asset Management"], "must_any": ["David", "DeRosa"]},
    {"q": "When did IPA acquire 8 Goose Hill Rd and for how much?",
     "must": ["Goose Hill"], "must_any": ["2018", "400,000", "$400"]},
    {"q": "What is the current title status of 183 Mark Tree Rd, Centereach?",
     "must": ["183MA"], "must_any": ["Rivera", "90%"]},
    {"q": "Is 904 Bayshore Dr, Terra Ceia FL owned by David DeRosa?",
     "must": ["Laney Homes"], "must_any": ["not", "no ", "Virginia"]},
]


def _norm(s):
    return re.sub(r"[\s,]+", " ", (s or "").lower())


def main():
    passed = 0
    for i, g in enumerate(GOLDEN, 1):
        chat = S.make_chat()
        try:
            ans = (chat.ask(g["q"]).answer or "")
        except Exception as exc:  # noqa: BLE001
            ans = f"ERROR {exc}"
        a = _norm(ans)
        ok_must = all(_norm(x) in a for x in g.get("must", []))
        ok_any = (not g.get("must_any")) or any(_norm(x) in a for x in g["must_any"])
        ok = ok_must and ok_any
        passed += int(ok)
        miss = [x for x in g.get("must", []) if _norm(x) not in a]
        print(f"[{i}/{len(GOLDEN)}] {'PASS' if ok else 'FAIL'} :: {g['q'][:50]}"
              + ("" if ok else f"  missing={miss} any_ok={ok_any}"))
        sys.stdout.flush()
    print(f"\nGOLDEN REGRESSION: {passed}/{len(GOLDEN)} passed")
    sys.exit(0 if passed == len(GOLDEN) else 1)


if __name__ == "__main__":
    main()
