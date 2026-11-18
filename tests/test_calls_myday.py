"""Calls, My day and the sweep — the parts that must be exactly right without a model:
who Granola says spoke, that a quote is word for word, that a card number never stays in a
transcript, what a call writes into a file, the order of a person's day, that a task given
reaches the list and the outbox, and that a line from the last week is never put aside."""
from datetime import datetime, timedelta, timezone

import mongomock

from mangotree.brain import granola as gr
from mangotree.brain.calls import Calls, check_quotes, et_clock, et_label, roi_speaker, sha_of
from mangotree.brain.myday import TEAM, MyDay, due_morning
from mangotree.brain.store import BrainStore
from mangotree.brain.sweep import Sweep

UTC = timezone.utc


class FakeMongo:
    def __init__(self):
        self.db = mongomock.MongoClient().db
        self.artifacts = self.db["artifacts"]


def _store():
    BrainStore._indexed = True
    m = FakeMongo()
    return m, BrainStore(m)


def _same(a, b):
    """The test database hands dates back without their time zone; the moment is what is compared."""
    return a.replace(tzinfo=None) == b.replace(tzinfo=None)


def _line(text, who="them", name=None, at="2026-11-12T14:01:00Z"):
    sp = {"source": "microphone" if who == "me" else "speaker", "attribution": who}
    if name:
        sp["name"] = name
    return {"speaker": sp, "text": text, "start_time": at, "end_time": at}


# ------------------------------------------------------------------ Granola
def test_a_speaker_is_named_only_when_granola_names_them():
    note = {"transcript": [_line("We pay Friday.", "me"), _line("Good.", "them", "Wes Stone"), _line("And the permit?", "them")]}
    t = gr.turns(note)
    assert [x["who"] for x in t] == [gr.RAKESH, "Wes Stone", gr.UNNAMED]
    assert gr.speakers(note)["named"] == ["Wes Stone"] and gr.speakers(note)["separated"] is True


def test_a_call_on_one_microphone_names_nobody():
    note = {"transcript": [_line("Hello Wes.", "me"), _line("Hi Rakesh, the bill is 40,000.", "me")]}
    assert {x["who"] for x in gr.turns(note)} == {gr.NOT_SEPARATED}
    assert gr.speakers(note)["separated"] is False


def test_consecutive_lines_of_one_speaker_become_one_turn_with_its_first_time():
    note = {"transcript": [_line("One.", "me", at="2026-11-12T14:01:00Z"), _line("Two.", "me", at="2026-11-12T14:01:30Z"), _line("Three.", "them", "Kelly")]}
    t = gr.turns(note)
    assert len(t) == 2 and t[0]["words"] == "One. Two." and t[0]["at"] == datetime(2026, 11, 12, 14, 1, tzinfo=UTC)


def test_a_card_number_read_aloud_is_removed_and_money_is_left():
    rows = [{"words": "The total after tax was $5,928.18 on a 40,000 quote."},
            {"words": "Okay, what's the card number again? 4147 2023 5566 7788."},
            {"words": "123 is the security code, expiry 11 29."},
            {"words": "We closed at 1512 Varnum for $133,215.57."}]
    n = gr.scrub(rows)
    assert n >= 3
    assert "$5,928.18" in rows[0]["words"] and "40,000" in rows[0]["words"]
    assert "4147" not in rows[1]["words"] and "7788" not in rows[1]["words"]
    assert "123" not in rows[2]["words"] and "11 29" not in rows[2]["words"]
    assert "$133,215.57" in rows[3]["words"]


def test_a_sixteen_digit_run_is_removed_even_with_no_word_near_it():
    rows = [{"words": "it is 4147202355667788 okay"}]
    assert gr.scrub(rows) == 1 and "4147" not in rows[0]["words"]


def test_ordinary_talk_about_dates_and_expiry_keeps_its_numbers():
    rows = [{"words": "The insurance expires November 30 and the permit number is 2026."}]
    assert gr.scrub(rows) == 0


