"""
Aggressive cleaner for email plain-text bodies.

Strategy (linear time, no backtracking):
  1. Encoding fixes + remove _x000D_ artifacts.
  2. Strip firm-specific signature blocks (Westerman Ball / Mandelbaum /
     Goldberg Weprin / Bookkeepingservices / MangoTree …).
  3. Truncate at the first reply / forwarded-message marker so the entire
     downstream history is dropped in one cut.
  4. Strip standard legal-disclaimer blocks (CYBER FRAUD WARNING,
     IRS Circular 230, External Sender, generic confidentiality, …).
  5. Strip miscellaneous noise lines (decorative dashes, "Sent from my
     iPhone", "Please consider the environment", cid: image refs, …).
  6. Strip orphan contact lines (Phone:/Cell:/Fax:/(xxx) xxx-xxxx, …).
  7. Strip quoted '>' lines and any surviving "From:/Sent:/To:/…" headers.
  8. Excise signature clusters at the TOP and BOTTOM of the remaining body
     (catches new firms we don't yet have an explicit pattern for).
  9. Whitespace normalization.

Patterns derived from the actual fraud_emails corpus.  Re-running is safe.
"""
from __future__ import annotations

import re
from typing import List

import ftfy

# ---------------------------------------------------------------------------
# Helper for "match a starting phrase, consume to next blank line / EOF"
# ---------------------------------------------------------------------------
def _block(start_pattern: str) -> re.Pattern:
    """Compile a regex that matches `start_pattern` and everything that
    follows until the next blank line OR end of text.  Used for legal
    disclaimer paragraphs that always end at a blank line.

    Accepts optional markdown emphasis markers (`*`, `_`), table pipes
    (`|`), zero-width spaces (\u200b/\u200c) and quote markers (`>`) before
    the pattern, because email→markdown converters wrap disclaimers in
    italic/bold and stylised HTML sigs become markdown tables."""
    return re.compile(
        rf"(?is)(?:^|\n)[ \t>*_|\u200b\u200c\u200d\ufeff]*{start_pattern}.*?(?=\n[ \t]*\n|\Z)",
    )


# ---------------------------------------------------------------------------
# Reply-chain truncation
# ---------------------------------------------------------------------------

# RFC 3676 signature delimiter: a line containing only "-- "
_SIG_DELIMITER = re.compile(r"^\-\-\s*$", re.MULTILINE)

# "On <...>, <Name> wrote:" reply markers (multi-language)
_REPLY_HEADER_PATTERNS = [
    re.compile(r"^On\s.{1,200}\swrote:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Le\s.{1,200}\sa\s[ée]crit\s*:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^El\s.{1,200}\sescribi[oó]\s*:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Am\s.{1,200}\sschrieb\s.{1,200}:\s*$", re.IGNORECASE | re.MULTILINE),
]

# Forwarded-message dividers
_DIVIDER_PATTERNS = [
    re.compile(r"^[-_]{3,}\s*Original Message\s*[-_]{3,}\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[-_]{3,}\s*Forwarded Message\s*[-_]{3,}\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[-_]{3,}\s*Begin forwarded message\s*[-_]{3,}\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^_{15,}\s*$", re.MULTILINE),
    re.compile(r"^-{15,}\s*$", re.MULTILINE),
    re.compile(r"^={15,}\s*$", re.MULTILINE),
]

