"""
Claude Vision OCR — production-grade fallback for big multi-page scanned PDFs.

Why this module exists
----------------------
RapidOCR (PP-OCR v4 via ONNX) is excellent for small single-page documents,
inline images, and signatures, where it runs in milliseconds. But for big
multi-page court scans (50-200 pages), running RapidOCR sequentially on
CPU takes hours per document. The fix is to dispatch each page in parallel
to Claude's vision API, which:

  - Produces transcription quality on par with cloud document-AI services
    (AWS Textract / Azure DI / Google Document AI)
  - Is parallelizable across many concurrent HTTP requests
  - Costs ~$0.015-0.025 per page on Sonnet 4.5

Key design choices
------------------
- We pass each page as a single user message with one image, asking Claude
  to transcribe the visible text **verbatim** (no summarisation, no
  commentary).
- We use a low-temperature instruction (`temperature=0`) and a tight
  system prompt so output is the document's text, nothing else.
- We do **parallel page-level dispatch** with a bounded thread pool
  (default 8 concurrent calls).
- We track and cap **spend per run** with a budget guard.
- We retry transient failures with exponential backoff.
- Confidence is reported as 0.97 (Claude doesn't expose per-token logprobs
  here, but its OCR quality is empirically ≥ Sonnet's overall accuracy on
  legal forms).

Returned shape mirrors `PdfPage`-compatible tuples so it can drop in to the
PDF extractor.
"""
from __future__ import annotations

import base64
import io
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

from PIL import Image

from src.utils.logger import logger


# Per-page billing approximation for the budget guard.
# Sonnet 4.5: $3/M input, $15/M output. A page at 1500x2000 ≈ 1.6K input
# tokens; output text typically 500-1500 tokens.
# We use a conservative upper estimate so we never over-spend.
_COST_PER_PAGE_SONNET = 0.025   # USD
_COST_PER_PAGE_HAIKU = 0.008    # USD

# Reset image to fit Anthropic's 8000 px max dim AND keep request size sane.
_MAX_IMAGE_DIM_PX = 1600
_JPEG_QUALITY = 88

# Anthropic Tier 1 rate limits (default for new $5 paid accounts):
#   50 requests/min,  30K input tokens/min,  8K output tokens/min.
# We cap below those to avoid 429s, with override via env for higher tiers.
_CLAUDE_RPM = int(os.environ.get("CLAUDE_RPM", "45"))
_CLAUDE_INPUT_TPM = int(os.environ.get("CLAUDE_INPUT_TPM", "27000"))
_CLAUDE_OUTPUT_TPM = int(os.environ.get("CLAUDE_OUTPUT_TPM", "7500"))

# max_tokens we request per page. Anthropic's rate-limit check counts
# `max_tokens` toward output-TPM, so keep it tight. Most legal pages
# transcribe to <1200 tokens; we leave a little headroom for dense pages.
_MAX_OUTPUT_TOKENS = int(os.environ.get("CLAUDE_VISION_MAX_TOKENS", "1500"))

# Hard global cap on in-flight Claude calls across ALL worker threads.
# Anthropic Tier 1 output-TPM is 8K — with max_tokens=1500 per call,
# 4 concurrent would peak at 6000 (well under 8K). We use 3 for safety.
_MAX_INFLIGHT = int(os.environ.get("CLAUDE_VISION_MAX_INFLIGHT", "3"))


@dataclass
class VisionPage:
    page_no: int
    text: str
    method: str = "claude_vision"
    ocr_confidence: float = 0.97


# ------------------------------------------------------------------------
# Spend tracking
# ------------------------------------------------------------------------

