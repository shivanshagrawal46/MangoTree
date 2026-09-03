"""Decode the access token's claims to see what it can actually do.

A JWT access token is signed, not encrypted, so the claims are readable locally
without contacting Microsoft. `scp` is the decisive field: it lists the delegated
scopes actually granted. Config screens describe intent; the token describes
reality, and when the two disagree the token wins.

The token itself is never printed — only its claims.
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.ingest.graph_auth import GraphDelegatedAuth


def decode_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def main() -> None:
    auth = GraphDelegatedAuth(
        tenant_id=SETTINGS.graph_tenant_id,
        client_id=SETTINGS.graph_client_id,
        mailbox=SETTINGS.graph_mailbox,
        cache_path=SETTINGS.graph_token_cache,
    )
    token = auth.access_token()

    parts = token.split(".")
    if len(parts) < 2:
        print("token is not a JWT")
        return

    claims = decode_segment(parts[1])

    print("=== access token claims ===\n")
    for key in ("aud", "iss", "upn", "unique_name", "preferred_username",
                "app_displayname", "appid", "tid", "ver"):
        if key in claims:
            print(f"  {key:<20}{claims[key]}")

    exp = claims.get("exp")
    if exp:
        when = datetime.fromtimestamp(exp, tz=timezone.utc)
        left = (when - datetime.now(timezone.utc)).total_seconds() / 60
        print(f"  {'expires':<20}{when:%Y-%m-%d %H:%M UTC}  ({left:.0f} min left)")

    scopes = (claims.get("scp") or "").split()
    print(f"\n  delegated scopes ({len(scopes)}):")
    for scope in sorted(scopes):
        print(f"    {scope}")

    roles = claims.get("roles") or []
    if roles:
        print(f"\n  APPLICATION roles ({len(roles)}) — these would be tenant-wide:")
        for role in roles:
            print(f"    {role}")
    else:
        print("\n  application roles: none (correct — this is a delegated token)")

    print("\n=== verdict ===")
    if "Mail.Read" in scopes:
        print("  Mail.Read IS present. The permission is not the problem;")
        print("  the 504s are Exchange failing to serve the mailbox.")
    else:
        print("  Mail.Read is MISSING from the token. That is the bug.")
        print(f"  The token only carries: {scopes}")
        print("  Graph accepted the request and then could not route it,")
        print("  which is why it looked like a timeout rather than a denial.")

    # Graph accepts two spellings of its own audience: the URL in v2 tokens and
    # this well-known app id in v1 tokens. Treating the GUID as wrong produces a
    # scary warning on a perfectly valid token.
    GRAPH_AUDIENCES = {
        "https://graph.microsoft.com",
        "00000003-0000-0000-c000-000000000000",
    }
    aud = str(claims.get("aud", "")).rstrip("/")
    if aud not in GRAPH_AUDIENCES:
        print(f"\n  WARNING: audience is {aud!r}, not Microsoft Graph.")


if __name__ == "__main__":
    main()
