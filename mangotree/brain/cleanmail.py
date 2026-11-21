"""Emails made fit to read — without breaking the conversation.

Measured on 2026-11-14 over nine property files: of the email text placed in
front of the model, 38% was each message's own words and 58% was older messages
quoted again underneath (77% on the busiest property). The same message was
read two, three, five times, and the one that mattered sat among its copies.

What this module does, with no model:

* ``split_quoted``   an email's own words, and the older messages quoted under it,
                     cut into one piece per quoted message.
* ``covered``        whether a quoted message is already held as an email of its
                     own in the same conversation. Only then is the quote left
                     out — the message is shown once, in its place in the chain.
                     A quote that is NOT held separately (a forward of someone
                     else's email, an earlier message RKB was not copied on) is
                     kept, word for word. The chain is never broken to save space.
* ``Signatures``     what each sender puts under every email — name, title,
                     phone, the firm's lines, a legal footer — learned from that
                     sender's own emails, and taken off the END of a message only.
* ``tidy``           link-tracking text, image placeholders and mail-server header
                     lines, which carry nothing a person would read.

Nothing here removes a word somebody wrote to somebody.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

FORWARD_MARK = "[Forwarded message]"

# Where a quoted message starts. "From:" counts only when a Sent/Date/To line follows it.
_QUOTE_START = re.compile(
    r"^(?:\[Forwarded message\]|Begin forwarded message:|-{2,}\s*(?:Original|Forwarded) [Mm]essage\s*-{0,}|_{8,}|"
    r"On .{8,120} wrote:|From:\s.+)\s*$", re.M)
_HEADER_LINE = re.compile(r"^\s*\*?(?:from|sent|date|to|cc|bcc|subject|reply-to)\s*:", re.I)

_PHONE = re.compile(r"^(?:(?:cell|mobile|phone|office|tel|direct|fax|india cell|m|o|p|t|c)\s*[:.]?\s*)?(?:<tel:[^>]*>)?[\d\s()+\-.x]{7,}(?:<tel:[^>]*>)?$", re.I)
_CONTACT = re.compile(r"^(?:[\w.+-]+@[\w-]+\.[\w.-]+(?:<mailto:[^>]*>)?|(?:https?://|www\.)\S+)$", re.I)
_COURTESY = re.compile(r"^(?:thanks?(?: you)?(?: (?:all|both|again|so much|very much|\w+))?|many thanks|regards|best regards|kind regards|warm regards|"
                       r"best|all the best|sincerely|cheers|respectfully)[\s,.!\-]*$", re.I)
_PLACEHOLDER = re.compile(r"^\[(?:cid:[^\]]*|image[^\]]*|.*AI-generated content may be incorrect\.?|Image removed by sender[^\]]*)\]$", re.I)
_SENT_FROM = re.compile(r"^(?:sent from my \w+(?: \w+)?|get outlook for \w+.*)$", re.I)
_MACHINE = re.compile(r"^(?:ARC-[\w-]+|X-[\w-]+|DKIM-Signature|Received(?:-SPF)?|Authentication-Results|Return-Path|MIME-Version|Content-Type|"
                      r"Content-Transfer-Encoding|Message-ID|References|In-Reply-To|darn|bh|fh|b|h|d|s)\s*[:=]|^[A-Za-z0-9+/=_\-]{40,}$|"
                      r"^\s+(?:[A-Za-z0-9+/=_\-:;.]{24,})\s*$")
_TRACKING = re.compile(r"<(?:https?://[^>\s]{50,}|mailto:[^>\s]+|tel:[^>\s]+)>")


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().strip("*_>|·•").lower())


def tidy(text: str) -> str:
    """Take out what no person reads: the tracking address behind a link, image
    placeholders, and runs of mail-server header lines (as in a bounce notice).
    A run of header lines is replaced by one line saying how many were left out."""
    text = _TRACKING.sub("", text or "")
    out: List[str] = []
    run: List[str] = []

    def flush() -> None:
        # Three or more such lines in a row are a header dump. One or two could be a
        # person's own line that happens to start "Received:" — those are kept.
        if len(run) >= 3:
            out.append(f"(… {len(run)} lines of mail-server headers left out)")
        else:
            out.extend(run)
        del run[:]

    for line in text.splitlines():
        s = line.strip()
        if _PLACEHOLDER.match(s):
            continue
        if s and _MACHINE.match(line):
            run.append(line.rstrip())
            continue
        if not s and run:
            continue                                  # a blank line inside a header dump does not end it
        flush()
        out.append(line.rstrip())
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def split_quoted(body: str, quoted_field: str = "") -> Tuple[str, List[str]]:
    """``(own words, [one text per quoted message, newest first])``.

    ``body`` is the cleaned body as stored; where the mail client's quote was
    stored apart (``quoted_field``) and is not already inside the body, it is
    used as the quoted part."""
    body = body or ""
    cut: Optional[int] = None
    for m in _QUOTE_START.finditer(body):
        if not _is_start(body, m):
            continue
        cut = m.start()
        break
    own, quoted = (body[:cut], body[cut:]) if cut is not None else (body, "")
    if not quoted.strip() and (quoted_field or "").strip():
        quoted = quoted_field
    return own.strip(), _pieces(quoted)


def _is_start(text: str, m) -> bool:
    """Does this line really begin a quoted message? A "From:" line does when a
    Sent/Date/To line follows it. A line of underscores does only when such a
    "From:" comes right after it — many people put that line above their own
    signature, and their signature is not somebody else's message."""
    line = m.group(0).strip()
    if line.startswith("From:"):
        return bool(re.search(r"^\s*(?:Sent|Date|To)\s*:", text[m.end():m.end() + 400], re.M))
    if re.fullmatch(r"_{8,}", line):
        nxt = [ln.strip() for ln in text[m.end():m.end() + 600].splitlines() if ln.strip()][:2]
        return bool(nxt) and nxt[0].startswith("From:") and bool(re.search(r"^\s*(?:Sent|Date|To)\s*:", text[m.end():m.end() + 600], re.M))
    return True


