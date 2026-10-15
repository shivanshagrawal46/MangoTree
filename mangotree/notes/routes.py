"""Notes analysis routes (2026-10-16): list the analyses on file and read one.
Each analysis is a JSON dataset in mangotree/notes/datasets built by
scripts/notes_analysis/make_dataset.py (ZIP, metro and state price history,
five-year scenarios, rates, the note-by-note table and the narrative).
Installed onto the app like the other route modules; every route takes an RKB
session. Nothing else in the API is touched."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

from mangotree.api.auth import CurrentUser

DATASETS = Path(__file__).resolve().parent / "datasets"
_CACHE: Dict[str, Dict[str, Any]] = {}
_MTIME: Dict[str, float] = {}
_LOCK = threading.Lock()


def _load(path: Path) -> Dict[str, Any]:
    key = path.stem
    mtime = os.path.getmtime(path)
    with _LOCK:
        if key in _CACHE and _MTIME.get(key) == mtime:
            return _CACHE[key]
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc.setdefault("id", key)
        _CACHE[key] = doc
        _MTIME[key] = mtime
        return doc


def list_datasets() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(DATASETS.glob("*.json")):
        d = _load(p)
        out.append({"id": d["id"], "title": d.get("title"), "as_of": d.get("as_of"), "data_through": d.get("data_through"),
                    "source_file": d.get("source_file"), "summary": d.get("summary"), "zips": len(d.get("zips") or []), "headline": (d.get("narrative") or {}).get("headline")})
    out.sort(key=lambda x: x.get("as_of") or "", reverse=True)
    return out


def get_dataset(analysis_id: str) -> Dict[str, Any]:
    safe = "".join(ch for ch in analysis_id if ch.isalnum() or ch in "_-")
    p = DATASETS / (safe + ".json")
    if not p.exists():
        raise HTTPException(404, "No notes analysis with that id")
    return _load(p)


def install(app, mongo=None) -> None:
    @app.get("/notes/analyses")
    def notes_list(user=CurrentUser):
        return {"items": list_datasets()}

    @app.get("/notes/analyses/{analysis_id}")
    def notes_get(analysis_id: str, user=CurrentUser):
        return get_dataset(analysis_id)
