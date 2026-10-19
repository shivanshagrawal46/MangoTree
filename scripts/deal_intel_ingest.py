"""Deal Intelligence — ingest the sold-property corpus and write a post-mortem per deal.

    python scripts/deal_intel_ingest.py --phase all        # store, extract, index, mail, timeline, postmortem, synthesis
    python scripts/deal_intel_ingest.py --phase postmortem --only bristol_pl

Admin directive 2026-10-19 (Rakesh Sir via Shivansh): property by property; every
email on the property; from each folder only the spreadsheets from the START of the
deal; the summary workbook's row for the property; then a fast Claude Fable 5.1
post-mortem per deal (<= $10), and by morning a full report — good deals, bad
deals, how to underwrite the next one, the best three and worst three things
per deal, and where RKB made mistakes from start to end.

Every phase is idempotent and resumable: progress is in ``deal_intel_progress``;
a re-run skips what is done. Source of the structured numbers is the workbook
"LP Remodeling Sold Properties Summary.xlsx" (one row per property); every
figure the post-mortem states beyond those must be quoted from a document.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from mangotree.config.registry import PROPERTIES, PROPERTY_INDEX, properties_named_in  # noqa: E402
from mangotree.config.settings import SETTINGS  # noqa: E402
from mangotree.core.logging import logger  # noqa: E402
from mangotree.retrieve import config as cfg  # noqa: E402
from mangotree.storage.mongo import get_mongo  # noqa: E402

SOLD_ROOT = Path(r"E:\LP Remodeling Projects\Sold Properties")
SUMMARY_XLSX = SOLD_ROOT / "LP Remodeling Sold Properties Summary.xlsx"
SOLD_IDS: List[str] = list(cfg.SOLD_PROPERTY_IDS)
MEDIA_SKIP = {".mp4", ".mov", ".avi", ".m4v", ".m4a", ".mp3", ".wav", ".zip"}
EARLY_SPREADSHEET_WINDOW_DAYS = 45
POSTMORTEM_MODEL = "claude-fable-5-1"
POSTMORTEM_MAX_TOOL_CALLS = 10
POSTMORTEM_MAX_COST_USD = 10.0
SYNTHESIS_MODEL = "claude-fable-5-1"

mongo = get_mongo()
PROG = mongo.db["deal_intel_progress"]
PROG.create_index("property_id", unique=True, name="ux_di_prog")
_print_lock = threading.Lock()


def say(msg: str) -> None:
    with _print_lock:
        print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def done(pid: str, stage: str) -> bool:
    d = PROG.find_one({"property_id": pid}, {stage: 1})
    return bool(d and d.get(stage, {}).get("done"))


def mark(pid: str, stage: str, **info) -> None:
    PROG.update_one({"property_id": pid}, {"$set": {f"{stage}": {"done": True, "at": datetime.now(timezone.utc), **info}}}, upsert=True)


# ============================================================ summary workbook
_ADDR_TO_PID = {
    "10923 Bristol": "bristol_pl", "15 Baileys": "baileys_ct", "1015 Drew": "drew_st", "9483 Canary": "canary_dr",
    "20 Farrell": "farrell_ln", "2105 Fort Donelson": "fort_donelson", "16600 Dominion": "dominion_reserve",
    "6181 Occoquan": "occoquan_forest", "4080 Hanson": "hanson_oaks", "47 Metz": "metz_ct", "202 W Park": "w_park_rd",
    "2115 Van Buren": "van_buren", "940 Woodland": "woodland_dr", "11218 Keystone": "keystone_ave", "5309 Ready": "ready_ave",
    "12912 Middlevale": "middlevale", "10618 Beach Mill": "beach_mill", "1833 Bay": "bay_st", "6412 Elliott": "elliott_pl",
    "836 Balboa": "balboa_ave", "13206 Golden": "golden_dr", "116 Lake": "lake_dr", "607 K St": "k_st", "3731 9th": "9th_st_nw",
    "2000 Chita": "chita_ct", "2820 Wood": "wood_st", "5715 Linda": "linda_ln", "10703 Redbird": "redbird_ln", "20428 Crimson": "crimson_pl",
}


def read_summary() -> Dict[str, Dict[str, Any]]:
    import openpyxl
    wb = openpyxl.load_workbook(SUMMARY_XLSX, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = None
    section = "sold"
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cells = list(r)
        if header is None and cells[1] == "Property Address":
            header = cells
            continue
        if header is None:
            continue
        label = str(cells[1] or "")
        if label.startswith("LP sold and kept cash"):
            section = "lp_sold_kept_cash"
            continue
        if not label or label in ("Total", "Grand Total") or label.startswith("Advance against"):
            continue
        pid = next((p for k, p in _ADDR_TO_PID.items() if label.startswith(k)), None)
        if not pid:
            say(f"summary: no property for row '{label}'")
            continue
        def f(i):
            v = cells[i]
            return float(v) if isinstance(v, (int, float)) else None
        out[pid] = {
            "property_id": pid, "section": section, "row_label": label.strip(),
            "investment_date": cells[2], "received_date": cells[3],
            "cash_invested": f(5), "points_billed": f(6), "interest_billed": f(7), "extension_fee_billed": f(8),
            "rkb_yield": f(9), "roi": f(10), "days_invested": int(cells[11]) if isinstance(cells[11], (int, float)) else None,
            "annualized_roi": f(12), "total_to_collect": f(13), "cash_collected": f(14), "excess_shortage": f(15),
            "source": {"file": SUMMARY_XLSX.name, "sheet": ws.title},
        }
    return out


def facts_block(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return "(no row in the summary workbook for this property)"
    def money(v): return "n/a" if v is None else f"${v:,.2f}"
    def pct(v): return "n/a" if v is None else f"{v*100:.1f}%"
    d = lambda v: v.strftime("%Y-%m-%d") if isinstance(v, datetime) else str(v or "n/a")
    sec = "LP sold the property and kept the cash (RKB not paid from the sale)" if row["section"] == "lp_sold_kept_cash" else "sold, RKB paid from the sale"
    return (f"From RKB's workbook '{SUMMARY_XLSX.name}' (row '{row['row_label']}', category: {sec}):\n"
            f"  investment date {d(row['investment_date'])}; received date {d(row['received_date'])}; days invested {row['days_invested']}\n"
            f"  cash invested {money(row['cash_invested'])}; points billed {money(row['points_billed'])}; interest billed {money(row['interest_billed'])}; "
            f"extension fee {money(row['extension_fee_billed'])}\n"
            f"  RKB yield (profit/loss) {money(row['rkb_yield'])}; ROI {pct(row['roi'])}; annualised ROI {pct(row['annualized_roi'])}\n"
            f"  total to be collected {money(row['total_to_collect'])}; cash collected {money(row['cash_collected'])}; excess/(shortage) {money(row['excess_shortage'])}")


# ====================================================================== store
def select_files(folder: Path) -> List[Path]:
    files = [p for p in sorted(folder.rglob("*")) if p.is_file()]
    sheets = [p for p in files if p.suffix.lower() in (".xlsx", ".xls", ".xlsm", ".csv")]
    keep_sheets = set()
    if sheets:
        t0 = min(datetime.fromtimestamp(p.stat().st_mtime) for p in sheets)
        keep_sheets = {p for p in sheets if datetime.fromtimestamp(p.stat().st_mtime) <= t0 + timedelta(days=EARLY_SPREADSHEET_WINDOW_DAYS)}
    out = []
    for p in files:
        ext = p.suffix.lower()
        if ext in MEDIA_SKIP:
            continue
        if ext in (".xlsx", ".xls", ".xlsm", ".csv") and p not in keep_sheets:
            continue
        out.append(p)
    return out


def phase_store(only: Sequence[str]) -> None:
    from mangotree.ingest.disk_ingest import DiskIngestor
    ing = DiskIngestor(mongo, SOLD_ROOT, run_id=datetime.now(timezone.utc).strftime("sold-disk-%Y%m%d-%H%M%S"))
    for pid in only:
        if done(pid, "store"):
            continue
        prop = PROPERTY_INDEX[pid]
        if not prop.disk_folder:
            mark(pid, "store", files=0, note="no disk folder")
            continue
        folder = SOLD_ROOT / prop.disk_folder
        if not folder.exists():
            say(f"store {pid}: folder missing {folder}")
            continue
        chosen = select_files(folder)
        skipped = sum(1 for p in folder.rglob("*") if p.is_file()) - len(chosen)
        shas: List[str] = []
        for path in chosen:
            try:
                before = mongo.artifacts.count_documents({})
                ing._ingest_file(path, prop.disk_folder, pid)
                from mangotree.core.hashing import sha256_file
                if path.suffix.lower() not in (".msg", ".eml"):
                    sha = sha256_file(path)
                    shas.append(sha)
                    # folder is authoritative for a sold deal: make sure the pid is on the artifact
                    mongo.artifacts.update_one({"sha256": sha}, {"$addToSet": {"property_ids": pid}, "$set": {"placement": "property", "scope": "property", "lifecycle": "sold"}})
            except Exception as exc:
                say(f"store {pid}: FAILED {path.name}: {exc}")
        mark(pid, "store", files=len(chosen), skipped=skipped, shas=shas)
        say(f"store {pid}: {len(chosen)} files stored ({skipped} skipped: later spreadsheets / media)")


# ==================================================================== extract
def phase_extract(only: Sequence[str], workers: int = 3) -> None:
    from mangotree.extract.runner import ExtractionRunner

    def one(pid: str):
        if done(pid, "extract"):
            return
        shas = (PROG.find_one({"property_id": pid}) or {}).get("store", {}).get("shas") or []
        # also any attachment/disk artifact on this pid still pending extraction
        pend = [a["sha256"] for a in mongo.artifacts.find({"property_ids": pid, "extraction.status": {"$in": ["pending", None, "partial"]}, "source_type": {"$in": ["disk_file", "attachment"]}}, {"sha256": 1})]
        todo = sorted(set(shas) | set(pend))
        if not todo:
            mark(pid, "extract", n=0); return
        t0 = time.time()
        r = ExtractionRunner(mongo, api_key=SETTINGS.anthropic_api_key, openai_api_key=SETTINGS.openai_api_key or None,
                             run_id=f"sold-extract-{pid}-{datetime.now(timezone.utc):%H%M%S}")
        try:
            st = r.run(only_shas=todo)
        finally:
            try: r.cleanup()
            except Exception: pass
        d = st.as_dict() if hasattr(st, "as_dict") else {}
        mark(pid, "extract", n=len(todo), stats={k: d.get(k) for k in ("processed", "native_pages", "vision_pages", "errors")}, secs=round(time.time() - t0))
        say(f"extract {pid}: {len(todo)} artifacts, vision pages {d.get('vision_pages')}, errors {d.get('errors')}, {round(time.time()-t0)}s")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, only))


# ====================================================================== index
def phase_index() -> None:
    from mangotree.index.indexer import Indexer
    from mangotree.index.questions import QuestionAugmenter
    t0 = time.time()
    ix = Indexer(mongo, api_key=SETTINGS.voyage_api_key, anthropic_api_key=SETTINGS.anthropic_api_key, tier1=True)
    s = ix.run()
    say(f"index: {s.as_dict() if hasattr(s, 'as_dict') else s}")
    q = QuestionAugmenter(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key).run()
    say(f"questions: {q.as_dict()} ({round(time.time()-t0)}s)")
    # chunk placement for sold artifacts
    for pid in SOLD_IDS:
        for a in mongo.artifacts.find({"property_ids": pid, "source_type": "disk_file"}, {"sha256": 1, "property_ids": 1}):
            mongo.chunks.update_many({"artifact_sha": a["sha256"]}, {"$set": {"placement": "property", "scope": "property", "property_ids": a.get("property_ids") or [pid]}})


def phase_summaries() -> None:
    from mangotree.pipeline.arrival import ArrivalChain
    chain = ArrivalChain(mongo)
    shas = [a["sha256"] for a in mongo.artifacts.find({"property_ids": {"$in": SOLD_IDS}}, {"sha256": 1})]
    trace: Dict[str, Any] = {}
    chain._doc_summaries_and_graph(shas, trace)
    say(f"doc summaries: {trace.get('doc_summaries')} new of {len(shas)} artifacts")


# ======================================================================= mail
def phase_mail(only: Sequence[str]) -> None:
    """Every email and attachment already in the corpus that names a sold property
    gets that property added (additively — a live property's placement is never
    removed). Deterministic alias match on subject, body, filenames, text head."""
    from pymongo import UpdateOne
    sold = set(only)
    t0 = time.time()
    cur = mongo.artifacts.aggregate([
        {"$match": {"source_type": {"$in": ["email", "attachment", "upload"]}}},
        {"$project": {"sha256": 1, "subject": 1, "filename": 1, "attachment_names": 1, "property_ids": 1, "placement": 1,
                      "parent_email_shas": 1, "body_clean": {"$substrCP": [{"$ifNull": ["$body_clean", ""]}, 0, 6000]},
                      "text": {"$substrCP": [{"$ifNull": ["$text", ""]}, 0, 4000]}}},
    ], allowDiskUse=True)
    adds: Dict[str, set] = {}
    scanned = 0
    for a in cur:
        scanned += 1
        hay = " \n".join(str(x) for x in [a.get("subject"), a.get("filename"), " ".join(a.get("attachment_names") or []), a.get("body_clean"), a.get("text")] if x)
        hits = properties_named_in(hay) & sold
        new = hits - set(a.get("property_ids") or [])
        if new:
            adds[a["sha256"]] = new
    say(f"mail: scanned {scanned}, {len(adds)} artifacts gain a sold property")
    ops, cops, dops = [], [], []
    per_pid: Dict[str, int] = {}
    for sha, new in adds.items():
        a = mongo.artifacts.find_one({"sha256": sha}, {"property_ids": 1, "placement": 1})
        pids = sorted(set(a.get("property_ids") or []) | new)
        placement = "property"
        ops.append(UpdateOne({"sha256": sha}, {"$set": {"property_ids": pids, "placement": placement, "scope": "property", "sold_mail_placement": {"added": sorted(new), "at": datetime.now(timezone.utc), "by": "alias"}}}))
        cops.append(UpdateOne({"artifact_sha": sha}, {"$set": {"property_ids": pids, "placement": placement, "scope": "property"}}))
        dops.append(UpdateOne({"artifact_sha": sha}, {"$set": {"property_ids": pids, "placement": placement}}))
        for p in new:
            per_pid[p] = per_pid.get(p, 0) + 1
    if ops:
        mongo.artifacts.bulk_write(ops, ordered=False)
        mongo.chunks.bulk_write(cops, ordered=False) if cops else None
        mongo.db["doc_summaries"].bulk_write(dops, ordered=False) if dops else None
    # attachments inherit from their parent email
    inh = 0
    for pid in only:
        emails = [e["sha256"] for e in mongo.artifacts.find({"source_type": "email", "property_ids": pid}, {"sha256": 1})]
        if not emails:
            continue
        for att in mongo.artifacts.find({"parent_email_shas": {"$in": emails}, "property_ids": {"$ne": pid}}, {"sha256": 1, "property_ids": 1}):
            pids = sorted(set(att.get("property_ids") or []) | {pid})
            mongo.artifacts.update_one({"sha256": att["sha256"]}, {"$set": {"property_ids": pids, "placement": "property", "scope": "property"}})
            mongo.chunks.update_many({"artifact_sha": att["sha256"]}, {"$set": {"property_ids": pids, "placement": "property", "scope": "property"}})
            mongo.db["doc_summaries"].update_many({"artifact_sha": att["sha256"]}, {"$set": {"property_ids": pids, "placement": "property"}})
            inh += 1
    for pid in only:
        n_email = mongo.artifacts.count_documents({"source_type": "email", "property_ids": pid})
        n_att = mongo.artifacts.count_documents({"source_type": "attachment", "property_ids": pid})
        n_disk = mongo.artifacts.count_documents({"source_type": "disk_file", "property_ids": pid})
        mark(pid, "mail", emails=n_email, attachments=n_att, disk=n_disk, added=per_pid.get(pid, 0))
        say(f"mail {pid}: emails {n_email}, attachments {n_att}, disk files {n_disk} (+{per_pid.get(pid, 0)} placed now)")
    say(f"mail: attachments inherited {inh}; {round(time.time()-t0)}s")


# =================================================================== timeline
def phase_timeline(only: Sequence[str]) -> None:
    from mangotree.timeline.runner import TimelineBuilder
    todo = [p for p in only if not done(p, "timeline")]
    if not todo:
        return
    t0 = time.time()
    tb = TimelineBuilder(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, concurrency=8)
    out = tb.run(property_ids=todo, use_model=True)
    for p in todo:
        n = mongo.db["timeline_events"].count_documents({"property_id": p})
        mark(p, "timeline", events=n)
    say(f"timeline: {len(todo)} properties in {round(time.time()-t0)}s: {str(out)[:300]}")


# ================================================================= post-mortem
POSTMORTEM_QUESTION = """You are writing the post-mortem of a COMPLETED (sold / closed-out) deal for RKB Consulting Group — the lender that
funded Listing Prophet / LP Remodeling (Wes Stone) on this property. Read the whole file for this property: the investor
package, the note, deed of trust and assignment, the construction status and draw records, inspection reports, every
email and attachment, demand letters, payoffs. The deal is over; your job is to tell RKB honestly what happened, what it
did well, what it did badly, and what it must check before funding the next deal like this.

