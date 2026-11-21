"""What must hold without a model (2026-11-21): a reply that has gone out takes its waiting
draft and its to-do off the list; new mail is read twice a day and not in between; the
construction work is Manjunath Sir's; his replies keep Rakesh Sir in copy; calls come
straight from Granola."""
from datetime import datetime, timedelta, timezone

import mongomock

UTC = timezone.utc


class _Records:
    def rkb_addresses(self):
        return ["rakesh@mtreh.com", "manjunath@mtreh.com", "jp@mtreh.com"]

    def name(self, a):
        return {"rakesh@mtreh.com": "Rakesh Sir", "manjunath@mtreh.com": "Manjunath Sir"}.get(str(a).lower(), str(a))

    def people_by_address(self):
        return {"rakesh@mtreh.com": {"display_name": "Rakesh Bhargava (Rakesh Sir)"}}


def _world():
    from mangotree.brain.answered import Answered
    from mangotree.brain.store import BrainStore

    class M:
        db = mongomock.MongoClient().db
        artifacts = db["artifacts"]
        people = db["people"]

    m = M()
    return m, BrainStore(m), Answered(m, _Records())


def _mail(sha, when, frm, to=(), cc=(), thread="t1", **more):
    return {"sha256": sha, "source_type": "email", "date": when, "thread_key": thread, "subject": "Durham draw",
            "participants": {"from": [frm], "to": list(to), "cc": list(cc)}, **more}


ASKED = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)


def test_between_the_daily_runs_only_rkb_writing_in_the_conversation_to_the_person_who_wrote_starts_a_check():
    from mangotree.brain.replied import Replied, strong_sign
    m, store, answered = _world()
    asked = _mail("in", ASKED, "wes@roiblocks.com", to=["rakesh@mtreh.com"], internet_message_id="<in@x>")
    m.artifacts.insert_many([
        asked,
        _mail("fwd", ASKED + timedelta(hours=1), "rakesh@mtreh.com", to=["manjunath@mtreh.com"]),                      # "Manju, please respond": Wes has heard nothing
        _mail("other", ASKED + timedelta(hours=2), "rakesh@mtreh.com", to=["wes@roiblocks.com"], thread="t-other"),    # to Wes, about something else
    ])
    r = Replied(m, store, answered=answered)
    cands = answered.candidates(asked)
    assert [c["sha256"] for c in cands] == ["fwd", "other"] and not strong_sign(asked, cands, None)
    assert not r._due(asked, cands, {}, full=False) and r._due(asked, cands, {}, full=True)       # another subject waits for a daily run
    assert not r._due(asked, cands, {"latest": "other"}, full=True)                              # and is not weighed twice
    m.artifacts.insert_one(_mail("reply", ASKED + timedelta(hours=3), "manjunath@mtreh.com", to=["wes@roiblocks.com"], cc=["rakesh@mtreh.com"]))
    cands = answered.candidates(asked)
    assert strong_sign(asked, cands, None) and strong_sign(asked, cands, "other") and r._due(asked, cands, {"latest": "other"}, full=False)
    assert not strong_sign(asked, cands, "reply") and not r._due(asked, cands, {"latest": "reply"}, full=False)
    # A direct reply under a changed subject (another conversation key) is as strong a sign.
    m.artifacts.insert_one(_mail("direct", ASKED + timedelta(hours=5), "rakesh@mtreh.com", to=["wes@roiblocks.com"], thread="t-new", in_reply_to=["<in@x>"]))
    cands = answered.candidates(asked)
    assert cands[-1]["link"] == "direct_reply" and strong_sign(asked, cands, "reply")
    assert not r._due(asked, [], {}, full=True)


def _verdicts(answered, m, table):
    """The reading that decides whether RKB's emails answer the incoming one, played from a table: incoming sha -> status."""
    asked = []

    def check(art, candidates=None):
        cands = answered.candidates(art) if candidates is None else candidates
        asked.append(art["sha256"])
        status, owed = table[art["sha256"]]
        last = cands[-1]
        by = [] if status == "not_answered" else [{"sha": last["sha256"], "who": _Records().name(last["participants"]["from"][0]), "at": last["date"],
                                                  "subject": last.get("subject"), "link": last["link"], "reached_sender": True}]
        return {"status": status, "by": by, "what_rkb_said": "The draw is approved." if by else "", "still_owed": owed, "latest": last["sha256"],
                "checked_at": datetime.now(UTC), "candidates": len(cands), "rule": 2, "cost_usd": 0.07}
    answered.check = check
    return asked


