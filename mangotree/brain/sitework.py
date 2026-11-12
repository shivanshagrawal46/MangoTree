"""How is the work going? — a reading of the last month of site logs and photos.

For each property with a Buildertrend job, one Opus 5.5 call is shown the daily
logs of the last month, in order, each followed by its photos, together with
what the job is meant to be (the completion target, the scope lines on file).
It reports what was done, how the visible workmanship looks, the pace against
the target, where the logs and the photos do not agree, and what to ask.

What keeps this honest:

* Every observation must name the photos it rests on. Photo ids the model was
  never shown are removed after the call, and a workmanship remark left with no
  photo is dropped — a remark about quality that cannot be pointed at is not
  shown to anyone.
* The counts (days with a log, longest gap, days since the last log) are worked
  out in code, not by the model.
* The model is told what a photo cannot show: what is behind a wall, whether
  something works, whether it meets code. It is told not to give a percentage
  complete and not to predict a finish date.
* The page shows the cited photos beside each remark, so the reader judges with
  their own eyes. This is a reading of the contractor's own photos — photos the
  contractor chose to post — and it says so. It is not an inspection.

Collection: ``site_analyses`` (one per run; the latest per property is shown).
"""
from __future__ import annotations

import base64
import io
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .buildertrend import Buildertrend, usd
from .llm import BrainError, cost_of, structured
from .store import BrainStore, name_of, now

PERIOD_DAYS = 31
MAX_PHOTOS = 90                 # shown to the model in one reading
MAX_SIDE = 1024                 # pixels; enough to judge finish, a quarter of the bytes

BRIEF = """YOUR JOB NOW: HOW IS THE WORK GOING ON THIS SITE?
You are reading, as an experienced owner's representative would, the contractor's own
record of the last month on one renovation: the daily logs, in date order, each followed
by the photos posted with it. Each photo is labelled with its id. Report to Rakesh Sir —
the lender whose money is in this house — how the work is going.

WHAT YOU CAN AND CANNOT SAY
* Say only what a photo shows or a log says, and say which. Every observation names the
  photo ids it rests on (and the log date). An observation you cannot point at is not an
  observation: leave it out.
* A photo cannot show what is behind a finished wall, whether a thing works, whether it
  passed inspection or meets code. Do not say or imply any of these. "Wiring is run to
  the boxes" is something you can see; "the electrical is done correctly" is not.
* These are photos the contractor chose to post. Rooms and defects that were not
  photographed are unknown to you — say what has NOT been shown when it matters (a bath
  the logs call finished with no photo of it).
* Do not give a percentage complete. Do not predict a finish date. Judge the pace against
  the completion target you are given, by what is visibly left to do.
* If the photos are too few, too dark or too close-up to judge something, the answer is
  "cannot_tell". That is a useful answer; a guess is not.

WHAT TO LOOK FOR
* What was done in the period, by area and trade, and where the job visibly stands now
  (demolition / framing / rough-in / insulation and drywall / finishes / punch list /
  complete).
* Workmanship you can see: framing plumb and regular; drywall joints and corners; tile
  layout, cuts, lippage and grout lines; paint coverage and cut lines; trim and casing
  joints; cabinets level and aligned; flooring seams and transitions; fixtures set
  straight. Note what is done well as readily as what is not.
* Things a lender should hear about: water staining or damage, cracks, rot, mould,
  temporary or exposed wiring left in a finished space, missing protection of finished
  surfaces, an unsafe site, work that appears to have been redone, materials that look
  different from what the scope calls for.
* Pace: is the work advancing from log to log, or are the same rooms shown in the same
  state? Are there gaps with no log? (The counts are given to you — use them.)
* Where a log and the photos disagree: a log that claims work no photo shows, or photos
  that show less — or more — than the log says.

Write plainly, in short sentences, for a man who knows construction. Dates as "11 Nov".

HE READS THE POINTS, NOT THE PARAGRAPHS. The page shows him only these, so make them count:
* headline — one sentence, at most 16 words: where the job stands and the one thing that matters.
* done_points — at most 5 points, each at most 10 words: what got done in the period.
* raise_points — at most 5 points, each at most 14 words, most serious first: what he should
  raise with the contractor. A point is a fact with its date, not advice ("Half-bath wall
  torn and unpainted; 11 Nov log says painting 100%").
* workmanship_short, pace_short, record_short — the reason for each rating in at most 12 words.
No point may say anything the fuller fields below do not support.
"""

