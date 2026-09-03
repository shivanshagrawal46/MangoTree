"""
Sprint 5 detectors — money-flow + communications open-loops.

  detect_instrument_conflicts — the SAME instrument number (cheque/wire #)
      recorded with MATERIALLY DIFFERENT amounts. A cheque/wire number is a
      unique identifier; two different amounts on one number is a concrete
      reconciliation anomaly worth review.

  detect_open_loops — email threads whose LATEST message is an inbound ASK
      to our side (contains a question / "please confirm" / "agree?") with no
      later reply from us. Surfaces decisions/requests waiting on us — the
      "Bill's 'Agree?' is unanswered" class.

Deterministic, read-mostly; emit findings with verbatim evidence. Follows
the existing detector contract (write=False for a dry read).
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from src.detect.findings import (Finding, Evidence, upsert_finding, ensure_indexes,
                                  SEV_HIGH, SEV_MEDIUM, SEV_INFO)

# "our side" — the client team. Anything else is a counterparty/counsel.
OUR_EMAILS = {"rakesh.bhargava@gmail.com", "rakeshsir@mtreh.com"}
OUR_DOMAINS = {"mtreh.com"}

_ASK_RE = re.compile(
    r"(\?|\bplease\s+(confirm|advise|let\s+me\s+know|review|approve|sign)\b|"
    r"\bagree\b|\bcan\s+you\b|\bcould\s+you\b|\bwould\s+you\b|\bawaiting\b|"
    r"\bkindly\b|\bneed\s+your\b)", re.IGNORECASE)

# instrument numbers that are not real identifiers
_BAD_INSTR = {"", "n/a", "na", "none", "unknown", "-", "--", "0", "check", "wire"}


def _is_ours(email: Optional[str]) -> bool:
    if not email:
        return False
    e = email.lower().strip()
    if e in OUR_EMAILS:
        return True
    dom = e.split("@")[-1] if "@" in e else ""
    return dom in OUR_DOMAINS


def detect_instrument_conflicts(m, *, write: bool = True) -> List[Finding]:
    mr, findings = m.db["money_records"], m.db["findings"]
    # Group by (payer, instrument_no): a cheque/wire number is only unique
    # WITHIN a payer's account. #1703 from payer A is unrelated to #1703 from
    # payer B — grouping by number alone manufactures false conflicts.
    by_instr: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for r in mr.find({"instrument_no": {"$nin": [None, ""]}},
                     {"instrument_no": 1, "amount_value": 1, "amount": 1,
                      "payer": 1, "payee": 1, "date": 1, "document_id": 1,
                      "source_quote": 1, "bank": 1}):
        ino = str(r.get("instrument_no") or "").strip().lower()
        if ino in _BAD_INSTR or len(ino) < 3:
            continue
        payer = (r.get("payer") or "").strip().lower()
        if not payer:
            continue  # can't attribute the instrument to an account
        by_instr[(payer, ino)].append(r)

    out: List[Finding] = []
    for (payer, ino), recs in by_instr.items():
        amounts = sorted({round(float(r["amount_value"]), 2)
                          for r in recs if isinstance(r.get("amount_value"), (int, float))})
        if len(amounts) < 2:
            continue
        # Material gap (> $100), not OCR cents/typo noise.
        if amounts[-1] - amounts[0] <= 100.0:
            continue
        # The conflict must span >=2 DIFFERENT source documents — two amounts
        # inside one ledger/OCR page are almost always extraction artifacts,
        # not a substituted instrument.
        doc_ids = {r.get("document_id") for r in recs if r.get("document_id")}
        if len(doc_ids) < 2:
            continue
        payees = sorted({(r.get("payee") or "?") for r in recs})
        ev = [Evidence(doc_id=r.get("document_id"), quote=(r.get("source_quote") or "")[:200],
                       note=f"{r.get('amount')} payer={r.get('payer')} payee={r.get('payee')}")
              for r in recs[:6]]
        f = Finding(
            finding_type="money_conflict",
            title=f"Instrument #{recs[0].get('instrument_no')} recorded with {len(amounts)} different amounts",
            detail=(f"Instrument number {recs[0].get('instrument_no')} appears with materially "
                    f"different amounts ({', '.join(f'${a:,.2f}' for a in amounts)}) across "
                    f"{len(recs)} records; payees: {', '.join(payees[:4])}. A single cheque/wire "
                    f"number should carry one amount — review for a substituted or altered instrument."),
            severity=SEV_MEDIUM, confidence=0.45, detector="detect_instrument_conflicts",
            key=f"instr|{payer}|{ino}", evidence=ev,
        )
        out.append(f)
        if write:
            upsert_finding(findings, f)
    return out


_OPEN_LOOP_SYSTEM = (
    "You triage a legal team's inbox. You are given an inbound REQUEST from an "
    "OUTSIDE party to our side, and OUR SUBSEQUENT REPLIES in that same thread "
    "(which may be empty). Decide whether the request has been RESOLVED by our "
    "replies -- i.e. we substantively answered it or did what was asked. A "
    "reply that merely acknowledges ('will get back to you', 'noted', "
    "'thanks') is NOT resolved. Automated notifications, court e-filing "
    "(NYSCEF/ECF) notices, docket alerts, FYIs, newsletters, and rhetorical "
    "questions are NOT real requests -> resolved=true. "
    "Return STRICT JSON: {\"resolved\": true|false, "
    "\"summary\": \"<one short line: what is being asked of us>\"}."
)


class OpenLoopJudge:
    """GPT-5.5 semantic judge: given the request + OUR replies, is it resolved?
    Uses the dedicated entailment key + model; adaptive params."""

    def __init__(self, *, model: Optional[str] = None, api_key: Optional[str] = None):
        self.model = model or os.getenv("ENTAILMENT_MODEL") or "gpt-5.5"
        self._api_key = (api_key or os.getenv("ENTAILMENT_OPENAI_API_KEY")
                         or os.getenv("OPENAI_API_KEY"))
        self._client = None
        self._lock = threading.Lock()

    def _client_lazy(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from openai import OpenAI
                    self._client = OpenAI(api_key=self._api_key)
        return self._client

    def __call__(self, subject: str, ask_body: str, reply_body: str):
        client = self._client_lazy()
        user = (f"SUBJECT: {subject}\n\nTHEIR REQUEST:\n{(ask_body or '')[:3000]}\n\n"
                f"OUR REPLIES AFTER IT:\n{(reply_body or '').strip() or '(none)'}\n\n"
                f"Return strict JSON.")
        base = {"model": self.model, "max_completion_tokens": 800,
                "messages": [{"role": "system", "content": _OPEN_LOOP_SYSTEM},
                             {"role": "user", "content": user}]}
        try:
            resp = client.chat.completions.create(reasoning_effort="minimal", **base)
        except Exception as exc:  # noqa: BLE001
            if "reasoning_effort" in str(exc):
                resp = client.chat.completions.create(**base)
            else:
                raise
        content = (resp.choices[0].message.content or "").strip()
        mm = re.search(r"\{.*\}", content, re.DOTALL)
        if mm:
            try:
                o = json.loads(mm.group(0))
                return bool(o.get("resolved")), str(o.get("summary") or "")[:200]
            except json.JSONDecodeError:
                pass
        return False, ""  # fail-open: if unclear, treat as NOT resolved (keep it)


def detect_open_loops(m, *, write: bool = True, within_days: Optional[int] = None,
                      judge_fn: Optional[Callable[[str, str, str], Any]] = None) -> List[Finding]:
    # Only surface loops from the last few days of activity — keeps the list
    # tight and relevant. Default 3 days; tune via OPEN_LOOP_WINDOW_DAYS.
    if within_days is None:
        try:
            within_days = int(os.getenv("OPEN_LOOP_WINDOW_DAYS", "3"))
        except ValueError:
            within_days = 3
    emails, findings = m.db["emails"], m.db["findings"]
    threads: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    max_date = None
    for e in emails.find(
        {}, {"thread_id": 1, "date": 1, "from": 1, "to": 1, "subject": 1,
             "body_text": 1}):
        tid = e.get("thread_id") or str(e["_id"])
        threads[tid].append(e)
        d = e.get("date")
        if d and (max_date is None or d > max_date):
            max_date = d

    from datetime import timedelta
    cutoff = (max_date - timedelta(days=within_days)) if max_date else None

    # ---- Phase 1: threads with a recent inbound ASK to us + OUR replies ----
    cands: List[Dict[str, Any]] = []
    for tid, msgs in threads.items():
        dated = [x for x in msgs if x.get("date")]
        if not dated:
            continue
        dated.sort(key=lambda x: x["date"])
        if cutoff is not None and dated[-1]["date"] < cutoff:
            continue  # whole thread is historical
        # most recent inbound ask from an outside party to our side
        ask = None
        for msg in reversed(dated):
            frm = (msg.get("from") or {}).get("email")
            if _is_ours(frm):
                continue
            if not any(_is_ours((t or {}).get("email")) for t in (msg.get("to") or [])):
                continue
            if _ASK_RE.search((msg.get("body_text") or "")[:1500]):
                ask = msg
                break
        if ask is None or (cutoff is not None and ask["date"] < cutoff):
            continue
        # OUR replies AFTER the ask — this is what tells "done vs not done"
        our_replies = [x for x in dated if x["date"] > ask["date"]
                       and _is_ours((x.get("from") or {}).get("email"))]
        reply_text = "\n---\n".join((r.get("body_text") or "")[:1500] for r in our_replies[-2:])
        ask_body = ask.get("body_text") or ""
        mob = _ASK_RE.search(ask_body[:1500])
        i = max(0, mob.start() - 80) if mob else 0
        snippet = ask_body[i:(mob.end() + 80) if mob else 200].strip().replace("\n", " ")
        cands.append({"tid": tid, "ask": ask, "frm": (ask.get("from") or {}).get("email"),
                      "snippet": snippet, "ask_body": ask_body, "reply_text": reply_text,
                      "n_replies": len(our_replies),
                      "subject": ask.get("subject") or "(no subject)"})

    # ---- Phase 2: GPT-5.5 — did OUR replies RESOLVE the request? ----
    use_ai = (judge_fn is not None
              or os.getenv("RAG_OPEN_LOOP_AI", "true").lower() in ("1", "true", "yes"))
    summaries: Dict[str, str] = {}
    if use_ai and cands:
        jf = judge_fn or OpenLoopJudge()

        def _classify(c):
            try:
                resolved, summary = jf(c["subject"], c["ask_body"], c["reply_text"])
            except Exception:  # noqa: BLE001 — fail-open -> treat as unresolved
                resolved, summary = False, ""
            return c["tid"], resolved, summary

        keep: set = set()
        workers = max(1, min(6, len(cands)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for tid, resolved, summary in ex.map(_classify, cands):
                summaries[tid] = summary
                if not resolved:
                    keep.add(tid)  # keep only what is NOT done
        cands = [c for c in cands if c["tid"] in keep]
    else:
        # deterministic fallback: open only if we never replied after the ask
        cands = [c for c in cands if c["n_replies"] == 0]

    # ---- Phase 3: emit (only UNRESOLVED = still waiting on us) ----
    out: List[Finding] = []
    for c in cands:
        ask, tid = c["ask"], c["tid"]
        when = ask["date"].date() if hasattr(ask.get("date"), "date") else ask.get("date")
        summ = summaries.get(tid) or ""
        status = ("we replied but it appears UNRESOLVED" if c["n_replies"] > 0
                  else "no reply on record")
        detail = (f"Inbound request from {c['frm']} ({when}) - {status}."
                  + (f" Request: {summ}" if summ else f" Ask: \"{c['snippet'][:180]}\""))
        f = Finding(
            finding_type="open_loop",
            title=f"Unanswered request: {c['subject']}",
            detail=detail, severity=SEV_MEDIUM, confidence=0.55,
            detector="detect_open_loops", key=f"loop|{tid}",
            evidence=[Evidence(chunk_id=str(ask["_id"]), quote=c["snippet"][:280],
                               note=f"from {c['frm']} | our replies after ask: {c['n_replies']}")],
        )
        out.append(f)
        if write:
            upsert_finding(findings, f)
    return out


def run_flow_detectors(m, *, write: bool = True) -> Dict[str, int]:
    if write:
        ensure_indexes(m.db["findings"])
    ic = detect_instrument_conflicts(m, write=write)
    ol = detect_open_loops(m, write=write)
    return {"money_conflict": len(ic), "open_loop": len(ol)}


__all__ = ["detect_instrument_conflicts", "detect_open_loops", "run_flow_detectors"]