RKB's own workbook row for this deal is given below as ESTABLISHED FACTS — use these numbers as the money outcome; do not
contradict them, and do not invent any other figure: every other number you state must be quoted from a document with a
citation. Where the records do not establish something, say "not established in the records".

{facts}

Write the post-mortem in exactly these sections, in markdown, each heading on its own line:

## 1. The deal in one paragraph
What the property was, who the parties were (homeowner/borrower, contractor, title, custodian such as NGTC), what RKB
funded and on what terms (amount, points, rate, term, security, lien position), the plan, and how it ended.

## 2. What happened, in order
A dated list of the events that mattered: underwriting, funding, draws, inspections, permits, delays, extensions,
disputes, listing, sale, payoff. Cite the document for each date.

## 3. The money
Planned versus actual: budget vs funded, term vs days outstanding, interest/points billed vs collected, the exit
(sale price, who was paid, what RKB collected), and any shortage — reconciling to the workbook figures above.

## 4. What went wrong — and when it first became visible
Each problem, the FIRST document in which it is visible (date), and how many days after funding. Say explicitly whether
it was knowable at underwriting or only emerged later.

## 5. What went right
What protected RKB's money or made the deal work.

## 6. The best 3 things RKB did on this deal
## 7. The worst 3 things RKB did (or failed to do) on this deal — from start to end
Concrete, cited; mistakes of underwriting, structure, documentation, monitoring, draw control, collection, or follow-up.