# First "From:" line — handles plain text AND **markdown bold**
_FROM_LINE = re.compile(
    r"^[ \t>]*\*{0,2}(?:From|De|Von|Da|Van)\*{0,2}\s*:",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Firm-specific signature blocks
# ---------------------------------------------------------------------------

# All firm patterns are designed to be markdown-aware: they tolerate `*`,
# `_`, `>` and whitespace at line starts because email→markdown converters
# wrap names in **bold** and disclaimers in _italic_.

# Boris Peyzner / Mandelbaum (Salsburg or Barrett — they renamed the firm).
# Anchored on the name → matches through firm marker, with several end-anchors
# to catch the various sig variants (full address w/ ZIP, "92nd year"
# rebranding blurb, or just the firm line followed by a blank line).
_MANDELBAUM_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_|]*Boris\s+Peyzner\b"
    r".{0,1500}?"
    r"(?:"
    r"Mandelbaum\s+Salsburg\s+(?:P\.?\s*C\.?|PC).{0,500}?\b\d{5}(?:-\d{4})?\b"
    r"|Mandelbaum\s+Barrett\s+(?:P\.?\s*C\.?|PC).{0,500}?Mandelbaum\s+Barrett[^\n]*"
    r"|Mandelbaum\s+Barrett\s+(?:P\.?\s*C\.?|PC).{0,300}?\b\d{5}(?:-\d{4})?\b"
    r"|Mandelbaum\s+(?:Salsburg|Barrett)[^\n]*"
    r")"
)
# Boris bare name + counsel role + phone (no firm address — happens when
# email ends mid-sig, e.g. forwarded to Rakesh).
_BORIS_NAME_BLOCK = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_|]*\*{0,2}\s*Boris\s+Peyzner\s*\*{0,2}\s*\|?\s*\*{0,2}Counsel\*{0,2}"
    r"(?:.{0,200}?\[\s*\(?\d{3}\)?[^\]]+\]\([^)]*tel:[^)]*\))?"
    r"(?:.{0,200}?bpeyzner@[^\s]+)?"
    r"(?:.{0,200}?Mandelbaum\s+(?:Salsburg|Barrett)[^\n]*)?"
)

# Phil Campisi / Westerman Ball.  Strong end-anchors: ZIP "11530" or website.
_PHIL_CAMPISI_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)[^\n]{0,160}?"
    r"Westerman\s+Ball\s+Ederer\s+Miller\s+Zucker\s*&\s*Sharfstein,?\s*LLP"
    r".{0,800}?"
    r"(?:www\.westermanllp\.com[^\n]*|\b115\d{2}\b)"
)

# Rakesh Bhargava / MangoTree.  Use the name as anchor — only if the name is
# present.  This avoids stripping body prose that mentions "MangoTree".
_RAKESH_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_]*Rakesh\s+Bhargava\b"
    r".{0,800}?"
    r"MangoTree\s+Real\s+Estate\s+Holdings[^\n]*"
    r"(?:.{0,400}?Connect\s+with\s+me\s+on\s+LinkedIn[^\n]*)?"
    r"(?:.{0,400}?<https?://ci\d*\.googleusercontent\.com/mail-sig/[^>]*>)?"
)

# Goldberg Weprin / Ted Donovan — name + firm + phone
_TED_DONOVAN_SIG_A = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_]*J\.\s*Ted\s+Donovan(?:,?\s*Esq\.?)?\b"
    r".{0,800}?"
    r"Goldberg\s+Weprin\s+Finkel\s+Goldstein\s+LLP"
    r".{0,400}?"
    r"\d{3}[\s\-\u2013\u2010]\d{4}"
)
# Goldberg Weprin — variant B (firm header without name)
_TED_DONOVAN_SIG_B = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_]*Goldberg\s+Weprin\s+Finkel\s+Goldstein\s+LLP"
    r".{0,400}?"
    r"\d{3}[\s\-\u2013\u2010]\d{4}"
)

# Jaspreet Pahawa / Bookkeepingservices.in / Bridgewater India
_PAHAWA_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_]*Jaspreet\s+Pahawa\b"
    r".{0,800}?"
    r"(?:bookkeepingservices\.in|Bridgewater\s+India)[^\n]*"
)

# ---- Bare firm-address patterns (no preceding name) ---------------------
# These fire only when the *full* address (street + city + ZIP) is present,
# so they cannot accidentally strip body prose that just mentions the firm.

_MANDELBAUM_ADDR = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_|]*Mandelbaum\s+(?:Salsburg|Barrett)\s+(?:P\.?\s*C\.?|PC)\s*"
    r"\n[ \t>*_|]*(?:3\s+Becker\s+Farm\s+Road|We\s+are\s+celebrating)"
    r"(?:\n[ \t>*_|]*[^\n]+){0,4}"
)

