"""Sweep — the open to-dos of a file read over against what has happened since.

An email or a call closes the to-do it settles when it is read. What that leaves
behind is the slow rot of any list: a line that was done without an email saying so,
two lines for one matter, a line nobody has touched for weeks, a line too vague to
act on. The sweep reads every open line of one file against the file's last days
(emails, calls, site, bills) and gives each a verdict:

    keep        still open and somebody's to do (title/detail/owner may be put right)
    done        the record shows it done, answered or decided — closed, with what settled it
    duplicate   says the same as another open line — the clearer one is kept
    put_aside   old, and nothing in the record of the last week touches it

``put_aside`` is the user's rule of 2026-11-18: "focus the follow ups and things from
the past 1 week only else remove all the before ones". Nothing is deleted: a line
put aside is closed as "dropped" with a note and can be reopened from the property page.

The follow-up lines worked out from bills and site (see ``followlist``) are not to-dos
and are not swept; the old ones are put aside with the same mark a person uses there
(``old_follow_lines``), and come back by themselves if the situation changes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .context import day, property_block
from .llm import BrainError, cost_of, structured, system_blocks
from .store import OWNERS, BrainStore, name_of, now

EFFORT = "high"
WEEK = 7

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items", "replies"], "properties": {
    "items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "verdict", "why"], "properties": {
        "id": {"type": "string"}, "verdict": {"type": "string", "enum": ["keep", "done", "duplicate", "put_aside"]},
        "why": {"type": "string"}, "same_as": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]}, "detail": {"type": ["string", "null"]},
        "owner": {"type": ["string", "null"]}, "due": {"type": ["string", "null"]}}}},
    "replies": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["draft_id", "verdict", "why"], "properties": {
        "draft_id": {"type": "string"}, "verdict": {"type": "string", "enum": ["ok", "outdated", "not_needed"]}, "why": {"type": "string"}}}}}}

REPLIES = """
THE REPLIES WRITTEN AND WAITING are listed after the items. Give each one a verdict in replies:
* ok — the email still needs this answer and the draft says nothing the record contradicts.
* outdated — a later email or a call changed the facts the draft states, answered part of it, or
  its dates have passed. why: exactly what must change, with the date and who.
* not_needed — the question was already answered (by an email RKB sent, or on a call), or no
  answer is owed any more. why: what answered it, with the date.
Where no replies are listed, replies is empty."""

SWEEP = """YOUR JOB NOW: READ THE OPEN ITEMS OVER
Below is the file of one property as it stands — with its last days of emails, calls, site
logs and bills — then the calls of the last weeks about it, then its open items. Each person
at RKB works from these items every morning, a few at a time. A line that is already dealt
with, said twice, stale or unclear takes their attention from the ones that matter. Give
every open item one verdict.

* done — the record shows it done, answered, decided, or overtaken so that nothing is left to
  do. why: what settled it, with the date and who (quote a call where one is given). Be sure:
  "discussed" is not done; a promise to do it is not done.
* duplicate — it says the same thing as another open item. same_as = the id of the one to keep
  (the clearer, the one with the date). Two steps of one matter owned by different people are
  not duplicates.
* put_aside — it came from OLD HISTORY (marked so below) and nothing in the record of the last
  seven days touches the matter: no email, no call, no site log, no bill about it. The team has
  decided to work from the last week; older untouched matters are put aside and can be brought
  back. An item that came from a record of the last seven days is never put aside.
* keep — everything else. For a kept item you may put right what is wrong, giving only the
  fields that change: title (begins with a verb, names the thing and the person, under 110
  characters, plain words), detail (what exactly, the figure, what "done" looks like), owner
  (@@OWNERS@@ — only when the record plainly shows it is someone else's), due (YYYY-MM-DD, only
  when the record gives a date). Do not reword a line that is already clear.
