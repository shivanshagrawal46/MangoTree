"""Gmail REST client — READ-ONLY.

Scope is ``gmail.readonly``; this client physically cannot send, delete, or
modify anything in the mailbox.

Design notes
------------
* ``get_raw`` returns the full RFC822 bytes so the *same* MIME parser handles
  Gmail mail, ``.eml`` files and (later) Outlook mail — no divergence between
  sources.
* Every call is retried with exponential backoff + jitter on 429/5xx, because a
  naive backfill against Gmail's quota silently loses messages.
* Message ids are streamed page-by-page so a 255k-message mailbox never has to
  fit in memory.
"""
from __future__ import annotations

import base64
import os
import random
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence

from mangotree.core.logging import logger

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_RETRY_STATUS = {403, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6


def _require_google():
    try:
        from google.auth.transport.requests import Request  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
        return True
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Gmail ingestion needs the Google client libraries:\n"
            "    python -m pip install google-api-python-client google-auth "
            "google-auth-oauthlib\n"
            f"(import error: {exc})"
        ) from exc


class GmailClient:
    def __init__(
        self,
        *,
        client_secret_path: str = "client_secret.json",
        token_path: str = "gmail_token.json",
    ) -> None:
        self.client_secret_path = client_secret_path
        self.token_path = token_path
        self._service = None
        self._creds = None
        # googleapiclient's service object wraps a single httplib2.Http, which is
        # NOT thread-safe. Concurrent fetches therefore each get their own
        # service built from the same credentials.
        self._local = threading.local()
        self.address: Optional[str] = None

    # ------------------------------------------------------------------
    def authenticate(self) -> "GmailClient":
        _require_google()
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, GMAIL_SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(self.token_path, "w", encoding="utf-8") as handle:
                handle.write(creds.to_json())
            logger.info("Gmail token refreshed")

        if not creds or not creds.valid:
            if not os.path.exists(self.client_secret_path):
                raise RuntimeError(
                    f"No valid token and no client secret at '{self.client_secret_path}'. "
                    "Download the OAuth desktop-app credentials from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secret_path, GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
            with open(self.token_path, "w", encoding="utf-8") as handle:
                handle.write(creds.to_json())
            logger.info("Gmail OAuth consent completed")

        self._creds = creds
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._local.service = self._service
        profile = self._call(self._service.users().getProfile(userId="me"))
        self.address = profile.get("emailAddress")
        logger.info(
            "Gmail authenticated: %s (%s messages)",
            self.address,
            profile.get("messagesTotal"),
        )
        return self

    # ------------------------------------------------------------------
    @property
    def service(self):
        """A service bound to the calling thread (httplib2 is not thread-safe)."""
        existing = getattr(self._local, "service", None)
        if existing is not None:
            return existing
        if self._creds is None:
            raise RuntimeError("GmailClient.authenticate() must be called first")
        from googleapiclient.discovery import build

        built = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        self._local.service = built
        return built

    # ------------------------------------------------------------------
    def _call(self, request) -> Dict[str, Any]:
        """Execute one API request with retry/backoff on throttling."""
        from googleapiclient.errors import HttpError

        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return request.execute()
            except HttpError as exc:  # pragma: no cover - network dependent
                status = getattr(exc.resp, "status", None)
                if status not in _RETRY_STATUS:
                    raise
                last_exc = exc
            except (TimeoutError, ConnectionError, OSError) as exc:  # pragma: no cover
                last_exc = exc

            sleep_for = min(60.0, (2 ** attempt)) + random.uniform(0, 1.0)
            logger.warning(
                "Gmail API retry %s/%s in %.1fs (%s)",
                attempt + 1, _MAX_ATTEMPTS, sleep_for, type(last_exc).__name__,
            )
            time.sleep(sleep_for)

        raise RuntimeError(f"Gmail API failed after {_MAX_ATTEMPTS} attempts: {last_exc}")

    # ------------------------------------------------------------------
    def list_labels(self) -> List[Dict[str, Any]]:
        data = self._call(self.service.users().labels().list(userId="me"))
        return data.get("labels", [])

    def iter_message_ids(
        self,
        *,
        query: str = "",
        label_ids: Optional[Sequence[str]] = None,
        include_spam_trash: bool = False,
        page_token: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield ``{"id", "threadId", "_page_token"}`` for every match.

        ``_page_token`` is the token that produced the current page, so a run
        can be resumed exactly where it stopped.
        """
        token = page_token
        while True:
            params: Dict[str, Any] = {
                "userId": "me",
                "maxResults": 500,
                "includeSpamTrash": include_spam_trash,
            }
            if query:
                params["q"] = query
            if label_ids:
                params["labelIds"] = list(label_ids)
            if token:
                params["pageToken"] = token

            data = self._call(self.service.users().messages().list(**params))
            current = token
            for msg in data.get("messages", []):
                yield {
                    "id": msg["id"],
                    "threadId": msg.get("threadId"),
                    "_page_token": current,
                }

            token = data.get("nextPageToken")
            if not token:
                return

    def get_raw(self, message_id: str) -> Dict[str, Any]:
        """Full message: raw RFC822 bytes + label ids + provider timestamps."""
        data = self._call(
            self.service.users().messages().get(
                userId="me", id=message_id, format="raw"
            )
        )
        raw_b64 = data.get("raw", "")
        raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii")) if raw_b64 else b""
        return {
            "id": data.get("id"),
            "thread_id": data.get("threadId"),
            "label_ids": data.get("labelIds", []),
            "internal_date_ms": int(data.get("internalDate", 0) or 0),
            "size_estimate": data.get("sizeEstimate"),
            "raw": raw_bytes,
        }

    def get_metadata(
        self,
        message_id: str,
        headers: Sequence[str] = (
            "From", "To", "Cc", "Bcc", "Subject", "Date", "Message-ID",
        ),
    ) -> Dict[str, Any]:
        """Envelope headers only — no body, no attachments.

        Counterpart to ``get_raw`` for surveying. A raw fetch of a mailbox this
        size moves hundreds of megabytes to answer questions that only need the
        headers, and Gmail's per-user quota is spent on bytes as well as calls.
        """
        data = self._call(
            self.service.users().messages().get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=list(headers),
            )
        )
        payload = data.get("payload", {}) or {}
        header_map = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in payload.get("headers", [])
        }
        return {
            "id": data.get("id"),
            "thread_id": data.get("threadId"),
            "label_ids": data.get("labelIds", []),
            "internal_date_ms": int(data.get("internalDate", 0) or 0),
            "headers": header_map,
        }

    def count(self, query: str) -> int:
        return sum(1 for _ in self.iter_message_ids(query=query))