def test_call_time_is_the_first_spoken_line_not_when_the_note_was_made():
    note = {"created_at": "2026-11-12T13:59:00Z", "transcript": [_line("Hi", "me", at="2026-11-12T14:01:14Z")]}
    assert gr.times(note)["start"] == datetime(2026, 11, 12, 14, 1, 14, tzinfo=UTC)
    assert gr.times({"created_at": "2026-11-12T13:59:00Z"})["start"] == datetime(2026, 11, 12, 13, 59, tzinfo=UTC)


# -------------------------------------------------------------------- calls
def test_times_are_shown_in_us_eastern():
    at = datetime(2026, 11, 12, 14, 1, tzinfo=UTC)        # after the clocks went back: EST, UTC-5
    assert et_clock(at) == "9:01 AM" and et_label(at).startswith("12 Nov 2026, 9:01 AM")
    assert et_clock(datetime(2026, 7, 1, 16, 30, tzinfo=UTC)) == "12:30 PM"


def test_an_roi_person_is_recognised_by_full_or_first_name_only():
    assert roi_speaker(["Jaspreet Pahwa", "Wes Stone"]) == "Wes Stone"
    assert roi_speaker(["Kelly"]) == "Kelly"
    assert roi_speaker(["W", "Manjunath", "Wesley Snipes"]) is None


def test_a_quote_that_is_not_in_the_transcript_is_removed_and_the_point_stays():
    turns = [{"words": "Ok so I will pay it “Friday” then."}, {"words": "वो मैं देख लेता हूँ सर"}]
    secs = [{"points": [{"quote": "I will  pay it \"Friday\" then.", "text": "a"}, {"quote": "वो मैं देख लेता हूँ सर", "text": "b"},
                        {"quote": "I will pay it Monday.", "text": "c"}, {"quote": "", "text": "d"}]}]
    assert check_quotes(secs, turns) == 1
    p = secs[0]["points"]
    assert p[0]["quote"] and p[1]["quote"] and p[2]["quote"] == "" and p[2]["quote_removed"] == "I will pay it Monday." and p[2]["text"] == "c"


def _call(note="not_abc", at=datetime(2026, 11, 12, 14, 0, tzinfo=UTC)):
    return {"note_id": note, "title": "Call with Wes", "started_at": at, "speakers": {"Rakesh Sir": 3, "Wes Stone": 4}}


def test_a_call_closes_changes_and_adds_to_dos_and_leaves_its_mark():
    m, st = _store()
    st.save_file("varnum", {"open_items": [], "facts": [{"key": "Finish date", "value": "15 Nov"}]})
    old = st.add_item("varnum", title="Ask Wes when drywall finishes", owner="rakesh")
    keep = st.add_item("varnum", title="Pay the drywall balance", owner="jp")
    d = st.create_draft({"property_ids": ["varnum"], "subject": "Re: drywall", "body": "When will drywall finish?", "to": []})
    calls = Calls(m, st, client=object())
    parts = [{"property_id": "varnum", "topic": "drywall", "summary": "s", "points": [{"kind": "promise", "text": "t", "by": "Wes Stone", "at": "9:05 AM", "quote": "Next week."}]}]
    out = calls._write("varnum", _call(), parts, {
        "title": "Wes on Varnum", "summary": "Wes: \"Next week.\"", "relevance": "material", "headline": None, "status": None,
        "facts_set": [{"key": "finish date", "value": "end of December (call 2026-11-12)"}], "facts_remove": [],
        "items_close": [{"id": old["id"], "note": "Wes on the 12 Nov call: \"Next week.\""}],
        "items_update": [{"id": keep["id"], "why": "date said", "due": "2026-11-20"}],
        "items_add": [{"id": "i-fixed00001", "title": "Check the drywall photos", "detail": "", "owner": "manjunath", "kind": "action", "due": None}],
        "drafts_outdated": [{"draft_id": d["draft_id"], "why": "Answered on the call."}]})
    assert out == {"changed": ["facts"], "items_added": 1, "items_closed": 1, "items_updated": 1, "drafts_outdated": 1}
    f = st.get_file("varnum")
    by_id = {i["id"]: i for i in f["open_items"]}
    assert by_id[old["id"]]["status"] == "done" and by_id[old["id"]]["closed_by"] == "call" and by_id[old["id"]]["closed_by_sha"] == sha_of("not_abc")
    assert _same(by_id[keep["id"]]["due"], datetime(2026, 11, 20))
    new = by_id["i-fixed00001"]
    assert new["owner"] == "manjunath" and new["source_sha"] == "granola-not_abc" and _same(new["opened_at"], datetime(2026, 11, 12, 14, 0))
    assert len(f["facts"]) == 1 and f["facts"][0]["value"].startswith("end of December") and _same(f["facts"][0]["as_of"], datetime(2026, 11, 12, 14, 0))
    assert st.get_draft(d["draft_id"])["stale"]["kind"] == "call"
    act = st.activity.find_one({"property_id": "varnum", "sha": "granola-not_abc"})
    assert act["kind"] == "call" and act["call"] == "not_abc" and act["quotes"][0]["quote"] == "Next week." and _same(act["at"], datetime(2026, 11, 12, 14, 0))


