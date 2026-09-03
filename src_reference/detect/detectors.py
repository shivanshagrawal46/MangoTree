"""Sprint 4 detectors — read grounded_facts + the entity graph, emit findings.

  detect_anachronisms      (4.4) — instrument executed on behalf of an LLC
                                    BEFORE that LLC legally existed (backdating).
  detect_voidable_transfers(4.8) — UFTA/NY-DCL: property transferred to a
                                    David-insider, tested vs claim/judgment
                                    timeline + value. Voidable-transfer candidate.

Both are deterministic over data we already hold; every finding carries the
verbatim grounded source_quote. No LLM in the rule test itself.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.detect.dates import parse_date
from src.detect.findings import (Finding, Evidence, upsert_finding, ensure_indexes,
                                  SEV_CRITICAL, SEV_HIGH, SEV_MEDIUM)
from src.graph.normalize import norm_name, strip_suffixes
from src.graph.resolve import split_owner_string

_TRANSFER_KEYS = ["chain_of_title", "assignments"]


def _name_to_entity(ents) -> Dict[str, Dict[str, Any]]:
    """norm_name (and suffix-stripped) -> entity doc, for party resolution."""
    idx: Dict[str, Dict[str, Any]] = {}
    for e in ents.find({"kind": {"$in": ["llc", "person", "org"]}, "is_active": {"$ne": False}},
                       {"canonical_name": 1, "name_norm": 1, "aliases": 1, "is_david": 1,
                        "side": 1, "dos_filing_date": 1, "kind": 1}):
        keys = set()
        for a in [e.get("canonical_name"), e.get("name_norm")] + (e.get("aliases") or []):
            if a:
                keys.add(norm_name(a))
                keys.add(strip_suffixes(norm_name(a)))
        for k in keys:
            if k and k not in idx:
                idx[k] = e
    return idx


def _resolve(name: str, name_idx: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    n = norm_name(name)
    return name_idx.get(n) or name_idx.get(strip_suffixes(n))


def _resolve_parties(name: str, name_idx: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve a possibly-COMBINED party string ('A LLC, AS TO 90% AND B, AS
    ADMINISTRATOR...') to all component entities. Fixes the miss where a deed's
    grantee vesting string hid a David LLC inside a multi-party string."""
    out: List[Dict[str, Any]] = []
    seen = set()
    candidates = split_owner_string(name) or [name]
    if name not in candidates:
        candidates = candidates + [name]
    for c in candidates:
        e = _resolve(c, name_idx)
        if e and e["_id"] not in seen:
            seen.add(e["_id"])
            out.append(e)
    return out