def _pieces(quoted: str) -> List[str]:
    """Cut a quoted chain into its messages, at each line where one starts."""
    quoted = (quoted or "").strip()
    if not quoted:
        return []
    starts: List[int] = []
    for m in _QUOTE_START.finditer(quoted):
        if not _is_start(quoted, m):
            continue
        # A separator line directly followed by a From: line is one start, not two.
        if starts and not quoted[starts[-1]:m.start()].strip(" \n_-") .replace(FORWARD_MARK, "").replace("Begin forwarded message:", "").strip():
            continue
        starts.append(m.start())
    if not starts or starts[0] != 0:
        starts = [0] + starts
    parts = [quoted[a:b].strip() for a, b in zip(starts, starts[1:] + [len(quoted)])]
    return [p for p in parts if p.strip(" \n_-").replace(FORWARD_MARK, "").strip()]


def _words(text: str) -> List[str]:
    # The ">" a mail client puts in front of each quoted line is taken off first, so a
    # quoted "> From: …" line is recognised as a header line and left out like any other.
    lines = [re.sub(r"^\s*(?:>\s?)+", "", ln) for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if not _HEADER_LINE.match(ln) and not _PLACEHOLDER.match(ln.strip()) and not re.match(r"^On .{8,120} wrote:\s*$", ln.strip())]
    return re.findall(r"[a-z0-9$%']+", _TRACKING.sub(" ", " ".join(lines)).lower())


