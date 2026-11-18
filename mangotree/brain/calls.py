"""Calls — what was said on a call reaches the property files by itself.

    Granola note  ->  taken?  ->  read (Opus 5.5 high)  ->  one note per property,
                                  with the speaker's exact words and the time (US Eastern)
                              ->  applied to each property file: to-dos closed, changed
                                  and added, facts set, waiting replies marked outdated

Which calls are taken (user, 2026-11-18):

* from the day the folder exists: every call Rakesh Sir puts in the Granola folder ``RKB-ROI``;
* calls before that (from 1 Nov 2026): a call where Granola names an ROI Blocks person as a
  speaker, or whose content is RKB's real-estate work — decided on Granola's own summary.

A call that is not taken leaves one line here (its id and the date) so it is not looked at
again. Its title, summary and transcript are not kept.

Rules the user set: the exact words are kept in the language spoken; a person is named only
when Granola names them; a call about no single property goes to the General file; the
newest statement wins — a call never overwrites something that happened after it.

Collection ``calls``: one document per Granola note.

Trial area: ``TrialStore`` is a copy of the property files (``trial_*`` collections). A
reading can be applied there first, looked at and judged, and then applied to the real
files without asking the model again (``apply(..., replay=True)``).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.config.registry import PEOPLE, PROPERTY_INDEX, Org
from mangotree.core import eastern
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import granola as gr
from . import prompts
from .context import day, property_block
from .llm import BrainError, add_usage, cost_of, structured, system_blocks
from .store import OWNERS, BrainStore, _id, active_property_ids, name_of, now

EFFORT = "high"  # the user: "use opus 5.5 high everytime for reading the transcripts"
SINCE = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)  # 1 Nov 2026, midnight Eastern
ROI_ORGS = (Org.ROI_BLOCKS, Org.LP_REMODEL)
POINT_KINDS = ("decision", "promise", "number", "instruction", "open_question", "concern", "update")
TRIES = 3                    # a reading or an update that fails this many times is left alone, not asked for ever


def sha_of(note_id: str) -> str:
    """The mark a call leaves on an activity line, a fact and a to-do (where an email leaves its sha)."""
    return f"granola-{note_id}"


def et_label(at: Optional[datetime]) -> str:
    return eastern.label(eastern.to_et(at)) if at else "time not recorded"


def et_clock(at: Optional[datetime]) -> str:
    e = eastern.to_et(at) if at else None
    return f"{e.hour % 12 or 12}:{e:%M} {'AM' if e.hour < 12 else 'PM'}" if e else "--:--"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", str(text or "")).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().lower()


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", str(text or "").lower())


def roi_speaker(names: Sequence[str]) -> Optional[str]:
    """The first speaker Granola names who is on the ROI Blocks side (full name, or the first name alone)."""
    for p in PEOPLE:
        if p.org not in ROI_ORGS:
            continue
        full = _letters(re.sub(r"\(.*?\)", "", p.display_name))
        first = _letters(p.display_name.split()[0])
        for n in names:
            k = _letters(n)
            if k and (k == full or k == first):
                return n
    return None


class TrialStore(BrainStore):
    """The property files, their activity and the waiting replies — as copies nobody else reads."""

    def __init__(self, mongo: Mongo):
        super().__init__(mongo)
        db = mongo.db
        self.files, self.activity, self.drafts = db["trial_property_files"], db["trial_property_activity"], db["trial_reply_drafts"]

    def seed(self) -> Dict[str, int]:
        """Start the trial from the real files as they stand now."""
        live = BrainStore(self.mongo)
        counts = {}
        for src, dst, q in ((live.files, self.files, {}), (live.activity, self.activity, {"at": {"$gte": now() - timedelta(days=45)}}),
                            (live.drafts, self.drafts, {"status": "ready"})):
            dst.drop()
            rows = [{k: v for k, v in d.items() if k != "_id"} for d in src.find(q)]
            if rows:
                dst.insert_many(rows)
            counts[dst.name] = len(rows)
        return counts


# ------------------------------------------------------------------ the three questions
PICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["calls"], "properties": {"calls": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "ours", "why"], "properties": {
        "id": {"type": "string"}, "ours": {"type": "boolean"}, "why": {"type": "string"}}}}}}

PICK = """YOUR JOB NOW: SAY WHICH CALLS ARE RKB'S REAL-ESTATE WORK
Below are Rakesh Sir's recorded calls: for each, the title and the short summary his note-taker
wrote, and the speakers it could name. He records everything — family, health, other businesses,
court cases that have nothing to do with these loans — and only RKB's real-estate lending work
is to be kept.

