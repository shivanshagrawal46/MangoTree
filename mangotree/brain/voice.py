"""Speaking instead of typing, in the chat.

The browser records what the person says; this turns the recording into text
and hands it back for the chat box. Nothing is asked on the person's behalf:
they read the text, correct it, and press Send themselves.

The speech is turned into text by OpenAI's transcription service, with the key
the system already holds. The recording goes from our server to that service
and is not kept here. The service is told RKB's own words (the addresses, the
people, the terms of the trade), so that it writes them the way the records do.

    POST /brain/chat/transcribe      audio (one recording)  ->  {"text": "..."}
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile

from mangotree.api.auth import CurrentUser
from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core.logging import logger

from .store import active_property_ids

MODEL = os.environ.get("MT_VOICE_MODEL", "gpt-4o-transcribe")
MAX_BYTES = 20 * 1024 * 1024          # the service takes 25 MB; ten minutes of speech is about 3 MB
MIN_BYTES = 1500                      # less than this is a press of the button with nothing said
PEOPLE = ("Rakesh Sir", "Manju Sir", "Manjunath", "JP Sir", "Shivansh", "Wes Stone", "Kelly Stone", "Duwayne Tutt", "David Gonzalez", "Brian Gallagher")
WORDS = ("RKB Consulting Group", "ROI Blocks", "LP Remodeling", "Buildertrend", "ARV", "as-is value", "draw", "hold-back", "change order", "deed of trust",
         "promissory note", "payoff", "underwriting", "title company", "closing", "rehab", "comps", "KVC Home Solutions")


class VoiceError(Exception):
    """A plain sentence for the person: why the speech did not become text."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def vocabulary() -> str:
    """What the service is told before it listens: our addresses, our people, our words."""
    places = [PROPERTY_INDEX[p].canonical_address for p in active_property_ids() if p in PROPERTY_INDEX]
    return ("A lender's team at RKB Consulting Group talking about its renovation loans with ROI Blocks. "
            "Properties: " + "; ".join(places) + ". People: " + ", ".join(PEOPLE) + ". Terms: " + ", ".join(WORDS) + ".")


def transcribe(raw: bytes, filename: str = "speech.webm", content_type: str = "audio/webm", *, client: Optional[Any] = None) -> Dict[str, Any]:
    """One recording to text. Raises VoiceError with a sentence a person can act on."""
    if len(raw) < MIN_BYTES:
        raise VoiceError("Nothing was recorded. Press Speak, say it, then press Stop.")
    if len(raw) > MAX_BYTES:
        raise VoiceError("That recording is too long. Please say it in shorter parts.", 413)
    if client is None:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise VoiceError("Speech to text is not set up on this server.", 503)
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=90, max_retries=1)
    told = vocabulary()
    began = time.time()
    try:
        r = client.audio.transcriptions.create(model=MODEL, file=(filename, raw, content_type.split(";")[0] or "audio/webm"), language="en", prompt=told)
    except Exception as exc:
        logger.warning("speech to text failed: %s: %s", type(exc).__name__, str(exc)[:300])
        raise VoiceError("The speech could not be turned into text just now. Please try again, or type it.", 502)
    text = " ".join(str(getattr(r, "text", "") or "").split())
    # On a silent recording the service can hand back the words it was told instead of what was said.
    if text and text.lower().rstrip(". ") in told.lower():
        text = ""
    return {"text": text, "bytes": len(raw), "ms": int((time.time() - began) * 1000), "model": MODEL}


def install(app: FastAPI) -> None:
    @app.post("/brain/chat/transcribe")
    def brain_transcribe(audio: UploadFile = File(...), user=CurrentUser):
        raw = audio.file.read(MAX_BYTES + 1)
        try:
            out = transcribe(raw, audio.filename or "speech.webm", audio.content_type or "audio/webm")
        except VoiceError as exc:
            raise HTTPException(exc.status, str(exc))
        logger.info("speech to text for %s: %d bytes, %d ms, %d characters", user.get("user_id"), out["bytes"], out["ms"], len(out["text"]))
        return {"text": out["text"]}
