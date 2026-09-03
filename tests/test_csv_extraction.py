"""CSV was routed to the spreadsheet extractor but never handled by it.

The live failure was a draw-schedule export that produced no text at all. CSVs
carry money like any other sheet, so they get the same cell-level treatment.
"""
from __future__ import annotations

import pytest

from mangotree.extract.spreadsheet import _coerce_number, extract_workbook


def _write(tmp_path, name: str, content: bytes):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_a_comma_separated_export_is_extracted(tmp_path):
    path = _write(
        tmp_path, "draw.csv",
        b"Item,Budget,Spent\nRoofing,12500.00,11800.00\nHVAC,8200.00,8200.00\n",
    )

    result = extract_workbook(path)

    assert result.engine == "csv"
    assert len(result.sheets) == 1
    text = result.text
    assert "Roofing" in text
    assert "12500" in text or "12,500" in text


def test_money_cells_are_detected_in_a_csv(tmp_path):
    path = _write(
        tmp_path, "budget.csv",
        b"Line,Amount\nDraw 2,45000.00\nRetainage,5000.00\n",
    )

    result = extract_workbook(path)
    money = [c for sheet in result.sheets for c in sheet.cells if c.is_money]

    assert money, "a CSV of dollar amounts must yield money cells"


def test_a_semicolon_delimited_export_is_not_read_as_one_column(tmp_path):
    """European exports use semicolons; sniffing keeps the grid intact."""
    path = _write(
        tmp_path, "euro.csv",
        b"Item;Budget;Spent\nRoofing;12500;11800\nHVAC;8200;8200\n",
    )

    result = extract_workbook(path)

    assert result.sheets[0].n_cols >= 3


def test_a_non_utf8_csv_still_extracts(tmp_path):
    path = _write(tmp_path, "latin.csv", "Item,Notes\nRoof,caf\xe9 trim\n".encode("cp1252"))

    result = extract_workbook(path)

    assert result.engine == "csv"
    assert "Roof" in result.text


def test_an_empty_csv_does_not_raise(tmp_path):
    path = _write(tmp_path, "empty.csv", b"")

    result = extract_workbook(path)

    assert result.engine in {"csv", "failed"}


class TestNumberCoercion:
    """CSV has no types, so numeric fields must be recovered from text."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("45000.00", 45000.0),
            ("45,000.00", 45000.0),
            ("$45,000.00", 45000.0),
            ("(5,000.00)", -5000.0),
            ("-250", -250),
            ("1234", 1234),
        ],
    )
    def test_money_shapes_become_numbers(self, text, expected):
        assert _coerce_number(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "007",            # leading zeros carry meaning
            "2024-10-15",     # a date
            "N/A",
            "",
            "12.34.56",
            "5551234567890",  # long digit runs are account/phone numbers
            "Unit 4B",
        ],
    )
    def test_identifiers_and_text_are_left_alone(self, text):
        assert isinstance(_coerce_number(text), str)

    def test_a_bare_five_digit_zip_is_accepted_as_a_number(self):
        """Not a defect worth chasing: a bare 20011 is indistinguishable from a
        quantity, and Excel converts it identically. The column header is what
        decides whether it is treated as money."""
        assert _coerce_number("20011") == 20011