## 8. Verdict
GOOD / BAD / MIXED, with the two or three reasons that decide it, in one short paragraph.

## 9. Underwriting lessons — what to check before funding a deal like this
Five to eight specific questions or checks, each tied to what happened here.

## 10. What the records cannot tell us
Facts you looked for and could not establish.

Be concrete, be specific, cite everything, and do not pad."""


def phase_postmortem(only: Sequence[str], workers: int = 2) -> None:
    from mangotree.agent.agent import Agent
    from mangotree.agent.scratchpad import BudgetTracker
    from mangotree.retrieve.scope import Scope
    summary = read_summary()
    coll = mongo.db["deal_postmortems"]
    coll.create_index("property_id", name="ix_pm_pid")

    def one(pid: str):
        if done(pid, "postmortem"):
            return
        prop = PROPERTY_INDEX[pid]
        t0 = time.time()
        say(f"postmortem {pid}: start ({prop.canonical_address})")
        agent = Agent(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key,
                      openai_api_key=SETTINGS.openai_api_key or "", model=POSTMORTEM_MODEL)
        budget = BudgetTracker(max_tool_calls=POSTMORTEM_MAX_TOOL_CALLS, max_wall_clock_s=12 * 60)
        q = POSTMORTEM_QUESTION.format(facts=facts_block(summary.get(pid)))
        try:
            res = agent.run(q, Scope.for_property(pid), critique=False, skeptic=True, budget=budget)
        except Exception as exc:
            say(f"postmortem {pid}: FAILED {type(exc).__name__}: {exc}")
            traceback.print_exc()
            PROG.update_one({"property_id": pid}, {"$set": {"postmortem_error": f"{type(exc).__name__}: {exc}"[:400]}}, upsert=True)
            return
        b = res.budget or {}
        run_cost = 0.0
        try:
            run_cost = float((b.get("run") or {}).get("total_cost_usd") or 0)
        except Exception:
            pass
        cost = float(b.get("planner_cost_usd") or 0) + run_cost
        sources, seen = [], set()
        for h in res.chunks[:80]:
            sha = getattr(h, "artifact_sha", None)
            if sha and sha not in seen:
                seen.add(sha)
                sources.append({"sha256": sha, "name": getattr(h, "display_name", None) or getattr(h, "filename", None), "date": getattr(h, "date", None)})
        doc = {"property_id": pid, "address": prop.canonical_address, "model": agent.model, "question_version": "pm-v1",
               "answer": res.answer, "open_items": list(res.open_items or []), "risks": list(res.risks or []),
               "coverage": res.coverage, "verification": res.verification, "outcome": res.outcome, "forced_reason": res.forced_reason,
               "steps": len(res.steps or []), "elapsed_ms": res.elapsed_ms, "sources": sources[:60],
               "budget": {k: b.get(k) for k in ("tool_calls_used", "max_tool_calls", "input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "planner_cost_usd", "elapsed_s", "run")},
               "cost_usd": round(cost, 2), "summary_row": summary.get(pid), "built_at": datetime.now(timezone.utc)}
        coll.update_one({"property_id": pid}, {"$set": doc}, upsert=True)
        mark(pid, "postmortem", cost_usd=round(cost, 2), tool_calls=b.get("tool_calls_used"), secs=round(time.time() - t0), chars=len(res.answer or ""))
        say(f"postmortem {pid}: done — {b.get('tool_calls_used')} calls, ${cost:.2f}, {round(time.time()-t0)}s, {len(res.answer or '')} chars, outcome={res.outcome}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, only))


# ================================================================== synthesis
SYNTHESIS_PROMPT = """You are the Deal Intelligence analyst for RKB Consulting Group, a private lender that funded Listing Prophet /
LP Remodeling (Wes Stone) on the residential renovation deals below. You have RKB's workbook row for every deal and a
cited post-mortem for each. THE WORKBOOK ROW IS THE AUTHORITATIVE ACCOUNTING for every deal (cash invested, points,
interest, days, collected, shortage) — it is RKB's latest reconciled figure; where a post-mortem's documents show a
different number, use the workbook and note the discrepancy. Write the cross-deal analysis for the CEO (Rakesh Bhargava). Be honest and specific; every
finding must carry the count it rests on and name the deals ("4 of 6 Florida/Maryland deals…"); never a percentage that
hides a small denominator. Groups of fewer than three deals are "too few to judge". No probabilities, no fitted model.

