"""Judge — a second reader that checks the to-dos and the waiting replies against the record.

This is the test the user asked for on 2026-11-18: "have a judge then test the things
that the things are been auto updated in the manner we wanted or not like a follow up
but if it is send or got the information in the call should not withstand the
unnecessary line".

The judge does not see how a line was made. For one property it is given the emails of
the last fourteen days word for word, what was said on the calls, and then the open
to-dos and the replies waiting to be sent — and for each it says whether a person
opening the system this morning should really be looking at it:

    to-do     ok · already_done · duplicate · stale · unclear · wrong_owner
    reply     ok · outdated · not_needed
    missing   something the record shows RKB owes or must do that no open line covers

It can be run on the real files and on the trial copy, so a change is measured before
it goes live: fewer lines that waste attention, nothing real lost. Results are kept in
``judge_runs`` (one document per property per run) with their cost.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .context import Records, day
from .llm import cost_of, structured, system_blocks
from .store import BrainStore, name_of, now

EFFORT = "high"
BAD = ("already_done", "duplicate", "stale", "unclear", "wrong_owner")

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items", "drafts", "missing"], "properties": {
    "items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "verdict", "why"], "properties": {
        "id": {"type": "string"}, "verdict": {"type": "string", "enum": ["ok", *BAD]}, "why": {"type": "string"}}}},
    "drafts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["draft_id", "verdict", "why"], "properties": {
        "draft_id": {"type": "string"}, "verdict": {"type": "string", "enum": ["ok", "outdated", "not_needed"]}, "why": {"type": "string"}}}},
    "missing": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["what", "owner", "why"], "properties": {
        "what": {"type": "string"}, "owner": {"type": "string"}, "why": {"type": "string"}}}}}}

JUDGE = """YOUR JOB NOW: CHECK THE LIST AGAINST THE RECORD
You are checking another reader's work. Above is every email of this property for the last
fourteen days, word for word. Below are what was said on the calls about it, then the open
to-dos the team will be shown this morning, then the replies written and waiting to be sent.
Judge each line only on what the record shows. Be strict in both directions: a line that
wastes a person's attention is a fault, and so is a real obligation with no line.

For each open to-do:
* ok — still open, clear, and with the right person.
* already_done — the record shows it done, answered, sent, paid, decided or overtaken. Say
  what settled it: the date and who (an email of the 14th, the call of the 12th).
* duplicate — another open to-do says the same thing; name it.
* stale — nothing in these fourteen days or on the calls touches the matter, and it carries no
  date still to come.
