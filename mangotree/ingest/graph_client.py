"""Microsoft Graph client for a single Exchange Online mailbox.

Scope, and why it is enforced twice
-----------------------------------
Admin directive: **Inbox and Sent Items only**, mailbox `Rakesh@mtreh.com` only,
read-only. The Application Access Policy in Exchange Online enforces the mailbox
restriction on Microsoft's side, and this client enforces it again on ours. Two
independent enforcements because an Access Policy that silently failed to apply
is indistinguishable from one that worked — right up until an audit. A client
that would happily read a second mailbox if the server let it is a client that
depends on a control it cannot see.

Auth
----
**Delegated**, via ``graph_auth.GraphDelegatedAuth`` (admin decision 2026-08-31).
Rakesh consents once for his own mailbox and the resulting token can reach
nothing else — the restriction is a property of the grant rather than a policy
layered on top of a tenant-wide one. The `_assert_scope` guard below is therefore
belt-and-braces against our own bugs, not the only thing standing between this
process and 36 mailboxes.

App-only client credentials were the original design and were rejected: the
application `Mail.Read` permission has no per-mailbox form, so it would have
meant holding a secret that could read every mailbox in the tenant.

Completeness
------------
Two mechanisms, deliberately overlapping:

* **Delta queries** for incremental sync — Graph returns a token and, on the next
  call, only what changed. Cheap and exact for steady state.
* **Time-window enumeration** for backfill and for the reconciliation sweep, so
  completeness never rests on a token we might have lost, corrupted, or had
  invalidated server-side. A delta token is a claim about history; enumerating
  the window is a check on it.

Raw MIME is fetched for every message (`$value`), not just the parsed JSON. It is
the only form that survives our own parser being wrong, and re-fetching a
provider months later is not something to rely on.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Sequence
from urllib.parse import quote

import requests

from mangotree.core.logging import logger

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

#: The only folders in scope. Well-known names, so no folder-id lookup is needed.
ALLOWED_FOLDERS = ("inbox", "sentitems")

#: Fields worth having on the message record alongside the raw MIME.
MESSAGE_FIELDS = (
    "id,internetMessageId,conversationId,subject,receivedDateTime,"
    "sentDateTime,from,sender,toRecipients,ccRecipients,bccRecipients,"
    "replyTo,hasAttachments,isDraft,parentFolderId,internetMessageHeaders"
)

#: Envelope-only projection for surveying. No body, no MIME — enough to judge
#: whether a folder is worth ingesting, and not enough to be a copy of the mail.
SURVEY_FIELDS = (
    "id,internetMessageId,subject,receivedDateTime,sentDateTime,from,"
    "toRecipients,ccRecipients,bccRecipients,hasAttachments"
)


class GraphScopeViolation(RuntimeError):
    """Raised when a caller asks for a mailbox or folder outside the mandate."""


@dataclass
class GraphStats:
    listed: int = 0
    fetched_mime: int = 0
    attachments: int = 0
    throttled: int = 0
    errors: int = 0
    error_detail: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "listed": self.listed,
            "fetched_mime": self.fetched_mime,
            "attachments": self.attachments,
            "throttled": self.throttled,
            "errors": self.errors,
            "error_detail": self.error_detail[:20],
        }


class GraphClient:
    def __init__(
        self,
        auth: "GraphDelegatedAuth",
        *,
        timeout: int = 60,
        max_retries: int = 5,
    ) -> None:
        self.auth = auth
        #: The single mailbox this client is permitted to touch. Taken from the
        #: auth object so the two can never disagree.
        self.mailbox = auth.mailbox
        if not self.mailbox:
            raise ValueError("auth.mailbox must be set")
        self.timeout = timeout
        self.max_retries = max_retries

        self.stats = GraphStats()
        self._lock = threading.Lock()
        self._session = requests.Session()

    @classmethod
    def from_settings(cls, settings=None, **kwargs) -> "GraphClient":
        from mangotree.config.settings import SETTINGS
        from mangotree.ingest.graph_auth import GraphDelegatedAuth

        settings = settings or SETTINGS
        auth = GraphDelegatedAuth(
            tenant_id=getattr(settings, "graph_tenant_id", ""),
            client_id=getattr(settings, "graph_client_id", ""),
            mailbox=getattr(settings, "graph_mailbox", ""),
        )
        return cls(auth, **kwargs)

    # ------------------------------------------------------------------ auth
    def _access_token(self) -> str:
        # MSAL owns caching and refresh, so there is no second expiry clock here
        # to drift out of step with the real one.
        return self.auth.access_token()

    #: ``/me`` is not usable under delegated auth here for a subtle reason: it
    #: resolves to whoever signed in, so a mistaken sign-in would silently read
    #: the wrong mailbox. Addressing ``/users/{mailbox}`` explicitly means a
    #: mismatch fails loudly instead.
    def _mailbox_root(self) -> str:
        return f"{GRAPH_ROOT}/users/{quote(self.mailbox)}"

    # ------------------------------------------------------------------ guard
    def _assert_scope(self, mailbox: str, folder: Optional[str] = None) -> None:
        if mailbox.strip().lower() != self.mailbox:
            raise GraphScopeViolation(
                f"mailbox {mailbox!r} is outside this client's mandate "
                f"({self.mailbox!r}). Refusing the request."
            )
        if folder is not None and folder.strip().lower() not in ALLOWED_FOLDERS:
            raise GraphScopeViolation(
                f"folder {folder!r} is out of scope; only {ALLOWED_FOLDERS} "
                f"may be read (admin directive)."
            )

    # ------------------------------------------------------------------ http
    def _request(
        self, method: str, url: str, *, raw: bool = False, **kwargs
    ) -> requests.Response:
        for attempt in range(1, self.max_retries + 1):
            headers = kwargs.pop("headers", {}) or {}
            headers["Authorization"] = f"Bearer {self._access_token()}"
            if not raw:
                headers.setdefault("Accept", "application/json")

            response = self._session.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )

            # Graph tells us how long to wait; guessing is worse than obeying.
            if response.status_code in (429, 503, 504):
                self.stats.throttled += 1
                delay = int(response.headers.get("Retry-After", min(2 ** attempt, 60)))
                logger.warning(
                    "Graph throttled (%s), sleeping %ss (attempt %d/%d)",
                    response.status_code, delay, attempt, self.max_retries,
                )
                time.sleep(delay)
                continue

            if response.status_code == 401 and attempt < self.max_retries:
                with self._lock:
                    self._token = None
                continue

            return response

        raise RuntimeError(f"Graph request exhausted retries: {method} {url}")

    # ------------------------------------------------------------------ read
    def list_messages(
        self,
        folder: str,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        page_size: int = 50,
        order: str = "asc",
    ) -> Iterator[dict]:
        """Enumerate a folder by time window. Used for backfill and reconciliation.

        Ascending by default: backfill walks forward through time so an
        interrupted run resumes from a date rather than a page offset. Pass
        ``order="desc"`` only for sampling the newest mail — never for backfill,
        where new arrivals mid-run would shift every subsequent page.
        """
        self._assert_scope(self.mailbox, folder)
        if order not in ("asc", "desc"):
            raise ValueError("order must be 'asc' or 'desc'")

        # Sent Items is ordered by when it was sent; Inbox by when it arrived.
        # Filtering Sent on receivedDateTime is a real and easy mistake — it
        # exists on sent messages but does not mean what the caller intends.
        date_field = "sentDateTime" if folder.lower() == "sentitems" else "receivedDateTime"

        filters = []
        if since:
            filters.append(f"{date_field} ge {_iso(since)}")
        if until:
            filters.append(f"{date_field} lt {_iso(until)}")

        params = {
            "$select": MESSAGE_FIELDS,
            "$top": str(page_size),
            "$orderby": f"{date_field} {order}",
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        url = (
            f"{self._mailbox_root()}/mailFolders/{folder}/messages"
        )
        first = True
        while url:
            response = self._request("GET", url, params=params if first else None)
            first = False
            if response.status_code != 200:
                self.stats.errors += 1
                self.stats.error_detail.append(
                    f"list {folder} {response.status_code}: {response.text[:300]}"
                )
                raise RuntimeError(
                    f"Graph list failed {response.status_code}: {response.text[:400]}"
                )
            payload = response.json()
            for message in payload.get("value", []):
                self.stats.listed += 1
                message["_folder"] = folder
                yield message
            url = payload.get("@odata.nextLink")

    def delta_messages(
        self, folder: str, *, delta_token: Optional[str] = None
    ) -> tuple[List[dict], Optional[str]]:
        """Incremental sync. Returns (messages, next_delta_token)."""
        self._assert_scope(self.mailbox, folder)

        if delta_token:
            url = delta_token
            params = None
        else:
            url = (
                f"{self._mailbox_root()}/mailFolders/{folder}/messages/delta"
            )
            params = {"$select": MESSAGE_FIELDS}

        messages: List[dict] = []
        next_token: Optional[str] = None
        while url:
            response = self._request("GET", url, params=params)
            params = None
            if response.status_code != 200:
                self.stats.errors += 1
                raise RuntimeError(
                    f"Graph delta failed {response.status_code}: {response.text[:400]}"
                )
            payload = response.json()
            for message in payload.get("value", []):
                message["_folder"] = folder
                messages.append(message)
                self.stats.listed += 1
            url = payload.get("@odata.nextLink")
            if not url:
                next_token = payload.get("@odata.deltaLink")
        return messages, next_token

    def raw_mime(self, message_id: str) -> bytes:
        """The message exactly as it arrived.

        Fetched for every message. Our parser will be wrong about something
        eventually, and the raw form is what makes that recoverable without
        going back to Microsoft months later.
        """
        url = (
            f"{self._mailbox_root()}/messages/{quote(message_id)}/$value"
        )
        response = self._request("GET", url, raw=True)
        if response.status_code != 200:
            self.stats.errors += 1
            raise RuntimeError(
                f"Graph MIME fetch failed {response.status_code}: {response.text[:300]}"
            )
        self.stats.fetched_mime += 1
        return response.content

    def list_attachments(self, message_id: str) -> List[dict]:
        url = (
            f"{self._mailbox_root()}/messages/{quote(message_id)}/attachments"
        )
        response = self._request("GET", url)
        if response.status_code != 200:
            self.stats.errors += 1
            return []
        items = response.json().get("value", [])
        self.stats.attachments += len(items)
        return items

    # ------------------------------------------------------------------ survey
    def folder_census(self, *, max_depth: int = 3) -> List[dict]:
        """Every folder's name and message count. No message content is read.

        Deliberately not guarded by ``_assert_scope``'s folder check. That guard
        exists to stop us *reading mail* outside Inbox and Sent; this reads only
        folder metadata. The distinction matters because "Inbox and Sent only"
        is unsafe to obey blindly — if a retention rule moved two years of mail
        into Archive, honouring the directive without knowing that would mean
        silently ingesting an incomplete record. Counting what is elsewhere is
        how that gets surfaced for a decision rather than discovered later.
        """
        rows: List[dict] = []

        def walk(url: str, prefix: str, depth: int) -> None:
            if depth > max_depth:
                return
            params = {
                "$top": "100",
                "$select": "id,displayName,totalItemCount,childFolderCount",
            }
            first = True
            while url:
                response = self._request("GET", url, params=params if first else None)
                first = False
                if response.status_code != 200:
                    self.stats.errors += 1
                    return
                payload = response.json()
                for folder in payload.get("value", []):
                    name = folder.get("displayName") or "(unnamed)"
                    path = f"{prefix}/{name}" if prefix else name
                    rows.append({
                        "id": folder.get("id"),
                        "path": path,
                        "count": folder.get("totalItemCount", 0),
                        "depth": depth,
                    })
                    if folder.get("childFolderCount", 0):
                        walk(
                            f"{self._mailbox_root()}/mailFolders/"
                            f"{quote(folder['id'])}/childFolders",
                            path,
                            depth + 1,
                        )
                url = payload.get("@odata.nextLink")

        walk(f"{self._mailbox_root()}/mailFolders", "", 0)
        return rows

    def survey_messages(
        self,
        folder_id: str,
        *,
        date_field: str = "receivedDateTime",
        since: Optional[datetime] = None,
        page_size: int = 100,
    ) -> Iterator[dict]:
        """Envelope metadata for any folder. Never bodies, never MIME, never stored.

        Separate from ``list_messages`` on purpose. That method is the ingestion
        path and stays fenced to Inbox and Sent. This one exists only to answer
        "what is in the folders we are about to exclude, and does any of it
        concern our properties" — a question that cannot be answered from folder
        names alone, and whose answer decides whether the exclusion is safe.
        Callers must not pass anything from here into the pipeline.
        """
        params = {
            "$select": SURVEY_FIELDS,
            "$top": str(page_size),
            "$orderby": f"{date_field} asc",
        }
        if since:
            params["$filter"] = f"{date_field} ge {_iso(since)}"

        url = f"{self._mailbox_root()}/mailFolders/{quote(folder_id)}/messages"
        first = True
        while url:
            response = self._request("GET", url, params=params if first else None)
            first = False
            if response.status_code != 200:
                self.stats.errors += 1
                self.stats.error_detail.append(
                    f"survey {folder_id[:24]} {response.status_code}: {response.text[:200]}"
                )
                return
            payload = response.json()
            for message in payload.get("value", []):
                yield message
            url = payload.get("@odata.nextLink")

    def folder_id_for(self, path: str) -> Optional[str]:
        """Resolve a census path such as ``Sent Items/Forwarded to JP Sir``."""
        for row in self.folder_census():
            if row["path"].lower() == path.strip().lower():
                return row["id"]
        return None

    def folder_span(self, folder: str) -> dict:
        """Oldest and newest message in a folder, plus its total count.

        The span is what tells us whether a backfill window is actually
        satisfiable. A folder whose oldest message postdates the window start
        cannot supply the earlier mail no matter how the query is written.
        """
        self._assert_scope(self.mailbox, folder)
        date_field = (
            "sentDateTime" if folder.lower() == "sentitems" else "receivedDateTime"
        )

        def edge(order: str) -> Optional[dict]:
            return next(iter(self.list_messages(folder, page_size=1, order=order)), None)

        meta = self._request(
            "GET",
            f"{self._mailbox_root()}/mailFolders/{folder}",
            params={"$select": "displayName,totalItemCount"},
        )
        total = meta.json().get("totalItemCount") if meta.status_code == 200 else None

        oldest, newest = edge("asc"), edge("desc")
        return {
            "folder": folder,
            "total": total,
            "date_field": date_field,
            "oldest": oldest,
            "newest": newest,
        }

    # ------------------------------------------------------------------ audit
    def verify_scope_restriction(self, other_mailbox: str) -> dict:
        """Prove the Application Access Policy is actually in force.

        Reads our own mailbox (must succeed) and another tenant mailbox (must be
        denied). Bypasses ``_assert_scope`` deliberately — the whole point is to
        exercise the *server's* control rather than our own, because ours proves
        nothing about what the credential could do in other hands.
        """
        result = {
            "mailbox": self.mailbox,
            "other_mailbox": other_mailbox,
            "own_access": None,
            "other_access": None,
            "restriction_enforced": False,
            "checked_at": datetime.now(timezone.utc),
        }

        own = self._request(
            "GET",
            f"{GRAPH_ROOT}/users/{quote(self.mailbox)}/mailFolders/inbox/messages",
            params={"$top": "1", "$select": "id"},
        )
        result["own_access"] = own.status_code

        other = self._request(
            "GET",
            f"{GRAPH_ROOT}/users/{quote(other_mailbox)}/mailFolders/inbox/messages",
            params={"$top": "1", "$select": "id"},
        )
        result["other_access"] = other.status_code
        result["other_body"] = other.text[:300]

        result["restriction_enforced"] = (
            own.status_code == 200 and other.status_code in (403, 404)
        )
        return result


def _iso(value: datetime) -> str:
    """Graph wants UTC ISO-8601 with a literal Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