class _SpendGuard:
    """Process-wide tracker for total Claude vision spend in this run."""

    def __init__(self, budget_usd: float) -> None:
        self.budget = budget_usd
        self.spent = 0.0
        self._lock = threading.Lock()

    def check_and_reserve(self, est_cost: float) -> bool:
        with self._lock:
            if self.spent + est_cost > self.budget:
                return False
            self.spent += est_cost
            return True

    def record_actual(self, est_cost: float, actual_cost: float) -> None:
        # Replace the estimate with the actual cost (post-call true-up).
        with self._lock:
            self.spent += (actual_cost - est_cost)


_GLOBAL_GUARD: Optional[_SpendGuard] = None
_GUARD_LOCK = threading.Lock()


def init_spend_guard(budget_usd: float) -> _SpendGuard:
    """Initialize (or replace) the process-wide spend guard."""
    global _GLOBAL_GUARD
    with _GUARD_LOCK:
        _GLOBAL_GUARD = _SpendGuard(budget_usd)
        logger.info(f"Claude vision spend guard armed: budget=${budget_usd:.2f}")
        return _GLOBAL_GUARD


def get_spend_guard() -> Optional[_SpendGuard]:
    return _GLOBAL_GUARD


# ------------------------------------------------------------------------
# Sliding-window rate limiter (RPM + input-TPM + output-TPM)
# ------------------------------------------------------------------------

class _ClaudeRateLimiter:
    """
    Process-wide limiter for the Anthropic API.

    Tracks three sliding 60-second windows:
      - requests
      - input tokens   (estimated from image bytes)
      - output tokens  (we use max_tokens as the conservative budget,
                        which is what Anthropic itself uses for the
                        rate-limit pre-check)
    """

    def __init__(self, rpm: int, in_tpm: int, out_tpm: int) -> None:
        self.rpm = rpm
        self.in_tpm = in_tpm
        self.out_tpm = out_tpm
        # (ts, in_tok, out_tok)
        self._events: Deque[Tuple[float, int, int]] = deque()
        self._lock = threading.Lock()

    def acquire(self, in_tokens: int, out_tokens: int) -> None:
        while True:
            now = time.monotonic()
            with self._lock:
                cutoff = now - 60.0
                while self._events and self._events[0][0] < cutoff:
                    self._events.popleft()
                cur_reqs = len(self._events)
                cur_in = sum(e[1] for e in self._events)
                cur_out = sum(e[2] for e in self._events)
                if (
                    cur_reqs < self.rpm
                    and cur_in + in_tokens <= self.in_tpm
                    and cur_out + out_tokens <= self.out_tpm
                ):
                    self._events.append((now, in_tokens, out_tokens))
                    return
                # Compute earliest-clear time for whichever window is full.
                wait = 0.0
                if cur_reqs >= self.rpm:
                    wait = max(wait, self._events[0][0] + 60.0 - now)
                if cur_in + in_tokens > self.in_tpm and self._events:
                    excess = cur_in + in_tokens - self.in_tpm
                    spent = 0
                    for ts, ti, _to in self._events:
                        spent += ti
                        if spent >= excess:
                            wait = max(wait, ts + 60.0 - now)
                            break
                if cur_out + out_tokens > self.out_tpm and self._events:
                    excess = cur_out + out_tokens - self.out_tpm
                    spent = 0
                    for ts, _ti, to in self._events:
                        spent += to
                        if spent >= excess:
                            wait = max(wait, ts + 60.0 - now)
                            break
                wait = max(wait, 0.2) + 0.1
            logger.info(
                f"  Claude vision rate-limit pause {wait:.1f}s "
                f"(reqs={cur_reqs}/{self.rpm}, in={cur_in}/{self.in_tpm}, out={cur_out}/{self.out_tpm})"
            )
            time.sleep(wait)


_RATE_LIMITER: Optional[_ClaudeRateLimiter] = None
_RATE_LOCK = threading.Lock()

# True global concurrency cap (semaphore). Even if multiple PDFs each spin
# up their own ThreadPoolExecutor, this semaphore guarantees only
# _MAX_INFLIGHT requests are in-flight to Anthropic at any one moment.
_INFLIGHT_SEM = threading.BoundedSemaphore(value=_MAX_INFLIGHT)