ours = true when the call is about: one of the properties in the list above or a house RKB may
fund; the work with ROI Blocks / LP Remodeling (Wes Stone, Kelly Stone) — draws, bills, budgets,
payments, contractors, insurance on these houses, how the two firms work together; the notes,
liens, payoffs, title, closings and foreclosures on RKB's loans; RKB's own team (JP Sir,
Manjunath Sir, Shivansh) working on any of that, or on the system that tracks it.
ours = false for everything else, however businesslike: his own home or family property, other
ventures (trading, software, health, mortgage-note funds, land abroad), litigation about other
matters, personal calls. When the summary does not let you tell, ours = false.
why: a few words. Answer for every id given."""

READ_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["about_our_work", "overall", "sections"], "properties": {
    "about_our_work": {"type": "boolean"},
    "overall": {"type": "string"},
    "sections": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                           "required": ["property_id", "topic", "summary", "points"], "properties": {
        "property_id": {"type": "string"}, "topic": {"type": "string"}, "summary": {"type": "string"},
        "points": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                             "required": ["kind", "text", "by", "quote", "at"], "properties": {
            "kind": {"type": "string", "enum": list(POINT_KINDS)}, "text": {"type": "string"}, "by": {"type": "string"},
            "quote": {"type": "string"}, "at": {"type": "string"}}}}}}}}}

READ = """YOUR JOB NOW: READ THIS CALL AND SPLIT IT BY PROPERTY
Below is the transcript of a call Rakesh Sir recorded. Each turn begins with the time it was
said (US Eastern) and the speaker. Say what the call settled, property by property, and keep
the exact words that matter.

1. One section for each property that is really discussed — a decision, a number, a date, a
   problem, a promise, an instruction — not for a name said in passing. property_id is the id
   from the PROPERTY IDS list. What belongs to no single property in that list (the payment
   process, funding limits, insurance in general, a new market, how RKB and ROI work together,
   a house that is not in the list) goes in a section with property_id "_general"; make one
   such section per subject and name the subject in topic. For a property section, topic is a
   few words on what was discussed. The ids are for property_id only: in summary, text, topic
   and overall write the address or "general matters", never an id.
   A card, bank-account or identity number spoken on the call is never repeated — not in a
   quote, not in a summary. "[number removed]" in the transcript marks where one was taken out.
2. Leave out whatever is not RKB's real-estate work (family, health, other ventures, small
   talk). If none of the call is RKB's real-estate work, about_our_work = false and no sections.
3. summary — plain English, two to six sentences: what was said and what was settled about
   this property on this call. No view of your own.
4. points — every decision, every promise (who will do what, by when), every number (amount,
   rate, date, size), every instruction, every question left open, every concern. One point
   each. text is your one-sentence statement of it in English, complete on its own: it names
   the person and the property and gives the figure.
5. quote — the speaker's own words for that point, copied character for character from ONE
   turn, in the language they were spoken (Hindi stays Hindi; do not translate, tidy, shorten
   inside, or join words from different turns). One to three sentences, cut at sentence ends,
   enough to carry the point. The transcript is machine-made and mis-hears words ("Warnum" is
   Varnum): keep the words as they stand in the quote and use the right name in text. Where
   the matter is spread over many short turns and no one turn carries it, leave quote empty.
   Never write a quote that is not in the transcript.
6. by — the speaker label exactly as it stands on that turn. "Rakesh Sir" is his microphone: it
   is him, or someone in the room with him. "Other person (not named)" and "Voices not
   separated" mean the recorder did not name the speaker — use the label as it is and never
   work out a name from what is said.
7. at — the time printed on that turn.
8. Later in the call wins: where something is said and then changed, give the final position
   and say it was changed.
overall — two or three sentences on the whole call."""

APPLY_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["relevance", "title", "summary", "headline", "status", "facts_set", "facts_remove", "items_close", "items_update",
                             "items_add", "drafts_outdated"],
                "properties": {
                    "relevance": {"type": "string", "enum": ["material", "minor", "noise"]},
                    "title": {"type": "string"}, "summary": {"type": "string"},
                    "headline": {"type": ["string", "null"]}, "status": {"type": ["string", "null"]},
                    "facts_set": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "value"],
                                                            "properties": {"key": {"type": "string"}, "value": {"type": "string"}}}},
                    "facts_remove": {"type": "array", "items": {"type": "string"}},
                    "items_close": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "note"],
                                                              "properties": {"id": {"type": "string"}, "note": {"type": "string"}}}},
                    "items_update": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "why"], "properties": {
                        "id": {"type": "string"}, "why": {"type": "string"}, "title": {"type": ["string", "null"]}, "detail": {"type": ["string", "null"]},
                        "owner": {"type": ["string", "null"]}, "due": {"type": ["string", "null"]}}}},
                    "items_add": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                            "required": ["title", "detail", "owner", "kind", "due"], "properties": {
                        "title": {"type": "string"}, "detail": {"type": "string"}, "owner": {"type": "string", "enum": list(OWNERS)},
                        "kind": {"type": "string", "enum": ["reply", "action", "waiting", "decision"]}, "due": {"type": ["string", "null"]}}}},
                    "drafts_outdated": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["draft_id", "why"],
                                                                  "properties": {"draft_id": {"type": "string"}, "why": {"type": "string"}}}}}}

APPLY = """YOUR JOB NOW: SAY WHAT THIS CALL CHANGES IN THE FILE
A call has been read. Below are the file of one property as it stands, the replies written and
waiting to be sent, and what the call said about this property, with the speakers' exact
words. Say what the call changes. The team works from this file every morning: every open
item is something a named person must do or is waiting for, so a line that is already dealt
with, or that says the same thing twice, costs them attention.

