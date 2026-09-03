"""HIGH-END Gmail completeness audit — emails + attachments + corpus tagging.

For a label (e.g. "__....Boris Lawsuit"), treats Gmail as the AUTHORITY and
verifies, for every single message:

  1. EMAIL PRESENCE  — the message is in our corpus (matched by RFC822
     Message-ID or by Gmail id).
  2. ATTACHMENT COMPLETENESS — the message's REAL attachments (signature logos /
     tiny inline images excluded, same rule ingestion uses) are all stored: the
     stored attachment_count >= the real attachments Gmail shows.
  3. CORPUS TAGGING — the stored email is tagged legal_correspondence /
     privileged (the lawyer corpus) so it flows into Clean-mode + v2 retrieval.

Outputs a certificate + CSVs of any email gaps and any attachment gaps. Nothing
is written to the DB — this is read-only verification.

Usage:
  python -m scripts.gmail_audit_deep --label "__....Boris Lawsuit"
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.ingest.gmail_client import GmailClient
from scripts.ingest_eml_folder import _is_signature_logo
from src.rag.evidence_schema import CORPUS_LEGAL_CORRESPONDENCE, PRIVILEGE_PRIVILEGED


def _norm_mid(s: str) -> str:
    return (s or "").strip().strip("<>").strip().lower()


def _parse_day(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc) if s else None


def _real_attachments(parts) -> list:
    """Parts that ingestion KEEPS (drop signature logos / tiny inline images)."""
    out = []
    for p in parts:
        is_logo = _is_signature_logo(
            filename=p.get("filename", ""), content_type=p.get("mime", ""),
            size=p.get("size", 0) or 0, disposition=p.get("disposition", ""),
            content_id=p.get("content_id"))
        if not is_logo:
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Deep Gmail completeness audit (read-only).")
    ap.add_argument("--label", required=True)
    ap.add_argument("--after", default=None)
    ap.add_argument("--before", default=None)
    ap.add_argument("--corpus", default=CORPUS_LEGAL_CORRESPONDENCE,
                    help="Expected corpus tag (default legal_correspondence; use "
                         "fraud_communications for the David/AA_Fund folder).")
    ap.add_argument("--privilege", default=PRIVILEGE_PRIVILEGED,
                    help="Expected privilege tag (default privileged; use "
                         "adverse_party for AA_Fund).")
    ap.add_argument("--client-secret", default=None)
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    kwargs = {}
    if args.client_secret:
        kwargs["client_secret_path"] = args.client_secret
    if args.token:
        kwargs["token_path"] = args.token
    client = GmailClient(**kwargs).authenticate()

    label_id = list(client.resolve_labels([args.label]).values())[0]
    after, before = _parse_day(args.after), _parse_day(args.before)

    print(f"Enumerating '{args.label}'…", flush=True)
    ids = list(client.iter_message_ids(label_ids=[label_id], after=after, before=before))
    print(f"Gmail reports {len(ids):,} messages.\n", flush=True)
    if not ids:
        print("Nothing to audit.")
        return 0

    # corpus indexes: key -> {attachment_count, corpus, privilege}
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    by_mid, by_gid = {}, {}
    for d in m.emails.find({}, {"internet_message_id": 1, "source.gmail_id": 1,
                                "gmail_id": 1, "pst_entry_id": 1,
                                "attachment_count": 1, "corpus": 1,
                                "privilege_status": 1}):
        info = {"attachment_count": d.get("attachment_count", 0) or 0,
                "corpus": d.get("corpus"), "privilege": d.get("privilege_status")}
        if d.get("internet_message_id"):
            by_mid[_norm_mid(d["internet_message_id"])] = info
        gid = (d.get("source") or {}).get("gmail_id") or d.get("gmail_id")
        if gid:
            by_gid[gid] = info
        pid = d.get("pst_entry_id") or ""
        if pid.startswith("gmail:"):
            by_gid[pid.split("gmail:", 1)[1]] = info

    missing_email, attach_gap, wrong_corpus = [], [], []
    present = 0
    exp_attach_total = stored_attach_total = 0

    for mid in tqdm(ids, desc="Deep audit", unit="msg"):
        h = client.get_full_summary(mid)
        real = _real_attachments(h.get("parts", []))
        exp_attach_total += len(real)
        info = by_gid.get(mid) or by_mid.get(_norm_mid(h.get("message_id_header")))
        if not info:
            missing_email.append(h)
            continue
        present += 1
        stored = info["attachment_count"]
        stored_attach_total += stored
        if stored < len(real):
            attach_gap.append({"gmail_id": h.get("gmail_id"), "date": h.get("date"),
                               "from": h.get("from"), "subject": h.get("subject"),
                               "expected": len(real), "stored": stored,
                               "files": "; ".join(p["filename"] for p in real)})
        if info.get("corpus") != args.corpus or info.get("privilege") != args.privilege:
            wrong_corpus.append({"gmail_id": h.get("gmail_id"), "subject": h.get("subject"),
                                 "corpus": info.get("corpus"), "privilege": info.get("privilege")})
    m.close()

    base = args.label.replace(" ", "_")
    if missing_email:
        with open(f"deep_missing_emails_{base}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["gmail_id", "date", "from", "subject", "message_id_header"])
            for h in missing_email:
                w.writerow([h.get("gmail_id"), h.get("date"), h.get("from"),
                            h.get("subject"), h.get("message_id_header")])
    if attach_gap:
        with open(f"deep_attachment_gaps_{base}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["gmail_id", "date", "from", "subject",
                                            "expected", "stored", "files"])
            for r in attach_gap:
                w.writerow([r["gmail_id"], r["date"], r["from"], r["subject"],
                            r["expected"], r["stored"], r["files"]])

    print("\n============ DEEP COMPLETENESS AUDIT ============")
    print(f"Label                         : {args.label}")
    print(f"Messages in Gmail (authority) : {len(ids):,}")
    print(f"  EMAILS present in corpus    : {present:,}")
    print(f"  EMAILS missing              : {len(missing_email):,}")
    print(f"Real attachments expected     : {exp_attach_total:,}")
    print(f"Attachments stored (matched)  : {stored_attach_total:,}")
    print(f"  emails with attachment gap  : {len(attach_gap):,}")
    print(f"  emails not tagged {args.corpus:<11}: {len(wrong_corpus):,}")
    ok = not missing_email and not attach_gap
    if missing_email:
        print(f"\n  -> missing emails CSV: deep_missing_emails_{base}.csv")
    if attach_gap:
        print(f"  -> attachment gaps CSV: deep_attachment_gaps_{base}.csv")
    print("\n" + ("CERTIFICATE: every email AND its real attachments are present "
                  "in the corpus." if ok else
                  "NOT COMPLETE - see CSV(s) above; pull/re-extract the gaps."))
    if wrong_corpus and ok:
        print("   (note: some emails not tagged legal_correspondence/privileged — "
              "run tag_chunk_corpus / verify corpus.)")
    print("=" * 49)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
