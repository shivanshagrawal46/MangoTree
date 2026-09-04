"""Cross-provider thread stitching.

Threads are stitched on ``Message-ID`` / ``In-Reply-To`` / ``References`` —
identifiers written into the message itself at creation, immutable thereafter,
and therefore valid across Gmail *and* Outlook. This is deterministic string
matching, never a similarity guess.

That matters here because a single negotiation routinely alternates providers:
Rakesh Sir sends from Gmail under the ``rakesh@mtreh.com`` alias, and the reply
lands in Outlook because it is addressed to ``rakesh@mtreh.com``.

The union-find structure lets late-arriving messages merge two previously
separate fragments into one conversation without a rebuild.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

_MSGID_RE = re.compile(r"<[^<>@\s]+@[^<>\s]+>")
_SUBJECT_PREFIX = re.compile(r"(?i)^\s*(re|fw|fwd|aw|antw|res|rif)\s*(\[\d+\])?\s*:\s*")


def parse_message_ids(header_value: Optional[str]) -> List[str]:
    if not header_value:
        return []
    return [m.strip() for m in _MSGID_RE.findall(header_value)]


def normalize_subject(subject: Optional[str]) -> str:
    """Strip Re:/Fwd: chains for the conservative fallback key."""
    text = (subject or "").strip()
    prev = None
    while prev != text:
        prev = text
        text = _SUBJECT_PREFIX.sub("", text).strip()
    return re.sub(r"\s+", " ", text).lower()


@dataclass
class ThreadIndex:
    """Union-find over message ids, resolving to a stable thread key."""

    parent: Dict[str, str] = field(default_factory=dict)

    def _find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        # path compression
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> str:
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            return ra
        # Deterministic winner so thread keys are stable across runs.
        winner, loser = (ra, rb) if ra <= rb else (rb, ra)
        self.parent[loser] = winner
        return winner

    def add(
        self,
        message_id: Optional[str],
        references: Sequence[str] = (),
        in_reply_to: Sequence[str] = (),
        *,
        fallback: str = "",
    ) -> str:
        """Register a message and return its thread key."""
        node = message_id or fallback
        if not node:
            return ""
        self._find(node)
        for other in (*references, *in_reply_to):
            if other:
                self.union(node, other)
        return self._find(node)


def thread_key_for(
    index: ThreadIndex,
    *,
    message_id: Optional[str],
    references: Sequence[str] = (),
    in_reply_to: Sequence[str] = (),
    subject: Optional[str] = None,
    participants: Optional[Iterable[str]] = None,
    provider_thread_id: Optional[str] = None,
) -> str:
    """Compute a stable thread key for one message.

    Falls back conservatively: when headers are missing (some forwarding tools
    strip them) we prefer the provider's own thread id, and only then a
    subject+participants key. When uncertain we keep threads *separate* — a
    wrongly merged thread contaminates two properties' timelines, while a
    wrongly split one merely looks untidy.
    """
    if message_id or references or in_reply_to:
        key = index.add(message_id, references, in_reply_to)
        if key:
            return key

    if provider_thread_id:
        return f"provider:{provider_thread_id}"

    subject_norm = normalize_subject(subject)
    if subject_norm:
        who = ",".join(sorted({p.lower() for p in (participants or []) if p})[:4])
        return f"subject:{subject_norm}|{who}"

    return ""
