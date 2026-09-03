"""One-time Gmail authorization — link-based ("CEO clicks a link, runs nothing").

Use this when the mailbox owner (e.g. the CEO) cannot run any command and is not
sitting at this machine. YOU run this script; it prints an authorization LINK
you send to the owner. The owner opens the link, signs into HIS Gmail, and
approves read-only access. Google then shows a page with an authorization
CODE (and/or redirects to a localhost URL that contains `code=...`). The owner
copies that code (or the whole redirected URL) and sends it back to you; you
paste it here, and the script saves `gmail_token.json`.

That token then works permanently (the app must be in *Production* publishing
status so the refresh token does not expire after 7 days).

Flow type: manual loopback code exchange. Same Desktop-app `client_secret.json`
as the rest of the pipeline — no web server / hosting needed.

Usage:
    python -m scripts.gmail_authorize
    # (optional) custom paths:
    python -m scripts.gmail_authorize --client-secret client_secret.json --token gmail_token.json
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingest.gmail_client import GMAIL_SCOPES, DEFAULT_CLIENT_SECRET, DEFAULT_TOKEN_PATH

# Loopback redirect — for a Desktop-app client this is accepted, and because we
# are NOT running a local server the browser will simply fail to load the page
# AFTER approval; the authorization code is in that page's URL for copy-back.
_REDIRECT = "http://localhost"


def _extract_code(pasted: str) -> str:
    """Accept either a raw code or the full redirected localhost URL."""
    pasted = (pasted or "").strip()
    if pasted.startswith("http://") or pasted.startswith("https://"):
        qs = parse_qs(urlparse(pasted).query)
        code = (qs.get("code") or [""])[0]
        return code
    # sometimes the owner pastes "code=XXXX" or "?code=XXXX"
    if "code=" in pasted:
        return parse_qs(pasted.split("?")[-1]).get("code", [pasted])[0]
    return pasted


def main() -> int:
    ap = argparse.ArgumentParser(description="Link-based one-time Gmail authorization.")
    ap.add_argument("--client-secret", default=DEFAULT_CLIENT_SECRET)
    ap.add_argument("--token", default=DEFAULT_TOKEN_PATH)
    args = ap.parse_args()

    if not os.path.exists(args.client_secret):
        print(f"ERROR: client secret not found at '{args.client_secret}'.")
        return 2

    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        print("ERROR: install deps first:\n"
              "    python -m pip install google-api-python-client google-auth "
              "google-auth-oauthlib")
        return 2

    flow = Flow.from_client_secrets_file(
        args.client_secret, scopes=GMAIL_SCOPES, redirect_uri=_REDIRECT)
    # offline + consent => we receive a long-lived refresh token.
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent")

    # Also write the link to a file so it can be sent WITHOUT being broken /
    # truncated by chat apps (a split URL is the usual cause of Google's
    # "400: malformed request").
    link_file = Path("gmail_auth_link.txt")
    try:
        link_file.write_text(auth_url, encoding="utf-8")
    except Exception:  # noqa: BLE001
        link_file = None

    print("\n" + "=" * 74)
    print("STEP 1 — send this LINK to the mailbox owner (the CEO):")
    print("=" * 74)
    print(auth_url)
    print("=" * 74)
    if link_file:
        print(f"\n(The link is also saved to '{link_file.resolve()}'.")
        print(" Send it as ONE unbroken line — attach that .txt file, or use a")
        print(" URL shortener. A link split across lines causes Google '400:")
        print(" malformed request'.)")
    print(
        "\nWhat the owner does (no commands, just a browser):\n"
        "  1. Open the link, sign in with HIS Gmail.\n"
        "  2. If shown 'Google hasn't verified this app' -> Advanced -> Go to ... -> Allow.\n"
        "  3. Approve the read-only access.\n"
        "  4. The browser will then try to open 'localhost' and show a\n"
        "     'this site can't be reached' page -- THAT IS EXPECTED.\n"
        "  5. He copies the FULL ADDRESS-BAR URL of that page (it contains\n"
        "     'code=...') and sends it back to you. (Or just the code value.)\n"
    )
    pasted = input("STEP 2 — paste the URL or code the owner sent you, then Enter:\n> ")
    code = _extract_code(pasted)
    if not code:
        print("ERROR: could not find an authorization code in what you pasted.")
        return 2

    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR exchanging the code: {exc}\n"
              "Common causes: the code was already used (each is single-use — "
              "re-run and get a fresh link), or it was truncated when copied.")
        return 2

    creds = flow.credentials
    with open(args.token, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())

    has_refresh = bool(getattr(creds, "refresh_token", None))
    print("\n" + "=" * 74)
    print(f"SUCCESS — token saved to '{args.token}'.")
    print(f"Refresh token present (permanent access): {has_refresh}")
    if not has_refresh:
        print("WARNING: no refresh token returned. Make sure the app is in "
              "PRODUCTION and re-run (the owner may need to revoke prior access "
              "at myaccount.google.com/permissions, then re-approve).")
    print("=" * 74)
    print("\nVerify it works:\n    python -m scripts.ingest_gmail profile")
    print("Then list folders:\n    python -m scripts.ingest_gmail labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