_WESTERMAN_ADDR = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_|]*Westerman\s+Ball\s+Ederer\s+Miller\s+Zucker\s*&\s*Sharfstein,?\s*LLP\s*"
    r"\n[ \t>*_|]*\d+\s+[A-Z][^\n]{3,80}"
    r"\n[ \t>*_|]*[A-Z][^\n]{2,40}\s+(?:NY|NJ|CT)\s+\d{5}"
)

# Phil Campisi's stylised HTML signature converted to markdown is a *table*
# block, identifiable by the "Phil Campisi" name appearing inside table-pipe
# lines, then rows of "Main.: ... / Ext.: ... / Fax: ... / E-mail: ..." and a
# trailing westermanllp.com link.  Match from the first table row that
# mentions Phil Campisi through the website footer.
_PHIL_CAMPISI_TABLE_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)\|[^\n]{0,200}Phil(?:ip)?\s+Campisi[^\n]{0,200}"
    r"(?:\n[^\n]{0,500}){0,30}?"
    r"westermanllp\.com[^\n]*"
)
# Generic Westerman Ball markdown-table signature: any attorney name
# (Phil Campisi, William Heuer, Greg Zucker, …) immediately followed by the
# firm header + phone / fax / website.  We don't need the website at the
# end — a Tel.: / Fax: / Ext.: line is enough of an end-anchor.
_WESTERMAN_TABLE_NAMED_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)\|[^\n]{0,300}\b[A-Z][A-Za-z]+\s+(?:[A-Z]\.?\s+)?[A-Z][A-Za-z]+\b[^\n]{0,200}"
    r"(?:\n[^\n]{0,500}){0,5}?"
    r"Westerman\s+Ball\s+Ederer[^\n]*"
    r"(?:\n[^\n]{0,500}){0,20}?"
    r"(?:westermanllp\.com|516[\s\-\u2013\u2010\u2011]?6\d{2}[\s\-\u2013\u2010\u2011]?\d{4}|Fax\.?\s*:|Tel\.?\s*:|Ext\.?\s*:)[^\n]*"
)
# Same signature without name (forwarded / partial)
_WESTERMAN_TABLE_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)\|[^\n]{0,200}(?:1201\s+RXR\s+Plaza|Uniondale,?\s+NY|westermanllp\.com)[^\n]{0,200}"
    r"(?:\n[^\n]{0,500}){0,15}?"
    r"westermanllp\.com[^\n]*"
)
# Westerman Ball firm-header + immediately following phone/fax (no leading name).
_WESTERMAN_FIRM_BLOCK = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_|]*Westerman\s+Ball\s+Ederer[^\n]*"
    r"(?:\n[^\n]{0,500}){0,15}?"
    r"(?:westermanllp\.com|516[\s\-\u2013\u2010\u2011]?6\d{2}[\s\-\u2013\u2010\u2011]?\d{4}|Fax\.?\s*:|Tel\.?\s*:|Ext\.?\s*:)[^\n]*"
)

# Ted Donovan's markdown-table signature (different layout from variant A/B).
# "|  |  |  |  J. Ted Donovan, Esq. ... Goldberg Weprin Finkel ... New York 10017 ... O: (212) ..."
_TED_DONOVAN_TABLE_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)\|[^\n]{0,200}J\.\s*Ted\s+Donovan[^\n]{0,200}"
    r"(?:\n[^\n]{0,500}){0,20}?"
    r"(?:\d{3}[\s\-\u2013\u2010\u2011]\d{4}|Goldberg\s+Weprin)[^\n]*"
)
# Same firm, no name (forwarded)
_GOLDBERG_TABLE_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_|]*Goldberg\s+Weprin\s+Finkel\s+Goldstein\s+LLP[^\n]*"
    r"(?:\n[^\n]{0,500}){0,15}?"
    r"\d{3}[\s\-\u2013\u2010\u2011]\d{4}[^\n]*"
)

