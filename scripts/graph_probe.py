"""Find out which Graph endpoints actually work with this delegated token.

`/users/{upn}/...` returned 504 five times running. That reads like a Microsoft
outage, but the far more likely explanation is the endpoint choice: under a
delegated token, `/me` is the supported path for the signed-in user, while
`/users/{upn}` asks Graph to resolve and proxy to a directory object and is
known to be slower and flakier even when the caller is that same user.

This times each candidate endpoint separately so the difference is visible
rather than assumed. One slow call proves nothing; `/me` fast and `/users/{upn}`
timing out proves the routing is the problem.
"""
from __future__ import annotations

import sys
import time
from urllib.parse import quote

import requests

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.ingest.graph_auth import GraphDelegatedAuth

GRAPH = "https://graph.microsoft.com/v1.0"


def probe(session, token, label, url, params=None, timeout=45):
    started = time.time()
    try:
        response = session.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
            timeout=timeout,
        )
        elapsed = time.time() - started
        note = ""
        if response.status_code == 200:
            try:
                body = response.json()
                if "value" in body:
                    note = f"{len(body['value'])} item(s)"
                else:
                    note = str(
                        body.get("userPrincipalName")
                        or body.get("displayName")
                        or ""
                    )[:48]
            except Exception:
                note = f"{len(response.content)} bytes"
        else:
            note = response.text[:110].replace("\n", " ")
        print(f"  {label:<44}{response.status_code:>5}  {elapsed:>6.1f}s  {note}")
        return response.status_code
    except requests.exceptions.Timeout:
        print(f"  {label:<44}{'TMO':>5}  {time.time()-started:>6.1f}s  client timeout")
        return None
    except Exception as exc:
        print(f"  {label:<44}{'ERR':>5}  {time.time()-started:>6.1f}s  {str(exc)[:60]}")
        return None


def main() -> None:
    auth = GraphDelegatedAuth(
        tenant_id=SETTINGS.graph_tenant_id,
        client_id=SETTINGS.graph_client_id,
        mailbox=SETTINGS.graph_mailbox,
        cache_path=SETTINGS.graph_token_cache,
    )
    token = auth.access_token()
    mailbox = SETTINGS.graph_mailbox
    session = requests.Session()

    print(f"\nsigned in as: {auth.signed_in_account()}")
    print(f"{'endpoint':<46}{'code':>5}  {'time':>7}  detail")
    print("-" * 92)

    probe(session, token, "/me", f"{GRAPH}/me")
    probe(session, token, "/me/mailFolders/inbox",
          f"{GRAPH}/me/mailFolders/inbox")
    probe(session, token, "/me/mailFolders/inbox/messages",
          f"{GRAPH}/me/mailFolders/inbox/messages",
          params={"$top": "1", "$select": "id,subject"})
    probe(session, token, "/me/mailFolders/sentitems/messages",
          f"{GRAPH}/me/mailFolders/sentitems/messages",
          params={"$top": "1", "$select": "id,subject"})

    print()
    probe(session, token, f"/users/{{self}}",
          f"{GRAPH}/users/{quote(mailbox)}")
    probe(session, token, "/users/{self}/mailFolders/inbox/messages",
          f"{GRAPH}/users/{quote(mailbox)}/mailFolders/inbox/messages",
          params={"$top": "1", "$select": "id,subject"})

    print("\n  negative control (must NOT be readable):")
    probe(session, token, "/users/jp@mtreh.com/.../messages",
          f"{GRAPH}/users/{quote('jp@mtreh.com')}/mailFolders/inbox/messages",
          params={"$top": "1", "$select": "id"})

    print("\n  mailbox size (for the backfill estimate):")
    for folder in ("inbox", "sentitems"):
        probe(session, token, f"/me/mailFolders/{folder} counts",
              f"{GRAPH}/me/mailFolders/{folder}")


if __name__ == "__main__":
    main()
