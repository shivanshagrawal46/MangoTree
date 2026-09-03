"""Tiny Claude Vision API call to verify the key + model work."""
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

# Make sure .env is loaded
from config.settings import Settings
s = Settings.load()

from src.extractor.claude_ocr import _ocr_page_via_claude


img = Image.new("RGB", (800, 200), "white")
d = ImageDraw.Draw(img)
try:
    f = ImageFont.truetype("arial.ttf", 36)
except Exception:
    f = ImageFont.load_default()
d.text((40, 40), "INVOICE #2024-0917", fill="black", font=f)
d.text((40, 100), "Total Due: $12,345.67", fill="black", font=f)

print("Calling Claude Vision OCR on a synthetic test image...")
text, cost = _ocr_page_via_claude(img, model=s.ocr_vision_model)
print(f"\n  text:        {text!r}")
print(f"  actual cost: ${cost:.5f}")
print("\nKey + model work end-to-end." if text else "\nWARNING: empty text returned.")