def test_a_sent_reply_takes_the_waiting_draft_and_its_to_do_off_the_list_by_itself():
    from mangotree.brain.drafts import DraftWriter
    from mangotree.brain.replied import Replied
    m, store, answered = _world()
    m.artifacts.insert_many([_mail("in", ASKED, "wes@roiblocks.com", to=["rakesh@mtreh.com"]),
                             _mail("in2", ASKED, "kelly@roiblocks.com", to=["rakesh@mtreh.com"], thread="t2"),
                             _mail("in3", ASKED, "margarita@kcwilson.com", to=["rakesh@mtreh.com"], thread="t3")])
    reply_item = store.new_item(title="Reply to Wes on the Durham draw", owner="manjunath", kind="reply", source_sha="in")
    other_item = store.new_item(title="Check the permit before the draw is approved", owner="manjunath", kind="action", source_sha="in")
    kelly_item = store.new_item(title="Give Kelly the wire date and the payoff figure", owner="jp", kind="reply", source_sha="in2")
    deed_item = store.new_item(title="Tell KC Wilson the recorded deed will follow", owner="rakesh", kind="reply", source_sha="in3")
    store.files.insert_one({"property_id": "durham_st", "open_items": [reply_item, other_item, kelly_item, deed_item]})
    d = store.create_draft({"property_ids": ["durham_st"], "reply_to_sha": "in", "subject": "Re: Durham draw", "sender": "manjunath", "body": "Dear Wes, ...",
                            "written_at": ASKED + timedelta(hours=1)})
    writer = object.__new__(DraftWriter)
    writer.mongo, writer.store, writer.records, writer.answered = m, store, _Records(), answered
    asked = _verdicts(answered, m, {"in": ("answered", []), "in2": ("partly_answered", ["the payoff figure"]), "in3": ("not_answered", [])})
    r = Replied(m, store, writer=writer)
    opened = lambda: {i["id"]: i for i in store.files.find_one({"property_id": "durham_st"})["open_items"]}

    out = r.run()
    assert (out["drafts"], out["items"], out["checked"]) == ([], [], 0) and asked == []           # nobody at RKB has written: nothing is read, nothing is paid

    m.artifacts.insert_many([
        _mail("reply", ASKED + timedelta(hours=3), "manjunath@mtreh.com", to=["wes@roiblocks.com"], cc=["rakesh@mtreh.com"]),      # the reply to Wes, Rakesh Sir in copy
        _mail("part", ASKED + timedelta(hours=3), "jp@mtreh.com", to=["kelly@roiblocks.com"], thread="t2"),                       # the wire date, not the payoff
        _mail("tape", ASKED + timedelta(hours=3), "jp@mtreh.com", to=["margarita@kcwilson.com"], thread="t3"),                    # "please find attached data tape": same conversation, not the reply
    ])
    out = r.run()
    assert out["checked"] == 3 and sorted(asked) == ["in", "in2", "in3"] and out["cost_usd"] == 0.14        # the draft's check is paid inside recheck
    assert [x["draft_id"] for x in out["drafts"]] == [d["draft_id"]] and out["drafts"][0]["by"] == "Manjunath Sir"
    after = store.get_draft(d["draft_id"])
    assert after["status"] == "sent" and after["auto_closed"] and after["sent_sha"] == "reply" and after["answer_check"]["status"] == "answered"
    items = opened()
    assert items[reply_item["id"]]["status"] == "done" and items[reply_item["id"]]["closed_by"] == "mail" and "Manjunath Sir replied on 20 Nov 2026" in items[reply_item["id"]]["closed_note"]
    assert items[other_item["id"]]["status"] == "open"                           # a check to make is not done by a reply going out
    # Answered in part: the to-do stays, and now says what went out and what is still owed.
    assert items[kelly_item["id"]]["status"] == "open" and "Still owed: the payoff figure." in items[kelly_item["id"]]["detail"] and out["partly"][0]["item_id"] == kelly_item["id"]
    # Same conversation, same person, and not the reply: the request stays on the list.
    assert items[deed_item["id"]]["status"] == "open" and items[deed_item["id"]]["reply_check"]["status"] == "not_answered" and out["items"] == []

    out = r.run()
    assert out["checked"] == 0 and len(asked) == 3                              # the same emails are not weighed, or paid for, again
    out = r.run(full=True)
    assert out["checked"] == 0

    # The recorded deed goes out: that request now leaves the list too.
    m.artifacts.insert_one(_mail("deed", ASKED + timedelta(hours=6), "rakesh@mtreh.com", to=["margarita@kcwilson.com"], thread="t3"))
    asked.clear()
    _verdicts(answered, m, {"in3": ("answered", [])})
    out = r.run()
    assert [x["item_id"] for x in out["items"]] == [deed_item["id"]] and opened()[deed_item["id"]]["status"] == "done"
    assert store.activity.count_documents({"property_id": "durham_st", "kind": "reply_sent"}) == 2