def test_applying_the_same_call_twice_adds_nothing_twice():
    m, st = _store()
    st.save_file("varnum", {"open_items": []})
    calls = Calls(m, st, client=object())
    data = {"title": "t", "summary": "s", "relevance": "minor", "headline": None, "status": None, "facts_set": [], "facts_remove": [], "items_close": [],
            "items_update": [], "items_add": [{"id": "i-same000001", "title": "Send the lien waiver", "detail": "", "owner": "jp", "kind": "action", "due": None}], "drafts_outdated": []}
    calls._write("varnum", _call(), [], data)
    assert calls._write("varnum", _call(), [], data)["items_added"] == 0
    assert len(st.get_file("varnum")["open_items"]) == 1 and st.activity.count_documents({"property_id": "varnum"}) == 1


def test_a_replay_keeps_the_newer_headline_of_the_real_file():
    m, st = _store()
    st.save_file("varnum", {"open_items": [], "headline": "As of today", "status": "today's words"})
    Calls(m, st, client=object())._write("varnum", _call(), [], {"title": "t", "summary": "s", "relevance": "minor", "headline": "from the trial", "status": "old words",
                                                               "facts_set": [], "facts_remove": [], "items_close": [], "items_update": [], "items_add": [], "drafts_outdated": []}, keep_text=False)
    f = st.get_file("varnum")
    assert f["headline"] == "As of today" and f["status"] == "today's words"


# ------------------------------------------------------------------- my day
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)      # 10 am Eastern, Wed 18 Nov


def _day(st):
    st.save_file("varnum", {"open_items": []})
    st.save_file("_general", {"open_items": []})
    return MyDay(st.mongo, st, follow=lambda: [], outbox=FakeOutbox())


class FakeOutbox:
    def __init__(self):
        self.sent = []

    def queue(self, **kw):
        self.sent.append(kw)
        return {"outbox_id": f"ob-{len(self.sent)}", "status": "queued"}


def test_a_person_sees_only_their_own_and_the_late_one_comes_first():
    m, st = _store()
    md = _day(st)
    st.add_item("varnum", title="Review the bathroom bill", owner="manjunath", opened_at=NOW - timedelta(days=1))
    late = st.add_item("varnum", title="Send the payoff letter", owner="jp", due="2026-11-14", opened_at=NOW - timedelta(days=6))
    soon = st.add_item("varnum", title="Reconcile the Durham draw", owner="jp", due="2026-11-25", opened_at=NOW - timedelta(days=2))
    plain = st.add_item("varnum", title="File the insurance certificate", owner="jp", opened_at=NOW - timedelta(days=1))
    b = md.board("jp", today=NOW)
    assert [c["item_id"] for c in b["now"]] == [late["id"], soon["id"], plain["id"]]
    assert b["left"] == 3 and b["late"] == 1 and b["now"][0]["why_now"] == "Was due 4 days ago"
    assert md.board("manjunath", today=NOW)["left"] == 1 and md.board("shivansh", today=NOW)["left"] == 0


