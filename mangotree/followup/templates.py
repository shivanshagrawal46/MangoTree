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
                     new_since_last: List[Dict[str, Any]], carried: List[Dict[str, Any]] | None = None,
                     unacknowledged_since: Any = None, is_reminder: bool = False) -> Tuple[str, str, str]:
    """Subject, html, text for the one daily email to JP Sir / Manjunath Sir —
    the sheet attached, and a cover note that is the whole message for the day."""
    who = LABEL[person]
    tag = cfg.SYSTEM_MAIL_TAG
    carried = carried or []
    subject = f"{tag} Next steps for you — {day_label}" + (" (gentle reminder)" if is_reminder else "")
    opening = [f"Dear {who},"]
    if is_reminder:
        opening.append(f"A gentle reminder, Sir — I had sent across today's next steps ({day_label}) and have not yet seen a reply. "
                       "I know the day gets busy; whenever you have a moment, please do glance through the attached sheet and send me a one-line acknowledgement so I know it has reached you.")
    else:
        opening.append(f"Please find attached today's next steps for your properties, prepared from the {day_label} reviews — a Word file and a PDF of the same sheet. "
                       "It is short by design: only the one or two things per property that truly need you.")
        if unacknowledged_since:
            opening.append(f"I did not see a reply to yesterday's sheet (sent {_date(unacknowledged_since)}), Sir — no trouble at all, today's replaces it; "
                           "a one-line acknowledgement to this one would be a great help.")
    bullets: Dict[str, List[str]] = {}
    if top:
        bullets["Most important today"] = [f"{s.get('address')}: {s.get('title')}" + (f" — by {_date(s.get('due'))}" if s.get("due") else "") for s in top[:3]]
    if carried:
        bullets["Still open from earlier sheets"] = [
            f"{s.get('address')}: {s.get('title')} — on the sheet since {_date_str(s.get('first_seen'))}, day {int(s.get('carried_days') or 0) + 1}"
            + (f", due {_date(s.get('due'))}" if s.get("due") else "") for s in carried[:3]]
    if followups:
        bullets["Still awaiting your reply — most important"] = [
            f"{(f.get('counterparty') or {}).get('name') or 'A counterparty'} on {_addr(f.get('property_ids') or [])}: {f.get('what')} (since {_date(f.get('asked_at'))}"
            f"{', escalated' if f.get('status') == 'escalated' else ''})" for f in followups[:3]]
    if new_since_last and not is_reminder:
        bullets["New since yesterday's sheet"] = [f"{s.get('address')}: {s.get('title')}" for s in new_since_last[:5]]
    closing = ("Kindly reply to this email once you have gone through it, Sir — a single line is enough. "
               + ("If a step listed as still open is already done, please tick it on your desk or tell me here, so it does not come back tomorrow. " if carried else "")
               + "And if anything here looks wrong, please just say so in your reply and it will be corrected.")
    html, text = _wrap(opening, bullets, closing)
    return subject, html, text


def wes_cover(*, day_label: str, top: List[Dict[str, Any]], carried: List[Dict[str, Any]], properties: int) -> Tuple[str, str, str]:
    """Subject, html, text for Rakesh's email to Wes with his sheet — warm,
    partner to partner; the sheet does the asking, the note frames it."""
    subject = f"Next steps for you — {day_label}"
    paragraphs = [
        "Hi Wes,",
        (f"Attached is today's sheet — one or two items per property that matter most right now, across {properties} "
         f"propert{'y' if properties == 1 else 'ies'}, as a Word file and a PDF of the same thing. It comes out of the {day_label} review of everything "
         "in the file, so where it names a date or a figure, that is where it came from."),
    ]
    bullets: Dict[str, List[str]] = {}
    if top:
        bullets["The ones I'd put first"] = [f"{s.get('address')}: {s.get('title')}" + (f" — by {_date(s.get('due'))}" if s.get("due") else "") for s in top[:3]]
    if carried:
        bullets["Still open from an earlier sheet"] = [
            f"{s.get('address')}: {s.get('title')} — since {_date_str(s.get('first_seen'))}" for s in carried[:3]]
    closing = ("A quick reply with where each item stands — done, in hand, or blocked and why — is all I need; a line per property is plenty. "
               "If anything on the sheet is already handled or simply wrong, tell me and it comes off tomorrow's. Thank you, as always, for the work.")
    text_parts = paragraphs + [f"{k}\n" + "\n".join(f"  • {i}" for i in v) for k, v in bullets.items() if v] + [closing, "Best regards,\nRakesh Bhargava\nRKB Consulting Group, Inc."]
    html, _ = _wrap(paragraphs, bullets, closing)
    html = html.replace("Warm regards,<br>", "Best regards,<br>")
    return subject, html, "\n\n".join(text_parts)