def _shingles(words: Sequence[str], n: int = 5) -> set:
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def covered(piece: str, held: Iterable[str], *, threshold: float = 0.5) -> bool:
    """Is this quoted message already held as an email of its own?

    Judged on the words themselves, five at a time, so a different line wrap or
    a ">" in front of each line does not matter. Measured over 806 quoted
    messages on three files (2026-11-14), the share of a quote found in its
    chain falls into two groups with nothing between them: 612 at 0.5 or more
    (the message is there; what differs is a re-typed header or phone format)
    and 163 under 0.3 (it is not). The line is drawn in the gap. A quote too short to judge
    (under eight words — "Thanks", a forwarded header with nothing under it) is
    treated as covered only when it has no words at all."""
    words = _words(piece)
    if len(words) < 8:
        return len(words) == 0
    want = _shingles(words)
    if not want:
        return False
    have: set = set()
    for text in held:
        have |= _shingles(_words(text))
    return len(want & have) / len(want) >= threshold


def _content_lines(text: str) -> List[List[str]]:
    """The lines of a text that somebody wrote, as words — without quote marks,
    header lines, separators, phone and address lines."""
    out: List[List[str]] = []
    for ln in (text or "").splitlines():
        ln = re.sub(r"^\s*(?:>\s?)+", "", ln)
        s = ln.strip().strip("*_-–—|·• ")
        if (not s or _HEADER_LINE.match(ln) or re.match(r"^On .{8,120} wrote:\s*$", s) or _PLACEHOLDER.match(s) or _PHONE.match(s)
                or _CONTACT.match(s) or s == FORWARD_MARK or s.lower().startswith("begin forwarded message")):
            continue
        words = re.findall(r"[a-z0-9$%']+", _TRACKING.sub(" ", s).lower())
        if words:
            out.append(words)
    return out


_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def quoted_author(piece: str) -> str:
    """The address the quoted message says it is from, if it says."""
    for ln in (piece or "").splitlines()[:10]:
        s = re.sub(r"^\s*(?:>\s?)+", "", ln).strip().lstrip("*")
        if re.match(r"(?i)^from\*?:", s) or re.search(r"wrote:\s*$", s) or re.match(r"(?i)^on .{8,}", s):
            m = _ADDRESS.search(s)
            if m:
                return m.group(0).lower()
    return ""


def written_inside(piece: str, held: Iterable[str], *, signature_of=None) -> bool:
    """Did somebody write INSIDE this quoted message — answers typed between the
    lines of the email being answered ("see my replies below")?

    Then the quote is not a repeat: it carries words that exist nowhere else,
    and each answer only makes sense beside the line it answers. Such a quote is
    always kept whole.

    A line of the quote counts as the original's when its words, with the two
    words before and after it, run on in the same order in an earlier email of
    the chain (so a line the mail client re-wrapped is still recognised). The
    quoted writer's own signature, a closing courtesy, a web address and the
    "On … wrote:" line are not answers. A line that fits nowhere else is
    somebody's addition. When in doubt this says yes: keeping a repeat costs a
    little, losing an answer breaks the chain.

    ``signature_of(address)`` gives a sender's learned signature lines."""
    streams: List[str] = []
    threes: set = set()
    for h in held:
        words = [w for line in _content_lines(h) for w in line]
        streams.append(" " + " ".join(words) + " ")
        threes |= _shingles(words, 3)
    theirs: set = set()
    if signature_of:
        author = quoted_author(piece)
        if author:
            theirs = {" ".join(re.findall(r"[a-z0-9$%']+", _TRACKING.sub(" ", ln).lower())) for ln in signature_of(author)}
    lines = _content_lines(piece)
    for i, w in enumerate(lines):
        text = " ".join(w)
        probe = w if len(w) >= 5 else (lines[i - 1][-2:] if i else []) + w + (lines[i + 1][:2] if i + 1 < len(lines) else [])
        needle = " " + " ".join(probe) + " "
        if any(needle in s for s in streams):
            continue
        if len(w) >= 5:
            runs = _shingles(w, 3)
            if runs and len(runs & threes) / len(runs) >= 0.6:      # the original's line with a small difference in it
                continue
        if (not re.search(r"[a-z]{2,}", text) or text in theirs or _COURTESY.match(text) or w[0] in ("http", "https", "www") or text == "wrote"
                or re.match(r"^on (?:mon|tue|wed|thu|fri|sat|sun|\w+ \d)", text) and re.search(r"\b20\d\d\b", text)):
            continue                                                 # not an answer: a signature, a courtesy, a link, the "On … wrote" line
        return True
    return False