POINTS_BRIEF = """You are given a finished analysis of the last month of work on one renovation site. Put it
into the short points Rakesh Sir reads. Use ONLY what the analysis says — add nothing, and
keep every date and figure exactly as written.
* headline — one sentence, at most 16 words: where the job stands and the one thing that matters.
* done_points — at most 5 points, each at most 10 words: what got done in the period.
* raise_points — at most 5 points, each at most 14 words, most serious first: what he should
  raise with the contractor. A point is a fact with its date, not advice.
* workmanship_short, pace_short, record_short — the reason for each rating in at most 12 words.
"""

_POINT_FIELDS = {
    "headline": {"type": "string"}, "done_points": {"type": "array", "items": {"type": "string"}}, "raise_points": {"type": "array", "items": {"type": "string"}},
    "workmanship_short": {"type": "string"}, "pace_short": {"type": "string"}, "record_short": {"type": "string"},
}
POINTS_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(_POINT_FIELDS), "properties": _POINT_FIELDS}

_RATING = ["good", "acceptable", "concerns", "cannot_tell"]
_OBS = {"type": "object", "additionalProperties": False, "required": ["kind", "what", "where", "photo_ids", "log_date", "confidence"],
        "properties": {"kind": {"type": "string", "enum": ["good", "concern", "defect", "safety"]}, "what": {"type": "string"}, "where": {"type": "string"},
                       "photo_ids": {"type": "array", "items": {"type": "integer"}}, "log_date": {"type": "string"},
                       "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}
_WORK = {"type": "object", "additionalProperties": False, "required": ["work", "area", "status", "dates", "photo_ids"],
         "properties": {"work": {"type": "string"}, "area": {"type": "string"}, "status": {"type": "string", "enum": ["done", "in_progress", "started", "claimed_not_shown"]},
                        "dates": {"type": "string"}, "photo_ids": {"type": "array", "items": {"type": "integer"}}}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["headline", "done_points", "raise_points", "workmanship_short", "pace_short", "record_short",
                 "overall", "one_line", "phase", "summary", "work", "workmanship", "workmanship_reason", "observations", "pace", "pace_reason",
                 "documentation", "documentation_reason", "mismatches", "not_shown", "questions", "best_photos"],
    "properties": {
        **_POINT_FIELDS,
        "overall": {"type": "string", "enum": ["going_well", "acceptable", "concerns", "stalled", "cannot_tell"]},
        "one_line": {"type": "string"}, "phase": {"type": "string"}, "summary": {"type": "string"},
        "work": {"type": "array", "items": _WORK},
        "workmanship": {"type": "string", "enum": _RATING}, "workmanship_reason": {"type": "string"},
        "observations": {"type": "array", "items": _OBS},
        "pace": {"type": "string", "enum": ["on_track", "at_risk", "behind", "stalled", "cannot_tell"]}, "pace_reason": {"type": "string"},
        "documentation": {"type": "string", "enum": ["good", "thin", "poor"]}, "documentation_reason": {"type": "string"},
        "mismatches": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["log_says", "photos_show", "log_date", "photo_ids"],
                                                  "properties": {"log_says": {"type": "string"}, "photos_show": {"type": "string"}, "log_date": {"type": "string"},
                                                                 "photo_ids": {"type": "array", "items": {"type": "integer"}}}}},
        "not_shown": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "best_photos": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["photo_id", "caption"],
                                                   "properties": {"photo_id": {"type": "integer"}, "caption": {"type": "string"}}}},
    },
}


def _small(blob: bytes) -> bytes:
    """The photo at a size that still shows finish quality, re-encoded as JPEG."""
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(blob))).convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82)
        return out.getvalue()
    except Exception:
        return blob


