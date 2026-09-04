"""Second pass for emails Opus 5 could not place alone — judged with their thread.

Segregation reads one email at a time. That is right for the common case and
wrong for the tail: a reply whose entire body is "Received." or "What time?"
carries no property signal, so it lands in the review queue even though the
conversation it belongs to was resolved on an earlier message.

The mechanical fix — copy the thread's property onto the reply — is tempting and
unsafe. Threads in this corpus regularly cover two deals at once; one observed
thread resolved to both Decatur and Varnum, and stamping both onto a one-line
reply would put a message into a property no evidence links it to.

So the thread is supplied to Opus 5 as *evidence* and the model decides, with
reasoning, exactly as it does everywhere else. The cost is one call per email in
the eligible tail, which is small against the main run.

Only emails whose thread has at least one resolved sibling are considered.
Everything else has nothing new to reason from and is left for a human.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.resolve.segregator import PropertySegregator
from mangotree.storage.mongo import Mongo

CONCURRENCY = 20

#: Sibling messages shown per email. The most recently dated resolved messages
#: carry the most relevant context, and a long thread would otherwise dominate
#: the prompt.
MAX_SIBLINGS = 4


@dataclass
class ThreadContextStats:
    candidates: int = 0
    eligible: int = 0
    called: int = 0
    resolved: int = 0
    still_unresolved: int = 0
    disagreed_with_thread: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    assignments: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            k: (dict(v) if isinstance(v, dict) else v)
            for k, v in self.__dict__.items()
        }


class ThreadContextRunner:
    def __init__(self, mongo: Mongo, api_key: str, *, model: Optional[str] = None):
        self.mongo = mongo
        self.segregator = PropertySegregator(api_key, model=model)
        self.stats = ThreadContextStats()
        self.run_id = f"threadctx-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"

    # ------------------------------------------------------------------
    def _candidates(self, limit: Optional[int]) -> List[dict]:
        """Unresolved emails that belong to a thread."""
        query = {
            "source_type": "email",
            "property_ids": [],
            "resolution_status": "needs_review",
            "thread_key": {"$nin": [None, ""]},
        }
        cursor = self.mongo.artifacts.find(
            query,
            {"sha256": 1, "subject": 1, "date": 1, "body_clean": 1,
             "participants": 1, "thread_key": 1, "property_candidates": 1},
        ).sort("date", 1)
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    def _siblings_for(self, candidates: List[dict]) -> Dict[str, List[dict]]:
        """Resolved messages per thread key — one query, not one per email."""
        keys = sorted({c["thread_key"] for c in candidates})
        by_thread: Dict[str, List[dict]] = {k: [] for k in keys}

        cursor = self.mongo.artifacts.find(
            {
                "thread_key": {"$in": keys},
                "source_type": "email",
                "property_ids": {"$ne": []},
            },
            {"sha256": 1, "subject": 1, "date": 1, "body_clean": 1,
             "participants": 1, "thread_key": 1, "property_ids": 1},
        ).sort("date", -1)

        for doc in cursor:
            bucket = by_thread.setdefault(doc["thread_key"], [])
            if len(bucket) < MAX_SIBLINGS:
                bucket.append(doc)
        return by_thread

    # ------------------------------------------------------------------
    @staticmethod
    def _payload(email: dict, siblings: List[dict]) -> dict:
        people = email.get("participants") or {}

        def joined(key: str) -> str:
            return ", ".join(people.get(key) or [])

        return {
            "sha256": email["sha256"],
            "subject": email.get("subject"),
            "date": str(email.get("date") or ""),
            "from": joined("from"),
            "to": joined("to"),
            "cc": joined("cc"),
            "body": email.get("body_clean") or "",
            "hints": sorted(
                {
                    c.get("property_id")
                    for c in (email.get("property_candidates") or [])
                    if c.get("property_id")
                }
            ),
            "thread_context": [
                {
                    "subject": s.get("subject"),
                    "date": str(s.get("date") or ""),
                    "from": ", ".join((s.get("participants") or {}).get("from") or []),
                    "body": s.get("body_clean") or "",
                    "property_ids": s.get("property_ids") or [],
                }
                for s in siblings
            ],
        }

    # ------------------------------------------------------------------
    def run(self, *, limit: Optional[int] = None) -> ThreadContextStats:
        candidates = self._candidates(limit)
        self.stats.candidates = len(candidates)
        if not candidates:
            logger.info("Thread-context pass: nothing unresolved to reconsider")
            return self.stats

        siblings = self._siblings_for(candidates)
        eligible = [c for c in candidates if siblings.get(c["thread_key"])]
        self.stats.eligible = len(eligible)

        logger.info(
            "Thread-context pass: %d unresolved, %d have a resolved sibling "
            "(concurrency=%d)",
            len(candidates), len(eligible), CONCURRENCY,
        )
        if not eligible:
            return self.stats

        self.mongo.runs.insert_one({
            "run_id": self.run_id,
            "kind": "thread_context",
            "model": self.segregator.model,
            "pending": len(eligible),
            "started_at": datetime.now(timezone.utc),
            "status": "running",
        })

        started = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futures = {}
            for email in eligible:
                payload = self._payload(email, siblings[email["thread_key"]])
                future = pool.submit(self.segregator.segregate, payload, [])
                futures[future] = (email, siblings[email["thread_key"]])

            for future in as_completed(futures):
                email, sibs = futures[future]
                done += 1
                try:
                    result = future.result()
                except Exception as exc:
                    self.stats.errors += 1
                    logger.error("Thread-context call failed: %s", exc)
                    continue

                try:
                    self._persist(email, sibs, result)
                except Exception as exc:
                    self.stats.errors += 1
                    logger.error("Persisting thread-context decision failed: %s", exc)

                if done % 20 == 0:
                    rate = done / max(1e-6, time.time() - started)
                    logger.info(
                        "  %d/%d  %.1f/s  resolved=%d still_unresolved=%d",
                        done, len(eligible), rate,
                        self.stats.resolved, self.stats.still_unresolved,
                    )

        self.stats.input_tokens = self.segregator.input_tokens
        self.stats.output_tokens = self.segregator.output_tokens
        self.mongo.runs.update_one(
            {"run_id": self.run_id},
            {"$set": {
                "status": "complete",
                "finished_at": datetime.now(timezone.utc),
                **self.stats.as_dict(),
            }},
        )
        logger.info("Thread-context pass complete: %s", self.stats.as_dict())
        return self.stats

    # ------------------------------------------------------------------
    def _persist(self, email: dict, siblings: List[dict], result) -> None:
        if result.error and not result.email.properties:
            self.stats.errors += 1
            return

        self.stats.called += 1
        decision = result.email
        properties = list(decision.properties)

        thread_properties = {p for s in siblings for p in (s.get("property_ids") or [])}

        record = decision.as_dict()
        record.update({
            "model": result.model,
            "run_id": self.run_id,
            "decided_at": datetime.now(timezone.utc),
            "method": "thread_context",
            "thread_properties_seen": sorted(thread_properties),
            "siblings_shown": len(siblings),
        })

        update = {
            "property_ids": properties,
            "scope": "property" if properties else "common",
            "segregation": record,
            "resolution_status": (
                "needs_review" if decision.needs_review
                else "resolved" if properties
                else "no_property"
            ),
        }
        self.mongo.artifacts.update_one({"sha256": email["sha256"]}, {"$set": update})

        if properties:
            self.stats.resolved += 1
            for pid in properties:
                self.stats.assignments[pid] = self.stats.assignments.get(pid, 0) + 1
            # Worth counting: the model choosing a subset of, or something
            # outside, what the thread carried is the whole reason a model is
            # doing this rather than an inheritance rule.
            if set(properties) != thread_properties:
                self.stats.disagreed_with_thread += 1
            # The queue entry asked "which property?" and now has an answer.
            self.mongo.review_queue.delete_many({
                "artifact_sha": email["sha256"],
                "kind": {"$in": ["property_unresolved", "property_resolution"]},
            })
        else:
            self.stats.still_unresolved += 1