class Signatures:
    """What each sender puts under every email, learned from that sender's own mail.

    A line belongs to the signature when it stands, word for word, in the closing
    lines of at least three of the sender's recent emails (many people sign only
    some of their emails, so a share of them is not asked for). A line that reads
    as a sentence — it ends in a full stop or a question mark — must be there
    five times, so that "Please see attached." is not taken for a signature.
    What is learned is kept for a day, so the same email reads the same from one
    hour to the next — which is also what lets the stored prompt be reused."""

    TTL = 24 * 3600
    _cache: Dict[str, Tuple[float, FrozenSet[str]]] = {}
    _lock = threading.Lock()

    def __init__(self, artifacts):
        self.art = artifacts

    def learn(self, bodies: Sequence[str]) -> FrozenSet[str]:
        seen: Counter = Counter()
        n = 0
        for body in bodies:
            own, _ = split_quoted(body)
            # Learned from the text as it is shown (tidied), so a learned line matches the line it is compared with.
            tail = [_norm_line(ln) for ln in tidy(own).splitlines() if ln.strip()][-25:]
            if not tail:
                continue
            n += 1
            for ln in set(tail):
                if len(ln) >= 3:
                    seen[ln] += 1
        if n < 3:
            return frozenset()

        def sentence(ln: str) -> bool:
            return ln[-1:] in ".?!" and not re.search(r"\d|@|www\.|http|\b(?:llc|inc|l\.p|ltd|esq|corp)\.?$", ln)

        return frozenset(ln for ln, c in seen.items() if c >= (5 if sentence(ln) else 3) and not _COURTESY.match(ln))

    def lines(self, address: str) -> FrozenSet[str]:
        addr = (address or "").lower()
        if not addr:
            return frozenset()
        now = time.time()
        with self._lock:
            hit = self._cache.get(addr)
            if hit and now - hit[0] < self.TTL:
                return hit[1]
        try:
            bodies = [r.get("body_clean") or "" for r in self.art.find({"source_type": "email", "participants.from": addr},
                                                                       {"body_clean": 1}).sort("date", -1).limit(60)]
        except Exception:
            bodies = []
        learned = self.learn(bodies)
        with self._lock:
            self._cache[addr] = (now, learned)
        return learned

    @staticmethod
    def strip(own: str, learned: FrozenSet[str] = frozenset(), names: Iterable[str] = ()) -> str:
        """Take the signature off the END of a message. It stops at the first
        line, reading upward, that is not part of one — so nothing in the body
        of the message is touched, and a closing "Thanks" is kept."""
        known = {w.lower() for n in names for w in re.findall(r"[A-Za-z]{2,}", str(n or ""))}
        lines = own.splitlines()
        end = len(lines)
        while end > 0:
            raw = lines[end - 1].strip()
            s = raw.strip("*_-–—|·•> ")
            if not s:
                end -= 1
                continue
            words = re.findall(r"[A-Za-z][A-Za-z.'-]*", s)
            is_name = bool(words) and len(words) <= 4 and len(s) <= 60 and all(w[0].isupper() for w in words) and any(w.lower().strip(".") in known for w in words)
            scrap = len(re.findall(r"[A-Za-z0-9]", s)) < 2 or bool(re.fullmatch(r"[a-z/]{1,6}>", s))      # "v>", "--", what a mail client left behind
            if _norm_line(raw) in learned or _PHONE.match(s) or _CONTACT.match(s) or _PLACEHOLDER.match(s) or _SENT_FROM.match(s) or is_name or scrap:
                end -= 1
                continue
            break
        return "\n".join(lines[:end]).rstrip()


