"""Tests for the rules that must never silently break.

These cover the three failure modes that would quietly corrupt the record:
  1. ingesting internal-only RKB mail, or dropping real business mail
  2. misfiling Rakesh Sir's alias-sent mail as "received"
  3. merging 904 Bayshore with 910 Bayshore

Run:  python -m pytest tests/ -v
"""
from __future__ import annotations

import pytest

from mangotree.clean.cleaner import clean_body, split_quoted, strip_signature
from mangotree.ingest.direction import Direction, resolve_direction
from mangotree.ingest.participants import (
    Decision,
    ParticipantSet,
    build_participants,
    decide,
    extract_addresses,
)
from mangotree.ingest.threading import ThreadIndex, normalize_subject, thread_key_for
from mangotree.resolve.property_resolver import ResolutionStatus, resolve_property


# ======================================================================
# 1. the participant filter
# ======================================================================
class TestParticipantFilter:
    def test_internal_only_rkb_mail_is_skipped(self):
        """Rakesh -> JP + Manjunath, no external party: not deal evidence."""
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "jp@mtreh.com, manjunath@mtreh.com",
        })
        result = decide(participants)
        assert result.decision is Decision.SKIP_INTERNAL_ONLY
        assert not result.ingest

    def test_rkb_to_external_is_ingested(self):
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "wes@roiblocks.com",
        })
        assert decide(participants).ingest

    def test_internal_thread_with_one_external_cc_is_ingested(self):
        """A single external Cc makes the whole message a business record."""
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "jp@mtreh.com",
            "Cc": "wes@lpremodel.com",
        })
        assert decide(participants).ingest

    def test_personal_mail_is_skipped_without_a_special_rule(self):
        """No registry contact, no property named -> rule 4."""
        participants = build_participants({
            "From": "rakesh.bhargava@gmail.com",
            "To": "friend@example.com",
        })
        result = decide(participants, subject="dinner on Friday")
        assert not result.ingest
        assert result.decision is Decision.SKIP_NO_SIGNAL

    def test_mail_between_two_strangers_is_skipped(self):
        participants = build_participants({
            "From": "newsletter@marketing.com",
            "To": "someone@else.com",
        })
        assert decide(participants).decision is Decision.SKIP_NO_SIGNAL

    def test_unknown_counterparties_are_deferred_to_discovery(self):
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "newcontractor@example.com",
        })
        result = decide(participants, subject="quote for the kitchen")
        assert result.decision is Decision.SKIP_NO_SIGNAL
        assert "newcontractor@example.com" in result.discovery_candidates

    def test_bcc_mail_with_no_visible_rkb_address_is_ingested(self):
        """Rule 2 has no RKB requirement, deliberately.

        Counsel writes to the builder and blind-copies Rakesh Sir. His copy
        carries no RKB address at all because Bcc is stripped from it, but the
        message is in his mailbox and concerns a live foreclosure.
        """
        participants = build_participants({
            "From": "bgallagher@g-e-law.com",
            "To": "wes@roiblocks.com",
        })
        assert decide(participants, subject="Varnum foreclosure").ingest

    def test_property_in_subject_admits_an_otherwise_unknown_message(self):
        """Rule 3 — no registry contact, but the subject names a property."""
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "stranger@example.com",
        })
        result = decide(participants, subject="RE: 4304 Varnum St roof quote")
        assert result.ingest

    def test_internal_mail_is_excluded_even_when_it_names_a_property(self):
        """Rule 1 outranks rule 3 — order matters."""
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "jp@mtreh.com",
        })
        result = decide(participants, subject="Varnum payoff statement")
        assert not result.ingest
        assert result.decision is Decision.SKIP_INTERNAL_ONLY

    def test_ambiguous_bare_alias_does_not_admit_a_message(self):
        """'Bayshore' alone cannot separate 904 from 910, so it is not a signal."""
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "stranger@example.com",
        })
        assert not decide(participants, subject="Bayshore update").ingest

    def test_unlisted_mtreh_staff_are_treated_as_internal(self):
        """A new @mtreh.com hire must not look like an external counterparty."""
        participants = build_participants({
            "From": "rakesh@mtreh.com",
            "To": "newhire@mtreh.com",
        })
        assert decide(participants).decision is Decision.SKIP_INTERNAL_ONLY

    def test_address_extraction_handles_display_names(self):
        addrs = extract_addresses('"Stone, Wes" <wes@roiblocks.com>, kelly@lpremodel.com')
        assert addrs == ["wes@roiblocks.com", "kelly@lpremodel.com"]

    def test_address_extraction_deduplicates_case_insensitively(self):
        addrs = extract_addresses("Wes@RoiBlocks.com, wes@roiblocks.com")
        assert addrs == ["wes@roiblocks.com"]


