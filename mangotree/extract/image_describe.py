"""Describe photographs that contain no readable text.

Why this is a separate field and not `text`
-------------------------------------------
OCR is a *transcription*: the words were on the page and we read them, so OCR'd
text has the same evidentiary standing as the document it came from. A scene
description is an *interpretation*: "kitchen appears roughly 70% complete" is a
model's opinion about a photograph.

Those must never share a field. If they did, a description would eventually be
retrieved, cited, and argued in a draw dispute as though it were a measurement.
So descriptions land in ``vision_description``, tagged with the model that wrote
them, and every retrievable form carries a prefix saying so. The original image
is untouched and remains byte-for-byte fetchable from the object store — admin
directive: images are stored as-is, not reduced to derived data.

What the prompt does and does not ask for
----------------------------------------
It asks for observable facts: the room, the trade, the materials present, the
state of visible work, legible signage, equipment, damage. It explicitly forbids
completion percentages and quality judgements, because those are the outputs
someone would most want to rely on and the ones a photograph least supports.
"""
from __future__ import annotations

import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from mangotree.core.logging import logger

#: Marker carried by any retrievable form of a description, so the distinction
#: between "we read this" and "a model thinks this" survives into an answer.
DESCRIPTION_NOTICE = (
    "AI-GENERATED IMAGE DESCRIPTION (not transcribed text, not a measurement) —"
)

_SYSTEM = """You describe photographs from residential renovation projects for a lender's records.

Report only what is OBSERVABLE in the image. Be concrete and specific.

Cover, when visible:
- what the space is (kitchen, bathroom, exterior rear elevation, basement, roof)
- the stage of work shown (bare studs, rough-in, drywall hung, taped, primed,
  finished cabinetry installed, fixtures set)
- trades evident (framing, electrical, plumbing, HVAC, drywall, tile, paint, roofing)
- materials and equipment present, and any brand names or model numbers legible
- any text legible in the image: signage, permits, labels, handwriting, screens
- visible damage, water staining, mould, debris, structural concerns
- whether the space appears occupied, vacant, or under active work

Hard rules:
- NEVER estimate a completion percentage. NEVER say a project is "on track",
  "behind", "good quality" or "poor quality". Those are judgements the image
  cannot support.
- NEVER guess an address, a date, a person's name or a property.
- If the image is unclear, blurred, dark or shows nothing identifiable, say so
  plainly and stop.
- Write 2-5 sentences of plain prose. No preamble, no bullet lists, no headings.
- If text is legible in the image, quote it verbatim in double quotes."""


@dataclass
class DescribeStats:
    attempted: int = 0
    described: int = 0
    empty: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "attempted": self.attempted,
            "described": self.described,
            "empty": self.empty,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "errors": self.errors[:20],
        }


class ImageDescriber:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-sonnet-4-6",
        concurrency: int = 6,
        max_output_tokens: int = 700,
    ) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.concurrency = concurrency
        self.max_output_tokens = max_output_tokens
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def describe_bytes(
        self, image_bytes: bytes, *, hint: str = "", stats: Optional[DescribeStats] = None
    ) -> str:
        stats = stats or DescribeStats()
        with self._lock:
            stats.attempted += 1

        # The filename is offered as a weak hint only. It is frequently just
        # IMG_6503, and where it does carry meaning the model must still not
        # treat it as fact about the scene.
        user_text = (
            "Describe this photograph."
            + (f"\n\nFilename (weak hint only, may be meaningless): {hint}" if hint else "")
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=_SYSTEM,
            messages=[{"role": "user", "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    },
                },
                {"type": "text", "text": user_text},
            ]}],
        )

        usage = response.usage
        with self._lock:
            stats.input_tokens += getattr(usage, "input_tokens", 0) or 0
            stats.output_tokens += getattr(usage, "output_tokens", 0) or 0

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        with self._lock:
            if text:
                stats.described += 1
            else:
                stats.empty += 1
        return text

    # ------------------------------------------------------------------
    def describe_many(
        self, jobs: Sequence[dict], *, stats: Optional[DescribeStats] = None
    ) -> Dict[str, str]:
        """jobs: [{"sha": ..., "bytes": ..., "hint": ...}] -> {sha: description}"""
        stats = stats or DescribeStats()
        out: Dict[str, str] = {}
        guard = threading.Lock()

        def run(job: dict) -> None:
            try:
                text = self.describe_bytes(
                    job["bytes"], hint=job.get("hint", ""), stats=stats
                )
            except Exception as exc:
                with guard:
                    stats.failures += 1
                    stats.errors.append(f"{job.get('hint')}: {exc}"[:300])
                logger.warning("Image description failed for %s: %s",
                               job.get("hint"), exc)
                return
            if text:
                with guard:
                    out[job["sha"]] = text

        if self.concurrency <= 1 or len(jobs) == 1:
            for job in jobs:
                run(job)
            return out

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            list(pool.map(run, jobs))
        return out


def description_record(text: str, model: str) -> dict:
    """The stored shape — always tagged, never bare prose."""
    return {
        "vision_description": text,
        "description_model": model,
        "description_is_model_generated": True,
        "described_at": datetime.now(timezone.utc),
    }


def retrievable_description(text: str, model: str) -> str:
    """Description in the form that may enter the search index."""
    if not text.strip():
        return ""
    return f"{DESCRIPTION_NOTICE} produced by {model} from the photograph.\n{text.strip()}"