def clean(body: str, quoted_field: str, held: Sequence[str], *, learned: FrozenSet[str] = frozenset(), names: Iterable[str] = (),
          signature_of=None, drop_repeats: bool = False) -> Dict[str, object]:
    """One email as it is shown: its own words without the signature, then what
    it carries underneath.

    By default (``drop_repeats`` off — admin directive 2026-11-14, "it's a more
    safer option") an email is kept as it was sent: the chain quoted under it
    stays under it, whole, even where it repeats a message shown above. Only the
    signature comes off. On top of that, a quote the mail client stored apart
    from the body — which was never shown before — is added when the message it
    quotes is not held as an email of its own, or has answers written inside it.

    With ``drop_repeats`` on, a quoted message that stands in the chain as its own
    email is left out wherever it is quoted (measured: 58% of the email text).
    Kept for measuring; not in use.

    ``held`` are the texts of the earlier emails of the same conversation that
    are shown with it. Returns ``own``, ``kept`` (quoted messages shown, in the
    order written), ``why`` for each (sent | not_held | inside), and counts."""
    held = list(held)
    in_body = split_quoted(body, "")[1]                # what the email carries under it, as it was sent
    apart = [] if in_body else _pieces(quoted_field)   # what the mail client stored apart from the body (never shown before)
    own = tidy(Signatures.strip(tidy(split_quoted(body, "")[0]), learned, names))
    kept: List[str] = []
    why: List[str] = []
    for p, stored_apart in [(p, False) for p in in_body] + [(p, True) for p in apart]:
        if _only_signature(p, learned):
            continue
        if not stored_apart and not drop_repeats:
            reason = "sent"                            # the email as it was sent: its quoted chain stays under it, whole
        else:
            is_held = covered(p, held)
            answered_in_it = is_held and written_inside(p, held, signature_of=signature_of)
            if is_held and not answered_in_it:
                continue                               # the message stands in the chain itself: shown once, there
            reason = "inside" if answered_in_it else "not_held"
        text = tidy(p)
        if text:
            kept.append(text)
            why.append(reason)
    n = len(in_body) + len(apart)
    return {"own": own, "kept": kept, "why": why, "inside": [w == "inside" for w in why], "quoted": n, "left_out": n - len(kept)}


def _only_signature(piece: str, learned: FrozenSet[str]) -> bool:
    """A quoted piece with nothing in it but the sender's own signature and
    separator lines (a mail client puts the signature under the forward mark,
    or stores it apart from the body as if it were a quote)."""
    text = tidy(piece)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    has_header = bool(re.search(r"^\s*(?:>\s?)*(?:From:|On .{8,120} wrote:|-{2,}\s*(?:Original|Forwarded) [Mm]essage|Begin forwarded message:|\[Forwarded message\])", text, re.M))
    if not has_header and not any(ln.startswith(">") for ln in lines):
        # No header and no quote marks: not somebody's message. It is a signature block
        # unless it reads as prose — a long line, or two lines that end like sentences.
        prose = any(len(ln) > 90 for ln in lines) or sum(1 for ln in lines if ln[-1:] in ".?!" and len(ln.split()) >= 5) >= 2
        if not prose:
            return True
    words = 0
    for line in text.splitlines():
        s = line.strip().strip("*_-–—|·•> ")
        if not s or s == FORWARD_MARK or s.lower().startswith("begin forwarded message") or _HEADER_LINE.match(s):
            continue
        if _norm_line(line) in learned or _PHONE.match(s) or _CONTACT.match(s) or _SENT_FROM.match(s) or _COURTESY.match(s):
            continue
        words += len(re.findall(r"[A-Za-z]{2,}", s))
    return words < 4