def test_only_five_are_shown_now_and_the_rest_wait():
    m, st = _store()
    md = _day(st)
    for n in range(8):
        st.add_item("varnum", title=f"Task {n}", owner="rakesh", opened_at=NOW - timedelta(days=n))
    b = md.board("rakesh", today=NOW)
    assert len(b["now"]) == 5 and len(b["later"]) == 3 and b["left"] == 8


def test_a_reply_already_written_is_one_card_not_two():
    m, st = _store()
    md = _day(st)
    st.add_item("varnum", title="Reply to Wes on the EOI", owner="rakesh", kind="reply", source_sha="abc")
    d = st.create_draft({"property_ids": ["varnum"], "reply_to_sha": "abc", "sender": "rakesh", "subject": "Re: EOI", "to": [{"name": "Wes Stone"}], "body": "x"})
    other = st.create_draft({"property_ids": ["varnum"], "reply_to_sha": "zzz", "sender": "rakesh", "subject": "Re: draws", "to": [{"name": "Kelly Stone"}], "body": "x"})
    cards = md.board("rakesh", today=NOW)["now"]
    assert len(cards) == 2 and {c["type"] for c in cards} == {"reply"}
    assert {c["draft_id"] for c in cards} == {d["draft_id"], other["draft_id"]}
    assert next(c for c in cards if c["draft_id"] == other["draft_id"])["title"].startswith("Send the reply to Kelly Stone")


def test_bills_and_site_checks_reach_the_right_person():
    m, st = _store()
    follow = [{"key": "approve_bill:varnum:1", "kind": "approve_bill", "owner": "manju", "property_id": "varnum", "property": "1512 Varnum St NW", "title": "Approve or query: Drywall",
               "detail": "", "since": NOW - timedelta(days=4), "days": 4, "level": "high", "amount": 5100.0, "tab": "sheet"},
              {"key": "pay_bills:varnum:2", "kind": "pay_bills", "owner": "jp", "property_id": "varnum", "property": "1512 Varnum St NW", "title": "1 approved bill not yet paid",
               "detail": "", "since": NOW - timedelta(days=1), "days": 1, "level": "normal", "amount": 900.0, "tab": "sheet"},
              {"key": "no_log:varnum:3", "kind": "no_log", "owner": "wes", "property_id": "varnum", "property": "1512 Varnum St NW", "title": "No daily log", "detail": "", "since": None,
               "days": None, "level": "normal", "tab": "site"}]
    st.save_file("varnum", {"open_items": []})
    md = MyDay(m, st, follow=lambda: follow, outbox=FakeOutbox())
    mj = md.board("manjunath", today=NOW)["now"]
    assert len(mj) == 1 and mj[0]["type"] == "bill" and mj[0]["overdue"] and mj[0]["amount"] == 5100.0
    assert [c["type"] for c in md.board("jp", today=NOW)["now"]] == ["bill"] and md.board("rakesh", today=NOW)["left"] == 0


