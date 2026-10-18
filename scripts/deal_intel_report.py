"""Render the Deal Intelligence report — portfolio overview, cross-deal synthesis and one
page per deal — as HTML (interactive) and PDF, from ``deals``, ``deal_postmortems`` and
``deal_intel_reports``.

    python scripts/deal_intel_report.py --out "C:\\Users\\...\\Desktop\\Deal Intelligence - Sold Properties"
"""
from __future__ import annotations

import argparse
import html
import io
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import markdown  # noqa: E402

from mangotree.config.registry import PROPERTY_INDEX  # noqa: E402
from mangotree.storage.mongo import get_mongo  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CSS = """
:root{--navy:#1F3550;--gold:#B0893B;--ink:#2A2A2E;--grey:#6B6B76;--rule:#D9D3C7;--cream:#F7F2EA;--red:#B4432B;--green:#2E7D4F;--bg:#FBF9F5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Calibri,"Segoe UI",Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:26px 28px 60px}
.eyebrow{font-size:10px;letter-spacing:.24em;font-weight:700;color:var(--gold)}
h1{font-family:Georgia,serif;color:var(--navy);font-size:30px;margin:4px 0 2px;line-height:1.1}
.sub{font-family:Georgia,serif;font-style:italic;color:var(--grey);font-size:15px;margin-bottom:14px}
nav.tabs{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;flex-wrap:wrap;gap:6px;padding:10px 0;border-bottom:1px solid var(--rule);margin-bottom:18px}
nav.tabs a{text-decoration:none;color:var(--grey);font-size:12.5px;font-weight:600;padding:6px 11px;border-radius:999px;border:1px solid transparent}
nav.tabs a:hover{color:var(--navy);background:#fff;border-color:var(--rule)}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:12px 0 18px}
.kpi{background:#fff;border:1px solid var(--rule);border-radius:12px;padding:12px 14px}
.kpi .v{font-family:Georgia,serif;font-size:22px;color:var(--navy);line-height:1.1}.kpi .v.red{color:var(--red)}.kpi .v.green{color:var(--green)}
.kpi .l{font-size:11.5px;color:var(--grey);margin-top:4px}
h2{font-family:Georgia,serif;color:var(--navy);font-size:21px;margin:26px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--rule)}
h3{color:var(--navy);font-size:15px;margin:16px 0 4px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--rule);font-size:12.5px;margin:6px 0 10px}
th{background:var(--cream);color:var(--navy);text-align:left;padding:7px 9px;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;white-space:nowrap}
td{padding:6px 9px;border-top:1px solid var(--rule);vertical-align:top}
td.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.bad td{background:#FBEDEA}tr.good td{background:#EEF6F0}tr.mixed td{background:#FBF6EA}
.tag{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:2px 7px;border-radius:4px;color:#fff}
.tag.good{background:var(--green)}.tag.bad{background:var(--red)}.tag.mixed{background:var(--gold)}.tag.na{background:var(--grey)}
.md h2{font-size:17px;margin:20px 0 4px}.md h3{font-size:14px}.md p{margin:0 0 8px}.md li{margin:0 0 4px}.md table{font-size:12px}
.md blockquote{border-left:3px solid var(--gold);margin:6px 0;padding:4px 10px;color:var(--grey)}
.card{background:#fff;border:1px solid var(--rule);border-radius:12px;padding:14px 18px;margin:8px 0}
.deal-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.meta{font-size:12px;color:var(--grey)}
.cite{color:var(--gold);font-size:11px}
a{color:var(--navy)}
.foot{margin-top:30px;padding-top:8px;border-top:1px solid var(--rule);font-size:11px;color:var(--grey)}
details{background:#fff;border:1px solid var(--rule);border-radius:10px;padding:8px 14px;margin:0 0 8px}summary{cursor:pointer;font-weight:700;color:var(--navy)}
@media print{body{background:#fff;font-size:11.5px}.wrap{max-width:none;padding:0}nav.tabs{display:none}h2{break-after:avoid}.card,.kpi,tr{break-inside:avoid}.page{break-before:page}@page{size:Letter;margin:14mm}}
"""
JS = """
document.querySelectorAll('table.sortable th').forEach((th, i) => th.addEventListener('click', () => {
  const t = th.closest('table'); const rows = [...t.tBodies[0].rows]; const asc = !(th.dataset.asc === '1');
  th.dataset.asc = asc ? '1' : '0';
  rows.sort((a, b) => { const x = a.cells[i].dataset.v ?? a.cells[i].innerText, y = b.cells[i].dataset.v ?? b.cells[i].innerText;
    const nx = parseFloat(x), ny = parseFloat(y); const c = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y)); return asc ? c : -c; });
  rows.forEach(r => t.tBodies[0].appendChild(r)); }));
"""


