"""In-process job runner with live event streams.

An answer takes five to ten minutes. The request that starts it returns a job id
immediately; the page subscribes to the job's SSE stream and watches the agent
work — every search, the sufficiency gate, the second reader, the verdict — and
receives the final answer as the last event. Reconnecting replays everything the
job has emitted so far, so a refresh never loses the trace.

Jobs are also mirrored to Mongo (``jobs``) so their status survives a restart
even though the running thread does not.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "as_dict"):
        return _jsonable(obj.as_dict())
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


class Job:
    def __init__(self, kind: str, meta: Dict[str, Any]):
        self.job_id = uuid.uuid4().hex[:16]
        self.kind = kind
        self.meta = meta
        self.status = "queued"
        self.created_at = time.time()
        self.events: List[Dict[str, Any]] = []
        self.subscribers: List[queue.Queue] = []
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        #: Set by a cancel request. The worker checks it and drops its output.
        self.cancelled = False
        #: The answer's BudgetTracker, when the job is an answer, so a cancel
        #: can ask the agent to stop at its next turn instead of running on.
        self.budget = None

    def emit(self, kind: str, payload: Dict[str, Any]) -> None:
        ev = {"seq": len(self.events) + 1, "t": round(time.time() - self.created_at, 1), "kind": kind, "data": _jsonable(payload)}
        with self._lock:
            self.events.append(ev)
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(ev)
            except Exception:
                pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for ev in self.events:
                q.put_nowait(ev)
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)


class JobRunner:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.jobs: Dict[str, Job] = {}
        self.coll = mongo.db["jobs"]
        self.coll.create_index("job_id", unique=True, name="ux_job_id")
        self.coll.create_index([("created_at", -1)], name="ix_job_created")

    def start(self, kind: str, meta: Dict[str, Any], fn: Callable[[Job], Dict[str, Any]]) -> Job:
        job = Job(kind, meta)
        self.jobs[job.job_id] = job
        self.coll.insert_one({"job_id": job.job_id, "kind": kind, "meta": _jsonable(meta), "status": "queued",
                              "created_at": datetime.now(timezone.utc)})

        def run() -> None:
            job.status = "running"
            self.coll.update_one({"job_id": job.job_id}, {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}})
            job.emit("job", {"status": "running"})
            try:
                result = fn(job)
                job.result = _jsonable(result)
                job.status = "done"
                job.emit("result", job.result)
                self.coll.update_one({"job_id": job.job_id}, {"$set": {"status": "done", "finished_at": datetime.now(timezone.utc),
                                                                        "result_summary": {k: job.result.get(k) for k in ("headline", "verdict", "elapsed_ms") if isinstance(job.result, dict)}}})
            except Exception as exc:
                logger.exception("job %s failed", job.job_id)
                job.error = f"{type(exc).__name__}: {exc}"[:400]
                job.status = "failed"
                job.emit("error", {"error": job.error})
                self.coll.update_one({"job_id": job.job_id}, {"$set": {"status": "failed", "error": job.error, "finished_at": datetime.now(timezone.utc)}})
            finally:
                job.emit("end", {"status": job.status})

        threading.Thread(target=run, name=f"job-{job.job_id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.cancelled = True
        if job.budget is not None:
            job.budget.interrupt_requested = True
        job.emit("status", {"text": "Cancelled by user."})
        self.coll.update_one({"job_id": job_id}, {"$set": {"cancel_requested_at": datetime.now(timezone.utc)}})
        return True

    def sweep_orphans(self) -> int:
        """At startup: jobs the previous process left 'running' can never finish.

        Their event streams are gone with the process. Marking them failed lets the
        chat show 'the server restarted — ask again' instead of a spinner that
        never stops."""
        r = self.coll.update_many({"status": {"$in": ["queued", "running"]}},
                                  {"$set": {"status": "failed", "error": "server restarted before this finished — please ask again",
                                            "finished_at": datetime.now(timezone.utc)}})
        return r.modified_count

    def sse(self, job: Job):
        """Generator of SSE frames; ends when the job ends."""
        q = job.subscribe()
        try:
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    if job.status in ("done", "failed") and q.empty():
                        break
                    continue
                yield f"id: {ev['seq']}\nevent: {ev['kind']}\ndata: {json.dumps(ev)}\n\n"
                if ev["kind"] == "end":
                    break
        finally:
            job.unsubscribe(q)
