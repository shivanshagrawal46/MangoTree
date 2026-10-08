"""Permit register routes: the board across properties, one property's
permits, and a rebuild (a job, so the UI can watch it)."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.retrieve import config as cfg

from . import data
from .auth import CurrentUser


def install(app, mongo, jobs) -> None:
    from mangotree.permits.register import PermitRegister
    reg = PermitRegister(mongo)
    app.state.permits = reg

    @app.get("/permits")
    def permits_board(user=CurrentUser):
        return data.clean(reg.board(cfg.analysis_property_ids()))

    @app.get("/properties/{pid}/permits")
    def property_permits(pid: str, user=CurrentUser):
        if pid not in PROPERTY_INDEX:
            raise HTTPException(404, f"unknown property {pid}")
        return data.clean({"items": reg.for_property(pid)})

    @app.post("/permits/rebuild")
    def permits_rebuild(user=CurrentUser):
        if user.get("role") != "ceo":
            raise HTTPException(403, "Rakesh Sir only")
        if jobs.active_of_kind("permits"):
            raise HTTPException(409, "a rebuild is already running")

        def run(job):
            job.emit("status", {"text": "Reading permit exports, inspection notices, result screenshots and what people said…"})
            out: Dict[str, Any] = reg.build()
            job.emit("status", {"text": f"{out['permits']} permits on the register; {out.get('placed', {}).get('records', 0)} unplaced records placed by permit number"})
            return out
        job = jobs.start("permits", {"by": user["user_id"]}, run)
        return {"job_id": job.job_id}