# ======================================================================
# 2. direction — the send-as alias problem
# ======================================================================
class TestDirection:
    MAILBOX = "rakesh.bhargava@gmail.com"

    def test_alias_sent_mail_is_sent_not_received(self):
        """THE critical case: composed in Gmail, sent as rakesh@mtreh.com.

        A From-header rule would call this 'received from a stranger'.
        """
        result = resolve_direction(
            mailbox=self.MAILBOX,
            from_addrs=["rakesh@mtreh.com"],
            labels=["SENT"],
        )
        assert result.direction is Direction.SENT
        assert result.via_alias is True
        assert result.author_person_id == "rakesh"

    def test_inbound_mail_from_counterparty_is_received(self):
        result = resolve_direction(
            mailbox=self.MAILBOX,
            from_addrs=["wes@roiblocks.com"],
            labels=["INBOX"],
        )
        assert result.direction is Direction.RECEIVED
        assert result.via_alias is False

    def test_archived_sent_mail_still_attributes_to_the_owner(self):
        """No SENT label, but From resolves to the mailbox owner."""
        result = resolve_direction(
            mailbox=self.MAILBOX,
            from_addrs=["rakesh@mtreh.com"],
            labels=["ARCHIVE"],
        )
        assert result.direction is Direction.SENT
        assert result.author_person_id == "rakesh"

    def test_sent_folder_wins_over_header_inspection(self):
        """Sent-folder membership is sufficient evidence on its own."""
        result = resolve_direction(
            mailbox=self.MAILBOX,
            from_addrs=["someone-unregistered@example.com"],
            labels=["SENT"],
        )
        assert result.direction is Direction.SENT

    def test_drafts_are_not_counted_as_sent(self):
        result = resolve_direction(
            mailbox=self.MAILBOX,
            from_addrs=["rakesh@mtreh.com"],
            labels=["DRAFT"],
        )
        assert result.direction is Direction.DRAFT

    def test_non_alias_sent_mail_is_not_flagged_as_alias(self):
        result = resolve_direction(
            mailbox=self.MAILBOX,
            from_addrs=[self.MAILBOX],
            labels=["SENT"],
        )
        assert result.direction is Direction.SENT
        assert result.via_alias is False


# ======================================================================
# 3. property resolution — the 904/910 hazard
# ======================================================================
class TestPropertyResolution:
    def test_904_and_910_bayshore_never_merge(self):
        a = resolve_property(subject="904 Bayshore - insurance renewal")
        b = resolve_property(subject="910 Bayshore - draw request")
        assert a.property_ids == ["bayshore_904"]
        assert b.property_ids == ["bayshore_910"]
        assert not set(a.property_ids) & set(b.property_ids)

    def test_bare_bayshore_is_ambiguous_not_guessed(self):
        """Rather than pick a loan, an unqualified street name goes to review."""
        result = resolve_property(subject="Bayshore update")
        assert result.status is not ResolutionStatus.RESOLVED
        assert result.needs_review

    def test_folder_name_maps_to_the_real_street_number(self):
        """The '9th St NW' folder is really 3731 9th St."""
        result = resolve_property(
            subject="Construction Status",
            disk_folder="9th St NW Washington DC 20010",
        )
        assert result.property_ids == ["9th_st_nw"]

    def test_multi_property_email_fans_out_to_both(self):
        result = resolve_property(
            subject="Weekly update",
            body="1512 Varnum is on track. 912 Decatur needs a change order.",
        )
        assert set(result.property_ids) == {"varnum", "decatur_st"}

    def test_attachment_filename_resolves_the_property(self):
        result = resolve_property(
            subject="Documents attached",
            filenames=["910 Bayshore draw schedule.xlsx"],
        )
        assert "bayshore_910" in result.property_ids

    def test_thread_inheritance_only_applies_without_own_signal(self):
        inherited = resolve_property(subject="Re: update", thread_property_ids=["chita_ct"])
        assert inherited.property_ids == ["chita_ct"]

        explicit = resolve_property(
            subject="Re: 5901 Euclid punch list",
            thread_property_ids=["chita_ct"],
        )
        assert explicit.property_ids == ["euclid_st"]

    def test_no_signal_is_unresolved_not_a_guess(self):
        result = resolve_property(subject="Quick question", body="Can you call me?")
        assert result.status is ResolutionStatus.UNRESOLVED
        assert result.property_ids == []

    def test_contact_hint_alone_is_below_the_bar(self):
        """Being on a deal is a hint, never an assignment."""
        result = resolve_property(subject="Following up", person_ids=["ali_parva"])
        assert result.needs_review