# Scott J. Kreppein / Devitt Spellman Barrett LLP (Hauppauge NY).
_KREPPEIN_SIG = re.compile(
    r"(?is)"
    r"(?:^|\n)[ \t>*_|]*\*{0,4}Scott\s+J\.\s+Kreppein\*{0,4}"
    r"(?:.{0,1500}?)"
    r"(?:devittspellmanlaw\.com|Devitt\s+Spellman\s+Barrett|Hauppauge,?\s*NY|11788)[^\n]*"
)

# Boris Peyzner standalone signature line (markdown-bold name + phone link)
_BORIS_NAME_LINE = re.compile(
    r"(?im)^[ \t>*_]*\*{0,2}Boris\s+Peyzner\*{0,2}\s*\|?\s*\*{0,2}Counsel\*{0,2}\s*$"
)
# Phone-link line: "[(973) 327-6605](<tel:...>)"
_PHONE_MARKDOWN_LINK = re.compile(
    r"(?im)^[ \t>*_]*\[\s*\(?\d{3}\)?[\s\-\u2013]?\d{3}[\s\-\u2013]?\d{4}[^\]]*\]\(<?\s*(?:tel|mailto):[^)]*\)\s*$"
)

FIRM_SIGNATURE_BLOCKS: List[re.Pattern] = [
    _WESTERMAN_TABLE_NAMED_SIG,
    _PHIL_CAMPISI_TABLE_SIG,
    _WESTERMAN_TABLE_SIG,
    _WESTERMAN_FIRM_BLOCK,
    _PHIL_CAMPISI_SIG,
    _WESTERMAN_ADDR,
    _RAKESH_SIG,
    _TED_DONOVAN_TABLE_SIG,
    _GOLDBERG_TABLE_SIG,
    _TED_DONOVAN_SIG_A,
    _TED_DONOVAN_SIG_B,
    _MANDELBAUM_SIG,
    _BORIS_NAME_BLOCK,
    _MANDELBAUM_ADDR,
    _PAHAWA_SIG,
    _KREPPEIN_SIG,
]

# Fast-path: a regex only runs if its "marker" substring is found in the text
# (case-insensitive).  Saves ~20x on emails that don't contain a given firm.
FIRM_FAST_MARKERS: list[tuple[re.Pattern, str]] = [
    (_WESTERMAN_TABLE_NAMED_SIG, "westerman ball ederer"),
    (_PHIL_CAMPISI_TABLE_SIG,    "phil campisi"),
    (_WESTERMAN_TABLE_SIG,       "westermanllp"),
    (_WESTERMAN_FIRM_BLOCK,      "westerman ball ederer"),
    (_PHIL_CAMPISI_SIG,          "westermanllp"),
    (_WESTERMAN_ADDR,            "westerman ball ederer"),
    (_RAKESH_SIG,                "rakesh bhargava"),
    (_TED_DONOVAN_TABLE_SIG,     "ted donovan"),
    (_GOLDBERG_TABLE_SIG,        "goldberg weprin"),
    (_TED_DONOVAN_SIG_A,         "ted donovan"),
    (_TED_DONOVAN_SIG_B,         "goldberg weprin"),
    (_MANDELBAUM_SIG,            "boris peyzner"),
    (_BORIS_NAME_BLOCK,          "boris peyzner"),
    (_MANDELBAUM_ADDR,           "mandelbaum"),
    (_PAHAWA_SIG,                "pahawa"),
    (_KREPPEIN_SIG,              "kreppein"),
]

DISCLAIMER_FAST_MARKERS: list[tuple[re.Pattern, tuple[str, ...]]] = []  # filled below


# ---------------------------------------------------------------------------
# Disclaimer blocks (paragraph-level)
# ---------------------------------------------------------------------------

# Markdown-aware whitespace tokens.
_W = r"[\s*_?]+"      # 1+ whitespace / markdown / encoded-`?` chars
_W0 = r"[\s*_?]*"     # 0+ (use between optional prefix chars and a keyword)

