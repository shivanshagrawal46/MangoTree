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


def install(app, mongo) -> None:
    wbk = mongo.db["deal_intel_workbook"]
    pms = mongo.db["deal_postmortems"]
    reps = mongo.db["deal_intel_reports"]
    briefs = mongo.db["deal_briefs"]
    BRIEF_KEYS = ("one_line", "verdict", "verdict_reason", "what_happened", "went_wrong", "went_right", "lessons")

    @app.get("/sold/portfolio")
    def sold_portfolio(user=CurrentUser):
        wb = wbk.find_one({"kind": "sold_workbook"}, {"_id": 0})
        if not wb:
            raise HTTPException(404, "the sold-properties workbook has not been loaded")
        syn = reps.find_one({"kind": "sold_portfolio_synthesis", "answer": {"$ne": ""}}, {"_id": 0}, sort=[("built_at", -1)])
        rank = _ranking((syn or {}).get("answer") or "")
        have_pm = {d["property_id"]: {"built_at": d.get("built_at"), "cost_usd": d.get("cost_usd")} for d in pms.find({}, {"property_id": 1, "built_at": 1, "cost_usd": 1})}
        brief_by = {b["property_id"]: b for b in briefs.find({}, {"_id": 0})}
        ceo = reps.find_one({"kind": "sold_ceo_brief"}, {"_id": 0})
        rows = []
        for r in wb["rows"]:
            pid = r.get("property_id")
            prop = PROPERTY_INDEX.get(pid) if pid else None
            rows.append({**{k: r.get(k) for k in ("n", "label", "kind", "section", "investment_date", "received_date", "cash_invested", "points_billed", "interest_billed",
                                                  "extension_fee_billed", "rkb_yield", "roi", "days_invested", "annualized_roi", "total_to_collect", "cash_collected", "excess_shortage")},
                         "property_id": pid, "address": prop.canonical_address if prop else r.get("label"), "city": prop.city if prop else None, "state": prop.state if prop else None,
                         "verdict": (rank.get(pid) or {}).get("verdict") if pid else None, "reason": (rank.get(pid) or {}).get("reason") if pid else None,
                         "one_line": (brief_by.get(pid) or {}).get("one_line") if pid else None,
                         "postmortem": have_pm.get(pid) if pid else None})
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
        }
        return {"workbook": {"file": wb.get("file"), "sheet": wb.get("sheet"), "file_mtime": wb.get("file_mtime"), "loaded_at": wb.get("built_at"), "header": wb.get("header")},
                "totals_as_written": wb.get("totals"), "rows": rows, "derived": derived,
                "verdict_counts": {v: sum(1 for r in deals if r["verdict"] == v) for v in ("good", "mixed", "bad")},
                "brief": {k: ceo.get(k) for k in ("headline", "in_plain_words", "biggest_mistakes", "what_worked", "checklist", "decisions", "built_at")} if ceo else None,
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
        syn = reps.find_one({"kind": "sold_portfolio_synthesis", "answer": {"$ne": ""}}, {"_id": 0, "answer": 1}, sort=[("built_at", -1)])
        rank = _ranking((syn or {}).get("answer") or "").get(pid)
        brief = briefs.find_one({"property_id": pid}, {"_id": 0})
        return {"property_id": pid, "address": prop.canonical_address, "city": prop.city, "state": prop.state, "notes": prop.notes,
                "row": row, "verdict": (rank or {}).get("verdict"), "reason": (rank or {}).get("reason"),
                "brief": {k: brief.get(k) for k in BRIEF_KEYS} if brief else None,
                "postmortem": {"model": pm.get("model"), "built_at": pm.get("built_at"), "cost_usd": pm.get("cost_usd"), "tool_calls": (pm.get("budget") or {}).get("tool_calls_used"),
                               "sections": _sections(pm.get("answer") or ""), "sources": pm.get("sources") or []} if pm else None,
                "counts": {"emails": mongo.artifacts.count_documents({"source_type": "email", "property_ids": pid}),
                           "attachments": mongo.artifacts.count_documents({"source_type": "attachment", "property_ids": pid}),
                           "files": mongo.artifacts.count_documents({"source_type": "disk_file", "property_ids": pid})}}
