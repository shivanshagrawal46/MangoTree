"""Detect blank template / sample documents.

Admin decision 2026-08-31: templates stay **fully retrievable** — a question
asked against the sample form should find it. The danger was never that it is
searchable; it is that it looks executed.

`DOT-Note- DMV SAMPLE.pdf` is 34,000 characters of authentic commercial
deed-of-trust language whose parties were never filled in — `between HOMEOWNER,
with a business address of PROPERTY ADDRESS` — yet it carries a leftover
`Loan Amount: $131,762.75` and an October 2022 date from whatever real deal the
form was cut from. Varnum's actual loan is $1,004,492.04. Indexed under Varnum
with no marking, it can answer "what are the Varnum loan terms" with a figure
wrong by a factor of eight, in language indistinguishable from the real
instrument.

So we mark rather than hide. A flagged document is prefixed in its own context so
every chunk announces itself as a blank form, and any answer citing it inherits
that caveat. It stays in the index, stays attached to its property, and stays
findable.

This also generalises: a corpus assembled from ten deals' worth of folders very
likely holds more blank forms filed beside real ones, and the tell is cheap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence

#: Placeholder tokens. Deliberately upper-case-sensitive where the token is an
#: ordinary word: "homeowner" appears constantly in real documents, whereas
#: `HOMEOWNER` standing in a party slot is a form field nobody completed.
_PLACEHOLDER_PATTERNS: Sequence[tuple[str, str]] = (
    (r"\bHOMEOWNER\b", "HOMEOWNER placeholder"),
    (r"\bPROPERTY ADDRESS\b", "PROPERTY ADDRESS placeholder"),
    (r"\bBORROWER NAME\b", "BORROWER NAME placeholder"),
    (r"\bLENDER NAME\b", "LENDER NAME placeholder"),
    (r"\bINSERT\s+(?:NAME|DATE|ADDRESS|AMOUNT)\b", "INSERT directive"),
    (r"\[\s*(?:INSERT|TBD|XXX+|N/?A)\s*\]", "bracketed placeholder"),
    (r"_{6,}", "blank fill-in rule"),
    (r"\bXX/XX/(?:XX|XXXX)\b", "placeholder date"),
    (r"\$\s*X{2,}", "placeholder amount"),
    (r"\bCLIENT NAME\b", "CLIENT NAME placeholder"),
)

#: Words in the filename that declare the document's own status.
_FILENAME_MARKERS = ("sample", "template", "blank", "form only", "specimen", "draft form")

_COMPILED = tuple((re.compile(p), label) for p, label in _PLACEHOLDER_PATTERNS)


@dataclass
class TemplateVerdict:
    is_template: bool
    confidence: float
    signals: List[str]

    @property
    def reason(self) -> str:
        return "; ".join(self.signals) if self.signals else ""


def detect_template(filename: str, text: str, *, doc_class: str = "") -> TemplateVerdict:
    """Is this a blank form rather than an executed document?

    Two independent families of evidence, because either alone misfires. A
    filename saying "SAMPLE" could be a real signed document someone named
    carelessly; placeholder tokens alone could appear in a genuine exhibit that
    quotes a form. Together they are decisive.
    """
    signals: List[str] = []
    lower_name = (filename or "").lower()
    body = text or ""

    name_hits = [marker for marker in _FILENAME_MARKERS if marker in lower_name]
    for marker in name_hits:
        signals.append(f"filename says '{marker}'")

    placeholder_hits: List[str] = []
    for pattern, label in _COMPILED:
        found = pattern.findall(body)
        if found:
            placeholder_hits.append(f"{label} x{len(found)}")
    signals.extend(placeholder_hits)

    # Scoring. Filename alone is suggestive; placeholders alone are suggestive;
    # both together is as close to certain as a heuristic gets. Anything below
    # the threshold is left alone rather than guessed at — a real document
    # wrongly marked "blank form" would be dismissed by a reader, which is its
    # own kind of damage.
    confidence = 0.0
    if name_hits:
        confidence += 0.45
    if placeholder_hits:
        confidence += 0.30 + min(0.20, 0.05 * len(placeholder_hits))
    if name_hits and placeholder_hits:
        confidence += 0.20

    confidence = min(confidence, 0.99)
    return TemplateVerdict(
        is_template=confidence >= 0.60,
        confidence=round(confidence, 2),
        signals=signals,
    )


#: Prefix injected ahead of a flagged document's context. Written so it survives
#: being read in isolation by both a retrieval model and a human skimming a
#: citation — the caveat has to travel with the text, not sit in a metadata field
#: nobody renders.
TEMPLATE_NOTICE = (
    "BLANK TEMPLATE / SAMPLE FORM — not an executed document. Party names, "
    "addresses, amounts and dates in this document are unfilled form fields or "
    "leftovers from the form's original source, and must NOT be read as terms of "
    "any actual deal."
)


def template_context_prefix(verdict: TemplateVerdict) -> str:
    if not verdict.is_template:
        return ""
    return TEMPLATE_NOTICE