Write in markdown with exactly these sections:

## 1. The portfolio in numbers
Totals and distributions from the workbook rows: capital out, collected, yield, days, annualised ROI; the split between
deals RKB was paid from the sale and deals where LP sold and kept the cash; the worst shortages by name.

## 2. Good deals, bad deals — ranked, with the reason
Every deal on one line: GOOD / MIXED / BAD, days, annualised ROI, shortage, and the single reason that decides it.

## 3. What the good deals had in common
## 4. What the bad deals had in common — the recurring failure sequences
Name the pattern, the deals, and the typical number of days between steps.

## 5. RKB's mistakes, from start to end — the ten that cost the most
Underwriting, structure, documentation, draw control, monitoring, collection, follow-up. Each with the deals that show it.

## 6. What RKB did best — the practices to keep
## 7. The underwriting checklist — questions to answer before funding the next deal
Ten to fifteen, each with the deals that justify it and a weight (how many deals it would have flagged).

## 8. Markets and deal types — only where the count supports it
By state / locality / loan size band; mark "too few to judge" where appropriate.

## 9. What the data cannot tell us
## 10. The three decisions for the CEO
"""


def phase_synthesis() -> Dict[str, Any]:
    import anthropic
    summary = read_summary()
    pms = {d["property_id"]: d for d in mongo.db["deal_postmortems"].find({}, {"_id": 0})}
    parts = []
    for pid in SOLD_IDS + ["9th_st_nw", "chita_ct"]:
        row = summary.get(pid)
        pm = pms.get(pid)
        prop = PROPERTY_INDEX.get(pid)
        if not row and not pm:
            continue
        parts.append(f"\n\n==================== {prop.canonical_address if prop else pid} ({pid}) ====================\n{facts_block(row)}\n\nPOST-MORTEM:\n{(pm or {}).get('answer') or '(no post-mortem — not analysed)'}")
    corpus = "".join(parts)
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key, max_retries=3)
    t0 = time.time()
    # Adaptive thinking at medium effort with a large output budget: at 16k tokens
    # and default effort Fable 5.1 spent the whole budget reasoning and returned
    # no text (2026-10-19). "disabled" is not accepted by this model.
    with client.messages.stream(model=SYNTHESIS_MODEL, max_tokens=48000, thinking={"type": "adaptive"}, output_config={"effort": "medium"},
                                system="You write precise, evidence-bound analysis for a lender's CEO. Counts over percentages. Name deals. No padding.",
                                messages=[{"role": "user", "content": SYNTHESIS_PROMPT + "\n\nTHE DEALS:\n" + corpus}]) as stream:
        resp = stream.get_final_message()
    text = "".join(getattr(b, "text", "") for b in resp.content)
    u = resp.usage
    from mangotree.core.usage import cost_usd
    cost = cost_usd(SYNTHESIS_MODEL, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                    cache_read=getattr(u, "cache_read_input_tokens", 0) or 0, cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0)
    doc = {"kind": "sold_portfolio_synthesis", "model": SYNTHESIS_MODEL, "answer": text, "deals": len(parts), "cost_usd": round(cost, 2),
           "input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "built_at": datetime.now(timezone.utc), "version": "syn-v1"}
    mongo.db["deal_intel_reports"].insert_one(dict(doc))
    say(f"synthesis: {len(parts)} deals, {u.input_tokens} in / {u.output_tokens} out, ${cost:.2f}, {round(time.time()-t0)}s")
    return doc


# ================================================================ deal records
def phase_deals() -> None:
    summary = read_summary()
    coll = mongo.db["deals"]
    coll.create_index("property_id", unique=True, name="ux_deals_pid")
    now = datetime.now(timezone.utc)
    for pid, row in summary.items():
        prop = PROPERTY_INDEX.get(pid)
        fields = {k: {"value": row[k], "as_of": row.get("received_date"), "source": row["source"], "established": row[k] is not None}
                  for k in ("investment_date", "received_date", "cash_invested", "points_billed", "interest_billed", "extension_fee_billed",
                            "rkb_yield", "roi", "days_invested", "annualized_roi", "total_to_collect", "cash_collected", "excess_shortage")}
        coll.update_one({"property_id": pid}, {"$set": {"property_id": pid, "address": prop.canonical_address if prop else row["row_label"],
                                                       "city": prop.city if prop else None, "state": prop.state if prop else None,
                                                       "lifecycle": "sold", "section": row["section"], "fields": fields, "updated_at": now},
                                              "$setOnInsert": {"created_at": now, "version": 1}}, upsert=True)
    say(f"deals: {len(summary)} records written from the workbook")


# ================================================ second analyst: GPT-6 Astra
SECOND_MODEL = "gpt-6-astra"


def phase_second(only: Sequence[str], workers: int = 3, force: bool = False) -> None:
    """An independent post-mortem per deal by GPT-6 Astra: the same question, the
    same retrieval tools, its own searches — it never sees Fable's answer. Stored
    in ``deal_postmortems_second`` so the two can be reconciled."""
    from mangotree.agent.agent import Agent
    from mangotree.agent.scratchpad import BudgetTracker
    from mangotree.retrieve.scope import Scope
    summary = read_summary()
    coll = mongo.db["deal_postmortems_second"]
    coll.create_index("property_id", name="ix_pm2_pid")

    def one(pid: str):
        if not force and done(pid, "second"):
            return
        prop = PROPERTY_INDEX[pid]
        t0 = time.time()
        say(f"second {pid}: start ({prop.canonical_address})")
        agent = Agent(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key,
                      openai_api_key=SETTINGS.openai_api_key or "", model=SECOND_MODEL)
        budget = BudgetTracker(max_tool_calls=POSTMORTEM_MAX_TOOL_CALLS, max_wall_clock_s=12 * 60)
        q = POSTMORTEM_QUESTION.format(facts=facts_block(summary.get(pid)))
        try:
            res = agent.run(q, Scope.for_property(pid), critique=False, skeptic=False, budget=budget)
        except Exception as exc:
            say(f"second {pid}: FAILED {type(exc).__name__}: {exc}")
            traceback.print_exc()
            PROG.update_one({"property_id": pid}, {"$set": {"second_error": f"{type(exc).__name__}: {exc}"[:400]}}, upsert=True)
            return
        b = res.budget or {}
        try:
            cost = float(b.get("planner_cost_usd") or 0) + float((b.get("run") or {}).get("total_cost_usd") or 0)
        except Exception:
            cost = 0.0
        sources, seen = [], set()
        for h in res.chunks[:80]:
            sha = getattr(h, "artifact_sha", None)
            if sha and sha not in seen:
                seen.add(sha)
                sources.append({"sha256": sha, "name": getattr(h, "display_name", None) or getattr(h, "filename", None), "date": getattr(h, "date", None)})
        doc = {"property_id": pid, "address": prop.canonical_address, "model": agent.model, "question_version": "pm-v1",
               "answer": res.answer, "open_items": list(res.open_items or []), "outcome": res.outcome, "steps": len(res.steps or []),
               "elapsed_ms": res.elapsed_ms, "sources": sources[:60],
               "budget": {k: b.get(k) for k in ("tool_calls_used", "max_tool_calls", "input_tokens", "output_tokens", "planner_cost_usd", "elapsed_s", "run")},
               "cost_usd": round(cost, 2), "summary_row": summary.get(pid), "built_at": datetime.now(timezone.utc)}
        coll.update_one({"property_id": pid}, {"$set": doc}, upsert=True)
        mark(pid, "second", cost_usd=round(cost, 2), tool_calls=b.get("tool_calls_used"), secs=round(time.time() - t0), chars=len(res.answer or ""))
        say(f"second {pid}: done — {b.get('tool_calls_used')} calls, ${cost:.2f}, {round(time.time()-t0)}s, {len(res.answer or '')} chars, outcome={res.outcome}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, only))


# ============================================== reconcile the two analysts
RECONCILE_SYSTEM = ("You are the senior reviewer at a private lender. Two analysts (A and B) independently wrote a post-mortem of the "
                    "same deal from the same files. You have both, plus the lender's reconciled workbook row, which is the accounting of "
                    "record. Your job: the single truthful account for the CEO, and — above all — what the first analyst (A) missed or got "
                    "wrong. Be concrete: names, dates, dollar figures as they appear in the analyses. Never invent. Plain English, short "
                    "sentences, a CEO reading on a phone. Reply with JSON only — no prose, no code fence.")

RECONCILE_PROMPT = """Return this JSON:
{
  "verdict": "good" | "mixed" | "bad",
  "verdict_reason": "<= 20 words: the deciding fact",
  "one_line": "<= 22 words: what the deal was and how it ended",
  "what_happened": ["3 bullets, <= 20 words each, in order: funding -> what went on -> how RKB got paid (or not)"],
  "mistakes": [{"text": "<= 20 words, the mistake and its cost or risk", "found_by": "both" | "A" | "B", "evidence": "<= 16 words: the document or date that shows it"}],
  "went_right": ["up to 3 bullets, <= 18 words each"],
  "missed_first_time": ["bullets, <= 24 words each: facts or mistakes B found that A missed or A stated wrongly; [] if none. Write them for the CEO in one voice — 'We missed …' / 'The files show …' — never the words analyst, A or B"],
  "disagreements": [{"topic": "<= 8 words", "a": "<= 18 words", "b": "<= 18 words", "resolution": "<= 20 words, which is better supported and why"}],
  "money_check": "<= 30 words: does the documents' money trail match the workbook row; name any gap in dollars",
  "lessons": ["3 to 4 bullets, <= 16 words each, rules phrased as instructions"]
}
Rules: "mistakes" holds up to 6, ordered by cost. Mark found_by honestly. Use the workbook row's figures for money. No citations like [#3]. No markdown.
Every text the CEO reads (one_line, verdict_reason, what_happened, mistakes, went_right, missed_first_time, money_check, lessons) is your single final
judgement in one voice: never write "Analyst A", "Analyst B", "A said", "B found". The disagreements field is the only place A and B may be named.