why — one plain sentence a person can check. Give a verdict for every id below, and only those."""


def _aware(d: Any) -> Optional[datetime]:
    return d.replace(tzinfo=d.tzinfo or timezone.utc) if isinstance(d, datetime) else None


class Sweep:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.runs = mongo.db["sweep_runs"]

    def _dates(self, shas: Sequence[str]) -> Dict[str, datetime]:
        """When the record behind each to-do is dated (an email by its date, a call by its start)."""
        out: Dict[str, datetime] = {}
        mail = [s for s in shas if s and not s.startswith("granola-")]
        if mail:
            for a in self.mongo.artifacts.find({"sha256": {"$in": mail}}, {"sha256": 1, "date": 1}):
                if _aware(a.get("date")):
                    out[a["sha256"]] = _aware(a["date"])
        notes = [s[8:] for s in shas if s and s.startswith("granola-")]
        if notes:
            for c in self.mongo.db["calls"].find({"note_id": {"$in": notes}}, {"note_id": 1, "started_at": 1}):
                if _aware(c.get("started_at")):
                    out["granola-" + c["note_id"]] = _aware(c["started_at"])
        return out

    def _calls_text(self, pid: str, since: datetime) -> str:
        from .calls import Calls
        rows = list(self.mongo.db["calls"].find({"take": True, "property_ids": pid, "started_at": {"$gte": since}},
                                                {"turns": 0, "granola_summary": 0, "costs": 0, "applied": 0}).sort("started_at", 1))
        return "\n\n".join(Calls.section_text(c, [s for s in c["reading"]["sections"] if s["property_id"] == pid]) for c in rows if c.get("reading")) or "(no calls about this property)"

    def review(self, pid: str, *, today: Optional[datetime] = None, deep: bool = False) -> Dict[str, Any]:
        """``deep``: the emails of the last fourteen days are given word for word as well (as the judge
        reads them). It finds what the short log lines cannot show — a to-do answered inside a long
        email — and costs about four times as much, so it is the weekly reading, not the daily one."""
        today = today or now()
        file = self.store.get_file(pid) or {}
        items = BrainStore.open_items(file)
        if not items:
            return {"property_id": pid, "items": [], "cost_usd": 0.0}
        week = today - timedelta(days=WEEK)
        dates = self._dates([i.get("source_sha") for i in items if i.get("source_sha")])
        lines = []
        for i in items:
            d = dates.get(i.get("source_sha") or "")
            if i.get("assigned_by"):
                came = f"a task given by {i.get('assigned_by_name') or 'Rakesh Sir'} on {day(i.get('assigned_at') or i.get('opened_at'))}"
            elif d:
                came = f"a record of {day(d)}" + ("" if d >= week else " — OLD HISTORY")
            elif i.get("by") not in ("brain", "call", "sweep") and _aware(i.get("opened_at")) and _aware(i["opened_at"]) >= week:
                came = f"added by {i.get('by')} on {day(i.get('opened_at'))}"
            else:
                came = "OLD HISTORY (taken from the older record when the file was opened)"
            due = f", due {day(i.get('due'))}" if i.get("due") else ""
            lines.append(f"- [{i['id']}] ({i.get('owner')}, {i.get('kind')}{due}; came from: {came}) {i.get('title')}" + (f" — {i['detail']}" if i.get("detail") else ""))
        user = (f"TODAY: {today:%Y-%m-%d}   THE LAST SEVEN DAYS BEGIN: {week:%Y-%m-%d}\n\n{property_block(self.mongo, self.store, pid, file=file)}\n\n"
                f"=== CALLS ABOUT THIS PROPERTY SINCE {today - timedelta(days=21):%Y-%m-%d} ===\n{self._calls_text(pid, today - timedelta(days=21))}\n\n"
                f"=== THE OPEN ITEMS TO READ OVER ({len(items)}) ===\n" + "\n".join(lines))
        job = (prompts.general_note() if pid == bc.GENERAL_ID else "") + "\n" + SWEEP.replace("@@OWNERS@@", ", ".join(OWNERS)) + REPLIES
        drafts = self.store.list_drafts(pid=pid) if deep else []
        if drafts:
            # The waiting replies are checked on the thorough reading only: whether a draft is still
            # right can only be told against the emails themselves.
            rows = []
            for d in drafts:
                to = ", ".join(t.get("name") or t.get("email") or "" for t in d.get("to") or [])
                rows.append(f"[{d['draft_id']}] to {to} — \"{d.get('subject')}\" (written {day(d.get('created_at'))})\n{str(d.get('body') or '')[:2500]}")
            user += f"\n\n=== THE REPLIES WRITTEN AND WAITING ({len(drafts)}) ===\n" + "\n\n".join(rows)
        if deep:
            from .context import Records
            hour = {"type": "ephemeral", "ttl": "1h"}       # the same blocks the judge reads, so one pays for the other
            system = [{"type": "text", "text": prompts.CORE, "cache_control": hour},
                      {"type": "text", "text": Records(self.mongo).record_block(pid), "cache_control": hour},
                      {"type": "text", "text": job + "\nThe emails of the last fourteen days are given above word for word: use them. A to-do answered or done "
                                                     "inside one of them is done, even where the short log line does not say so."}]
        else:
            system = system_blocks(prompts.CORE, job)
        r = structured(system=system, user=user, schema=SCHEMA, effort=EFFORT, max_tokens=20_000, label=f"sweep {pid}")
        ids = {i["id"]: i for i in items}
        verdicts = [v for v in r["data"].get("items") or [] if v.get("id") in ids]
        for v in verdicts:
            v["was"] = {k: ids[v["id"]].get(k) for k in ("title", "owner", "kind")}
            # The rule is the user's and is not left to the model: a line from the last week is never put aside.
            src = dates.get(ids[v["id"]].get("source_sha") or "")
            if v["verdict"] == "put_aside" and ((src and src >= week) or ids[v["id"]].get("assigned_by")):
                v["verdict"], v["why"] = "keep", "From the last week — kept. (" + str(v.get("why") or "") + ")"
            if v["verdict"] == "duplicate" and (v.get("same_as") not in ids or v.get("same_as") == v["id"]):
                v["verdict"] = "keep"
        known = {d["draft_id"] for d in drafts}
        replies = [v for v in r["data"].get("replies") or [] if v.get("draft_id") in known and v.get("verdict") != "ok"]
        return {"property_id": pid, "items": verdicts, "replies": replies, "usage": r["usage"], "cost_usd": cost_of(r["usage"]), "open_before": len(items)}

    def mark_replies(self, replies: Sequence[Dict[str, Any]], *, today: Optional[datetime] = None) -> int:
        """A waiting reply the record has overtaken is marked, with the reason, so nobody sends it as it
        is: it shows as "rewrite" on the list, and ``rewrite_outdated`` writes it again."""
        n = 0
        for v in replies:
            kind = "not_needed" if v["verdict"] == "not_needed" else "record"
            n += self.store.drafts.update_one({"draft_id": v["draft_id"], "status": "ready", "stale.kind": {"$nin": ["call", "record", "not_needed"]}},
                                              {"$set": {"stale": {"kind": kind, "since": today or now(), "why": str(v.get("why") or "")[:400]}, "updated_at": now()}}).modified_count
        return n

    def rewrite_outdated(self, *, limit: int = 4, emit=None) -> Dict[str, Any]:
        """Write again the waiting replies that a call or a later email has overtaken, telling the writer
        what changed. The new draft takes the old one's place; one that turns out to be answered already
        is closed by the writer's own check. A few at a time: each is a full draft."""
        from .drafts import DraftWriter
        say = emit or (lambda *_: None)
        writer = DraftWriter(self.mongo, self.store)
        out: Dict[str, Any] = {"rewritten": [], "closed": [], "failed": [], "cost_usd": 0.0}
        q = {"status": "ready", "stale.kind": {"$in": ["call", "record", "not_needed"]}, "reply_to_sha": {"$nin": [None, ""]}, "rewrite_tries": {"$not": {"$gte": 2}}}
        for d in list(self.store.drafts.find(q, {"_id": 0}).sort("created_at", 1).limit(limit)):
            self.store.drafts.update_one({"draft_id": d["draft_id"]}, {"$inc": {"rewrite_tries": 1}})
            why = (d.get("stale") or {}).get("why") or ""
            instruction = ("This reply was written earlier and has not been sent. Since then things have moved: " + why +
                           "\nWrite it again so that it is right as of today. If nothing is owed any more, say so instead of writing a reply."
                           "\n\nTHE PREVIOUS DRAFT (rewrite it; keep what still fits):\n" + str(d.get("body") or ""))
            try:
                new = writer.write(d["reply_to_sha"], pids=d.get("property_ids") or [], points=d.get("points") or [], sender=d.get("sender") or bc.DEFAULT_SENDER,
                                   why=d.get("why") or "", urgency=d.get("urgency") or "", instruction=instruction, replaces=d["draft_id"])
            except Exception as exc:
                logger.exception("rewrite of draft %s failed", d["draft_id"])
                out["failed"].append({"draft_id": d["draft_id"], "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            row = {"was": d["draft_id"], "subject": d.get("subject"), "why": why[:200]}
            if new and new.get("draft_id"):
                out["cost_usd"] = round(out["cost_usd"] + (new.get("cost_usd") or 0), 4)
                out["rewritten"].append({**row, "draft_id": new["draft_id"]})
            else:
                # The writer found that no reply is owed. If its own check did not close the draft (answered
                # by an email), the draft stays on the list marked "looks already answered", with the
                # writer's reason in place of the old one, and is not tried again.
                note = str((new or {}).get("note") or "").strip()
                self.store.drafts.update_one({"draft_id": d["draft_id"], "status": "ready"},
                                             {"$set": {"stale": {"kind": "not_needed", "since": now(), "why": note[:900] or why}, "rewrite_tries": 2, "updated_at": now()}})
                out["closed"].append({**row, "note": note})
            say("rewritten", row)
        return out

    def apply(self, pid: str, verdicts: Sequence[Dict[str, Any]], *, today: Optional[datetime] = None) -> Dict[str, int]:
        today = today or now()
        n = {"done": 0, "duplicate": 0, "put_aside": 0, "reworded": 0, "kept": 0}
        titles = {i["id"]: i.get("title") for i in BrainStore.open_items(self.store.get_file(pid))}
        for v in verdicts:
            iid, why = v["id"], str(v.get("why") or "").strip()
            if v["verdict"] == "done":
                n["done"] += int(self.store.close_item(pid, iid, note=why, by="sweep", status="done"))
            elif v["verdict"] == "duplicate":
                n["duplicate"] += int(self.store.close_item(pid, iid, note=f"Same as: {titles.get(v.get('same_as')) or v.get('same_as')}", by="sweep", status="dropped"))
            elif v["verdict"] == "put_aside":
                n["put_aside"] += int(self.store.close_item(pid, iid, note=f"Put aside on {today:%d %b %Y}: nothing on this in the last week. Reopen it if it is still needed.",
                                                            by="sweep", status="dropped"))
            else:
                fields = {k: v[k] for k in ("title", "detail", "owner") if v.get(k)}
                if v.get("due"):
                    fields["due"] = v["due"]
                if fields and self.store.update_item(pid, iid, fields):
                    n["reworded"] += 1
                n["kept"] += 1
        if n["done"] or n["duplicate"] or n["put_aside"]:
            self.store.add_activity(pid, at=today, kind="sweep", who="MangoTree", relevance="minor",
                                    title=f"To-dos read over: {n['done']} closed as done, {n['duplicate']} said twice, {n['put_aside']} put aside",
                                    summary="; ".join(f"{v['was'].get('title')} — {v['verdict'].replace('_', ' ')}: {v.get('why')}" for v in verdicts if v["verdict"] != "keep")[:1200])
        return n

    def run(self, pids: Optional[Sequence[str]] = None, *, today: Optional[datetime] = None, emit=None, deep: bool = False) -> Dict[str, Any]:
        """Every file, four at a time (each is its own reading)."""
        from concurrent.futures import ThreadPoolExecutor
        say = emit or (lambda *_: None)
        where = "trial" if type(self.store).__name__ == "TrialStore" else "live"
        pids = list(pids) if pids else [f["property_id"] for f in self.store.list_files() if BrainStore.open_items(f)]
        out: Dict[str, Any] = {"files": {}, "failed": [], "cost_usd": 0.0, "where": where}

        def one(pid):
            try:
                r = self.review(pid, today=today, deep=deep)
                r["applied"] = self.apply(pid, r["items"], today=today)
                r["applied"]["replies_marked"] = self.mark_replies(r.get("replies") or [], today=today)
                return r
            except (BrainError, Exception) as exc:
                logger.exception("sweep of %s failed", pid)
                return {"property_id": pid, "error": f"{type(exc).__name__}: {exc}"[:300]}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for r in pool.map(one, pids):
                if r.get("error"):
                    out["failed"].append(r)
                    continue
                out["files"][r["property_id"]] = {"open_before": r.get("open_before", 0), **(r.get("applied") or {})}
                out["cost_usd"] = round(out["cost_usd"] + (r.get("cost_usd") or 0), 4)
                self.runs.insert_one({"at": now(), "where": where, "deep": deep, "property_id": r["property_id"], "items": r["items"], "replies": r.get("replies") or [], "applied": r.get("applied"),
                                      "usage": r.get("usage"), "cost_usd": r.get("cost_usd")})
                say("swept", {"property_id": r["property_id"], **out["files"][r["property_id"]]})
        return out

    def replay_trial(self, *, today: Optional[datetime] = None) -> Dict[str, Any]:
        """Do to the real files what the readings-over decided on the trial copy, in the same order,
        without asking the model again (the trial is what was looked at and judged). A line that has
        been closed or changed on the real file in the meantime is simply left as it is."""
        if type(self.store).__name__ == "TrialStore":
            raise ValueError("replay is for the real files")
        total = {"done": 0, "duplicate": 0, "put_aside": 0, "reworded": 0, "kept": 0, "replies_marked": 0, "runs": 0}
        for r in self.runs.find({"where": "trial"}).sort("at", 1):
            n = self.apply(r["property_id"], r.get("items") or [], today=today)
            n["replies_marked"] = self.mark_replies(r.get("replies") or [], today=today)
            for k, v in n.items():
                total[k] += v
            total["runs"] += 1
        # The marks on the waiting replies, as the trial left them (the first trial runs did not keep
        # the reply verdicts with the run, so they are read off the trial drafts themselves).
        for d in self.mongo.db["trial_reply_drafts"].find({"status": "ready"}, {"_id": 0, "draft_id": 1, "stale": 1, "rewrite_tries": 1}):
            stale = d.get("stale") if (d.get("stale") or {}).get("kind") in ("record", "not_needed") else None
            if not stale and d.get("rewrite_tries"):
                stale = {"kind": "record", "since": today or now(), "why": "Its dates and facts have moved on since it was written."}
            if stale:
                total["replies_marked"] += self.store.drafts.update_one({"draft_id": d["draft_id"], "status": "ready", "stale.kind": {"$nin": ["call", "record", "not_needed"]}},
                                                                        {"$set": {"stale": stale, "updated_at": now()}}).modified_count
        return total

    # ----------------------------------------------- the follow-up lines from bills and site
    def old_follow_lines(self, *, today: Optional[datetime] = None, apply: bool = False, by: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Follow-up lines whose matter is older than a week (a bill dated before it, a log gap that began
        before it). With ``apply`` they are put aside the way a person does it on the Follow-ups page."""
        from .followlist import FollowUps
        today = today or now()
        fu = FollowUps(self.mongo, self.store)
        old = [i for i in fu.build(today=today) if i.get("days") is not None and i["days"] > WEEK]
        if apply:
            for i in old:
                fu.marks.update_one({"key": i["key"]}, {"$set": {"key": i["key"], "state": "done", "by": (by or {}).get("user_id") or "sweep",
                                                                 "by_name": (by or {}).get("name") or "MangoTree", "at": today, "until": None,
                                                                 "note": f"Older than one week — put aside on {today:%d %b %Y}."}}, upsert=True)
        return [{k: i.get(k) for k in ("key", "kind", "owner", "property", "title", "days", "amount")} for i in old]


    # ------------------------------------------------------------ the strict one-week rule
    def week_cut(self, *, today: Optional[datetime] = None, apply: bool = False) -> Dict[str, Any]:
        """Keep open only what belongs to the last week; put everything older aside (never deleted).

        The admin's rule (2026-11-17, repeated 2026-11-18): "keep only the past one week un replied or
        to dos open everything else should be closed". The age of a to-do is the age of what raised
        it, not the day the system wrote it down — the files were first built on 11-12 Nov, so by
        that day's date everything looked new. A to-do stays open when any of these holds:
          * the email or call it came from is dated inside the week;
          * a person or a later reading wrote it inside the week (after the file's first build);
          * a call held inside the week changed it;
          * Rakesh Sir gave it as a task.
        Everything else — carried over from older history when the file was built, or raised by an
        email or call older than a week — is put aside with a note, and can be reopened.
        A waiting reply is put aside when the email it answers is older than a week."""
        today = today or now()
        cut = today - timedelta(days=WEEK)
        where = "trial" if type(self.store).__name__ == "TrialStore" else "live"

        def aware(d: Any) -> Optional[datetime]:
            return d.replace(tzinfo=d.tzinfo or timezone.utc) if isinstance(d, datetime) else None
        files = list(self.store.files.find({}, {"_id": 0, "property_id": 1, "built_at": 1, "open_items": 1}))
        open_items = [(f, i) for f in files for i in f.get("open_items") or [] if i.get("status") == "open"]
        shas = {str(i["source_sha"]) for _, i in open_items if i.get("source_sha")}
        call_at: Dict[str, Optional[datetime]] = {}
        touched: set = set()
        for c in self.mongo.db["calls"].find({"status": {"$ne": "skipped"}}, {"note_id": 1, "started_at": 1, f"applied.{where}": 1}):
            at = aware(c.get("started_at"))
            call_at["granola-" + str(c.get("note_id"))] = at
            if at and at >= cut:
                for pid, a in (((c.get("applied") or {}).get(where)) or {}).items():
                    for u in ((a.get("changes") or {}).get("items_update") or []):
                        touched.add((pid, str(u.get("id") or "")))
        art_at = {a["sha256"]: aware(a.get("date") or a.get("created_at"))
                  for a in self.mongo.artifacts.find({"sha256": {"$in": [s for s in shas if not s.startswith("granola-")]}}, {"sha256": 1, "date": 1, "created_at": 1})}
        out: Dict[str, Any] = {"cut": cut, "kept": 0, "put_aside": [], "replies_put_aside": [], "applied": apply}
        for f, i in open_items:
            pid, sha = f["property_id"], str(i.get("source_sha") or "")
            raised = (call_at.get(sha) if sha.startswith("granola-") else art_at.get(sha)) if sha else None
            opened, built = aware(i.get("opened_at")), aware(f.get("built_at"))
            if i.get("assigned_by"):
                why = None
            elif (pid, str(i.get("id"))) in touched:
                why = None
            elif raised is not None:
                why = None if raised >= cut else f"it comes from {'a call' if sha.startswith('granola-') else 'an email'} of {raised:%d %b}"
            elif opened and opened >= cut and (not built or opened > built + timedelta(hours=1)):
                why = None                                              # written inside the week, by a person or a later reading
            else:
                why = "it was carried over from older history when the file was first built"
            if why is None:
                out["kept"] += 1
                continue
            row = {"property_id": pid, "id": i.get("id"), "owner": i.get("owner"), "title": i.get("title"), "due": i.get("due"), "why": why}
            if apply and not self.store.close_item(pid, str(i.get("id")), by="sweep", status="dropped",
                                                   note=f"Put aside on {today:%d %b %Y}: {why}, and nothing in the last week touched it. Only the last week is kept open. Reopen it if it is still needed."):
                continue
            out["put_aside"].append(row)
        for d in self.store.drafts.find({"status": "ready"}, {"_id": 0, "draft_id": 1, "subject": 1, "in_reply_to": 1, "created_at": 1, "property_ids": 1}):
            got = aware((d.get("in_reply_to") or {}).get("date") or d.get("created_at"))
            if got is None or got >= cut:
                continue
            if apply:
                self.store.drafts.update_one({"draft_id": d["draft_id"], "status": "ready"},
                                             {"$set": {"status": "dismissed", "status_by": "sweep", "updated_at": today,
                                                       "put_aside": {"at": today, "why": f"The email it answers is from {got:%d %b}: older than one week. Put aside on {today:%d %b %Y}; set it back to waiting if a reply is still owed."}}})
            out["replies_put_aside"].append({"draft_id": d["draft_id"], "subject": (d.get("in_reply_to") or {}).get("subject") or d.get("subject"), "email_date": got})
        if apply:
            by_pid: Dict[str, List[Dict[str, Any]]] = {}
            for r in out["put_aside"]:
                by_pid.setdefault(r["property_id"], []).append(r)
            for pid, rows in by_pid.items():
                self.store.add_activity(pid, at=today, kind="sweep", who="MangoTree", relevance="minor",
                                        title=f"Only the last week is kept open: {len(rows)} older to-do{'' if len(rows) == 1 else 's'} put aside",
                                        summary="; ".join(str(r["title"]) for r in rows)[:1200])
            self.mongo.db["sweep_runs"].insert_one({"at": today, "where": where, "kind": "week_cut", "cut": cut, "kept": out["kept"],
                                                    "put_aside": out["put_aside"], "replies_put_aside": out["replies_put_aside"]})
        return out


def main(argv=None) -> int:
    import argparse
    import json
    from mangotree.storage.mongo import get_mongo
    ap = argparse.ArgumentParser(description="Read the open to-dos over: run | old-follow")
    ap.add_argument("action", choices=["run", "old-follow", "replay-trial", "rewrite", "week-cut"])
    ap.add_argument("--trial", action="store_true")
    ap.add_argument("--apply", action="store_true", help="old-follow: put the old lines aside (without it they are only listed)")
    ap.add_argument("--pid", action="append")
    ap.add_argument("--deep", action="store_true", help="read against the emails of the last fourteen days word for word")
    args = ap.parse_args(argv)
    mongo = get_mongo()
    if args.trial:
        from .calls import TrialStore
        store = TrialStore(mongo)
    else:
        store = BrainStore(mongo)
    sw = Sweep(mongo, store)
    if args.action == "run":
        print(json.dumps(sw.run(args.pid, deep=args.deep, emit=lambda k, r: print(k, json.dumps(r, default=str), flush=True)), indent=1, default=str))
    elif args.action == "replay-trial":
        print(json.dumps(sw.replay_trial(), indent=1, default=str))
    elif args.action == "rewrite":
        print(json.dumps(sw.rewrite_outdated(limit=4), indent=1, default=str, ensure_ascii=False))
    elif args.action == "week-cut":
        r = sw.week_cut(apply=args.apply)
        for x in r["put_aside"]:
            print(f"{str(x['owner']):<10} {x['property_id'][:14]:<14} {str(x['title'])[:70]:<70} | {x['why']}")
        for x in r["replies_put_aside"]:
            print(f"reply      {x['email_date']:%d %b}  {str(x['subject'])[:80]}")
        print(f"cut-off {r['cut']:%d %b %Y %H:%M} UTC | kept open {r['kept']} | to-dos put aside {len(r['put_aside'])} | replies put aside {len(r['replies_put_aside'])} |",
              "APPLIED" if args.apply else "listed only (add --apply)")
    else:
        rows = sw.old_follow_lines(apply=args.apply)
        for r in rows:
            print(f"{r['owner']:<6} {str(r['days']):>4}d  {r['property'][:24]:<24} {r['title'][:80]}")
        print(len(rows), "old lines", "put aside" if args.apply else "(listed only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