def test_a_task_from_rakesh_sir_lands_on_the_list_first_and_an_email_goes():
    m, st = _store()
    md = _day(st)
    st.add_item("varnum", title="Send the payoff letter", owner="jp", due="2026-11-10")
    out = md.assign({"user_id": "rakesh", "name": "Rakesh Sir"}, to="jp", title="Call the bank about next-day ACH", detail="Ask for the cut-off time.", due="2026-11-19", pid="varnum")
    assert out["item"]["owner"] == "jp" and out["item"]["assigned_by"] == "rakesh" and out["email"]["to"] == TEAM["jp"]["email"]
    b = md.board("jp", today=NOW)
    assert b["now"][0]["title"] == "Call the bank about next-day ACH" and b["now"][0]["why_now"] == "Rakesh Sir asked you to do this"
    mail = md._outbox.sent[0]
    assert mail["kind"] == "task_given" and mail["to"] == [("JP Sir", "jp@mtreh.com")] and mail["subject"] == "Task from Rakesh Sir: Call the bank about next-day ACH"
    assert "Rakesh Sir has asked you to do this" in mail["text"] and "Ask for the cut-off time." in mail["text"] and "1512 Varnum" in mail["text"]
    assert st.activity.find_one({"kind": "task_given"})["to"] == "jp"


def test_a_task_can_only_be_given_to_the_team_and_must_say_something():
    m, st = _store()
    md = _day(st)
    for bad in ({"to": "wes", "title": "Send photos"}, {"to": "jp", "title": "ok"}):
        try:
            md.assign({"user_id": "rakesh"}, **bad)
            assert False, "should have refused"
        except ValueError:
            pass
    assert md._outbox.sent == []


def test_a_test_email_says_test_in_its_subject():
    m, st = _store()
    md = _day(st)
    md.assign({"user_id": "rakesh", "name": "Rakesh Sir"}, to="shivansh", title="Check the morning email", test=True)
    assert md._outbox.sent[0]["subject"].startswith("TEST — Task from Rakesh Sir")
    st.add_item("varnum", title="Review the bathroom bill", owner="shivansh", due="2026-11-18")
    mail = md.morning_mail("shivansh", today=NOW, test=True)
    assert mail["subject"].startswith("TEST — Your day, 18 Nov: 2 things first") and mail["to"] == ("Shivansh", "shivansh@mtreh.com")
    assert "1. Check the morning email" in mail["text"] and "Rakesh Sir asked you to do this" in mail["text"]


def test_what_was_closed_today_counts_as_done_today():
    m, st = _store()
    md = _day(st)
    a = st.add_item("varnum", title="Send the payoff letter", owner="jp")
    st.close_item("varnum", a["id"], note="sent", by="jp")
    b = md.board("jp")
    assert b["left"] == 0 and len(b["done_today"]) == 1 and b["done_today"][0]["title"] == "Send the payoff letter"


def test_the_morning_email_is_due_from_four_thirty_eastern():
    assert not due_morning(datetime(2026, 11, 18, 9, 29, tzinfo=UTC))      # 4:29 am EST
    assert due_morning(datetime(2026, 11, 18, 9, 30, tzinfo=UTC))          # 4:30 am EST
    assert due_morning(datetime(2026, 11, 18, 13, 59, tzinfo=UTC))         # 8:59 am EST
    assert not due_morning(datetime(2026, 11, 18, 14, 0, tzinfo=UTC))      # 9:00 am EST
    assert due_morning(datetime(2026, 7, 1, 8, 30, tzinfo=UTC))            # 4:30 am EDT


