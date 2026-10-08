"""Build the permit register from the records. Deterministic, no model calls;
every fact carries the record it came from and a verbatim quote.

Sources, strongest first
  dob_export   — ``Permits_searchResult*.xlsx`` from the DC DOB portal: one row
                 per review step; Permit#, Type, Status, Date Filed, Review
                 Status, Discipline, Status Date. Address in every row.
  tertius      — ``<address> - DOB Inspection [Permit: X]`` scheduling emails:
                 Event Number, Date, permits with their inspection types.
  screenshot   — Tertius portal images Rodrigo pastes: the ONLY official
                 inspection outcomes (No Show / Disapproved / Approved) with
                 the inspector's note.
  statement    — a person's sentence quoting a permit number (Kelly: "Permit
                 #E2406196 for Varnum is not expired"). Kept apart, labelled
                 "reported by", never promoted to official status.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from mangotree.config.registry import PEOPLE, PROPERTIES, PROPERTY_INDEX
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

PEOPLE_INDEX = {p.person_id: p for p in PEOPLE}

# ---------------------------------------------------------------- shapes
#: DC DOB: letter prefix + 7 digits, optional extension suffix.
DC_NO = r"(?:B|P|E|M|D|MVQ|R|S|FP|SW)\d{7}(?:-EXT-\d+)?"
#: Manatee County FL (904 / 910 Bayshore) and City of Alexandria VA (Ridge Rd).
FL_NO = r"(?:COBL|BLD|ELE|PLM|MEC|ROF|POL|DEM)\d{4}-\d{4}"
VA_NO = r"(?:BLDR|BLDC|ELER|ELEC|PLMR|PLMC|MECR|MECC|DEMO)\d{4}-\d{5}"
PERMIT_RE = re.compile(rf"\b({DC_NO}|{FL_NO}|{VA_NO})\b", re.I)
#: Date-shaped strings that pass the DC pattern (M10112023 = 10/11/2023) — never a permit.
_DATE_SHAPED = re.compile(r"^[A-Z](?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:19|20)\d{2}$")

KIND_BY_PREFIX = {"B": "building", "P": "plumbing", "E": "electrical", "M": "mechanical", "D": "demolition / raze", "MVQ": "DOB application (MVQ)",
                  "R": "roofing", "S": "solar", "FP": "fire protection", "SW": "sidewalk / public space", "BP": "DOB record (BP)", "DK": "DOB record (DK)",
                  "SR": "DOB service request", "TPIA": "public space (TPIA)", "SOL": "solar", "F": "fire (F)", "PC": "DOB record (PC)",
                  "COBL": "building (Manatee County)", "BLD": "building", "ELE": "electrical", "PLM": "plumbing", "MEC": "mechanical",
                  "ROF": "roofing", "POL": "pool", "DEM": "demolition", "BLDR": "building, residential (Alexandria)", "BLDC": "building, commercial (Alexandria)",
                  "ELER": "electrical, residential (Alexandria)", "PLMR": "plumbing, residential (Alexandria)", "MECR": "mechanical, residential (Alexandria)",
                  "DEMO": "demolition (Alexandria)"}

#: Jurisdiction from the registry's state; the expiry rule is the jurisdiction's.
JURISDICTION = {
    "DC": {"id": "dc", "name": "District of Columbia (DOB)", "expiry_days": 365,
           "rule": "DC: a permit becomes invalid one year after issuance unless work starts and continues, or an extension is issued (12 DCMR 105.5). Estimate: issuance + 365 days."},
    "VA": {"id": "alexandria_va", "name": "City of Alexandria, VA (Code Administration)", "expiry_days": 180,
           "rule": "Virginia USBC: a permit expires if work has not started within 6 months or is abandoned for 6 months. Estimate: last activity + 180 days."},
    "FL": {"id": "manatee_fl", "name": "Manatee County, FL (Building & Development)", "expiry_days": 180,
           "rule": "Florida Building Code 105.4.1: a permit becomes invalid if work does not start within 6 months or stops for 6 months. Estimate: last inspection or issuance + 180 days."},
    "MD": {"id": "pg_md", "name": "Prince George's County, MD (DPIE)", "expiry_days": 730,
           "rule": "PG County: building permits are valid two years from issuance. Estimate: issuance + 730 days."},
}
_SHAPE_FOR = {"dc": re.compile(rf"^{DC_NO}$", re.I), "manatee_fl": re.compile(rf"^{FL_NO}$", re.I), "alexandria_va": re.compile(rf"^{VA_NO}$", re.I)}

OUTCOMES = ("No Show", "Disapproved", "Approved", "Passed", "Partial Approval", "Cancelled", "Canceled", "Not Ready")
_OUTCOME_RE = re.compile(r"^(?P<type>[A-Za-z][A-Za-z /\-]{2,60}?)\s+(?P<outcome>" + "|".join(re.escape(o) for o in OUTCOMES) + r")\s*$", re.I)
_TERTIUS_SUBJECT = re.compile(r"^(?:(?:Accepted|Declined|Tentative|RE|FW|FWD):\s*)*(?P<addr>.+?)\s*-\s*DOB Inspection\s*\[Permit:\s*(?P<no>[A-Z0-9\-]+)\]", re.I)
_SYSTEM_EXPORT = re.compile(r"^(Property Tasks|Next steps - |Tasks - |MangoTree)", re.I)

STALE_AFTER_DAYS = 1
EXPIRING_SOON_DAYS = 45


def jurisdiction_for(pid: str) -> Dict[str, Any]:
    p = PROPERTY_INDEX.get(pid)
    return JURISDICTION.get((getattr(p, "state", "") or "").upper(), JURISDICTION["DC"])


def kind_for(no: str) -> str:
    m = re.match(r"^([A-Z]+)", no.upper())
    return KIND_BY_PREFIX.get(m.group(1) if m else "", "permit")


def permit_numbers(text: str) -> List[str]:
    out = []
    for m in PERMIT_RE.finditer(text or ""):
        no = m.group(1).upper()
        if _DATE_SHAPED.match(no) or no in out:
            continue
        out.append(no)
    return out


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    head = s.split(" ")[0]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(head, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _address_property(addr: str) -> Optional[str]:
    """'912 DECATUR ST NW, WASHINGTON, DC 20011' -> decatur_st. Street number
    plus the first word of the street name; exact, not fuzzy."""
    m = re.match(r"\s*(\d+)\s+([A-Za-z0-9]+)", addr or "")
    if not m:
        return None
    num, word = m.group(1), m.group(2).lower()
    for p in PROPERTIES:
        mm = re.match(r"\s*(\d+)\s+([A-Za-z0-9]+)", p.canonical_address)
        if mm and mm.group(1) == num and mm.group(2).lower() == word:
            return p.property_id
    return None


def _who(a: Dict[str, Any]) -> str:
    who = a.get("author_person_id")
    if who and who in PEOPLE_INDEX:
        return PEOPLE_INDEX[who].display_name
    frm = ((a.get("participants") or {}).get("from") or [""])[0]
    if who or frm:
        return who or frm
    return f"document: {a.get('filename') or a.get('subject') or 'untitled'}"[:80]


def _quote_around(text: str, no: str, width: int = 220) -> str:
    i = text.upper().find(no.upper())
    if i < 0:
        return ""
    start = max(0, i - width)
    end = min(len(text), i + len(no) + width)
    return re.sub(r"\s+", " ", text[start:end]).strip()


class PermitRegister:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.coll = mongo.db["permits"]
        self.coll.create_index([("property_id", 1), ("permit_no", 1)], unique=True, name="ux_permit")
        self.coll.create_index([("alerts.kind", 1)], name="ix_permit_alerts")

    # ------------------------------------------------------------- readers
    def _dob_rows(self, a: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        for line in (a.get("text") or "").split("\n"):
            if "Permit#=" not in line:
                continue
            fields = {}
            for part in line.split(" | "):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[re.sub(r"^\[.*?\]\s*", "", k).strip()] = v.strip()
            no = (fields.get("Permit#") or "").upper()
            # Footer / disclaimer rows and blank cells are not permits.
            if not re.fullmatch(r"[A-Z]{1,4}\d{5,10}(?:-EXT-\d+)?", no):
                continue
            rows.append({"permit_no": no, "address": fields.get("Address", ""), "type": fields.get("Permit Type"), "status": fields.get("Permit Status"),
                         "description": fields.get("Description of work/ Review status"), "filed": _parse_date(fields.get("Date Filed")),
                         "review_status": fields.get("Review Status"), "discipline": fields.get("Discipline"), "status_date": _parse_date(fields.get("Status Date")),
                         "applicant": fields.get("Applicant Name"), "owner": fields.get("Owner Name"), "line": re.sub(r"\s+", " ", line)[:600]})
        return rows

    def _tertius(self, a: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        m = _TERTIUS_SUBJECT.match(a.get("subject") or "")
        if not m:
            return None
        body = a.get("body_clean") or ""
        ev = re.search(r"Event Number:\s*(\d+)", body)
        dt = re.search(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})", body)
        contact = re.search(r"Site Contact:\s*(.+)", body)
        pairs: List[Tuple[str, str]] = []
        current = None
        for line in body.split("\n"):
            s = line.strip().lstrip("*").strip()
            if not s:
                continue
            if PERMIT_RE.fullmatch(s):
                current = s.upper()
            elif current and _is_inspection_type(s):
                pairs.append((current, s))
        subj_no = m.group("no").upper()
        if subj_no not in [p for p, _ in pairs]:
            pairs.append((subj_no, "(inspection type not listed)"))
        return {"address": m.group("addr").strip(), "subject_no": subj_no, "event_no": ev.group(1) if ev else None,
                "event_date": _parse_date(dt.group(1)) if dt else None, "contact": contact.group(1).strip()[:120] if contact else None,
                "pairs": pairs, "accepted": bool(re.match(r"^(Accepted|Declined|Tentative):", a.get("subject") or "", re.I)), "body": body}

    def _screenshot(self, a: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Tertius result blocks: a permit number on its own line, then
        '<Type> <Outcome>' lines, each optionally followed by 'Notes:' + a line."""
        text = a.get("text") or ""
        if "PERMIT NUMBER" not in text.upper() and "TERTIUS" not in text.upper():
            return []
        lines = [l.strip() for l in text.split("\n")]
        out, current = [], None
        for i, s in enumerate(lines):
            if not s:
                continue
            if PERMIT_RE.fullmatch(s):
                current = s.upper()
                continue
            mm = _OUTCOME_RE.match(s)
            if current and mm:
                note = None
                for j in range(i + 1, min(i + 4, len(lines))):
                    if lines[j].lower().startswith("notes:"):
                        rest = lines[j][6:].strip()
                        note = rest or next((lines[k] for k in range(j + 1, min(j + 3, len(lines))) if lines[k]), None)
                        break
                    if PERMIT_RE.fullmatch(lines[j]) or _OUTCOME_RE.match(lines[j]):
                        break
                out.append({"permit_no": current, "type": mm.group("type").strip(), "outcome": mm.group("outcome").title(),
                            "note": note, "quote": (s + (" — Notes: " + note if note else ""))[:400]})
        return out

    # --------------------------------------------------------------- build
    def build(self, property_ids: Optional[Sequence[str]] = None, *, place_unplaced: bool = True) -> Dict[str, Any]:
        art = self.mongo.artifacts
        now = datetime.now(timezone.utc)
        # permit_no -> property_id -> {strength: n}
        anchors: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        facts: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def rec(no: str, pid: str) -> Dict[str, Any]:
            return facts.setdefault((pid, no), {"property_id": pid, "permit_no": no, "rows": [], "inspections": [], "notices": [], "statements": [], "sources": set()})

        proj = {"sha256": 1, "source_type": 1, "filename": 1, "subject": 1, "date": 1, "property_ids": 1, "text": 1, "body_clean": 1, "author_person_id": 1,
                "participants.from": 1, "content_type": 1, "is_inline_image": 1, "thread_key": 1, "doc_class": 1, "placement": 1}
        q = {"$or": [{"text": {"$regex": r"\b(?:[BPEMD]|MVQ|COBL|BLDR|BLD)\d{4}", "$options": "i"}},
                     {"body_clean": {"$regex": r"\b(?:[BPEMD]|MVQ|COBL|BLDR|BLD)\d{4}", "$options": "i"}},
                     {"subject": {"$regex": r"DOB Inspection", "$options": "i"}}]}
        docs = list(art.find(q, proj))
        logger.info("permits: %d records mention a permit-shaped number", len(docs))

        # 1. official forms — the DOB export and the Tertius notices anchor a
        #    permit to its property; screenshots come after, so a result pasted
        #    into a mis-tagged email still lands on the permit's own property.
        docs = [a for a in docs if not _SYSTEM_EXPORT.match(a.get("filename") or "") and a.get("doc_class") != "system_export"]
        for a in docs:
            fn = a.get("filename") or ""
            if re.search(r"Permits_searchResult", fn, re.I):
                for r in self._dob_rows(a):
                    pid = _address_property(r["address"]) or (a["property_ids"][0] if len(a.get("property_ids") or []) == 1 else None)
                    if not pid:
                        continue
                    anchors[r["permit_no"]][pid]["official"] += 1
                    f = rec(r["permit_no"], pid)
                    f["rows"].append({**r, "source_sha": a["sha256"], "as_of": a.get("date")})
                    f["sources"].add(a["sha256"])
                continue
            t = self._tertius(a) if a.get("source_type") == "email" else None
            if t:
                pid = _address_property(t["address"]) or (a["property_ids"][0] if len(a.get("property_ids") or []) == 1 else None)
                if pid:
                    for no, itype in t["pairs"]:
                        anchors[no][pid]["official"] += 1
                        f = rec(no, pid)
                        f["notices"].append({"event_no": t["event_no"], "event_date": t["event_date"], "type": itype, "contact": t["contact"],
                                             "notice_date": a.get("date"), "accepted_reply": t["accepted"], "source_sha": a["sha256"],
                                             "stale": bool(t["event_date"] and a.get("date") and (a["date"] - t["event_date"]).days > STALE_AFTER_DAYS),
                                             "quote": f"{a.get('subject')} — Event Number: {t['event_no']} — Date: {t['event_date']:%m/%d/%Y}" if t["event_date"] else a.get("subject")})
                        f["sources"].add(a["sha256"])

        def official_home(no: str) -> Optional[str]:
            best = sorted(((p, c.get("official", 0)) for p, c in anchors.get(no, {}).items() if c.get("official")), key=lambda x: -x[1])
            return best[0][0] if best else None

        # 1b. Tertius result screenshots — the only official inspection outcomes.
        for a in docs:
            if not (a.get("source_type") == "attachment" and str(a.get("content_type") or "").startswith("image")):
                continue
            shots = self._screenshot(a)
            if not shots:
                continue
            pid = a["property_ids"][0] if len(a.get("property_ids") or []) == 1 else None
            for s in shots:
                # The permit's own property wins over the email's tag (the 25 Aug
                # Varnum No Show was pasted into a thread filed under Decatur).
                target = official_home(s["permit_no"]) or pid
                if not target:
                    continue
                anchors[s["permit_no"]][target]["screenshot"] += 1
                f = rec(s["permit_no"], target)
                f["inspections"].append({"type": s["type"], "outcome": s["outcome"], "note": s["note"], "date": a.get("date"), "date_basis": "screenshot date",
                                         "source_sha": a["sha256"], "quote": s["quote"], "filename": a.get("filename")})
                f["sources"].add(a["sha256"])

        # 2. statements by people (single- or two-property records only; the
        #    word "permit" or "inspection" must sit near the number)
        for a in docs:
            fn = a.get("filename") or ""
            if re.search(r"Permits_searchResult", fn, re.I) or a.get("is_inline_image") or str(a.get("content_type") or "").startswith("image"):
                continue
            if a.get("source_type") == "email" and self._tertius(a):
                continue
            text = (a.get("body_clean") if a.get("source_type") == "email" else a.get("text")) or ""
            if not text:
                continue
            props = list(a.get("property_ids") or [])
            if len(props) > 2:
                continue
            for no in permit_numbers(text):
                q_ = _quote_around(text, no)
                if not re.search(r"permit|inspection|DOB|expir|issued|approved", q_, re.I):
                    continue
                targets = props or list(anchors.get(no, {}).keys())
                for pid in targets:
                    if pid not in PROPERTY_INDEX:
                        continue
                    if props:
                        anchors[no][pid]["statement"] += 1
                    f = rec(no, pid)
                    f["statements"].append({"by": _who(a), "person_id": a.get("author_person_id"), "date": a.get("date"), "quote": q_[:440],
                                            "source_sha": a["sha256"], "subject": a.get("subject") or fn, "source_type": a.get("source_type")})
                    f["sources"].add(a["sha256"])

        # 3. decide the property of each permit: official beats screenshot beats
        #    statements; a permit with only statements needs two records.
        strong: Dict[str, str] = {}
        for no, by_pid in anchors.items():
            best = sorted(by_pid.items(), key=lambda kv: (kv[1].get("official", 0) * 100 + kv[1].get("screenshot", 0) * 10 + kv[1].get("statement", 0)), reverse=True)
            pid, score = best[0]
            if score.get("official") or score.get("screenshot") or score.get("statement", 0) >= 2:
                strong[no] = pid

        # 4. write the register
        written, kept = 0, set()
        for (pid, no), f in facts.items():
            if strong.get(no) != pid and not f["rows"] and not f["notices"]:
                continue   # a weak mention on the wrong property
            doc = self._compose(pid, no, f, now, anchored=(strong.get(no) == pid))
            self.coll.update_one({"property_id": pid, "permit_no": no}, {"$set": doc, "$setOnInsert": {"first_seen": now}}, upsert=True)
            kept.add((pid, no)); written += 1
        # permits no longer supported by any record disappear
        stale = [d for d in self.coll.find({}, {"property_id": 1, "permit_no": 1}) if (d["property_id"], d["permit_no"]) not in kept]
        for d in stale:
            self.coll.delete_one({"_id": d["_id"]})

        out: Dict[str, Any] = {"permits": written, "anchored_numbers": len(strong), "records_read": len(docs), "removed": len(stale)}
        if place_unplaced:
            out["placed"] = self.place_by_anchor(strong)
        return out

    # ----------------------------------------------------------- compose
    def _compose(self, pid: str, no: str, f: Dict[str, Any], now: datetime, *, anchored: bool) -> Dict[str, Any]:
        jur = jurisdiction_for(pid)
        rows = sorted(f["rows"], key=lambda r: (r.get("as_of") or datetime.min.replace(tzinfo=timezone.utc), r.get("status_date") or datetime.min.replace(tzinfo=timezone.utc)))
        latest = rows[-1] if rows else None
        issued = None
        for r in rows:
            if (r.get("status") or "").lower() in ("permit issued", "issued") and r.get("status_date"):
                issued = r["status_date"] if not issued or r["status_date"] > issued else issued
            if (r.get("review_status") or "").lower() == "permit issued" and r.get("status_date") and not issued:
                issued = r["status_date"]
        inspections = sorted(f["inspections"], key=lambda i: i.get("date") or datetime.min.replace(tzinfo=timezone.utc))
        notices = sorted(f["notices"], key=lambda n: n.get("notice_date") or datetime.min.replace(tzinfo=timezone.utc))
        statements = sorted(f["statements"], key=lambda s: s.get("date") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:12]
        official_status = (latest or {}).get("status")
        # Expiry: stated by the portal (status Expired), else estimated.
        last_activity = max([d for d in [issued, *(i.get("date") for i in inspections), *(n.get("event_date") for n in notices)] if d], default=None)
        expiry: Dict[str, Any] = {"date": None, "basis": None, "rule": jur["rule"]}
        status_l = (official_status or "").lower()
        closed = any(k in status_l for k in ("expired", "completed", "cancel", "closed", "withdrawn", "denied", "revoked", "request complete"))
        # Only a live permit gets an estimated expiry; a completed or cancelled
        # one has nothing to expire. No official status (number known only from
        # what people said) gets an estimate from the last activity we saw.
        if status_l == "expired":
            expiry = {"date": (latest or {}).get("status_date"), "basis": "stated: DOB portal shows Expired", "rule": jur["rule"]}
        elif closed:
            expiry = {"date": None, "basis": f"not applicable: status {official_status}", "rule": jur["rule"]}
        elif jur["id"] == "dc" and issued:
            expiry = {"date": issued + timedelta(days=jur["expiry_days"]), "basis": "estimated from issuance", "rule": jur["rule"]}
        elif last_activity:
            expiry = {"date": last_activity + timedelta(days=jur["expiry_days"]), "basis": "estimated from last activity", "rule": jur["rule"]}
        stated = [s for s in statements if re.search(r"not expired|expired|expires|valid (until|through)", s["quote"], re.I)]
        # Alerts
        alerts: List[Dict[str, Any]] = []
        if status_l == "expired":
            as_of = (latest or {}).get("as_of")
            alerts.append({"kind": "expired_official", "level": "critical",
                           "text": f"{no} shows Expired on the DOB portal export of {as_of:%Y-%m-%d}" if as_of else f"{no} shows Expired on the DOB portal"})
        elif expiry.get("date"):
            days = (expiry["date"] - now).days
            if days < 0:
                # Long past: history, not an alarm — the portal export still says
                # "Permit Issued" because DC does not expire a permit under
                # continuing inspections. Recent: worth a check this week.
                alerts.append({"kind": "expiry_estimate_passed", "level": "high" if days > -365 else "watch",
                               "text": f"{no}: estimated expiry {expiry['date']:%Y-%m-%d} has passed ({expiry['basis']}); confirm on the portal or get the extension"})
            elif days <= EXPIRING_SOON_DAYS:
                alerts.append({"kind": "expiring_soon", "level": "high", "text": f"{no}: estimated expiry in {days} days ({expiry['date']:%Y-%m-%d}, {expiry['basis']})"})
        last_by_type: Dict[str, Dict[str, Any]] = {}
        for i in inspections:
            last_by_type[i["type"].lower()] = i
        for t, i in last_by_type.items():
            if i["outcome"].lower() in ("no show", "disapproved", "not ready"):
                text = f"{no} {i['type']}: {i['outcome']}"
                if i.get("note"):
                    text += f" — {i['note']}"
                if i.get("date"):
                    text += f" (seen {i['date']:%Y-%m-%d})"
                alerts.append({"kind": "inspection_failed_open", "level": "critical", "text": text})
        for n in notices:
            if n.get("event_date") and n["event_date"] >= now - timedelta(days=1) and not n.get("accepted_reply"):
                alerts.append({"kind": "inspection_scheduled", "level": "normal", "text": f"{no} {n['type']}: inspection scheduled {n['event_date']:%Y-%m-%d} (event {n.get('event_no')})"})
            if n.get("stale") and not n.get("accepted_reply"):
                alerts.append({"kind": "stale_notice", "level": "watch", "text": f"{no}: notice received {n['notice_date']:%Y-%m-%d} repeats an event dated {n['event_date']:%m/%d/%Y} — re-sent, not a new date"})
        shape = _SHAPE_FOR.get(jur["id"])
        if shape and not shape.match(no) and not rows and not notices:
            # Known only from what people wrote, and not the shape this
            # jurisdiction issues: possibly a tracking or order number.
            alerts.append({"kind": "number_shape", "level": "watch", "text": f"{no} does not look like a {jur['name']} permit number; check the record it came from"})
        return {
            "property_id": pid, "permit_no": no, "kind": kind_for(no), "jurisdiction": jur["id"], "jurisdiction_name": jur["name"], "anchored": anchored,
            "official": {"status": official_status, "type": (latest or {}).get("type"), "description": (latest or {}).get("description"),
                         "filed": (latest or {}).get("filed"), "issued": issued, "review_status": (latest or {}).get("review_status"),
                         "discipline": (latest or {}).get("discipline"), "status_date": (latest or {}).get("status_date"), "as_of": (latest or {}).get("as_of"),
                         "applicant": (latest or {}).get("applicant"), "source_sha": (latest or {}).get("source_sha"), "quote": (latest or {}).get("line")},
            "review_steps": [{"review_status": r.get("review_status"), "discipline": r.get("discipline"), "status_date": r.get("status_date"), "as_of": r.get("as_of"), "source_sha": r.get("source_sha")} for r in rows][-12:],
            "inspections": inspections, "notices": notices, "statements": statements, "expiry_statements": stated[:4],
            "expiry": expiry, "last_activity": last_activity, "alerts": alerts,
            "sources": sorted(f["sources"]), "updated_at": now,
        }

    # ------------------------------------------------------- anchoring
    def place_by_anchor(self, strong: Dict[str, str]) -> Dict[str, Any]:
        """Unplaced records that quote an anchored permit number are placed on
        that permit's property; the rest of their thread follows. Additive,
        audited (``placed_by: permit_anchor``), and idempotent."""
        art = self.mongo.artifacts
        if not strong:
            return {"records": 0, "by_thread": 0}
        now = datetime.now(timezone.utc)
        placed = 0
        thread_props: Dict[str, set] = defaultdict(set)
        regex = "|".join(re.escape(n) for n in strong)
        q = {"$or": [{"property_ids": {"$in": [[], None]}}, {"property_ids": {"$exists": False}}],
             "is_inline_image": {"$ne": True},
             "$and": [{"$or": [{"text": {"$regex": regex, "$options": "i"}}, {"body_clean": {"$regex": regex, "$options": "i"}}]}]}
        for a in art.find(q, {"sha256": 1, "text": 1, "body_clean": 1, "thread_key": 1, "filename": 1, "property_ids": 1}):
            if _SYSTEM_EXPORT.match(a.get("filename") or ""):
                continue
            text = (a.get("body_clean") or "") + "\n" + (a.get("text") or "")
            props = sorted({strong[n] for n in permit_numbers(text) if n in strong})
            if not props:
                continue
            self._place(a["sha256"], props, now, reason=f"quotes permit {', '.join(n for n in permit_numbers(text) if n in strong)}")
            placed += 1
            if a.get("thread_key"):
                thread_props[a["thread_key"]].update(props)
        by_thread = 0
        for tk, props in thread_props.items():
            for a in art.find({"thread_key": tk, "$or": [{"property_ids": {"$in": [[], None]}}, {"property_ids": {"$exists": False}}], "is_inline_image": {"$ne": True}},
                              {"sha256": 1}):
                self._place(a["sha256"], sorted(props), now, reason="same thread as a record that quotes the permit")
                by_thread += 1
        if placed or by_thread:
            try:
                from mangotree.api import data
                data.invalidate_portfolio()
            except Exception:
                pass
        return {"records": placed, "by_thread": by_thread}

    def _place(self, sha: str, props: List[str], now: datetime, *, reason: str) -> None:
        self.mongo.artifacts.update_one({"sha256": sha}, {"$set": {
            "property_ids": props, "placement": "property", "scope": "property", "placed_at": now, "placed_by": "permit_anchor",
            "resolution_status": "resolved", "resolution.status": "segregated",
            "segregation.properties": props, "segregation.reasoning": f"permit register: {reason}", "segregation.fallback_used": "permit_anchor",
            "segregation.decided_at": now, "segregation.model": "permit_anchor", "updated_at": now}})
        self.mongo.chunks.update_many({"artifact_sha": sha}, {"$set": {"property_ids": props, "placement": "property", "scope": "property"}})
        self.mongo.db["doc_summaries"].update_many({"artifact_sha": sha}, {"$set": {"property_ids": props, "placement": "property"}})

    # ---------------------------------------------------------------- read
    def for_property(self, pid: str) -> List[Dict[str, Any]]:
        rows = list(self.coll.find({"property_id": pid}, {"_id": 0}).sort("permit_no", 1))
        return sorted(rows, key=lambda r: (-len(r.get("alerts") or []), r.get("permit_no")))

    def board(self, property_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        q = {"property_id": {"$in": list(property_ids)}} if property_ids else {}
        rows = list(self.coll.find(q, {"_id": 0, "review_steps": 0}).sort([("property_id", 1), ("permit_no", 1)]))
        by_prop: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_prop[r["property_id"]].append(r)
        alerts = [{**a, "property_id": r["property_id"], "permit_no": r["permit_no"], "address": PROPERTY_INDEX[r["property_id"]].canonical_address}
                  for r in rows for a in (r.get("alerts") or [])]
        order = {"critical": 0, "high": 1, "normal": 2, "watch": 3}
        alerts.sort(key=lambda a: (order.get(a["level"], 9), a["property_id"]))
        return {"properties": [{"property_id": pid, "address": PROPERTY_INDEX[pid].canonical_address, "jurisdiction": jurisdiction_for(pid)["name"], "permits": by_prop.get(pid, [])}
                               for pid in (property_ids or sorted(by_prop))], "alerts": alerts, "count": len(rows),
                "updated_at": max((r.get("updated_at") for r in rows if r.get("updated_at")), default=None)}

    def live_state_lines(self, pid: str) -> List[str]:
        """Short lines for the next-steps writer's live state."""
        out = []
        for r in self.for_property(pid):
            o = r.get("official") or {}
            bits = [f"{r['permit_no']} ({r.get('kind')})"]
            if o.get("status"):
                bits.append(f"DOB status {o['status']}" + (f" as of {o['as_of']:%Y-%m-%d}" if o.get("as_of") else ""))
            if (r.get("expiry") or {}).get("date"):
                bits.append(f"expiry {r['expiry']['date']:%Y-%m-%d} ({r['expiry']['basis']})")
            for a in (r.get("alerts") or [])[:2]:
                bits.append(a["text"])
            out.append("  - " + "; ".join(bits))
        return out


def _is_inspection_type(s: str) -> bool:
    return bool(re.match(r"^[A-Z][A-Za-z /\-]{3,50}$", s)) and not re.match(r"^(Address|Date|Event|Site|Permits|Inspector|Please|An inspection|Inspection)", s, re.I)
