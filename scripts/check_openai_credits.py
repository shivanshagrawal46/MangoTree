"""Smallest possible call that proves the OpenAI key can currently bill."""
import sys

sys.path.insert(0, ".")

from mangotree.config.models import OCR
from mangotree.config.settings import SETTINGS

try:
    from openai import OpenAI

    client = OpenAI(api_key=SETTINGS.openai_api_key)
    model = getattr(OCR, "cross_provider_model", None) or "gpt-5"
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        max_completion_tokens=16,
    )
    text = (response.choices[0].message.content or "").strip()
    print(f"\n  OK — {model} responded: {text!r}")
    print("  GPT-5 fallback is available again.\n")
except Exception as exc:
    print(f"\n  STILL FAILING — {type(exc).__name__}: {str(exc)[:300]}\n")
    raise SystemExit(1)
