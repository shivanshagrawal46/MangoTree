"""Check the Azure app registration before asking Rakesh to sign in.

Everything here is unauthenticated and read-only. The point is to catch a wrong
tenant id, a typo'd client id, or public-client-flows still switched off *now*,
rather than while a person is waiting on a code that expires in 15 minutes.

The device code call does start a real flow, but starting one and abandoning it
costs nothing — no token is issued unless someone completes it.
"""
from __future__ import annotations

import sys

import requests

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS


def main() -> None:
    tenant = SETTINGS.graph_tenant_id
    client = SETTINGS.graph_client_id
    mailbox = SETTINGS.graph_mailbox

    print("=== Graph pre-flight ===\n")
    print(f"  tenant  {tenant}")
    print(f"  client  {client}")
    print(f"  mailbox {mailbox}\n")

    if not SETTINGS.graph_configured:
        print("  NOT CONFIGURED — check GRAPH_* values in .env")
        return

    # 1. Does the tenant exist? A wrong tenant id authenticates fine later and
    # returns zero mailboxes, which looks exactly like a permissions bug.
    url = f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
    try:
        response = requests.get(url, timeout=20)
        if response.status_code == 200:
            issuer = response.json().get("issuer", "")
            print(f"  [OK]   tenant resolves\n         issuer {issuer}")
        else:
            print(f"  [FAIL] tenant lookup HTTP {response.status_code}")
            print(f"         {response.text[:200]}")
            return
    except Exception as exc:
        print(f"  [FAIL] tenant lookup error: {exc}")
        return

    # 2. Does the client id exist in that tenant, and are public client flows on?
    # Initiating a device flow answers both at once: a bad client id is rejected,
    # and a confidential-only app returns no user_code.
    try:
        import msal

        app = msal.PublicClientApplication(
            client, authority=f"https://login.microsoftonline.com/{tenant}"
        )
        flow = app.initiate_device_flow(scopes=["Mail.Read"])
        if "user_code" in flow:
            print("  [OK]   client id valid, public client flows enabled")
            print("  [OK]   Mail.Read scope accepted")
            print("\n  Everything is ready. Run:")
            print("      python -m mangotree.cli outlook-auth")
            print("\n  (The code just issued is deliberately discarded — the real")
            print("   one is printed when Rakesh is actually at his browser.)")
        else:
            error = flow.get("error", "")
            print(f"  [FAIL] device flow did not start: {error}")
            print(f"         {str(flow.get('error_description', ''))[:300]}")
            if error in ("unauthorized_client", "invalid_client"):
                print("\n  Most likely: 'Allow public client flows' is still No.")
                print("  Entra > App registrations > your app > Authentication >")
                print("  Advanced settings > Allow public client flows > Yes > Save.")
    except Exception as exc:
        print(f"  [FAIL] device flow error: {exc}")


if __name__ == "__main__":
    main()
