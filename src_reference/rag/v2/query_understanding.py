"""
Query Understanding — extract structured signals from a natural-language query.

Why this exists:
  Pure semantic search treats every query as a "blob of text". For legal
  RAG we lose accuracy if we don't pull out the structured signals the
  user is implicitly asking about — dates, dollar amounts, party names,
  filenames, case numbers, intent.

  Once extracted, these become FILTERS on the search side and HINTS to
  the reranker / scorer. This catches the queries where pure embedding
  similarity fails (e.g. "$450,000" or "Global Stipulation").

Design notes:
  • Pure regex / heuristic — no LLM call. Fast (< 1ms) and deterministic.
  • All extractors fail closed: if nothing found, return empty list.
  • All extracted spans preserve the original surface form (for keyword
    boosting in the reranker) AND a normalised form (for filtering).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


# ---------------------------------------------------------------------------
# Patterns — kept at module level so they compile once.
# ---------------------------------------------------------------------------

# Money: $1,234,567.89 / USD 1.5M / $450K / 1.2 million dollars
# We allow bare $ (most common), US$, USD, and the trailing-dollar form.
_MONEY_RE = re.compile(
    r"""
    (?:
        (?:US?\$|USD\s*|\$)\s*([\d,]+(?:\.\d+)?)        # $1,234 / US$1,234 / USD 1234
        (?:\s*(?:million|mil|m|k|thousand|bn|billion))?
      |
        (\d[\d,]*(?:\.\d+)?)\s*(?:dollar|usd|m\b|k\b|million|billion)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Bare comma-separated number that LOOKS like a money figure even without
# a $ sign. We match a number with at least one comma OR 5+ contiguous
# digits. Only triggered when the surrounding query context suggests a
# financial query (handled in extract_signals via _MONEY_CONTEXT_RE).
_BARE_NUMBER_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{5,}(?:\.\d+)?)\b")

# Hint words that, when present in the query, mean we should treat bare
# numbers as money candidates. Without this, "page 100,000" or "row 12345"
# would falsely trip the money detector.
_MONEY_CONTEXT_RE = re.compile(
    r"\b(amount|money|payment|paid|paid?ing|owe|owed|sum|figure|number|"
    r"settle\w*|escrow|proceeds|funds?|cost|fee|price|dollars?|usd|"
    r"\$|deposit|wire|wired|transferr\w*|debit|credit)\b",
    re.IGNORECASE,
)

# Calendar dates — multiple formats. We collect surface forms; conversion
# to datetime happens later for the ones we can confidently parse.
_DATE_PATTERNS: List[re.Pattern] = [
    # 2026-01-20 / 2026/01/20
    re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b"),
    # 01-20-2026 / 01/20/2026  (US m-d-y)
    re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b"),
    # 20 January 2026 / January 20, 2026 / Jan 20 2026
    re.compile(
        r"\b(?:(\d{1,2})\s+)?"
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
        r"(?:\s+(\d{1,2}))?(?:[,\s]+)(20\d{2})\b",
        re.IGNORECASE,
    ),
    # 01-20-26 / 01/20/26  (US m-d-yy)  — common in filenames.
    # We assume yy < 70 → 20yy, else 19yy. Bounded to plausible legal
    # corpus range (2000-2069 / 1970-1999).
    re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2})\b"),
    # "Jan 20-26" / "Jan 20 26" — month name + day + 2-digit year
    re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
        r"\s+(\d{1,2})[-\s/]+(\d{2})\b",
        re.IGNORECASE,
    ),
]
_MONTH_TO_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Year-only references: "in 2024", "during 2026"
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

# Year-range references: "from 2021 to 2024"
_YEAR_RANGE_RE = re.compile(
    r"\b(?:between|from)\s+(20\d{2})\s+(?:to|and|-|–)\s+(20\d{2})\b",
    re.IGNORECASE,
)