WORKBOOK ROW (accounting of record):
{facts}

ANALYST A:
{a}

ANALYST B:
{b}
"""


def phase_reconcile(only: Optional[Sequence[str]] = None, workers: int = 4) -> None:
    """One reconciled brief per deal from both post-mortems and the current
    workbook row, written by Fable 5.1 as reviewer. Stored in ``deal_reconciled``;
    the website prefers it over the single-analyst brief."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    summary = read_summary()
    first = {d["property_id"]: d for d in mongo.db["deal_postmortems"].find({"answer": {"$ne": ""}}, {"_id": 0, "property_id": 1, "answer": 1})}
    second = {d["property_id"]: d for d in mongo.db["deal_postmortems_second"].find({"answer": {"$ne": ""}}, {"_id": 0, "property_id": 1, "answer": 1})}
    pids = [p for p in (only or list(first)) if p in first and p in second]
    missing = [p for p in (only or list(first)) if p not in second]
    if missing:
        say(f"reconcile: no second opinion yet for {missing}")
    coll = mongo.db["deal_reconciled"]
    total = 0.0

    def one(pid):
        prompt = RECONCILE_PROMPT.replace("{facts}", facts_block(summary.get(pid))).replace("{a}", first[pid]["answer"]).replace("{b}", second[pid]["answer"])
        data, cost = _brief_call(prompt, pid, system=RECONCILE_SYSTEM, effort="medium", max_tokens=20000)
        coll.replace_one({"property_id": pid}, {"property_id": pid, **data, "model": SYNTHESIS_MODEL, "analysts": {"A": POSTMORTEM_MODEL, "B": SECOND_MODEL},
                                                "cost_usd": round(cost, 3), "built_at": datetime.now(timezone.utc)}, upsert=True)
        return pid, cost, len(data.get("missed_first_time") or [])

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, p) for p in pids]
        for f in as_completed(futs):
            try:
                pid, cost, n_missed = f.result(); total += cost
                say(f"reconcile {pid}: done ${cost:.2f}, {n_missed} missed-first-time items")
            except Exception as e:  # noqa: BLE001
                say(f"reconcile FAILED: {e}")
    say(f"reconcile: {len(pids)} deals, ${total:.2f}")