DISCLAIMER_BLOCKS: List[re.Pattern] = [
    _block(rf"\?{{0,5}}{_W0}CYBER{_W}FRAUD{_W}WARNING\s*:"),
    _block(rf"PLEASE{_W}TAKE{_W}NOTICE\s*:[\s*_]*The{_W}information{_W}transmitted"),
    _block(rf"IRS{_W}Circular{_W}230"),
    _block(rf"Disclaimer\s*:[\s*_]*Unless{_W}the{_W}above{_W}communication"),
    _block(rf"Disclaimer\s*\n[\s*_>]*Unless{_W}the{_W}above{_W}communication"),
    _block(rf"If{_W}you{_W}receive{_W}an{_W}e-?mail{_W}which{_W}seems{_W}to{_W}be{_W}from{_W}me,?\s*directing{_W}you{_W}to{_W}wire"),
    _block(rf"External{_W}Sender\s*:[\s*_]*Free{_W}email{_W}provider{_W}detected"),
    _block(rf"This{_W}is{_W}an{_W}external{_W}e-?mail{_W}sent{_W}from{_W}a{_W}free{_W}email{_W}provider"),
    _block(rf"This{_W}message{_W}is{_W}intended{_W}for{_W}the{_W}sole{_W}use{_W}of{_W}the{_W}addressee"),
    _block(rf"This{_W}email{_W}and{_W}any{_W}attachments{_W}are{_W}intended{_W}solely{_W}for{_W}the{_W}recipient"),
    _block(rf"This{_W}e-?mail(?:{_W}message)?{_W}and{_W}any{_W}attachments(?:{_W}may)?{_W}contain{_W}(?:confidential|information)"),
    _block(rf"CONFIDENTIALITY{_W}NOTICE\s*:"),
    _block(rf"NOTICE\s*:[\s*_]*This{_W}(?:e-?mail|communication)"),
    # Boris Peyzner-specific "intended solely for the recipient ... privileged and confidential ..."
    _block(rf"This{_W}email{_W}and{_W}any{_W}attachments[^\n]{{0,40}}intended"),
]

# Fast-path markers per disclaimer pattern (any of these substrings must be
# present — case-insensitive — for the regex to even run).
DISCLAIMER_FAST_MARKERS = [
    (DISCLAIMER_BLOCKS[0],  ("cyber fraud",)),
    (DISCLAIMER_BLOCKS[1],  ("please take notice", "information transmitted")),
    (DISCLAIMER_BLOCKS[2],  ("irs circular",)),
    (DISCLAIMER_BLOCKS[3],  ("disclaimer", "above communication")),
    (DISCLAIMER_BLOCKS[4],  ("disclaimer", "above communication")),
    (DISCLAIMER_BLOCKS[5],  ("seems to be from", "directing you to wire")),
    (DISCLAIMER_BLOCKS[6],  ("external sender", "free email provider")),
    (DISCLAIMER_BLOCKS[7],  ("external e-mail", "external email")),
    (DISCLAIMER_BLOCKS[8],  ("sole use of the addressee",)),
    (DISCLAIMER_BLOCKS[9],  ("intended solely for the recipient",)),
    (DISCLAIMER_BLOCKS[10], ("confidential", "attachments")),
    (DISCLAIMER_BLOCKS[11], ("confidentiality notice",)),
    (DISCLAIMER_BLOCKS[12], ("notice:",)),
    (DISCLAIMER_BLOCKS[13], ("any attachments", "intended")),
]


# ---------------------------------------------------------------------------
# Single-line noise & orphan contact lines
# ---------------------------------------------------------------------------

