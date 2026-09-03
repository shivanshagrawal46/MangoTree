"""Gmail API client — read-only pull for Phase 4 Sprint 1.

Thin wrapper over the Gmail REST API that does exactly what the ingestion
needs and nothing more:

  • authenticate()      — OAuth installed-app flow (read-only scope) with a
                          durably-stored, auto-refreshing token.
  • list_labels()       — every Gmail label (= "folder") with message counts,
                          so we can confirm the exact folder names to pull.
  • resolve_labels()    — map human label names -> Gmail label IDs.
  • iter_message_ids()  — paginated message-id stream for a label + date range
                          (Gmail search `after:`/`before:` + labelIds).
  • get_raw()           — the full RFC822 message bytes (format='raw'), which we
                          feed through the SAME parser the .eml ingestion uses,
                          so Gmail mail is parsed identically (no divergence).
  • get_metadata()      — labelIds / threadId / internalDate for one message.

Scope is **gmail.readonly** only — this client can never modify the mailbox.

The google client libraries are imported lazily so `--help` and unit imports
work even before `pip install -r requirements.txt` has been run on the box.
"""
from __future__ import annotations

import base64
import os
import random
import time
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.utils.logger import logger

# Read-only — cannot send, delete, or modify anything in the mailbox.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

DEFAULT_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "client_secret.json")
DEFAULT_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json")


def _require_google():
    """Import the google client libs, with a helpful message if missing."""
    try:
        from google.auth.transport.requests import Request  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
        from googleapiclient.errors import HttpError  # noqa: F401
        return True
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Gmail ingestion needs the Google client libraries. Install them:\n"
            "    python -m pip install google-api-python-client google-auth "
            "google-auth-oauthlib\n"
            f"(import error: {exc})"
        ) from exc