PORTFOLIO2_PROMPT = """You have, for each of a private lender's 29 sold deals, the reconciled brief (two analysts, reviewed) and the
workbook totals. Write the CEO's dashboard JSON:
{
  "headline": "<= 22 words: the one sentence the CEO should remember",
  "in_plain_words": ["4 to 5 bullets, <= 22 words each: what the money did overall (invested, collected, still owed, real return vs billed return)"],
  "biggest_mistakes": ["6 bullets, <= 20 words each: the repeated mistakes ordered by money lost, each naming example deals in brackets"],
  "missed_first_time": ["4 to 6 bullets, <= 24 words each: what the second, deeper read surfaced that the first analysis missed, naming deals; one voice — 'We missed …' — never the words analyst, A or B"],
  "what_worked": ["3 to 4 bullets, <= 18 words each, the controls that held, naming deals in brackets"],
  "checklist": ["7 to 9 bullets, <= 14 words each, rules to apply before funding"],
  "decisions": ["3 bullets, <= 22 words each: decisions for the CEO now"],
  "verdict_changes": ["bullets: deals whose verdict changed after review, with the reason; [] if none"]
}
Counts over percentages; name deals; never invent. Use the workbook totals exactly as given. No markdown, no citations.
One voice throughout — the CEO's own adviser — never "analyst A/B", "Fable", "Astra", "the models".

WORKBOOK TOTALS:
{totals}

RECONCILED BRIEFS:
{briefs}
"""


def phase_synthesis2() -> None:
    """Portfolio brief rebuilt from the reconciled briefs (both analysts)."""
    wb = mongo.db["deal_intel_workbook"].find_one({"kind": "sold_workbook"}) or {}
    totals = "\n".join(f"{t.get('which')}: invested ${t.get('cash_invested',0):,.2f}; yield billed ${t.get('rkb_yield',0):,.2f}; total to collect ${t.get('total_to_collect',0):,.2f}; "
                       f"collected ${t.get('cash_collected',0):,.2f}; excess/(shortage) ${t.get('excess_shortage',0):,.2f}" for t in wb.get("totals") or [])
    recs = list(mongo.db["deal_reconciled"].find({}, {"_id": 0, "model": 0, "analysts": 0, "cost_usd": 0, "built_at": 0}))
    rows = {r["property_id"]: r for r in wb.get("rows") or []}
    parts = []
    for r in recs:
        row = rows.get(r["property_id"]) or {}
        head = f"{row.get('label', r['property_id'])}: invested ${row.get('cash_invested') or 0:,.0f}, collected ${row.get('cash_collected') or 0:,.0f}, excess/(shortage) ${row.get('excess_shortage') or 0:,.0f}, {row.get('days_invested')} days"
        parts.append(head + "\n" + json.dumps(r, ensure_ascii=False, default=str))
    prompt = PORTFOLIO2_PROMPT.replace("{totals}", totals).replace("{briefs}", "\n\n".join(parts))
    data, cost = _brief_call(prompt, "portfolio2", system=BRIEF_SYSTEM, effort="medium", max_tokens=20000)
    mongo.db["deal_intel_reports"].replace_one({"kind": "sold_ceo_brief"}, {"kind": "sold_ceo_brief", **data, "model": SYNTHESIS_MODEL, "version": "v2-reconciled",
                                                                            "deals": len(recs), "cost_usd": round(cost, 3), "built_at": datetime.now(timezone.utc)}, upsert=True)
    say(f"synthesis2: {len(recs)} reconciled deals, ${cost:.2f}")


# ======================================= point explanations (click-through)
EXPLAIN_SECTIONS = ("in_plain_words", "biggest_mistakes", "what_worked", "checklist", "decisions")
EXPLAIN_PROMPT = """The CEO clicked this point on his dashboard and wants the detail behind it, deal by deal, like a senior adviser
would explain it across a desk.

SECTION: {section}
POINT: {point}

Return this JSON:
{
  "title": "<= 10 words, the point restated as a heading",
  "what_it_means": "<= 70 words, plain English: what this is and why it cost or saved money",
  "deals": [
    {"property_id": "<id from the list>", "headline": "<= 12 words: what happened on this deal",
     "detail": "<= 80 words with the dates and dollar figures as they appear in the briefs",
     "evidence": "<= 20 words: the document(s) that show it"}
  ],
  "why_it_matters": "<= 50 words: the consequence across the portfolio, with counts and dollars",
  "rule": "<= 25 words: the instruction that prevents it next time"
}
Rules: only deals whose brief actually shows this point (3 to 6 for mistakes and good practice; for portfolio-level
points use the deals that drive the numbers; [] if truly none). Use property_id values exactly as listed. Never invent.
Use workbook figures for money. One voice; never mention analysts, reviews, models or a second pass. No markdown.

DEALS (property_id · address · workbook figures · brief):
{deals}
"""


