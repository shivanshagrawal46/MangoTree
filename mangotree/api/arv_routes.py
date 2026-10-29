"""ARV routes (2026-10-14): start an analysis as a job, read it, edit any input
and get the deterministic half recomputed at once, add or toggle comps, ask
the analyst a follow-up, export. Installed onto the app like the other route
modules; every route takes an RKB session — or, since 2026-10-27, a contractor
account that has been granted the ``arv`` tool (Wes). A contractor session is
scoped to the files it created itself: it cannot list, open, edit or delete
anyone else's, cannot link a file to a portfolio property outside its scope,
and every search it starts is stamped with the account and organisation and
written to the contractor audit trail so RKB can see who searched what."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import Body, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from mangotree.config.registry import PROPERTY_INDEX

from . import data
from .auth import CurrentArvUser, is_contractor


class CreateBody(BaseModel):
    subject: Dict[str, Any]
    assumptions: Dict[str, Any] = {}
    rehab: Dict[str, Any] = {}
    settings: Dict[str, Any] = {}
    property_id: Optional[str] = None
    label: Optional[str] = None
    document_ids: List[str] = []   # staged uploads (POST /arv/documents) to attach before the run


class ApplyBody(BaseModel):
    what: str                      # facts | rehab | comps
    keys: Optional[List[str]] = None


class PatchBody(BaseModel):
    subject: Optional[Dict[str, Any]] = None
    assumptions: Optional[Dict[str, Any]] = None
    rehab: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None
    comps: Optional[List[Dict[str, Any]]] = None   # [{comp_id, included?, condition?, manual_adjustments?, ...field overrides}]
    answers: Optional[List[Dict[str, Any]]] = None
    label: Optional[str] = None


class CompBody(BaseModel):
    comp: Dict[str, Any]


class ChatBody(BaseModel):
    question: str


class RerunBody(BaseModel):
    stages: List[str] = ["comps", "analyst"]


_COMP_EDITABLE = ("address", "sale_price", "sale_date", "price_basis", "listing_price", "dom", "property_type", "beds", "baths", "sqft", "lot_sqft",
                  "year_built", "garage_spaces", "basement", "pool", "condition", "url", "note", "included", "manual_adjustments", "lat", "lon")


def install(app, mongo, jobs) -> None:
    from mangotree.arv import config as cfg, documents as documents_mod, pipeline, providers, report
    from mangotree.arv.geocode import GeocodeError, geocode
    from mangotree.arv.store import ArvStore
    store = ArvStore(mongo)
    app.state.arv = store
    # A run that was in flight when the server last stopped can never finish: say so instead of showing "running" forever.
    orphaned = store.coll.update_many({"status": {"$in": ["running", "queued"]}},
                                      {"$set": {"status": "failed", "error": "The server restarted during this run. Re-run the stages that did not finish."}}).modified_count
    if orphaned:
        from mangotree.core.logging import logger
        logger.warning("arv: %d analysis run(s) were interrupted by a restart and marked failed", orphaned)
    docs_coll = mongo.db["arv_documents"]
    docs_coll.create_index("doc_id", unique=True, name="ux_arv_doc_id")
    docs_coll.create_index("analysis_id", sparse=True, name="ix_arv_doc_analysis")

    def _attach(doc: Dict[str, Any], rec: Dict[str, Any]) -> None:
        """Put a document's digest on the analysis (replacing an earlier copy)."""
        d = documents_mod.digest(rec)
        d["analysis_id"] = doc["analysis_id"]
        doc["documents"] = [x for x in (doc.get("documents") or []) if x.get("doc_id") != rec["doc_id"]] + [d]

    def _adopt_bid(doc: Dict[str, Any], rec: Dict[str, Any]) -> bool:
        """A contractor bid on file is the budget, unless a person already chose one.
        The tier estimate is a placeholder; a priced scope replaces it the moment it is read.
        A budget someone set by hand (lump sum, edited lines) or one already following a
        bid is left alone; the Documents tab can switch to another bid at any time."""
        rehab = doc.get("rehab") or {}
        is_bid = ((rec.get("extracted") or {}).get("doc_type") == "contractor_bid")
        if rehab.get("source_doc_id"):
            # Already following a document: only a contractor's own bid may replace a budget that was
            # adopted from some other kind of document (an investor pack quoting the bid), never a choice a person made.
            prev = next((x for x in (doc.get("documents") or []) if x.get("doc_id") == rehab["source_doc_id"]), None)
            prev_type = ((prev or {}).get("extracted") or {}).get("doc_type")
            if not (rehab.get("auto") and is_bid and prev_type != "contractor_bid"):
                return False
        elif (rehab.get("mode") or "tier") != "tier":
            return False
        lines = documents_mod.rehab_lines_from(rec)
        if not lines:
            return False
        doc["rehab"] = {**rehab, "mode": "lines", "lines": lines, "source_doc_id": rec["doc_id"], "source": rec.get("filename"), "auto": True}
        return True

    def _own(user: Dict[str, Any], doc: Dict[str, Any]) -> bool:
        return not is_contractor(user) or doc.get("created_by") == user["user_id"]

    def _get(aid: str, user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        doc = store.get(aid)
        # A contractor asking for a file that is not its own gets the same 404 as
        # a file that does not exist: no confirmation that the id is real.
        if not doc or (user is not None and not _own(user, doc)):
            raise HTTPException(404, "no such analysis")
        return doc

    def _audit(user: Dict[str, Any], request: Optional[Request], action: str, doc: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> None:
        """Contractor use of the ARV system goes on the same hash-chained trail as
        the portal replies, so Rakesh Sir's desk shows "searched by Wes" next to
        everything else Wes did. RKB users are not logged here (their files
        already carry created_by)."""
        if not is_contractor(user):
            return
        try:
            from mangotree.portal import sheets as S
            ip = None
            if request is not None:
                fwd = request.headers.get("x-forwarded-for")
                ip = (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else None)
            S.record_event(mongo, user=user, action=action, ip=ip, property_id=doc.get("property_id"),
                           text=(doc.get("geocode") or {}).get("formatted") or (doc.get("subject") or {}).get("address"),
                           extra={"analysis_id": doc.get("analysis_id"), "tool": "arv", **(extra or {})})
        except Exception as exc:   # the audit row must never block the search itself
            from mangotree.core.logging import logger
            logger.warning("arv: could not record contractor audit event (%s): %s", action, exc)

    @app.get("/arv/status")
    def arv_status(user=CurrentArvUser):
        from mangotree.arv.engine import CONDITION_LABEL, DEFAULT_ASSUMPTIONS, PROPERTY_TYPES
        from mangotree.arv.rehab import DEFAULT_ADDERS, LINE_ITEMS, SCOPE_TIERS, STATE_COST_INDEX
        # A contractor may link a file only to the portfolio properties in its scope;
        # the rest of the portfolio is not listed to it.
        scope = set(user.get("property_ids") or []) if is_contractor(user) else None
        props = [p for p in PROPERTY_INDEX.values() if scope is None or p.property_id in scope]
        return {"providers": cfg.providers_status(), "doc_types": documents_mod.DOC_TYPES, "defaults": {"assumptions": DEFAULT_ASSUMPTIONS, "adders": DEFAULT_ADDERS,
                "radius_mi": cfg.COMP_RADIUS_MILES, "months": cfg.COMP_MONTHS, "target": cfg.COMP_TARGET},
                "scopes": {k: {kk: vv for kk, vv in v.items()} for k, v in SCOPE_TIERS.items()}, "line_items": [{"key": l["key"], "label": l["label"]} for l in LINE_ITEMS],
                "conditions": CONDITION_LABEL, "property_types": list(PROPERTY_TYPES), "state_cost_index": STATE_COST_INDEX,
                "properties": [{"property_id": p.property_id, "address": "%s, %s, %s %s" % (p.canonical_address, p.city, p.state, p.postal)} for p in props],
                "viewer": {"side": "contractor" if is_contractor(user) else "rkb", "name": user.get("name"), "org_name": user.get("org_name")}}

    @app.get("/arv/geocode")
    def arv_geocode(q: str, user=CurrentArvUser):
        try:
            return geocode(q)
        except GeocodeError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/arv/analyses")
    def arv_list(limit: int = 50, property_id: Optional[str] = None, user=CurrentArvUser):
        items = store.list(limit=min(max(limit, 1), 200), property_id=property_id,
                           created_by=user["user_id"] if is_contractor(user) else None)
        return data.clean({"items": items})

    @app.post("/arv/analyses")
    def arv_create(body: CreateBody, request: Request, user=CurrentArvUser):
        addr = (body.subject or {}).get("address") or ""
        if len(addr.strip()) < 6:
            raise HTTPException(422, "Enter the property address.")
        pid = body.property_id if body.property_id in PROPERTY_INDEX else None
        if pid and is_contractor(user) and pid not in (user.get("property_ids") or []):
            pid = None
        doc = pipeline.new_document(subject=body.subject, assumptions=body.assumptions, rehab=body.rehab, settings=body.settings, user=user,
                                    property_id=pid, label=body.label)
        if is_contractor(user):
            # Stamped on the file itself, so every list, PDF and workbook that
            # shows "by ..." reads "Wes Stone (ROI Blocks)" without a lookup.
            doc["created_side"] = "contractor"
            doc["created_org"] = user.get("org")
            doc["created_org_name"] = user.get("org_name")
            doc["created_by_name"] = "%s (%s)" % (user.get("name") or user["user_id"], user.get("org_name") or "contractor")
        for did in body.document_ids[:40]:
            rec = docs_coll.find_one({"doc_id": did, "$or": [{"analysis_id": None}, {"analysis_id": doc["analysis_id"]}]}, {"_id": 0})
            if rec:
                if is_contractor(user) and rec.get("uploaded_by") not in (None, user["user_id"]):
                    continue   # a staged upload belongs to whoever staged it
                docs_coll.update_one({"doc_id": did}, {"$set": {"analysis_id": doc["analysis_id"]}})
                _attach(doc, rec)
                _adopt_bid(doc, rec)
        store.create(doc)
        _audit(user, request, "arv_search", doc, {"label": doc.get("label")})

        def run(job):
            def emit(kind, payload):
                job.emit(kind, payload)
            out = pipeline.run(doc, store, emit)
            return {"analysis_id": out["analysis_id"], "status": out["status"], "headline": (out.get("analysis") or {}).get("headline"),
                    "arv": (out.get("valuation") or {}).get("arv")}
        job = jobs.start("arv", {"analysis_id": doc["analysis_id"], "by": user["user_id"], "address": addr}, run)
        doc["job_id"] = job.job_id          # the run saves this same dict, so the id survives every save
        store.patch(doc["analysis_id"], {"job_id": job.job_id})
        return {"analysis_id": doc["analysis_id"], "job_id": job.job_id}

    @app.get("/arv/analyses/{aid}")
    def arv_get(aid: str, user=CurrentArvUser):
        doc = _get(aid, user)
        # Files saved before the analyst's ARV became the file's ARV carry no
        # ``arv`` block and a max offer on the engine's number; one deterministic
        # recompute brings them in line (no model calls).
        if doc.get("status") == "done" and doc.get("analysis") and not doc.get("arv"):
            try:
                pipeline.recompute(doc)
                store.save(doc)
            except Exception:
                from mangotree.core.logging import logger
                logger.exception("arv backfill recompute failed for %s", aid)
        return data.clean(doc)

    @app.delete("/arv/analyses/{aid}")
    def arv_delete(aid: str, request: Request, user=CurrentArvUser):
        doc = _get(aid, user)
        if not store.delete(aid):
            raise HTTPException(404, "no such analysis")
        docs_coll.delete_many({"analysis_id": aid})   # the document records go with the file; the bytes stay in the object store by hash
        _audit(user, request, "arv_deleted", doc)
        return {"ok": True}

    @app.patch("/arv/analyses/{aid}")
    def arv_patch(aid: str, body: PatchBody, user=CurrentArvUser):
        doc = _get(aid, user)
        if body.subject is not None:
            merged = {**doc["subject"], **{k: v for k, v in body.subject.items()}}
            doc["subject"] = pipeline.clean_subject(merged)
            doc["subject"]["lat"], doc["subject"]["lon"] = (doc.get("geocode") or {}).get("lat"), (doc.get("geocode") or {}).get("lon")
            doc["subject_input"] = pipeline.clean_subject({**doc.get("subject_input", {}), **body.subject})
        if body.assumptions is not None:
            doc["assumptions"] = {**(doc.get("assumptions") or {}), **body.assumptions}
        if body.rehab is not None:
            doc["rehab"] = {**(doc.get("rehab") or {}), **body.rehab}
        if body.settings is not None:
            doc["settings"] = {**(doc.get("settings") or {}), **body.settings}
        if body.answers is not None:
            doc["answers"] = [{"question": str(a.get("question") or ""), "answer": str(a.get("answer") or "")} for a in body.answers if a.get("answer")]
        if body.label is not None:
            doc["label"] = body.label.strip()[:120] or None
        if body.comps:
            by_id = {c["comp_id"]: c for c in doc.get("comps") or []}
            for edit in body.comps:
                c = by_id.get(str(edit.get("comp_id")))
                if not c:
                    continue
                for k, v in edit.items():
                    if k in _COMP_EDITABLE:
                        c[k] = v
                c["edited_by"] = user["user_id"]
        pipeline.recompute(doc)
        store.save(doc)
        return data.clean(doc)

    @app.post("/arv/analyses/{aid}/comps")
    def arv_add_comp(aid: str, body: CompBody, user=CurrentArvUser):
        doc = _get(aid, user)
        c = providers.new_comp(**{k: v for k, v in (body.comp or {}).items() if k in _COMP_EDITABLE})
        c["source"] = "manual"
        c["included"] = True if c.get("included") is None else c["included"]
        c["added_by"] = user["user_id"]
        if c.get("lat") is None and c.get("address"):
            try:
                g = geocode(c["address"])
                c["lat"], c["lon"] = g["lat"], g["lon"]
            except Exception:
                pass
        doc["comps"] = providers.dedupe((doc.get("comps") or []) + [c], doc["subject"])
        pipeline.recompute(doc)
        store.save(doc)
        return data.clean(doc)

    @app.delete("/arv/analyses/{aid}/comps/{comp_id}")
    def arv_delete_comp(aid: str, comp_id: str, user=CurrentArvUser):
        doc = _get(aid, user)
        doc["comps"] = [c for c in doc.get("comps") or [] if c["comp_id"] != comp_id]
        pipeline.recompute(doc)
        store.save(doc)
        return data.clean(doc)

    @app.post("/arv/analyses/{aid}/rerun")
    def arv_rerun(aid: str, request: Request, body: RerunBody = Body(default=RerunBody()), user=CurrentArvUser):
        doc = _get(aid, user)
        if jobs.active_of_kind("arv") and any(j.meta.get("analysis_id") == aid for j in jobs.jobs.values() if j.kind == "arv" and j.status in ("queued", "running")):
            raise HTTPException(409, "this analysis is already running")
        stages = [s for s in body.stages if s in ("geocode", "records", "market", "comps", "shortlist", "analyst")] or ["analyst"]

        def run(job):
            out = pipeline.run(doc, store, lambda k, p: job.emit(k, p), stages=stages)
            return {"analysis_id": out["analysis_id"], "status": out["status"], "headline": (out.get("analysis") or {}).get("headline")}
        job = jobs.start("arv", {"analysis_id": aid, "by": user["user_id"], "stages": stages}, run)
        doc["job_id"] = job.job_id
        doc["status"] = "running"
        store.patch(aid, {"job_id": job.job_id, "status": "running"})
        _audit(user, request, "arv_rerun", doc, {"stages": stages})
        return {"analysis_id": aid, "job_id": job.job_id}

    @app.post("/arv/analyses/{aid}/chat")
    def arv_chat(aid: str, body: ChatBody, request: Request, user=CurrentArvUser):
        from mangotree.arv import analyst
        doc = _get(aid, user)
        q = body.question.strip()
        if not q:
            raise HTTPException(422, "ask something")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        doc.setdefault("chat", []).append({"role": "user", "content": q, "by": user["user_id"], "by_name": user.get("name"), "at": now,
                                           **({"by_org": user.get("org_name")} if is_contractor(user) else {})})
        store.save(doc)
        _audit(user, request, "arv_question", doc, {"question": q[:300]})

        def run(job):
            history = [{"role": m["role"], "content": m["content"]} for m in doc["chat"][:-1]]
            out = analyst.chat(doc, history, q, on_delta=lambda t: job.emit("delta", {"text": t}))
            doc["chat"].append({"role": "assistant", "content": out["answer"], "model": out["model"], "at": datetime.now(timezone.utc).isoformat(), "usage": out.get("usage")})
            doc["cost_usd"] = round(float(doc.get("cost_usd") or 0) + float((out.get("usage") or {}).get("total_cost_usd") or 0), 4)
            store.save(doc)
            return {"answer": out["answer"], "model": out["model"]}
        job = jobs.start("arv_chat", {"analysis_id": aid, "by": user["user_id"]}, run)
        return {"job_id": job.job_id}

    # ------------------------------------------------------------ documents
    @app.post("/arv/documents")
    async def arv_upload_documents(files: List[UploadFile] = File(...), analysis_id: Optional[str] = Form(None), hint: Optional[str] = Form(None),
                                   address: Optional[str] = Form(None), user=CurrentArvUser):
        """Store the files at once and read them in a job (Claude Opus 5.5 per
        document). Without analysis_id the records are staged for the intake
        wizard, which passes their ids to POST /arv/analyses; with one they are
        attached to that analysis as each read lands."""
        target = _get(analysis_id, user) if analysis_id else None
        payloads = []
        for f in files[:40]:
            data_bytes = await f.read()
            rec = documents_mod.new_record(filename=f.filename or "upload", size=len(data_bytes), content_type=f.content_type, user=user, hint=hint,
                                           analysis_id=target["analysis_id"] if target else None)
            docs_coll.insert_one(dict(rec))
            payloads.append((rec, data_bytes))
        if not payloads:
            raise HTTPException(422, "no files")
        subject_address = ((target or {}).get("geocode") or {}).get("formatted") if target else address

        def run(job):
            done = []
            for rec, data_bytes in payloads:
                job.emit("status", {"text": "Storing %s..." % rec["filename"]})
                out = documents_mod.ingest(data_bytes, filename=rec["filename"], content_type=rec.get("content_type"), user=user, hint=hint,
                                           subject_address=subject_address, emit=lambda t: job.emit("status", {"text": t}), record=rec)
                docs_coll.replace_one({"doc_id": out["doc_id"]}, {k: v for k, v in out.items() if k != "_id"}, upsert=True)
                d = documents_mod.digest(out)
                if target:
                    fresh = store.get(target["analysis_id"])
                    if fresh:
                        _attach(fresh, out)
                        if _adopt_bid(fresh, out):
                            job.emit("status", {"text": "%s: its priced scope of $%s is now the rehab budget" % (out["filename"], format(int(sum(l["amount"] for l in fresh["rehab"]["lines"])), ","))})
                        pipeline.recompute(fresh)
                        store.save(fresh)
                job.emit("partial", {"key": "document", "value": d})
                done.append(d)
            return {"documents": done, "read": sum(1 for d in done if d["status"] == "done"), "failed": sum(1 for d in done if d["status"] == "failed")}
        job = jobs.start("arv_docs", {"analysis_id": (target or {}).get("analysis_id"), "by": user["user_id"], "files": len(payloads)}, run)
        return data.clean({"job_id": job.job_id, "documents": [documents_mod.digest(rec) for rec, _ in payloads]})

    def _doc_rec(doc_id: str, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A document record, if this session may see it: RKB sees all; a
        contractor sees only what it uploaded or what sits on its own files."""
        rec = docs_coll.find_one({"doc_id": doc_id}, {"_id": 0})
        if rec and is_contractor(user) and rec.get("uploaded_by") != user["user_id"]:
            owner = store.get(rec["analysis_id"]) if rec.get("analysis_id") else None
            if not owner or owner.get("created_by") != user["user_id"]:
                return None
        return rec

    @app.get("/arv/documents/{doc_id}")
    def arv_document(doc_id: str, user=CurrentArvUser):
        rec = _doc_rec(doc_id, user)
        if not rec:
            raise HTTPException(404, "no such document")
        return data.clean(documents_mod.digest(rec))

    @app.get("/arv/documents/{doc_id}/file")
    def arv_document_file(doc_id: str, user=CurrentArvUser):
        from mangotree.storage.objectstore import get_object_store
        rec = _doc_rec(doc_id, user)
        if not rec or not rec.get("sha256"):
            raise HTTPException(404, "no such document")
        blob = get_object_store().get(rec["sha256"])
        if blob is None:
            raise HTTPException(404, "file is not in the store")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", rec.get("filename") or doc_id)
        return Response(blob, media_type=rec.get("content_type") or "application/octet-stream", headers={"Content-Disposition": 'inline; filename="%s"' % safe})

    @app.delete("/arv/analyses/{aid}/documents/{doc_id}")
    def arv_detach_document(aid: str, doc_id: str, user=CurrentArvUser):
        doc = _get(aid, user)
        doc["documents"] = [x for x in (doc.get("documents") or []) if x.get("doc_id") != doc_id]
        docs_coll.delete_one({"doc_id": doc_id, "analysis_id": aid})
        pipeline.recompute(doc)
        store.save(doc)
        return data.clean(doc)

    @app.post("/arv/analyses/{aid}/documents/{doc_id}/apply")
    def arv_apply_document(aid: str, doc_id: str, body: ApplyBody, user=CurrentArvUser):
        """Take what a document says into the file: its facts about the house,
        its priced repair items as the rehab budget, or its comparable sales."""
        doc = _get(aid, user)
        rec = next((x for x in (doc.get("documents") or []) if x.get("doc_id") == doc_id), None)
        if not rec or rec.get("status") != "done":
            raise HTTPException(404, "no readable document with that id on this analysis")
        ex = rec.get("extracted") or {}
        if body.what == "facts":
            facts = ex.get("subject_facts") or {}
            keys = [k for k in (body.keys or list(facts.keys())) if k in facts]
            subj = dict(doc["subject"])
            sources = dict(subj.get("fact_sources") or {})
            for k in keys:
                if k == "property_taxes_annual":
                    doc["assumptions"] = {**(doc.get("assumptions") or {}), "property_taxes_annual": facts[k], "property_taxes_basis": rec.get("filename")}
                    continue
                subj[k] = facts[k]
                sources[k] = rec.get("filename") or doc_id
            subj["fact_sources"] = sources
            doc["subject"] = pipeline.clean_subject(subj)
            doc["subject"]["lat"], doc["subject"]["lon"] = (doc.get("geocode") or {}).get("lat"), (doc.get("geocode") or {}).get("lon")
        elif body.what == "rehab":
            lines = documents_mod.rehab_lines_from(rec)
            if not lines:
                raise HTTPException(422, "this document has no priced repair items")
            doc["rehab"] = {**(doc.get("rehab") or {}), "mode": "lines", "lines": lines, "source_doc_id": doc_id, "source": rec.get("filename"), "auto": False}
        elif body.what == "comps":
            added = []
            for c in ex.get("comps") or []:
                if not c.get("address") or not c.get("sale_price"):
                    continue
                nc = providers.new_comp(address=c.get("address"), sale_price=c.get("sale_price"), sale_date=c.get("sale_date"), price_basis="closed", beds=c.get("beds"),
                                        baths=c.get("baths"), sqft=c.get("sqft"), lot_sqft=c.get("lot_sqft"), year_built=c.get("year_built"), distance_mi=c.get("distance_mi"),
                                        condition=c.get("condition"), note=("from %s" % rec.get("filename")) + (": " + c["note"] if c.get("note") else ""), included=True)
                nc["source"] = "document"
                nc["added_by"] = user["user_id"]
                try:
                    g = geocode(nc["address"])
                    nc["lat"], nc["lon"] = g["lat"], g["lon"]
                except Exception:
                    pass
                added.append(nc)
            if not added:
                raise HTTPException(422, "this document lists no comparable sales")
            doc["comps"] = providers.dedupe((doc.get("comps") or []) + added, doc["subject"])
        else:
            raise HTTPException(422, "what must be facts, rehab or comps")
        pipeline.recompute(doc)
        store.save(doc)
        return data.clean(doc)

    @app.get("/arv/analyses/{aid}/report.pdf")
    def arv_pdf(aid: str, user=CurrentArvUser):
        doc = _get(aid, user)
        name = re.sub(r"[^A-Za-z0-9]+", "-", (doc.get("geocode") or {}).get("formatted") or doc["subject"].get("address") or aid).strip("-")[:60]
        return Response(report.pdf(doc, user_name=user.get("name") or ""), media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="ARV-%s.pdf"' % name})

    @app.get("/arv/analyses/{aid}/calculator.xlsx")
    def arv_xlsx(aid: str, user=CurrentArvUser):
        doc = _get(aid, user)
        name = re.sub(r"[^A-Za-z0-9]+", "-", (doc.get("geocode") or {}).get("formatted") or doc["subject"].get("address") or aid).strip("-")[:60]
        return Response(report.xlsx(doc), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="MAO-Rehab-Calculator-%s.xlsx"' % name})
