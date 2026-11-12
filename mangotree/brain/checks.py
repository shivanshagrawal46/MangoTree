"""A mechanical check on what was written: is every figure in it something that
was actually read?

No model is involved. Every dollar amount, percentage and reference number
(case numbers, bill and invoice ids, confirmation codes) in an answer or a draft
is looked for in the text the model had in front of it — the mail, the property
file, the workbooks, every document it opened. Anything not found is reported to
the person, never hidden: it is either the model's own arithmetic, or it is
wrong. Either way he should know before he relies on it.
"""
from __future__ import annotations

import re
from typing import Iterable, List

#: A rounded figure ("about $205.6K", "$1.2M") is an approximation by design and is not checked.
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?![\d.,]*\s?(?:[KkMm]\b|million|thousand))")
_PERCENT = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?%")
#: Letters and digits joined by dashes, or a long code mixing both — 2026-CA-002551,
#: BILL-10040, P2PUGPTZ98BAL4. Plain dates and plain numbers are not references.
_REF = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d{3})[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b|\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d{3})[A-Z0-9]{10,}\b")


_LONG_DECIMAL = re.compile(r"(?<![\d.])\d+\.\d{3,}(?![\d.])")


def _squash(text: str) -> str:
    return re.sub(r"[,\s]", "", text or "")


def _amount_forms(amount: str) -> List[str]:
    """$3,188.90 may sit in a workbook as 3188.9, and $500.00 as 500."""
    d = re.sub(r"[^\d.]", "", amount).strip(".")
    forms = [d]
    if "." in d:
        trimmed = d.rstrip("0").rstrip(".")
        forms.append(trimmed)
        whole, _, frac = d.partition(".")
        if len(frac) == 1:
            forms.append(d + "0")
    return [f for f in dict.fromkeys(forms) if f]


def figures(text: str) -> List[str]:
    """Every checkable figure in a piece of writing, in order, without repeats."""
    found = _MONEY.findall(text or "") + _PERCENT.findall(text or "") + _REF.findall(text or "")
    return list(dict.fromkeys(f.strip() for f in found if f.strip()))


def _rounded(raw: str) -> set:
    """Amounts as a workbook holds them: 6180.148529710765 is $6,180.15 on the page."""
    out = set()
    for n in _LONG_DECIMAL.findall(raw):
        try:
            out.add(f"{round(float(n), 2):.2f}")
        except ValueError:
            pass
    return out


def not_found(text: str, read: Iterable[str]) -> List[str]:
    """Figures in ``text`` that appear nowhere in what was read."""
    raw = "\n".join(r for r in read if r)
    squashed = _squash(raw)
    upper = raw.upper()
    rounded = None
    missing: List[str] = []
    for f in figures(text):
        if f.startswith("$"):
            ok = any(form in squashed for form in _amount_forms(f))
            if not ok:
                if rounded is None:
                    rounded = _rounded(raw)
                try:
                    ok = f"{float(re.sub(r'[^0-9.]', '', f).strip('.')):.2f}" in rounded
                except ValueError:
                    ok = False
        elif f.endswith("%"):
            n = f.rstrip("%").strip()
            ok = (n + "%") in squashed or (n + "percent") in squashed.lower()
            if not ok:
                try:                                      # a workbook holds 17% as 0.17
                    ok = (f"{float(n) / 100:g}") in squashed
                except ValueError:
                    ok = False
        else:
            ok = f.upper() in upper
        if not ok:
            missing.append(f)
    return missing


def texts_of(system_blocks: Iterable[dict], messages: Iterable[dict]) -> List[str]:
    """Everything the model had in front of it during a turn, as plain text."""
    out = [b.get("text") or "" for b in system_blocks if isinstance(b, dict)]
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
            continue
        for b in c or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result" and isinstance(b.get("content"), str):
                out.append(b["content"])
    return out