def _compact_deals_for_explain() -> str:
    wb = mongo.db["deal_intel_workbook"].find_one({"kind": "sold_workbook"}) or {}
    rows = {r["property_id"]: r for r in wb.get("rows") or [] if r.get("property_id")}
    out = []
    for r in mongo.db["deal_reconciled"].find({}, {"_id": 0}):
        pid = r["property_id"]; row = rows.get(pid) or {}
        prop = PROPERTY_INDEX.get(pid)
        fig = (f"invested ${row.get('cash_invested') or 0:,.2f}; due ${row.get('total_to_collect') or 0:,.2f}; collected ${row.get('cash_collected') or 0:,.2f}; "
               f"excess/(shortage) ${row.get('excess_shortage') or 0:,.2f}; {row.get('days_invested')} days; funded {str(row.get('investment_date'))[:10]}; received {str(row.get('received_date'))[:10]}")
        brief = {k: r.get(k) for k in ("one_line", "verdict", "verdict_reason", "what_happened", "mistakes", "went_right", "money_check", "lessons")}
        out.append(f"### {pid} · {prop.canonical_address if prop else pid}\n{fig}\n{json.dumps(brief, ensure_ascii=False)}")
    return "\n\n".join(out)


def phase_explain(workers: int = 4, force: bool = False) -> None:
    """A click-through explanation for every point of the CEO brief, stored in
    ``deal_intel_reports`` (kind sold_point_explanations). Keyed by the point text,
    so an unchanged point keeps its explanation and a reworded one is redone."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    ceo = mongo.db["deal_intel_reports"].find_one({"kind": "sold_ceo_brief"}) or {}
    prev = mongo.db["deal_intel_reports"].find_one({"kind": "sold_point_explanations"}) or {}
    have = {} if force else {(e["section"], e["point"]): e for e in prev.get("items") or []}
    corpus = _compact_deals_for_explain()
    valid = {d["property_id"] for d in mongo.db["deal_reconciled"].find({}, {"property_id": 1})}
    todo = [(sec, pt) for sec in EXPLAIN_SECTIONS for pt in (ceo.get(sec) or []) if (sec, pt) not in have]
    say(f"explain: {len(todo)} points to write, {len(have)} kept")
    total = 0.0

    def one(sec, pt):
        prompt = EXPLAIN_PROMPT.replace("{section}", sec.replace("_", " ")).replace("{point}", pt).replace("{deals}", corpus)
        data, cost = _brief_call(prompt, f"{sec}", system=BRIEF_SYSTEM, effort="medium", max_tokens=16000)
        data["deals"] = [d for d in (data.get("deals") or []) if d.get("property_id") in valid]
        for d in data["deals"]:
            d["address"] = PROPERTY_INDEX[d["property_id"]].canonical_address
        return {"section": sec, "point": pt, **data, "cost_usd": round(cost, 3)}, cost

    items = list(have.values())
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, s, p) for s, p in todo]
        for f in as_completed(futs):
            try:
                item, cost = f.result(); total += cost; items.append(item)
                say(f"explain [{item['section']}] {item['point'][:50]}… ${cost:.2f}, {len(item['deals'])} deals")
            except Exception as e:  # noqa: BLE001
                say(f"explain FAILED: {e}")
    mongo.db["deal_intel_reports"].replace_one({"kind": "sold_point_explanations"}, {"kind": "sold_point_explanations", "items": items, "model": SYNTHESIS_MODEL,
                                                                                     "from_brief": ceo.get("built_at"), "built_at": datetime.now(timezone.utc)}, upsert=True)
    say(f"explain: {len(items)} explanations stored, ${total:.2f}")


# ======================================================= CEO briefs (short form)
BRIEF_SYSTEM = ("You condense a lender's deal post-mortem for its CEO, who reads on a phone. Plain English, short sentences, "
                "concrete names, dates and dollar figures from the text. Never invent; if the post-mortem does not say, leave it out. "
                "Reply with JSON only — no prose, no code fence.")

DEAL_BRIEF_PROMPT = """From the post-mortem below, return this JSON:
{
  "one_line": "<= 22 words: what this deal was and how it ended",
  "verdict": "good" | "mixed" | "bad",
  "verdict_reason": "<= 18 words: the single deciding fact",
  "what_happened": ["3 bullets, <= 20 words each, in order: funding -> what went on -> how RKB got paid (or not)"],
  "went_wrong": ["up to 3 bullets, <= 18 words each, the mistakes that cost money or risk; [] if none"],
  "went_right": ["up to 2 bullets, <= 18 words each, the controls that held; [] if none"],
  "lessons": ["3 bullets, <= 16 words each, rules RKB should apply next time, phrased as instructions"]
}
Keep every bullet a single plain sentence. No citations like [#3]. No markdown.

POST-MORTEM:
"""

PORTFOLIO_BRIEF_PROMPT = """From the cross-deal analysis below (29 sold deals), return this JSON for the CEO's dashboard:
{
  "headline": "<= 20 words: the one sentence the CEO should remember",
  "in_plain_words": ["4 to 5 bullets, <= 22 words each: what the money did overall, said simply (invested, collected, still owed, real return vs billed return)"],
  "biggest_mistakes": ["5 bullets, <= 18 words each, the repeated mistakes, each naming 1-2 example deals in brackets"],
  "what_worked": ["3 to 4 bullets, <= 18 words each, the controls that held on the good deals, naming deals in brackets"],
  "checklist": ["6 to 8 bullets, <= 14 words each, phrased as rules to apply before funding"],
  "decisions": ["3 bullets, <= 20 words each: decisions for the CEO now"]
}
Plain sentences. No citations. No markdown. Use the figures as they appear in the analysis.

ANALYSIS:
"""


def _brief_call(prompt: str, label: str, *, system: str = BRIEF_SYSTEM, effort: str = "low", max_tokens: int = 12000) -> Tuple[Dict[str, Any], float]:
    import anthropic
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key, max_retries=3)
    with client.messages.stream(model=SYNTHESIS_MODEL, max_tokens=max_tokens, thinking={"type": "adaptive"}, output_config={"effort": effort},
                                system=system, messages=[{"role": "user", "content": prompt}]) as stream:
        resp = stream.get_final_message()
    text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"{label}: no JSON in reply ({len(text)} chars)")
    data = json.loads(m.group(0))
    u = resp.usage
    from mangotree.core.usage import cost_usd
    cost = cost_usd(SYNTHESIS_MODEL, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                    cache_read=getattr(u, "cache_read_input_tokens", 0) or 0, cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0)
    return data, cost


def phase_brief(only: Optional[List[str]] = None, workers: int = 4) -> None:
    """Short-form briefs for the website: one per deal (from its post-mortem) and
    one for the portfolio (from the synthesis). Stored in ``deal_briefs`` and
    ``deal_intel_reports`` (kind sold_ceo_brief). Re-run after any post-mortem or
    synthesis changes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    pms = list(mongo.db["deal_postmortems"].find({"answer": {"$ne": ""}}, {"_id": 0, "property_id": 1, "answer": 1}))
    if only:
        pms = [p for p in pms if p["property_id"] in only]
    coll = mongo.db["deal_briefs"]
    total = 0.0

    def one(pm):
        data, cost = _brief_call(DEAL_BRIEF_PROMPT + pm["answer"], pm["property_id"])
        coll.replace_one({"property_id": pm["property_id"]}, {"property_id": pm["property_id"], **data, "model": SYNTHESIS_MODEL, "cost_usd": round(cost, 3), "built_at": datetime.now(timezone.utc)}, upsert=True)
        return pm["property_id"], cost

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, pm) for pm in pms]
        for f in as_completed(futs):
            try:
                pid, cost = f.result(); total += cost
                say(f"brief {pid}: done ${cost:.2f}")
            except Exception as e:  # noqa: BLE001
                say(f"brief FAILED: {e}")
    if not only:
        syn = mongo.db["deal_intel_reports"].find_one({"kind": "sold_portfolio_synthesis", "answer": {"$ne": ""}}, sort=[("built_at", -1)])
        if syn:
            data, cost = _brief_call(PORTFOLIO_BRIEF_PROMPT + syn["answer"], "portfolio"); total += cost
            mongo.db["deal_intel_reports"].replace_one({"kind": "sold_ceo_brief"}, {"kind": "sold_ceo_brief", **data, "model": SYNTHESIS_MODEL, "from_synthesis": syn.get("built_at"),
                                                                                    "cost_usd": round(cost, 3), "built_at": datetime.now(timezone.utc)}, upsert=True)
            say(f"brief portfolio: done ${cost:.2f}")
    say(f"briefs: {len(pms)} deals, ${total:.2f}")


