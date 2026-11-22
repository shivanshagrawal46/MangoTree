"""Routes for what the contractor enters (``/portal/work/*``) and RKB's part in it (``/brain/entry/*``).

The contractor's routes take a contractor session and nothing else; every one checks that the
job is one of that account's own and is open for entry (``Entry.check``). Each action is also
written to the contractor's append-only log, as every other portal action is.
"""
from __future__ import annotations

import io
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from mangotree.api import data
from mangotree.api.auth import CurrentContractor, CurrentUser
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from .entry import Entry, EntryError
from .store import BrainStore, name_of, now

SETTING_NOTIFY = "entry_notify"
NOTIFY_DEFAULT = ("manjunath",)
AGREE_ROLES = ("ceo", "operations")


class BillBody(BaseModel):
    kind: str = "work"
    pay_to: str = ""
    invoice_no: str = ""
    invoice_date: Optional[str] = None
    invoice_total: float = 0.0
    work_from: Optional[str] = None
    work_to: Optional[str] = None
    note: str = ""
    lines: Dict[str, float] = {}
    files: List[int] = []


class LogBody(BaseModel):
    date: Optional[str] = None
    tags: List[str] = []
    notes: str = ""
    blocked: str = ""
    files: List[int] = []


class ChangeOrderLine(BaseModel):
    title: str = ""
    kind: str = "labour"
    cost: float = 0.0
    price: float = 0.0


class ChangeOrderBody(BaseModel):
    title: str = ""
    group: str = ""
    days: int = 0
    why: str = ""
    lines: List[ChangeOrderLine] = []
    approved_by: str = ""
    approved_on: Optional[str] = None
    files: List[int] = []


class DecideBody(BaseModel):
    agree: bool
    why: str = ""


def _ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else None)


def _thumb(blob: bytes, side: int = 480) -> bytes:
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(blob))).convert("RGB")
    im.thumbnail((side, side))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80)
    return out.getvalue()