# -------------------------------------------------------------------- sweep
def test_the_sweep_closes_puts_aside_and_rewords_and_nothing_is_deleted():
    m, st = _store()
    st.save_file("varnum", {"open_items": []})
    done = st.add_item("varnum", title="Ask Wes for the drywall date", owner="rakesh")
    twin = st.add_item("varnum", title="Get drywall date from Wes", owner="rakesh")
    old = st.add_item("varnum", title="Review the 2025 tax protest", owner="jp")
    vague = st.add_item("varnum", title="Insurance", owner="jp")
    sw = Sweep(m, st)
    n = sw.apply("varnum", [
        {"id": done["id"], "verdict": "done", "why": "Wes gave the date on the 12 Nov call.", "was": {"title": done["title"]}},
        {"id": twin["id"], "verdict": "duplicate", "same_as": done["id"], "why": "same", "was": {"title": twin["title"]}},
        {"id": old["id"], "verdict": "put_aside", "why": "untouched", "was": {"title": old["title"]}},
        {"id": vague["id"], "verdict": "keep", "why": "unclear", "title": "Send Sharon the builder's risk certificate", "due": "2026-11-21", "was": {"title": vague["title"]}}],
        today=NOW)
    assert n == {"done": 1, "duplicate": 1, "put_aside": 1, "reworded": 1, "kept": 1}
    items = {i["id"]: i for i in st.get_file("varnum")["open_items"]}
    assert len(items) == 4                                              # every line is still there
    assert items[done["id"]]["status"] == "done" and items[done["id"]]["closed_by"] == "sweep"
    assert items[twin["id"]]["status"] == "dropped" and items[twin["id"]]["closed_note"] == "Same as: Ask Wes for the drywall date"
    assert items[old["id"]]["status"] == "dropped" and "Put aside on 18 Nov 2026" in items[old["id"]]["closed_note"]
    assert items[vague["id"]]["status"] == "open" and items[vague["id"]]["title"].startswith("Send Sharon") and _same(items[vague["id"]]["due"], datetime(2026, 11, 21))
    assert st.reopen_item("varnum", old["id"]) and BrainStore.open_items(st.get_file("varnum"))[0]["status"] == "open"
    assert st.activity.find_one({"kind": "sweep"})["title"] == "To-dos read over: 1 closed as done, 1 said twice, 1 put aside"


def test_the_date_behind_a_to_do_is_its_email_or_its_call():
    m, st = _store()
    m.artifacts.insert_one({"sha256": "abc", "date": datetime(2026, 11, 15, tzinfo=UTC)})
    m.db["calls"].insert_one({"note_id": "not_x", "started_at": datetime(2026, 11, 3, 17, 0, tzinfo=UTC)})
    d = Sweep(m, st)._dates(["abc", "granola-not_x", "missing"])
    assert set(d) == {"abc", "granola-not_x"} and _same(d["abc"], datetime(2026, 11, 15)) and _same(d["granola-not_x"], datetime(2026, 11, 3, 17, 0))


def test_a_long_call_is_read_in_parts_cut_between_turns_and_a_short_one_is_not_cut():
    from mangotree.brain.calls import split_turns
    short = [{"words": "x" * 1000} for _ in range(35)]                 # 35,000 characters: one part
    assert len(split_turns(short)) == 1
    long = [{"words": "y" * 1000, "n": n} for n in range(60)]           # 60,000: two parts of about half each
    parts = split_turns(long)
    assert len(parts) == 2 and [t["n"] for p in parts for t in p] == list(range(60))
    assert abs(len(parts[0]) - len(parts[1])) <= 2


def test_the_days_reading_sets_the_order_keeps_one_line_per_matter_and_lets_only_new_urgent_lines_in_front():
    from mangotree.core import eastern
    m, st = _store()
    md = _day(st)
    a = st.add_item("varnum", title="Reply to Wes on the senior mortgage", owner="rakesh", due="2026-11-10", opened_at=NOW - timedelta(days=9))
    b = st.add_item("varnum", title="Decide the lien amount", owner="rakesh", kind="decision", opened_at=NOW - timedelta(days=1))
    c = st.add_item("varnum", title="Answer Wes about the mortgage payments", owner="rakesh", due="2026-11-12", opened_at=NOW - timedelta(days=7))
    key = lambda i: f"todo:varnum:{i['id']}"
    md.plans.insert_one({"person": "rakesh", "day": f"{eastern.to_et(NOW):%Y-%m-%d}", "seen": [key(a), key(b), key(c)],
                         "order": [{"key": key(b), "why_now": "Title is waiting for the amount."}, {"key": key(a), "why_now": "Wes has asked three times."}],
                         "doubts": [{"key": key(c), "why": "same matter", "covered_by": key(a)}]})
    late_new = st.add_item("varnum", title="Sign the payoff letter", owner="rakesh", due="2026-11-17")      # came in after the reading, already late
    plain_new = st.add_item("varnum", title="Read the insurance quote", owner="rakesh")                     # came in after, not urgent
    got = [x["item_id"] for x in md.board("rakesh", today=NOW)["now"]]
    assert got == [late_new["id"], b["id"], a["id"], plain_new["id"], c["id"]]
    cards = {x["item_id"]: x for x in md.board("rakesh", today=NOW)["now"]}
    assert cards[b["id"]]["why_now"] == "Title is waiting for the amount." and cards[c["id"]]["covered_by"] == key(a)