def test_a_waiting_reply_answered_only_in_part_is_closed_but_its_to_do_stays_and_says_what_is_owed():
    from mangotree.brain.drafts import DraftWriter
    m, store, answered = _world()
    m.artifacts.insert_many([_mail("in", ASKED, "wes@roiblocks.com", to=["rakesh@mtreh.com"]),
                             _mail("reply", ASKED + timedelta(hours=3), "rakesh@mtreh.com", to=["wes@roiblocks.com"])])
    item = store.new_item(title="Reply to Wes on the draw and the permit", kind="reply", source_sha="in")
    store.files.insert_one({"property_id": "durham_st", "open_items": [item]})
    d = store.create_draft({"property_ids": ["durham_st"], "reply_to_sha": "in", "subject": "Re: Durham draw", "sender": "rakesh", "body": "Dear Wes, ...",
                            "written_at": ASKED + timedelta(hours=1)})
    writer = object.__new__(DraftWriter)
    writer.mongo, writer.store, writer.records, writer.answered = m, store, _Records(), answered
    _verdicts(answered, m, {"in": ("partly_answered", ["the permit number"])})
    after = writer.recheck(d, force=True)
    assert after["status"] == "sent"                                             # it must not wait beside a reply already sent
    it = store.files.find_one({"property_id": "durham_st"})["open_items"][0]
    assert it["status"] == "open" and "Not taken up in that reply: the permit number." in it["detail"]


def test_a_reply_marked_sent_by_hand_closes_only_the_reply_to_dos_of_that_email():
    m, store, _ = _world()
    a = store.new_item(title="Reply to Wes", kind="reply", source_sha="in")
    b = store.new_item(title="Reply to Kelly", kind="reply", source_sha="other")
    c = store.new_item(title="Decide the decking", kind="decision", source_sha="in")
    store.files.insert_one({"property_id": "durham_st", "open_items": [a, b, c]})
    closed = store.close_reply_items("in", note="Rakesh Sir marked the reply sent.", by="rakesh")
    assert [x["item_id"] for x in closed] == [a["id"]] and store.close_reply_items("in", note="again") == [] and store.close_reply_items(None, note="x") == []
    left = [i["id"] for i in store.open_items(store.files.find_one({"property_id": "durham_st"}))]
    assert left == [b["id"], c["id"]] and a["added_at"] is not None


def test_a_reply_from_manjunath_sir_always_has_rakesh_sir_in_copy_and_stays_out_of_outlook():
    from mangotree.brain.drafts import DraftWriter
    w = object.__new__(DraftWriter)
    w.records = _Records()
    w.sender_addresses = lambda s: {"rakesh": ["rakesh.b@gmail.com", "rakesh@mtreh.com"], "manjunath": ["manjunath@mtreh.com"]}.get(s, [])
    wes, kelly = {"name": "Wes", "email": "wes@roiblocks.com"}, {"name": "Kelly", "email": "kelly@roiblocks.com"}
    cc = w.with_rakesh_copied("manjunath", [wes], [kelly])
    assert [x["email"] for x in cc] == ["kelly@roiblocks.com", "rakesh@mtreh.com"] and cc[-1]["name"].startswith("Rakesh")
    assert w.with_rakesh_copied("manjunath", [wes], cc) == cc                                              # already there: not twice
    assert w.with_rakesh_copied("jp", [{"name": "", "email": "Rakesh.B@gmail.com"}], []) == []              # he is a recipient already
    assert w.with_rakesh_copied("rakesh", [wes], [kelly]) == [kelly]                                        # his own reply: nothing added


def test_new_mail_is_read_at_six_and_eleven_eastern_and_not_in_between():
    from mangotree.brain import config as bc
    from mangotree.brain.myday import due_morning
    from mangotree.briefing.morning import Scheduler
    assert bc.RUN_HOURS == (6, 11) and bc.EVENT_LOOKUPS == 5
    s = object.__new__(Scheduler)
    slot = lambda h, mi=0: (setattr(s, "_now_local", lambda: datetime(2026, 11, 23, h, mi)), s._brain_slot())[1]
    assert [slot(h) for h in (0, 5)] == [None, None]
    assert slot(6) == "brain_06" and slot(9, 59) == "brain_06"            # a run missed at 6 is made up until 10
    assert slot(10) is None
    assert slot(11) == "brain_11" and slot(14, 59) == "brain_11"
    assert [slot(h) for h in (15, 18, 23)] == [None, None, None]
    # The morning email follows the 6 am reading.
    et = lambda h, mi: datetime(2026, 11, 23, h + 5, mi, tzinfo=UTC)      # Eastern standard time is UTC-5 in November
    assert not due_morning(et(4, 30)) and not due_morning(et(6, 29)) and due_morning(et(6, 30)) and due_morning(et(10, 59)) and not due_morning(et(11, 0))