TIME ORDER. The call took place at CALL TIME. The file may already hold what happened after
it — look at the dates on the facts, on the open items and in the last days. The newest
statement wins: never replace something newer than the call with what the call said, and never
reopen what was settled after it. Where the file is already past the call on a matter, change
nothing on that matter.

* items_close — close an open item when the call shows it done, answered, decided or no longer
  needed. note: one line on what settled it — who said it, the date of the call, and their
  words in quotation marks where a quote is given. A matter that was only talked about, with
  nothing settled, stays open.
* items_update — when the call moves the date, the owner or the substance of an open item,
  change that item (give only the fields that change, and why) instead of adding another.
* items_add — only a real next step somebody must take that no open item already covers.
  owner: rakesh, jp, manjunath, shivansh, wes, kelly, or other — one person.
  title: begins with a verb, names the thing and the person, under 110 characters, in words a
  busy person takes in at a glance.
  detail: one or two sentences — what exactly, the figure, who said it on the call and when,
  and what "done" looks like.
  kind: reply (someone is owed an answer) · decision (Rakesh Sir has to choose) · action (a
  task) · waiting (RKB is waiting for wes, kelly or an outsider; the owner is the person
  awaited).
  due: YYYY-MM-DD only when a date was said or follows plainly from what was said; else null.
  Add nothing for what was finished on the call itself, for general talk, or for work ROI does
  on its own that RKB is not waiting for. Fewer, true items beat a long list.
* facts_set — lasting facts of the deal that the call fixed or changed (an amount, a date, a
  term, a name). Use the key the file already has when the fact is there; value carries the
  figure and ends "(call <date>)". facts_remove — keys the call made untrue.
* headline, status — rewrite only when the call changes where the property stands, keeping all
  that is still true; otherwise null.
* drafts_outdated — a waiting reply the call has overtaken: the question was answered on the
  call, or the draft now says something that is no longer so.
* title — the call in a few words (who, about what). summary — what this call means for this
  property, two to five sentences, with the one or two quotes that matter most written as
  Name: "words". relevance — material, minor, or noise (nothing here touches the file).

