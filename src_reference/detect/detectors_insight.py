"""
Insight-engine detectors — LLC-formation timing + insurance changes.

  detect_llc_transfer_timing — a David LLC formed shortly BEFORE it receives a
      property transfer is a classic "shell created to hold the asset" pattern.
      We flag conveyances whose grantee is a David LLC formed within N days
      before the transfer date.

  detect_insurance_changes — diff each property's insurance timeline for:
      cancellations, insurer switches, and (highest value) MangoTree being
      dropped from the named-insured / additional-insured — the exact pattern
      seen in the Unitas/Lloyd's removal.

Deterministic over data we already hold; findings carry verbatim evidence.
Follows the detector contract (write=False for a dry read).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.detect.dates import parse_date
from src.detect.detectors import _name_to_entity, _first_david
from src.detect.findings import (Finding, Evidence, upsert_finding, ensure_indexes,
                                  SEV_HIGH, SEV_MEDIUM)

# An LLC formed within this many days BEFORE a transfer it receives is
# "just-in-time" — worth review as a shell-for-transfer.
LLC_TIMING_WINDOW_DAYS = 120


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def detect_llc_transfer_timing(m, *, write: bool = True) -> List[Finding]:
    ents, docs, findings = m.db["entities"], m.db["documents"], m.db["findings"]
    name_idx = _name_to_entity(ents)
    out: List[Finding] = []
    for d in docs.find({"grounded_facts.chain_of_title.0": {"$exists": True}},
                       {"grounded_facts": 1, "property_ids": 1, "property_address": 1}):
        prop = (d.get("property_ids") or [None])[0]
        for it in (d.get("grounded_facts") or {}).get("chain_of_title", []):
            ent = _first_david(it.get("grantee") or "", name_idx)
            if not ent or not ent.get("is_david") or ent.get("kind") != "llc":
                continue
            formed = _aware(ent.get("dos_filing_date"))
            tdate = parse_date(it.get("dated") or it.get("recorded") or "")
            if not formed or not tdate:
                continue
            gap = (tdate - formed).days
            # formed BEFORE the transfer, within the window (0..window)
            if not (0 <= gap <= LLC_TIMING_WINDOW_DAYS):
                continue
            f = Finding(
                finding_type="llc_timing",
                title=f"{ent.get('canonical_name')} formed {gap}d before receiving "
                      f"{d.get('property_address') or prop}",
                detail=(f"{ent.get('canonical_name')} (David network) was formed on "
                        f"{formed.date()} and took title on {tdate.date()} — only "
                        f"{gap} days later. A newly-formed LLC receiving a transfer "
                        f"is a shell-for-transfer pattern; review consideration and "
                        f"purpose."),
                entity_ids=[ent["_id"]], property_id=prop,
                severity=SEV_MEDIUM, confidence=0.55,
                detector="detect_llc_transfer_timing",
                key=f"llctiming|{ent['_id']}|{tdate.date()}|{prop}",
                evidence=[Evidence(doc_id=d["_id"], quote=it.get("source_quote", ""),
                                   note=f"LLC formed {formed.date()}")],
            )
            out.append(f)
            if write:
                upsert_finding(findings, f)
    return out


def _mentions_mangotree(val: Any) -> bool:
    s = str(val or "").lower()
    return "mangotree" in s or "mango tree" in s


def detect_insurance_changes(m, *, write: bool = True) -> List[Finding]:
    docs, findings = m.db["documents"], m.db["findings"]
    proj = {"insurer": 1, "named_insured": 1, "effective_date": 1, "expiration_date": 1,
            "is_cancellation": 1, "certificate_number": 1, "policy_year": 1,
            "covered_addresses": 1, "property_ids": 1, "extracted_text": 1}
    by_prop: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for d in docs.find({"source_type": "insurance"}, proj):
        key = (d.get("property_ids") or [None])[0] or \
              (tuple(d.get("covered_addresses") or []) or "unknown")
        by_prop[key].append(d)

    out: List[Finding] = []
    for prop, policies in by_prop.items():
        policies.sort(key=lambda p: (_aware(p.get("effective_date")) or
                                     datetime(1900, 1, 1, tzinfo=timezone.utc)))
        prev = None
        for p in policies:
            pid = (p.get("property_ids") or [None])[0]
            addr = ", ".join(p.get("covered_addresses") or []) or str(prop)
            # 1) cancellation
            if p.get("is_cancellation"):
                out.append(_ins_finding(
                    "insurance_cancellation",
                    f"Insurance CANCELLED on {addr}",
                    f"A cancellation certificate ({p.get('certificate_number') or 'n/a'}) "
                    f"was recorded for {addr} (insurer {p.get('insurer') or 'n/a'}, "
                    f"effective {(_aware(p.get('effective_date')) or '').__str__()[:10]}). "
                    f"Confirm coverage was replaced.",
                    pid, SEV_HIGH, 0.6, p, findings, write, out_key=f"cxl|{p.get('_id')}"))
            # 2) MangoTree dropped from named insured vs the prior policy
            if prev is not None:
                was_mt = _mentions_mangotree(prev.get("named_insured"))
                now_mt = _mentions_mangotree(p.get("named_insured"))
                if was_mt and not now_mt:
                    out.append(_ins_finding(
                        "insurance_insured_change",
                        f"MangoTree removed from insurance on {addr}",
                        f"MangoTree appears as named/additional insured on the prior "
                        f"policy but NOT on the later one for {addr} "
                        f"(insurer {p.get('insurer') or 'n/a'}, cert "
                        f"{p.get('certificate_number') or 'n/a'}). Loss of "
                        f"mortgagee/additional-insured status — review urgently.",
                        pid, SEV_HIGH, 0.6, p, findings, write,
                        out_key=f"mtdrop|{p.get('_id')}"))
                # 3) insurer switch
                if (prev.get("insurer") and p.get("insurer")
                        and str(prev["insurer"]).lower() != str(p["insurer"]).lower()):
                    out.append(_ins_finding(
                        "insurance_insurer_change",
                        f"Insurer changed on {addr}",
                        f"Insurer changed from {prev.get('insurer')} to {p.get('insurer')} "
                        f"for {addr}. Verify continuity of coverage and MangoTree's status.",
                        pid, SEV_MEDIUM, 0.45, p, findings, write,
                        out_key=f"insurer|{p.get('_id')}"))
            prev = p
    return out


def _ins_finding(ftype, title, detail, pid, sev, conf, p, findings, write, *, out_key):
    f = Finding(
        finding_type=ftype, title=title, detail=detail,
        property_id=pid, severity=sev, confidence=conf,
        detector="detect_insurance_changes", key=out_key,
        evidence=[Evidence(doc_id=p.get("_id"),
                           quote=(p.get("extracted_text") or "")[:200],
                           note=f"cert {p.get('certificate_number') or 'n/a'}")],
    )
    if write:
        upsert_finding(findings, f)
    return f


def run_insight_detectors(m, *, write: bool = True) -> Dict[str, int]:
    if write:
        ensure_indexes(m.db["findings"])
    llc = detect_llc_transfer_timing(m, write=write)
    ins = detect_insurance_changes(m, write=write)
    return {"llc_timing": len(llc), "insurance_changes": len(ins)}


__all__ = ["detect_llc_transfer_timing", "detect_insurance_changes",
           "run_insight_detectors"]
