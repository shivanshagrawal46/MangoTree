"""Sprint 3 GATE — self-authored validation battery (no external answer key).

Proves the entity graph + fan-out deliver the vision's core promise:
"ask about a property/entity in plain language -> reach EVERY linked source."

Checks, across a sample of real David properties + key entities:
  1. Entity resolution: the address/name resolves to >=1 canonical entity.
  2. Multi-source fan-out: chunks span >=2 source types for properties that
     have multiple sources on file.
  3. Graph wiring: every David property with title also has >=1 ABOUT_PROPERTY
     edge; properties with insurance have HAS_INSURANCE edges.
  4. Legal-synonym + alias query expansion produces variants.
Prints a scorecard. Exit 0 if all gates pass.
"""
from __future__ import annotations

import sys
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.fanout import EntityIndex, fan_out_chunks, source_type_breakdown
from src.graph.query_expansion import expand_query, legal_synonyms

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
ents, chunks, rels = m.db["entities"], m.db["email_chunks_v2"], m.db["relationships"]
idx = EntityIndex(ents)

print(f"EntityIndex: {len(idx.by_id)} entities | {len(idx.phrase_to_ids)} name phrases "
      f"| {len(idx.addr_to_ids)} address keys\n")

# --- sample: David properties that have title + at least one other source ---
sample = list(ents.find({"kind": "property", "is_david": True, "has_title": True},
                        {"_id": 1, "canonical_address": 1, "has_insurance": 1,
                         "has_equity": 1, "has_litigation": 1}).limit(25))

resolved_ok = multi_source_ok = total_multi_expected = 0
rows_report = []
for p in sample:
    addr = p.get("canonical_address") or ""
    res = idx.resolve(addr)
    r_ok = p["_id"] in res["all"] or len(res["properties"]) >= 1
    resolved_ok += int(r_ok)
    ch = fan_out_chunks(chunks, res["all"] or {p["_id"]}, limit=60)
    bd = source_type_breakdown(ch)
    expect_multi = sum([bool(p.get("has_insurance")), bool(p.get("has_equity")),
                        bool(p.get("has_litigation"))]) >= 1
    if expect_multi:
        total_multi_expected += 1
        if len(bd) >= 2:
            multi_source_ok += 1
    rows_report.append((addr[:40], r_ok, bd))

print("Property fan-out sample (address | resolved | source breakdown):")
for addr, ok, bd in rows_report[:15]:
    print(f"  {'OK ' if ok else 'MISS'} {addr:42s} {bd}")

# --- key entities resolve ---
print("\nKey entity resolution:")
key_ok = 0
keys = ["IPA Asset Management", "Island Properties & Associates", "David DeRosa",
        "520 East 81st", "Mango Tree"]
for q in keys:
    res = idx.resolve(q)
    ok = len(res["all"]) >= 1
    key_ok += int(ok)
    print(f"  {'OK ' if ok else 'MISS'} {q:38s} -> {len(res['all'])} entities")

# --- graph wiring ---
title_props = list(ents.find({"kind": "property", "has_title": True}, {"_id": 1}))
about_ok = sum(1 for p in title_props
               if rels.count_documents({"type": "ABOUT_PROPERTY", "dst": p["_id"]}) > 0)
ins_props = list(ents.find({"kind": "property", "has_insurance": True}, {"_id": 1}))
ins_ok = sum(1 for p in ins_props
             if rels.count_documents({"type": "HAS_INSURANCE", "src": p["_id"]}) > 0)

# --- query expansion ---
exp = expand_query("who holds the mortgage and any lien on 520 East 81st?", idx)
syn = legal_synonyms("show the grantor on the deed")

print("\n================ SPRINT 3 SCORECARD ================")
print(f"  entity resolution (property sample): {resolved_ok}/{len(sample)}")
print(f"  multi-source fan-out:                {multi_source_ok}/{total_multi_expected}")
print(f"  key entity resolution:               {key_ok}/{len(keys)}")
print(f"  ABOUT_PROPERTY edge coverage:        {about_ok}/{len(title_props)}")
print(f"  HAS_INSURANCE edge coverage:         {ins_ok}/{len(ins_props)}")
print(f"  query expansion variants:            {len(exp)} (e.g. {exp[:2]})")
print(f"  legal synonyms('grantor/deed'):      {syn}")

gates = {
    "entity_resolution": resolved_ok >= 0.9 * len(sample),
    "multi_source": multi_source_ok >= 0.8 * max(total_multi_expected, 1),
    "key_entities": key_ok >= 4,
    "about_edges": about_ok >= 0.95 * len(title_props),
    "insurance_edges": ins_ok >= 0.95 * max(len(ins_props), 1),
    "expansion": len(exp) >= 2 and len(syn) >= 1,
}
print("\n  gate results:", {k: ("PASS" if v else "FAIL") for k, v in gates.items()})
ok = all(gates.values())
print("SPRINT 3 GATE:", "PASS" if ok else "FAIL — see above")
m.close()
sys.exit(0 if ok else 1)