def test_an_overtaken_reply_is_marked_with_the_reason_and_a_call_mark_is_not_overwritten():
    m, st = _store()
    a = st.create_draft({"property_ids": ["varnum"], "subject": "Re: drywall", "body": "x", "to": []})
    b = st.create_draft({"property_ids": ["varnum"], "subject": "Re: windows", "body": "x", "to": []})
    c = st.create_draft({"property_ids": ["varnum"], "subject": "Re: permit", "body": "x", "to": [], "stale": {"kind": "call", "why": "Settled on the 12 Nov call."}})
    n = Sweep(m, st).mark_replies([{"draft_id": a["draft_id"], "verdict": "outdated", "why": "The bill was paid on 16 Nov."},
                                   {"draft_id": b["draft_id"], "verdict": "not_needed", "why": "Rakesh Sir answered on 13 Nov."},
                                   {"draft_id": c["draft_id"], "verdict": "outdated", "why": "something else"}], today=NOW)
    assert n == 2
    assert st.get_draft(a["draft_id"])["stale"]["kind"] == "record" and st.get_draft(a["draft_id"])["stale"]["why"] == "The bill was paid on 16 Nov."
    assert st.get_draft(b["draft_id"])["stale"]["kind"] == "not_needed"
    assert st.get_draft(c["draft_id"])["stale"]["why"] == "Settled on the 12 Nov call."


def test_a_task_due_today_is_not_late_until_tomorrow():
    m, st = _store()
    md = _day(st)
    st.add_item("varnum", title="Pay the materials bill", owner="jp", due="2026-11-18")
    c = md.board("jp", today=NOW)["now"][0]
    assert c["overdue"] is False and c["why_now"] == "Due today"
    assert md.board("jp", today=NOW + timedelta(days=1))["now"][0]["overdue"] is True


def test_what_the_trial_decided_is_done_to_the_real_files_without_the_model():
    m, st = _store()
    st.save_file("varnum", {"open_items": []})
    a = st.add_item("varnum", title="Ask Wes for the drywall date", owner="rakesh")
    b = st.add_item("varnum", title="Review the 2025 tax protest", owner="jp")
    live = st.create_draft({"property_ids": ["varnum"], "subject": "Re: drywall", "body": "x", "to": []})
    m.db["sweep_runs"].insert_one({"where": "trial", "at": NOW, "property_id": "varnum", "replies": [],
                                   "items": [{"id": a["id"], "verdict": "done", "why": "Wes gave the date on the 12 Nov call.", "was": {"title": a["title"]}},
                                             {"id": b["id"], "verdict": "put_aside", "why": "untouched", "was": {"title": b["title"]}},
                                             {"id": "i-gone", "verdict": "done", "why": "no longer there", "was": {"title": "x"}}]})
    m.db["trial_reply_drafts"].insert_one({"draft_id": live["draft_id"], "status": "ready", "stale": {"kind": "record", "why": "The bill was paid on 16 Nov."}})
    out = Sweep(m, st).replay_trial(today=NOW)
    assert out["done"] == 1 and out["put_aside"] == 1 and out["replies_marked"] == 1 and out["runs"] == 1
    items = {i["id"]: i for i in st.get_file("varnum")["open_items"]}
    assert items[a["id"]]["status"] == "done" and items[b["id"]]["status"] == "dropped"
    assert st.get_draft(live["draft_id"])["stale"]["why"] == "The bill was paid on 16 Nov."
    again = Sweep(m, st).replay_trial(today=NOW)                       # a second time changes nothing
    assert again["done"] == 0 and again["put_aside"] == 0 and again["replies_marked"] == 0


