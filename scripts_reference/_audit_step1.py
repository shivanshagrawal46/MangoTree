"""Step-1 closing audit (free, read-only): verify everything in documents/.
Totals per vendor/type, page methods, dedup integrity, original<->update
linkage, embedded-ProTitle handling, entity linkage, quality flags.
"""
from collections import Counter, defaultdict

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
docs = m.db["documents"]
ents = m.db["entities"]

q = {"source_type": "title_report"}
total = docs.count_documents(q)
print(f"TOTAL title reports: {total}")

# vendor / type
vt = Counter()
methods = Counter()
pages_total = 0
emb = 0
emb_resolved = 0
review = []
vgroups = defaultdict(list)
owners_david = 0
no_owner_ent = 0
props = 0
for d in docs.find(q):
    vt[(d.get("vendor"), "update" if d.get("is_update") else "original")] += 1
    for pg in d.get("pages") or []:
        methods[pg.get("method")] += 1
        pages_total += 1
    if d.get("has_embedded_protitle"):
        emb += 1
        if d.get("embedded_protitle_refs"):
            emb_resolved += 1
    if (d.get("quality") or {}).get("needs_review"):
        review.append(d["_id"])
    vgroups[d.get("version_group")].append(d)
    if d.get("owner_is_david"):
        owners_david += 1
    if not d.get("owner_entity_id"):
        no_owner_ent += 1
    if d.get("property_ids"):
        props += 1

print("\nBy vendor/type:")
for (v, t), n in sorted(vt.items()):
    print(f"  {v:10} {t:8}: {n}")
print(f"\nPages: {pages_total}  methods={dict(methods)}")

# dedup integrity: no two docs share full identity
ids = Counter()
for d in docs.find(q, {"vendor": 1, "address_norm": 1, "order_number": 1, "completed_date": 1,
                       "index_date": 1, "order_type": 1, "search_date": 1,
                       "old_effective_date": 1, "new_effective_date": 1}):
    if d.get("vendor") == "protitle":
        k = ("PT", d.get("address_norm"), d.get("order_number"), d.get("completed_date"), d.get("index_date"))
    else:
        k = ("PW", d.get("address_norm"), d.get("order_type"), d.get("search_date"),
             d.get("old_effective_date"), d.get("new_effective_date"))
    ids[k] += 1
dups = {k: v for k, v in ids.items() if v > 1}
print(f"\nDuplicate identities in DB (must be 0): {len(dups)}")
for k, v in list(dups.items())[:5]:
    print(f"   DUP x{v}: {k}")

# version linkage
multi = {k: v for k, v in vgroups.items() if len(v) > 1}
linked_ok = 0
link_issues = []
for vg, items in multi.items():
    latest = [d for d in items if d.get("is_latest")]
    chained = all((d.get("supersedes") or d.get("superseded_by")) for d in items)
    if len(latest) == 1 and chained:
        linked_ok += 1
    else:
        link_issues.append(vg)
upd_no_group = [d["_id"] for items in vgroups.values() for d in items
                if d.get("is_update") and len(vgroups[d.get("version_group")]) == 1]
print(f"\nProperties (version groups): {len(vgroups)}  multi-version: {len(multi)}  "
      f"correctly chained: {linked_ok}")
if link_issues:
    print(f"  LINK ISSUES: {link_issues[:5]}")
print(f"Updates with NO original in their group (orphan updates): {len(upd_no_group)}")
for i in upd_no_group[:10]:
    print(f"   ORPHAN UPDATE: {i}")

print(f"\nEmbedded ProTitle inside Prowess: {emb} docs (resolved to canonical ref: {emb_resolved})")
print(f"Owner entity linked: {total - no_owner_ent}/{total}  (David-owned: {owners_david})")
print(f"Property entity linked: {props}/{total}")
print(f"entities/: property={ents.count_documents({'kind':'property'})} "
      f"llc={ents.count_documents({'kind':'llc'})} person={ents.count_documents({'kind':'person'})}")
print(f"\nneeds_review: {len(review)}")
for r in review[:10]:
    print(f"   REVIEW: {r}")
m.close()
