"""HTML body to clean plain text."""
from __future__ import annotations

import re
import threading

import html2text
from bs4 import BeautifulSoup

# Tags whose content should be discarded entirely
_DROP_TAGS = ("script", "style", "head", "meta", "link", "noscript", "title")

_MULTI_BLANK_LINES = re.compile(r"\n\s*\n\s*\n+")
_TRAILING_SPACES = re.compile(r"[ \t]+\n")


def _build_converter() -> html2text.HTML2Text:
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_images = True
    h.ignore_emphasis = False
    h.ignore_links = False
    h.protect_links = True
    h.unicode_snob = True
    h.skip_internal_links = True
    h.single_line_break = False
    return h


# html2text uses html.parser internally which is NOT thread-safe.  Use a
# per-thread converter so multiple threads can call html_to_text concurrently
# (BeautifulSoup with lxml is thread-safe; the converter must be thread-local).
_THREAD_LOCAL = threading.local()


def _get_converter() -> html2text.HTML2Text:
    conv = getattr(_THREAD_LOCAL, "converter", None)
    if conv is None:
        conv = _build_converter()
        _THREAD_LOCAL.converter = conv
    return conv


def html_to_text(html: str | None) -> str:
    """Convert HTML email body to readable plain text."""
    if not html:
        return ""

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    for comment in soup.find_all(string=lambda t: isinstance(t, type(soup.Comment)) if hasattr(soup, "Comment") else False):
        comment.extract()

    cleaned_html = str(soup)

    text = _get_converter().handle(cleaned_html)
    text = text.replace("\u00a0", " ")
    text = _TRAILING_SPACES.sub("\n", text)
    text = _MULTI_BLANK_LINES.sub("\n\n", text)
    return text.strip()
