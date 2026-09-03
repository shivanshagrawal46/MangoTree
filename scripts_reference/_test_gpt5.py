"""Diagnose the GPT-5 vision empty-response: render 1 page from a real report
and call GPT-5 exactly like the fallback does, printing the FULL response shape.
"""
import io
from pathlib import Path

import fitz
from PIL import Image

import config.settings  # loads .env
from openai import OpenAI

pdf = Path(r"F:\Title reports\2021\10 Heritage Lane, Wheatley Heights, NY.pdf")
doc = fitz.open(pdf)
page = doc[20]  # a scanned recorded-instrument page
mat = fitz.Matrix(180 / 72.0, 180 / 72.0)
pix = page.get_pixmap(matrix=mat, alpha=False)
img = Image.open(io.BytesIO(pix.tobytes("png")))
buf = io.BytesIO()
img.convert("RGB").save(buf, format="JPEG", quality=80)
import base64
b64 = base64.b64encode(buf.getvalue()).decode()

client = OpenAI()
resp = client.chat.completions.create(
    model="gpt-5",
    messages=[
        {"role": "system", "content": "Transcribe the page exactly, word by word."},
        {"role": "user", "content": [
            {"type": "text", "text": "Transcribe this page."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]},
    ],
)
ch = resp.choices[0]
print("finish_reason:", ch.finish_reason)
print("refusal:", getattr(ch.message, "refusal", None))
print("content len:", len(ch.message.content or ""))
print("content head:", (ch.message.content or "")[:300])
print("usage:", resp.usage)
