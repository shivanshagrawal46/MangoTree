"""Sold deals — the Deal Intelligence dashboard.

Two reads, both RKB staff only:

    GET /sold/portfolio    the workbook verbatim (rows + the sheet's own totals), a
                           verdict and one-line reason per deal from the cross-deal
                           ranking, and the cross-deal analysis split into sections
    GET /sold/deals/{pid}  one deal: its workbook row, the post-mortem in sections
                           (HTML), the sources it read

Money figures come from ``deal_intel_workbook`` — the summary workbook stored row
for row — and are never recomputed here except the clearly-labelled derived lines
(cash profit, annualised) which are shown beside, not instead of, the sheet's own
numbers (admin directive 2026-10-19: the workbook is the accounting of record).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from mangotree.api.auth import CurrentUser
from mangotree.config.registry import PROPERTY_INDEX

_SECTION = re.compile(r"^##\s*(\d+)\.\s*(.*?)\s*$", re.M)


def _sections(md_text: str) -> List[Dict[str, Any]]:
    """Split a markdown answer at its '## N. Title' headings → [{n, title, md, html}]."""
    import markdown
    out = []
    marks = list(_SECTION.finditer(md_text or ""))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md_text)
        body = md_text[m.end():end].strip()
        body = re.sub(r"\[#(\d+)\]", r'<span class="cite">[#\1]</span>', body)
        out.append({"n": int(m.group(1)), "title": m.group(2), "md": body,
                    "html": markdown.markdown(body, extensions=["tables", "sane_lists"])})
    return out


def _ranking(synthesis_md: str) -> Dict[str, Dict[str, str]]:
    """Section 2 of the synthesis is a table: | # | Deal | Verdict | Days | Ann. ROI | Excess | Reason |.
    Map each row to a property id by address prefix."""
    m = re.search(r"^##\s*2\..*?(?=^##\s*\d+\.|\Z)", synthesis_md or "", re.S | re.M)
    out: Dict[str, Dict[str, str]] = {}
    if not m:
        return out
    for line in m.group(0).splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 7 or cells[0] in ("#",):
            continue
        name, verdict, reason = cells[1], cells[2].upper(), cells[-1]
        pid = _pid_for(name)
        if pid and verdict in ("GOOD", "MIXED", "BAD"):
            out[pid] = {"verdict": verdict.lower(), "reason": reason}
    return out


_WORD = re.compile(r"[a-z0-9]+")
_NOISE = {"st", "ave", "dr", "ln", "ct", "pl", "rd", "way", "cir", "blvd", "ter", "nw", "ne", "se", "sw", "va", "md", "dc", "fl", "tx", "n", "s", "e", "w"}


def _pid_for(name: str) -> Optional[str]:
    """'Farrell Ln (VA)', '3731 9th St NW' or '607 K St NE' → the property id whose
    address contains those words. Street words count; a leading house number must
    match if given; suffixes and state tags are ignored."""
    words = _WORD.findall((name or "").lower())
    if not words:
        return None
    number = words[0] if words[0].isdigit() else None
    keys = [w for w in words if w not in _NOISE and not (number and w == number)]
    if not keys:
        keys = [w for w in words if not w.isdigit()]
    best, best_score = None, 0
    for pid, p in PROPERTY_INDEX.items():
        aw = set(_WORD.findall(p.canonical_address.lower()))
        if number and number not in aw:
            continue
        score = sum(1 for k in keys if k in aw)
        if score and keys[0] in aw and score > best_score:
            best, best_score = pid, score
    return best


ROW_KEYS = ("n", "label", "kind", "section", "investment_date", "received_date", "cash_invested", "points_billed", "interest_billed",
            "extension_fee_billed", "rkb_yield", "roi", "days_invested", "annualized_roi", "total_to_collect", "cash_collected", "excess_shortage")
BRIEF_KEYS = ("one_line", "verdict", "verdict_reason", "what_happened", "went_wrong", "went_right", "lessons")
RECON_KEYS = BRIEF_KEYS + ("mistakes", "missed_first_time", "disagreements", "money_check", "analysts", "built_at")
CEO_KEYS = ("headline", "in_plain_words", "biggest_mistakes", "missed_first_time", "what_worked", "checklist", "decisions", "verdict_changes", "version", "built_at")


def _pm_view(pm: Optional[dict]) -> Optional[dict]:
    if not pm:
        return None
    return {"model": pm.get("model"), "built_at": pm.get("built_at"), "cost_usd": pm.get("cost_usd"), "tool_calls": (pm.get("budget") or {}).get("tool_calls_used"),
            "sections": _sections(pm.get("answer") or ""), "sources": pm.get("sources") or []}


def install(app, mongo) -> None:
    wbk = mongo.db["deal_intel_workbook"]
    pms = mongo.db["deal_postmortems"]
    pms2 = mongo.db["deal_postmortems_second"]
    recon = mongo.db["deal_reconciled"]
    briefs = mongo.db["deal_briefs"]
    reps = mongo.db["deal_intel_reports"]

    def _deal_brief(pid: str, brief_by: dict, recon_by: dict) -> Optional[dict]:
        """The reconciled (two-analyst) brief when it exists, else the single-analyst one.
        `went_wrong` is derived from `mistakes` so the UI has one shape."""
        r = recon_by.get(pid)
        if r:
            out = {k: r.get(k) for k in RECON_KEYS}
            out["went_wrong"] = [m.get("text") for m in (r.get("mistakes") or []) if m.get("text")]
            out["reviewed"] = True
            return out
        b = brief_by.get(pid)
        return {**{k: b.get(k) for k in BRIEF_KEYS}, "reviewed": False} if b else None

    @app.get("/sold/portfolio")
    def sold_portfolio(user=CurrentUser):
        wb = wbk.find_one({"kind": "sold_workbook"}, {"_id": 0})
        if not wb:
            raise HTTPException(404, "the sold-properties workbook has not been loaded")
        syn = reps.find_one({"kind": "sold_portfolio_synthesis", "answer": {"$ne": ""}}, {"_id": 0}, sort=[("built_at", -1)])
        rank = _ranking((syn or {}).get("answer") or "")
        brief_by = {b["property_id"]: b for b in briefs.find({}, {"_id": 0})}
        recon_by = {b["property_id"]: b for b in recon.find({}, {"_id": 0})}
        second_ids = {d["property_id"] for d in pms2.find({"answer": {"$ne": ""}}, {"property_id": 1})}
        ceo = reps.find_one({"kind": "sold_ceo_brief"}, {"_id": 0})
        expl = reps.find_one({"kind": "sold_point_explanations"}, {"_id": 0, "items": 1}) or {}
        explanations = {(e["section"], e["point"]): e for e in expl.get("items") or []}
        rows = []
        for r in wb["rows"]:
            pid = r.get("property_id")
            prop = PROPERTY_INDEX.get(pid) if pid else None
            b = _deal_brief(pid, brief_by, recon_by) if pid else None
            verdict = (b or {}).get("verdict") if (b or {}).get("reviewed") else ((rank.get(pid) or {}).get("verdict") if pid else None)
            reason = (b or {}).get("verdict_reason") if (b or {}).get("reviewed") else ((rank.get(pid) or {}).get("reason") if pid else None)
            rows.append({**{k: r.get(k) for k in ROW_KEYS},
                         "property_id": pid, "address": prop.canonical_address if prop else r.get("label"), "city": prop.city if prop else None, "state": prop.state if prop else None,
                         "verdict": verdict, "reason": reason, "one_line": (b or {}).get("one_line"),
                         "first_verdict": (rank.get(pid) or {}).get("verdict") if pid else None,
                         "missed_first_time": (b or {}).get("missed_first_time") or [],
                         "reviewed": bool((b or {}).get("reviewed")), "second_opinion": pid in second_ids})
        deals = [r for r in rows if r["kind"] == "deal"]
        inv = sum(r["cash_invested"] or 0 for r in deals); col = sum(r["cash_collected"] or 0 for r in deals)
        yld = sum(r["rkb_yield"] or 0 for r in deals)
        wdays = (sum((r["days_invested"] or 0) * (r["cash_invested"] or 0) for r in deals) / inv) if inv else 0
        derived = {
            "deals": len(deals), "advances": len(rows) - len(deals),
            "deals_cash_invested": inv, "deals_cash_collected": col, "deals_yield_billed": yld,
            "deals_total_to_collect": sum(r["total_to_collect"] or 0 for r in deals),
            "deals_shortage_sum": sum(min(r["excess_shortage"] or 0, 0) for r in deals),
            "deals_excess_sum": sum(max(r["excess_shortage"] or 0, 0) for r in deals),
            "deals_cash_profit": col - inv, "deals_cash_profit_pct": (col - inv) / inv if inv else None,
            "deals_yield_pct": yld / inv if inv else None,
            "capital_weighted_days": wdays,
            "billed_yield_annualised": (yld / inv) * 365 / wdays if inv and wdays else None,
            "cash_profit_annualised": ((col - inv) / inv) * 365 / wdays if inv and wdays else None,
            "advances_outstanding": sum(r["total_to_collect"] or 0 for r in rows if r["kind"] == "advance"),
            "still_to_collect_all": -sum(min(r["excess_shortage"] or 0, 0) for r in rows),
            "principal_lost": -sum(min((r["cash_collected"] or 0) - (r["cash_invested"] or 0), 0) for r in deals),
            "deals_below_principal": sum(1 for r in deals if (r["cash_collected"] or 0) < (r["cash_invested"] or 0) - 1),
            "reviewed": sum(1 for r in deals if r["reviewed"]), "second_opinions": sum(1 for r in deals if r["second_opinion"]),
        }
        return {"workbook": {"file": wb.get("file"), "sheet": wb.get("sheet"), "file_mtime": wb.get("file_mtime"), "loaded_at": wb.get("built_at"), "header": wb.get("header")},
                "totals_as_written": wb.get("totals"), "rows": rows, "derived": derived,
                "verdict_counts": {v: sum(1 for r in deals if r["verdict"] == v) for v in ("good", "mixed", "bad")},
                "brief": {k: ceo.get(k) for k in CEO_KEYS} if ceo else None,
                "explanations": {sec: [explanations.get((sec, pt)) and {k: explanations[(sec, pt)].get(k) for k in ("title", "what_it_means", "deals", "why_it_matters", "rule")}
                                       for pt in (ceo.get(sec) or [])] for sec in ("in_plain_words", "biggest_mistakes", "what_worked", "checklist", "decisions")} if ceo else None,
                "synthesis": {"model": (syn or {}).get("model"), "built_at": (syn or {}).get("built_at"), "deals": (syn or {}).get("deals"),
                              "sections": _sections((syn or {}).get("answer") or "")} if syn else None}

    @app.get("/sold/deals/{pid}")
    def sold_deal(pid: str, user=CurrentUser):
        prop = PROPERTY_INDEX.get(pid)
        if not prop:
            raise HTTPException(404, "unknown property")
        wb = wbk.find_one({"kind": "sold_workbook"}, {"_id": 0, "rows": 1}) or {}
        row = next((r for r in wb.get("rows") or [] if r.get("property_id") == pid), None)
        pm = pms.find_one({"property_id": pid}, {"_id": 0})
        pm2 = pms2.find_one({"property_id": pid}, {"_id": 0})
        syn = reps.find_one({"kind": "sold_portfolio_synthesis", "answer": {"$ne": ""}}, {"_id": 0, "answer": 1}, sort=[("built_at", -1)])
        rank = _ranking((syn or {}).get("answer") or "").get(pid) or {}
        b = _deal_brief(pid, {pid: briefs.find_one({"property_id": pid}, {"_id": 0}) or {}} if briefs.count_documents({"property_id": pid}) else {},
                        {pid: recon.find_one({"property_id": pid}, {"_id": 0})} if recon.count_documents({"property_id": pid}) else {})
        verdict = b.get("verdict") if b and b.get("reviewed") else rank.get("verdict")
        reason = b.get("verdict_reason") if b and b.get("reviewed") else rank.get("reason")
        return {"property_id": pid, "address": prop.canonical_address, "city": prop.city, "state": prop.state, "notes": prop.notes,
                "row": row, "verdict": verdict, "reason": reason, "first_verdict": rank.get("verdict"), "brief": b,
                "postmortem": _pm_view(pm), "second": _pm_view(pm2),
                "counts": {"emails": mongo.artifacts.count_documents({"source_type": "email", "property_ids": pid}),
                           "attachments": mongo.artifacts.count_documents({"source_type": "attachment", "property_ids": pid}),
                           "files": mongo.artifacts.count_documents({"source_type": "disk_file", "property_ids": pid})}}