# ------------------------------------------------------------------ reminders
def internal_digest(owner: str, top: List[Dict[str, Any]], more: int,
                    carried_steps: List[Dict[str, Any]] | None = None) -> Tuple[str, str, str]:
    """One kind email a day to JP Sir / Manjunath Sir: the two or three replies
    they owe that matter most (oldest and escalated first), plus any urgent next
    step that has now been carried over unfinished — the way a colleague would
    mention both in one note rather than send five."""
    who = LABEL.get(owner, owner)
    carried_steps = carried_steps or []
    n = len(top) + more
    parts = []
    if n:
        parts.append(f"{n} repl{'y' if n == 1 else 'ies'} awaiting you")
    if carried_steps:
        parts.append(f"{len(carried_steps)} step{'s' if len(carried_steps) > 1 else ''} still open")
    where = ", ".join(sorted({_addr(f.get("property_ids") or []) for f in top} | {s.get("address") or "" for s in carried_steps} - {""}))[:80]
    subject = f"{cfg.SYSTEM_MAIL_TAG} {' and '.join(parts)} — {where}" if where else f"{cfg.SYSTEM_MAIL_TAG} {' and '.join(parts)}"
    paragraphs = [f"Dear {who},"]
    if n:
        paragraphs.append(f"A kind reminder, Sir. {'One reply is' if n == 1 else f'{n} replies are'} still pending from our side, and the ones below matter most today. "
                          "Each stays on your desk in MangoTree until you answer in the email thread or tick it done there.")
    else:
        paragraphs.append("A kind reminder, Sir, about a few items from your next-steps sheet that are still open.")
    bullets: Dict[str, List[str]] = {}
    if top:
        items = []
        for f in top:
            cp = (f.get("counterparty") or {}).get("name") or "the counterparty"
            flag = " — ESCALATED" if f.get("status") == "escalated" else ""
            items.append(f"{cp} · {_addr(f.get('property_ids') or [])} · since {_date(f.get('asked_at'))}{flag}: {f.get('what')}")
        bullets["Replies most needed"] = items
    if carried_steps:
        bullets["Still open from your earlier sheet"] = [
            f"{s.get('address')}: {s.get('title')} — on the sheet since {_date_str(s.get('first_seen'))}, day {int(s.get('carried_days') or 0) + 1}"
            + (f", due {_date(s.get('due'))}" if s.get("due") else "") for s in carried_steps[:3]]
    closing = (f"{'And ' + str(more) + ' more ' + ('is' if more == 1 else 'are') + ' listed on your desk. ' if more else ''}"
               + ("Could you please reply to each of them today, Sir, even if only to say when the full answer will follow? " if n else "")
               + ("If a step above is already done, please tick it on your desk (or tell me here) so it does not come back tomorrow; if something is blocking it, a line on what would help. " if carried_steps else "")
               + "A short reply keeps the work moving and keeps our word with Wes's team.")
    html, text = _wrap(paragraphs, bullets, closing)
    return subject, html, text


def _date_str(v: Any) -> str:
    if isinstance(v, datetime):
        return _date(v)
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%d %B").lstrip("0")
    except (ValueError, TypeError):
        return str(v or "")[:10]


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
