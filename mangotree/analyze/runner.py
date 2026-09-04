"""Analysis runner — produce, verify and persist per-property analyses.

Analyses are stored **versioned, never overwritten**. When an analysis changes,
the question "what did we believe last week, and on what evidence?" has to remain
answerable — a lender file that quietly rewrites its own history is worth less
than no file at all. Each run records the model, the evidence it saw, and the
citation integrity it achieved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from mangotree.analyze.analyst import Analysis, PropertyAnalyst
from mangotree.analyze.context import ContextBuilder
from mangotree.config.registry import PROPERTIES
from mangotree.core.logging import logger
from mangotree.retrieve.retriever import Retriever
from mangotree.storage.mongo import Mongo

DEFAULT_QUESTION = (
    "What is the current status of this property, what money is at risk, "
    "what has gone wrong, and what needs attention now?"
)


@dataclass
class RunSummary:
    analysed: int = 0
    skipped_no_evidence: int = 0
    failed: int = 0
    findings: int = 0
    dropped_claims: int = 0
    injection_flags: int = 0
    per_property: Dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "analysed": self.analysed,
            "skipped_no_evidence": self.skipped_no_evidence,
            "failed": self.failed,
            "total_findings": self.findings,
            "dropped_claims": self.dropped_claims,
            "injection_flags": self.injection_flags,
            "per_property": self.per_property,
        }


class AnalysisRunner:
    def __init__(self, mongo: Mongo, *, anthropic_key: str, voyage_key: str):
        self.mongo = mongo
        self.retriever = Retriever(mongo, voyage_api_key=voyage_key)
        self.context_builder = ContextBuilder(mongo, self.retriever)
        self.analyst = PropertyAnalyst(anthropic_key)
        self.run_id = datetime.now(timezone.utc).strftime("analysis-%Y%m%d-%H%M%S")

    # ------------------------------------------------------------------
    def analyse_property(
        self,
        property_id: str,
        *,
        question: str = DEFAULT_QUESTION,
        top_k: int = 25,
        persist: bool = True,
    ) -> Analysis:
        context = self.context_builder.build(property_id, question, top_k=top_k)
        analysis = self.analyst.analyse(context)

        if persist:
            self._persist(analysis, context.stats(), question)
        return analysis

    # ------------------------------------------------------------------
    def _persist(self, analysis: Analysis, context_stats: dict, question: str) -> None:
        collection = self.mongo.db["analyses"]
        previous = collection.count_documents({"property_id": analysis.property_id})
        record = analysis.as_dict()
        record.update({
            "version": previous + 1,
            "run_id": self.run_id,
            "question": question,
            "context_stats": context_stats,
        })
        collection.insert_one(record)

        # The dashboard reads the latest pointer; history stays in `analyses`.
        self.mongo.properties.update_one(
            {"property_id": analysis.property_id},
            {"$set": {
                "latest_analysis": {
                    "version": previous + 1,
                    "run_id": self.run_id,
                    "headline": analysis.headline,
                    "findings": len(analysis.findings),
                    "critical": sum(1 for f in analysis.findings if f.severity == "critical"),
                    "citation_integrity": analysis.citation_integrity,
                    "generated_at": analysis.generated_at,
                },
            }},
            upsert=True,
        )

    # ------------------------------------------------------------------
    def run(
        self,
        property_ids: Optional[Sequence[str]] = None,
        *,
        question: str = DEFAULT_QUESTION,
        top_k: int = 25,
    ) -> RunSummary:
        targets = list(property_ids) if property_ids else [p.property_id for p in PROPERTIES]
        summary = RunSummary()

        self.mongo.runs.insert_one({
            "run_id": self.run_id, "kind": "analysis", "status": "running",
            "started_at": datetime.now(timezone.utc), "target_count": len(targets),
        })

        for index, property_id in enumerate(targets, start=1):
            indexed = self.mongo.chunks.count_documents({"property_ids": property_id})
            if indexed == 0:
                summary.skipped_no_evidence += 1
                logger.info("  [%d/%d] %s — no indexed evidence, skipped",
                            index, len(targets), property_id)
                continue

            try:
                logger.info("  [%d/%d] %s — analysing (%d chunks indexed)",
                            index, len(targets), property_id, indexed)
                analysis = self.analyse_property(
                    property_id, question=question, top_k=top_k
                )
                summary.analysed += 1
                summary.findings += len(analysis.findings)
                summary.dropped_claims += len(analysis.dropped_claims)
                summary.injection_flags += len(analysis.suspicious_content)
                summary.per_property[property_id] = {
                    "headline": analysis.headline[:200],
                    "findings": len(analysis.findings),
                    "critical": sum(1 for f in analysis.findings if f.severity == "critical"),
                    "high": sum(1 for f in analysis.findings if f.severity == "high"),
                    "citation_integrity": round(analysis.citation_integrity, 3),
                    "truncated": analysis.truncated,
                }
            except Exception as exc:
                summary.failed += 1
                logger.error("Analysis failed for %s: %s", property_id, exc)
                self.mongo.errors.insert_one({
                    "run_id": self.run_id, "property_id": property_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "at": datetime.now(timezone.utc),
                })

        self.mongo.runs.update_one(
            {"run_id": self.run_id},
            {"$set": {"status": "complete", "finished_at": datetime.now(timezone.utc),
                      **summary.as_dict()}},
        )
        return summary