Dates as YYYY-MM-DD. Report only what the call notes support."""


def _registry_block() -> str:
    lines = ["PROPERTY IDS (use these ids; \"_general\" for anything else)"]
    for pid in active_property_ids():
        p = PROPERTY_INDEX[pid]
        also = "; ".join(str(a) for a in list(p.aliases)[:8])
        lines.append(f"- {pid}: {p.canonical_address}, {p.city}, {p.state}" + (f" — also called: {also}" if also else ""))
    return "\n".join(lines)


def render_turns(turns: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(f"[{et_clock(t.get('at'))}] {t['who']}: {t['words']}" for t in turns)


PART_CHARS = 30_000          # about half an hour of talk


def split_turns(turns: Sequence[Dict[str, Any]], limit: int = PART_CHARS) -> List[List[Dict[str, Any]]]:
    """A transcript in parts of roughly equal length, cut between turns. A call that fits in one part
    (with a third to spare) is not cut at all."""
    total = sum(len(t["words"]) for t in turns)
    if total <= limit * 4 // 3:
        return [list(turns)]
    n = -(-total // limit)
    size = total / n
    parts: List[List[Dict[str, Any]]] = [[]]
    run = 0
    for t in turns:
        if run >= size * len(parts) and len(parts) < n:
            parts.append([])
        parts[-1].append(t)
        run += len(t["words"])
    return [p for p in parts if p]


def check_quotes(sections: List[Dict[str, Any]], turns: Sequence[Dict[str, Any]]) -> int:
    """Every quote must stand in the transcript word for word. One that does not is removed
    (the point stays, without it). Returns how many were removed."""
    one = [_norm(t["words"]) for t in turns]
    whole = " ".join(one)
    removed = 0
    for s in sections:
        for p in s.get("points") or []:
            q = _norm(p.get("quote") or "").strip('"').strip()
            if not q:
                p["quote"] = ""
                continue
            if any(q in t for t in one) or q in whole:
                continue
            p["quote_removed"] = str(p["quote"])[:600]
            p["quote"] = ""
            removed += 1
    return removed


class Calls:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None, client: Optional[gr.Granola] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.trial = isinstance(self.store, TrialStore)
        self.where = "trial" if self.trial else "live"
        self.coll = mongo.db["calls"]
        self.client = client or gr.Granola()
        try:
            self.coll.create_index("note_id", unique=True, name="ux_calls_note")
            self.coll.create_index([("started_at", -1)], name="ix_calls_at")
        except Exception:
            pass

    # ------------------------------------------------------------- which calls
    def _known(self) -> set:
        return {d["note_id"] for d in self.coll.find({}, {"note_id": 1})}

    def _keep(self, note: Dict[str, Any], picked_by: str, why: str = "") -> Dict[str, Any]:
        t = gr.times(note)
        sp = gr.speakers(note)
        doc = {"note_id": note["id"], "take": True, "picked_by": picked_by, "picked_why": why, "title": (note.get("title") or "").strip() or "Untitled call",
               "started_at": t["start"], "ended_at": t["end"], "web_url": note.get("web_url"), "speakers": sp["lines"], "named": sp["named"],
               "separated": sp["separated"], "granola_summary": note.get("summary_text") or "", "turns": gr.turns(note),
               "folders": [f.get("name") for f in note.get("folder_membership") or []], "status": "picked", "updated_at": now()}
        self.coll.update_one({"note_id": note["id"]}, {"$set": doc, "$setOnInsert": {"created_at": now(), "cost_usd": 0.0, "costs": []}}, upsert=True)
        return doc

    def _skip(self, note: Dict[str, Any], why: str) -> None:
        # Not ours: only the id and the day are kept, so it is never fetched or judged again.
        self.coll.update_one({"note_id": note["id"]}, {"$set": {"note_id": note["id"], "take": False, "status": "skipped", "skipped_as": why,
                                                                "started_at": gr.times(note)["start"], "updated_at": now()},
                                                       "$setOnInsert": {"created_at": now()}}, upsert=True)

    def _cost(self, note_id: str, step: str, usage: Dict[str, int]) -> float:
        c = cost_of(usage)
        self.coll.update_one({"note_id": note_id}, {"$inc": {"cost_usd": c}, "$push": {"costs": {"step": step, "usd": c, "usage": usage, "at": now(), "in": self.where}}})
        return c

    def pull_folder(self) -> Dict[str, Any]:
        """The standing rule: every new note in the RKB-ROI folder is taken."""
        fid = self.client.folder_id()
        if not fid:
            return {"folder": None, "taken": []}
        known = self._known()
        taken = []
        for n in self.client.notes(folder_id=fid):
            if n["id"] in known:
                continue
            taken.append(self._keep(self.client.note(n["id"]), "folder")["note_id"])
        return {"folder": fid, "taken": taken}

    def pull_past(self, since: datetime = SINCE, until: Optional[datetime] = None) -> Dict[str, Any]:
        """The calls already made: taken when an ROI Blocks person is a named speaker, else by content."""
        known = self._known()
        out: Dict[str, Any] = {"seen": 0, "by_speaker": [], "by_content": [], "skipped": 0, "pick_cost_usd": 0.0}
        unsure: List[Dict[str, Any]] = []
        for n in self.client.notes(created_after=since):
            if n["id"] in known:
                continue
            note = self.client.note(n["id"])
            if until and (gr.times(note)["start"] or since) > until:
                continue
            out["seen"] += 1
            sp = gr.speakers(note)
            if any(gr._same_name(f.get("name") or "", gr.FOLDER_NAME) for f in note.get("folder_membership") or []):
                self._keep(note, "folder")
                out.setdefault("by_folder", []).append(n["id"])
            elif roi_speaker(sp["named"]):
                self._keep(note, "speaker", f"{roi_speaker(sp['named'])} is a named speaker")
                out["by_speaker"].append(n["id"])
            elif not (note.get("transcript") or []):
                self._skip(note, "no transcript")
                out["skipped"] += 1
            else:
                unsure.append(note)
        for i in range(0, len(unsure), 12):
            batch = unsure[i:i + 12]
            verdicts, usage = self._ours(batch)
            c = cost_of(usage)
            out["pick_cost_usd"] = round(out["pick_cost_usd"] + c, 4)
            self.mongo.db["calls_runs"].insert_one({"at": now(), "step": "pick", "notes": len(batch), "usage": usage, "cost_usd": c})
            for note in batch:
                v = verdicts.get(note["id"]) or {}
                if v.get("ours"):
                    self._keep(note, "content", str(v.get("why") or "")[:200])
                    out["by_content"].append(note["id"])
                else:
                    self._skip(note, "not our work" if v else "no answer")
                    out["skipped"] += 1
        return out

    def _ours(self, notes: Sequence[Dict[str, Any]]):
        body = []
        for n in notes:
            sp = gr.speakers(n)
            body.append(f"--- id: {n['id']}\ntitle: {n.get('title') or '(none)'}\nwhen: {et_label(gr.times(n)['start'])}\n"
                        f"named speakers: {', '.join(sp['named']) or '(none named)'}\nsummary:\n{(n.get('summary_text') or '')[:5000]}")
        r = structured(system=system_blocks(prompts.CORE, _registry_block() + "\n\n" + PICK), user="\n\n".join(body), schema=PICK_SCHEMA,
                       effort=EFFORT, max_tokens=8000, label="calls pick")
        return {str(c.get("id")): c for c in r["data"].get("calls") or []}, r["usage"]

    # ------------------------------------------------------------- the reading
    def read(self, note_id: str, *, again: bool = False) -> Dict[str, Any]:
        call = self.coll.find_one({"note_id": note_id})
        if not call or not call.get("take"):
            raise ValueError(f"call {note_id} is not taken")
        if call.get("reading") and not again:
            return call["reading"]
        turns = call.get("turns") or []
        head = (f"CALL: {call.get('title')}\nBEGAN: {et_label(call.get('started_at'))}   ENDED: {et_label(call.get('ended_at'))}\n"
                f"SPEAKERS AS RECORDED: {', '.join(f'{k} ({v} lines)' for k, v in (call.get('speakers') or {}).items())}\n"
                + ("" if call.get("separated") else "The recorder did not separate the voices on this call: every turn is labelled "
                   f"\"{gr.NOT_SEPARATED}\".\n"))
        # A long meeting that walks through every property (the Monday operations call: an hour and a
        # half, sixty thousand characters) has more to say than one answer can hold — the reply was cut
        # off at the limit twice. Such a call is read in parts of about half an hour each; a property
        # talked about in two parts simply has two sections, which the file update reads together.
        parts = split_turns(turns)
        valid = set(active_property_ids()) | {bc.GENERAL_ID}
        sections: List[Dict[str, Any]] = []
        usage: Dict[str, int] = {}
        overall: List[str] = []
        ours = False
        for n, part in enumerate(parts, 1):
            note = "" if len(parts) == 1 else (f"This is PART {n} OF {len(parts)} of one long call, in order. Read this part on its own; "
                                               "what the other parts say is read separately.\n")
            r = structured(system=system_blocks(prompts.CORE, _registry_block() + "\n\n" + READ), user=head + note + "\nTRANSCRIPT\n" + render_turns(part),
                           schema=READ_SCHEMA, effort=EFFORT, max_tokens=32_000, label=f"call read {note_id}" + (f" part {n}" if len(parts) > 1 else ""))
            d = r["data"]
            add_usage(usage, r["usage"])
            ours = ours or bool(d.get("about_our_work"))
            if d.get("overall"):
                overall.append(str(d["overall"]))
            for s in d.get("sections") or []:
                pid = str(s.get("property_id") or "").strip()
                if pid not in valid:
                    s["topic"] = f"{s.get('topic') or ''} (given as {pid})".strip()
                    pid = bc.GENERAL_ID
                sections.append({"property_id": pid, "topic": str(s.get("topic") or "")[:160], "summary": str(s.get("summary") or "")[:2400],
                                 "points": [{"kind": p.get("kind"), "text": str(p.get("text") or "")[:500], "by": str(p.get("by") or "")[:80],
                                             "quote": str(p.get("quote") or "")[:1200], "at": str(p.get("at") or "")[:12]} for p in s.get("points") or []]})
        removed = check_quotes(sections, turns)
        reading = {"about_our_work": ours and bool(sections), "overall": " ".join(overall)[:2400], "sections": sections, "parts": len(parts),
                   "quotes": sum(1 for s in sections for p in s["points"] if p["quote"]), "quotes_removed": removed, "read_at": now(),
                   "usage": usage, "cost_usd": cost_of(usage)}
        self._cost(note_id, "read", usage)
        self.coll.update_one({"note_id": note_id}, {"$set": {"reading": reading, "status": "read" if reading["about_our_work"] else "nothing_ours",
                                                             "property_ids": sorted({s["property_id"] for s in sections}), "updated_at": now()}})
        return reading

    # ------------------------------------------------------- into the file
    @staticmethod
    def section_text(call: Dict[str, Any], parts: Sequence[Dict[str, Any]]) -> str:
        lines = [f"CALL: {call.get('title')}", f"CALL TIME: {et_label(call.get('started_at'))} ({day(call.get('started_at'))})",
                 f"SPEAKERS AS RECORDED: {', '.join((call.get('speakers') or {}).keys())}"]
        for s in parts:
            lines += ["", f"ABOUT: {s.get('topic')}", s.get("summary") or ""]
            for p in s.get("points") or []:
                q = f" — {p['by']} at {p['at']}: \"{p['quote']}\"" if p.get("quote") else (f" — {p['by']}" if p.get("by") else "")
                lines.append(f"- ({p.get('kind')}) {p.get('text')}{q}")
        return "\n".join(lines)

    def _file_text(self, pid: str) -> str:
        file = self.store.get_file(pid) or {}
        block = property_block(self.mongo, self.store, pid, file=file, with_drafts=False)
        dated = [f"- {f.get('key')}: as of {day(f.get('as_of') or f.get('set_at'))}" for f in file.get("facts") or [] if f.get("as_of") or f.get("set_at")]
        drafts = []
        for d in self.store.list_drafts(pid=pid):
            to = ", ".join(t.get("name") or t.get("email") or "" for t in d.get("to") or [])
            drafts.append(f"[{d['draft_id']}] to {to} — \"{d.get('subject')}\" (written {day(d.get('created_at'))})\n{str(d.get('body') or '')[:1800]}")
        return (block + ("\n\nWHEN EACH FACT WAS LAST SET\n" + "\n".join(dated) if dated else "")
                + "\n\nREPLIES WRITTEN AND WAITING TO BE SENT\n" + ("\n\n".join(drafts) if drafts else "(none)"))

    def apply(self, note_id: str, *, replay: bool = False, again: bool = False) -> Dict[str, Any]:
        """Put one read call into the files it touches. ``replay`` applies to these files what
        the trial decided, without asking the model again."""
        call = self.coll.find_one({"note_id": note_id})
        if not call or not (call.get("reading") or {}).get("sections"):
            return {"note_id": note_id, "applied": {}}
        done = (call.get("applied") or {}).get(self.where) or {}
        by_pid: Dict[str, List[dict]] = {}
        for s in call["reading"]["sections"]:
            by_pid.setdefault(s["property_id"], []).append(s)
        def settled(pid: str, row: Dict[str, Any]) -> Dict[str, Any]:
            # Written down even when nothing was done, so the same call is not picked up again every fifteen minutes.
            row = {**row, "at": now()}
            self.coll.update_one({"note_id": note_id}, {"$set": {f"applied.{self.where}.{pid}": row, "updated_at": now()}})
            return row

        def one(pid: str, parts: List[dict]) -> Dict[str, Any]:
            if pid in done and not again:
                return done[pid]
            if not self.store.get_file(pid):
                return settled(pid, {"skipped": "no file for this property"})
            if replay:
                changes = ((call.get("applied") or {}).get("trial") or {}).get(pid, {}).get("changes")
                if not changes:
                    return settled(pid, {"skipped": "nothing decided in the trial"})
                usage = None
            else:
                user = (f"TODAY: {datetime.now(timezone.utc):%Y-%m-%d}\n\n{self._file_text(pid)}\n\n=== WHAT THE CALL SAID ABOUT THIS PROPERTY ===\n"
                        f"{self.section_text(call, parts)}")
                try:
                    r = structured(system=system_blocks(prompts.CORE, (prompts.general_note() if pid == bc.GENERAL_ID else "") + "\n" + APPLY),
                                   user=user, schema=APPLY_SCHEMA, effort=EFFORT, max_tokens=16_000, label=f"call apply {note_id} {pid}")
                except BrainError as exc:
                    logger.warning("call %s could not be applied to %s: %s", note_id, pid, exc)
                    tries = int(((call.get("apply_tries") or {}).get(self.where) or {}).get(pid) or 0) + 1
                    self.coll.update_one({"note_id": note_id}, {"$set": {f"apply_tries.{self.where}.{pid}": tries}})
                    if tries >= TRIES:      # asked three times: left for a person, not asked for ever
                        return settled(pid, {"error": str(exc)[:300], "gave_up": True})
                    return {"error": str(exc)[:300]}
                changes, usage = r["data"], r["usage"]
                for it in changes.get("items_add") or []:
                    it["id"] = _id("i")  # fixed now, so the trial and the real file get the same to-do
                self._cost(note_id, f"apply {pid}", usage)
            result = self._write(pid, call, parts, changes, keep_text=not replay)
            row = {"changes": changes, **result, "at": now(), **({"cost_usd": cost_of(usage)} if usage else {"replayed": True})}
            self.coll.update_one({"note_id": note_id}, {"$set": {f"applied.{self.where}.{pid}": row, "updated_at": now()}})
            return row

        # One call touches several files, and each file is its own question: they are asked side by side.
        # (Calls themselves are applied one after another, oldest first — see ``run``.)
        from concurrent.futures import ThreadPoolExecutor
        pids = list(by_pid)
        with ThreadPoolExecutor(max_workers=5) as pool:
            out: Dict[str, Any] = dict(zip(pids, pool.map(lambda p: one(p, by_pid[p]), pids)))
        if not self.trial and out:
            self.coll.update_one({"note_id": note_id}, {"$set": {"status": "applied"}})
        return {"note_id": note_id, "applied": out}

    def _write(self, pid: str, call: Dict[str, Any], parts: Sequence[Dict[str, Any]], data: Dict[str, Any], *, keep_text: bool = True) -> Dict[str, Any]:
        """The same writes an email reading makes (see Updater._apply), marked with the call."""
        st, sha, at = self.store, sha_of(call["note_id"]), call.get("started_at") or now()
        file = st.get_file(pid) or {}
        sets: Dict[str, Any] = {}
        changed: List[str] = []
        # A replay lands on a file that has moved on since the trial: its to-dos, facts and log line
        # are still right, but a headline or status written then would wipe out newer words.
        if keep_text:
            if data.get("headline"):
                sets["headline"] = str(data["headline"]).strip()[:400]
                changed.append("headline")
            if data.get("status"):
                sets["status"] = str(data["status"]).strip()[:4000]
                changed.append("status")
        facts = {str(f.get("key")).strip().lower(): f for f in (file.get("facts") or []) if f.get("key")}
        for f in data.get("facts_set") or []:
            k = str(f.get("key") or "").strip()
            if k and str(f.get("value") or "").strip():
                facts[k.lower()] = {"key": k[:80], "value": str(f["value"]).strip()[:400], "source_sha": sha, "set_at": now(), "as_of": at}
        for k in data.get("facts_remove") or []:
            facts.pop(str(k).strip().lower(), None)
        if data.get("facts_set") or data.get("facts_remove"):
            sets["facts"] = list(facts.values())[:60]
            changed.append("facts")
        if sets:
            st.files.update_one({"property_id": pid}, {"$set": {**sets, "updated_at": now()}, "$inc": {"version": 1}})
        closed = sum(1 for c in data.get("items_close") or [] if st.close_item(pid, str(c.get("id") or ""), note=c.get("note") or "", by="call", sha=sha))
        updated = 0
        for u in data.get("items_update") or []:
            fields = {k: u[k] for k in ("title", "detail", "owner") if u.get(k)}
            if u.get("due"):
                fields["due"] = u["due"]
            if fields and st.update_item(pid, str(u.get("id") or ""), fields):
                updated += 1
        have = {i["id"] for i in (st.get_file(pid) or {}).get("open_items") or []}
        added = 0
        for it in data.get("items_add") or []:
            if not str(it.get("title") or "").strip() or it.get("id") in have:
                continue
            item = BrainStore.new_item(title=it["title"], detail=it.get("detail") or "", owner=it.get("owner") or "rakesh", kind=it.get("kind") or "action",
                                       due=it.get("due"), source_sha=sha, opened_at=at, by="call")
            item["id"] = it.get("id") or item["id"]
            st.files.update_one({"property_id": pid}, {"$push": {"open_items": item}, "$set": {"updated_at": now()}, "$inc": {"version": 1}})
            added += 1
        stale = 0
        for d in data.get("drafts_outdated") or []:
            r = st.drafts.update_one({"draft_id": str(d.get("draft_id") or ""), "status": "ready"},
                                     {"$set": {"stale": {"kind": "call", "since": at, "sha": sha, "why": str(d.get("why") or "")[:400]}, "updated_at": now()}})
            stale += r.modified_count
        quotes = [{"by": p["by"], "at": p["at"], "quote": p["quote"], "text": p["text"], "kind": p["kind"]} for s in parts for p in s.get("points") or [] if p.get("quote")]
        st.add_activity(pid, at=at, kind="call", who=", ".join(k for k in (call.get("speakers") or {}) if k != gr.NOT_SEPARATED)[:120],
                        title=data.get("title") or call.get("title") or "Call", summary=data.get("summary") or "", sha=sha,
                        relevance=data.get("relevance") or "material",
                        extra={"changed": changed, "items_added": added, "items_closed": closed, "items_updated": updated, "drafts_outdated": stale,
                               "call": call["note_id"], "quotes": quotes[:12]})
        if "status" in changed or "headline" in changed:
            try:
                from .snapshot import refresh_stage
                refresh_stage(self.mongo, st, pid)
            except Exception:
                logger.exception("stage refresh failed for %s", pid)
        return {"changed": changed, "items_added": added, "items_closed": closed, "items_updated": updated, "drafts_outdated": stale}

    # ---------------------------------------------------------------- runs
    def waiting(self, step: str) -> List[Dict[str, Any]]:
        """Taken calls, oldest first, that still need this step (``read`` or ``apply``) — oldest
        first so that a later call is always applied on top of an earlier one."""
        q: Dict[str, Any] = {"take": True}
        if step == "read":
            q["reading"] = {"$exists": False}
            q["read_tries"] = {"$not": {"$gte": TRIES}}
        else:
            q["reading.about_our_work"] = True
        rows = list(self.coll.find(q, {"note_id": 1, "started_at": 1, "title": 1, "applied": 1, "property_ids": 1}).sort("started_at", 1))
        if step == "apply":
            rows = [r for r in rows if set(r.get("property_ids") or []) - set(((r.get("applied") or {}).get(self.where) or {}).keys())]
        return rows

    def run(self, *, emit=None) -> Dict[str, Any]:
        """Read whatever is taken and unread, then apply whatever is read and not applied here."""
        say = emit or (lambda *_: None)
        out: Dict[str, Any] = {"read": [], "applied": [], "failed": []}
        # Readings do not depend on each other (each is one call against its own transcript), so a
        # backlog is read four at a time. Applying stays one by one, oldest first.
        from concurrent.futures import ThreadPoolExecutor

        def one(c):
            try:
                r = self.read(c["note_id"])
                return {"note_id": c["note_id"], "title": c.get("title"), "sections": len(r["sections"]), "quotes": r["quotes"],
                        "quotes_removed": r["quotes_removed"], "cost_usd": r["cost_usd"]}
            except Exception as exc:  # one bad call must not stop the rest
                logger.exception("call %s could not be read", c["note_id"])
                self.coll.update_one({"note_id": c["note_id"]}, {"$inc": {"read_tries": 1}, "$set": {"read_error": f"{type(exc).__name__}: {exc}"[:300]}})
                return {"note_id": c["note_id"], "step": "read", "error": f"{type(exc).__name__}: {exc}"[:300]}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for row in pool.map(one, self.waiting("read")):
                out["failed" if row.get("error") else "read"].append(row)
                say("read", row)
        for c in self.waiting("apply"):
            try:
                a = self.apply(c["note_id"])
                out["applied"].append({"note_id": c["note_id"], "title": c.get("title"),
                                       "files": {p: {k: v for k, v in r.items() if k != "changes"} for p, r in a["applied"].items()}})
                say("applied", out["applied"][-1])
            except Exception as exc:
                logger.exception("call %s could not be applied", c["note_id"])
                out["failed"].append({"note_id": c["note_id"], "step": "apply", "error": f"{type(exc).__name__}: {exc}"[:300]})
        return out

    def cost(self) -> Dict[str, Any]:
        rows = list(self.coll.find({"take": True}, {"note_id": 1, "title": 1, "started_at": 1, "cost_usd": 1, "costs": 1}))
        pick = sum(r.get("cost_usd") or 0 for r in self.mongo.db["calls_runs"].find({"step": "pick"}, {"cost_usd": 1}))
        steps: Dict[str, float] = {}
        for r in rows:
            for c in r.get("costs") or []:
                k = "read" if c["step"] == "read" else f"apply ({c.get('in')})"
                steps[k] = round(steps.get(k, 0) + (c.get("usd") or 0), 4)
        return {"calls": len(rows), "pick_usd": round(pick, 4), "by_step": steps, "total_usd": round(pick + sum(steps.values()), 4)}

    # ------------------------------------------------------------- for the screens
    @staticmethod
    def public(call: Dict[str, Any], pid: Optional[str] = None) -> Dict[str, Any]:
        r = call.get("reading") or {}
        secs = [s for s in r.get("sections") or [] if not pid or s["property_id"] == pid]
        return {"note_id": call["note_id"], "title": call.get("title"), "started_at": call.get("started_at"), "ended_at": call.get("ended_at"),
                "when": et_label(call.get("started_at")), "speakers": list((call.get("speakers") or {}).keys()), "separated": call.get("separated"),
                "picked_by": call.get("picked_by"), "overall": r.get("overall"), "status": call.get("status"), "web_url": call.get("web_url"),
                "sections": [{**s, "name": name_of(s["property_id"])} for s in secs],
                "applied": {p: {k: v for k, v in a.items() if k in ("changed", "items_added", "items_closed", "items_updated", "drafts_outdated", "at")}
                            for p, a in ((call.get("applied") or {}).get("live") or {}).items() if not pid or p == pid}}

    def list(self, pid: Optional[str] = None, *, limit: int = 60) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"take": True, "reading.about_our_work": True}
        if pid:
            q["property_ids"] = pid
        return [self.public(c, pid) for c in self.coll.find(q, {"turns": 0, "granola_summary": 0, "costs": 0}).sort("started_at", -1).limit(limit)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json
    from mangotree.storage.mongo import get_mongo
    ap = argparse.ArgumentParser(description="Granola calls: pull-past | pull-folder | run | apply-live | cost | status | seed-trial")
    ap.add_argument("action", choices=["pull-past", "pull-folder", "run", "apply-live", "cost", "status", "seed-trial", "read-one"])
    ap.add_argument("--trial", action="store_true", help="work on the trial copy of the property files")
    ap.add_argument("--note", default=None)
    args = ap.parse_args(argv)
    mongo = get_mongo()
    store = TrialStore(mongo) if args.trial or args.action == "seed-trial" else BrainStore(mongo)
    calls = Calls(mongo, store)

    def show(x):
        print(json.dumps(x, indent=1, default=str, ensure_ascii=False))
    if args.action == "seed-trial":
        show(store.seed())
    elif args.action == "pull-past":
        show(calls.pull_past())
    elif args.action == "pull-folder":
        show(calls.pull_folder())
    elif args.action == "read-one":
        show({k: v for k, v in calls.read(args.note, again=True).items() if k != "usage"})
    elif args.action == "run":
        show(calls.run(emit=lambda kind, row: print(kind, json.dumps(row, default=str, ensure_ascii=False)[:400], flush=True)))
    elif args.action == "apply-live":
        live = Calls(mongo, BrainStore(mongo))
        show([live.apply(c["note_id"], replay=True) for c in live.waiting("apply")])
    elif args.action == "cost":
        show(calls.cost())
    else:
        rows = list(calls.coll.find({}, {"note_id": 1, "title": 1, "status": 1, "picked_by": 1, "started_at": 1, "cost_usd": 1, "property_ids": 1}).sort("started_at", 1))
        for r in rows:
            if r.get("status") != "skipped":
                print(f"{et_label(r.get('started_at')):<28} {r.get('status'):<13} {str(r.get('picked_by')):<8} ${r.get('cost_usd') or 0:>5.2f}  {(r.get('title') or '')[:60]}  {r.get('property_ids') or ''}")
        print("taken", sum(1 for r in rows if r.get("status") != "skipped"), "| skipped", sum(1 for r in rows if r.get("status") == "skipped"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
