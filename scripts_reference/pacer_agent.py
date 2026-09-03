"""PACER / CM-ECF document download agent (visible-browser, human-assisted login).

Design goals:
  * NEVER stores your PACER password. You log in yourself in a visible browser.
    A *persistent* browser profile remembers the session so you only log in once.
  * Idempotent + resumable: already-downloaded documents are skipped.
  * Rate-limited: polite pause between documents (CM/ECF is a live paid system).
  * Staged: run with --list-only first (cheap — no per-document fees) to confirm
    we parsed the docket correctly, then --max N to test the download mechanism
    on a couple of docs, then a full run.

Flow:
  1. Launch a VISIBLE browser (your real Chrome if available) with a persistent
     profile dir.
  2. You log in to PACER and open the case DOCKET / HISTORY page (the one that
     lists every entry). The agent waits and auto-detects that page.
  3. The agent scrapes every hyperlinked document entry, then for each one opens
     it, accepts the CM/ECF receipt ("View Document"), captures the PDF, and
     saves it to the output folder named by docket entry.
  4. Saves the docket HTML + a manifest CSV.

Usage (from repo root):
  python scripts/pacer_agent.py --list-only
  python scripts/pacer_agent.py --max 2
  python scripts/pacer_agent.py            # full run

Nothing here is specific to one court; it keys off standard CM/ECF `doc1` links.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUT_DEFAULT = r"E:\PACER\IPA_asset_8-2025bk72526"
PROFILE_DEFAULT = r"E:\PACER\.browser_profile"
CREDS_DEFAULT = r"E:\PACER\.pacer_creds.json"
CASE_HINT = "72526"          # substring that must appear on the docket page
START_URL = "https://pacer.uscourts.gov/"
LOGIN_URL = "https://pacer.login.uscourts.gov/csologin/login.jsf"
# Court + internal case id, used to open the FULL docket automatically.
COURT_HOST = "https://ecf.nyeb.uscourts.gov"
CASE_ID = "529191"           # 8:25-bk-72526-SC internal caseid

# CM/ECF document links look like  https://ecf.<court>.uscourts.gov/doc1/<digits>
DOC1_RE = re.compile(r"/doc1/\d+", re.I)


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    try:
        print(f"[{stamp}] {msg}", flush=True)
    except Exception:
        print(f"[{stamp}] {msg.encode('ascii', 'replace').decode('ascii')}", flush=True)


def _safe(text: str, n: int = 60) -> str:
    """Filesystem-safe short slug from a docket description."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r"[^A-Za-z0-9 _.\-]", "", text)
    text = text.strip().replace(" ", "-")
    return text[:n].strip("-") or "doc"


def try_auto_login(page, creds_path: str) -> str:
    """If a creds file exists and we're on the PACER login form, fill + submit.
    Never hard-fails — returns a status string; on any problem we fall back to
    manual login by the user in the visible window."""
    cf = Path(creds_path)
    if not cf.exists():
        return "no creds file — manual login"
    try:
        creds = json.loads(cf.read_text(encoding="utf-8"))
        user, pw = creds.get("username"), creds.get("password")
        if not user or not pw:
            return "creds file incomplete — manual login"
    except Exception as exc:  # noqa: BLE001
        return f"creds unreadable ({exc}) — manual login"

    try:
        page.goto(LOGIN_URL, wait_until="load", timeout=45000)
    except Exception:
        pass
    time.sleep(1.5)

    # If there's no username field, we're probably already logged in (persistent profile).
    user_sel = [
        '#loginForm\\:loginName', 'input[name="loginForm:loginName"]',
        'input[name*="loginName" i]', 'input[id*="loginName" i]',
        'input[name="javax.faces.partial.ajax"] ~ input[type="text"]',
    ]
    pass_sel = [
        '#loginForm\\:password', 'input[name="loginForm:password"]',
        'input[type="password"]',
    ]
    u = next((page.query_selector(s) for s in user_sel if page.query_selector(s)), None)
    if not u:
        return "already logged in (no login form)"
    p = next((page.query_selector(s) for s in pass_sel if page.query_selector(s)), None)
    if not p:
        return "login form found but no password field — manual login"

    try:
        u.fill(user)
        p.fill(pw)
        btn = None
        for s in ['#loginForm\\:fbtnLogin', 'input[name="loginForm:fbtnLogin"]',
                  'input[type="submit"][value*="Login" i]', 'button:has-text("Login")',
                  'input[type="submit"]']:
            btn = page.query_selector(s)
            if btn:
                break
        if btn:
            btn.click()
        else:
            p.press("Enter")
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(1.5)
        if page.query_selector('input[type="password"]'):
            return "submitted but still on login page — check for error/CAPTCHA, finish manually"
        return "auto-login submitted"
    except Exception as exc:  # noqa: BLE001
        return f"auto-login error ({exc}) — finish manually"


