"""Attachment cleanliness pass — classify every stored attachment as KEEP
(real evidence) or NOISE (logos / inline graphics / empty HTML stubs / calendar
parts / redundant winmail.dat), so OCR/embedding only processes real documents.

SAFETY:
  • DRY-RUN by default — writes a report CSV, changes NOTHING.
  • Real-evidence extensions are always kept (incl. xlsm/odt/msg/mp3/dwg/vcf/html
    and MIME-encoded PDFs).
  • Unnamed `attachment_N` / `CID-*` fragments are BYTE-SNIFFED: removed only if
    they are a small image; anything else is kept.
  • `winmail.dat` is only marked removable when its email already has >=1 real
    document stored (contents decoded); otherwise it is KEPT (needs decoding).

  --apply --mode flag    : non-destructive — set skip_extraction=true on noise
  --apply --mode delete  : permanently delete noise attachments (+ GridFS blobs)

Usage:
  python -m scripts.clean_attachments                      # dry-run report
  python -m scripts.clean_attachments --apply --mode flag  # reversible
  python -m scripts.clean_attachments --apply --mode delete
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

LOGO_RE = re.compile(r"^image\d+\.(png|jpe?g|gif|bmp|tif|tiff|webp|emz)$", re.I)

# Always-keep real-evidence extensions.
KEEP_EXT = (".pdf", ".xls", ".xlsx", ".xlsm", ".doc", ".docx", ".csv", ".txt",
            ".rtf", ".ppt", ".pptx", ".tif", ".tiff", ".eml", ".rar", ".zip",
            ".odt", ".msg", ".mp3", ".wav", ".m4a", ".dwg", ".vcf", ".xml")
# Inline-graphic / artifact extensions = noise.
NOISE_EXT = (".gif", ".emz", ".mso", ".ics", ".calendar", ".p7s", ".p7m")

_IMG_MAGIC = [b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"II*\x00", b"MM\x00*"]
TINY_HTML = 1024  # .htm/.html below this = empty forwarded-body stub
# A signature logo is small. Any image >= this is KEPT (could be a pasted
# screenshot / scan of real evidence, e.g. "David's criminal background").
LOGO_MAX = 50 * 1024
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".emz", ".tif", ".tiff")


def _ext(fn: str) -> str:
    low = (fn or "").strip().lower()
    base = low.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return ("." + base.rsplit(".", 1)[-1]) if "." in base else ""


def _sniff_is_small_image(mongo, gridfs_id, size) -> bool:
    if not gridfs_id or (size or 0) > 50 * 1024:
        return False  # too big to be a signature logo
    try:
        head = mongo.gridfs.open_download_stream(gridfs_id).read(8)
    except Exception:  # noqa: BLE001
        return False
    return any(head.startswith(sig) for sig in _IMG_MAGIC)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--mode", choices=("flag", "delete"), default="flag")
    args = ap.parse_args()

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    att, em = m.attachments, m.emails

    rows = list(att.find({}, {"filename": 1, "content_type": 1, "size_bytes": 1,
                              "gridfs_id": 1, "email_id": 1}))
    subj_map = {e["_id"]: e.get("subject") for e in em.find({}, {"subject": 1})}
    logger.info(f"Classifying {len(rows):,} attachments…")

    # pass 1: base classification + which emails have a real doc
    emails_with_realdoc = set()

    def base_cat(a):
        fn = (a.get("filename") or "").strip()
        low = fn.lower()
        ext = _ext(fn)
        if not low:
            return "unnamed"
        if LOGO_RE.match(low):
            return "logo"
        if low == "winmail.dat":
            return "winmail"
        if ext in NOISE_EXT or "text.calendar" in low:
            return "graphic_or_calendar"
        if ext in (".htm", ".html"):
            return "html_big" if (a.get("size_bytes") or 0) >= TINY_HTML else "html_stub"
        if ext in KEEP_EXT:
            return "real_doc"
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            return "real_doc"  # non-logo image = likely a real scan/photo
        if low.startswith("attachment_") or low.startswith("cid-"):
            return "unnamed"
        if "=?" in fn:  # MIME-encoded name (e.g. encoded PDF) = keep
            return "real_doc"
        return "unknown_keep"

    cat0 = {}
    for a in rows:
        c = base_cat(a)
        cat0[a["_id"]] = c
        if c in ("real_doc", "html_big", "unknown_keep"):
            emails_with_realdoc.add(a.get("email_id"))

    # pass 2: finalize (winmail redundancy + sniff unnamed)
    final = {}
    counts = Counter()
    delete_rows = []
    for a in rows:
        c = cat0[a["_id"]]
        keep = True
        reason = ""
        if c == "real_doc" or c == "html_big" or c == "unknown_keep":
            keep, reason = True, "real evidence"
        elif c == "logo":
            if (a.get("size_bytes") or 0) >= LOGO_MAX:
                keep, reason = True, "large image00N (>=50KB) — possible inline evidence, KEPT for OCR/review"
            else:
                keep, reason = False, "signature logo (small inline image)"
        elif c == "graphic_or_calendar":
            if _ext(a.get("filename")) in _IMG_EXTS and (a.get("size_bytes") or 0) >= LOGO_MAX:
                keep, reason = True, "large graphic (>=50KB) — KEPT for review"
            else:
                keep, reason = False, "inline graphic / calendar / signature"
        elif c == "html_stub":
            keep, reason = False, "empty forwarded-body HTML stub"
        elif c == "winmail":
            if a.get("email_id") in emails_with_realdoc:
                keep, reason = False, "winmail.dat — contents already stored (redundant)"
            else:
                keep, reason = True, "winmail.dat — KEEP (contents not yet decoded)"
        elif c == "unnamed":
            if _sniff_is_small_image(m, a.get("gridfs_id"), a.get("size_bytes")):
                keep, reason = False, "unnamed small image (inline logo)"
            else:
                keep, reason = True, "unnamed non-image — KEEP (inspect)"
        else:
            keep, reason = True, "keep (default-safe)"

        final[a["_id"]] = keep
        counts[("KEEP" if keep else "REMOVE", reason)] += 1
        if not keep:
            delete_rows.append({"filename": a.get("filename"), "reason": reason,
                                "size": a.get("size_bytes"),
                                "subject": (subj_map.get(a.get("email_id")) or "")[:60]})

    keep_n = sum(1 for v in final.values() if v)
    rm_n = len(final) - keep_n
    print("\n================ ATTACHMENT CLEANLINESS (dry-run) ================")
    print(f"Total attachments : {len(rows):,}")
    print(f"  KEEP            : {keep_n:,}")
    print(f"  REMOVE (noise)  : {rm_n:,}")
    print("\n-- breakdown --")
    for (kind, reason), n in sorted(counts.items(), key=lambda x: (-x[1])):
        print(f"  [{kind:6}] {reason:48} {n:>6,}")

    out = Path("attachment_cleanup_candidates.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "reason", "size", "subject"])
        w.writeheader(); w.writerows(delete_rows)
    print(f"\nRemoval candidates listed in: {out.resolve()}")

    if not args.apply:
        print("\nDRY-RUN — nothing changed. Re-run with --apply --mode flag|delete.")
        m.close(); return 0

    # APPLY
    rm_ids = [aid for aid, keep in final.items() if not keep]
    if args.mode == "flag":
        att.update_many({"_id": {"$in": rm_ids}},
                        {"$set": {"skip_extraction": True, "noise": True}})
        print(f"\nFLAGGED {len(rm_ids):,} attachments skip_extraction=true (reversible).")
    else:
        gfids = [a["gridfs_id"] for a in rows if not final[a["_id"]] and a.get("gridfs_id")]
        for gid in gfids:
            try:
                m.gridfs.delete(gid)
            except Exception:  # noqa: BLE001
                pass
        att.delete_many({"_id": {"$in": rm_ids}})
        print(f"\nDELETED {len(rm_ids):,} noise attachments (+ {len(gfids):,} blobs).")
    m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
