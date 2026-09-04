"""GPT-5 vision OCR — the second provider in the cascade.

Why a second *provider* rather than a second Anthropic model
-----------------------------------------------------------
47 pages in this corpus were refused outright by Anthropic with
"Output blocked by content filtering policy". Every one of them is a title
report, title policy, or owner search — documents dense with personal
identifiers. That refusal is a property of the provider's policy, not of the
page's legibility, so retrying on Opus 5 returns the identical refusal. Only a
different provider can read them.

This is the concrete payoff of the provider-diversity rule in
`docs/01-AI-MODEL-STACK.md`: without a non-Anthropic vision model, 47 pages of
title work — liens, encumbrances, vesting, exceptions — would be permanently
reduced to offline OCR output flagged for manual typing.

Same delimited output contract as the Anthropic path, for the same reason:
verbatim legal text is full of unescaped quotation marks that break JSON.
"""
from __future__ import annotations

import base64
import time
from typing import Any, Dict, Optional

from mangotree.config.models import OCR as OCR_CFG
from mangotree.core.logging import logger

#: Reuses the Anthropic prompt so both providers are held to one contract.
from mangotree.extract.ocr import _PROMPT, _parse_response


class OpenAIVisionOCR:
    def __init__(self, api_key: str, *, model: Optional[str] = None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model or OCR_CFG.openai_model
        self.calls = 0

    # ------------------------------------------------------------------
    def read_page_raw(self, jpeg: bytes, *, attempts: int = 4) -> Dict[str, Any]:
        payload = base64.standard_b64encode(jpeg).decode("ascii")
        last: Optional[Exception] = None

        for attempt in range(attempts):
            try:
                self.calls += 1
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_completion_tokens=OCR_CFG.max_output_tokens,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/jpeg;base64,{payload}",
                                "detail": "high",
                            }},
                        ],
                    }],
                )
                choice = response.choices[0]
                parsed = _parse_response(choice.message.content or "")
                parsed["truncated"] = choice.finish_reason == "length"
                return parsed
            except Exception as exc:
                last = exc
                message = str(exc).lower()
                if any(m in message for m in ("rate", "429", "timeout", "overloaded", "503")):
                    time.sleep(min(30, 2 ** attempt) + 0.5)
                    continue
                raise

        raise RuntimeError(f"GPT-5 OCR failed after {attempts} attempts: {last}")
