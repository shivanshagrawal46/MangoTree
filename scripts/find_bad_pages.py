"""Which page arrays hold the pages that still need a permitted-engine read?"""
from mangotree.storage.mongo import get_mongo

FORBIDDEN = ("rapidocr", "local")


def main() -> None:
    db = get_mongo().db
    for a in db.artifacts.find(
        {"extraction": {"$exists": True}},
        {"filename": 1, "extraction.method": 1, "extraction.detail": 1},
    ):
        ex = a.get("extraction") or {}
        detail = ex.get("detail") or {}
        for array_name in ("vision_pages", "pages"):
            pages = detail.get(array_name) or []
            bad = []
            for p in pages:
                model = str(p.get("model") or "")
                if any(f in model.lower() for f in FORBIDDEN):
                    bad.append((p.get("page"), model, "FORBIDDEN ENGINE"))
                elif p.get("needs_human"):
                    bad.append((p.get("page"), model, f"needs_human conf={p.get('confidence')}"))
                elif not p.get("text") and not p.get("is_blank"):
                    bad.append((p.get("page"), model, "empty text"))
            if bad:
                print(f"\n{a['filename']}")
                print(f"   method={ex.get('method')}  array={array_name}")
                for page, model, why in bad:
                    print(f"     p{page}: {model} — {why}")


if __name__ == "__main__":
    main()