# ======================================================================
# 4. cleaning
# ======================================================================
class TestCleaner:
    def test_quoted_reply_is_split_and_retained(self):
        text = (
            "Yes, approved.\n\n"
            "On Mon, Jan 6, 2025 at 9:14 AM Wes Stone <wes@roiblocks.com> wrote:\n"
            "> Can we release draw 3?"
        )
        new, quoted = split_quoted(text)
        assert new == "Yes, approved."
        assert "draw 3" in quoted

    def test_signature_is_separated_from_the_message(self):
        text = "Please send the invoice.\n\n--\nWes Stone\nROI Blocks\n555-123-4567"
        body, signature = strip_signature(text)
        assert body == "Please send the invoice."
        assert "Wes Stone" in signature

    def test_signoff_mid_message_is_not_treated_as_a_signature(self):
        text = "Thanks for the update.\n\nThe draw is approved and funds go out Friday."
        body, _ = strip_signature(text)
        assert "funds go out Friday" in body

    def test_mojibake_is_repaired(self):
        result = clean_body(raw_text="Weâ€™ll release the draw")
        assert "'" in result.body_clean or "’" in result.body_clean
        assert "â€" not in result.body_clean

    def test_html_is_converted_to_text(self):
        result = clean_body(raw_html="<div><p>Draw <b>3</b> approved</p></div>")
        assert "Draw" in result.body_clean and "approved" in result.body_clean
        assert "<" not in result.body_clean

    def test_full_text_keeps_quoted_context_for_retrieval(self):
        result = clean_body(
            raw_text="Approved.\n\nOn Mon, Jan 6, 2025 at 9:14 AM Wes wrote:\n> Release draw 3?"
        )
        assert "Approved." in result.full_text
        assert "draw 3" in result.full_text


# ======================================================================
# 5. threading
# ======================================================================
class TestThreading:
    def test_reply_joins_its_parent_thread(self):
        index = ThreadIndex()
        a = thread_key_for(index, message_id="<a@mail>", subject="Draw 3")
        b = thread_key_for(
            index, message_id="<b@mail>", in_reply_to=["<a@mail>"], subject="Re: Draw 3"
        )
        assert a == b

    def test_cross_provider_messages_stitch_on_message_id(self):
        """Gmail-sent + Outlook-received halves of one conversation."""
        index = ThreadIndex()
        gmail = thread_key_for(index, message_id="<x@mtreh>", subject="910 Bayshore")
        outlook = thread_key_for(
            index, message_id="<y@roiblocks>", references=["<x@mtreh>"],
            subject="Re: 910 Bayshore",
        )
        assert gmail == outlook

    def test_late_message_merges_two_fragments(self):
        index = ThreadIndex()
        thread_key_for(index, message_id="<a@m>")
        thread_key_for(index, message_id="<c@m>")
        merged = thread_key_for(index, message_id="<b@m>", references=["<a@m>", "<c@m>"])
        assert thread_key_for(index, message_id="<a@m>") == merged
        assert thread_key_for(index, message_id="<c@m>") == merged

    def test_unrelated_messages_stay_separate(self):
        index = ThreadIndex()
        a = thread_key_for(index, message_id="<a@m>", subject="Chita")
        b = thread_key_for(index, message_id="<z@m>", subject="Euclid")
        assert a != b

    def test_subject_normalization_strips_reply_prefixes(self):
        assert normalize_subject("Re: Fwd: RE: Draw 3") == "draw 3"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