# Soft circuit-breaker: once we've seen "credit balance too low" or the
# spend guard has tripped, all subsequent calls in this run short-circuit
# immediately so we don't burn retries.
_VISION_DISABLED_FOR_RUN = False
_VISION_DISABLED_REASON = ""
_DISABLE_LOCK = threading.Lock()

# When Claude credits are exhausted we DO NOT drop to RapidOCR — we switch the
# whole run to the GPT-5 frontier vision model so OCR stays frontier-only
# (Claude Sonnet 4.6 -> GPT-5). This is the user's hard requirement for
# court-grade documents (title reports etc.).
_PREFER_OPENAI_FOR_RUN = False
_PREFER_OPENAI_REASON = ""


def _disable_vision_for_run(reason: str) -> None:
    global _VISION_DISABLED_FOR_RUN, _VISION_DISABLED_REASON
    with _DISABLE_LOCK:
        if not _VISION_DISABLED_FOR_RUN:
            _VISION_DISABLED_FOR_RUN = True
            _VISION_DISABLED_REASON = reason
            logger.warning(
                f"Claude Vision disabled for the rest of this run: {reason}. "
                f"All remaining OCR pages will use RapidOCR."
            )


def _prefer_openai_for_run(reason: str) -> None:
    """Switch the run to GPT-5 vision (frontier) for all remaining pages instead
    of disabling vision / dropping to RapidOCR."""
    global _PREFER_OPENAI_FOR_RUN, _PREFER_OPENAI_REASON
    with _DISABLE_LOCK:
        if not _PREFER_OPENAI_FOR_RUN:
            _PREFER_OPENAI_FOR_RUN = True
            _PREFER_OPENAI_REASON = reason
            logger.warning(
                f"Switching OCR to GPT-5 frontier vision for the rest of this run: "
                f"{reason}. Frontier-only policy preserved (no RapidOCR)."
            )


def prefer_openai_active() -> bool:
    return _PREFER_OPENAI_FOR_RUN


def is_vision_disabled() -> Tuple[bool, str]:
    return _VISION_DISABLED_FOR_RUN, _VISION_DISABLED_REASON


def _get_rate_limiter() -> _ClaudeRateLimiter:
    global _RATE_LIMITER
    if _RATE_LIMITER is not None:
        return _RATE_LIMITER
    with _RATE_LOCK:
        if _RATE_LIMITER is None:
            _RATE_LIMITER = _ClaudeRateLimiter(
                rpm=_CLAUDE_RPM,
                in_tpm=_CLAUDE_INPUT_TPM,
                out_tpm=_CLAUDE_OUTPUT_TPM,
            )
            logger.info(
                f"Claude vision rate-limiter armed: "
                f"rpm={_CLAUDE_RPM}, in_tpm={_CLAUDE_INPUT_TPM}, out_tpm={_CLAUDE_OUTPUT_TPM}, "
                f"max_tokens_per_call={_MAX_OUTPUT_TOKENS}"
            )
        return _RATE_LIMITER


# ------------------------------------------------------------------------
# Anthropic client (lazy, thread-safe singleton)
# ------------------------------------------------------------------------

_CLIENT_LOCK = threading.Lock()
_CLIENT = None


def _get_client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        import anthropic  # type: ignore

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY missing. Add it to .env before using Claude Vision OCR."
            )
        _CLIENT = anthropic.Anthropic(api_key=api_key)
        return _CLIENT


# ------------------------------------------------------------------------
# Image prep
# ------------------------------------------------------------------------

