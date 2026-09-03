"""One-off PACER probe: reuse the logged-in persistent profile, open doc 136's
receipt directly, and dump exactly what (A) an in-page fetch POST and (B) a real
'View Document' click return. No user interaction needed if the session cookie
is still valid."""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROFILE = r"E:\PACER\.browser_profile"
RECEIPT_URL = "https://ecf.nyeb.uscourts.gov/doc1/122031754976"  # doc 136
OUT = Path(r"E:\PACER\_probe")
OUT.mkdir(parents=True, exist_ok=True)

INPAGE_FETCH = """
async ([action, data]) => {
    const body = new URLSearchParams(data).toString();
    const r = await fetch(action, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: body, credentials: 'include'
    });
    const buf = new Uint8Array(await r.arrayBuffer());
    let bin = ''; const chunk = 0x8000;
    for (let i = 0; i < buf.length; i += chunk) {
        bin += String.fromCharCode.apply(null, buf.subarray(i, i + chunk));
    }
    return {status: r.status, url: r.url, ct: (r.headers.get('content-type') || ''), b64: btoa(bin)};
}
"""


def head(b: bytes, n=24):
    return b[:n]


def main():
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, channel="chrome", headless=False, accept_downloads=True)
        except Exception:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=False, accept_downloads=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        resp = page.goto(RECEIPT_URL, wait_until="load", timeout=60000)
        print("goto url:", page.url)
        # Let PACER SSO settle and the receipt render (poll up to 60s).
        html = ""
        for _ in range(60):
            try:
                html = page.content()
            except Exception:
                time.sleep(1)
                continue
            if "goDLS" in html:
                break
            time.sleep(1)
        print("landed url:", page.url)
        (OUT / "receipt.html").write_text(html, encoding="utf-8", errors="ignore")
        print("has receipt form:", "goDLS" in html, "| is_login:", "login.jsf" in page.url.lower())

        frm = page.query_selector("form")
        action = frm.get_attribute("action") if frm else None
        onsubmit = frm.get_attribute("onsubmit") if frm else None
        print("form action:", action)
        print("form onsubmit:", onsubmit)

        # ---- Attempt A: in-page fetch POST ----
        data = {"caseid": "529191", "got_receipt": "1"}
        try:
            res = page.evaluate(INPAGE_FETCH, [action, data])
            raw = base64.b64decode(res.get("b64", ""))
            print("\n[A] fetch status:", res.get("status"), "final url:", res.get("url"),
                  "ct:", res.get("ct"), "len:", len(raw), "head:", head(raw))
            (OUT / "A_fetch.bin").write_bytes(raw)
        except Exception as e:
            print("[A] fetch error:", e)

        # ---- Attempt B: real click, capture PDF response / new page / download ----
        page.goto(RECEIPT_URL, wait_until="load", timeout=60000)
        for _ in range(60):
            try:
                if page.query_selector('input[value="View Document"]'):
                    break
            except Exception:
                pass
            time.sleep(1)
        captured = {}

        def on_resp(r):
            try:
                ct = (r.headers.get("content-type") or "").lower()
                print("   [resp]", r.request.method, r.status, ct[:40], r.url[:90])
                if "application/pdf" in ct:
                    captured["ct"] = ct
                    captured["url"] = r.url
            except Exception:
                pass

        def on_page(pg):
            print("   [new page]", pg.url)

        def on_download(d):
            print("   [download]", d.suggested_filename)
            captured["dl"] = d

        ctx.on("response", on_resp)
        ctx.on("page", on_page)
        ctx.on("download", on_download)

        print("\n[B] clicking View Document ...")
        try:
            page.eval_on_selector('input[value="View Document"]', "el => el.click()")
        except Exception as e:
            print("[B] click error:", e)
        page.wait_for_timeout(9000)
        print("[B] after click, page url:", page.url)
        # Save the wrapper HTML that the POST produced + find the show_temp URL.
        try:
            wrapper = page.content()
            (OUT / "wrapper.html").write_text(wrapper, encoding="utf-8", errors="ignore")
            import re as _re
            hits = _re.findall(r"[^\"'\s<>]*show_temp\.pl\?[^\"'\s<>]+", wrapper)
            print("[B] wrapper len:", len(wrapper), "show_temp hits:", hits[:3])
            srcs = _re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', wrapper)
            print("[B] wrapper srcs:", [s for s in srcs if "temp" in s or ".pdf" in s or "doc" in s][:5])
        except Exception as e:
            print("[B] wrapper read error:", e)
        print("[B] open pages:", [pg.url[:90] for pg in ctx.pages])
        print("[B] captured:", {k: (v if k != 'dl' else 'download') for k, v in captured.items()})
        if "dl" in captured:
            dst = OUT / "B_download.pdf"
            captured["dl"].save_as(str(dst))
            b = dst.read_bytes()
            print("[B] download head:", head(b), "len:", len(b))

        # If a new tab holds the PDF, try to read it via fetch there.
        for pg in ctx.pages:
            if "/doc1/" in pg.url and pg.url != RECEIPT_URL:
                try:
                    res = pg.evaluate(
                        """async (u) => { const r = await fetch(u, {credentials:'include'});
                           const b = new Uint8Array(await r.arrayBuffer());
                           let s=''; for (let i=0;i<Math.min(b.length,16);i++) s+=String.fromCharCode(b[i]);
                           return {ct:r.headers.get('content-type'), len:b.length, head:s}; }""",
                        pg.url)
                    print("[B] newtab refetch:", res)
                except Exception as e:
                    print("[B] newtab refetch error:", e)

        print("\nDone. Artifacts in", OUT)
        time.sleep(2)
        ctx.close()


if __name__ == "__main__":
    main()