# ============================================================ workbook, verbatim
def phase_workbook() -> None:
    """The summary workbook, row for row, into ``deal_intel_workbook`` — deal rows,
    advance rows and the sheet's own Total / Grand Total rows exactly as written —
    so the website shows RKB's accounting of record, not a recomputation."""
    import openpyxl
    wb = openpyxl.load_workbook(SUMMARY_XLSX, data_only=True)
    ws = wb.worksheets[0]
    cols = ["n", "label", "investment_date", "received_date", "_", "cash_invested", "points_billed", "interest_billed", "extension_fee_billed",
            "rkb_yield", "roi", "days_invested", "annualized_roi", "total_to_collect", "cash_collected", "excess_shortage"]
    rows, totals, header = [], [], None
    section = "paid_from_sale"
    for r in ws.iter_rows(values_only=True):
        c = list(r) + [None] * 16
        if header is None and c[1] == "Property Address":
            header = [str(x) for x in c[:16]]
            continue
        if header is None:
            continue
        label = str(c[1] or "").strip()
        if not label:
            continue
        if label.startswith("LP sold and kept"):
            section = "lp_sold_kept_cash"
            continue
        rec = {k: c[i] for i, k in enumerate(cols) if k != "_"}
        rec["section"] = section
        if label in ("Total", "Grand Total"):
            rec["which"] = "grand" if label == "Grand Total" else section
            totals.append(rec)
            continue
        rec["kind"] = "advance" if label.startswith("Advance against") else "deal"
        rec["property_id"] = next((p for k, p in _ADDR_TO_PID.items() if label.startswith(k)), None)
        rows.append(rec)
    doc = {"kind": "sold_workbook", "file": SUMMARY_XLSX.name, "sheet": ws.title, "file_mtime": datetime.fromtimestamp(SUMMARY_XLSX.stat().st_mtime, tz=timezone.utc),
           "header": header, "rows": rows, "totals": totals, "built_at": datetime.now(timezone.utc)}
    mongo.db["deal_intel_workbook"].replace_one({"kind": "sold_workbook"}, doc, upsert=True)
    say(f"workbook: {len(rows)} rows ({sum(1 for r in rows if r['kind']=='deal')} deals, {sum(1 for r in rows if r['kind']=='advance')} advances), {len(totals)} total rows stored verbatim")


# ======================================================================= main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", help="store|extract|index|summaries|mail|timeline|postmortem|deals|synthesis|all")
    ap.add_argument("--only", default="", help="comma-separated property ids")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="re-run a stage even if marked done")
    args = ap.parse_args()
    only = [p.strip() for p in args.only.split(",") if p.strip()] or (SOLD_IDS + ["9th_st_nw", "chita_ct"])
    phases = [p.strip() for p in args.phase.split(",")]
    if "all" in phases:
        phases = ["store", "extract", "index", "summaries", "mail", "timeline", "deals", "postmortem", "synthesis"]
    say(f"Deal Intelligence ingest — phases {phases} — {len(only)} properties")
    for ph in phases:
        t0 = time.time()
        say(f"=== phase {ph} ===")
        if ph == "store": phase_store(only)
        elif ph == "extract": phase_extract(only, workers=args.workers)
        elif ph == "index": phase_index()
        elif ph == "summaries": phase_summaries()
        elif ph == "mail": phase_mail(only)
        elif ph == "timeline": phase_timeline(only)
        elif ph == "deals": phase_deals(); phase_workbook()
        elif ph == "workbook": phase_workbook()
        elif ph == "brief": phase_brief(only if args.only else None, workers=args.workers)
        elif ph == "second": phase_second(only, workers=args.workers, force=args.force)
        elif ph == "reconcile": phase_reconcile(only if args.only else None, workers=args.workers)
        elif ph == "synthesis2": phase_synthesis2()
        elif ph == "explain": phase_explain(workers=args.workers, force=args.force)
        elif ph == "postmortem": phase_postmortem(only, workers=min(args.workers, 2))
        elif ph == "synthesis": phase_synthesis()
        else: say(f"unknown phase {ph}")
        say(f"=== phase {ph} done in {round(time.time()-t0)}s ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
