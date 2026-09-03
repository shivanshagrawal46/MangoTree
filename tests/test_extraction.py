"""Tests for extraction parsing and routing.

These guard failures that are silent by nature: a page whose transcription was
quietly lost, or a spreadsheet cell that arrives without knowing where it came
from. Neither raises an error — they just produce a thinner corpus that nobody
notices until an answer is wrong.
"""
from __future__ import annotations

import pytest

from mangotree.extract.documents import MIN_CHARS_PER_PAGE, page_needs_vision
from mangotree.extract.ocr import _parse_response
from mangotree.extract.spreadsheet import _col_letter, _detect_header_row


class TestOCRResponseParsing:
    def test_parses_the_delimited_format(self):
        raw = (
            "###META\n"
            "confidence: 0.94\n"
            "blank: no\n"
            "tables: yes\n"
            "handwriting: no\n"
            "notes: none\n"
            "###TEXT\n"
            "AtoZ Title and Settlement LLC\nRE: 513 Allison Street NW"
        )
        out = _parse_response(raw)
        assert out["confidence"] == 0.94
        assert out["has_tables"] is True
        assert out["is_blank"] is False
        assert out["notes"] == ""
        assert "AtoZ Title" in out["text"]

    def test_quotes_in_legal_text_survive_intact(self):
        """The bug this format exists to prevent.

        JSON-wrapped output lost everything after the first unescaped quote, and
        contracts are full of them: 'the "Holdback"', '"Borrower" means'.
        """
        body = '1.07. "Loan" means the loan of $1,044,492.04 (the "Holdback") per \\ terms.'
        raw = f"###META\nconfidence: 0.97\nblank: no\n###TEXT\n{body}"
        out = _parse_response(raw)
        assert out["text"] == body
        assert '"Holdback"' in out["text"]
        assert "1,044,492.04" in out["text"]

    def test_transcription_survives_a_missing_header(self):
        out = _parse_response("###TEXT\nsome page text here")
        assert out["text"] == "some page text here"

    def test_unformatted_response_is_kept_not_discarded(self):
        out = _parse_response("just the raw page text")
        assert "raw page text" in out["text"]
        assert 0.0 < out["confidence"] <= 1.0

    def test_blank_page_is_marked(self):
        out = _parse_response("###META\nconfidence: 1.0\nblank: yes\n###TEXT\n")
        assert out["is_blank"] is True

    def test_confidence_is_clamped(self):
        assert _parse_response("###META\nconfidence: 9\n###TEXT\nx")["confidence"] == 1.0
        assert _parse_response("###META\nconfidence: junk\n###TEXT\nx")["confidence"] == 0.8

    def test_markdown_fence_is_stripped(self):
        out = _parse_response("```\n###META\nconfidence: 0.9\n###TEXT\nhello\n```")
        assert out["text"].strip() == "hello"


class TestVisionRouting:
    def test_scanned_page_goes_to_vision(self):
        needs, reason = page_needs_vision("")
        assert needs and "scanned" in reason

    def test_real_text_layer_is_trusted(self):
        text = (
            "This Deed of Trust is made this 3rd day of July, 2024, between the "
            "Borrower and the Lender, securing the principal sum of $361,131.59 "
            "together with interest thereon at the rate set forth in the Note."
        )
        assert len(text) > MIN_CHARS_PER_PAGE
        needs, _ = page_needs_vision(text)
        assert not needs

    def test_corrupt_font_extraction_goes_to_vision(self):
        """Broken embedded fonts yield long strings of symbol garbage that would
        otherwise be trusted purely on length."""
        needs, reason = page_needs_vision("\u0001\u0002\u0003" * 80)
        assert needs and "corrupt" in reason


class TestSpreadsheetHelpers:
    @pytest.mark.parametrize("index,letter", [(1, "A"), (26, "Z"), (27, "AA"), (52, "AZ")])
    def test_column_letters(self, index, letter):
        assert _col_letter(index) == letter

    def test_header_row_found_below_a_title(self):
        rows = [
            ["Construction Status Report", None, None],
            [None, None, None],
            ["Category", "Cost Code", "Amount"],
            ["Demolition", "02", 2500],
        ]
        index, headers = _detect_header_row(rows)
        assert index == 2
        assert headers[:3] == ["Category", "Cost Code", "Amount"]

    def test_no_header_when_there_is_none(self):
        index, headers = _detect_header_row([[1, 2, 3], [4, 5, 6]])
        assert index is None and headers == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