def pick_photos(logs: List[Dict[str, Any]], have: set, budget: int = MAX_PHOTOS) -> Dict[int, List[int]]:
    """Which photos to show when there are more than fit: every log keeps its place,
    and the newest logs are filled first. Returns {log_id: [file_id, ...]}."""
    pools = {l["log_id"]: [p["file_id"] for p in l.get("photos") or [] if p["file_id"] in have] for l in logs}
    chosen: Dict[int, List[int]] = {k: [] for k in pools}
    left = budget
    newest_first = sorted(logs, key=lambda l: l.get("date") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    while left > 0 and any(pools.values()):
        for l in newest_first:
            pool = pools[l["log_id"]]
            if pool and left > 0:
                chosen[l["log_id"]].append(pool.pop(0))
                left -= 1
    return chosen


def stats(logs: List[Dict[str, Any]], *, days: int = PERIOD_DAYS, job_began: Optional[datetime] = None) -> Dict[str, Any]:
    """The counts, worked out here rather than asked of the model. A job whose first
    log ever falls inside the period is measured from that log: the days before the
    job began are not a gap in the work."""
    today = now().date()
    dates = sorted({l["date"].date() for l in logs if l.get("date")})
    start = today - timedelta(days=days)
    if job_began and job_began.date() > start:
        start = job_began.date()
    edges = [start] + dates + [today]
    gap = max(((b - a).days for a, b in zip(edges, edges[1:])), default=days)
    return {"logs": len(logs), "days_with_a_log": len(dates), "longest_gap_days": gap, "days_since_last_log": (today - dates[-1]).days if dates else None,
            "first_log": dates[0].isoformat() if dates else None, "last_log": dates[-1].isoformat() if dates else None, "period_days": days}


class SiteWork:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.bt = Buildertrend(mongo)
        self.coll = mongo.db["site_analyses"]

    def latest(self, pid: str) -> Optional[Dict[str, Any]]:
        return self.coll.find_one({"property_id": pid}, {"_id": 0}, sort=[("at", -1)])

    def _scope(self, pid: str) -> str:
        """What the job is meant to be: the completion target and the scope lines on file."""
        f = self.store.get_file(pid) or {}
        st = f.get("stage") or {}
        out = [f"WHERE THE DEAL STANDS (from RKB's property file): {f.get('headline') or 'not on file'}"]
        if st.get("construction_expected"):
            out.append(f"COMPLETION TARGET: {st['construction_expected']:%d %b %Y}. {st.get('construction_basis') or ''}")
        else:
            out.append("COMPLETION TARGET: none on record.")
        from .snapshot import arv_detail
        try:
            a = arv_detail(self.mongo, pid)
        except Exception:
            a = {"exists": False}
        lines = ((a.get("rehab") or {}).get("lines") or []) if a.get("exists") else []
        if lines:
            out.append("SCOPE LINES ON FILE (" + str((a.get("rehab") or {}).get("basis") or "rehab budget") + "): "
                       + "; ".join(f"{l['label']} {usd(float(l['amount']))}" for l in lines))
        t = self.bt.bill_totals(pid)
        if t["count"]:
            out.append(f"BILLED SO FAR IN BUILDERTREND: {usd(t['billed'])} across {t['count']} bills, {usd(t['paid'])} paid.")
        return "\n".join(out)

    def analyse(self, pid: str, *, days: int = PERIOD_DAYS) -> Optional[Dict[str, Any]]:
        logs = self.bt.logs_of(pid, days=days)[::-1]                   # oldest first
        if not logs:
            return None
        have_rows = {p["file_id"]: p["sha256"] for p in self.bt.photos.find({"file_id": {"$in": [p["file_id"] for l in logs for p in l.get("photos") or []]}}, {"file_id": 1, "sha256": 1})}
        chosen = pick_photos(logs, set(have_rows))
        st = stats(logs, days=days, job_began=self.bt.summary(pid).get("first_log"))
        total_photos = sum(len(l.get("photos") or []) for l in logs)
        shown: List[int] = []
        content: List[Dict[str, Any]] = [{"type": "text", "text": (
            f"TODAY: {now():%d %b %Y}\nPROPERTY: {name_of(pid)}\n{self._scope(pid)}\n\n"
            f"THE LAST {days} DAYS IN BUILDERTREND: {st['logs']} daily logs on {st['days_with_a_log']} different days; longest stretch with no log {st['longest_gap_days']} days; "
            f"last log {st['days_since_last_log']} day(s) ago. {total_photos} photos were posted with these logs; you are shown {sum(len(v) for v in chosen.values())} of them "
            f"(every log is represented; the newest logs most fully).\n\nThe logs follow, oldest first. Each photo is preceded by its id.")}]
        for l in logs:
            ids = chosen.get(l["log_id"]) or []
            posted = len(l.get("photos") or [])
            content.append({"type": "text", "text": (f"\n──────── LOG {l['date']:%d %b %Y} — “{l.get('title') or 'untitled'}” — written by {l.get('by') or 'unknown'} — "
                                                     f"{posted} photo(s) posted{'' if len(ids) == posted else f', {len(ids)} shown here'}\n{l.get('notes') or '(no text; photos only)'}")})
            for fid in ids:
                blob = self.bt.photo(fid)
                if not blob:
                    continue
                content.append({"type": "text", "text": f"photo id {fid}:"})
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(_small(blob)).decode()}})
                shown.append(fid)
        try:
            r = structured(system=prompts.CORE + "\n" + BRIEF, user=content, schema=SCHEMA, effort="high", max_tokens=20_000, label=f"site work {pid}")
        except BrainError as exc:
            logger.warning("site work for %s could not be read: %s", pid, exc)
            return None
        d = self._clean(r["data"], set(shown))
        doc = {"property_id": pid, "at": now(), "period_days": days, "stats": st, "photos_posted": total_photos, "photos_shown": len(shown),
               "pulled_at": self.bt.last_pull(), "model": bc.MODEL, "cost_usd": cost_of(r["usage"]), **d}
        self.coll.insert_one(dict(doc))
        doc.pop("_id", None)
        return doc

    @staticmethod
    def _clean(d: Dict[str, Any], shown: set) -> Dict[str, Any]:
        """Remove what cannot be pointed at: photo ids that were never shown, and
        remarks about workmanship left with no photo behind them."""
        def ids(xs):
            return [int(x) for x in xs or [] if isinstance(x, int) and x in shown]
        dropped = 0
        obs = []
        for o in d.get("observations") or []:
            o = {**o, "photo_ids": ids(o.get("photo_ids"))}
            if o["photo_ids"]:
                obs.append(o)
            else:
                dropped += 1
        work = [{**w, "photo_ids": ids(w.get("photo_ids"))} for w in d.get("work") or []]
        for w in work:
            if w["status"] in ("done", "in_progress", "started") and not w["photo_ids"]:
                w["status"] = "claimed_not_shown"        # said in a log, seen in no photo
        mism = [{**m, "photo_ids": ids(m.get("photo_ids"))} for m in d.get("mismatches") or []]
        best = [b for b in d.get("best_photos") or [] if b.get("photo_id") in shown][:8]
        return {**d, "observations": obs, "work": work, "mismatches": mism, "best_photos": best, "dropped_unsupported": dropped,
                "done_points": [str(x) for x in d.get("done_points") or []][:5], "raise_points": [str(x) for x in d.get("raise_points") or []][:5]}

    def shorten(self, pid: str) -> Optional[Dict[str, Any]]:
        """Give a stored reading its short points (for readings made before the points existed).
        Text only: the photos are not read again, and nothing may be added to what the reading says."""
        import json
        a = self.coll.find_one({"property_id": pid}, sort=[("at", -1)])
        if not a:
            return None
        src = {k: a.get(k) for k in ("overall", "one_line", "phase", "summary", "workmanship", "workmanship_reason", "pace", "pace_reason", "documentation", "documentation_reason",
                                     "work", "observations", "mismatches", "not_shown", "stats")}
        try:
            r = structured(system=POINTS_BRIEF, user=f"PROPERTY: {name_of(pid)}\n\nTHE ANALYSIS:\n{json.dumps(src, default=str, ensure_ascii=False)}", schema=POINTS_SCHEMA,
                           effort="medium", max_tokens=6000, label=f"site points {pid}")
        except BrainError as exc:
            logger.warning("points for %s could not be written: %s", pid, exc)
            return None
        d = r["data"]
        fields = {**{k: str(d.get(k) or "") for k in ("headline", "workmanship_short", "pace_short", "record_short")},
                  "done_points": [str(x) for x in d.get("done_points") or []][:5], "raise_points": [str(x) for x in d.get("raise_points") or []][:5]}
        self.coll.update_one({"_id": a["_id"]}, {"$set": fields, "$inc": {"cost_usd": cost_of(r["usage"])}})
        return fields

    # ------------------------------------------------------------ for the model
    def block(self, pid: str) -> str:
        """The latest reading, for the chat and the drafts — marked for what it is."""
        a = self.latest(pid)
        if not a:
            return ""
        out = [f"\n=== SITE WORK ANALYSIS for {name_of(pid)} — an AI reading of the contractor's logs and {a.get('photos_shown')} of {a.get('photos_posted')} photos, "
               f"made {a['at']:%Y-%m-%d}; NOT an inspection ===",
               f"Overall: {a.get('overall')} — {a.get('one_line')}", f"Visible phase: {a.get('phase')}",
               f"Workmanship (as far as the photos show): {a.get('workmanship')} — {a.get('workmanship_reason')}",
               f"Pace: {a.get('pace')} — {a.get('pace_reason')}", f"Summary: {a.get('summary')}"]
        flagged = [o for o in a.get("observations") or [] if o.get("kind") != "good"]
        if flagged:
            out.append("Flagged from the photos: " + "; ".join(f"[{o['kind']}, {o.get('confidence')} confidence, log {o.get('log_date')}] {o['what']} ({o.get('where')})" for o in flagged[:12]))
        if a.get("mismatches"):
            out.append("Logs and photos disagree: " + "; ".join(f"log {m.get('log_date')} says “{m['log_says']}”, photos show {m['photos_show']}" for m in a["mismatches"][:6]))
        if a.get("questions"):
            out.append("Questions it suggests asking the contractor: " + " | ".join(a["questions"][:8]))
        return "\n".join(out)


