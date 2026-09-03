"""A malformed header must cost us that header, never the whole message.

Both cases here were found in live Outlook mail, and each one silently dropped a
real message: one of them a property closing email carrying fifteen attachments.
The pipeline records such failures rather than hiding them, but a recorded loss
is still a loss, so these are regression tests, not hypotheticals.
"""
from __future__ import annotations

import pytest

from mangotree.ingest.mime_parser import _decode_bytes, parse_rfc822


def _message(headers: str, body: str = "Body text here.") -> bytes:
    return (headers.strip() + "\n\n" + body).encode("utf-8", errors="replace")


class TestMalformedDateHeader:
    """``parsedate_to_datetime`` returns None on a bad date, then unpacks it."""

    BAD_DATES = [
        "Not A Date",
        "",
        "Mon, 32 Xxx 2024 99:99:99 +9999",
        "0",
    ]

    @pytest.mark.parametrize("bad_date", BAD_DATES)
    def test_a_bad_date_does_not_lose_the_message(self, bad_date):
        raw = _message(
            f"From: Neha Jha <neha@mtreh.com>\n"
            f"To: rakesh@mtreh.com\n"
            f"Subject: Closing on 513 Allison St\n"
            f"Date: {bad_date}\n"
            f"Message-ID: <bad-date@example.com>"
        )

        parsed = parse_rfc822(raw)

        assert parsed.subject == "Closing on 513 Allison St"
        assert "Body text here." in parsed.body_text
        assert parsed.headers["from"]

    def test_a_bad_date_leaves_other_headers_fully_parsed(self):
        """The blast radius stays at one header: the rest decode normally."""
        raw = _message(
            "From: Neha Jha <neha@mtreh.com>\n"
            "Subject: =?utf-8?B?UmU6IDUxMyBBbGxpc29u?=\n"
            "Date: garbage\n"
            "Message-ID: <x@example.com>"
        )

        parsed = parse_rfc822(raw)

        # RFC 2047 decoding still happened for the subject.
        assert parsed.subject == "Re: 513 Allison"
        assert parsed.date is None

    def test_a_good_date_is_still_parsed(self):
        raw = _message(
            "From: a@b.com\n"
            "Subject: Normal\n"
            "Date: Tue, 15 Oct 2024 10:30:00 -0400"
        )

        parsed = parse_rfc822(raw)

        assert parsed.date is not None
        assert parsed.date.year == 2024
        assert parsed.date.month == 10


class TestUnknownCharset:
    """Codec labels that are legal in mail but absent from Python's registry."""

    def test_windows_874_is_decoded_not_raised(self):
        # cp874 is the same codec under the name Python actually registers.
        payload = "งบประมาณ".encode("cp874")

        assert _decode_bytes(payload, "windows-874") == "งบประมาณ"

    @pytest.mark.parametrize(
        "label", ["windows-1252", "unknown-8bit", "x-unknown", "", None, "utter-nonsense"]
    )
    def test_no_charset_label_can_raise(self, label):
        assert isinstance(_decode_bytes(b"budget \xa3400", label), str)

    def test_a_message_in_an_unknown_charset_still_yields_its_body(self):
        raw = (
            b"From: somchai@example.co.th\r\n"
            b"Subject: Invoice\r\n"
            b'Content-Type: text/plain; charset="windows-874"\r\n'
            b"\r\n" + "งบประมาณ 400".encode("cp874")
        )

        parsed = parse_rfc822(raw)

        assert "400" in parsed.body_text
        assert parsed.subject == "Invoice"


class TestAttachmentsSurviveBadHeaders:
    def test_attachments_are_kept_when_the_date_is_malformed(self):
        """The live failure lost fifteen attachments off a closing email."""
        raw = (
            b"From: title@example.com\r\n"
            b"Subject: URGENT RE: Closing\r\n"
            b"Date: not-a-real-date\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n'
            b"\r\n"
            b"--B\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Settlement statement attached.\r\n"
            b"--B\r\n"
            b'Content-Type: application/pdf; name="ALTA.pdf"\r\n'
            b"Content-Transfer-Encoding: base64\r\n"
            b'Content-Disposition: attachment; filename="ALTA.pdf"\r\n'
            b"\r\n"
            b"JVBERi0xLjQK\r\n"
            b"--B--\r\n"
        )

        parsed = parse_rfc822(raw)

        assert len(parsed.attachments) == 1
        assert parsed.attachments[0].filename == "ALTA.pdf"
        assert parsed.attachments[0].size > 0
        assert "Settlement statement" in parsed.body_text