def test_only_the_last_week_stays_open_by_the_date_of_what_raised_it():
    """The strict rule: a to-do is as old as the email or call behind it, not as the day the system wrote it down."""
    m, store = _store()
    today = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    built = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)                      # the file was first built inside the week
    item = lambda i, **k: {"id": i, "title": f"to-do {i}", "detail": "", "owner": "rakesh", "kind": "action", "due": None, "status": "open", "opened_at": built, "source_sha": None, **k}
    m.artifacts.insert_many([{"sha256": "mail-new", "date": today - timedelta(days=2)}, {"sha256": "mail-old", "date": today - timedelta(days=20)}])
    m.db["calls"].insert_many([
        {"note_id": "c-old", "status": "applied", "started_at": today - timedelta(days=12), "applied": {"live": {}}},
        {"note_id": "c-new", "status": "applied", "started_at": today - timedelta(days=1), "applied": {"live": {"durham_st": {"changes": {"items_update": [{"id": "touched", "why": "moved"}]}}}}}])
    store.files.insert_one({"property_id": "durham_st", "built_at": built, "open_items": [
        item("from-build"),                                                # carried over from older history at the first build
        item("touched"),                                                   # the same, but a call this week changed it
        item("new-mail", source_sha="mail-new"), item("old-mail", source_sha="mail-old"),
        item("old-call", source_sha="granola-c-old", opened_at=today - timedelta(days=12)), item("new-call", source_sha="granola-c-new"),
        item("by-hand", opened_at=today - timedelta(days=3), by="rakesh"),   # written by a person inside the week
        item("given", assigned_by="rakesh", opened_at=today - timedelta(days=30))]})
    store.drafts.insert_many([
        {"draft_id": "d-old", "status": "ready", "subject": "Re: old", "in_reply_to": {"date": today - timedelta(days=9), "subject": "old"}},
        {"draft_id": "d-new", "status": "ready", "subject": "Re: new", "in_reply_to": {"date": today - timedelta(days=2), "subject": "new"}}])
    sw = Sweep(m, store)
    look = sw.week_cut(today=today)                                        # listed only: nothing changes
    assert sorted(r["id"] for r in look["put_aside"]) == ["from-build", "old-call", "old-mail"] and look["kept"] == 5
    assert [r["draft_id"] for r in look["replies_put_aside"]] == ["d-old"]
    assert len(BrainStore.open_items(store.get_file("durham_st"))) == 8 and store.drafts.count_documents({"status": "ready"}) == 2
    done = sw.week_cut(today=today, apply=True)
    assert len(done["put_aside"]) == 3
    left = {i["id"] for i in BrainStore.open_items(store.get_file("durham_st"))}
    assert left == {"touched", "new-mail", "new-call", "by-hand", "given"}
    gone = {i["id"]: i for i in store.get_file("durham_st")["open_items"] if i["status"] != "open"}
    assert gone["old-mail"]["status"] == "dropped" and "an email of 29 Oct" in gone["old-mail"]["closed_note"] and "Reopen it" in gone["old-mail"]["closed_note"]
    assert "older history" in gone["from-build"]["closed_note"]
    d = store.drafts.find_one({"draft_id": "d-old"})
    assert d["status"] == "dismissed" and "older than one week" in d["put_aside"]["why"] and store.drafts.find_one({"draft_id": "d-new"})["status"] == "ready"
    assert store.reopen_item("durham_st", "old-mail") and "old-mail" in {i["id"] for i in BrainStore.open_items(store.get_file("durham_st"))}   # nothing is lost
