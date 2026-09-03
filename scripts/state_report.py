"""Count every OCR'd page by engine, across both page-array shapes."""
from collections import Counter

from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db

    engines = Counter()
    flags = Counter()
    bad_docs = []

    for a in db.artifacts.find(
        {"extraction": {"$exists": True}},
        {"filename": 1, "folder": 1, "extraction.detail": 1},
    ):
        detail = (a.get("extraction") or {}).get("detail") or {}
        pages = list(detail.get("vision_pages") or []) + list(detail.get("pages") or [])
        offending = []
        for p in pages:
            model = str(p.get("model") or "unknown")
            engines[model] += 1
            for f in ("blocked", "needs_human", "truncated", "escalated"):
                if p.get(f):
                    flags[f] += 1
            if p.get("error"):
                flags["error"] += 1
            low = (p.get("confidence") or 0) < 0.75 and not p.get("is_blank")
            if "rapid" in model.lower() or "local" in model.lower() or p.get("blocked") \
               or p.get("needs_human") or p.get("error") or low:
                offending.append((p.get("page"), model, p.get("confidence"),
                                  p.get("blocked"), p.get("needs_human"), p.get("error")))
        if offending:
            bad_docs.append((a.get("folder"), a.get("filename"), offending))

    print("=== PAGES BY OCR ENGINE (all vision-read pages) ===")
    tot = sum(engines.values())
    for k, v in engines.most_common():
        print(f"  {k:34s} {v:>6,}  ({100.0*v/max(tot,1):.1f}%)")
    print(f"  {'TOTAL':34s} {tot:>6,}")
    print("\n=== PAGE FLAGS ===")
    print("  ", dict(flags) or "none")

    print(f"\n=== DOCUMENTS WITH ANY UNACCEPTABLE / WEAK PAGE: {len(bad_docs)} ===")
    for folder, fn, offs in bad_docs:
        print(f"\n  [{str(folder)[:36]:36s}] {fn}")
        for page, model, conf, blocked, nh, err in offs:
            print(f"      p{page}: model={model} conf={conf} blocked={blocked} needs_human={nh} err={str(err)[:50]}")


if __name__ == "__main__":
    main()
