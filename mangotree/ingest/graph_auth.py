"""Delegated Microsoft Graph auth for one mailbox, via device code flow.

Why delegated rather than app-only
----------------------------------
Admin decision, 2026-08-31. The application permission `Mail.Read` has no
per-mailbox variant — Microsoft's consent screen reads "Read mail in all
mailboxes" — so app-only access to Rakesh's inbox means granting an unattended
credential permanent read access to all 36 mailboxes in the tenant, then fencing
it back with an Exchange Application Access Policy. The fence works, but it is a
control we do not own: if it silently fails to apply, a leaked `.env` opens
every employee's mail.

Delegated consent is scoped to the signing user by construction. Rakesh consents
for *his* mailbox, the token can reach nothing else, and there is no tenant-wide
grant to fence in the first place. That it is also fewer steps for him is a
bonus, not the reason.

Why device code flow
--------------------
The alternatives both cost more for no benefit here. Authorization-code flow
needs a redirect URI and a local web server to catch the callback — awkward for a
CLI, and a redirect URI is one more thing to register and get wrong.
Username/password (ROPC) cannot be used at all: it breaks under MFA, which any
Global Admin account should have.

Device code needs nothing registered but the app itself. We print a URL and an
eight-character code; Rakesh opens the URL on any device, types the code, signs
in with his normal MFA. He never sees a terminal.

Token lifetime, honestly
------------------------
Access tokens last about an hour and are refreshed silently. The refresh token is
what matters, and it renews on every use, so nightly ingestion keeps it alive
indefinitely. It dies in three cases: Rakesh changes his password, his MFA is
reset, or nothing runs for roughly 90 days. Recovery is one sign-in, and
``needs_reauth`` reports it explicitly rather than letting ingestion fail with a
confusing 401.

The token cache is a bearer credential for a real mailbox. It is written to a
git-ignored path with restrictive permissions and is never logged.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from mangotree.core.logging import logger

#: Delegated scopes. `Mail.Read` and nothing else — ingestion never writes, and a
#: write scope we do not use is a write scope someone can later misuse.
#: `offline_access` is what yields the refresh token; without it we would need
#: Rakesh to sign in hourly.
SCOPES: List[str] = ["Mail.Read", "offline_access"]

AUTHORITY = "https://login.microsoftonline.com/{tenant}"


class GraphAuthError(RuntimeError):
    pass


class GraphReauthRequired(GraphAuthError):
    """The refresh token is gone or rejected — a human must sign in again."""


@dataclass
class DeviceCodePrompt:
    """What to show the person signing in."""
    verification_uri: str
    user_code: str
    expires_in: int
    message: str


class GraphDelegatedAuth:
    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        mailbox: str,
        cache_path: Optional[Path] = None,
    ) -> None:
        if not tenant_id or not client_id:
            raise ValueError("tenant_id and client_id are required")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.mailbox = (mailbox or "").strip().lower()
        self.cache_path = Path(cache_path or ".secrets/graph_token_cache.json")
        self._lock = threading.Lock()
        self._app = None
        self._cache = None

    # ------------------------------------------------------------------ cache
    def _load_cache(self):
        import msal

        cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            try:
                cache.deserialize(self.cache_path.read_text(encoding="utf-8"))
            except Exception as exc:
                # A corrupt cache must not be silently ignored: it would look
                # like "never signed in" and trigger a surprise device prompt in
                # the middle of an unattended run.
                logger.warning("Graph token cache unreadable (%s); re-auth needed", exc)
        return cache

    def _save_cache(self) -> None:
        if self._cache is None or not self._cache.has_state_changed:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(self._cache.serialize(), encoding="utf-8")
        try:
            # Owner read/write only. On Windows this is advisory, but it costs
            # nothing and is meaningful if this ever runs on a POSIX host.
            os.chmod(self.cache_path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass

    def _application(self):
        import msal

        if self._app is None:
            self._cache = self._load_cache()
            # PublicClientApplication: no client secret exists in this flow, so
            # there is no long-lived tenant-wide credential on disk at all —
            # only a refresh token bound to one consenting user.
            self._app = msal.PublicClientApplication(
                self.client_id,
                authority=AUTHORITY.format(tenant=self.tenant_id),
                token_cache=self._cache,
            )
        return self._app

    # ------------------------------------------------------------------ state
    @property
    def is_authenticated(self) -> bool:
        try:
            app = self._application()
        except Exception:
            return False
        return bool(app.get_accounts())

    def signed_in_account(self) -> Optional[str]:
        app = self._application()
        accounts = app.get_accounts()
        return accounts[0].get("username") if accounts else None

    # ------------------------------------------------------------------ sign-in
    def sign_in_device_code(
        self, on_prompt: Optional[Callable[[DeviceCodePrompt], None]] = None
    ) -> str:
        """Interactive one-time sign-in. Returns the signed-in username."""
        app = self._application()
        flow = app.initiate_device_flow(scopes=[s for s in SCOPES if s != "offline_access"])

        if "user_code" not in flow:
            raise GraphAuthError(
                "Device flow could not start. The usual cause is that the app "
                "registration does not allow public client flows — in Entra, open "
                "the app, Authentication, and set 'Allow public client flows' to "
                f"Yes. Raw response: {json.dumps(flow)[:400]}"
            )

        prompt = DeviceCodePrompt(
            verification_uri=flow.get("verification_uri", ""),
            user_code=flow.get("user_code", ""),
            expires_in=int(flow.get("expires_in", 900)),
            message=flow.get("message", ""),
        )
        if on_prompt:
            on_prompt(prompt)
        else:
            logger.info("Graph sign-in: %s", prompt.message)

        # Blocks until the person completes sign-in or the code expires.
        result = app.acquire_token_by_device_flow(flow)
        self._save_cache()

        if "access_token" not in result:
            raise GraphAuthError(
                f"Sign-in failed: {result.get('error')} — "
                f"{result.get('error_description', '')[:400]}"
            )

        claims = result.get("id_token_claims") or {}
        username = (
            claims.get("preferred_username")
            or claims.get("upn")
            or self.signed_in_account()
            or ""
        )

        # The whole security model rests on *which* mailbox consented, so a
        # mismatch is fatal rather than a warning. Signing in as the wrong
        # account would otherwise produce a working client pointed at a mailbox
        # nobody approved.
        if self.mailbox and username and username.strip().lower() != self.mailbox:
            raise GraphAuthError(
                f"Signed in as {username!r} but this client is configured for "
                f"{self.mailbox!r}. Sign out and repeat as {self.mailbox}, or "
                f"correct GRAPH_MAILBOX."
            )

        logger.info("Graph delegated sign-in complete for %s", username)
        return username

    # ------------------------------------------------------------------ tokens
    def access_token(self) -> str:
        """A valid access token, refreshed silently. Never prompts."""
        with self._lock:
            app = self._application()
            accounts = app.get_accounts()
            if not accounts:
                raise GraphReauthRequired(
                    "No signed-in account in the token cache. Run "
                    "`python -m mangotree.cli outlook-auth` and have "
                    f"{self.mailbox} complete the sign-in."
                )

            account = accounts[0]
            if self.mailbox:
                match = [
                    a for a in accounts
                    if (a.get("username") or "").strip().lower() == self.mailbox
                ]
                if not match:
                    raise GraphReauthRequired(
                        f"Token cache holds {[a.get('username') for a in accounts]} "
                        f"but not {self.mailbox}."
                    )
                account = match[0]

            result = app.acquire_token_silent(
                [s for s in SCOPES if s != "offline_access"], account=account
            )
            self._save_cache()

            if not result or "access_token" not in result:
                error = (result or {}).get("error_description", "no cached token")
                raise GraphReauthRequired(
                    "Silent token refresh failed — the refresh token has expired "
                    "or been revoked (password change, MFA reset, or ~90 days "
                    "idle). Re-run `python -m mangotree.cli outlook-auth`. "
                    f"Detail: {str(error)[:300]}"
                )
            return result["access_token"]

    def needs_reauth(self) -> bool:
        try:
            self.access_token()
            return False
        except GraphReauthRequired:
            return True

    def sign_out(self) -> None:
        """Forget the cached tokens. Does not revoke consent server-side."""
        app = self._application()
        for account in app.get_accounts():
            app.remove_account(account)
        self._save_cache()
        if self.cache_path.exists():
            self.cache_path.unlink()
        logger.info("Graph token cache cleared")


def describe_expiry(auth: GraphDelegatedAuth) -> dict:
    """Cheap health summary for the status command."""
    return {
        "authenticated": auth.is_authenticated,
        "account": auth.signed_in_account(),
        "cache_path": str(auth.cache_path),
        "cache_exists": auth.cache_path.exists(),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
