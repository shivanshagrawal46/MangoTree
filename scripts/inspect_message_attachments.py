"""Show what was kept and what was filtered for a given message subject."""
import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

needle = sys.argv[1] if len(sys.argv) > 1 else "Woodland"
mongo = get_mongo()

emails = list(mongo.artifacts.find({"subject": {"$regex": needle, "$options": "i"}}))
print(f"\n  {len(emails)} email artifact(s) matching {needle!r}\n")

for doc in emails:
    sha = doc.get("sha256", "")
    print(f"  subject   {str(doc.get('subject', ''))[:80]}")
    print(f"  sha       {sha[:16]}   kind={doc.get('kind')}")
    print(f"  property  {doc.get('property_id')}  status={doc.get('resolution_status')}")

    children = list(mongo.artifacts.find(
        {"parent_email_shas": sha},
        {"filename": 1, "size": 1, "likely_logo": 1, "kind": 1},
    ))
    print(f"  child artifacts: {len(children)}")
    for art in children:
        flag = "  [logo/filtered]" if art.get("likely_logo") else ""
        print(f"      {str(art.get('filename', '?'))[:50]:<52} "
              f"{art.get('size', 0):>9,}  {art.get('kind', '')}{flag}")
    print()
