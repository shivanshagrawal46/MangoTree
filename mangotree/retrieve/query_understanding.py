"""Deterministic query understanding — what the question literally contains.

Runs before any model and costs nothing. It pulls out the things regex is good
at and a model is wasteful for: money amounts, dates and periods, filenames,
quoted phrases, identifiers, property names, senders. Each becomes either a
filter (applied inside the search) or a boost term (rewarded in rescoring) or a
channel trigger (a filename fires the filename channel; a quoted phrase fires the
phrase channel).

It also classifies intent coarsely — factual, temporal, enumeration, comparison,
negative — because the pipeline behaves differently for each: an enumeration
question bypasses similarity for a complete set, a temporal one leans on the
timeline, a comparison in global mode fans out per property.

Everything here is a hint. Opus 5's rewrite may agree, extend, or override.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from mangotree.config.registry import PROPERTIES, properties_named_in

INTENTS = ("factual", "temporal", "enumeration", "comparison", "negative", "procedural")
COMPLEXITIES = ("simple", "moderate", "complex")

_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?(?:\s?(?:k|m|mm|million|thousand))?\b", re.I)
_MONEY_WORDS = re.compile(r"\b(\d[\d,]*(?:\.\d{1,2})?)\s*(dollars|usd)\b", re.I)
_QUOTED = re.compile(r"[\"“”']([^\"“”']{3,120})[\"“”']")
_FILENAME = re.compile(
    r"\b([\w\-. ()&']{2,80}?\.(?:pdf|docx?|xlsx?|csv|pptx?|msg|eml|png|jpe?g|heic|txt|zip))\b", re.I
)
_EXTENSION_WORD = re.compile(r"\b(pdfs?|spreadsheets?|excel|xlsx?|word docs?|docx?|images?|photos?|scans?)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LOAN_ID = re.compile(r"\b(?:loan|note|acct|account|file|policy|order|escrow|case)\s*(?:#|no\.?|number)?\s*[:\-]?\s*([A-Z]{0,4}[\-]?\d{4,})\b", re.I)
_BARE_ID = re.compile(r"\b(\d{6,})\b")
_APN = re.compile(r"\b(\d{2,4}[-\s]\d{2,4}[-\s]\d{2,4}(?:[-\s]\d{1,4})?)\b")
_STREET_ADDRESS = re.compile(
    r"\b(\d{2,6}\s+[A-Z][\w']+(?:\s+[A-Z][\w']+){0,3}\s+"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ct|Court|Ln|Lane|Pl|Place|Blvd|Way|Cir|Circle|Ter|Terrace|Pkwy|Hwy)\b\.?"
    r"(?:\s+(?:NW|NE|SW|SE))?)",
    re.I,
)
_YEAR = re.compile(r"\b(20[0-4]\d|19[89]\d)\b")
_MONTH_YEAR = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(20[0-4]\d)\b", re.I
)
_QUARTER = re.compile(r"\b(q[1-4])\s*(?:of\s*)?(20[0-4]\d)\b", re.I)
_ISO_DATE = re.compile(r"\b(20[0-4]\d)-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20[0-4]\d|\d{2})\b")
_RELATIVE = re.compile(r"\b(last|past|previous)\s+(\d{1,2}|few|couple of|several)?\s*(days?|weeks?|months?|quarters?|years?)\b", re.I)
_BEFORE_AFTER = re.compile(r"\b(before|after|since|until|through|prior to|from)\b", re.I)

_ENUMERATION = re.compile(
    r"\b(all|every|each|list|how many|count|number of|complete|entire|any and all|"
    r"enumerate|inventory|full set|everything|total)\b", re.I
)
_TEMPORAL = re.compile(
    r"\b(when|what date|timeline|chronolog|history|sequence|before|after|since|"
    r"between|during|latest|earliest|most recent|first|last time|over time)\b", re.I
)
_COMPARISON = re.compile(
    r"\b(compare|comparison|versus|vs\.?|across|which propert|between the|differ|"
    r"higher|lower|most|least|rank|best|worst|each property|per property|portfolio)\b", re.I
)
_NEGATIVE = re.compile(
    r"\b(is there|are there|was there|were there|does .{0,40} exist|do we have|"
    r"did .{0,40} ever|any (?:notice|default|lien|lawsuit|claim|complaint)|missing|never|no record)\b", re.I
)
_PROCEDURAL = re.compile(r"\b(how do|how to|process|procedure|steps|policy|rule|should we|allowed)\b", re.I)

_STOP = set("""a an the of to in on for and or is are was were be been by with from at as it its this that these
those what which who whom whose when where why how do does did can could would should shall may might must
have has had i we you they he she me us them our your their any some all every each about into over under
please tell show give find get list""".split())

_DOC_CLASS_WORDS: Dict[str, Tuple[str, ...]] = {
    "deed_of_trust": ("deed of trust", "trust deed", "mortgage", "dot"),
    "note": ("promissory note", "the note"),
    "assignment": ("assignment", "allonge"),
    "guaranty": ("guaranty", "guarantee", "guarantor"),
    "payoff_statement": ("payoff", "pay-off", "payoff statement"),
    "settlement_statement": ("settlement statement", "alta", "hud-1", "hud", "closing statement", "closing disclosure"),
    "title_commitment": ("title commitment", "prelim", "title report"),
    "title_policy": ("title policy", "loan policy", "ltp"),
    "draw_request": ("draw", "draw request", "advance", "disbursement"),
    "invoice": ("invoice", "bill"),
    "inspection_report": ("inspection", "inspection report", "site visit"),
    "appraisal": ("appraisal", "valuation", "bpo", "arv"),
    "insurance": ("insurance", "coi", "builder's risk", "builders risk"),
    "loan_agreement": ("loan agreement", "lending agreement", "construction loan agreement"),
    "extension": ("extension", "modification", "forbearance"),
    "default_notice": ("notice of default", "default", "demand letter", "nod"),
    "wire": ("wire", "wire instructions", "wiring instructions"),
    "operating_agreement": ("operating agreement", "articles of organization", "formation"),
}


@dataclass
class DateRange:
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    source: str = ""

    def as_filter(self, field: str = "date") -> Dict:
        rng = {}
        if self.start:
            rng["$gte"] = self.start
        if self.end:
            rng["$lte"] = self.end
        return {field: rng} if rng else {}

    def describe(self) -> str:
        s = self.start.strftime("%Y-%m-%d") if self.start else "…"
        e = self.end.strftime("%Y-%m-%d") if self.end else "…"
        return f"{s} → {e}"


@dataclass
class QueryUnderstanding:
    raw: str
    normalized: str = ""
    intent: str = "factual"
    intents: List[str] = field(default_factory=list)
    complexity: str = "simple"
    money: List[str] = field(default_factory=list)
    money_values: List[float] = field(default_factory=list)
    quoted: List[str] = field(default_factory=list)
    filenames: List[str] = field(default_factory=list)
    extensions: List[str] = field(default_factory=list)
    identifiers: List[str] = field(default_factory=list)
    addresses: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    property_ids: List[str] = field(default_factory=list)
    ambiguous_property_terms: List[str] = field(default_factory=list)
    date_range: Optional[DateRange] = None
    doc_classes: List[str] = field(default_factory=list)
    boost_terms: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    prefers_latest: bool = False
    prefers_earliest: bool = False

    def exact_tokens(self) -> List[str]:
        """Strings whose literal presence in a chunk is strong evidence."""
        out = list(self.money) + list(self.quoted) + list(self.identifiers) + list(self.addresses)
        return [t for t in out if t]

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["date_range"] = self.date_range.describe() if self.date_range else None
        return d


def _parse_money(token: str) -> Optional[float]:
    m = re.search(r"\d[\d,]*(?:\.\d{1,2})?", token)
    if not m:
        return None
    value = float(m.group(0).replace(",", ""))
    low = token.lower()
    if re.search(r"\b(m|mm|million)\b", low):
        value *= 1_000_000
    elif re.search(r"\b(k|thousand)\b", low):
        value *= 1_000
    return value


def _month_range(year: int, month: int) -> Tuple[datetime, datetime]:
    last = calendar.monthrange(year, month)[1]
    return (datetime(year, month, 1, tzinfo=timezone.utc),
            datetime(year, month, last, 23, 59, 59, tzinfo=timezone.utc))


def _dates(text: str, now: Optional[datetime] = None) -> Optional[DateRange]:
    now = now or datetime.now(timezone.utc)
    low = text.lower()

    m = _QUARTER.search(text)
    if m:
        q = int(m.group(1)[1]); year = int(m.group(2))
        start = datetime(year, 3 * (q - 1) + 1, 1, tzinfo=timezone.utc)
        end_month = 3 * q
        return DateRange(start, _month_range(year, end_month)[1], f"Q{q} {year}")

    m = _ISO_DATE.search(text)
    if m:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        return _directional(low, d, d + timedelta(days=1), m.group(0))

    m = _US_DATE.search(text)
    if m:
        mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 100:
            yr += 2000
        try:
            d = datetime(yr, mo, da, tzinfo=timezone.utc)
            return _directional(low, d, d + timedelta(days=1), m.group(0))
        except ValueError:
            pass

    m = _MONTH_YEAR.search(text)
    if m:
        month = list(calendar.month_abbr).index(m.group(1)[:3].title())
        year = int(m.group(2))
        s, e = _month_range(year, month)
        return _directional(low, s, e, m.group(0))

    m = _RELATIVE.search(text)
    if m:
        n_raw, unit = m.group(2), m.group(3).lower()
        n = {"few": 3, "couple of": 2, "several": 4, None: 1}.get(n_raw, None)
        n = int(n_raw) if n_raw and n_raw.isdigit() else (n or 1)
        days = {"day": 1, "week": 7, "month": 30, "quarter": 91, "year": 365}[unit.rstrip("s")]
        return DateRange(now - timedelta(days=n * days), now, m.group(0))

    years = sorted({int(y) for y in _YEAR.findall(text)})
    if years:
        if len(years) >= 2:
            return DateRange(datetime(years[0], 1, 1, tzinfo=timezone.utc),
                             datetime(years[-1], 12, 31, 23, 59, 59, tzinfo=timezone.utc),
                             f"{years[0]}–{years[-1]}")
        y = years[0]
        return _directional(low, datetime(y, 1, 1, tzinfo=timezone.utc),
                            datetime(y, 12, 31, 23, 59, 59, tzinfo=timezone.utc), str(y))
    return None


def _directional(low: str, start: datetime, end: datetime, label: str) -> DateRange:
    """'before March 2024' opens the start; 'after/since' opens the end."""
    idx = low.find(label.lower())
    prefix = low[max(0, idx - 12):idx] if idx >= 0 else ""
    if re.search(r"\b(before|prior to|until|through)\s*$", prefix):
        return DateRange(None, start if "through" not in prefix else end, f"before {label}")
    if re.search(r"\b(after|since|from)\s*$", prefix):
        return DateRange(start, None, f"since {label}")
    return DateRange(start, end, label)


def understand(query: str, *, now: Optional[datetime] = None) -> QueryUnderstanding:
    q = QueryUnderstanding(raw=query)
    text = " ".join(query.split())
    q.normalized = text
    low = text.lower()

    # --- exact things --------------------------------------------------------
    q.money = [m.group(0).strip() for m in _MONEY.finditer(text)]
    q.money += [f"${m.group(1)}" for m in _MONEY_WORDS.finditer(text)]
    q.money_values = [v for v in (_parse_money(t) for t in q.money) if v is not None]
    q.quoted = [m.group(1).strip() for m in _QUOTED.finditer(text)]
    q.filenames = sorted({m.group(1).strip() for m in _FILENAME.finditer(text)})
    for m in _EXTENSION_WORD.finditer(text):
        word = m.group(1).lower()
        ext = {"pdf": ".pdf", "pdfs": ".pdf", "spreadsheet": ".xlsx", "spreadsheets": ".xlsx",
               "excel": ".xlsx", "xlsx": ".xlsx", "xls": ".xls", "word doc": ".docx", "word docs": ".docx",
               "docx": ".docx", "doc": ".docx", "image": ".jpg", "images": ".jpg", "photo": ".jpg",
               "photos": ".jpg", "scan": ".pdf", "scans": ".pdf"}.get(word)
        if ext and ext not in q.extensions:
            q.extensions.append(ext)
    q.emails = sorted({m.group(0).lower() for m in _EMAIL.finditer(text)})
    ids = {m.group(1) for m in _LOAN_ID.finditer(text)}
    ids |= {m.group(1) for m in _BARE_ID.finditer(text) if m.group(1) not in {y for y in _YEAR.findall(text)}}
    ids |= {m.group(1) for m in _APN.finditer(text)}
    q.identifiers = sorted(ids)
    q.addresses = sorted({m.group(1).strip() for m in _STREET_ADDRESS.finditer(text)})

    # --- properties ----------------------------------------------------------
    q.property_ids = sorted(properties_named_in(text))
    if re.search(r"\bbayshore\b", low) and not {"bayshore_904", "bayshore_910"} & set(q.property_ids):
        q.ambiguous_property_terms.append("bayshore")
        q.property_ids = sorted(set(q.property_ids) | {"bayshore_904", "bayshore_910"})

    # --- time ----------------------------------------------------------------
    # Street numbers are not years: "2000 Chita Ct" must not become the year
    # 2000. Addresses and property aliases are blanked before dates are read.
    dateless = text
    for addr in q.addresses:
        dateless = dateless.replace(addr, " ")
    for prop in PROPERTIES:
        for alias in (prop.canonical_address, *prop.aliases):
            if any(ch.isdigit() for ch in alias):
                dateless = re.sub(re.escape(alias), " ", dateless, flags=re.I)
    q.date_range = _dates(dateless, now)
    q.prefers_latest = bool(re.search(r"\b(latest|most recent|current|newest|last)\b", low))
    q.prefers_earliest = bool(re.search(r"\b(earliest|first|original|initial)\b", low))

    # --- document types ------------------------------------------------------
    for cls, words in _DOC_CLASS_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
            q.doc_classes.append(cls)

    # --- intent ----------------------------------------------------------------
    intents: List[str] = []
    if _ENUMERATION.search(text):
        intents.append("enumeration")
    if _TEMPORAL.search(text) or q.date_range:
        intents.append("temporal")
    if _COMPARISON.search(text) or len(q.property_ids) > 1 and not q.ambiguous_property_terms:
        intents.append("comparison")
    if _NEGATIVE.search(text):
        intents.append("negative")
    if _PROCEDURAL.search(text):
        intents.append("procedural")
    q.intents = intents or ["factual"]
    # Primary intent: the one that changes the pipeline the most.
    for pick in ("enumeration", "comparison", "negative", "temporal", "procedural"):
        if pick in intents:
            q.intent = pick
            break

    # --- complexity ------------------------------------------------------------
    clauses = len(re.findall(r"\b(and|or|but|also|as well as|then|;|\?)\b", low)) + text.count("?")
    signals = len(q.property_ids) + len(q.doc_classes) + len(q.money) + len(q.identifiers) + len(q.filenames)
    words = len(text.split())
    score = (words > 18) + (words > 35) + (clauses >= 2) + (signals >= 3) + (len(intents) >= 2)
    q.complexity = "simple" if score <= 1 else "moderate" if score <= 2 else "complex"

    # --- keywords and boosts -------------------------------------------------
    tokens = [t for t in re.findall(r"[a-z0-9][a-z0-9\-']+", low) if t not in _STOP and len(t) > 2]
    q.keywords = list(dict.fromkeys(tokens))
    q.boost_terms = list(dict.fromkeys(
        q.exact_tokens() + [a for p in PROPERTIES if p.property_id in q.property_ids for a in (p.canonical_address,)]
    ))
    return q