def test_a_run_is_made_once_per_slot_and_reads_until_nothing_waits(monkeypatch):
    from mangotree.brain import sweep as sweep_mod, updater as updater_mod
    from mangotree.brain.store import BrainStore
    from mangotree.briefing.morning import Scheduler

    class M:
        db = mongomock.MongoClient().db

    s = object.__new__(Scheduler)
    s.mongo, s.runs, claimed, rounds = M(), M.db["scheduled_runs"], [], []
    s._now_local = lambda: datetime(2026, 11, 23, 6, 0)
    s._claim_daily = lambda job: (job not in claimed) and (claimed.append(job) or True)
    s._release_daily = lambda job, ok: None

    class FakeUpdater:
        def __init__(self, *a):
            pass

        def run_pending(self, max_items=25):
            rounds.append(max_items)
            return {"read": 25, "drafts": 1, "errors": 0, "remaining": 30 - 25 * len(rounds) if len(rounds) < 2 else 0}

    class FakeSweep:
        def __init__(self, *a):
            pass

        def rewrite_outdated(self, limit=4):
            return {"rewritten": [1], "closed": [], "failed": []}

    monkeypatch.setattr(updater_mod, "Updater", FakeUpdater)
    monkeypatch.setattr(sweep_mod, "Sweep", FakeSweep)
    weighed = []
    s.run_replied = lambda full=False: (weighed.append(full), {"checked": 2, "drafts": [1], "items": []})[1]
    out = s.run_brain()
    assert (out["slot"], out["read"], out["drafts"], out["remaining"], out["replies_rewritten"]) == ("brain_06", 50, 2, 0, 1) and len(rounds) == 2
    assert weighed == [True] and (out["sent_checked"], out["sent_closed"]) == (2, 1)      # everything RKB sent is weighed at a daily run
    assert s.run_brain() == {"skipped": "already run"} and len(rounds) == 2            # the next minute's round does nothing
    s._now_local = lambda: datetime(2026, 11, 23, 10, 30)
    assert s.run_brain() == {"skipped": "not a run hour"}
    BrainStore(M()).set_setting("brain_enabled", False, "rakesh")
    s._now_local = lambda: datetime(2026, 11, 23, 11, 0)
    assert s.run_brain() == {"skipped": "switched off"} and claimed == ["brain_06"]     # switched off: the 11 am run is not used up


def test_construction_work_is_manjunath_sirs_in_every_reading():
    from mangotree.brain import prompts
    for text in (prompts.CORE, prompts.CORE_LEAN):
        assert "WHO GETS THE CONSTRUCTION WORK" in text and "has owner manjunath" in text and "goes out from manjunath" in text
        assert "Paying a bill that is already approved" in text
    assert "A reply about\n  the construction work" in prompts.EVENT_LEAN and "5 look-ups" in prompts.EVENT_LEAN


def test_calls_are_taken_straight_from_granola_not_from_the_folder(monkeypatch):
    from mangotree.brain.calls import SINCE, Calls
    seen = {}
    c = object.__new__(Calls)

    def past(since=SINCE, until=None):
        seen["since"] = since
        return {"seen": 3, "by_speaker": ["n1"], "by_content": ["n2"], "skipped": 1, "pick_cost_usd": 0.02}

    c.pull_past = past
    out = c.pull_new()
    assert out["taken"] == ["n1", "n2"] and out["seen"] == 3
    assert timedelta(days=3, hours=23) < datetime.now(UTC) - seen["since"] < timedelta(days=4, minutes=1) or seen["since"] == SINCE


def test_one_files_records_are_read_back_to_back_in_the_order_they_came():
    from mangotree.brain.updater import by_file
    rows = [{"n": 1, "p": ["durham_st"]}, {"n": 2, "p": []}, {"n": 3, "p": ["varnum"]}, {"n": 4, "p": ["durham_st"]}, {"n": 5, "p": []}, {"n": 6, "p": ["varnum", "durham_st"]}]
    assert [r["n"] for r in by_file(rows, lambda r: r["p"])] == [1, 4, 2, 5, 3, 6]      # no file given: the general file
    assert by_file([], lambda r: r["p"]) == []
