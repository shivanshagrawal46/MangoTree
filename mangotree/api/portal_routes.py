"""The contractor portal routes (``/portal/*``) and the CEO's account admin.

Every route here takes ``CurrentContractor`` — an RKB session is refused the
same way a contractor session is refused everywhere else. The portal reads
``contractor_sheets`` and ``contractor_events`` only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core.logging import logger

from .auth import CurrentContractor, CurrentUser, ensure_contractor

#: A person answering a sheet writes a handful of replies; a misbehaving client
#: must not be able to flood the log or the corpus.
MAX_WRITES_PER_DAY = 200
MAX_TEXT = 4000


class ReplyBody(BaseModel):
    text: str


class DoneBody(BaseModel):
    text: str = ""


class ViewedBody(BaseModel):
    sheet_id: str


class ContractorAccount(BaseModel):
    user_id: str
    login: str
    name: str
    full_name: str = ""
    org: str = "roi_blocks"
    org_name: str = "ROI Blocks / LP Remodeling"
    person_id: str
    property_ids: List[str] = []
    password: Optional[str] = None
    active: bool = True


def _ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else None)


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def install(app, mongo) -> None:
    from mangotree.portal import sheets as S

    def sheet_for(user) -> Optional[Dict[str, Any]]:
        sheet = S.latest_sheet(mongo, user["org"])
        if not sheet:
            return None
        scope = set(user.get("property_ids") or [])
        sheet["properties"] = [p for p in sheet.get("properties") or [] if p["property_id"] in scope]
        return sheet

    # ------------------------------------------------------------ portal
    @app.get("/portal/me")
    def portal_me(user=CurrentContractor):
        return user

    @app.get("/portal/sheet")
    def portal_sheet(user=CurrentContractor):
        """The latest sheet for this person's organisation, with the replies
        already made on each step — theirs and their colleagues'."""
        sheet = sheet_for(user)
        if not sheet:
            return {"sheet": None, "message": "No sheet has been published for you yet."}
        ids = [s["step_id"] for p in sheet["properties"] for s in p["steps"]]
        replies = S.replies_for_steps(mongo, user["org"], ids)
        for p in sheet["properties"]:
            for s in p["steps"]:
                s["replies"] = replies.get(s["step_id"], [])
                s["reported_done"] = any(r.get("action") == "reported_done" for r in s["replies"])
        sheet["open_count"] = sum(1 for p in sheet["properties"] for s in p["steps"] if not s["reported_done"])
        return _jsonable({"sheet": sheet})

    @app.post("/portal/viewed")
    def portal_viewed(body: ViewedBody, request: Request, user=CurrentContractor):
        """A read receipt: which sheet version this person opened, and when."""
        sheet = S.latest_sheet(mongo, user["org"])
        if not sheet or sheet["sheet_id"] != body.sheet_id:
            raise HTTPException(404, "that sheet is not the current one")
        # One receipt per person per sheet version is enough for "they saw it".
        if mongo.db["contractor_events"].find_one({"user_id": user["user_id"], "sheet_id": body.sheet_id, "action": "viewed"}, {"_id": 1}):
            return {"ok": True, "already": True}
        ev = S.record_event(mongo, user=user, action="viewed", ip=_ip(request), sheet_id=body.sheet_id, run_id=sheet["run_id"],
                            extra={"content_hash": sheet.get("content_hash")})
        return {"ok": True, "event_id": ev["event_id"]}

    def _write(user, request, step_id: str, text: str, action: str):
        text = (text or "").strip()
        if action == "replied" and not text:
            raise HTTPException(400, "write a reply first")
        if len(text) > MAX_TEXT:
            raise HTTPException(400, f"keep it under {MAX_TEXT} characters")
        if S.writes_today(mongo, user["user_id"]) >= MAX_WRITES_PER_DAY:
            raise HTTPException(429, "too many entries today; please continue tomorrow or email Rakesh")
        sheet = sheet_for(user)
        if not sheet:
            raise HTTPException(404, "no sheet to answer")
        step = S.find_step(sheet, step_id)
        if not step:
            raise HTTPException(404, "that item is not on your current sheet")
        out = S.store_reply(mongo, user=user, sheet=sheet, step=step, text=text, action=action, ip=_ip(request))
        return _jsonable({"ok": True, "event_id": out["event"]["event_id"], "at": out["event"]["at"], "step_id": step_id})

    @app.post("/portal/steps/{step_id}/reply")
    def portal_reply(step_id: str, body: ReplyBody, request: Request, user=CurrentContractor):
        return _write(user, request, step_id, body.text, "replied")

    @app.post("/portal/steps/{step_id}/done")
    def portal_done(step_id: str, body: DoneBody, request: Request, user=CurrentContractor):
        """'Reported done' — shown to RKB as reported; RKB confirms and closes."""
        return _write(user, request, step_id, body.text, "reported_done")

    @app.get("/portal/history")
    def portal_history(user=CurrentContractor):
        """What this organisation has said through the portal, newest first."""
        rows = S.recent_events(mongo, user["org"], limit=200, actions=("replied", "reported_done"))
        for r in rows:
            r.pop("hash", None); r.pop("prev_hash", None); r.pop("ip", None)
            r["address"] = PROPERTY_INDEX[r["property_id"]].canonical_address if r.get("property_id") in PROPERTY_INDEX else None
        return _jsonable({"items": rows})

    # ---------------------------------------------------- RKB side (CEO)
    def ceo(user) -> None:
        if user.get("role") != "ceo":
            raise HTTPException(403, "Rakesh Sir only")

    @app.get("/portal-admin/accounts")
    def portal_accounts(user=CurrentUser):
        ceo(user)
        rows = list(mongo.db["users"].find({"side": "contractor"}, {"_id": 0, "password_hash": 0}))
        return _jsonable({"items": rows})

    @app.post("/portal-admin/accounts")
    def portal_account_upsert(body: ContractorAccount, user=CurrentUser):
        """Create a contractor account or reset its password / scope."""
        ceo(user)
        bad = [p for p in body.property_ids if p not in PROPERTY_INDEX]
        if bad:
            raise HTTPException(400, f"unknown properties: {bad}")
        try:
            out = ensure_contractor(mongo, user_id=body.user_id.strip().lower(), login_name=body.login, name=body.name, full_name=body.full_name,
                                    org=body.org, org_name=body.org_name, person_id=body.person_id, property_ids=body.property_ids, password=body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        mongo.db["users"].update_one({"user_id": body.user_id.strip().lower()}, {"$set": {"active": body.active}})
        logger.info("portal: contractor account %s %s by %s", body.user_id, "updated" if not body.password else "created/reset", user["user_id"])
        return out

    @app.get("/portal-admin/events")
    def portal_events(org: Optional[str] = None, limit: int = 200, user=CurrentUser):
        """The audit trail, newest first, for Rakesh Sir's desk."""
        ceo(user)
        q: Dict[str, Any] = {"org": org} if org else {}
        rows = list(mongo.db["contractor_events"].find(q, {"_id": 0}).sort("at", -1).limit(min(limit, 1000)))
        for r in rows:
            r["address"] = PROPERTY_INDEX[r["property_id"]].canonical_address if r.get("property_id") in PROPERTY_INDEX else None
        return _jsonable({"items": rows, "chain": S.verify_chain(mongo)})

    @app.post("/portal-admin/publish")
    def portal_publish(user=CurrentUser):
        """Re-project the latest complete run for every organisation (after a
        new account, or a scope change)."""
        ceo(user)
        run = mongo.db["next_steps_runs"].find_one({"status": "complete"}, {"_id": 0}, sort=[("started_at", -1)])
        if not run:
            raise HTTPException(404, "no complete next-steps run yet")
        return {"published": S.publish_all(mongo, run), "run_id": run["run_id"]}
