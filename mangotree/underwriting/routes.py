"""Underwriting routes (2026-11-20), installed onto the app like the other modules.

Every route takes an RKB session, or a contractor account granted the
``underwriting`` tool. A contractor session sees and works only the files it
started, and a file that is not its own answers 404, the same as one that does
not exist.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from mangotree.api import data
from mangotree.api.auth import CurrentUwUser


def is_contractor(user: Dict[str, Any]) -> bool:
    """A contractor login that is LIMITED in underwriting (see ``store.CONTRACTOR_FULL_ACCESS``).
    Only a contractor that was given the underwriting tool reaches these routes at all."""
    from . import store
    return store.is_contractor(user)


class FactsBody(BaseModel):
    facts: Dict[str, Any] = {}
    arv_analysis_id: Optional[str] = None


class ItemBody(BaseModel):
    values: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    reason: Optional[str] = None


class ConfirmBody(BaseModel):
    password: str
    note: Optional[str] = None


class StateApproveBody(BaseModel):
    password: str
    drop: Optional[List[str]] = None     # extra lines the person approving does not want in the file


class DocumentBody(BaseModel):
    blocks: Optional[List[Dict[str, Any]]] = None
    approved: Optional[bool] = None
    restore: Optional[bool] = None


MAX_UPLOAD = 40 * 1024 * 1024
SUFFIXES = (".pdf", ".docx", ".doc", ".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")


def install(app, mongo, jobs) -> None:
    from mangotree.arv.rehab import SCOPE_TIERS
    from . import checklist as C, engine as E, newstate, reader, secret, states as S, store as st
    store = st.UwStore(mongo)
    app.state.uw = store
    newstate.load(mongo)     # states whose sheet Claude wrote and RKB approved work like the built-in four

    @app.get("/uw/states")
    def uw_states(user=CurrentUwUser):
        return data.clean({"items": newstate.listing(mongo)})

    @app.get("/uw/states/{code}")
    def uw_state(code: str, user=CurrentUwUser):
        row = mongo.db["uw_states"].find_one({"code": code.upper()}, {"_id": 0})
        if not row:
            raise HTTPException(404, "no sheet has been written for this state")
        return data.clean(row)

    @app.post("/uw/states/{code}/propose")
    def uw_state_propose(code: str, user=CurrentUwUser):
        """Have Claude read that state's rules and write its sheet (a job of a few minutes). Proposed only: nothing changes until RKB approves it."""
        if is_contractor(user):
            raise HTTPException(403, "Only RKB can have a new state written.")
        code = code.strip().upper()[:2]
        if code in newstate.BUILT_IN or len(code) != 2 or not code.isalpha():
            raise HTTPException(422, "that state is already set up, or is not a two-letter state")

        def run(job):
            row = newstate.propose(code, mongo, user, emit=lambda t: job.emit("status", {"text": t}))
            return {"code": code, "name": row["sheet"].get("name"), "extra_lines": len(row["sheet"].get("extra_lines") or []), "cost_usd": row["cost_usd"]}
        job = jobs.start("uw_state", {"code": code, "by": user["user_id"]}, run)
        return {"job_id": job.job_id, "code": code}

    @app.post("/uw/states/{code}/approve")
    def uw_state_approve(code: str, body: StateApproveBody, user=CurrentUwUser):
        """RKB approves a state's sheet, with the confirmation password. From then on the state works like the built-in four."""
        if is_contractor(user):
            raise HTTPException(403, "Only RKB can approve a state.")
        verdict = secret.check(mongo, body.password, user)
        if not verdict["ok"]:
            raise HTTPException(403, verdict["why"])
        try:
            row = newstate.approve(code, mongo, user, drop=body.drop)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return data.clean(row)

    def _own(user: Dict[str, Any], doc: Dict[str, Any]) -> bool:
        return not is_contractor(user) or doc.get("created_by") == user["user_id"]

    def _get(fid: str, user: Dict[str, Any]) -> Dict[str, Any]:
        doc = store.get(fid)
        if not doc or doc.get("deleted") or not _own(user, doc):
            raise HTTPException(404, "no such file")
        return doc

    def _out(doc: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
        d = data.clean(doc)
        # The file travels without the text of its documents: one document is fetched when it is opened.
        d["documents"] = [{k: v for k, v in x.items() if k not in ("blocks", "versions")} for x in d.get("documents") or []]
        d["events"] = (d.get("events") or [])[-60:]
        d["me"] = {"user_id": user.get("user_id"), "name": user.get("name"), "contractor": is_contractor(user), "can_sign": user.get("user_id") == st.SIGNER,
                   "can_confirm": not is_contractor(user), "confirm_password_set": secret.is_set(mongo)}
        return d

    def _arv_facts(aid: Optional[str], user: Dict[str, Any]) -> Dict[str, Any]:
        if not aid:
            return {}
        arv = app.state.arv.get(aid) if getattr(app.state, "arv", None) else None
        if not arv or (is_contractor(user) and arv.get("created_by") != user["user_id"]):
            raise HTTPException(404, "no such ARV file")
        return st.facts_from_arv(arv)

    def _do(fn, *args):
        try:
            return fn(*args)
        except st.Refused as exc:
            raise HTTPException(409, str(exc))

    @app.get("/uw/config")
    def uw_config(user=CurrentUwUser):
        """What the screens need to draw the forms: the states, the choices and the limits."""
        return {"states": [{"code": k, "name": v["name"], "instrument": v["instrument"], "foreclosure": v["foreclosure"], "licence_register": v["licence_register"],
                            "notes": v["notes"], "mechanics_lien": v["mechanics_lien"]} for k, v in S.STATES.items()],
                "md_counties": sorted(c.title() for c in S.MD_RECORDATION_PER_500),
                "owner_kinds": [{"key": k, "label": S.OWNER_LABEL[k]} for k in S.OWNER_KINDS],
                "lien_status": [{"key": k, "label": v} for k, v in E.LIEN_STATUS_LABEL.items()],
                "scopes": [{"key": k, "label": v["label"], "covers": v["covers"]} for k, v in SCOPE_TIERS.items()],
                "kinds": E.KIND_LABEL, "parties": [{"key": p, "label": C.PARTY_LABEL[p]} for p in C.PARTY_ORDER],
                "limits": {"arv": E.LIMIT, "markdown": E.MARKDOWN, "selling_cost": E.SELLING_COST, "funded_share": E.FUNDED_SHARE, "contingency": E.MIN_CONTINGENCY,
                           "rate": E.DEFAULT_RATE, "points": E.DEFAULT_POINTS, "term_months": E.DEFAULT_TERM}}

    @app.post("/uw/check")
    def uw_check(body: FactsBody, user=CurrentUwUser):
        """The quick check, without saving anything: facts in, the three tests and the verdict out."""
        facts = {**_arv_facts(body.arv_analysis_id, user), **st.clean_facts(body.facts)}
        a = E.assess(facts)
        return data.clean({"facts": facts, "assessment": a, "details_missing": E.details_missing(facts), "more_missing": E.more_missing(facts),
                           "items": [{"party": i["party"], "label": i["label"], "when": i["when"], "required": i["required"]} for i in C.build(facts, a)]})

    @app.get("/uw/files")
    def uw_files(user=CurrentUwUser):
        return data.clean({"items": store.list(created_by=user["user_id"] if is_contractor(user) else None), "can_delete": not is_contractor(user)})

    @app.get("/uw/arv-files")
    def uw_arv_files(user=CurrentUwUser):
        """The ARV files a new underwriting file can start from, newest first (a contractor sees its own)."""
        arv = getattr(app.state, "arv", None)
        rows = arv.list(limit=80, created_by=user["user_id"] if is_contractor(user) else None) if arv else []
        out = []
        for r in rows:
            if r.get("status") != "done":
                continue
            an, val, geo, subj = r.get("analysis") or {}, r.get("valuation") or {}, r.get("geocode") or {}, r.get("subject") or {}
            value = an.get("arv_opinion") or val.get("arv")
            if r.get("mode") == "as_is" or not value:
                continue
            out.append({"analysis_id": r["analysis_id"], "address": geo.get("formatted") or subj.get("address"), "state": geo.get("state"), "arv": value,
                        "as_is": an.get("as_is_opinion"), "sqft": subj.get("sqft"), "by": r.get("created_by_name"), "at": r.get("created_at")})
        return data.clean({"items": out})

    @app.post("/uw/files")
    def uw_create(body: FactsBody, user=CurrentUwUser):
        from_arv = _arv_facts(body.arv_analysis_id, user)
        facts = st.clean_facts(body.facts)
        if not (facts.get("address") or from_arv.get("address") or "").strip():
            raise HTTPException(422, "the property address is needed")
        return _out(st.create(store, facts, user, from_arv=from_arv), user)

    @app.get("/uw/files/{fid}")
    def uw_file(fid: str, user=CurrentUwUser):
        return _out(st.refresh(_get(fid, user)), user)

    @app.delete("/uw/files/{fid}")
    def uw_delete(fid: str, user=CurrentUwUser):
        """Take a file off the list. RKB only. The record stays in the database, marked deleted."""
        if is_contractor(user):
            raise HTTPException(403, "Only RKB can delete a file.")
        doc = store.save(_do(st.delete, _get(fid, user), user))
        return {"ok": True, "file_id": doc["file_id"]}

    @app.patch("/uw/files/{fid}")
    def uw_patch(fid: str, body: FactsBody, user=CurrentUwUser):
        doc = _get(fid, user)
        from_arv = _arv_facts(body.arv_analysis_id, user) if body.arv_analysis_id else None
        return _out(store.save(_do(st.set_facts, doc, st.clean_facts(body.facts), user, from_arv)), user)

    @app.patch("/uw/files/{fid}/items/{key}")
    def uw_item(fid: str, key: str, body: ItemBody, user=CurrentUwUser):
        doc = _get(fid, user)
        if body.values is not None:
            _do(st.fill, doc, key, body.values, user)
        if body.status:
            _do(st.mark, doc, key, body.status, user, body.reason)
        return _out(store.save(doc), user)

    @app.post("/uw/files/{fid}/items/{key}/confirm")
    def uw_confirm(fid: str, key: str, body: ConfirmBody, user=CurrentUwUser):
        """RKB confirms a line, with or without a paper. Always asks for RKB's confirmation
        password; a wrong one is refused and counted, and nothing on the file changes."""
        doc = _get(fid, user)
        if is_contractor(user):
            raise HTTPException(403, "Only RKB can confirm a line.")
        verdict = secret.check(mongo, body.password, user)
        if not verdict["ok"]:
            st.log(doc, user, "A confirmation was refused: %s" % verdict["why"], item=key)
            store.save(doc)
            raise HTTPException(403, verdict["why"])
        return _out(store.save(_do(st.confirm, doc, key, user, body.note)), user)

    @app.post("/uw/files/{fid}/documents")
    def uw_documents_make(fid: str, user=CurrentUwUser):
        """Write (or write again) the document set from the file."""
        doc = _get(fid, user)
        return _out(store.save(_do(st.make_documents, doc, user)), user)

    @app.get("/uw/files/{fid}/documents/{key}")
    def uw_document_get(fid: str, key: str, user=CurrentUwUser):
        doc = _get(fid, user)
        return data.clean(_do(st.document_of, doc, key))

    @app.get("/uw/files/{fid}/documents/{key}/word")
    def uw_document_word(fid: str, key: str, user=CurrentUwUser):
        from . import documents as D
        doc = _get(fid, user)
        d = _do(st.document_of, doc, key)
        name = re.sub(r"[^A-Za-z0-9]+", "-", "%s %s" % ((doc.get("facts") or {}).get("address", "").split(",")[0], d["title"])).strip("-") + ".docx"
        return Response(D.to_docx(d), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": 'attachment; filename="%s"' % name})

    @app.patch("/uw/files/{fid}/documents/{key}")
    def uw_document(fid: str, key: str, body: DocumentBody, user=CurrentUwUser):
        """Correct a document's wording (RKB), take a correction back, or tick it approved (Rakesh Sir only)."""
        doc = _get(fid, user)
        if body.blocks is not None:
            _do(st.edit_document, doc, key, body.blocks, user)
        if body.restore:
            _do(st.restore_document, doc, key, user)
        if body.approved is not None:
            _do(st.approve_document, doc, key, user, body.approved)
        return {**_out(store.save(doc), user), "document": data.clean(st.document_of(doc, key))}

    @app.post("/uw/files/{fid}/items/{key}/upload")
    async def uw_upload(fid: str, key: str, files: List[UploadFile] = File(...), user=CurrentUwUser):
        """Store the papers at once; read each against the item's checks in a job."""
        from mangotree.arv.documents import sha256
        from mangotree.storage.objectstore import get_object_store
        doc = _get(fid, user)
        item = _do(st.item_of, doc, key)
        if key == "identity":
            raise HTTPException(409, "Identity papers go to the attorney's office, not here.")
        payloads = []
        for f in files[:10]:
            blob = await f.read()
            name = f.filename or "upload"
            suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if suffix not in SUFFIXES:
                raise HTTPException(422, "%s: this kind of file cannot be read (%s)" % (name, suffix or "no extension"))
            if len(blob) > MAX_UPLOAD:
                raise HTTPException(422, "%s is larger than 40 MB" % name)
            sha = sha256(blob)
            get_object_store().put(sha, blob, name, {"kind": "uw_paper", "file_id": fid, "item": key, "uploaded_by": user.get("user_id") or ""})
            rec = {"sha256": sha, "filename": name, "size": len(blob), "content_type": f.content_type, "uploaded_by": user.get("user_id"),
                   "uploaded_by_name": user.get("name"), "uploaded_at": data.clean(st._now())}
            _do(st.add_file, doc, key, rec, user)
            payloads.append((rec, blob))
        if not payloads:
            raise HTTPException(422, "no files")
        store.save(doc)
        facts, principal = dict(doc["facts"]), doc["assessment"]["money"]["principal"]

        def run(job):
            read = 0
            for rec, blob in payloads:
                job.emit("status", {"text": "Reading %s…" % rec["filename"]})
                reading, error = None, None
                try:
                    ext = reader.extract(blob, rec["filename"])
                    reading = reader.read(item=item, facts=facts, principal=principal, filename=rec["filename"], text=ext["text"], images=ext["images"])
                    read += 1
                except Exception as exc:   # a paper that cannot be read is said so on the item; the job itself does not fail
                    error = ("%s: %s" % (type(exc).__name__, exc))[:300]
                fresh = store.get(fid)
                if fresh:
                    store.save(st.add_reading(fresh, key, rec["sha256"], reading, error))
                job.emit("partial", {"key": "reading", "value": {"item": key, "sha256": rec["sha256"], "passed": bool(reading and reading["passed"]), "error": error}})
            return {"read": read, "files": len(payloads)}
        job = jobs.start("uw_read", {"file_id": fid, "item": key, "by": user["user_id"], "files": len(payloads)}, run)
        return {**_out(doc, user), "job_id": job.job_id}

    @app.get("/uw/files/{fid}/papers/{sha}")
    def uw_paper(fid: str, sha: str, user=CurrentUwUser):
        from mangotree.storage.objectstore import get_object_store
        doc = _get(fid, user)
        rec = next((f for i in doc.get("items") or [] for f in i.get("files") or [] if f.get("sha256") == sha), None)
        if not rec:
            raise HTTPException(404, "no such paper on this file")
        blob = get_object_store().get(sha)
        if blob is None:
            raise HTTPException(404, "the paper is not in the store")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", rec.get("filename") or sha)
        return Response(blob, media_type=rec.get("content_type") or "application/octet-stream", headers={"Content-Disposition": 'inline; filename="%s"' % safe})

    @app.post("/uw/files/{fid}/sign")
    def uw_sign(fid: str, user=CurrentUwUser):
        doc = _get(fid, user)
        return _out(store.save(_do(st.sign, doc, user)), user)