def _first_david(name: str, name_idx: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    parties = _resolve_parties(name, name_idx)
    return next((e for e in parties if e.get("is_david")), (parties[0] if parties else None))


def detect_anachronisms(m, *, write: bool = True) -> List[Finding]:
    """Instrument dated BEFORE the acting LLC's formation date => backdating."""
    ents, docs, findings = m.db["entities"], m.db["documents"], m.db["findings"]
    name_idx = _name_to_entity(ents)
    out: List[Finding] = []
    for d in docs.find({"grounded_facts": {"$exists": True}},
                       {"grounded_facts": 1, "property_ids": 1, "property_address": 1}):
        gf = d.get("grounded_facts") or {}
        prop = (d.get("property_ids") or [None])[0]
        # parties that "act" in an instrument: grantee (takes title), borrower (mortgage)
        candidates = []
        for it in gf.get("chain_of_title", []):
            candidates.append((it.get("grantee"), it.get("dated") or it.get("recorded"), "took title", it))
        for it in gf.get("mortgages", []):
            candidates.append((it.get("borrower"), it.get("dated") or it.get("recorded"), "borrowed under mortgage", it))
        for it in gf.get("assignments", []):
            candidates.append((it.get("assignee"), it.get("dated"), "took assignment", it))
        for party, datestr, verb, it in candidates:
            ent = next((e for e in _resolve_parties(party or "", name_idx)
                        if e.get("kind") == "llc" and e.get("dos_filing_date")), None)
            if not ent:
                continue
            inst_date = parse_date(datestr or "")
            formed = ent.get("dos_filing_date")
            if not inst_date or not formed:
                continue
            if hasattr(formed, "tzinfo") and formed.tzinfo is None:
                from datetime import timezone as _tz
                formed = formed.replace(tzinfo=_tz.utc)
            if inst_date < formed:
                f = Finding(
                    finding_type="anachronism",
                    title=f"Corporate anachronism: {ent.get('canonical_name')} {verb} before it existed",
                    detail=(f"{ent.get('canonical_name')} {verb} on {inst_date.date()} but the LLC "
                            f"was not formed until {formed.date()} — a temporal impossibility "
                            f"indicating a backdated/fabricated instrument."),
                    entity_ids=[ent["_id"]], property_id=prop,
                    severity=SEV_CRITICAL, confidence=0.9, detector="detect_anachronisms",
                    key=f"{ent['_id']}|{inst_date.date()}",
                    evidence=[Evidence(doc_id=d["_id"], quote=it.get("source_quote", ""),
                                       note=f"LLC formed {formed.date()}")],
                )
                out.append(f)
                if write:
                    upsert_finding(findings, f)
    return out


def detect_voidable_transfers(m, *, write: bool = True) -> List[Finding]:
    """UFTA/NY-DCL: property conveyed to a David-insider. Severity rises when the
    transfer post-dates the earliest known claim/judgment/lis-pendens date."""
    ents, docs, findings = m.db["entities"], m.db["documents"], m.db["findings"]
    name_idx = _name_to_entity(ents)

    # earliest "claim arose" proxy: earliest litigation document_date / lis pendens
    claim_dates = []
    for d in docs.find({"source_type": "litigation_update"}, {"document_date": 1}):
        if d.get("document_date"):
            claim_dates.append(d["document_date"])
    earliest_claim = min(claim_dates) if claim_dates else None
    if earliest_claim is not None and hasattr(earliest_claim, "tzinfo") and earliest_claim.tzinfo is None:
        from datetime import timezone as _tz
        earliest_claim = earliest_claim.replace(tzinfo=_tz.utc)

    out: List[Finding] = []
    for d in docs.find({"grounded_facts": {"$exists": True}},
                       {"grounded_facts": 1, "property_ids": 1, "property_address": 1}):
        gf = d.get("grounded_facts") or {}
        prop = (d.get("property_ids") or [None])[0]
        for it in gf.get("chain_of_title", []):
            grantee = it.get("grantee")
            ent = _first_david(grantee or "", name_idx)
            if not ent or not ent.get("is_david"):
                continue  # only transfers TO a David-network insider are clawback candidates
            tdate = parse_date(it.get("dated") or it.get("recorded") or "")
            post_claim = bool(earliest_claim and tdate and tdate >= earliest_claim)
            sev = SEV_HIGH if post_claim else SEV_MEDIUM
            detail = (f"Property {d.get('property_address') or prop} conveyed to insider "
                      f"{ent.get('canonical_name')} (David network)"
                      + (f" on {tdate.date()}" if tdate else "")
                      + (f" — AFTER our claim arose ({earliest_claim.date()}): voidable-transfer "
                         f"candidate under UFTA/NY-DCL." if post_claim else
                         " — insider transfer; verify consideration vs market value."))
            f = Finding(
                finding_type="voidable_transfer",
                title=f"Insider conveyance to {ent.get('canonical_name')}",
                detail=detail, entity_ids=[ent["_id"]], property_id=prop,
                severity=sev, confidence=0.75 if post_claim else 0.6,
                detector="detect_voidable_transfers",
                key=f"{ent['_id']}|{tdate.date() if tdate else 'na'}|{prop}",
                evidence=[Evidence(doc_id=d["_id"], quote=it.get("source_quote", ""),
                                   note=("grantor: " + (it.get("grantor") or "?")))],
            )
            out.append(f)
            if write:
                upsert_finding(findings, f)
    return out


def detect_contradictions(m, *, write: bool = True) -> List[Finding]:
    """Party-SCOPED contradiction/omission detection (avoids false positives from
    prior-owner/historical encumbrances in title searches): flag a recorded
    judgment whose DEBTOR resolves to a David entity, and mark it an omission
    when David's own equity schedule shows the property as not in
    foreclosure/litigation."""
    ents, docs, findings = m.db["entities"], m.db["documents"], m.db["findings"]
    doss = m.db["property_dossier"]
    name_idx = _name_to_entity(ents)
    out: List[Finding] = []
    for d in docs.find({"grounded_facts": {"$exists": True}},
                       {"grounded_facts": 1, "property_ids": 1, "property_address": 1}):
        gf = d.get("grounded_facts") or {}
        prop = (d.get("property_ids") or [None])[0]
        dossier = doss.find_one({"_id": prop}, {"equity": 1}) if prop else None
        eq = (dossier or {}).get("equity") or {}
        disclosed = bool(eq.get("active_foreclosure") or eq.get("lis_pendens"))
        for j in gf.get("judgments", []):
            # 4.5 redaction-aware: a redacted field is "withheld", not "omitted by
            # David" — never build a contradiction on a redacted quote.
            q = (j.get("source_quote") or "")
            if re.search(r"\[REDACTED|REDACTED\]|X{4,}|█{2,}", q, re.I):
                continue
            deb = _resolve(j.get("debtor") or "", name_idx)
            if not deb or not deb.get("is_david"):
                continue  # only judgments AGAINST a David entity are scoped-in
            omitted = not disclosed
            f = Finding(
                finding_type="contradiction" if omitted else "encumbrance",
                title=(f"Recorded judgment against {deb.get('canonical_name')}"
                       + (" not reflected in David's equity schedule" if omitted else "")),
                detail=(f"A money judgment names David-network entity "
                        f"{deb.get('canonical_name')} as debtor"
                        + (f" (creditor: {j.get('creditor')})" if j.get('creditor') else "")
                        + (f", amount {j.get('amount')}" if j.get('amount') else "")
                        + (". David's equity schedule represents this property as NOT in "
                           "foreclosure/litigation — an apparent omission." if omitted else ".")),
                entity_ids=[deb["_id"]], property_id=prop,
                severity=SEV_HIGH if omitted else SEV_MEDIUM,
                confidence=0.7, detector="detect_contradictions",
                key=f"jud|{deb['_id']}|{j.get('amount','')}|{j.get('entered','')}",
                evidence=[Evidence(doc_id=d["_id"], quote=j.get("source_quote", ""))],
            )
            out.append(f)
            if write:
                upsert_finding(findings, f)
    return out


def run_all(m) -> Dict[str, int]:
    ensure_indexes(m.db["findings"])
    a = detect_anachronisms(m)
    v = detect_voidable_transfers(m)
    c = detect_contradictions(m)
    return {"anachronisms": len(a), "voidable_transfers": len(v), "contradictions": len(c)}
