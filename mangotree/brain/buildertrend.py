"""Buildertrend — the contractor's own record of each job: the daily site logs, their
photos, and the bills.

Buildertrend is ROI Blocks' account, not RKB's. What is in it is what the
contractor wrote: a daily log says what the crew reported doing, a bill says what
the contractor is asking to be paid. Neither is RKB's inspection or RKB's
approval, and the model is told so.

How it gets here: a pull (job list, every daily log, every bill, the bills in
review with their line items, recent photos) is handed over by the browser and
loaded piece by piece (``import_jobs``, ``import_logs``, ``import_bills``,
``add_photo``). Loading is plain copying — no model reads anything.

WHERE THE PHOTOS LIVE: on the company's own server storage (DigitalOcean Spaces)
and nowhere else. ``add_photo`` uploads straight from memory; nothing is written
to the disk of the machine doing the pull, and there is no local cache. Without
the storage keys a photo is refused rather than kept on a laptop. The text
(jobs, logs, bills) goes into the database, which is on the server already. What the model then sees is ``site_block``: the recent logs word for
word and the bills in review line by line, placed with THE MAIL and THE FILES
in every answer, draft and update.

Collections: ``bt_jobs``, ``bt_daily_logs``, ``bt_bills``, ``bt_photos``, ``bt_pulls``.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from .store import active_property_ids, name_of, now

SITE_DAYS = 30                # daily logs shown word for word to the model
SITE_BLOCK_CHARS = 120_000
_STREET_WORDS = {"street", "road", "drive", "place", "lane", "court", "avenue", "north", "south", "east", "west", "washington", "baltimore"}
# The bill statuses as Buildertrend's own tabs group them.
IN_REVIEW, READY, PAID = "In review", "Ready for payment", "Paid"
# The contractor enters each draw as two bills: "... (Contractor)" for 90% and "... (Hold-back)"
# for the 10% RKB retains from every draw until the work is complete (Construction Rider,
# section 5, "Retainage"). A hold-back bill is money being held, not a bill waiting to be paid.
HOLDBACK = re.compile(r"hold[\s-]?back|retainage", re.I)


def is_holdback(title: Any) -> bool:
    return bool(HOLDBACK.search(str(title or "")))


class ServerStorageMissing(RuntimeError):
    """The server's storage cannot be reached from this machine: its keys are not set."""


_PHOTO_STORE: Dict[str, Any] = {}


def photo_store():
    """The server storage for site photos — DigitalOcean Spaces, with NO local cache —
    or None when this machine has not been given the keys (DO_SPACES_KEY,
    DO_SPACES_SECRET, DO_SPACES_BUCKET, optional DO_SPACES_REGION / DO_SPACES_ENDPOINT)."""
    if "store" not in _PHOTO_STORE:
        import os
        key, secret, bucket = (os.environ.get(k, "").strip() for k in ("DO_SPACES_KEY", "DO_SPACES_SECRET", "DO_SPACES_BUCKET"))
        if key and secret and bucket:
            from mangotree.storage.objectstore import SpacesObjectStore
            _PHOTO_STORE["store"] = SpacesObjectStore(key=key, secret=secret, bucket=bucket, region=os.environ.get("DO_SPACES_REGION", "nyc3"),
                                                      endpoint=os.environ.get("DO_SPACES_ENDPOINT") or None, cache_root=None)
        else:
            _PHOTO_STORE["store"] = None
    return _PHOTO_STORE["store"]


def _dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00")[:26].rstrip("."))
    except ValueError:
        try:
            d = datetime.fromisoformat(str(v)[:19])
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (s or "").lower()) if w not in _STREET_WORDS}


def match_property(street: str) -> Optional[str]:
    """A Buildertrend job belongs to the property with the same house number and a
    shared street word ("24333 Narrow Gauge Rd" is "24333 Narrow Guage")."""
    m = re.match(r"\s*(\d+)\s+(.*)", street or "")
    if not m:
        return None
    number, words = m.group(1), _words(m.group(2))
    for pid in active_property_ids():
        pm = re.match(r"\s*(\d+)\s+(.*)", name_of(pid))
        if pm and pm.group(1) == number and (words & _words(pm.group(2)) or not words):
            return pid
    return None


def _money(v: Any) -> Optional[float]:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return round(float(v), 2) if v is not None else None
    except (TypeError, ValueError):
        return None


def usd(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:,.2f}"