def install(app, mongo: Mongo, jobs) -> None:
    store = BrainStore(mongo)
    entry = Entry(mongo, store)

    def guard(fn, *a, **k):
        try:
            return fn(*a, **k)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except EntryError as exc:
            raise HTTPException(422, str(exc))

    def log_event(user, request, action: str, pid: str, text: str, **extra) -> None:
        try:
            from mangotree.portal import sheets as S
            S.record_event(mongo, user=user, action=action, ip=_ip(request), property_id=pid, text=text[:400], extra=extra or None)
        except Exception:
            logger.exception("entry: the contractor log could not be written (%s)", action)

    def tell_rkb(kind: str, ref: str, subject: str, lines: List[str], link: str) -> None:
        """One plain email to RKB's reviewer(s) the moment something is sent in. Never to the contractor."""
        try:
            from mangotree.mail.outbox import Outbox
            from .billmail import BillMail
            from .myday import TEAM
            to = [(TEAM[p]["name"], TEAM[p]["email"]) for p in (store.setting(SETTING_NOTIFY, list(NOTIFY_DEFAULT)) or []) if p in TEAM]
            if not to:
                return
            text = "\n".join(lines + ["", f"Open it: {link}", "", "Sent by MangoTree the moment the contractor sent it in."])
            html = "".join(f"<p style='margin:0 0 6px;font:14px/1.5 Segoe UI,Arial,sans-serif'>{l}</p>" for l in lines) + \
                f"<p style='margin:12px 0 0;font:14px Segoe UI,Arial,sans-serif'><a href='{link}'>Open it in MangoTree</a></p>"
            q = Outbox(mongo).queue(kind=kind, ref=ref, to=to, subject=subject, html=html, text=text, send_after=now() + timedelta(minutes=5),
                                    meta={"ref": ref})
            if q.get("outbox_id") and q.get("status") not in ("sent", "replied"):
                BillMail(mongo, store).send_now(q["outbox_id"], wait=10.0)
        except Exception:
            logger.exception("entry: RKB could not be told about %s %s", kind, ref)

    def site() -> str:
        import os
        return (os.environ.get("MT_PUBLIC_URL") or "http://139.59.39.65:3017").rstrip("/")

    # ------------------------------------------------------------ the contractor
    @app.get("/portal/work")
    def work_jobs(user=CurrentContractor):
        return {"jobs": entry.jobs_of(user)}

    @app.get("/portal/work/{pid}")
    def work_home(pid: str, user=CurrentContractor):
        return data.clean(guard(entry.home, user, pid))

    @app.post("/portal/work/{pid}/files")
    async def work_upload(pid: str, request: Request, role: str = Form(...), files: List[UploadFile] = File(...), user=CurrentContractor):
        out = []
        for f in files[:20]:
            blob = await f.read()
            d = guard(entry.add_file, user, pid, role=role, name=f.filename or "file", blob=blob)
            out.append({k: d.get(k) for k in ("file_id", "role", "name", "ext", "bytes", "is_photo", "taken_at", "gps")})
        log_event(user, request, "files_added", pid, f"{len(out)} file(s) added as {role}", file_ids=[d["file_id"] for d in out])
        return data.clean({"files": out})

    @app.get("/portal/work/files/{file_id}")
    def work_file(file_id: int, thumb: int = 0, user=CurrentContractor):
        f = entry.file(file_id)
        if not f:
            raise HTTPException(404, "not on file")
        guard(entry.check, user, f["property_id"])
        blob = entry.file_bytes(file_id)
        if not blob:
            raise HTTPException(404, "the file could not be read")
        if thumb and f.get("is_photo"):
            try:
                return Response(_thumb(blob), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
            except Exception:
                pass
        kind = "application/pdf" if f["ext"] == "pdf" else "image/jpeg" if f["ext"] in ("jpg", "jpeg") else f"image/{f['ext']}"
        return Response(blob, media_type=kind, headers={"Content-Disposition": f'inline; filename="{f["name"]}"', "Cache-Control": "private, max-age=3600"})

    @app.post("/portal/work/{pid}/bills")
    def work_bill_new(pid: str, body: BillBody, user=CurrentContractor):
        return data.clean(guard(entry.save_bill, user, pid, body.dict()))

    @app.put("/portal/work/{pid}/bills/{entry_id}")
    def work_bill_save(pid: str, entry_id: str, body: BillBody, user=CurrentContractor):
        return data.clean(guard(entry.save_bill, user, pid, body.dict(), entry_id))

    @app.post("/portal/work/bills/{entry_id}/send")
    def work_bill_send(entry_id: str, request: Request, user=CurrentContractor):
        v = guard(entry.send_bill, user, entry_id)
        log_event(user, request, "bill_sent", v["property_id"], f"{v['number']} {v['kind_name']} invoice {v['invoice_no']} ${v['total']:,.2f}", bill_id=v["bill_id"])
        bill_id, pid = v["bill_id"], v["property_id"]

        def after(job):
            # RKB is told first, then the check is read: the email must not wait for the reading.
            tell_rkb("entry_bill", str(bill_id), f"New bill from the contractor — {name_of(pid)} — ${v['total']:,.2f}",
                     [f"<b>{name_of(pid)}</b>: {v['by_name'] or 'The contractor'} has sent in a bill.",
                      f"{v['kind_name']}, invoice {v['invoice_no']}, pay to {v['pay_to']}.",
                      f"Amount ${v['total']:,.2f}" + (f" — ${v['pay_now']:,.2f} to pay now, ${v['holdback']:,.2f} held back." if v["holdback"] else "."),
                      f"{len(v['files'])} paper(s) and photo(s) attached. The line-by-line check is being read now."], f"{site()}/bills/{bill_id}")
            try:
                from .billcheck import BillCheck
                return data.clean(BillCheck(mongo, store).read(bill_id) or {"none": True})
            except Exception as exc:
                logger.exception("entry: the check of bill %s failed", bill_id)
                return {"error": str(exc)[:300]}
        jobs.start("bill_check", {"bill_id": bill_id, "by": user["user_id"], "from": "contractor"}, after)
        try:
            from mangotree.api import swr
            swr.clear()
        except Exception:
            pass
        return data.clean(v)

    @app.post("/portal/work/bills/{entry_id}/withdraw")
    def work_bill_withdraw(entry_id: str, request: Request, user=CurrentContractor):
        v = guard(entry.withdraw_bill, user, entry_id)
        log_event(user, request, "bill_taken_back", v["property_id"], f"{v.get('number') or entry_id} taken back to change")
        return data.clean(v)

    @app.delete("/portal/work/bills/{entry_id}")
    def work_bill_discard(entry_id: str, user=CurrentContractor):
        guard(entry.discard_bill, user, entry_id)
        return {"ok": True}

    @app.get("/portal/work/{pid}/logs")
    def work_logs(pid: str, user=CurrentContractor):
        return data.clean(guard(entry.logs_of, user, pid))

    @app.post("/portal/work/{pid}/logs")
    def work_log_new(pid: str, body: LogBody, request: Request, user=CurrentContractor):
        out = guard(entry.add_log, user, pid, body.dict())
        log_event(user, request, "log_sent", pid, f"daily log, {out['photos']} photos: {', '.join(out['tags'])}", log_id=out["log_id"])
        try:
            from mangotree.api import swr
            swr.clear()
        except Exception:
            pass
        return data.clean({**out, **guard(entry.logs_of, user, pid)})

    @app.get("/portal/work/{pid}/change-orders")
    def work_cos(pid: str, user=CurrentContractor):
        return data.clean(guard(entry.change_orders_of, user, pid))

    @app.post("/portal/work/{pid}/change-orders")
    def work_co_new(pid: str, body: ChangeOrderBody, request: Request, user=CurrentContractor):
        v = guard(entry.add_change_order, user, pid, body.dict())
        log_event(user, request, "change_order_sent", pid, f"{v['number']} {v['title']} ${v['cost']:,.2f}", co_id=v["co_id"])
        tell_rkb("entry_change_order", str(v["co_id"]), f"Change order to agree — {name_of(pid)} — ${v['cost']:,.2f}",
                 [f"<b>{name_of(pid)}</b>: {v['by_name'] or 'The contractor'} has sent in a change order.",
                  f"{v['title']} — ${v['cost']:,.2f} cost to RKB (${v['price']:,.2f} to the homeowner).",
                  f"Approved in Buildertrend by {v['approved_by']} on {v['approved_on']}; the approved copy is attached to it.",
                  "It cannot be billed until RKB agrees."], f"{site()}/bills")
        return data.clean({**v, **guard(entry.change_orders_of, user, pid)})

    # ------------------------------------------------------------ RKB
    @app.get("/brain/entry/change-orders")
    def rkb_cos(user=CurrentUser):
        return data.clean({"waiting": entry.waiting_for_rkb(), "can_decide": user.get("role") in AGREE_ROLES})

    @app.post("/brain/entry/change-orders/{co_id}/decide")
    def rkb_co_decide(co_id: int, body: DecideBody, user=CurrentUser):
        if user.get("role") not in AGREE_ROLES:
            raise HTTPException(403, "Only Rakesh Sir or Manjunath Sir can agree to a change order.")
        v = guard(entry.decide_change_order, user, co_id, agree=body.agree, why=body.why)
        try:
            from mangotree.api import swr
            swr.clear()
        except Exception:
            pass
        return data.clean({**v, "waiting": entry.waiting_for_rkb()})

    @app.get("/brain/entry/files/{file_id}")
    def rkb_file(file_id: int, thumb: int = 0, user=CurrentUser):
        f = entry.file(file_id)
        blob = entry.file_bytes(file_id) if f else None
        if not blob:
            raise HTTPException(404, "not on file")
        if thumb and f.get("is_photo"):
            try:
                return Response(_thumb(blob), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
            except Exception:
                pass
        kind = "application/pdf" if f["ext"] == "pdf" else "image/jpeg" if f["ext"] in ("jpg", "jpeg") else f"image/{f['ext']}"
        return Response(blob, media_type=kind, headers={"Content-Disposition": f'inline; filename="{f["name"]}"'})