def after_pull(mongo: Mongo, *, workers: int = 3) -> Dict[str, Any]:
    """After a Buildertrend pull: for every property that got logs it did not have
    before, read the last month again (logs and photos) and place the deal on its
    path again. Properties with no new log are left as they are — nothing changed
    for them. Safe to call more than once: a pull is marked once it has been read."""
    from concurrent.futures import ThreadPoolExecutor
    from .snapshot import refresh_stage
    sw = SiteWork(mongo)
    waiting = list(sw.bt.pulls.find({"analysed": False, "new_logs": {"$exists": True}}))
    pids = sorted({p for w in waiting for p in (w.get("new_logs") or {})})
    out: Dict[str, Any] = {"properties_with_new_logs": pids, "read_again": [], "failed": [], "cost_usd": 0.0}
    if not pids:
        return out

    def one(pid: str):
        try:
            a = sw.analyse(pid)
            refresh_stage(mongo, sw.store, pid)
            return pid, a
        except Exception:
            logger.exception("reading %s after the pull failed", pid)
            return pid, None

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
        for pid, a in pool.map(one, pids):
            if a:
                out["read_again"].append({"property_id": pid, "overall": a.get("overall"), "pace": a.get("pace"), "headline": a.get("headline") or a.get("one_line")})
                out["cost_usd"] += float(a.get("cost_usd") or 0)
            else:
                out["failed"].append(pid)
    # A property whose reading failed stays marked, so the next pull or the next call tries it again.
    failed = set(out["failed"])
    for w in waiting:
        left = {p: n for p, n in (w.get("new_logs") or {}).items() if p in failed}
        sw.bt.pulls.update_one({"_id": w["_id"]}, {"$set": {"analysed": not left, "new_logs": left or w.get("new_logs"), "analysed_at": now()}})
    try:
        from mangotree.api import swr
        swr.clear()
    except Exception:
        pass
    out["cost_usd"] = round(out["cost_usd"], 2)
    return out