class Buildertrend:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        db = mongo.db
        self.jobs, self.logs, self.bills, self.photos, self.pulls = db["bt_jobs"], db["bt_daily_logs"], db["bt_bills"], db["bt_photos"], db["bt_pulls"]
        self.estimates = db["bt_estimates"]

    def ensure_indexes(self) -> None:
        self.logs.create_index([("property_id", 1), ("date", -1)])
        self.logs.create_index("log_id")
        self.bills.create_index([("property_id", 1), ("invoice_date", -1)])
        self.bills.create_index("bill_id")
        self.photos.create_index("file_id")
        self.photos.create_index("job_id")
        self.jobs.create_index("property_id")
        self.estimates.create_index("property_id")
        self.estimates.create_index("job_id")

    # ------------------------------------------------------------------ loading
    def _job_pid(self) -> Dict[int, str]:
        return {int(j["job_id"]): j["property_id"] for j in self.jobs.find({"property_id": {"$ne": None}}, {"job_id": 1, "property_id": 1})}

    def import_jobs(self, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        from pymongo import UpdateOne
        ops, out = [], {"jobs": 0, "matched": 0, "unmatched": []}
        for j in jobs:
            pid = match_property(j.get("street") or "")
            doc = {"job_id": j["jobId"], "builder_id": j.get("builderId"), "name": (j.get("jobName") or "").strip(), "street": j.get("street"),
                   "city_state_zip": j.get("cityStateZip"), "owner_name": (j.get("ownerName") or "").strip() or None, "property_id": pid, "seen_at": now()}
            ops.append(UpdateOne({"job_id": j["jobId"]}, {"$set": doc}, upsert=True))
            out["jobs"] += 1
            if pid:
                out["matched"] += 1
            elif j.get("street"):
                out["unmatched"].append(doc["name"])
        if ops:
            self.jobs.bulk_write(ops, ordered=False)
        return out

    def import_logs(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Load the daily logs. Returns how many logs each property got that were not here
        before — those properties are the ones whose site-work reading is now out of date."""
        from pymongo import UpdateOne
        job_pid = self._job_pid()
        pulled_at = _dt(d.get("pulled_at")) or now()
        known = {l["log_id"] for l in self.logs.find({}, {"log_id": 1})}
        ops, new_by_pid, n = [], {}, 0
        for job, rows in (d.get("logs") or {}).items():
            pid = job_pid.get(int(job))
            if not pid:
                continue
            for r in rows:
                photos = [{"file_id": i["id"], "title": i.get("title"), "size": i.get("fileSize")} for i in r.get("images") or [] if i.get("isPhoto")]
                files = [{"file_id": i["id"], "title": i.get("title"), "ext": i.get("extension")} for i in r.get("files") or [] if not i.get("isPhoto")]
                ops.append(UpdateOne({"log_id": r["dailyLogId"]}, {"$set": {
                    "log_id": r["dailyLogId"], "job_id": int(job), "property_id": pid, "date": _dt(r.get("logDate")), "title": (r.get("logTitle") or "").strip(),
                    "notes": (r.get("logNotes") or "").strip(), "by": (r.get("addedBy") or "").strip(), "tags": [t.get("name") if isinstance(t, dict) else t for t in r.get("tags") or []],
                    "weather": r.get("weather"), "weather_notes": (r.get("weatherNotes") or "").strip(), "comments": r.get("commentCount") or 0,
                    "photos": photos, "files": files, "pulled_at": pulled_at}}, upsert=True))
                n += 1
                if r["dailyLogId"] not in known:
                    new_by_pid[pid] = new_by_pid.get(pid, 0) + 1
        if ops:
            self.logs.bulk_write(ops, ordered=False)
        self.ensure_indexes()
        # The pull is recorded with the properties that have new logs and have not been read again yet.
        self.pulls.insert_one({"at": pulled_at, "loaded_at": now(), "logs": n, "new_logs": new_by_pid, "analysed": not new_by_pid})
        self.__dict__.pop("_views", None)
        return {"logs": n, "new_logs": new_by_pid, "pulled_at": pulled_at}

    def import_bills(self, d: Dict[str, Any]) -> Dict[str, Any]:
        from pymongo import UpdateOne
        job_pid = self._job_pid()
        bills_at = _dt(d.get("pulled_at")) or now()
        details = d.get("details") or {}
        out = {"bills": 0, "in_review": 0}
        for job, v in (d.get("bills") or {}).items():
            pid = job_pid.get(int(job))
            if not pid:
                continue
            review = set(v.get("inReviewIds") or [])
            ops, seen = [], []
            for r in v.get("rows") or []:
                doc = self._bill(r, pid, r["id"] in review, details.get(str(r["id"])))
                doc["pulled_at"] = bills_at
                ops.append(UpdateOne({"bill_id": r["id"]}, {"$set": doc}, upsert=True))
                seen.append(r["id"])
                out["bills"] += 1
                out["in_review"] += int(doc["in_review"])
            if ops:
                self.bills.bulk_write(ops, ordered=False)
            # A bill that has left Buildertrend (deleted or voided there) leaves here too.
            self.bills.delete_many({"job_id": int(job), "bill_id": {"$nin": seen}})
        self.__dict__.pop("_views", None)
        return out

    @staticmethod
    def _detail_fields(detail: Dict[str, Any]) -> Dict[str, Any]:
        """What a bill's own page adds to its row in the list: who entered it, and its lines.
        Every bill carries the WHOLE proposal — one line per proposal item and per change
        order — with the line's price (unit cost) and the share of it this bill claims
        (quantity, a fraction) and the money that share comes to (amount)."""
        return {
            "created_by": detail.get("createdByName"),
            "description": ((detail.get("description") or {}).get("value") or "").strip(),
            "line_items": [{
                "title": (li.get("title") or "").strip(), "description": (li.get("description") or "").strip(), "cost_code": (li.get("costCodeTitle") or "").strip(),
                "quantity": li.get("quantity"), "unit_cost": _money(li.get("unitCost")), "amount": _money(li.get("builderCost") if li.get("builderCost") is not None else li.get("calculatedAmount")),
                "variance": None if (li.get("varianceCodeTitle") or "").startswith("--") else li.get("varianceCodeTitle"),
            } for li in ((detail.get("lineItems") or {}).get("value") or [])],
            "file_names": [f.get("title") for f in ((detail.get("attachedFiles") or {}).get("files") or []) if f.get("title")][:40],
        }

    def import_bill_details(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """The line items of bills already on file (a pull of bill pages on their own, used
        to fill in the lines of bills that were first read from the list only)."""
        from pymongo import UpdateOne
        at = _dt(d.get("pulled_at")) or now()
        ops = []
        for bid, detail in (d.get("details") or {}).items():
            if isinstance(detail, dict) and (detail.get("lineItems") or {}).get("value") is not None:
                ops.append(UpdateOne({"bill_id": int(bid)}, {"$set": {**self._detail_fields(detail), "lines_pulled_at": at}}))
        matched = self.bills.bulk_write(ops, ordered=False).matched_count if ops else 0
        self.__dict__.pop("_views", None)
        return {"details": len(ops), "matched": matched}

    # ------------------------------------------------------------------ the proposal (Buildertrend's Estimate)
    @staticmethod
    def _estimate_line(li: Dict[str, Any]) -> Dict[str, Any]:
        """One line of the proposal. ``builder_cost`` is what the work costs; ``price`` is what
        the homeowner is charged for it; the difference is the markup on that line."""
        cost, price = _money(li.get("builderCost")), _money(li.get("ownerPrice"))
        return {
            "line_id": li.get("id"), "title": (li.get("itemTitle") or "").strip(), "cost_code": (li.get("costCodeTitle") or "").strip(),
            "description": (li.get("description") or "").strip(), "notes": (li.get("internalNotes") or "").strip(),
            "quantity": li.get("quantity"), "unit": (li.get("unit") or "").strip(), "unit_cost": _money(li.get("unitCost")),
            "builder_cost": cost, "markup": _money(li.get("markupAmount")), "markup_percent": li.get("markupPercent"), "price": price,
            "invoiced": _money(li.get("amountInvoiced")), "has_purchase_order": bool(li.get("hasRelatedPurchaseOrder")),
            "po_line_id": li.get("relatedPurchaseOrderLineItemId"), "line_type": li.get("lineItemType"), "added": _dt(li.get("dateAdded")),
            "order": li.get("worksheetDisplayOrder"),
        }

    def _estimate_groups(self, groups: List[Dict[str, Any]], parent: str = "") -> List[Dict[str, Any]]:
        out = []
        for g in sorted(groups or [], key=lambda x: x.get("displayOrder") or 0):
            title = (g.get("title") or "").strip()
            lines = sorted((self._estimate_line(li) for li in g.get("lineItems") or []), key=lambda x: x.get("order") or 0)
            out.append({"title": title, "parent": parent or None, "description": (g.get("description") or "").strip(), "lines": lines})
            out.extend(self._estimate_groups(g.get("subGroups") or [], title))
        return out

    def import_estimates(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """The proposal of each job, as Buildertrend's Estimate page holds it: every line with
        its cost code, builder cost, markup and price to the homeowner. One record per job,
        replaced whole on each pull (the estimate is edited in place until it is locked)."""
        from pymongo import UpdateOne
        job_pid = self._job_pid()
        at = _dt(d.get("pulled_at")) or now()
        ops, out = [], {"estimates": 0, "lines": 0, "empty": [], "unmatched": []}
        for job, w in (d.get("estimates") or {}).items():
            pid = job_pid.get(int(job))
            if not pid:
                out["unmatched"].append(int(job))
                continue
            groups = self._estimate_groups(w.get("formatData") or [])
            lines = [li for g in groups for li in g["lines"]]
            if not lines:
                out["empty"].append(pid)
            cost = round(sum(li["builder_cost"] or 0 for li in lines), 2)
            price = round(sum(li["price"] or 0 for li in lines), 2)
            doc = {
                "job_id": int(job), "property_id": pid, "worksheet_id": w.get("worksheetId"), "pulled_at": at,
                "locked": bool(w.get("worksheetLocked")), "locked_by": w.get("lockedByName"), "locked_at": _dt(w.get("lockedByDate")),
                "sent_to_budget": bool(w.get("isSentToBudget")), "status_code": w.get("proposalStatus"), "created_by": w.get("createdBy"),
                "created": _dt(w.get("createdByDate")), "default_markup_percent": w.get("defaultMarkup"),
                "contract_price": _money(w.get("contractPrice")), "remaining_contract_price": _money(w.get("remainingContractPrice")),
                "already_invoiced": _money(w.get("alreadyInvoicedAmount")),
                "totals": {"builder_cost": cost, "price": price, "markup": round(price - cost, 2), "lines": len(lines)},
                "groups": groups,
            }
            ops.append(UpdateOne({"job_id": int(job)}, {"$set": doc}, upsert=True))
            out["estimates"] += 1
            out["lines"] += len(lines)
        if ops:
            self.estimates.bulk_write(ops, ordered=False)
        self.__dict__.pop("_views", None)
        return out

    def estimate_of(self, pid: str) -> Optional[Dict[str, Any]]:
        return self.estimates.find_one({"property_id": pid}, {"_id": 0})

    def add_photo(self, file_id: int, job_id: int, blob: bytes) -> bool:
        """Put one photo on the server's storage, straight from memory. Returns False if it was there already."""
        store = photo_store()
        if store is None:
            raise ServerStorageMissing("Site photos are kept on the server's storage only, and this machine does not have its keys "
                                       "(DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_BUCKET in .env). The photo was not stored anywhere.")
        sha = hashlib.sha256(blob).hexdigest()
        had = self.photos.find_one({"file_id": int(file_id), "where": "server"}, {"_id": 1})
        if not store.exists(sha):
            store.put(sha, blob, f"buildertrend-{int(file_id)}.jpg", {"content_type": "image/jpeg", "source": "buildertrend"})
        self.photos.update_one({"file_id": int(file_id)}, {"$set": {"file_id": int(file_id), "job_id": int(job_id), "sha256": sha, "bytes": len(blob), "where": "server", "stored_at": now()},
                                                           "$setOnInsert": {"added_at": now()}}, upsert=True)
        self.__dict__.pop("_views", None)
        return not had

    def import_pull(self, folder: Path) -> Dict[str, Any]:
        """Load a pull that was saved to a folder (the first pulls were). Photos in it go to the server's storage."""
        folder = Path(folder)
        out: Dict[str, Any] = self.import_jobs(json.loads((folder / "jobs.json").read_text(encoding="utf-8")))
        if (folder / "daily_logs.json").exists():
            out.update(self.import_logs(json.loads((folder / "daily_logs.json").read_text(encoding="utf-8"))))
        if (folder / "bills.json").exists():
            out.update(self.import_bills(json.loads((folder / "bills.json").read_text(encoding="utf-8"))))
        out["photos"] = self.import_photos(folder)
        return out

    def import_photos(self, folder: Path) -> int:
        """Send the photos saved under ``<folder>/photos/<job id>/<file id>.jpg`` to the server's storage."""
        pdir = Path(folder) / "photos"
        if not pdir.exists():
            return 0
        on_server = {p["file_id"] for p in self.photos.find({"where": "server"}, {"file_id": 1})}
        added = 0
        for path in pdir.glob("*/*.jpg"):
            fid = int(path.stem)
            if fid not in on_server:
                added += int(self.add_photo(fid, int(path.parent.name), path.read_bytes()))
        return added

    # ------------------------------------------------- the photos that are still on a laptop
    def _local_bytes(self, doc: Dict[str, Any], folder: Optional[Path] = None) -> Optional[bytes]:
        try:
            from mangotree.storage.objectstore import LocalObjectStore
            from mangotree.config.settings import SETTINGS
            blob = LocalObjectStore(SETTINGS.raw_store).get(doc["sha256"])
            if blob:
                return blob
        except Exception:
            pass
        if folder:
            f = Path(folder) / "photos" / str(doc.get("job_id")) / f"{doc['file_id']}.jpg"
            if f.exists():
                return f.read_bytes()
        return None

    def photos_status(self) -> Dict[str, Any]:
        on_server = self.photos.count_documents({"where": "server"})
        total = self.photos.count_documents({})
        return {"photos": total, "on_server": on_server, "on_this_machine_only": total - on_server, "server_storage_reachable": photo_store() is not None}

    def move_photos_to_server(self, folder: Optional[Path] = None) -> Dict[str, Any]:
        """Upload every photo that is only on this machine to the server's storage, and check each one arrived."""
        store = photo_store()
        if store is None:
            raise ServerStorageMissing("This machine does not have the keys to the server's storage (DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_BUCKET in .env).")
        moved = missing = failed = 0
        for d in list(self.photos.find({"where": {"$ne": "server"}}, {"_id": 0})):
            blob = self._local_bytes(d, folder)
            if blob is None:
                missing += 1
                continue
            try:
                store.put(d["sha256"], blob, f"buildertrend-{d['file_id']}.jpg", {"content_type": "image/jpeg", "source": "buildertrend"})
                if not store.exists(d["sha256"]):
                    raise RuntimeError("not found on the server after upload")
                self.photos.update_one({"file_id": d["file_id"]}, {"$set": {"where": "server", "stored_at": now()}})
                moved += 1
            except Exception as exc:
                failed += 1
                logger.warning("photo %s could not be moved to the server: %s", d.get("file_id"), exc)
        self.__dict__.pop("_views", None)
        return {"moved": moved, "not_found_on_this_machine": missing, "failed": failed, **self.photos_status()}

    def delete_local_copies(self, folder: Optional[Path] = None) -> Dict[str, Any]:
        """Remove this machine's copies of the photos — ONLY those confirmed present on the server just now."""
        store = photo_store()
        if store is None:
            raise ServerStorageMissing("Cannot confirm the server copies from this machine; nothing was deleted.")
        from mangotree.storage.objectstore import LocalObjectStore
        from mangotree.config.settings import SETTINGS
        local = LocalObjectStore(SETTINGS.raw_store)
        removed = kept = 0
        for d in self.photos.find({"where": "server"}, {"_id": 0}):
            if not store.exists(d["sha256"]):
                kept += 1                       # not confirmed on the server: its local copy stays
                continue
            for f in ([local._path(d["sha256"])] + ([Path(folder) / "photos" / str(d.get("job_id")) / f"{d['file_id']}.jpg"] if folder else [])):
                try:
                    if Path(f).exists():
                        Path(f).unlink()
                        removed += 1
                except Exception as exc:
                    logger.warning("could not remove %s: %s", f, exc)
        return {"files_removed": removed, "photos_kept_because_not_confirmed_on_server": kept}

    def latest_photos(self, pid: str, *, limit: int = 12) -> List[Dict[str, Any]]:
        """The newest photos we hold for the property, newest log first, each with its log's date and title."""
        jobs = [j["job_id"] for j in self.jobs.find({"property_id": pid}, {"job_id": 1})]
        if not jobs:
            return []
        have = {p["file_id"] for p in self.photos.find({"job_id": {"$in": jobs}}, {"file_id": 1})}
        out: List[Dict[str, Any]] = []
        for l in self.logs.find({"property_id": pid, "photos.0": {"$exists": True}}, {"_id": 0, "date": 1, "title": 1, "photos.file_id": 1}).sort("date", -1).limit(40):
            for p in l.get("photos") or []:
                if p["file_id"] in have:
                    out.append({"photo_id": p["file_id"], "date": self._day(l.get("date")), "title": l.get("title")})
                    if len(out) >= limit:
                        return out
        return out

    @staticmethod
    def _bill(r: Dict[str, Any], pid: str, in_review: bool, detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        code = (r.get("purchaseOrderPaymentStatus") or {}).get("paymentStatus")
        amount, paid, balance = _money(r.get("paymentAmount")), _money(r.get("amountPaid")), _money(r.get("remainingBalance"))
        if in_review:
            status = IN_REVIEW
        elif code == 2 or (r.get("datePaid") and not balance):
            status = PAID
        elif code == 1:
            status = READY
        elif paid and balance:
            status = "Partly paid"
        else:
            status = f"Other (Buildertrend status {code})"
        due = r.get("dueDate") or {}
        doc: Dict[str, Any] = {
            "bill_id": r["id"], "job_id": r.get("jobId"), "property_id": pid, "number": (r.get("billNumberLink") or {}).get("title"),
            "title": (r.get("billTitleLink") or {}).get("title"), "pay_to": (r.get("payTo") or {}).get("payTo"), "kind": r.get("documentType") or "Bill",
            "amount": amount, "paid": paid, "balance": balance, "status": status, "status_code": code, "in_review": in_review,
            "holdback": is_holdback((r.get("billTitleLink") or {}).get("title")),
            "invoice_date": _dt(r.get("invoicedDate")), "due_date": _dt(due.get("date")), "past_due": bool(due.get("isPastDue")), "date_paid": _dt(r.get("datePaid")),
            "created": _dt(r.get("createdDate")), "cost_codes": r.get("costCodes") or [], "variance_codes": r.get("varianceCodes") or [],
            "approvers": [a.get("approverName") for a in ((r.get("approvers") or {}).get("approvers") or []) if a.get("approverName")],
            "purchase_orders": [p.get("title") for p in r.get("relatedPurchaseOrders") or [] if p.get("title")], "documents": (r.get("attachedFiles") or {}).get("docCount") or 0,
        }
        if detail:
            doc.update(Buildertrend._detail_fields(detail))
        return doc

    # ------------------------------------------------------------------ reading
    def last_pull(self) -> Optional[datetime]:
        p = self.pulls.find_one({}, {"at": 1}, sort=[("at", -1)])
        if p:
            return p["at"]
        # A load that has not finished yet has still stamped what it has brought in.
        l = self.logs.find_one({"pulled_at": {"$ne": None}}, {"pulled_at": 1}, sort=[("pulled_at", -1)])
        return l["pulled_at"] if l else None

    def logs_of(self, pid: str, *, days: Optional[int] = None, limit: int = 0) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"property_id": pid}
        if days:
            q["date"] = {"$gte": now() - timedelta(days=days)}
        cur = self.logs.find(q, {"_id": 0}).sort("date", -1)
        return list(cur.limit(limit) if limit else cur)

    def bills_of(self, pid: str, *, in_review: Optional[bool] = None) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"property_id": pid}
        if in_review is not None:
            q["in_review"] = in_review
        return list(self.bills.find(q, {"_id": 0}).sort("invoice_date", -1))

    def bill_totals(self, pid: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"count": 0, "billed": 0.0, "paid": 0.0, "by_status": {}}
        for b in self.bills.find({"property_id": pid}, {"amount": 1, "paid": 1, "status": 1, "balance": 1}):
            out["count"] += 1
            out["billed"] += b.get("amount") or 0
            out["paid"] += b.get("paid") or 0
            s = out["by_status"].setdefault(b.get("status") or "?", {"count": 0, "amount": 0.0})
            s["count"] += 1
            s["amount"] += (b.get("balance") if b.get("status") != PAID else b.get("amount")) or 0
        out["billed"], out["paid"] = round(out["billed"], 2), round(out["paid"], 2)
        for s in out["by_status"].values():
            s["amount"] = round(s["amount"], 2)
        return out

    def summary(self, pid: str) -> Dict[str, Any]:
        first = self.logs.find_one({"property_id": pid}, {"date": 1}, sort=[("date", 1)])
        last = self.logs.find_one({"property_id": pid}, {"date": 1}, sort=[("date", -1)])
        job = self.jobs.find_one({"property_id": pid}, {"_id": 0})
        return {"in_buildertrend": bool(job), "job": job, "logs_total": self.logs.count_documents({"property_id": pid}),
                "first_log": first and first.get("date"), "last_log": last and last.get("date"), "pulled_at": self.last_pull()}

    # --------------------------------------------------------- what the model reads
    @staticmethod
    def render_bill(b: Dict[str, Any], *, lines: bool = True) -> str:
        d = lambda x: x.strftime("%Y-%m-%d") if x else "no date"  # noqa: E731
        head = (f"- {b.get('number') or 'bill'} “{b.get('title') or ''}” — pay to {b.get('pay_to') or 'not stated'} — {usd(b.get('amount'))}"
                f" — invoiced {d(b.get('invoice_date'))}, due {d(b.get('due_date'))}{' (PAST DUE)' if b.get('past_due') else ''}"
                + (f" — approver named in Buildertrend: {', '.join(b['approvers'])}" if b.get("approvers") else "")
                + (f" — entered by {b['created_by']}" if b.get("created_by") else ""))
        out = [head]
        if lines:
            for li in b.get("line_items") or []:
                out.append(f"    · {li.get('title') or li.get('cost_code') or 'line'} — {usd(li.get('amount'))} [{li.get('cost_code') or 'no cost code'}]"
                           + (f" — variance: {li['variance']}" if li.get("variance") else "") + (f" — {li['description'][:300]}" if li.get("description") else ""))
            if b.get("file_names"):
                out.append(f"    (documents attached to the bill in Buildertrend, not read here: {'; '.join(b['file_names'][:12])})")
        return "\n".join(out)

    def site_block(self, pid: str, *, days: int = SITE_DAYS) -> str:
        """The contractor's site diary and bills, as the model reads them."""
        s = self.summary(pid)
        if not s["in_buildertrend"]:
            return ""
        d = lambda x, f="%Y-%m-%d": x.strftime(f) if x else "?"  # noqa: E731
        out = [f"\n=== THE SITE — Buildertrend daily logs for {name_of(pid)}, last {days} days, word for word, oldest first ===",
               f"(Buildertrend is the contractor's own system. A daily log is what the crew or the contractor's staff wrote that day — it is their "
               f"report, not RKB's inspection. Buildertrend was last read on {d(s['pulled_at'], '%Y-%m-%d %H:%M')} UTC; anything written there since is not "
               f"here. {s['logs_total']} logs on file in all, from {d(s['first_log'])} to {d(s['last_log'])}. Cite a log as “Buildertrend daily log of <date>”.)"]
        recent = self.logs_of(pid, days=days)[::-1]
        if not recent:
            if s["last_log"]:
                # A quiet diary is itself information; its last entries say where the work was left.
                recent = self.logs_of(pid, limit=3)[::-1]
                out.append(f"(NO daily log in the last {days} days. The site diary stops on {d(s['last_log'])}. Its last {len(recent)} entries follow.)")
            else:
                out.append("(No daily log has ever been written for this job.)")
        parts = []
        for l in recent:
            parts.append(f"[{d(l.get('date'))}] “{l.get('title') or 'untitled'}” — written by {l.get('by') or 'unknown'} — {len(l.get('photos') or [])} photo(s) attached (not shown to you)\n"
                         f"{l.get('notes') or '(no text — photos only)'}")
        while parts and sum(len(p) for p in parts) > SITE_BLOCK_CHARS:
            parts.pop(0)
        out.append("\n\n".join(parts))

        everything = self.bills_of(pid)
        review = [b for b in everything if b.get("in_review") and not b.get("holdback")]
        held = [b for b in everything if b.get("holdback") and b.get("status") != PAID]
        t = self.bill_totals(pid)
        out.append(f"\n=== BILLS IN BUILDERTREND for {name_of(pid)} — as of the same reading ===")
        out.append("(A bill here is what the contractor has entered to be paid. “In review” means it is waiting for approval; it has not been approved or paid. "
                   "Each draw is entered as two bills: “(Contractor)” for 90% and “(Hold-back)” for the 10% RKB retains from every draw until the work is complete. "
                   "A hold-back bill is money held, not a bill due: never describe one as unpaid, overdue or awaiting approval.)")
        if held:
            out.append(f"HELD BACK BY RKB SO FAR (the 10% retainage, payable only at completion): {len(held)} hold-back bill(s), "
                       f"{usd(round(sum((b.get('balance') if b.get('balance') is not None else b.get('amount')) or 0 for b in held), 2))}.")
        if t["count"]:
            by = "; ".join(f"{k}: {v['count']} ({usd(v['amount'])})" for k, v in sorted(t["by_status"].items()))
            out.append(f"All bills: {t['count']}, billed {usd(t['billed'])}, paid {usd(t['paid'])}. By status — {by}.")
        out.append(f"IN REVIEW — {len(review)} bill(s), {usd(round(sum(b.get('amount') or 0 for b in review), 2))}:" if review else "IN REVIEW — none.")
        out.extend(self.render_bill(b) for b in review)
        ready = [b for b in everything if b.get("status") == READY and not b.get("holdback")]
        if ready:
            out.append(f"READY FOR PAYMENT (approved in Buildertrend, not yet paid) — {len(ready)} bill(s), {usd(round(sum(b.get('balance') or 0 for b in ready), 2))}:")
            out.extend(self.render_bill(b, lines=False) for b in ready[:40])
        return "\n".join(out)

    # ----------------------------------------------------------- what the page shows
    @staticmethod
    def _day(d: Optional[datetime]) -> Optional[datetime]:
        """Buildertrend's dates are calendar days at the job. Sent at midday so the
        page shows the same day in India and in the US."""
        return d.replace(hour=12, minute=0, second=0, microsecond=0) if isinstance(d, datetime) else d

    def view(self, pid: str, *, logs: int = 8) -> Dict[str, Any]:
        """What the property page shows. Buildertrend data changes only when a pull is
        loaded, so the page is not made to re-read it from the database on every visit."""
        import time
        cache = self.__dict__.setdefault("_views", {})
        hit = cache.get(pid)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        out = self._view(pid, logs=logs)
        cache[pid] = (time.time(), out)
        return out

    def _view(self, pid: str, *, logs: int = 8) -> Dict[str, Any]:
        from concurrent.futures import ThreadPoolExecutor
        jobs = list(self.jobs.find({"property_id": pid}, {"_id": 0}))
        if not jobs:
            return {"in_buildertrend": False}
        ids = [j["job_id"] for j in jobs]
        with ThreadPoolExecutor(max_workers=6) as pool:
            f_logs = pool.submit(lambda: list(self.logs.find({"property_id": pid}, {"_id": 0}).sort("date", -1).limit(logs)))
            f_count = pool.submit(lambda: self.logs.count_documents({"property_id": pid}))
            f_first = pool.submit(lambda: self.logs.find_one({"property_id": pid}, {"date": 1}, sort=[("date", 1)]))
            f_bills = pool.submit(lambda: list(self.bills.find({"property_id": pid}, {"_id": 0}).sort("invoice_date", -1)))
            f_photos = pool.submit(lambda: {p["file_id"] for p in self.photos.find({"job_id": {"$in": ids}}, {"file_id": 1})})
            f_pull = pool.submit(self.last_pull)
            rows, total, first, bills, have, pulled = f_logs.result(), f_count.result(), f_first.result(), f_bills.result(), f_photos.result(), f_pull.result()
        recent = []
        for l in rows:
            ph = l.get("photos") or []
            recent.append({"log_id": l["log_id"], "date": self._day(l.get("date")), "title": l.get("title"), "notes": l.get("notes"), "by": l.get("by"),
                           "photo_count": len(ph), "photos": [p["file_id"] for p in ph if p["file_id"] in have][:12]})
        totals: Dict[str, Any] = {"count": 0, "billed": 0.0, "paid": 0.0, "by_status": {}}
        for b in bills:
            totals["count"] += 1
            totals["billed"] += b.get("amount") or 0
            totals["paid"] += b.get("paid") or 0
            st = totals["by_status"].setdefault(b.get("status") or "?", {"count": 0, "amount": 0.0})
            st["count"] += 1
            st["amount"] += (b.get("balance") if b.get("status") != PAID else b.get("amount")) or 0
        totals["billed"], totals["paid"] = round(totals["billed"], 2), round(totals["paid"], 2)
        for st in totals["by_status"].values():
            st["amount"] = round(st["amount"], 2)
        review = [{**b, "invoice_date": self._day(b.get("invoice_date")), "due_date": self._day(b.get("due_date"))} for b in bills if b.get("in_review") and not b.get("holdback")]
        held = [b for b in bills if b.get("holdback") and b.get("status") != PAID]
        return {"in_buildertrend": True, "job_name": jobs[0].get("name"), "owner_name": jobs[0].get("owner_name"), "logs_total": total,
                "first_log": self._day(first and first.get("date")), "last_log": self._day(rows[0].get("date")) if rows else None, "pulled_at": pulled, "recent": recent,
                "bills": {"totals": totals, "in_review": review, "in_review_amount": round(sum(b.get("amount") or 0 for b in review), 2),
                          # The 10% kept back from each draw so far, not payable until the work is complete.
                          "held_back": {"count": len(held), "amount": round(sum((b.get("balance") if b.get("balance") is not None else b.get("amount")) or 0 for b in held), 2)},
                          "ready": [{k: b.get(k) for k in ("bill_id", "number", "title", "pay_to", "amount", "balance", "invoice_date", "due_date", "past_due")}
                                    for b in bills if b.get("status") == READY][:30]}}

    def logs_page(self, pid: str, *, skip: int = 0, limit: int = 30) -> Dict[str, Any]:
        """The site diary, newest first, a page at a time (the Site logs tab)."""
        jobs = [j["job_id"] for j in self.jobs.find({"property_id": pid}, {"job_id": 1})]
        have = {p["file_id"] for p in self.photos.find({"job_id": {"$in": jobs}}, {"file_id": 1})} if jobs else set()
        rows = []
        for l in self.logs.find({"property_id": pid}, {"_id": 0}).sort("date", -1).skip(max(0, skip)).limit(max(1, min(limit, 100))):
            ph = l.get("photos") or []
            rows.append({"log_id": l["log_id"], "date": self._day(l.get("date")), "title": l.get("title"), "notes": l.get("notes"), "by": l.get("by"),
                         "photo_count": len(ph), "photos": [p["file_id"] for p in ph if p["file_id"] in have][:24]})
        return {"in_buildertrend": bool(jobs), "total": self.logs.count_documents({"property_id": pid}), "rows": rows, "pulled_at": self.last_pull()}

    def bills_page(self, pid: str) -> Dict[str, Any]:
        """Every bill of the job, newest first (the Bills tab)."""
        rows = [{**{k: b.get(k) for k in ("bill_id", "number", "title", "pay_to", "kind", "amount", "paid", "balance", "status", "in_review", "past_due", "approvers",
                                         "cost_codes", "created_by", "documents", "line_items", "file_names")},
                 "invoice_date": self._day(b.get("invoice_date")), "due_date": self._day(b.get("due_date")), "date_paid": self._day(b.get("date_paid"))}
                for b in self.bills_of(pid)]
        return {"in_buildertrend": bool(self.jobs.find_one({"property_id": pid}, {"_id": 1})), "totals": self.bill_totals(pid), "rows": rows, "pulled_at": self.last_pull()}

    def photo(self, file_id: int) -> Optional[bytes]:
        """The photo's bytes: from the server's storage (never cached here); for a photo
        not moved there yet, from the copy still on this machine."""
        p = self.photos.find_one({"file_id": int(file_id)}, {"_id": 0})
        if not p:
            return None
        try:
            store = photo_store()
            if p.get("where") == "server" and store is not None:
                return store.get(p["sha256"])
            return self._local_bytes(p)
        except Exception:
            logger.exception("buildertrend photo %s could not be read", file_id)
            return None

    def photo_url(self, file_id: int) -> Optional[str]:
        """A short-lived link straight to the photo on the server's storage, so the
        browser fetches it from there and the image never passes through this machine."""
        store = photo_store()
        if store is None:
            return None
        p = self.photos.find_one({"file_id": int(file_id), "where": "server"}, {"sha256": 1})
        return store.presigned_url(p["sha256"], expires=900) if p else None