def _page_has_docs(page) -> bool:
    try:
        return DOC1_RE.search(page.content()) is not None
    except Exception:
        return False


def _count_docs(page) -> int:
    try:
        return len(DOC1_RE.findall(page.content()))
    except Exception:
        return 0


def auto_open_docket(context, page, timeout_s: int = 90):
    """Navigate to the FULL docket ourselves so we capture the ENTIRE case
    (every entry, in one shot) instead of relying on whatever page is open.
    Tries the History-of-Documents query and the Docket Report; runs the query
    with all dates so nothing is filtered out. Returns the results page or None."""
    # ONLY the History-of-Documents query: it labels each link with the real
    # docket number (matching existing files) and merges multi-part filings.
    # The Docket Report is intentionally NOT used (it numbers links 1,2,3...).
    queries = [
        f"{COURT_HOST}/cgi-bin/HistDocQry.pl?{CASE_ID}",
    ]
    best = None
    best_n = 0
    dbg = Path(OUT_DEFAULT) / "_docket"
    try:
        dbg.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    for qi, url in enumerate(queries):
        try:
            log(f"[auto] opening {url}")
            page.goto(url, wait_until="load", timeout=60000)
        except Exception:
            continue
        # Wait until the page settles into a doc listing OR a query form with a
        # submit button (allow PACER SSO redirects to complete).
        for _ in range(45):
            try:
                if _page_has_docs(page) or page.query_selector('input[type="submit"], button[type="submit"]'):
                    break
            except Exception:
                pass
            time.sleep(1)
        try:
            (dbg / f"_auto_query_{qi}.html").write_text(
                page.content(), encoding="utf-8", errors="ignore")
        except Exception:
            pass

        # If it's already a doc listing, take it.
        if _page_has_docs(page):
            n = _count_docs(page)
            log(f"[auto] {url} is already a listing -> {n} document links")
            if n > best_n:
                best, best_n = page.url, n
            if best_n >= 100:
                break
            continue

        # It's the History/Documents query form. Choose "Only events with
        # documents" + "Oldest date first" (so we download in sequence 1..N).
        try:
            r = page.query_selector('input[type="radio"][name="QueryType"][value="Documents" i]') \
                or page.query_selector('input[type="radio"][value="Documents" i]')
            if r:
                r.check()
        except Exception:
            pass
        try:
            if page.query_selector('select[name="sort1"]'):
                page.select_option('select[name="sort1"]', "asc")
        except Exception:
            pass
        # Clear any date fields (some query forms have them).
        try:
            page.eval_on_selector_all(
                'input[type="text"]',
                """els => els.forEach(e => {
                     const n = (e.name||'') + (e.id||'');
                     if (/date|from|to|beg|end/i.test(n)) e.value = '';
                })""")
        except Exception:
            pass
        # The Run Query button is type="button" (JS-submits the form), so match
        # by value/name too, not just type=submit.
        clicked = False
        for sel in ('input[value*="Run Query" i]', 'input[name="button1"]',
                    'input[value*="Run Report" i]', 'input[value*="Run" i]',
                    'input[type="submit"]', 'button[type="submit"]'):
            btn = page.query_selector(sel)
            if btn:
                try:
                    btn.click()
                    clicked = True
                    break
                except Exception:
                    pass
        if not clicked:
            continue
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if _page_has_docs(page):
                break
            time.sleep(2)
        n = _count_docs(page)
        log(f"[auto] {url} -> {n} document links")
        if n > best_n:
            best, best_n = page.url, n
        # If we got a healthy full docket, stop early.
        if best_n >= 100:
            break

    if best:
        # Make sure the winning page is the one currently loaded.
        try:
            if page.url != best:
                page.goto(best, wait_until="load", timeout=60000)
                for _ in range(40):
                    if _page_has_docs(page):
                        break
                    time.sleep(1)
        except Exception:
            pass
        log(f"[auto] using docket page with {_count_docs(page)} document links: {page.url}")
        return page
    return None