NOISE_LINES: List[re.Pattern] = [
    re.compile(r"(?im)^[ \t>]*Please\s+consider\s+the\s+environment\s+before\s+printing\s+this\s+e-?mail\.?\s*$"),
    # "External Sender" banner — accept any markdown wrapping (** _ etc.)
    re.compile(r"(?im)^[ \t>*_|]*\*?_?\s*External\s+Sender\s*_?\*?[ \t*_]*$"),
    # Decorative "????" lines (encoding artifact for a divider char)
    re.compile(r"(?m)^[ \t>]*\?{2,}\s*$"),
    # Long underscore / dash separators
    re.compile(r"(?m)^[ \t>]*_{6,}\s*$"),
    re.compile(r"(?m)^[ \t>]*-{6,}\s*$"),
    # Empty markdown table cells: "| | | |" or "|||"
    re.compile(r"(?m)^[ \t]*\|[\s|\u200b\u200c\u200d\ufeff]*$"),
    # Markdown table separators: "---|---" or just "---"
    re.compile(r"(?m)^[ \t]*-{2,}(?:\|-{2,})+\s*$"),
    re.compile(r"(?m)^[ \t]*-{3}\s*$"),
    # Standalone underscore/asterisk markers (orphan markdown emphasis)
    re.compile(r"(?m)^[ \t]*[_*]\s*$"),
    # Bare signature-image links / cid: refs
    re.compile(r"(?im)^[ \t>]*<https?://ci\d*\.googleusercontent\.com/mail-sig/[^>]*>\s*$"),
    re.compile(r"(?im)^[ \t>]*<https?://[^>]*\.(?:png|jpg|jpeg|gif|bmp)>\s*$"),
    # Orphan firm URL
    re.compile(r"(?im)^[ \t>]*https?://(?:www\.)?bookkeepingservices\.in[^\n]*$"),
    # "endsig" marker often glued to a name
    re.compile(r"(?i)endsig\b"),
    # "Sent from my iPhone/Android/Outlook/Gmail/..."
    re.compile(
        r"(?im)^[ \t>]*(?:Sent|Get|Verstuurd|Enviado)\s+(?:from|via|de|vanaf)\s+[^\n]{0,80}$"
    ),
    # "Please consider the environment ..." catch-all
    re.compile(r"(?im)^[ \t>]*[^\n]{0,80}\bconsider\s+the\s+environment\s+before\s+printing\b[^\n]*$"),
    # Markdown image refs "[image: ...]"
    re.compile(r"(?im)^[ \t>]*\[image:\s*[^\]]+\]\s*$"),
    # Copyright notices
    re.compile(r"(?im)^[ \t>]*©\s*\d{4}[^\n]*$"),
]

ORPHAN_CONTACT_LINES: List[re.Pattern] = [
    # Markdown-bold name lines followed by a counsel/title role separator
    re.compile(r"(?im)^[ \t>*_]*\*{1,2}Boris\s+Peyzner\*{1,2}\s*\|[^\n]*$"),
    re.compile(r"(?im)^[ \t>*_]*\*{1,2}Boris\s+Peyzner\b[^\n]*\|\s*Counsel\b[^\n]*$"),
    # Markdown phone link "[(973) 327-6605](<tel:\(973\) 327-6605>)" alone
    re.compile(r"(?im)^[ \t>*_]*\[\s*\(?\+?\d{1,3}\)?[\s\-\u2013\.]*\d{2,4}[\s\-\u2013\.]*\d{2,4}[^\]]*\]\([^)]*(?:tel|mailto)[^)]*\)[ \t]*$"),
    # Bare email-address line
    re.compile(r"(?im)^[ \t>*_]*[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\s*$"),
    # Address fragment lines (street number + Becker Farm / etc. — when address-block was decapitated)
    re.compile(r"(?im)^[ \t>*_]*\d+\s+Becker\s+Farm\s+Road\s*$"),
    re.compile(r"(?im)^[ \t>*_]*Roseland,?\s*NJ\s*\d{5}\s*$"),
    # "O:/D:/M:/T:/F:/P: (xxx) xxx-xxxx" or similar (single letter prefix)
    re.compile(r"(?im)^[ \t>]*[ODMTFP][:\.]\s*\(?\d{3}\)?[\s\?\-\u2013]\d{3}[\?\-\u2013]\d{4}[^\n]*$"),
    # "Phone:/Cell:/Fax:/Main:/Direct:/Mobile:/Tel:/Office:/Ext.:/E-mail: ..."
    re.compile(
        r"(?im)^[ \t>]*(?:Phone|Cell|Fax|Main|Direct|Mobile|Tel|Office|Ext|E-?mail|Email)\s*\.?\s*:[^\n]*$"
    ),
    # "<tel:...>" leftovers
    re.compile(r"(?im)^[ \t>]*<tel:[^>]*>\s*$"),
    # LinkedIn references
    re.compile(r"(?im)^[ \t>]*Connect\s+with\s+me\s+on\s+LinkedIn[^\n]*$"),
    re.compile(r"(?im)^[ \t>]*<https?://(?:www\.)?linkedin\.com/[^>]*>\s*$"),
    # Standalone Esq. lines
    re.compile(r"(?im)^[ \t>]*,?\s*Esq\.?\s*$"),
    # Bare website lines
    re.compile(r"(?im)^[ \t>]*www\.[^\s<]+\s*(?:<[^>]*>)?\s*$"),
    # Markdown-link-only lines (e.g. "[(973) 327-6605](<tel:...>)" alone)
    re.compile(r"(?im)^[ \t>]*\[[^\]]*\]\(<?\s*(?:tel|mailto):[^)]*\)\s*$"),
    # Plain phone number that's the whole line
    re.compile(r"(?m)^[ \t>]*[+(]?\d{1,3}[\s.\-]?\(?\d{2,3}\)?[\s.\-]?\d{3}[\s.\-]?\d{2,4}\s*$"),
    # Markdown maps URLs
    re.compile(r"(?im)^[ \t>]*\[[^\]]*\]\(<?https?://(?:maps|www\.google)\.[^)]*\)\s*$"),
]

