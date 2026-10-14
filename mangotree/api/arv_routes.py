"""ARV routes (2026-10-14): start an analysis as a job, read it, edit any input
and get the deterministic half recomputed at once, add or toggle comps, ask
the analyst a follow-up, export. Installed onto the app like the other route
modules; every route takes an RKB session."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import Body, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from mangotree.config.registry import PROPERTY_INDEX

from . import data
from .auth import CurrentUser


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

    def _get(aid: str) -> Dict[str, Any]:
        doc = store.get(aid)
        if not doc:
            raise HTTPException(404, "no such analysis")
        return doc

    @app.get("/arv/status")
    def arv_status(user=CurrentUser):
        from mangotree.arv.engine import CONDITION_LABEL, DEFAULT_ASSUMPTIONS, PROPERTY_TYPES
        from mangotree.arv.rehab import DEFAULT_ADDERS, LINE_ITEMS, SCOPE_TIERS, STATE_COST_INDEX
        return {"providers": cfg.providers_status(), "doc_types": documents_mod.DOC_TYPES, "defaults": {"assumptions": DEFAULT_ASSUMPTIONS, "adders": DEFAULT_ADDERS,
                "radius_mi": cfg.COMP_RADIUS_MILES, "months": cfg.COMP_MONTHS, "target": cfg.COMP_TARGET},
                "scopes": {k: {kk: vv for kk, vv in v.items()} for k, v in SCOPE_TIERS.items()}, "line_items": [{"key": l["key"], "label": l["label"]} for l in LINE_ITEMS],
                "conditions": CONDITION_LABEL, "property_types": list(PROPERTY_TYPES), "state_cost_index": STATE_COST_INDEX,
                "properties": [{"property_id": p.property_id, "address": "%s, %s, %s %s" % (p.canonical_address, p.city, p.state, p.postal)} for p in PROPERTY_INDEX.values()]}

    @app.get("/arv/geocode")
    def arv_geocode(q: str, user=CurrentUser):
        try:
            return geocode(q)
        except GeocodeError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/arv/analyses")
    def arv_list(limit: int = 50, property_id: Optional[str] = None, user=CurrentUser):
        return data.clean({"items": store.list(limit=min(max(limit, 1), 200), property_id=property_id)})

    @app.post("/arv/analyses")
    def arv_create(body: CreateBody, user=CurrentUser):
        addr = (body.subject or {}).get("address") or ""
        if len(addr.strip()) < 6:
            raise HTTPException(422, "Enter the property address.")
        pid = body.property_id if body.property_id in PROPERTY_INDEX else None
        doc = pipeline.new_document(subject=body.subject, assumptions=body.assumptions, rehab=body.rehab, settings=body.settings, user=user,
                                    property_id=pid, label=body.label)
        for did in body.document_ids[:40]:
            rec = docs_coll.find_one({"doc_id": did, "$or": [{"analysis_id": None}, {"analysis_id": doc["analysis_id"]}]}, {"_id": 0})
            if rec:
                docs_coll.update_one({"doc_id": did}, {"$set": {"analysis_id": doc["analysis_id"]}})
                _attach(doc, rec)
        store.create(doc)

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
    def arv_get(aid: str, user=CurrentUser):
        return data.clean(_get(aid))

    @app.delete("/arv/analyses/{aid}")
    def arv_delete(aid: str, user=CurrentUser):
        if not store.delete(aid):
            raise HTTPException(404, "no such analysis")
        docs_coll.delete_many({"analysis_id": aid})   # the document records go with the file; the bytes stay in the object store by hash
        return {"ok": True}

    @app.patch("/arv/analyses/{aid}")
    def arv_patch(aid: str, body: PatchBody, user=CurrentUser):
        doc = _get(aid)
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
    def arv_add_comp(aid: str, body: CompBody, user=CurrentUser):
        doc = _get(aid)
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
    def arv_delete_comp(aid: str, comp_id: str, user=CurrentUser):
        doc = _get(aid)
        doc["comps"] = [c for c in doc.get("comps") or [] if c["comp_id"] != comp_id]
        pipeline.recompute(doc)
        store.save(doc)
        return data.clean(doc)

    @app.post("/arv/analyses/{aid}/rerun")
    def arv_rerun(aid: str, body: RerunBody = Body(default=RerunBody()), user=CurrentUser):
        doc = _get(aid)
        if jobs.active_of_kind("arv") and any(j.meta.get("analysis_id") == aid for j in jobs.jobs.values() if j.kind == "arv" and j.status in ("queued", "running")):
            raise HTTPException(409, "this analysis is already running")
        stages = [s for s in body.stages if s in ("geocode", "records", "market", "comps", "analyst")] or ["analyst"]

        def run(job):
            out = pipeline.run(doc, store, lambda k, p: job.emit(k, p), stages=stages)
            return {"analysis_id": out["analysis_id"], "status": out["status"], "headline": (out.get("analysis") or {}).get("headline")}
        job = jobs.start("arv", {"analysis_id": aid, "by": user["user_id"], "stages": stages}, run)
        doc["job_id"] = job.job_id
        doc["status"] = "running"
        store.patch(aid, {"job_id": job.job_id, "status": "running"})
        return {"analysis_id": aid, "job_id": job.job_id}

    @app.post("/arv/analyses/{aid}/chat")
    def arv_chat(aid: str, body: ChatBody, user=CurrentUser):
        from mangotree.arv import analyst
        doc = _get(aid)
        q = body.question.strip()
        if not q:
            raise HTTPException(422, "ask something")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        doc.setdefault("chat", []).append({"role": "user", "content": q, "by": user["user_id"], "by_name": user.get("name"), "at": now})
        store.save(doc)

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
                                   address: Optional[str] = Form(None), user=CurrentUser):
        """Store the files at once and read them in a job (Claude Opus 5.5 per
        document). Without analysis_id the records are staged for the intake
        wizard, which passes their ids to POST /arv/analyses; with one they are
        attached to that analysis as each read lands."""
        target = _get(analysis_id) if analysis_id else None
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
                        pipeline.recompute(fresh)
                        store.save(fresh)
                job.emit("partial", {"key": "document", "value": d})
                done.append(d)
            return {"documents": done, "read": sum(1 for d in done if d["status"] == "done"), "failed": sum(1 for d in done if d["status"] == "failed")}
        job = jobs.start("arv_docs", {"analysis_id": (target or {}).get("analysis_id"), "by": user["user_id"], "files": len(payloads)}, run)
        return data.clean({"job_id": job.job_id, "documents": [documents_mod.digest(rec) for rec, _ in payloads]})

    @app.get("/arv/documents/{doc_id}")
    def arv_document(doc_id: str, user=CurrentUser):
        rec = docs_coll.find_one({"doc_id": doc_id}, {"_id": 0})
        if not rec:
            raise HTTPException(404, "no such document")
        return data.clean(documents_mod.digest(rec))

    @app.get("/arv/documents/{doc_id}/file")
    def arv_document_file(doc_id: str, user=CurrentUser):
        from mangotree.storage.objectstore import get_object_store
        rec = docs_coll.find_one({"doc_id": doc_id}, {"_id": 0})
        if not rec or not rec.get("sha256"):
            raise HTTPException(404, "no such document")
        blob = get_object_store().get(rec["sha256"])
        if blob is None:
            raise HTTPException(404, "file is not in the store")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", rec.get("filename") or doc_id)
        return Response(blob, media_type=rec.get("content_type") or "application/octet-stream", headers={"Content-Disposition": 'inline; filename="%s"' % safe})

    @app.delete("/arv/analyses/{aid}/documents/{doc_id}")
    def arv_detach_document(aid: str, doc_id: str, user=CurrentUser):
        doc = _get(aid)
        doc["documents"] = [x for x in (doc.get("documents") or []) if x.get("doc_id") != doc_id]
        docs_coll.delete_one({"doc_id": doc_id, "analysis_id": aid})
        pipeline.recompute(doc)
        store.save(doc)
        return data.clean(doc)

    @app.post("/arv/analyses/{aid}/documents/{doc_id}/apply")
    def arv_apply_document(aid: str, doc_id: str, body: ApplyBody, user=CurrentUser):
        """Take what a document says into the file: its facts about the house,
        its priced repair items as the rehab budget, or its comparable sales."""
        doc = _get(aid)
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
            doc["rehab"] = {**(doc.get("rehab") or {}), "mode": "lines", "lines": lines, "source_doc_id": doc_id, "source": rec.get("filename")}
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
    def arv_pdf(aid: str, user=CurrentUser):
        doc = _get(aid)
        name = re.sub(r"[^A-Za-z0-9]+", "-", (doc.get("geocode") or {}).get("formatted") or doc["subject"].get("address") or aid).strip("-")[:60]
        return Response(report.pdf(doc, user_name=user.get("name") or ""), media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="ARV-%s.pdf"' % name})

    @app.get("/arv/analyses/{aid}/calculator.xlsx")
    def arv_xlsx(aid: str, user=CurrentUser):
        doc = _get(aid)
        name = re.sub(r"[^A-Za-z0-9]+", "-", (doc.get("geocode") or {}).get("formatted") or doc["subject"].get("address") or aid).strip("-")[:60]
        return Response(report.xlsx(doc), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="MAO-Rehab-Calculator-%s.xlsx"' % name})