* unclear — a person could not tell from the line what exactly to do or when it is done.
* wrong_owner — the record shows someone else is to do it.
For each waiting reply:
* ok — the email still needs this answer and the draft says nothing the record contradicts.
* outdated — a later email or a call changed the facts the draft states, or answered part of it.
* not_needed — the question was answered already (by email or on a call), or no answer is owed.
missing — at most six things the record shows RKB owes someone or must do that no open to-do
and no waiting reply covers: a question asked of RKB and not answered, a promise RKB made on
a call or in an email with nothing tracking it, a date about to pass. owner: rakesh, jp,
manjunath, shivansh. Only what the record plainly shows — not what would be nice to do.
why — one sentence a person can check against the record. Answer for every id given."""


class Judge:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.where = "trial" if type(self.store).__name__ == "TrialStore" else "live"
        self.records = Records(mongo)
        self.runs = mongo.db["judge_runs"]

    def _calls(self, pid: str, since: datetime) -> str:
        from .calls import Calls
        rows = list(self.mongo.db["calls"].find({"take": True, "property_ids": pid, "started_at": {"$gte": since}},
                                                {"turns": 0, "granola_summary": 0, "costs": 0, "applied": 0}).sort("started_at", 1))
        return "\n\n".join(Calls.section_text(c, [s for s in c["reading"]["sections"] if s["property_id"] == pid]) for c in rows if c.get("reading")) or "(no calls about this property)"

    def one(self, pid: str, label: str, *, today: Optional[datetime] = None) -> Dict[str, Any]:
        today = today or now()
        file = self.store.get_file(pid) or {}
        items = BrainStore.open_items(file)
        drafts = self.store.list_drafts(pid=pid)
        if not items and not drafts:
            return {"property_id": pid, "items": [], "drafts": [], "missing": [], "cost_usd": 0.0}
        lines = [f"- [{i['id']}] (owner {i.get('owner')}, {i.get('kind')}" + (f", due {day(i.get('due'))}" if i.get("due") else "") + f", open since {day(i.get('opened_at'))}) {i.get('title')}"
                 + (f" — {i['detail']}" if i.get("detail") else "") for i in items]
        dl = []
        for d in drafts:
            to = ", ".join(t.get("name") or t.get("email") or "" for t in d.get("to") or [])
            dl.append(f"[{d['draft_id']}] to {to} — \"{d.get('subject')}\" (written {day(d.get('created_at'))}"
                      + (", already marked as needing a second look" if d.get("stale") else "") + f")\n{str(d.get('body') or '')[:2500]}")
        user = (f"TODAY: {today:%Y-%m-%d}\nPROPERTY: {name_of(pid)}\n\n=== WHAT WAS SAID ON THE CALLS (last 21 days) ===\n{self._calls(pid, today - timedelta(days=21))}\n\n"
                f"WHERE IT STANDS: {file.get('headline') or ''}\n\n=== OPEN TO-DOS ({len(items)}) ===\n" + ("\n".join(lines) or "(none)")
                + f"\n\n=== REPLIES WRITTEN AND WAITING ({len(drafts)}) ===\n" + ("\n\n".join(dl) or "(none)"))
        # The record is the large part and is the same for the "before" and the "after" run of a property,
        # so it is kept for an hour. A longer-kept block may not follow a shorter-kept one, so the
        # instructions in front of it are kept for an hour too.
        hour = {"type": "ephemeral", "ttl": "1h"}
        system = [{"type": "text", "text": prompts.CORE, "cache_control": hour},
                  {"type": "text", "text": self.records.record_block(pid), "cache_control": hour},
                  {"type": "text", "text": (prompts.general_note() if pid == bc.GENERAL_ID else "") + "\n" + JUDGE}]
        r = structured(system=system, user=user, schema=SCHEMA, effort=EFFORT, max_tokens=24_000, label=f"judge {pid}")
        by_id = {i["id"]: i for i in items}
        d = r["data"]
        out = {"property_id": pid, "label": label, "where": self.where, "at": now(),
               "items": [{**v, "title": by_id[v["id"]].get("title"), "owner": by_id[v["id"]].get("owner"), "kind": by_id[v["id"]].get("kind"),
                          "by": by_id[v["id"]].get("by")} for v in d.get("items") or [] if v.get("id") in by_id],
               "drafts": [v for v in d.get("drafts") or [] if any(x["draft_id"] == v.get("draft_id") for x in drafts)],
               "missing": (d.get("missing") or [])[:6], "open": len(items), "waiting_replies": len(drafts), "usage": r["usage"], "cost_usd": cost_of(r["usage"])}
        self.runs.insert_one(dict(out))
        return out

    def run(self, label: str, pids: Optional[Sequence[str]] = None, *, emit=None) -> Dict[str, Any]:
        from concurrent.futures import ThreadPoolExecutor
        say = emit or (lambda *_: None)
        pids = list(pids) if pids else [f["property_id"] for f in self.store.list_files()]
        rows: List[Dict[str, Any]] = []

        def one(pid):
            try:
                return self.one(pid, label)
            except Exception as exc:
                logger.exception("judge of %s failed", pid)
                return {"property_id": pid, "error": f"{type(exc).__name__}: {exc}"[:300]}
        with ThreadPoolExecutor(max_workers=3) as pool:
            for r in pool.map(one, pids):
                rows.append(r)
                say("judged", {"property_id": r["property_id"], "error": r.get("error"), "open": r.get("open"),
                               "not_ok": sum(1 for i in r.get("items") or [] if i["verdict"] != "ok"), "cost_usd": r.get("cost_usd")})
        return self.summary(rows, label)

    @staticmethod
    def summary(rows: Sequence[Dict[str, Any]], label: str) -> Dict[str, Any]:
        items = [i for r in rows for i in r.get("items") or []]
        drafts = [d for r in rows for d in r.get("drafts") or []]
        by_owner: Dict[str, Dict[str, int]] = {}
        for i in items:
            o = by_owner.setdefault(i.get("owner") or "?", {"open": 0, "ok": 0})
            o["open"] += 1
            o["ok"] += int(i["verdict"] == "ok")
            if i["verdict"] != "ok":
                o[i["verdict"]] = o.get(i["verdict"], 0) + 1
        verdicts: Dict[str, int] = {}
        for i in items:
            verdicts[i["verdict"]] = verdicts.get(i["verdict"], 0) + 1
        dv: Dict[str, int] = {}
        for d in drafts:
            dv[d["verdict"]] = dv.get(d["verdict"], 0) + 1
        return {"label": label, "files": len(rows), "failed": [r for r in rows if r.get("error")], "to_dos": len(items), "to_do_verdicts": verdicts,
                "ok_share": round(verdicts.get("ok", 0) / len(items), 3) if items else None, "by_owner": by_owner,
                "replies": len(drafts), "reply_verdicts": dv, "missing": sum(len(r.get("missing") or []) for r in rows),
                "cost_usd": round(sum(r.get("cost_usd") or 0 for r in rows), 4)}

    def report(self, label: str) -> Dict[str, Any]:
        rows = list(self.runs.find({"label": label}, {"_id": 0}))
        return {**self.summary(rows, label), "rows": rows}


def main(argv=None) -> int:
    import argparse
    import json
    from mangotree.storage.mongo import get_mongo
    ap = argparse.ArgumentParser(description="Check the to-dos and waiting replies against the record: run | report")
    ap.add_argument("action", choices=["run", "report"])
    ap.add_argument("--label", required=True)
    ap.add_argument("--trial", action="store_true")
    ap.add_argument("--pid", action="append")
    args = ap.parse_args(argv)
    mongo = get_mongo()
    if args.trial:
        from .calls import TrialStore
        store = TrialStore(mongo)
    else:
        store = BrainStore(mongo)
    j = Judge(mongo, store)
    if args.action == "run":
        out = j.run(args.label, args.pid, emit=lambda k, r: print(k, json.dumps(r, default=str), flush=True))
    else:
        out = {k: v for k, v in j.report(args.label).items() if k != "rows"}
    print(json.dumps(out, indent=1, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
