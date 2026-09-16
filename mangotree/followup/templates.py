"""Email wording for the standard procedure. Kind, warm, specific; "Sir" for our
own people; never a demand. Plain text and a light HTML twin of the same words.
"""
from __future__ import annotations

import html as _h
from datetime import datetime
from typing import Any, Dict, List, Sequence, Tuple

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.retrieve import config as cfg

LABEL = {"rakesh": "Rakesh Sir", "jp": "JP Sir", "manjunath": "Manjunath Sir", "wes": "Wes"}
SIGNATURE_TEXT = "Warm regards,\nRakesh Bhargava\nRKB Consulting Group, Inc."
SIGNATURE_HTML = "Warm regards,<br>Rakesh Bhargava<br><span style='color:#6b6b76'>RKB Consulting Group, Inc.</span>"


def _addr(pids: Sequence[str]) -> str:
    names = [PROPERTY_INDEX[p].canonical_address for p in pids if p in PROPERTY_INDEX]
    return ", ".join(names) if names else "the portfolio"


def _date(d: Any) -> str:
    return d.strftime("%d %B").lstrip("0") if isinstance(d, datetime) else str(d or "")[:10]


def _wrap(paragraphs: List[str], bullets: Dict[str, List[str]] | None = None, closing: str = "") -> Tuple[str, str]:
    """(html, text) from paragraphs, optional titled bullet lists, and a closing."""
    text_parts, html_parts = [], []
    for p in paragraphs:
        text_parts.append(p)
        html_parts.append(f"<p style='margin:0 0 12px'>{_h.escape(p)}</p>")
    for title, items in (bullets or {}).items():
        if not items:
            continue
        text_parts.append(title + "\n" + "\n".join(f"  • {i}" for i in items))
        html_parts.append(f"<p style='margin:14px 0 4px;font-weight:600;color:#1f3550'>{_h.escape(title)}</p><ul style='margin:0 0 12px 18px;padding:0'>"
                          + "".join(f"<li style='margin:0 0 6px'>{_h.escape(i)}</li>" for i in items) + "</ul>")
    if closing:
        text_parts.append(closing)
        html_parts.append(f"<p style='margin:14px 0 12px'>{_h.escape(closing)}</p>")
    text_parts.append(SIGNATURE_TEXT)
    html_parts.append(f"<p style='margin:18px 0 0'>{SIGNATURE_HTML}</p>")
    html = ("<div style='font-family:Calibri,Segoe UI,Helvetica,Arial,sans-serif;font-size:14.5px;line-height:1.55;color:#2a2a2e;max-width:640px'>"
            + "".join(html_parts) + "</div>")
    return html, "\n\n".join(text_parts)


# ----------------------------------------------------------------- next steps
def next_steps_cover(person: str, *, day_label: str, top: List[Dict[str, Any]], followups: List[Dict[str, Any]],
                     new_since_last: List[Dict[str, Any]], is_reminder: bool = False) -> Tuple[str, str, str]:
    """Subject, html, text for the sheet email to JP Sir / Manjunath Sir."""
    who = LABEL[person]
    tag = cfg.SYSTEM_MAIL_TAG
    subject = f"{tag} Next steps for you — {day_label}" + (" (gentle reminder)" if is_reminder else "")
    opening = [f"Dear {who},"]
    if is_reminder:
        opening.append(f"A gentle reminder, Sir — I had sent across today's next steps ({day_label}) and have not yet seen a reply. "
                       "I know the day gets busy; whenever you have a moment, please do glance through the attached sheet and send me a one-line acknowledgement so I know it has reached you.")
    else:
        opening.append(f"Please find attached today's next steps for your properties, prepared from the {day_label} reviews — a Word file and a PDF of the same sheet. "
                       "It is short by design: only the one or two things per property that truly need you.")
    bullets: Dict[str, List[str]] = {}
    if top:
        bullets["Most important today"] = [f"{s.get('address')}: {s.get('title')}" + (f" — by {_date(s.get('due'))}" if s.get("due") else "") for s in top[:3]]
    if followups:
        bullets["Still awaiting your reply"] = [f"{(f.get('counterparty') or {}).get('name') or 'A counterparty'} on {_addr(f.get('property_ids') or [])}: {f.get('what')} (since {_date(f.get('asked_at'))})" for f in followups[:5]]
    if new_since_last and not is_reminder:
        bullets["New since yesterday's sheet"] = [f"{s.get('address')}: {s.get('title')}" for s in new_since_last[:5]]
    closing = ("Kindly reply to this email once you have gone through it, Sir — a single line is enough. The system notes the reply, so I will not trouble you again for it. "
               "And if anything here looks wrong or already done, please just say so in your reply and it will be corrected.")
    html, text = _wrap(opening, bullets, closing)
    return subject, html, text


# ------------------------------------------------------------------ reminders
def internal_reminder(f: Dict[str, Any], owner: str) -> Tuple[str, str, str]:
    who = LABEL.get(owner, owner)
    cp = (f.get("counterparty") or {}).get("name") or "the counterparty"
    subject = f"{cfg.SYSTEM_MAIL_TAG} Awaiting your reply to {cp} — {_addr(f.get('property_ids') or [])}"
    paragraphs = [
        f"Dear {who},",
        f"A kind reminder, Sir. {cp} wrote on {_date(f.get('asked_at'))} regarding {_addr(f.get('property_ids') or [])} "
        f"(subject: “{f.get('subject') or ''}”), and a reply from our side is still pending:",
        f"— {f.get('what')}",
        "Could you please respond to them today, Sir, even if only to say when the full answer will follow? A short reply keeps the work moving and keeps our word with Wes's team. "
        "If this has already been handled by phone or from another mailbox, please reply here with one line and I will close it.",
    ]
    html, text = _wrap(paragraphs)
    return subject, html, text


def external_reminder(f: Dict[str, Any]) -> Tuple[str, str]:
    """Draft for Rakesh (or whoever asked) to send to Wes / Kelly. Plain text."""
    cp = (f.get("counterparty") or {}).get("name") or "there"
    first = cp.split(" ")[0]
    props = _addr(f.get("property_ids") or [])
    subject = f"Re: {f.get('subject') or props}"
    body = (f"Hi {first},\n\n"
            f"I hope you are well. Following up on my note of {_date(f.get('asked_at'))} about {props} — we are still waiting on:\n\n"
            f"  • {f.get('what')}\n\n"
            "I know there is a lot on your plate; even a quick update on where it stands, and when we can expect it, would help us keep the funding side moving without delay.\n\n"
            "Many thanks, as always, for your help.\n\n"
            "Best regards,\nRakesh Bhargava\nRKB Consulting Group, Inc.")
    return subject, body