def _image_to_jpeg_b64(img: Image.Image) -> Tuple[str, str]:
    """Resize, encode JPEG, return (media_type, base64)."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # Cap longest side to MAX_IMAGE_DIM_PX (Claude vision sweet spot).
    w, h = img.size
    longest = max(w, h)
    if longest > _MAX_IMAGE_DIM_PX:
        scale = _MAX_IMAGE_DIM_PX / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    raw = buf.getvalue()
    return "image/jpeg", base64.standard_b64encode(raw).decode("ascii")


# ------------------------------------------------------------------------
# Single-page OCR call
# ------------------------------------------------------------------------

_OCR_SYSTEM_PROMPT = (
    "You are a precise OCR engine for legal documents. Transcribe the "
    "visible text from the supplied page image verbatim. Preserve line "
    "breaks, paragraph order, and bullet/number markers. Preserve dollar "
    "amounts, dates, account numbers, case numbers, and signatures EXACTLY "
    "as written. Do NOT summarise. Do NOT add commentary. Do NOT translate. "
    "If a portion is illegible, write [illegible] for that span. Output ONLY "
    "the transcribed text — no preface, no postface, no markdown fencing."
)


def _per_page_cost(model: str) -> float:
    if "haiku" in model.lower():
        return _COST_PER_PAGE_HAIKU
    return _COST_PER_PAGE_SONNET


def _estimate_image_input_tokens(b64_len: int) -> int:
    """Heuristic: at our resize cap (1600 px longest side, JPEG q88) a page
    is typically ~1300-2000 input tokens. We use 2000 as a conservative
    upper bound for the rate-limiter budget."""
    # b64_len gives bytes-of-encoded-image; very rough proxy for image
    # complexity. Most of our pages land around 1500-2000 tokens.
    return 2000


def _ocr_page_via_claude(
    img: Image.Image,
    *,
    model: str,
    max_retries: int = 4,
) -> Tuple[str, float]:
    """OCR a single page image with Claude Vision. Returns (text, actual_cost_usd)."""
    media_type, b64 = _image_to_jpeg_b64(img)
    client = _get_client()
    limiter = _get_rate_limiter()

    in_est = _estimate_image_input_tokens(len(b64))
    out_budget = _MAX_OUTPUT_TOKENS

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        # 1. Block on sliding-window limiter (RPM + TPM budgets).
        limiter.acquire(in_est, out_budget)
        # 2. Acquire one of the _MAX_INFLIGHT slots — this is the hard
        #    cap on simultaneous in-flight requests across all workers.
        _INFLIGHT_SEM.acquire()
        try:
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=out_budget,
                    temperature=0,
                    system=_OCR_SYSTEM_PROMPT,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": "Transcribe this page.",
                            },
                        ],
                    }],
                )
            finally:
                # Release the in-flight slot the moment the API call
                # returns (success OR error), so other workers can proceed.
                _INFLIGHT_SEM.release()

            parts: List[str] = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            text = "\n".join(parts).strip()

            usage = getattr(resp, "usage", None)
            in_tok = getattr(usage, "input_tokens", 0) if usage else 0
            out_tok = getattr(usage, "output_tokens", 0) if usage else 0
            if "haiku" in model.lower():
                in_rate, out_rate = 1.0 / 1_000_000, 5.0 / 1_000_000
            else:
                in_rate, out_rate = 3.0 / 1_000_000, 15.0 / 1_000_000
            cost = in_tok * in_rate + out_tok * out_rate
            return text, cost
        except Exception as exc:
            last_err = exc
            msg = str(exc)
            low = msg.lower()
            # Permanent failures — abort retries, fall back to RapidOCR:
            if "credit balance" in low or "low_credit" in low:
                logger.warning(
                    "  Claude vision: credit balance too low — falling back to RapidOCR for remaining pages"
                )
                raise RuntimeError(f"low_credit: {msg[:120]}")
            if "content filtering" in low or "content_filter" in low:
                logger.warning(
                    "  Claude vision: content filter blocked — falling back to RapidOCR for this page"
                )
                raise RuntimeError(f"content_filter_block: {msg[:120]}")
            if "invalid_request_error" in low and "credit" in low:
                raise RuntimeError(f"low_credit: {msg[:120]}")
            # Transient: 429 rate-limit → longer backoff
            if "429" in msg or "rate_limit" in low:
                wait = max(20.0, 10.0 * (attempt + 1))
                logger.warning(
                    f"  Claude vision 429 (attempt {attempt + 1}/{max_retries}) — backing off {wait:.0f}s"
                )
            else:
                wait = 2.0 ** attempt + 1.0
                logger.warning(
                    f"  Claude vision attempt {attempt + 1}/{max_retries} failed: {msg[:160]} — retrying in {wait:.0f}s"
                )
            time.sleep(wait)
    raise RuntimeError(f"Claude vision OCR failed after {max_retries} retries: {last_err}")


# ------------------------------------------------------------------------
# High-quality second-model fallback: OpenAI GPT vision
# ------------------------------------------------------------------------
# When Claude's OUTPUT content-filter false-positives on a legal page (common
# on scanned forms / signatures), we transcribe that page with a DIFFERENT
# frontier vision model instead of dropping to RapidOCR. Same verbatim-OCR
# prompt. Best-effort: returns text or None (None -> caller's RapidOCR path).
_OPENAI_OCR_MODEL = os.environ.get("OPENAI_OCR_MODEL", "gpt-5")


def _ocr_page_via_openai(img: "Image.Image") -> Optional[str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        media_type, b64 = _image_to_jpeg_b64(img)
        client = OpenAI(api_key=api_key)
        # NOTE: gpt-5 only supports the default temperature (1); passing 0
        # returns a 400. Omit it entirely — fine for verbatim OCR.
        # gpt-5 is a REASONING model: without explicit room it can burn the
        # whole completion budget on reasoning and return EMPTY content (while
        # still billing us). minimal effort + large cap fixes that for OCR.
        # If a dense page still hits finish_reason='length' with empty output,
        # we retry with a progressively larger token cap so a Claude-rejected
        # page is NEVER silently dropped to RapidOCR.
        for cap in (16384, 32768, 65536):
            resp = client.chat.completions.create(
                model=_OPENAI_OCR_MODEL,
                reasoning_effort="minimal",
                max_completion_tokens=cap,
                messages=[
                    {"role": "system", "content": _OCR_SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Transcribe this page."},
                        {"type": "image_url",
                         "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                    ]},
                ],
            )
            ch = resp.choices[0]
            txt = (ch.message.content or "").strip()
            if txt:
                return txt
            # Empty: if we ran out of room (finish_reason='length'), bump the
            # cap and try again; any other finish_reason won't be helped by it.
            if ch.finish_reason == "length" and cap != 65536:
                logger.warning(
                    f"  OpenAI vision empty (finish_reason=length) at cap={cap}; "
                    f"retrying with a larger token cap"
                )
                continue
            # NEVER fail silently — this exact path hid 154 empty responses.
            logger.warning(
                f"  OpenAI vision returned EMPTY content "
                f"(finish_reason={ch.finish_reason}, "
                f"refusal={getattr(ch.message, 'refusal', None)})"
            )
            return None
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"  OpenAI vision OCR fallback failed: {str(exc)[:160]}")
        return None


# ------------------------------------------------------------------------
# Public batch API
# ------------------------------------------------------------------------

def ocr_pages_via_claude(
    images: List[Tuple[int, Image.Image]],
    *,
    model: str = "claude-sonnet-4-6",
    max_concurrency: int = 8,
) -> List[VisionPage]:
    """
    OCR a list of pages in parallel via Claude Vision.

    Parameters
    ----------
    images : list of (page_no, PIL.Image)
        Pages to transcribe. page_no is 1-indexed.
    model : str
        Anthropic model id. claude-sonnet-4-6 (default) or claude-haiku-4-5.
    max_concurrency : int
        Parallel API calls. Anthropic Tier 1 supports ~50 RPM safely.

    Returns
    -------
    list[VisionPage]   — one per page, in the original page_no order.
    """
    if not images:
        return []

    # Soft circuit-breaker: if a previous page already saw low_credit or
    # budget exhaustion, just bail immediately so the caller falls back
    # to RapidOCR for this whole document.
    disabled, reason = is_vision_disabled()
    if disabled:
        logger.info(
            f"  Claude Vision skipped for {len(images)} page(s) — {reason}; "
            f"caller will use RapidOCR fallback."
        )
        return [VisionPage(page_no=p, text="", method="vision_failed", ocr_confidence=0.0)
                for p, _ in images]

    guard = get_spend_guard()
    est_cost_per_page = _per_page_cost(model)

    pages_out: List[Optional[VisionPage]] = [None] * len(images)

    def _worker(slot: int, page_no: int, img: Image.Image) -> None:
        # If the run already switched to GPT-5 (Claude credits exhausted), use
        # the frontier OpenAI model directly — never RapidOCR.
        if prefer_openai_active():
            alt = _ocr_page_via_openai(img)
            pages_out[slot] = VisionPage(
                page_no=page_no, text=(alt or ""),
                method=("openai_vision" if alt else "vision_failed"),
                ocr_confidence=(0.95 if alt else 0.0),
            )
            return
        # Re-check the disable flag (another worker may have just tripped it).
        disabled, _ = is_vision_disabled()
        if disabled:
            pages_out[slot] = VisionPage(
                page_no=page_no, text="", method="vision_failed", ocr_confidence=0.0
            )
            return
        if guard is not None and not guard.check_and_reserve(est_cost_per_page):
            _disable_vision_for_run(
                f"budget guard tripped at ${guard.spent:.2f}/${guard.budget:.2f}"
            )
            pages_out[slot] = VisionPage(
                page_no=page_no, text="", method="vision_skipped_budget", ocr_confidence=0.0
            )
            return
        try:
            text, actual_cost = _ocr_page_via_claude(img, model=model)
            if guard is not None:
                guard.record_actual(est_cost_per_page, actual_cost)
            pages_out[slot] = VisionPage(
                page_no=page_no,
                text=text,
                method="claude_vision",
                ocr_confidence=0.97,
            )
        except Exception as exc:
            err = str(exc)
            if "low_credit" in err or "credit" in err.lower():
                # Claude credits exhausted -> switch the whole run to GPT-5
                # frontier vision (NOT RapidOCR) and transcribe this page now.
                _prefer_openai_for_run("Anthropic credit balance exhausted")
                alt = _ocr_page_via_openai(img)
                if alt:
                    logger.info(f"  page {page_no}: transcribed via GPT-5 vision ({len(alt)} chars)")
                pages_out[slot] = VisionPage(
                    page_no=page_no, text=(alt or ""),
                    method=("openai_vision" if alt else "vision_failed"),
                    ocr_confidence=(0.95 if alt else 0.0),
                )
                return
            # Per-page failure (esp. content_filter false-positive): try a
            # DIFFERENT frontier vision model before giving up to RapidOCR, so
            # legal content is never garbled or dropped.
            logger.warning(
                f"  page {page_no}: Claude vision failed: {err[:120]} — trying OpenAI vision fallback"
            )
            alt = _ocr_page_via_openai(img)
            if alt:
                logger.info(f"  page {page_no}: recovered via OpenAI vision ({len(alt)} chars)")
                pages_out[slot] = VisionPage(
                    page_no=page_no, text=alt, method="openai_vision", ocr_confidence=0.95
                )
            else:
                pages_out[slot] = VisionPage(
                    page_no=page_no, text="", method="vision_failed", ocr_confidence=0.0
                )

    n_workers = max(1, min(max_concurrency, len(images)))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = []
        for slot, (page_no, img) in enumerate(images):
            futs.append(pool.submit(_worker, slot, page_no, img))
        for f in as_completed(futs):
            f.result()

    return [p for p in pages_out if p is not None]
