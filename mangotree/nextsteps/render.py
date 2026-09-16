"""Render a next-steps run for one person as DOCX and PDF, in the house style.

The style is the 14 September 2026 task sheet Rakesh sent Wes: navy serif title
under a small gold letter-spaced company line, an italic grey subtitle, then per
property a gold "PROPERTY 01" eyebrow, the address in navy serif, a one-line
italic status, and each step as a cream card with a gold rule on the left, a
gold number, a bold title, the detail, and a checkbox. Footer: company · sheet ·
page.

Rakesh's sheet shows, under his own steps for a property, a compact "Also on
this property" list of the other three people's steps, so he sees the whole
board without opening three files.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .generator import PERSONS, PERSON_LABEL

NAVY = "1F3550"
GOLD = "B0893B"
CREAM = "F7F2EA"
INK = "2A2A2E"
GREY = "6B6B76"
RULE = "D9D3C7"

SHEET_TITLE = {"wes": "Next steps — Wes", "manjunath": "Next steps — Manjunath Sir", "jp": "Next steps — JP Sir", "rakesh": "Next steps — Rakesh Sir"}
FOOTER_LABEL = {"wes": "Property next steps for Wes", "manjunath": "Property next steps for Manjunath Sir",
                "jp": "Property next steps for JP Sir", "rakesh": "Property next steps — CEO sheet"}


def _day_label(run: Dict[str, Any]) -> str:
    d = run.get("day") or ""
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%-d %B %Y") if d else ""
    except ValueError:
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%d %B %Y").lstrip("0")
        except ValueError:
            return d


def subtitle_for(run: Dict[str, Any]) -> str:
    return f"Property by property, from the {_day_label(run)} reviews"


def _due(s: Dict[str, Any]) -> str:
    d = s.get("due")
    if isinstance(d, datetime):
        return d.strftime("%d %b %Y").lstrip("0")
    return str(d)[:10] if d else ""


def sections(run: Dict[str, Any], person: str) -> List[Dict[str, Any]]:
    """Property blocks for one person's sheet: only properties with at least one
    step for that person (Rakesh's sheet also lists properties where only the
    others have steps, since he reads the whole board)."""
    out = []
    props = run.get("properties") or {}
    for pid in run.get("order") or []:
        r = props.get(pid) or {}
        mine = list(r.get(person) or [])
        others = {p: list(r.get(p) or []) for p in PERSONS if p != person} if person == "rakesh" else {}
        if not mine and not any(others.values()):
            continue
        out.append({"property_id": pid, "address": r.get("address") or pid, "headline": r.get("headline") or "",
                    "steps": mine, "others": others})
    return out


# =============================================================================
# PDF
# =============================================================================

def build_pdf(run: Dict[str, Any], person: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, HRFlowable

    navy, gold, cream, ink, grey, rule = (colors.HexColor("#" + c) for c in (NAVY, GOLD, CREAM, INK, GREY, RULE))
    buf = io.BytesIO()
    title = SHEET_TITLE[person]
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=20 * mm, title=title,
                            author="RKB Consulting Group, Inc.")
    st = {
        "eyebrow": ParagraphStyle("eyebrow", fontName="Helvetica-Bold", fontSize=7.2, leading=10, textColor=gold, spaceAfter=3),
        "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=23, leading=27, textColor=navy, spaceAfter=3),
        "subtitle": ParagraphStyle("subtitle", fontName="Times-Italic", fontSize=11, leading=14, textColor=grey, spaceAfter=6),
        "peye": ParagraphStyle("peye", fontName="Helvetica-Bold", fontSize=6.8, leading=9, textColor=gold, spaceBefore=10, spaceAfter=2),
        "addr": ParagraphStyle("addr", fontName="Times-Bold", fontSize=14.5, leading=18, textColor=navy, spaceAfter=1),
        "head": ParagraphStyle("head", fontName="Times-Italic", fontSize=10, leading=13, textColor=grey, spaceAfter=4),
        "num": ParagraphStyle("num", fontName="Times-Bold", fontSize=13, leading=15, textColor=gold),
        "stitle": ParagraphStyle("stitle", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=navy, spaceAfter=3),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.4, leading=13.2, textColor=ink),
        "why": ParagraphStyle("why", fontName="Helvetica-Oblique", fontSize=8.6, leading=12, textColor=grey, spaceBefore=3),
        "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=8.2, leading=11, textColor=grey, spaceBefore=4),
        "box": ParagraphStyle("box", fontName="Helvetica", fontSize=12, leading=14, textColor=navy, spaceBefore=6),
        "otitle": ParagraphStyle("otitle", fontName="Helvetica-Bold", fontSize=7.2, leading=10, textColor=grey, spaceBefore=6, spaceAfter=2),
        "oitem": ParagraphStyle("oitem", fontName="Helvetica", fontSize=8.6, leading=12, textColor=ink, leftIndent=8),
        "quiet": ParagraphStyle("quiet", fontName="Helvetica-Oblique", fontSize=9, leading=12, textColor=grey),
    }

    def esc(s: Any) -> str:
        return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story: List[Any] = [
        Paragraph("R K B &nbsp;C O N S U L T I N G &nbsp;G R O U P ,&nbsp; I N C .", st["eyebrow"]),
        Paragraph(esc(title), st["title"]),
        Paragraph(esc(subtitle_for(run)), st["subtitle"]),
        HRFlowable(width="100%", thickness=0.8, color=rule, spaceAfter=4),
    ]

    def card(i: int, s: Dict[str, Any]) -> Table:
        inner: List[Any] = [Paragraph(esc(s.get("title")), st["stitle"]), Paragraph(esc(s.get("detail")), st["body"])]
        if s.get("why_critical"):
            inner.append(Paragraph("Why now: " + esc(s["why_critical"]), st["why"]))
        meta = []
        if _due(s):
            meta.append(f"<b>Due {esc(_due(s))}</b>")
        if s.get("urgency") == "critical":
            meta.append("<font color='#B4432B'>Critical</font>")
        for e in (s.get("evidence") or [])[:1]:
            meta.append("“" + esc(e.get("quote", ""))[:180] + "”")
        if meta:
            inner.append(Paragraph(" &nbsp;·&nbsp; ".join(meta), st["meta"]))
        inner.append(Spacer(1, 5))
        # A drawn box, not a glyph: the core fonts have no ☐. A done step shows a tick (ZapfDingbats '4').
        box = Table([[Paragraph("<font name='ZapfDingbats' size='8'>4</font>", st["body"]) if s.get("done") else ""]],
                    colWidths=[4.4 * mm], rowHeights=[4.4 * mm], hAlign="LEFT")
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.9, navy), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
        inner.append(box)
        t = Table([[Paragraph(str(i), st["num"]), inner]], colWidths=[9 * mm, None])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), cream), ("LINEBEFORE", (0, 0), (0, -1), 2.2, gold),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, -1), 8), ("LEFTPADDING", (1, 0), (1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        return t

    for n, sec in enumerate(sections(run, person), start=1):
        head: List[Any] = [Paragraph(f"P R O P E R T Y &nbsp;{n:02d}", st["peye"]), Paragraph(esc(sec["address"]), st["addr"])]
        if sec["headline"]:
            head.append(Paragraph(esc(sec["headline"]), st["head"]))
        head.append(HRFlowable(width="100%", thickness=0.6, color=rule, spaceAfter=6))
        rest: List[Any] = []
        if sec["steps"]:
            for i, s in enumerate(sec["steps"], start=1):
                rest.append(card(i, s))
                rest.append(Spacer(1, 6))
        elif person == "rakesh":
            rest.append(Paragraph("Nothing needs you here today; the others' steps are below.", st["quiet"]))
            rest.append(Spacer(1, 4))
        if person == "rakesh":
            for other in ("wes", "manjunath", "jp"):
                items = sec["others"].get(other) or []
                if not items:
                    continue
                rest.append(Paragraph(f"ALSO ON THIS PROPERTY — {esc(PERSON_LABEL[other]).upper()}", st["otitle"]))
                for s in items:
                    due = f" <font color='#{GREY}'>(due {esc(_due(s))})</font>" if _due(s) else ""
                    rest.append(Paragraph("• " + esc(s.get("title")) + due, st["oitem"]))
        # The property header never sits alone at the foot of a page: it travels
        # with its first card.
        story.append(KeepTogether(head + rest[:1]))
        story.extend(rest[1:])
        story.append(Spacer(1, 8))

    if len(story) <= 4:
        story.append(Paragraph("Nothing critical today.", st["quiet"]))

    footer_label = FOOTER_LABEL[person]

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setStrokeColor(rule)
        canvas.setLineWidth(0.6)
        canvas.line(22 * mm, 14 * mm, A4[0] - 22 * mm, 14 * mm)
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(grey)
        canvas.drawCentredString(A4[0] / 2, 9.5 * mm, f"RKB Consulting Group, Inc.   ·   {footer_label}   ·   {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


# =============================================================================
# DOCX
# =============================================================================

def build_docx(run: Dict[str, Any], person: str) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    navy, gold, ink, grey = (RGBColor.from_string(c) for c in (NAVY, GOLD, INK, GREY))
    d = Document()
    for s in d.sections:
        s.left_margin = s.right_margin = Cm(2.2)
        s.top_margin = Cm(2.0)
        s.bottom_margin = Cm(1.8)
    normal = d.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)

    def para(text: str, *, size: float, bold=False, italic=False, color=ink, font: Optional[str] = None,
             before=0, after=2, spacing: Optional[float] = None, align=None):
        p = d.add_paragraph()
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        if align is not None:
            p.alignment = align
        r = p.add_run(text)
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.italic = italic
        r.font.color.rgb = color
        if font:
            r.font.name = font
            r._element.rPr.rFonts.set(qn("w:eastAsia"), font)
        if spacing:
            rpr = r._element.get_or_add_rPr()
            sp = OxmlElement("w:spacing")
            sp.set(qn("w:val"), str(int(spacing * 20)))
            rpr.append(sp)
        return p

    def rule():
        p = d.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        ppr = p._p.get_or_add_pPr()
        pbdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "6"); bottom.set(qn("w:space"), "1"); bottom.set(qn("w:color"), RULE)
        pbdr.append(bottom)
        ppr.append(pbdr)

    def shade(cell, fill: str):
        tcpr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        tcpr.append(shd)

    def borders(cell, *, left: Optional[str] = None):
        tcpr = cell._tc.get_or_add_tcPr()
        b = OxmlElement("w:tcBorders")
        for side in ("top", "bottom", "right", "left"):
            el = OxmlElement(f"w:{side}")
            if side == "left" and left:
                el.set(qn("w:val"), "single"); el.set(qn("w:sz"), "24"); el.set(qn("w:color"), left)
            else:
                el.set(qn("w:val"), "nil")
            b.append(el)
        tcpr.append(b)

    def card(i: int, s: Dict[str, Any]):
        t = d.add_table(rows=1, cols=2)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        num, body = t.rows[0].cells
        num.width = Cm(1.1)
        body.width = Cm(15.4)
        for c in (num, body):
            shade(c, CREAM)
        borders(num, left=GOLD)
        borders(body)
        np_ = num.paragraphs[0]
        np_.paragraph_format.space_before = Pt(6)
        r = np_.add_run(str(i)); r.font.size = Pt(13); r.font.bold = True; r.font.color.rgb = gold; r.font.name = "Georgia"
        bp = body.paragraphs[0]
        bp.paragraph_format.space_before = Pt(6); bp.paragraph_format.space_after = Pt(2)
        r = bp.add_run(s.get("title") or ""); r.font.bold = True; r.font.size = Pt(10.5); r.font.color.rgb = navy
        p = body.add_paragraph(); p.paragraph_format.space_after = Pt(2)
        r = p.add_run(s.get("detail") or ""); r.font.size = Pt(10); r.font.color.rgb = ink
        if s.get("why_critical"):
            p = body.add_paragraph(); p.paragraph_format.space_after = Pt(2)
            r = p.add_run("Why now: " + s["why_critical"]); r.font.size = Pt(9); r.font.italic = True; r.font.color.rgb = grey
        meta = []
        if _due(s):
            meta.append(f"Due {_due(s)}")
        if s.get("urgency") == "critical":
            meta.append("Critical")
        if meta:
            p = body.add_paragraph(); p.paragraph_format.space_after = Pt(2)
            r = p.add_run("  ·  ".join(meta)); r.font.size = Pt(9); r.font.bold = True; r.font.color.rgb = grey
        p = body.add_paragraph(); p.paragraph_format.space_before = Pt(4); p.paragraph_format.space_after = Pt(8)
        r = p.add_run("☑" if s.get("done") else "☐"); r.font.size = Pt(14); r.font.color.rgb = navy
        d.add_paragraph().paragraph_format.space_after = Pt(2)

    para("RKB CONSULTING GROUP, INC.", size=7.5, bold=True, color=gold, spacing=2.2, after=2)
    para(SHEET_TITLE[person], size=23, bold=True, color=navy, font="Georgia", after=1)
    para(subtitle_for(run), size=11, italic=True, color=grey, font="Georgia", after=2)
    rule()

    secs = sections(run, person)
    for n, sec in enumerate(secs, start=1):
        para(f"PROPERTY {n:02d}", size=7, bold=True, color=gold, spacing=2.0, before=12, after=1)
        para(sec["address"], size=14.5, bold=True, color=navy, font="Georgia", after=0)
        if sec["headline"]:
            para(sec["headline"], size=10, italic=True, color=grey, font="Georgia", after=2)
        rule()
        if sec["steps"]:
            for i, s in enumerate(sec["steps"], start=1):
                card(i, s)
        elif person == "rakesh":
            para("Nothing needs you here today; the others' steps are below.", size=9.5, italic=True, color=grey)
        if person == "rakesh":
            for other in ("wes", "manjunath", "jp"):
                items = sec["others"].get(other) or []
                if not items:
                    continue
                para(f"ALSO ON THIS PROPERTY — {PERSON_LABEL[other].upper()}", size=7.2, bold=True, color=grey, spacing=1.4, before=6, after=1)
                for s in items:
                    p = para("•  " + (s.get("title") or "") + (f"  (due {_due(s)})" if _due(s) else ""), size=9.2, color=ink, after=1)
                    p.paragraph_format.left_indent = Cm(0.4)
    if not secs:
        para("Nothing critical today.", size=10, italic=True, color=grey)

    # footer
    for s in d.sections:
        fp = s.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = fp.add_run(f"RKB Consulting Group, Inc.   ·   {FOOTER_LABEL[person]}")
        r.font.size = Pt(7.5); r.font.color.rgb = grey

    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def filename_for(run: Dict[str, Any], person: str, ext: str) -> str:
    who = {"wes": "Wes", "manjunath": "Manjunath Sir", "jp": "JP Sir", "rakesh": "Rakesh Sir"}[person]
    return f"Next steps - {who} - {run.get('day')}.{ext}"
