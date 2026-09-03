"""Run the hybrid search pipeline on one question and show every stage.

Usage:
    python scripts/search.py "who guaranteed the Chita Ct loan" --property chita_ct
    python scripts/search.py "which properties have a notice of default"        # global
    python scripts/search.py "..." --no-stage2 --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.retrieve.pipeline import HybridSearch
from mangotree.retrieve.scope import Scope
from mangotree.storage.mongo import get_mongo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--property", default=None)
    parser.add_argument("--privileged", action="store_true")
    parser.add_argument("--no-rewrite", action="store_true")
    parser.add_argument("--no-stage2", action="store_true")
    parser.add_argument("--keep", type=int, default=12)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    scope = Scope.for_property(args.property, include_privileged=args.privileged) if args.property \
        else Scope.global_(include_privileged=args.privileged)
    hs = HybridSearch(get_mongo(), voyage_api_key=SETTINGS.voyage_api_key, anthropic_api_key=SETTINGS.anthropic_api_key)
    res = hs.search(args.question, scope, keep=args.keep, use_rewrite=not args.no_rewrite, use_stage2=not args.no_stage2)

    u, rw, t = res.understanding, res.rewrite, res.trace
    print(f"\n  {res.scope}   {res.elapsed_ms} ms")
    print(f"  intent={u.intent} complexity={u.complexity} props={u.property_ids} period={u.date_range.describe() if u.date_range else '-'}")
    print(f"  exact={u.exact_tokens()} filenames={u.filenames}")
    print(f"  rewrite[{rw.model}{' DEGRADED' if rw.degraded else ''}]: {rw.standalone}")
    if rw.hyde:
        print(f"    hyde: {rw.hyde[:140]}…")
    for a in rw.alternates:
        print(f"    alt : {a}")
    if rw.filters:
        print(f"    filters: { {k: (v.date().isoformat() if hasattr(v, 'date') else v) for k, v in rw.filters.items()} }")
    print(f"  route: {res.route_reason}")
    print(f"  lists: {t.get('lists')}")
    print(f"  fused={t.get('fused')} pool={t.get('pool')} rerank={t.get('rerank')}")
    print(f"  expansion={t.get('expansion')}")
    if res.enumeration:
        e = res.enumeration
        print(f"  ENUMERATION: {e.criteria_text} -> {e.denominator}")
        for it in e.items[:12]:
            print(f"      {it['date'] or '----------'}  {str(it['name'])[:60]:<60} {it['property_ids']} {it['placement']}")

    print(f"\n  TOP {len(res.retrieved)} AFTER RERANK")
    for i, h in enumerate(res.retrieved, 1):
        chans = ",".join(sorted(k.split('/')[-1] for k in h.channel_ranks))[:60]
        print(f"  [{i:>2}] r2={h.rerank2_score} r1={h.rerank1_score and round(h.rerank1_score, 3)} "
              f"{h.date_str:<10} {h.citation[:58]:<58} {h.property_ids} {h.placement}")
        if h.rerank2_reason:
            print(f"        why: {h.rerank2_reason}")
        print(f"        via: {chans}")
    print(f"\n  FINAL EVIDENCE SET: {len(res.hits)} chunks, origins="
          f"{ {o: sum(1 for h in res.hits if h.origin == o) for o in ('retrieved','neighbor','parent','thread','fulldoc')} }")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res.as_dict(), f, indent=2, default=str)
        print(f"  written {args.json}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