# Quoted email-header lines (From:/Sent:/To:/Cc:/Bcc:/Subject:/Reply-To:)
EMAIL_HEADER_LINE = re.compile(
    r"(?im)^[ \t>]*\*{0,2}(?:From|Sent|To|Cc|Bcc|Subject|Reply\s*-?\s*To)\*{0,2}\s*:[^\n]*$"
)

# Quoted ">" lines
_QUOTED_LINE = re.compile(r"^>+\s.*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Cluster excision (TOP + BOTTOM safety net for new firms)
# ---------------------------------------------------------------------------

_SIG_LINE_PATTERNS = [
    re.compile(r"\]\(\s*<?\s*tel:", re.IGNORECASE),
    re.compile(r"\]\(\s*<?\s*mailto:", re.IGNORECASE),
    re.compile(
        r"^[ \t>*_]*(?:Tel|Phone|Direct|Mobile|Cell|Office|Fax|Main|Cellular|Ext\.?)[\.\:]?\s*[\*_]*\s*[+(\d]",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:Plaza|Avenue|Ave\.?|Street|St\.?|Road|Rd\.?|Drive|Dr\.?|Boulevard|Blvd\.?|Suite|Floor|Fl\.?|Ste\.?)\b.{0,80}\b\d{5}(?:-\d{4})?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\[\s*(?:www\.|https?://)[^\]]{4,80}\]\(", re.IGNORECASE),
    re.compile(r"^\s*\*\*[\w\s.,'\-&]{2,60}\*\*\s*$"),
    # Single bold line with a "|" dividing name and title
    re.compile(r"^\s*\*{0,2}[A-Z][\w\s.,'\-]{1,40}\*{0,2}\s*[\|,]\s*\*{0,2}[A-Z][\w\s\-/&.]{1,80}\*{0,2}\s*$"),
]


def _line_is_signature(line: str) -> bool:
    s = line.strip()
    if not s or len(s) <= 3:
        return False
    for pat in _SIG_LINE_PATTERNS:
        if pat.search(s):
            return True
    return False


def _line_is_neutral(line: str) -> bool:
    s = line.strip()
    return (not s) or len(s) <= 3


def _excise_signature_clusters(text: str) -> str:
    if not text:
        return ""
    lines = text.split("\n")
    n = len(lines)
    if n == 0:
        return text

    is_sig = [_line_is_signature(l) for l in lines]
    is_neutral = [_line_is_neutral(l) for l in lines]

    # Top cluster
    i = 0
    while i < n and (is_sig[i] or is_neutral[i]):
        i += 1
    top_cut = i if any(is_sig[:i]) else 0

    # Bottom cluster
    j = n - 1
    while j >= top_cut and (is_sig[j] or is_neutral[j]):
        j -= 1
    bottom_cut = j + 1
    if not any(is_sig[bottom_cut:n]):
        bottom_cut = n

    if top_cut == 0 and bottom_cut == n:
        return text
    return "\n".join(lines[top_cut:bottom_cut])


# ---------------------------------------------------------------------------
# Whitespace + encoding
# ---------------------------------------------------------------------------

_INVISIBLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_LEADING_BLANK = re.compile(r"^\s*\n+")
_MULTI_BLANK = re.compile(r"\n[ \t]*\n[ \t]*(?:\n[ \t]*)+")


def _fix_encoding(text: str) -> str:
    if not text:
        return ""
    text = ftfy.fix_text(text)
    text = text.replace("_x000D_", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _truncate_at_reply_chain(text: str) -> str:
    cuts: List[int] = []

    m = _FROM_LINE.search(text)
    if m:
        cuts.append(m.start())

    for pat in _REPLY_HEADER_PATTERNS:
        m = pat.search(text)
        if m:
            cuts.append(m.start())

    for pat in _DIVIDER_PATTERNS:
        m = pat.search(text)
        if m:
            cuts.append(m.start())

    m = _SIG_DELIMITER.search(text)
    if m and m.start() > 0:
        cuts.append(m.start())

    if cuts:
        return text[: min(cuts)]
    return text


def _normalize_whitespace(text: str) -> str:
    text = _INVISIBLE.sub("", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    text = _LEADING_BLANK.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def strip_signatures_and_quotes(text: str) -> str:
    """Apply the full strip pipeline (no encoding fix / no normalization)."""
    if not text:
        return ""

    # 1) Firm-specific signatures.  Fast pre-check: only run the regex if
    #    its marker substring appears in the body (case-insensitive).
    text_lc = text.lower()
    for _ in range(2):
        before = text
        any_match = False
        for pat, marker in FIRM_FAST_MARKERS:
            if marker in text_lc:
                new = pat.sub("\n", text)
                if new is not text:
                    text = new
                    text_lc = text.lower()
                    any_match = True
        if not any_match:
            break
        if text == before:
            break

    # 2) Cut at first reply / forwarded marker (drops the entire history)
    text = _truncate_at_reply_chain(text)
    text_lc = text.lower()

    # 3) Disclaimer paragraphs — same fast-path strategy
    for _ in range(2):
        before = text
        any_match = False
        for pat, markers in DISCLAIMER_FAST_MARKERS:
            if any(m in text_lc for m in markers):
                new = pat.sub("\n", text)
                if new is not text:
                    text = new
                    text_lc = text.lower()
                    any_match = True
        if not any_match:
            break
        if text == before:
            break

    # 4) Standalone noise lines
    for pat in NOISE_LINES:
        text = pat.sub("", text)

    # 5) Orphan contact lines
    for pat in ORPHAN_CONTACT_LINES:
        text = pat.sub("", text)

    # 6) Quoted '>' lines and surviving header lines
    text = _QUOTED_LINE.sub("", text)
    text = EMAIL_HEADER_LINE.sub("", text)

    # 7) Cluster excision at top/bottom (for any signatures we don't have an
    #    explicit firm-pattern for yet)
    text = _excise_signature_clusters(text)

    return text


def clean_email_body(text: str | None, *, strip_quotes: bool = True) -> str:
    """Top-level cleaner: encoding fix -> strip -> normalize."""
    if not text:
        return ""
    out = _fix_encoding(text)
    if strip_quotes:
        out = strip_signatures_and_quotes(out)
    out = _normalize_whitespace(out)
    return out