def wait_for_docket(context, timeout_s: int = 1800, case_hint: str = CASE_HINT):
    """Poll open pages until one looks like the target docket/history page
    (contains the case hint AND has CM/ECF doc1 links). Returns that page."""
    log("Waiting for you to open the case's HISTORY/DOCUMENTS query results "
        "(Reports > History/Documents > 'Documents only' > Run Query). "
        "NOTE: do NOT use the plain Docket Report — its links are misnumbered. "
        f"Looking for case hint '{case_hint}'. Timeout {timeout_s//60} min.")
    deadline = time.time() + timeout_s
    warned_dktrpt = False
    while time.time() < deadline:
        for page in list(context.pages):
            try:
                url = page.url
                html = page.content()
            except Exception:
                continue
            if (case_hint and case_hint not in html) or not DOC1_RE.search(html):
                continue
            if "histdocqry" in url.lower():
                log(f"Detected History/Documents page: {url}")
                return page
            if "dktrpt" in url.lower():
                if not warned_dktrpt:
                    log("[warn] A Docket Report page is open, but its links are "
                        "misnumbered. Please open History/Documents instead.")
                    warned_dktrpt = True
                continue
            # Any other page that lists doc links is acceptable too.
            log(f"Detected docket page: {url}")
            return page
        time.sleep(2)
    raise TimeoutError("Timed out waiting for the docket page.")