# Filenames — anything that looks like a filename ending in a known extension.
# We also accept underscored/hyphenated names without extensions if quoted.
_FILENAME_RE = re.compile(
    r"""
    (
      (?:[\w\.\-\s]+)
      \.(?:pdf|docx?|xlsx?|pptx?|txt|csv|jpg|jpeg|png|tiff?|html?|eml|msg)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Quoted strings — explicit document names: "Global Stipulation"
_QUOTED_RE = re.compile(r"[\"\u201c]([^\"\u201d]{3,80})[\"\u201d]")

# Title-cased phrases — 2+ Capitalized words that look like a document name
# or proper noun. Excludes common sentence-leading words via the curated
# stop list below. This is the workhorse for legal queries — most case-file
# documents are referenced like "Global Stipulation re Escrow and Appeal".
_TITLECASE_PHRASE_RE = re.compile(
    r"\b((?:[A-Z][a-zA-Z0-9]{1,}(?:\s+(?:re|of|and|the|to|on|in|for|vs?\.?|et\s+al\.?))?\s+){1,8}[A-Z][a-zA-Z0-9]{1,})\b"
)
# Common false positives — single-capital starts that lead a sentence.
_TITLECASE_STOPLEAD = {
    "I", "We", "You", "They", "He", "She", "Do", "Does", "Did",
    "Is", "Are", "Was", "Were", "Will", "Would", "Should", "Could",
    "Can", "Let", "Make", "Get", "Give", "Find", "Show", "Tell",
    "What", "Where", "When", "Who", "Why", "How", "Which", "Whose",
    "The", "A", "An",
}

# Email addresses — "what did wheuer@... say?"
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# Case numbers (US bankruptcy / civil): 24-73893-spg, 8-25-72526-spg, etc.
_CASE_RE = re.compile(r"\b(\d{1,2}[-:]\d{4,6}(?:[-_][A-Za-z]{2,4})?)\b")

# Docket numbers: "Dkt. No. 149", "ECF 128"
_DOCKET_RE = re.compile(
    r"\b(?:Dkt\.?\s*No\.?|ECF(?:\s*No\.?)?|Docket\s*(?:No\.?)?)\s*#?\s*(\d{1,6})\b",
    re.IGNORECASE,
)

# Comprehensive-intent detector — used by QuerySignals.is_comprehensive().
# Triggers a bigger evidence pack (adaptive_k_comprehensive) and full-doc mode.
_COMPREHENSIVE_RE = re.compile(
    r"\b("
    r"all\s+(?:references?|mentions?|instances?|emails?|documents?|stipulations?|"
    r"orders?|letters?|times?|cases?|filings?|attachments?|messages?|chats?|"
    r"the\s+\w+)|"
    r"every\s+(?:reference|mention|instance|email|document|stipulation|"
    r"order|letter|time|case|filing|attachment|message|chat|single)|"
    r"each\s+(?:reference|mention|instance|email|document|stipulation|"
    r"order|letter|case|filing|attachment|message|chat)|"
    r"comprehensive|exhaustive|"
    r"complete\s+(?:list|history|set|picture|timeline|record)|"
    r"entire\s+(?:list|history|chain|thread|record|file|case)|"
    r"list\s+everything|"
    r"give\s+me\s+everything"
    r")\b",
    re.IGNORECASE,
)

# Creation-verb detector — "drafted / signed / issued / filed / executed".
# When the query asks about CREATION rather than DISCUSSION, we prefer the
# PRIMARY (earliest) occurrence date over the latest_date for both filtering
# and recency scoring. In Option B both dates are available; this just
# flips which one wins for this single query.
_CREATION_VERB_RE = re.compile(
    r"\b("
    r"draft\w*|sign\w*|execut\w*|issu\w*|fil\w*|enact\w*|"
    r"creat\w*|author\w*|prepar\w*|writ\w*|compos\w*|"
    r"orig\w*|enter\w+into|"
    r"when\s+was|when\s+did|when\s+were"
    r")\b",
    re.IGNORECASE,
)


# Intent classifiers — keyword detectors per intent class.
_INTENT_PATTERNS = {
    "compare": re.compile(
        r"\b(compare|contradiction|differs?|changed|amended|revised|"
        r"vs\.?|versus|inconsistent|mismatch|discrepancy|inconsistency)\b",
        re.IGNORECASE,
    ),
    "timeline": re.compile(
        r"\b(timeline|chronolog(?:y|ical)|sequence of events|over time|"
        r"summari[sz]e (?:everything|all)|how (?:did|has) \w+ (?:evolve|develop)|"
        r"what happened (?:between|from|during))\b",
        re.IGNORECASE,
    ),
    "lookup": re.compile(
        r"\b(what is|where (?:does|is)|who (?:is|sent)|when (?:did|was|is)|"
        r"how much|how many|find|locate|show me|do you have)\b",
        re.IGNORECASE,
    ),
    "summary": re.compile(
        r"\b(summari[sz]e|overview|brief|recap|TL;?DR|key points|"
        r"main (?:points|takeaways|findings))\b",
        re.IGNORECASE,
    ),
    "opinion": re.compile(
        r"\b(should|advise|recommend|strateg(?:y|ies)|implication|interpret|"
        r"do you think|what's your take|opinion|legal (?:risk|exposure))\b",
        re.IGNORECASE,
    ),
}


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class QuerySignals:
    """Structured extraction from a user query. Used by retrieval + scoring."""

    text: str                                       # original query (untouched)
    money_terms: List[str] = field(default_factory=list)
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    explicit_dates: List[datetime] = field(default_factory=list)
    filenames: List[str] = field(default_factory=list)
    quoted_strings: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    case_numbers: List[str] = field(default_factory=list)
    docket_numbers: List[str] = field(default_factory=list)
    intents: List[str] = field(default_factory=list)
    # `keyword_boost_terms` are surface forms we want the reranker / scorer
    # to give bonus weight to (exact substring match → score boost).
    keyword_boost_terms: List[str] = field(default_factory=list)

    @property
    def has_temporal_signal(self) -> bool:
        return bool(
            self.date_from or self.date_to or self.explicit_dates
        )

    @property
    def prefer_creation_date(self) -> bool:
        """
        True when the query asks WHEN a document was CREATED / FILED /
        SIGNED, rather than when it was discussed. Under Option B this
        flips the date filter to target the PRIMARY (earliest) occurrence
        instead of the any-occurrence semantic.
        """
        return bool(_CREATION_VERB_RE.search(self.text or ""))

    @property
    def has_explicit_target(self) -> bool:
        """True if the query refers to a specific document, person, or number."""
        return bool(
            self.filenames or self.quoted_strings or self.emails
            or self.case_numbers or self.docket_numbers or self.money_terms
        )

    def primary_intent(self) -> str:
        """Return the strongest intent signal, or 'general' if none detected."""
        # Priority order matches typical legal-investigation needs.
        for intent in ("compare", "timeline", "lookup", "summary", "opinion"):
            if intent in self.intents:
                return intent
        return "general"

    def is_complex(self) -> bool:
        """A query is 'complex' if it has multiple signals or compare/timeline intent."""
        if "compare" in self.intents or "timeline" in self.intents:
            return True
        signal_count = (
            len(self.money_terms) + len(self.filenames) + len(self.quoted_strings)
            + len(self.emails) + len(self.case_numbers) + len(self.docket_numbers)
            + (1 if self.has_temporal_signal else 0)
        )
        return signal_count >= 3

    def is_comprehensive(self) -> bool:
        """
        A query is 'comprehensive' if it asks for *exhaustive* coverage.

        Two triggers:
          1. Surface keywords that explicitly demand completeness
             ("all", "every", "each", "comprehensive", "exhaustive",
             "complete list", "entire", "list everything", "every reference").
          2. Four or more independent entity signals — these queries are
             stitching together many sources and need a bigger evidence pack.
        """
        if _COMPREHENSIVE_RE.search(self.text or ""):
            return True
        signal_count = (
            len(self.money_terms) + len(self.filenames) + len(self.quoted_strings)
            + len(self.emails) + len(self.case_numbers) + len(self.docket_numbers)
        )
        return signal_count >= 4


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_signals(query: str) -> QuerySignals:
    """
    Extract every structured signal we can find in the query.

    Pure function: deterministic, no I/O, < 1ms for typical queries.
    Always returns a QuerySignals (never raises).
    """
    if not query or not query.strip():
        return QuerySignals(text=query or "")

    sig = QuerySignals(text=query)

    # ---- Money ----------------------------------------------------------
    for m in _MONEY_RE.finditer(query):
        surface = m.group(0).strip()
        if surface:
            sig.money_terms.append(surface)

    # If the query has a money-context hint word AND contains bare
    # comma-separated numbers, treat those as additional money candidates.
    # We capture both surface forms (`"450,000"`) and dollar-prefixed
    # variants (`"$450,000"`) so BM25 / exact-match boost catch both.
    if _MONEY_CONTEXT_RE.search(query):
        existing_money = {m.lower() for m in sig.money_terms}
        for m in _BARE_NUMBER_RE.finditer(query):
            surface = m.group(1).strip()
            if not surface or surface.lower() in existing_money:
                continue
            # Skip small numbers like "1,000" only if they look like noise;
            # require comma OR 5+ digits — already enforced by regex.
            sig.money_terms.append(surface)
            # Also add the $-prefixed form as a separate boost term so
            # exact-match scoring catches the canonical formatting.
            sig.money_terms.append(f"${surface}")
            existing_money.add(surface.lower())

    # ---- Date range (explicit "from X to Y") ----------------------------
    range_match = _YEAR_RANGE_RE.search(query)
    if range_match:
        ys, ye = sorted([int(range_match.group(1)), int(range_match.group(2))])
        sig.date_from = datetime(ys, 1, 1)
        sig.date_to = datetime(ye, 12, 31, 23, 59, 59)

    # ---- Specific dates --------------------------------------------------
    sig.explicit_dates = _extract_dates(query)

    # If no range was given but we have specific dates, build a tight window.
    if not range_match and sig.explicit_dates:
        ds = sorted(sig.explicit_dates)
        sig.date_from = ds[0].replace(hour=0, minute=0, second=0)
        sig.date_to = ds[-1].replace(hour=23, minute=59, second=59)

    # If no explicit date but a single year mentioned → year window.
    if not sig.has_temporal_signal:
        years = list({int(y) for y in _YEAR_RE.findall(query)})
        if len(years) == 1:
            sig.date_from = datetime(years[0], 1, 1)
            sig.date_to = datetime(years[0], 12, 31, 23, 59, 59)
        elif len(years) >= 2:
            ys, ye = min(years), max(years)
            sig.date_from = datetime(ys, 1, 1)
            sig.date_to = datetime(ye, 12, 31, 23, 59, 59)

    # ---- Filenames ------------------------------------------------------
    for m in _FILENAME_RE.finditer(query):
        fname = m.group(1).strip()
        if fname and len(fname) <= 200:
            sig.filenames.append(fname)

    # ---- Quoted strings -------------------------------------------------
    for m in _QUOTED_RE.finditer(query):
        s = m.group(1).strip()
        if s and 3 <= len(s) <= 80:
            sig.quoted_strings.append(s)

    # ---- Title-cased phrases (likely document names / proper nouns) -----
    # These are the strongest hint we get from un-quoted text in legal
    # queries. We add them as quoted_strings since downstream callers treat
    # quoted_strings as filename / proper-noun candidates.
    seen_phrases = {q.lower() for q in sig.quoted_strings}
    for m in _TITLECASE_PHRASE_RE.finditer(query):
        phrase = m.group(1).strip()
        if not phrase or len(phrase) < 6 or len(phrase) > 100:
            continue
        # Skip if the phrase is just a leading sentence pronoun + word.
        first_word = phrase.split(None, 1)[0]
        if first_word in _TITLECASE_STOPLEAD:
            # Re-attempt by trimming the leading stop-word.
            rest = phrase.split(None, 1)
            if len(rest) < 2:
                continue
            phrase = rest[1].strip()
            if not phrase or len(phrase) < 6:
                continue
        key = phrase.lower()
        if key in seen_phrases:
            continue
        seen_phrases.add(key)
        sig.quoted_strings.append(phrase)

    # ---- Emails ---------------------------------------------------------
    sig.emails = list({m.group(1).lower() for m in _EMAIL_RE.finditer(query)})

    # ---- Case / docket numbers ------------------------------------------
    sig.case_numbers = list({m.group(1) for m in _CASE_RE.finditer(query)})
    sig.docket_numbers = list({m.group(1) for m in _DOCKET_RE.finditer(query)})

    # ---- Intents --------------------------------------------------------
    for intent_name, pat in _INTENT_PATTERNS.items():
        if pat.search(query):
            sig.intents.append(intent_name)

    # ---- Keyword boost terms --------------------------------------------
    # These are surface tokens we want the reranker/scorer to weight up.
    boost: List[str] = []
    boost.extend(sig.money_terms)
    boost.extend(sig.filenames)
    boost.extend(sig.quoted_strings)
    boost.extend(sig.case_numbers)
    boost.extend(f"Dkt. {n}" for n in sig.docket_numbers)
    # Emails — also include the local part (before @) as a name hint.
    for e in sig.emails:
        boost.append(e)
        local = e.split("@", 1)[0]
        if len(local) >= 3:
            boost.append(local)
    # Dedupe preserving order.
    seen = set()
    sig.keyword_boost_terms = [
        t for t in boost
        if not (t.lower() in seen or seen.add(t.lower()))  # type: ignore[func-returns-value]
    ]

    return sig


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_dates(query: str) -> List[datetime]:
    """Best-effort parse of explicit calendar dates."""
    found: List[datetime] = []

    # ISO / slash dates: 2026-01-20 / 2026/01/20
    for m in _DATE_PATTERNS[0].finditer(query):
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            found.append(datetime(y, mo, d))
        except (ValueError, TypeError):
            continue

    # US m-d-y: 01-20-2026
    for m in _DATE_PATTERNS[1].finditer(query):
        try:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                found.append(datetime(y, mo, d))
        except (ValueError, TypeError):
            continue

    # Month-name dates: "January 20, 2026" / "20 Jan 2026"
    for m in _DATE_PATTERNS[2].finditer(query):
        try:
            day1, mon, day2, year = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
            mo_num = _MONTH_TO_NUM.get(mon[:3])
            if not mo_num:
                continue
            d_str = day1 or day2 or "1"
            d = int(d_str) if d_str.isdigit() else 1
            if 1 <= d <= 31:
                found.append(datetime(int(year), mo_num, d))
        except (ValueError, TypeError):
            continue

    # US m-d-yy: 01-20-26 → 2026 (yy < 70) or 1926 (yy >= 70)
    for m in _DATE_PATTERNS[3].finditer(query):
        try:
            mo, d, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                continue
            year = 2000 + yy if yy < 70 else 1900 + yy
            found.append(datetime(year, mo, d))
        except (ValueError, TypeError):
            continue

    # Month-name + day + yy: "Jan 20-26" / "Jan 20 26"
    for m in _DATE_PATTERNS[4].finditer(query):
        try:
            mon = m.group(1).lower()
            d = int(m.group(2))
            yy = int(m.group(3))
            mo_num = _MONTH_TO_NUM.get(mon[:3])
            if not mo_num or not (1 <= d <= 31):
                continue
            year = 2000 + yy if yy < 70 else 1900 + yy
            found.append(datetime(year, mo_num, d))
        except (ValueError, TypeError):
            continue

    # Dedupe by ISO string.
    seen: set = set()
    out: List[datetime] = []
    for dt in found:
        key = dt.isoformat()
        if key not in seen:
            seen.add(key)
            out.append(dt)
    return out
