"""Tests for property-aware segmentation.

The contamination these guard against is the quiet kind: the answer is fluent,
it cites a real document, and it attributes the wrong property's facts. Ranking
cannot catch it, so segmentation has to.
"""
from __future__ import annotations

import pytest

from mangotree.chunk.segmenter import (
    CARRY_LIMIT,
    contamination_report,
    properties_for_retrieval,
    segment_text,
    segments_for_property,
    split_segments,
)

MULTI = """Team, weekly update below.

1512 Varnum tile is done. The painter starts Monday.

912 Decatur needs $4,000 for the roof. Please approve the change order.

910 Bayshore inspection passed."""


class TestSegmentation:
    def test_each_property_gets_only_its_own_text(self):
        segs = segment_text(MULTI, document_property_ids=["varnum", "decatur_st", "bayshore_910"])
        decatur = " ".join(s.text for s in segments_for_property(segs, "decatur_st")).lower()
        assert "roof" in decatur
        assert "varnum" not in decatur
        assert "bayshore" not in decatur
        assert "tile" not in decatur

    def test_money_never_crosses_properties(self):
        """The $4,000 belongs to Decatur and must appear nowhere else."""
        segs = segment_text(MULTI, document_property_ids=["varnum", "decatur_st", "bayshore_910"])
        for pid in ("varnum", "bayshore_910"):
            text = " ".join(s.text for s in segments_for_property(segs, pid))
            assert "4,000" not in text and "4000" not in text

    def test_all_three_properties_are_found(self):
        segs = segment_text(MULTI, document_property_ids=["varnum", "decatur_st", "bayshore_910"])
        assert properties_for_retrieval(segs) >= {"varnum", "decatur_st", "bayshore_910"}

    def test_904_and_910_do_not_bleed_into_each_other(self):
        text = (
            "904 Bayshore builder's risk insurance is renewed.\n\n"
            "910 Bayshore still owes the water heater change order."
        )
        segs = segment_text(text, document_property_ids=["bayshore_904", "bayshore_910"])
        a = " ".join(s.text for s in segments_for_property(segs, "bayshore_904")).lower()
        b = " ".join(s.text for s in segments_for_property(segs, "bayshore_910")).lower()
        assert "insurance" in a and "water heater" not in a
        assert "water heater" in b and "insurance" not in b

    def test_continuation_carries_the_subject_forward(self):
        """Prose continues a subject across sentences; attribution must follow."""
        text = "1512 Varnum tile is done.\n\nThe painter starts Monday."
        segs = segment_text(text, document_property_ids=["varnum"])
        assert segs[1].property_ids == ["varnum"]
        assert segs[1].attribution in {"carried", "document"}

    def test_carry_forward_is_bounded(self):
        """An unbounded carry would re-create the contamination it prevents."""
        blocks = ["1512 Varnum tile is done."] + [f"Filler line {i}." for i in range(CARRY_LIMIT + 3)]
        segs = segment_text("\n\n".join(blocks), document_property_ids=["varnum", "decatur_st"])
        carried = [s for s in segs if s.attribution == "carried"]
        assert len(carried) <= CARRY_LIMIT

    def test_single_property_document_claims_its_unattributed_text(self):
        text = "Please see the attached.\n\nThanks for the quick turnaround."
        segs = segment_text(text, document_property_ids=["chita_ct"])
        assert all(s.property_ids == ["chita_ct"] for s in segs)

    def test_multi_property_document_withholds_unattributed_text(self):
        """The asymmetry that matters: guessing here is what causes leaks."""
        text = "Please see the attached.\n\nThanks for the quick turnaround."
        segs = segment_text(text, document_property_ids=["varnum", "decatur_st"])
        assert all(s.attribution == "ambiguous" for s in segs)
        assert all(s.property_ids == [] for s in segs)

    def test_bulleted_properties_split_one_per_item(self):
        text = (
            "Draw requests this week:\n"
            "- 1512 Varnum: $12,000 for framing\n"
            "- 912 Decatur: $8,500 for the roof\n"
            "- 5901 Euclid: $3,000 for cleanup"
        )
        segs = segment_text(text, document_property_ids=["varnum", "decatur_st", "euclid_st"])
        varnum = " ".join(s.text for s in segments_for_property(segs, "varnum"))
        assert "12,000" in varnum
        assert "8,500" not in varnum and "3,000" not in varnum

    def test_report_counts_attribution_kinds(self):
        segs = segment_text(MULTI, document_property_ids=["varnum", "decatur_st", "bayshore_910"])
        report = contamination_report(segs)
        assert report["explicit"] >= 3
        assert set(report["properties"]) >= {"varnum", "decatur_st", "bayshore_910"}


class TestBareShortNames:
    """Emails in this corpus name properties by bare short name far more often
    than by full address, so a bare name has to attribute — and, more urgently,
    has to stop the previous property carrying forward onto it.

    Regression for the 2026-09-02 bug: "Decatur still needs $4,000 for the roof"
    following a Varnum paragraph was tagged ``varnum``, putting Decatur's money
    on Varnum's ledger. Bare names scored 0.41 against a 0.45 bar, so they were
    invisible both as a claim and as a veto.
    """

    BARE_ALIASES = [
        ("Varnum is complete.", "varnum"),
        ("Decatur needs a new roof.", "decatur_st"),
        ("Briardale closing is scheduled.", "briardale"),
        ("Chita is under contract.", "chita_ct"),
        ("Euclid NOI was recorded.", "euclid_st"),
        ("Tahona estate paperwork is filed.", "tahona"),
        ("Allison inspection passed.", "allison_st"),
    ]

    @pytest.mark.parametrize("text,expected", BARE_ALIASES)
    def test_a_bare_short_name_attributes(self, text, expected):
        segs = segment_text(text, document_property_ids=[])
        assert segs[0].property_ids == [expected], segs[0].attribution

    def test_a_bare_name_stops_the_previous_property_carrying_forward(self):
        segs = segment_text(
            "1512 Varnum is complete and the final draw has been released.\n\n"
            "Decatur still needs $4,000 for the roof repair.",
            document_property_ids=[],
        )
        assert segs[0].property_ids == ["varnum"]
        assert segs[1].property_ids == ["decatur_st"], (
            f"Decatur's money landed on {segs[1].property_ids}"
        )

    def test_money_stays_with_its_own_property(self):
        segs = segment_text(
            "1512 Varnum is complete.\n\nDecatur still needs $4,000 for the roof.",
            document_property_ids=[],
        )
        varnum_text = " ".join(s.text for s in segments_for_property(segs, "varnum"))
        assert "$4,000" not in varnum_text

    def test_an_ambiguous_street_still_vetoes_a_carry(self):
        # "Bayshore" alone cannot claim a segment - it is shared by 904 and 910 -
        # but it must still stop Varnum from claiming the sentence.
        segs = segment_text(
            "1512 Varnum is complete.\n\nBayshore inspection was rescheduled.",
            document_property_ids=[],
        )
        assert "varnum" not in segs[1].property_ids


class TestSplitting:
    def test_paragraphs_split_on_blank_lines(self):
        assert len(split_segments("One.\n\nTwo.\n\nThree.")) == 3

    def test_empty_text_yields_nothing(self):
        assert split_segments("") == []
        assert segment_text("") == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
