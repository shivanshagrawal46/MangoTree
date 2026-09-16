"""The outbox: queue, send through Microsoft Graph, record, and watch for replies.

Sending
  ``POST /users/{mailbox}/sendMail`` with the body and base64 file attachments,
  ``saveToSentItems`` on. Graph returns nothing about the message it sent, so the
  Sent Items folder is read back (Mail.Read, which we already have) to capture
  the ``internetMessageId`` and ``conversationId`` — a reply keeps the
  conversation id, which is how it is matched to what we sent.

States
  queued           — waiting for a send attempt
  sent             — delivered to Graph; ids recorded
  needs_consent    — the signed-in token has no Mail.Send; a person must sign in
                     once more (the UI shows exactly what to run). Retried on
                     every tick after that.
  failed           — Graph refused; error kept; retried a few times
  replied          — a message in the same conversation arrived from a recipient

Replies are detected by ``poll_replies``: inbox messages since the last watermark
whose ``conversationId`` matches a sent item and whose sender is one of its
recipients. Only what we sent is looked at — the mailbox is not otherwise read
here — and nothing before FOLLOWUP_SINCE is considered.
"""
from __future__ import annotations

import base64
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg
from mangotree.storage.mongo import Mongo

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
MAX_ATTEMPTS = 6


class Outbox:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.coll = mongo.db["outbox"]
        self.coll.create_index([("status", 1), ("queued_at", 1)], name="ix_outbox_status")
        self.coll.create_index("conversation_id", name="ix_outbox_conv", sparse=True)
        self.coll.create_index([("kind", 1), ("ref", 1)], name="ix_outbox_ref")
        self.state = mongo.db["outbox_state"]
        self._auth = None
        self._client = None

    # ------------------------------------------------------------- graph
    def _graph(self):
        if self._client is None:
            from mangotree.ingest.graph_client import GraphClient
            self._client = GraphClient.from_settings()
            self._auth = self._client.auth
        return self._client

    @property
    def mailbox(self) -> str:
        return self._graph().mailbox

    def can_send(self) -> bool:
        try:
            return bool(self._graph().auth.can_send())
        except Exception as exc:
            logger.warning("outbox: Graph not configured for sending: %s", exc)
            return False

    def send_status(self) -> Dict[str, Any]:
        try:
            g = self._graph()
            return {"mailbox": g.mailbox, "can_send": g.auth.can_send(), "signed_in": g.auth.is_authenticated,
                    "how_to_enable": "On the server: cd /rkb/MangoTree && .venv/bin/python -m mangotree.cli outlook-auth — "
                                     "then rakesh@mtreh.com opens the link, enters the code and accepts 'Send mail as you'."}
        except Exception as exc:
            return {"mailbox": None, "can_send": False, "signed_in": False, "error": str(exc)[:200]}

    # -------------------------------------------------------------- queue
    def queue(self, *, kind: str, ref: str, to: Sequence[Tuple[str, str]], subject: str, html: str, text: str,
              attachments: Sequence[Tuple[str, bytes, str]] = (), meta: Optional[Dict[str, Any]] = None,
              dedupe: bool = True) -> Dict[str, Any]:
        """Put one email on the outbox. ``to`` is [(name, address)]; ``attachments``
        [(filename, bytes, content_type)]. With ``dedupe`` an identical
        (kind, ref) still queued or sent is not queued twice."""
        if dedupe:
            existing = self.coll.find_one({"kind": kind, "ref": ref, "status": {"$in": ["queued", "sent", "needs_consent", "replied"]}}, {"_id": 0, "outbox_id": 1, "status": 1})
            if existing:
                return {"outbox_id": existing["outbox_id"], "status": existing["status"], "deduped": True}
        oid = f"ob-{uuid.uuid4().hex[:12]}"
        doc = {"outbox_id": oid, "kind": kind, "ref": ref, "to": [{"name": n, "address": a.lower()} for n, a in to],
               "subject": subject, "html": html, "text": text,
               "attachments": [{"filename": f, "content_type": ct, "bytes": b, "size": len(b)} for f, b, ct in attachments],
               "meta": meta or {}, "status": "queued", "attempts": 0, "queued_at": datetime.now(timezone.utc),
               "sent_at": None, "error": None, "internet_message_id": None, "conversation_id": None,
               "replied_at": None, "replied_by": None, "reply_preview": None}
        self.coll.insert_one(doc)
        return {"outbox_id": oid, "status": "queued"}

    def get(self, outbox_id: str) -> Optional[Dict[str, Any]]:
        return self.coll.find_one({"outbox_id": outbox_id}, {"_id": 0, "attachments.bytes": 0, "html": 0})

    def list(self, *, kinds: Optional[Sequence[str]] = None, since_days: int = 30, limit: int = 200) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"queued_at": {"$gte": datetime.now(timezone.utc) - timedelta(days=since_days)}}
        if kinds:
            q["kind"] = {"$in": list(kinds)}
        return list(self.coll.find(q, {"_id": 0, "attachments.bytes": 0, "html": 0}).sort("queued_at", -1).limit(limit))

    # --------------------------------------------------------------- send
    def _payload(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        msg = {
            "subject": doc["subject"],
            "body": {"contentType": "HTML", "content": doc["html"]},
            "toRecipients": [{"emailAddress": {"address": r["address"], "name": r.get("name") or r["address"]}} for r in doc["to"]],
            "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": a["filename"], "contentType": a["content_type"],
                             "contentBytes": base64.b64encode(a["bytes"]).decode("ascii")} for a in doc.get("attachments") or []],
            "internetMessageHeaders": [{"name": "x-mangotree-outbox", "value": doc["outbox_id"]}],
        }
        return {"message": msg, "saveToSentItems": True}

    def _find_sent(self, doc: Dict[str, Any], since: datetime) -> Optional[Dict[str, Any]]:
        """Read Sent Items back to learn the ids Graph did not return."""
        g = self._graph()
        from mangotree.ingest.graph_auth import SCOPES
        url = (f"{GRAPH_ROOT}/users/{quote(g.mailbox)}/mailFolders/sentitems/messages"
               f"?$select=id,subject,internetMessageId,conversationId,sentDateTime,toRecipients"
               f"&$filter=sentDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}&$orderby=sentDateTime desc&$top=25")
        for _ in range(6):
            headers = {"Authorization": f"Bearer {g.auth.access_token(SCOPES)}", "Accept": "application/json"}
            r = g._session.get(url, headers=headers, timeout=g.timeout)
            if r.status_code == 200:
                for m in r.json().get("value") or []:
                    if (m.get("subject") or "") == doc["subject"]:
                        return m
            time.sleep(3)
        return None

    def send_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        from mangotree.ingest.graph_auth import GraphReauthRequired, SEND_SCOPES
        g = self._graph()
        now = datetime.now(timezone.utc)
        try:
            token = g.auth.access_token(SEND_SCOPES)
        except GraphReauthRequired as exc:
            self.coll.update_one({"outbox_id": doc["outbox_id"]}, {"$set": {"status": "needs_consent", "error": str(exc)[:400], "last_attempt_at": now},
                                                                    "$inc": {"attempts": 1}})
            return {"status": "needs_consent"}
        full = self.coll.find_one({"outbox_id": doc["outbox_id"]})
        url = f"{GRAPH_ROOT}/users/{quote(g.mailbox)}/sendMail"
        r = g._session.post(url, json=self._payload(full), headers={"Authorization": f"Bearer {token}"}, timeout=max(g.timeout, 120))
        if r.status_code in (202, 200):
            sent = self._find_sent(full, now - timedelta(minutes=2)) or {}
            self.coll.update_one({"outbox_id": doc["outbox_id"]}, {"$set": {
                "status": "sent", "sent_at": now, "error": None, "last_attempt_at": now,
                "internet_message_id": sent.get("internetMessageId"), "conversation_id": sent.get("conversationId"),
                "graph_message_id": sent.get("id")}, "$inc": {"attempts": 1}})
            logger.info("outbox: sent %s to %s (%s)", doc["subject"], [t["address"] for t in doc["to"]], doc["outbox_id"])
            return {"status": "sent", "conversation_id": sent.get("conversationId")}
        err = f"HTTP {r.status_code}: {r.text[:300]}"
        attempts = int(full.get("attempts") or 0) + 1
        status = "failed" if attempts >= MAX_ATTEMPTS or r.status_code in (400, 403) else "queued"
        self.coll.update_one({"outbox_id": doc["outbox_id"]}, {"$set": {"status": status, "error": err, "last_attempt_at": now}, "$inc": {"attempts": 1}})
        logger.warning("outbox: send failed for %s: %s", doc["outbox_id"], err)
        return {"status": status, "error": err}

    def flush(self, *, limit: int = 20) -> Dict[str, int]:
        """Attempt every queued email, and every needs_consent one (consent may
        have been granted since)."""
        out = {"sent": 0, "needs_consent": 0, "failed": 0, "queued": 0}
        pending = list(self.coll.find({"status": {"$in": ["queued", "needs_consent"]}}, {"_id": 0, "outbox_id": 1, "subject": 1, "to": 1, "status": 1})
                       .sort("queued_at", 1).limit(limit))
        if not pending:
            return out
        if not self.can_send():
            self.coll.update_many({"status": "queued"}, {"$set": {"status": "needs_consent", "last_attempt_at": datetime.now(timezone.utc)}})
            out["needs_consent"] = len(pending)
            return out
        for d in pending:
            try:
                res = self.send_one(d)
                out[res.get("status", "failed")] = out.get(res.get("status", "failed"), 0) + 1
            except Exception as exc:
                logger.exception("outbox: unexpected error sending %s", d["outbox_id"])
                self.coll.update_one({"outbox_id": d["outbox_id"]}, {"$set": {"error": str(exc)[:300], "last_attempt_at": datetime.now(timezone.utc)}, "$inc": {"attempts": 1}})
                out["failed"] += 1
        return out

    # ------------------------------------------------------------ replies
    def poll_replies(self) -> Dict[str, Any]:
        """Look at inbox messages since the watermark; a message in the
        conversation of a sent email, from one of its recipients, closes it."""
        from mangotree.ingest.graph_auth import SCOPES
        g = self._graph()
        st = self.state.find_one({"_id": "replies"}) or {}
        floor = datetime.strptime(cfg.FOLLOWUP_SINCE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        since = max(st.get("watermark") or floor, floor) - timedelta(minutes=10)
        open_sent = list(self.coll.find({"status": "sent", "conversation_id": {"$ne": None}}, {"_id": 0, "outbox_id": 1, "conversation_id": 1, "to": 1, "kind": 1, "ref": 1}))
        if not open_sent:
            self.state.update_one({"_id": "replies"}, {"$set": {"watermark": datetime.now(timezone.utc), "checked_at": datetime.now(timezone.utc)}}, upsert=True)
            return {"checked": 0, "replied": 0}
        by_conv = {d["conversation_id"]: d for d in open_sent}
        url = (f"{GRAPH_ROOT}/users/{quote(g.mailbox)}/mailFolders/inbox/messages"
               f"?$select=id,subject,from,conversationId,internetMessageId,receivedDateTime,bodyPreview"
               f"&$filter=receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}&$orderby=receivedDateTime desc&$top=100")
        headers = {"Authorization": f"Bearer {g.auth.access_token(SCOPES)}", "Accept": "application/json"}
        checked = replied = 0
        newest = since
        while url:
            r = g._session.get(url, headers=headers, timeout=g.timeout)
            if r.status_code != 200:
                logger.warning("outbox: reply poll failed HTTP %s", r.status_code)
                break
            data = r.json()
            for m in data.get("value") or []:
                checked += 1
                try:
                    rec = datetime.fromisoformat((m.get("receivedDateTime") or "").replace("Z", "+00:00"))
                    newest = max(newest, rec)
                except ValueError:
                    rec = None
                sent = by_conv.get(m.get("conversationId"))
                if not sent:
                    continue
                sender = (((m.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
                if sender and sender in {t["address"] for t in sent["to"]}:
                    self.coll.update_one({"outbox_id": sent["outbox_id"], "status": "sent"}, {"$set": {
                        "status": "replied", "replied_at": rec or datetime.now(timezone.utc), "replied_by": sender,
                        "reply_preview": (m.get("bodyPreview") or "")[:300], "reply_message_id": m.get("internetMessageId")}})
                    replied += 1
                    logger.info("outbox: %s replied to %s", sender, sent["outbox_id"])
            url = data.get("@odata.nextLink")
        self.state.update_one({"_id": "replies"}, {"$set": {"watermark": newest, "checked_at": datetime.now(timezone.utc)}}, upsert=True)
        return {"checked": checked, "replied": replied}