def money(v):
    return "" if v is None else f"${v:,.0f}"


def pct(v):
    return "" if v is None else f"{v*100:.1f}%"


def dstr(v):
    return v.strftime("%d %b %Y") if isinstance(v, datetime) else (str(v)[:10] if v else "")


def verdict_of(answer: str) -> str:
    m = re.search(r"##\s*8\.\s*Verdict\s*(.*?)(?:\n##|\Z)", answer or "", re.S)
    seg = (m.group(1) if m else "")[:400].upper()
    for v in ("GOOD", "BAD", "MIXED"):
        if re.search(rf"\b{v}\b", seg):
            # first-mentioned verdict word wins
            pass
    order = sorted([(seg.find(v), v) for v in ("GOOD", "BAD", "MIXED") if seg.find(v) >= 0])
    return order[0][1].lower() if order else "na"


def md(text: str) -> str:
    text = re.sub(r"\[#(\d+)\]", r'<span class="cite">[#\1]</span>', text or "")
    return markdown.markdown(text, extensions=["tables", "sane_lists"])


def build(out: Path) -> None:
    m = get_mongo()
    deals = {d["property_id"]: d for d in m.db["deals"].find({}, {"_id": 0})}
    pms = {d["property_id"]: d for d in m.db["deal_postmortems"].find({}, {"_id": 0})}
    syn = m.db["deal_intel_reports"].find_one({"kind": "sold_portfolio_synthesis"}, {"_id": 0}, sort=[("built_at", -1)])
    out.mkdir(parents=True, exist_ok=True)
    (out / "deals").mkdir(exist_ok=True)

    rows = []
    for pid, d in deals.items():
        f = {k: v.get("value") for k, v in d["fields"].items()}
        pm = pms.get(pid)
        rows.append({"pid": pid, "address": d.get("address"), "state": d.get("state") or "", "section": d.get("section"),
                     "verdict": verdict_of(pm["answer"]) if pm else "na", **f, "has_pm": bool(pm)})
    rows.sort(key=lambda r: (r["investment_date"] or datetime.min))

    tot_inv = sum(r["cash_invested"] or 0 for r in rows); tot_col = sum(r["cash_collected"] or 0 for r in rows)
    tot_yield = sum(r["rkb_yield"] or 0 for r in rows); tot_short = sum(min(r["excess_shortage"] or 0, 0) for r in rows)
    n_good = sum(1 for r in rows if r["verdict"] == "good"); n_bad = sum(1 for r in rows if r["verdict"] == "bad"); n_mixed = sum(1 for r in rows if r["verdict"] == "mixed")
    wdays = sum((r["days_invested"] or 0) * (r["cash_invested"] or 0) for r in rows) / (tot_inv or 1)

    # ---------------------------------------------------------- index page
    h = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Deal Intelligence — Sold Properties</title><style>{CSS}</style></head><body><div class='wrap'>",
         "<div class='eyebrow'>RKB CONSULTING GROUP, INC. · DEAL INTELLIGENCE</div>",
         "<h1>The sold deals — what made money, what did not, and how to underwrite the next one</h1>",
         f"<div class='sub'>{len(rows)} completed deals · RKB's workbook figures plus a cited post-mortem per deal (Claude Fable 5.1) · built {datetime.now():%d %b %Y %H:%M}</div>",
         "<nav class='tabs'><a href='#s1'>1 · Portfolio</a><a href='#s2'>2 · Every deal</a><a href='#s3'>3 · Cross-deal analysis</a><a href='#s4'>4 · Deal pages</a></nav>",
         "<section id='s1'><h2>1 · The portfolio in numbers</h2><div class='kpis'>",
         f"<div class='kpi'><div class='v'>{money(tot_inv)}</div><div class='l'>Cash invested, {len(rows)} deals</div></div>",
         f"<div class='kpi'><div class='v'>{money(tot_col)}</div><div class='l'>Cash collected</div></div>",
         f"<div class='kpi'><div class='v {'green' if tot_yield>=0 else 'red'}'>{money(tot_yield)}</div><div class='l'>Yield billed (points + interest + fees)</div></div>",
         f"<div class='kpi'><div class='v red'>{money(tot_short)}</div><div class='l'>Shortages (sum of negatives)</div></div>",
         f"<div class='kpi'><div class='v'>{n_good} / {n_mixed} / {n_bad}</div><div class='l'>Good / mixed / bad (post-mortem verdicts) · avg {wdays:.0f} days (capital-weighted)</div></div>",
         "</div><p class='meta'>Figures are RKB's workbook 'LP Remodeling Sold Properties Summary.xlsx', one row per property; verdicts are from each deal's cited post-mortem. Yield is as billed; shortage is what was not collected.</p></section>",
         "<section id='s2'><h2>2 · Every deal</h2><p class='meta'>Click a column to sort. Click the address for the full post-mortem.</p>",
         "<table class='sortable'><thead><tr><th>Address</th><th>State</th><th>Invested</th><th>Received</th><th>Days</th><th>Cash in</th><th>Collected</th><th>Yield</th><th>Ann. ROI</th><th>Excess / (short)</th><th>Category</th><th>Verdict</th></tr></thead><tbody>"]
    for r in rows:
        v = r["verdict"]
        link = f"deals/{r['pid']}.html" if r["has_pm"] else ""
        addr = f"<a href='{link}'>{html.escape(r['address'])}</a>" if link else html.escape(r["address"])
        es = r["excess_shortage"]
        h.append(f"<tr class='{v if v in ('good','bad','mixed') else ''}'><td>{addr}</td><td>{r['state']}</td><td data-v='{dstr(r['investment_date'])}'>{dstr(r['investment_date'])}</td><td>{dstr(r['received_date'])}</td>"
                 f"<td class='n' data-v='{r['days_invested'] or 0}'>{r['days_invested'] or ''}</td><td class='n' data-v='{r['cash_invested'] or 0}'>{money(r['cash_invested'])}</td><td class='n' data-v='{r['cash_collected'] or 0}'>{money(r['cash_collected'])}</td>"
                 f"<td class='n' data-v='{r['rkb_yield'] or 0}'>{money(r['rkb_yield'])}</td><td class='n' data-v='{r['annualized_roi'] or 0}'>{pct(r['annualized_roi'])}</td>"
                 f"<td class='n' data-v='{es or 0}' style='color:{'#B4432B' if (es or 0) < -1 else 'inherit'}'>{money(es)}</td><td>{'LP sold, kept cash' if r['section']=='lp_sold_kept_cash' else 'paid from sale'}</td>"
                 f"<td><span class='tag {v}'>{v.upper() if v!='na' else 'NOT ANALYSED'}</span></td></tr>")
    h.append("</tbody></table></section>")
    h.append("<section id='s3' class='page'><h2>3 · What the deals say together</h2>")
    if syn:
        h.append(f"<p class='meta'>Written by {syn.get('model')} over all post-mortems and workbook rows · {syn.get('deals')} deals · {dstr(syn.get('built_at'))}</p><div class='md card'>{md(syn['answer'])}</div>")
    else:
        h.append("<p class='meta'>Synthesis not yet written.</p>")
    h.append("</section><section id='s4' class='page'><h2>4 · Deal pages</h2><div class='meta'>One page per deal: the deal in a paragraph, what happened in order, the money, what went wrong and when it was first visible, what went right, best 3 / worst 3, verdict, underwriting lessons, what the records cannot tell.</div>")
    for r in rows:
        if r["has_pm"]:
            h.append(f"<div class='card'><div class='deal-head'><b><a href='deals/{r['pid']}.html'>{html.escape(r['address'])}</a></b><span class='tag {r['verdict']}'>{r['verdict'].upper()}</span></div><div class='meta'>{dstr(r['investment_date'])} → {dstr(r['received_date'])} · {r['days_invested']} days · in {money(r['cash_invested'])} · collected {money(r['cash_collected'])} · {money(r['excess_shortage'])}</div></div>")
    h.append(f"</section><div class='foot'>MangoTree Deal Intelligence. Every post-mortem cites its documents by [#n]; the sources are listed on each deal page. Nothing here is a probability; groups under three deals are too few to judge.</div></div><script>{JS}</script></body></html>")
    (out / "index.html").write_text("\n".join(h), encoding="utf-8")

    # ---------------------------------------------------------- deal pages
    for r in rows:
        pm = pms.get(r["pid"])
        if not pm:
            continue
        src = pm.get("sources") or []
        srcs = "".join(f"<li>[#{i+1}] {html.escape(str(s.get('name') or s.get('sha256','')[:12]))} <span class='meta'>{dstr(s.get('date'))}</span></li>" for i, s in enumerate(src[:40]))
        p = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(r['address'])} — post-mortem</title><style>{CSS}</style></head><body><div class='wrap'>",
             "<div class='eyebrow'>RKB CONSULTING GROUP, INC. · DEAL INTELLIGENCE · POST-MORTEM</div>",
             f"<h1>{html.escape(r['address'])}</h1><div class='sub'>{html.escape(PROPERTY_INDEX[r['pid']].city)}, {PROPERTY_INDEX[r['pid']].state} · <span class='tag {r['verdict']}'>{r['verdict'].upper()}</span> · <a href='../index.html'>back to the portfolio</a></div>",
             "<div class='kpis'>",
             f"<div class='kpi'><div class='v'>{money(r['cash_invested'])}</div><div class='l'>Cash invested · {dstr(r['investment_date'])}</div></div>",
             f"<div class='kpi'><div class='v'>{money(r['cash_collected'])}</div><div class='l'>Collected · {dstr(r['received_date'])}</div></div>",
             f"<div class='kpi'><div class='v'>{r['days_invested'] or ''}</div><div class='l'>Days invested</div></div>",
             f"<div class='kpi'><div class='v'>{pct(r['annualized_roi'])}</div><div class='l'>Annualised ROI · yield {money(r['rkb_yield'])}</div></div>",
             f"<div class='kpi'><div class='v {'red' if (r['excess_shortage'] or 0) < -1 else 'green'}'>{money(r['excess_shortage'])}</div><div class='l'>Excess / (shortage)</div></div></div>",
             f"<div class='md'>{md(pm['answer'])}</div>",
             f"<details><summary>Sources read ({len(src)})</summary><ol>{srcs}</ol></details>",
             f"<div class='foot'>Post-mortem by {pm.get('model')} · {pm.get('budget',{}).get('tool_calls_used')} retrieval calls · ${pm.get('cost_usd')} · {dstr(pm.get('built_at'))}. Workbook figures from 'LP Remodeling Sold Properties Summary.xlsx'.</div>",
             "</div></body></html>"]
        (out / "deals" / f"{r['pid']}.html").write_text("\n".join(p), encoding="utf-8")

    # ---------------------------------------------------------- one combined PDF-ready file
    combined = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Deal Intelligence — full report</title><style>{CSS}</style></head><body><div class='wrap'>",
                (out / "index.html").read_text(encoding="utf-8").split("<div class='wrap'>", 1)[1].rsplit("<script>", 1)[0].replace("deals/", "#")]
    for r in rows:
        pm = pms.get(r["pid"])
        if pm:
            combined.append(f"<section class='page' id='{r['pid']}'><h1>{html.escape(r['address'])} <span class='tag {r['verdict']}'>{r['verdict'].upper()}</span></h1><div class='meta'>{dstr(r['investment_date'])} → {dstr(r['received_date'])} · {r['days_invested']} days · in {money(r['cash_invested'])} · collected {money(r['cash_collected'])} · excess/(short) {money(r['excess_shortage'])}</div><div class='md'>{md(pm['answer'])}</div></section>")
    combined.append("</div></body></html>")
    (out / "Deal Intelligence - full report.html").write_text("\n".join(combined), encoding="utf-8")
    print("written:", out)


def to_pdf(html_path: Path, pdf_path: Path) -> None:
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.as_uri()], capture_output=True, timeout=180)
    time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--pdf", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); build(out)
    if a.pdf:
        to_pdf(out / "Deal Intelligence - full report.html", out / "Deal Intelligence - full report.pdf")
        print("pdf:", out / "Deal Intelligence - full report.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