def scrape_entries(page) -> List[Dict[str, Any]]:
    """Extract every document link on the docket/history page.
    Returns list of {doc_no, href, desc, date} in page order, de-duplicated."""
    anchors = page.eval_on_selector_all(
        "a",
        """els => els.map(a => ({
              text: (a.textContent||'').trim(),
              href: a.href,
              row:  (a.closest('tr') ? a.closest('tr').innerText : '').replace(/\\s+/g,' ').trim()
        }))""",
    )
    entries: List[Dict[str, Any]] = []
    seen = set()
    for a in anchors:
        href = a.get("href") or ""
        if not DOC1_RE.search(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        row = a.get("row") or ""
        m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", row)
        date = m.group(1) if m else ""
        doc_no = (a.get("text") or "").strip()
        entries.append({
            "doc_no": doc_no,
            "href": href,
            "desc": row,
            "date": date,
        })
    return entries


def already_have(out_docs: Path, doc_no: str) -> bool:
    if not doc_no:
        return False
    prefix = f"{int(doc_no):04d}_" if doc_no.isdigit() else f"{doc_no}_"
    return any(out_docs.glob(prefix + "*"))


def dest_name(entry: Dict[str, Any]) -> str:
    doc_no = entry["doc_no"]
    seq = f"{int(doc_no):04d}" if doc_no.isdigit() else _safe(doc_no, 12)
    date = (entry.get("date") or "").replace("/", "-")
    return f"{seq}_{date}_{_safe(entry.get('desc',''))}.pdf"


def _is_pdf(data: Optional[bytes]) -> bool:
    return bool(data) and data[:5].startswith(b"%PDF")


def _save_if_pdf(data: Optional[bytes], dest: Path) -> Optional[str]:
    if _is_pdf(data):
        dest.write_bytes(data)
        return f"OK ({len(data):,}B, {dest.name})"
    return None


# goDLS(url, caseid, de_seq_num, got_receipt, pdf_header, pdf_toggle_possible,
#       magic_num, claim_id, claim_num, claim_doc_seq)  -- from the court's core.js.
GODLS_RE = re.compile(r"goDLS\(([^)]*)\)")
GODLS_KEYS = ["caseid", "de_seq_num", "got_receipt", "pdf_header",
              "pdf_toggle_possible", "magic_num", "claim_id", "claim_num",
              "claim_doc_seq"]


def _godls_post_data(onsubmit: str) -> Optional[Dict[str, str]]:
    """Parse a goDLS(...) call into the POST body its hidden form would submit."""
    m = GODLS_RE.search(onsubmit or "")
    if not m:
        return None
    args = [a.strip().strip("'\"") for a in m.group(1).split(",")]
    data = {k: v for k, v in zip(GODLS_KEYS, args[1:]) if v}
    data["got_receipt"] = "1"
    return data


_INPAGE_GET = """
async (u) => {
    const r = await fetch(u, {credentials: 'include'});
    const buf = new Uint8Array(await r.arrayBuffer());
    let bin = ''; const chunk = 0x8000;
    for (let i = 0; i < buf.length; i += chunk) {
        bin += String.fromCharCode.apply(null, buf.subarray(i, i + chunk));
    }
    return {ct: (r.headers.get('content-type') || ''), b64: btoa(bin)};
}
"""


def _is_menu(html) -> bool:
    s = html.decode("utf-8", "ignore") if isinstance(html, (bytes, bytearray)) else (html or "")
    return ('id="view_button"' in s) or ("Document Selection Menu" in s)


def _wait_ready(page, timeout_s: int = 90) -> bool:
    """Wait for PACER SSO to settle and the receipt/menu button to render."""
    for _ in range(timeout_s):
        try:
            if (page.query_selector("#view_button")
                    or page.query_selector('input[value*="View Document" i]')):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _grab_pdf_on_click(page, selector: str, timeout: int = 120000) -> Optional[bytes]:
    """Click the given button (View Document / View Selected). CM/ECF returns a
    wrapper page that loads the real PDF from a show_temp.pl URL. Capture that
    application/pdf response and return its validated bytes."""
    try:
        with page.expect_response(
            lambda r: "application/pdf" in (r.headers.get("content-type", "") or "").lower(),
            timeout=timeout,
        ) as ri:
            page.eval_on_selector(selector, "el => el.click()")
        resp = ri.value
    except Exception:
        return None
    # Try the response body directly.
    try:
        data = resp.body()
        if _is_pdf(data):
            return data
    except Exception:
        pass
    # Fallback: re-fetch the temp PDF URL in-page (same session cookies).
    try:
        res = page.evaluate(_INPAGE_GET, resp.url)
        data = base64.b64decode(res.get("b64", ""))
        if _is_pdf(data):
            return data
    except Exception:
        pass
    return None


def download_one(context, entry: Dict[str, Any], dest: Path, delay: float) -> str:
    """Open a CM/ECF doc1 link and capture the real PDF -> dest by clicking the
    document's own 'View Document' (single) or 'View Selected' (multi-part,
    merged PDF of Main Document + all attachments) button, exactly like a human.
    Every file is validated to start with %PDF; failures save page HTML."""
    doc_page = context.new_page()
    try:
        resp = doc_page.goto(entry["href"], wait_until="load", timeout=60000)

        # doc1 returned the PDF directly (rare).
        if resp and "application/pdf" in (resp.headers.get("content-type", "") or "").lower():
            s = _save_if_pdf(resp.body(), dest)
            if s:
                return "OK direct " + s

        _wait_ready(doc_page)
        html = ""
        try:
            html = doc_page.content()
        except Exception:
            pass

        # Multi-part "Document Selection Menu": View Selected -> one merged PDF.
        if doc_page.query_selector("#view_button"):
            data = _grab_pdf_on_click(doc_page, "#view_button")
            s = _save_if_pdf(data, dest)
            if s:
                return "OK multi " + s

        # Single-document receipt: View Document.
        if doc_page.query_selector('input[value*="View Document" i]'):
            data = _grab_pdf_on_click(doc_page, 'input[value*="View Document" i]')
            s = _save_if_pdf(data, dest)
            if s:
                return "OK single " + s

        dbg = dest.parent / "_needs_manual"; dbg.mkdir(exist_ok=True)
        (dbg / (dest.stem + ".html")).write_text(html, encoding="utf-8", errors="ignore")
        return "MANUAL (no PDF captured — saved page HTML)"
    finally:
        time.sleep(delay)
        try:
            doc_page.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--profile-dir", default=PROFILE_DEFAULT)
    ap.add_argument("--creds", default=CREDS_DEFAULT,
                    help="JSON file with {username, password} (kept off git, on E:).")
    ap.add_argument("--list-only", action="store_true",
                    help="Parse the docket + write manifest; download NOTHING (no fees).")
    ap.add_argument("--max", type=int, default=0, help="Max documents to download (0 = all).")
    ap.add_argument("--only", default="", help="Comma-separated doc numbers to download (testing).")
    ap.add_argument("--delay", type=float, default=3.0, help="Seconds between documents.")
    ap.add_argument("--manual", action="store_true",
                    help="Skip auto-opening the docket; wait for you to open it.")
    ap.add_argument("--case-hint", default=CASE_HINT,
                    help="Substring that must appear on the docket page to identify "
                         "the right case (e.g. the case-number digits). Empty = accept "
                         "any page with doc links.")
    args = ap.parse_args()

    out = Path(args.out)
    out_docs = out / "documents"
    out_docket = out / "_docket"
    for d in (out_docs, out_docket, Path(args.profile_dir)):
        d.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        launch_kwargs = dict(headless=False, accept_downloads=True,
                             args=["--start-maximized"], no_viewport=True)
        try:
            context = p.chromium.launch_persistent_context(
                args.profile_dir, channel="chrome", **launch_kwargs)
            log("Launched your installed Chrome (persistent profile).")
        except Exception:
            context = p.chromium.launch_persistent_context(args.profile_dir, **launch_kwargs)
            log("Launched bundled Chromium (persistent profile).")

        page = context.pages[0] if context.pages else context.new_page()
        login_status = try_auto_login(page, args.creds)
        log(f"Login: {login_status}")

        docket = None
        if not args.manual:
            try:
                docket = auto_open_docket(context, page)
            except Exception as exc:  # noqa: BLE001
                log(f"[auto] docket auto-open failed ({exc}); falling back to manual.")
        if docket is None:
            docket = wait_for_docket(context, case_hint=args.case_hint)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        (out_docket / f"docket_{stamp}.html").write_text(
            docket.content(), encoding="utf-8", errors="ignore")

        entries = scrape_entries(docket)
        # Download in sequence: oldest/lowest doc number first (1 -> N).
        entries.sort(key=lambda e: int(e["doc_no"]) if e["doc_no"].isdigit() else 10 ** 9)
        log(f"Found {len(entries)} document links on the docket.")

        manifest = out / "manifest.csv"
        with manifest.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["doc_no", "date", "description", "href", "dest_file", "status"])
            done = 0
            only_set = {x.strip() for x in args.only.split(",") if x.strip()}
            for e in entries:
                if only_set and e["doc_no"] not in only_set:
                    continue
                dfile = dest_name(e)
                dpath = out_docs / dfile
                if args.list_only:
                    status = "LISTED"
                elif already_have(out_docs, e["doc_no"]):
                    status = "SKIP (already downloaded)"
                elif args.max and done >= args.max:
                    status = "SKIP (max reached)"
                else:
                    log(f">> doc {e['doc_no']} {e['date']} - {e['desc'][:70]}")
                    status = download_one(context, e, dpath, args.delay)
                    log(f"   {status}")
                    if status.startswith("OK"):
                        done += 1
                w.writerow([e["doc_no"], e["date"], e["desc"], e["href"], dfile, status])

        log(f"Manifest written: {manifest}")
        if args.list_only:
            log("LIST-ONLY complete — no documents downloaded, no fees incurred.")
        else:
            log(f"Downloaded {done} document(s) this run into {out_docs}")

        log("Leaving the browser open. Close it when you're done, or re-run to resume.")
        try:
            input("Press Enter here to close the browser and exit...")
        except Exception:
            time.sleep(5)
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