class GmailClient:
    """Read-only Gmail client. Build once, call authenticate(), then use."""

    def __init__(
        self,
        *,
        client_secret_path: str = DEFAULT_CLIENT_SECRET,
        token_path: str = DEFAULT_TOKEN_PATH,
    ) -> None:
        self.client_secret_path = client_secret_path
        self.token_path = token_path
        self._service = None  # built on authenticate()

    # ------------------------------------------------------------------
    # auth
    # ------------------------------------------------------------------
    def authenticate(self) -> "GmailClient":
        """Load a stored token (refreshing if expired) or run the one-time
        OAuth consent flow. Persists the token to `token_path` for reuse."""
        _require_google()
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, GMAIL_SCOPES)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Could not load stored Gmail token ({exc}); re-authing.")
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Gmail token.")
                creds.refresh(Request())
            else:
                if not os.path.exists(self.client_secret_path):
                    raise FileNotFoundError(
                        f"OAuth client secret not found at '{self.client_secret_path}'. "
                        "Create an OAuth 2.0 Client ID (Desktop app) in Google Cloud "
                        "Console, download the JSON, and point GMAIL_CLIENT_SECRET at it."
                    )
                logger.info("Starting Gmail OAuth consent flow (one-time)…")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secret_path, GMAIL_SCOPES
                )
                # Opens a browser; falls back to console if no browser available.
                try:
                    creds = flow.run_local_server(port=0)
                except Exception:  # noqa: BLE001
                    creds = flow.run_console()
            with open(self.token_path, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
            logger.info(f"Gmail token stored at '{self.token_path}'.")

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self

    @property
    def service(self):
        if self._service is None:
            raise RuntimeError("GmailClient not authenticated — call authenticate() first.")
        return self._service

    @staticmethod
    def _execute(request, *, max_retries: int = 6):
        """Execute a Gmail API request with exponential backoff on rate-limit
        (429) / transient server (5xx) errors. Needed for large backfills."""
        from googleapiclient.errors import HttpError
        attempt = 0
        while True:
            try:
                return request.execute()
            except HttpError as exc:  # noqa: PERF203
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    delay = min(60, (2 ** attempt)) + random.uniform(0, 1)
                    logger.warning(f"Gmail API {status}; backoff {delay:.1f}s "
                                   f"(attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise

    # ------------------------------------------------------------------
    # profile / labels
    # ------------------------------------------------------------------
    def get_profile(self) -> Dict[str, Any]:
        return self.service.users().getProfile(userId="me").execute()

    def list_labels(self) -> List[Dict[str, Any]]:
        """Return all labels with per-label message totals (one extra call each)."""
        resp = self.service.users().labels().list(userId="me").execute()
        labels = resp.get("labels", [])
        out = []
        for lb in labels:
            detail = self.service.users().labels().get(
                userId="me", id=lb["id"]).execute()
            out.append({
                "id": lb["id"],
                "name": lb.get("name"),
                "type": lb.get("type"),
                "messagesTotal": detail.get("messagesTotal"),
                "threadsTotal": detail.get("threadsTotal"),
            })
        out.sort(key=lambda x: (x.get("type") != "user", (x.get("name") or "").lower()))
        return out

    def resolve_labels(self, names: Sequence[str]) -> Dict[str, str]:
        """Map label NAMES (case-insensitive) -> label IDs. Raises if any name
        does not exist, listing the available labels so the user can correct it."""
        all_labels = {lb["name"].lower(): lb["id"]
                      for lb in self.service.users().labels().list(
                          userId="me").execute().get("labels", [])
                      if lb.get("name")}
        resolved: Dict[str, str] = {}
        missing: List[str] = []
        for n in names:
            lid = all_labels.get((n or "").lower())
            if lid:
                resolved[n] = lid
            else:
                missing.append(n)
        if missing:
            available = ", ".join(sorted(all_labels.keys()))
            raise ValueError(
                f"Gmail label(s) not found: {missing}. Available labels: {available}"
            )
        return resolved

    # ------------------------------------------------------------------
    # message enumeration
    # ------------------------------------------------------------------
    @staticmethod
    def _date_query(after: Optional[datetime], before: Optional[datetime]) -> str:
        parts: List[str] = []
        if after:
            parts.append(f"after:{after.strftime('%Y/%m/%d')}")
        if before:
            parts.append(f"before:{before.strftime('%Y/%m/%d')}")
        return " ".join(parts)

    def find_by_message_id(self, rfc822_message_id: str) -> Optional[str]:
        """Resolve an RFC822 Message-ID to its Gmail id via the `rfc822msgid:`
        search operator. Returns the Gmail id, or None if not found."""
        mid = (rfc822_message_id or "").strip().strip("<>").strip()
        if not mid:
            return None
        resp = self._execute(self.service.users().messages().list(
            userId="me", q=f"rfc822msgid:{mid}", maxResults=1))
        msgs = resp.get("messages") or []
        return msgs[0]["id"] if msgs else None

    def iter_message_ids(
        self,
        *,
        label_ids: Optional[Sequence[str]] = None,
        query: Optional[str] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        include_spam_trash: bool = False,
    ) -> Iterator[str]:
        """Yield message IDs matching the label(s) + date range, paginating
        through the full result set. Gmail's `before:` is exclusive of that day;
        we widen ranges with deliberate overlap at the call site so nothing at a
        boundary is missed (dedup absorbs the overlap)."""
        q_parts = [self._date_query(after, before)]
        if query:
            q_parts.append(query)
        q = " ".join(p for p in q_parts if p).strip() or None

        page_token = None
        page = 0
        while True:
            resp = self._execute(self.service.users().messages().list(
                userId="me",
                labelIds=list(label_ids) if label_ids else None,
                q=q,
                pageToken=page_token,
                maxResults=500,
                includeSpamTrash=include_spam_trash,
            ))
            for m in resp.get("messages", []) or []:
                yield m["id"]
            page += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    # ------------------------------------------------------------------
    # message fetch
    # ------------------------------------------------------------------
    def get_raw(self, message_id: str) -> bytes:
        """Full RFC822 bytes for one message (format='raw'). This is what we
        feed into the shared .eml parser."""
        resp = self._execute(self.service.users().messages().get(
            userId="me", id=message_id, format="raw"))
        return base64.urlsafe_b64decode(resp["raw"].encode("ascii"))

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download ONE attachment's bytes by its Gmail attachment-id. Gmail
        returns the DECODED bytes — including the real documents unpacked from
        an Outlook `winmail.dat` (TNEF) blob — so this recovers attachments the
        raw-message parser can't see."""
        resp = self._execute(self.service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id))
        return base64.urlsafe_b64decode(resp["data"].encode("ascii"))

    def get_metadata(self, message_id: str) -> Dict[str, Any]:
        """labelIds / threadId / internalDate / snippet for one message."""
        resp = self._execute(self.service.users().messages().get(
            userId="me", id=message_id, format="minimal"))
        return {
            "gmail_id": resp.get("id"),
            "thread_id": resp.get("threadId"),
            "label_ids": resp.get("labelIds", []),
            "internal_date": resp.get("internalDate"),
            "snippet": resp.get("snippet", ""),
        }

    @staticmethod
    def _walk_parts(payload: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        stack = [payload] if payload else []
        while stack:
            p = stack.pop()
            yield p
            for sub in (p.get("parts") or []):
                stack.append(sub)

    def get_headers(self, message_id: str) -> Dict[str, Any]:
        """Cheap per-message identity for the completeness audit: the RFC822
        Message-ID, Date, Subject, From headers + an attachment count. Uses
        format='metadata' (no body download)."""
        resp = self._execute(self.service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["Message-ID", "Message-Id", "Date", "Subject", "From"]))
        payload = resp.get("payload", {}) or {}
        headers = {h.get("name", "").lower(): h.get("value", "")
                   for h in payload.get("headers", []) or []}
        n_attach = sum(1 for part in self._walk_parts(payload)
                       if (part.get("filename") or "").strip())
        return {
            "gmail_id": resp.get("id"),
            "thread_id": resp.get("threadId"),
            "label_ids": resp.get("labelIds", []),
            "internal_date": resp.get("internalDate"),
            "message_id_header": headers.get("message-id", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "n_attachments": n_attach,
        }

    def get_full_summary(self, message_id: str) -> Dict[str, Any]:
        """Per-message identity + FULL attachment part list (filename, mime, size,
        disposition, content-id) for the deep completeness audit. Uses
        format='full' so we see every MIME part's filename + size WITHOUT
        downloading attachment bytes. The caller applies the signature-logo
        filter so the comparison matches what ingestion keeps."""
        resp = self._execute(self.service.users().messages().get(
            userId="me", id=message_id, format="full"))
        payload = resp.get("payload", {}) or {}
        top_headers = {h.get("name", "").lower(): h.get("value", "")
                       for h in payload.get("headers", []) or []}
        parts_out: List[Dict[str, Any]] = []
        for part in self._walk_parts(payload):
            fn = (part.get("filename") or "").strip()
            if not fn:
                continue
            ph = {h.get("name", "").lower(): h.get("value", "")
                  for h in part.get("headers", []) or []}
            disp = (ph.get("content-disposition", "") or "").split(";")[0].strip().lower()
            parts_out.append({
                "filename": fn,
                "mime": part.get("mimeType", ""),
                "size": (part.get("body", {}) or {}).get("size", 0),
                "disposition": disp,
                "content_id": ph.get("content-id"),
                "attachment_id": (part.get("body", {}) or {}).get("attachmentId"),
            })
        return {
            "gmail_id": resp.get("id"),
            "thread_id": resp.get("threadId"),
            "label_ids": resp.get("labelIds", []),
            "message_id_header": top_headers.get("message-id", ""),
            "date": top_headers.get("date", ""),
            "subject": top_headers.get("subject", ""),
            "from": top_headers.get("from", ""),
            "parts": parts_out,
        }

    # ------------------------------------------------------------------
    # push notifications (watch) + incremental history
    # ------------------------------------------------------------------
    def watch(
        self,
        *,
        topic_name: str,
        label_ids: Optional[Sequence[str]] = None,
        label_filter_action: str = "include",
    ) -> Dict[str, Any]:
        """Arm Gmail push notifications to a Cloud Pub/Sub topic.

        Gmail will publish a small message ({emailAddress, historyId}) to
        `topic_name` whenever the mailbox changes for the given labels. Must
        be re-called before it expires (~7 days). Covered by gmail.readonly.

        `topic_name` is the FULL resource name:
            projects/<project-id>/topics/<topic-id>
        Returns {historyId, expiration(ms epoch)}.
        """
        body: Dict[str, Any] = {"topicName": topic_name}
        if label_ids:
            body["labelIds"] = list(label_ids)
            body["labelFilterAction"] = label_filter_action
        resp = self._execute(self.service.users().watch(userId="me", body=body))
        return resp

    def stop_watch(self) -> None:
        """Disable all push notifications for this mailbox."""
        self._execute(self.service.users().stop(userId="me"))

    def list_history(
        self,
        *,
        start_history_id: str,
        label_id: Optional[str] = None,
        history_types: Optional[Sequence[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield history records since `start_history_id` (paginated).

        Each record may contain messagesAdded / labelsAdded / etc. The caller
        extracts the message ids it cares about. `label_id` restricts to a
        single label (the Boris folder); `history_types` defaults to
        ['messageAdded', 'labelAdded'] so we catch both newly-arrived mail
        and mail that just got the label (e.g. sent mail labeled by a filter)."""
        types = list(history_types or ["messageAdded", "labelAdded"])
        page_token = None
        while True:
            resp = self._execute(self.service.users().history().list(
                userId="me",
                startHistoryId=start_history_id,
                labelId=label_id,
                historyTypes=types,
                pageToken=page_token,
                maxResults=500,
            ))
            for h in resp.get("history", []) or []:
                yield h
            page_token = resp.get("nextPageToken")
            if not page_token:
                break


__all__ = ["GmailClient", "GMAIL_SCOPES"]
