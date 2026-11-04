"""Render hk_notes_outlook.html to a PDF with Chromium (Playwright).

The page is built for the screen: one interactive "one ZIP at a time" chart with a
<select>. For print, every ZIP is rendered in turn and the chart is replaced by a
grid of 17 static figures, so the PDF carries the full analysis. Light theme,
US Letter, page breaks before each numbered section.
"""
import os, sys, time
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "hk_notes_outlook.html")
OUT = os.path.join(HERE, "HK_Homes_Note_Portfolio_Outlook.pdf")

PRINT_CSS = """
@page { size: Letter; margin: 16mm 14mm 18mm 14mm; }
:root { color-scheme: light !important; }
body { padding: 0 !important; font-size: 12.5px !important; }
.wrap { max-width: none !important; }
.prose { max-width: none !important; }
h2 { break-before: page; margin-top: 0 !important; }
section:first-of-type h2, h2.nobreak { break-before: auto; }
h3, .cap { break-after: avoid; }
.tbl { overflow: visible !important; }
.chart, .kf, .legend, figure, .zipgrid > div { break-inside: avoid; }
tr { break-inside: avoid; }
thead { display: table-header-group; }
table { font-size: 11px !important; }
th, td { padding: 5px 7px !important; }
thead th, tbody th { white-space: normal !important; }
.flags { min-width: 0 !important; font-size: 10px !important; }
.pill { font-size: 9.5px !important; padding: 3px 5px !important; }
table:has(.flags) { font-size: 10px !important; }
table:has(.flags) th, table:has(.flags) td { padding: 4px 5px !important; }
.chart { height: 300px !important; }
a { color: inherit; text-decoration: none; }
.zipgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; margin: 12px 0; }
.zipgrid > div { break-inside: avoid; }
.zipgrid img { width: 100%; height: auto; display: block; border: 1px solid var(--rule); }
.zipgrid b { display: block; font: 500 12px/1.3 var(--mono); margin: 0 0 4px; }
.zipgrid small { display: block; color: var(--muted); font-size: 10.5px; line-height: 1.35; margin-top: 4px; }
.pdfnote { color: var(--muted); font-size: 11px; margin: 6px 0 0; }
"""

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 960, "height": 1200}, device_scale_factor=2)
    pg.emulate_media(color_scheme="light")
    pg.goto("file:///" + SRC.replace("\\", "/"), wait_until="networkidle", timeout=120000)
    pg.wait_for_function("document.fonts.status === 'loaded'", timeout=30000)
    pg.wait_for_timeout(1500)

    # Render every ZIP through the interactive chart and keep each as a PNG data URL.
    n = pg.evaluate("document.querySelectorAll('#zipsel option').length")
    print("zip options:", n)
    # Shrink the interactive chart to the width it will print at, so axis labels stay legible.
    pg.evaluate("() => { const c = document.getElementById('c_zip').closest('.chart'); c.style.width = '440px'; c.style.height = '250px'; }")
    pg.wait_for_timeout(700)
    figs = []
    for i in range(n):
        pg.evaluate("i => { const s = document.getElementById('zipsel'); s.selectedIndex = i; s.dispatchEvent(new Event('change', {bubbles: true})); }", i)
        pg.wait_for_timeout(350)
        label = pg.evaluate("document.getElementById('zipsel').selectedOptions[0].textContent")
        desc = pg.evaluate("(document.getElementById('zipdesc') || {}).textContent || ''")
        data = pg.evaluate("document.getElementById('c_zip').toDataURL('image/png')")
        figs.append((label.strip(), desc.strip(), data))
    print("rendered", len(figs), "zip charts")

    # Replace the select + chart with the grid of figures, then apply print CSS.
    pg.evaluate(
        """figs => {
            const sel = document.getElementById('zipsel');
            const para = sel.closest('p');
            const chart = document.getElementById('c_zip').closest('.chart');
            const grid = document.createElement('div');
            grid.className = 'zipgrid';
            for (const [label, desc, src] of figs) {
                const d = document.createElement('div');
                const b = document.createElement('b'); b.textContent = label;
                const img = document.createElement('img'); img.src = src; img.alt = label;
                const s = document.createElement('small'); s.textContent = desc;
                d.appendChild(b); d.appendChild(img); if (desc) d.appendChild(s);
                grid.appendChild(d);
            }
            para.replaceWith(grid);
            chart.remove();
            for (const c of document.querySelectorAll('p.cap')) {
                if (c.textContent.startsWith('Pick a ZIP')) c.textContent = 'Each ZIP: its history since 2016, the fair-value marker, and the three five-year paths from section 3 (bull, base, bear).';
            }
            const h2s = document.querySelectorAll('h2');
            if (h2s.length) h2s[0].classList.add('nobreak');
        }""",
        figs,
    )
    pg.add_style_tag(content=PRINT_CSS)
    # Charts resize to the print width when Chromium lays out the page; give Chart.js a moment.
    pg.wait_for_timeout(800)
    pg.pdf(
        path=OUT,
        format="Letter",
        print_background=True,
        prefer_css_page_size=True,
        display_header_footer=True,
        header_template="<div></div>",
        footer_template=(
            "<div style='width:100%;font-family:IBM Plex Mono,Consolas,monospace;font-size:8px;color:#777;"
            "padding:0 14mm;display:flex;justify-content:space-between'>"
            "<span>H&amp;K Homes note portfolio outlook, RKB Consulting Group, 16 October 2026</span>"
            "<span class='pageNumber'></span></div>"
        ),
    )
    b.close()

from pypdf import PdfReader
r = PdfReader(OUT)
print("pages:", len(r.pages), "size:", os.path.getsize(OUT) // 1024, "KB")
print(OUT)
