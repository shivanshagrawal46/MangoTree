"""The parts of the brain that must be exactly right without a model: which
workbook row belongs to which property, how a figure is shown, when two to-dos
are the same to-do, how much of a draft survived into the sent email, and that
a record cut for space always says so."""
from datetime import datetime, timedelta, timezone

from mangotree.brain import money
from mangotree.brain import checks
from mangotree.brain.context import clip
from mangotree.brain.drafts import body_html, similarity
from mangotree.brain.store import BrainStore, norm_owner, parse_day
from mangotree.brain.updater import BOOTSTRAP_SCHEMA, EVENT_SCHEMA, _similar


# ---------------------------------------------------------------- money
def test_two_bayshore_loans_never_merge():
    assert money.match_property("904 Bayshore") == "bayshore_904"
    assert money.match_property("910 Bayshore") == "bayshore_910"
    assert money.match_property("910 Bayshore Dr., Terra Ceia, FL") == "bayshore_910"
    assert money.match_property("904 Bayshore Dr, Terra Ceia FL 34250") == "bayshore_904"


def test_sheet_names_and_addresses_resolve():
    assert money.match_property("Varnum") == "varnum"
    assert money.match_property("1512 Varnum St NW, Washington, DC") == "varnum"
    assert money.match_property("Ridge Road") == "ridge_road"
    assert money.match_property("Summary") is None
    assert money.match_property("") is None


def test_figures_are_shown_as_entered():
    assert money.fmt("Note amount", 361131.59) == "$361,131.59"
    assert money.fmt("Rate of interest", 0.17) == "17%"
    assert money.fmt("Rate of interest", 0.099) == "9.90%"
    assert money.fmt("Daily interest (360-day year)", 170.53) == "$170.53"
    assert money.fmt("Contract date", datetime(2024, 4, 5)) == "2024-04-05"
    assert money.fmt("Late fees", "5% of any delinquent payment") == "5% of any delinquent payment"
    assert money.fmt("Maturity", None) is None
    assert money.fmt("Maturity", "   ") is None


# ---------------------------------------------------------------- store
def test_owner_names_normalise():
    assert norm_owner("Rakesh Sir") == "rakesh"
    assert norm_owner("JP") == "jp"
    assert norm_owner("Manjunath Sir") == "manjunath"
    assert norm_owner("Wes Stone") == "wes"
    assert norm_owner("the title company") == "other"
    assert norm_owner(None) == "other"


def test_due_dates_parse_or_are_left_empty():
    assert parse_day("2026-11-14") == datetime(2026, 11, 14, tzinfo=timezone.utc)
    assert parse_day("2026-11-14T09:00:00Z") == datetime(2026, 11, 14, tzinfo=timezone.utc)
    assert parse_day("next Friday") is None
    assert parse_day("2026-13-40") is None
    assert parse_day(None) is None


def test_new_item_is_clean_and_open():
    it = BrainStore.new_item(title="  Send the insurance certificate  ", owner="Manjunath Sir", kind="nonsense", due="2026-11-20")
    assert it["title"] == "Send the insurance certificate"
    assert it["owner"] == "manjunath" and it["kind"] == "action" and it["status"] == "open"
    assert it["due"] == datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert BrainStore.open_items({"open_items": [it, {**it, "id": "x", "status": "done"}]}) == [it]


# -------------------------------------------------------------- updater
def test_same_todo_in_other_words_is_not_added_twice():
    assert _similar("Ask Erin for the filing date and service returns", "Ask Erin for the filing date, and the service returns.")
    assert not _similar("Ask Erin for the filing date", "Fund the $2,549.06 contribution through AppFolio")
    assert not _similar("", "anything")


def test_schemas_are_closed_objects():
    """Structured output needs every object closed and every field required."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required") or []) == set(node.get("properties") or {})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(EVENT_SCHEMA)
    walk(BOOTSTRAP_SCHEMA)


# --------------------------------------------------------------- drafts
def test_kept_ratio_measures_what_survived():
    draft = "Jason,\n\nThank you. I approve the revised $537.90 for the three ridge caps.\n\nRakesh"
    assert similarity(draft, draft) == 1.0
    assert similarity(draft, "Jason,\n\nThank you.  I approve the revised $537.90 for the three ridge caps.\n\nRakesh\n") == 1.0
    assert similarity(draft, "Jason, thank you. I approve the revised $537.90 for the three ridge caps. Please send photos. Rakesh") > 0.8
    assert similarity(draft, "Call me.") < 0.3
    assert similarity("", draft) == 0.0


def test_draft_html_escapes_and_keeps_paragraphs():
    html = body_html("Hi Erin,\n\nCase <2026-CA-002551> & service.\nSecond line.\n\nThanks")
    assert html.count("<p>") == 3
    assert "&lt;2026-CA-002551&gt; &amp; service.<br>Second line." in html


# -------------------------------------------------------------- context
def test_a_cut_record_always_says_so():
    assert clip("short", 100) == "short"
    cut = clip("x" * 500, 100, sha="a1b2c3d4e5f6a7b8", what="attachment")
    assert cut.startswith("x" * 100)
    assert "400 more characters" in cut and "open_document('a1b2c3d4e5f6')" in cut


# --------------------------------------------------------------- checks
READ = """Kelly: the revised handyman quote is $537.90. Landscaping $3,188.9 with half up front.
Case No. 2026-CA-002551 was accepted. Rate 0.17 per the workbook; late fee 5% of any amount.
Confirmation P2PUGPTZ98BAL4 on BILL-10040. Contribution 2549.06 through AppFolio."""


def test_figures_that_were_read_pass():
    draft = ("I approve $537.90 and landscaping at $3,188.90. We will send $2,549.06. Case 2026-CA-002551, "
             "BILL-10040, confirmation P2PUGPTZ98BAL4. Interest is 17% and the late fee 5%.")
    assert checks.not_found(draft, [READ]) == []


def test_a_figure_that_was_never_read_is_reported():
    draft = "We will wire $12,500.00 by Friday under BILL-99999 at 18%."
    assert checks.not_found(draft, [READ]) == ["$12,500.00", "18%", "BILL-99999"]


def test_dates_and_plain_words_are_not_treated_as_references():
    assert checks.figures("Please reply by 2026-11-13, or call 703-678-5684 about the well-known follow-up.") == []
    assert checks.figures("Paid $500 on BILL-10040") == ["$500", "BILL-10040"]


def test_rounded_figures_are_not_checked_but_exact_ones_are():
    assert checks.figures("about $205.6K, or $1.2M; pay $500 monthly; $12,500.00 by Friday.") == ["$500", "$12,500.00"]


def test_everything_in_front_of_the_model_counts_as_read():
    system = [{"type": "text", "text": "file: total due $447,460.82"}]
    messages = [{"role": "user", "content": "the fee is $175"},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "pool service $187/month"},
                                             {"type": "text", "text": "reserve $500"}]}]
    read = checks.texts_of(system, messages)
    assert checks.not_found("Total $447,460.82; fee $175; pool $187; reserve $500.", read) == []
    assert checks.not_found("Deposit $100.", read) == ["$100"]


# -------------------------------------------------- second check, lessons
def test_workbook_floats_match_the_rounded_figure_on_the_page():
    read = ["Builder Cost=387.1485297107643 | 6180.148529710765 | Contractor=5562.133676739688 | Hold-back=618.0148529710765"]
    draft = "This draw is $6,180.15 of work, $5,562.13 payable, holdback $618.01, prep $387.15, and $999.99 extra."
    assert checks.not_found(draft, read) == ["$999.99"]


def test_checker_findings_go_back_to_the_writer_and_to_the_person_in_plain_words():
    from mangotree.brain import verify
    problems = [{"statement": "We have not received the certificate.", "kind": "overtaken",
                 "record_shows": "Kelly sent it on 10 Nov (COI.pdf).", "fix": "Acknowledge it was received."}]
    told = verify.as_instruction(problems)
    assert "We have not received the certificate." in told and "Kelly sent it on 10 Nov" in told and "overtaken" in told
    shown = verify.for_person(problems)
    assert shown[0]["label"] == "A later record changes this" and shown[0]["statement"].startswith("We have not received")
    assert set(verify.KINDS) >= {"unsupported", "contradicted", "overtaken", "misattributed"}


def test_only_a_real_change_is_learned_from():
    from mangotree.brain.lessons import changed_enough
    draft = "Jason,\n\nThank you. I approve the revised $537.90 for the three ridge caps.\n\nRakesh Bhargava"
    assert not changed_enough(draft, draft)
    assert not changed_enough(draft, "Jason,\n\nThank you.  I approve the revised $537.90 for the three ridge caps.\n\nRakesh Bhargava\n")
    assert changed_enough(draft, "Jason,\n\n1. Ridge caps: approved at $537.90.\n\nRakesh")
    assert not changed_enough("", draft) and not changed_enough(draft, "")


def test_the_record_block_is_recognised_for_long_caching():
    from mangotree.brain.llm import RECORD_MARK, system_blocks
    blocks = system_blocks("who we are", RECORD_MARK + " — every email …", "the property file", "the job")
    assert "cache_control" not in blocks[0]
    assert blocks[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert blocks[2]["cache_control"] == {"type": "ephemeral"} and blocks[3]["cache_control"] == {"type": "ephemeral"}


# ------------------------------------------------- has it already been answered?
class _FakeRecords:
    def rkb_addresses(self):
        return ["rakesh@mtreh.com", "Manjunath@mtreh.com", "jp@mtreh.com"]


def test_a_reply_from_anyone_at_rkb_counts_whichever_folder_it_was_found_in():
    from mangotree.brain.answered import Answered
    a = Answered(mongo=None, records=_FakeRecords())
    assert a.from_rkb({"participants": {"from": ["manjunath@mtreh.com"]}})      # arrived as received mail: still ours
    assert a.from_rkb({"participants": {"from": ["Rakesh@mtreh.com"]}})
    assert not a.from_rkb({"participants": {"from": ["kelly@roiblocks.com"]}})
    assert not a.from_rkb({"participants": {}})


def test_an_email_we_sent_is_never_itself_waiting_for_our_reply():
    from mangotree.brain.answered import Answered
    a = Answered(mongo=None, records=_FakeRecords())
    ours = {"sha256": "x" * 64, "date": datetime(2026, 11, 10, tzinfo=timezone.utc), "participants": {"from": ["rakesh@mtreh.com"]}}
    assert a.candidates(ours) == []
    assert a.check(ours)["status"] == "not_answered" and a.check(ours)["candidates"] == 0


# ------------------------------------------------- the top of the property page
def test_a_contact_is_attached_on_a_full_name_only():
    from mangotree.brain import snapshot

    class _People:
        def find(self, *a, **k):
            return [{"display_name": "Jessica Moore (buyer agent)", "addresses": ["jessica@example.com"]},
                    {"display_name": "Jackie Thomas", "addresses": ["jackie@title.example"]}]

    class _Mongo:
        people = _People()

    class _Terms:
        def find_one(self, *a, **k):
            return None

    class _Store:
        loan_terms = _Terms()

    file = {"facts": [{"key": "Title company", "value": "Mid-Atlantic Title, Jackie Thomas / Jessica Tolley; file 26-MA-2095"}]}
    rows = snapshot.people(_Mongo(), _Store(), "marmon", file)
    assert [r["role"] for r in rows] == ["Title company"]
    assert [c["name"] for c in rows[0]["contacts"]] == ["Jackie Thomas"]      # not the other Jessica


def test_a_number_missing_from_the_workbooks_says_where_it_came_from():
    from mangotree.brain import snapshot

    class _Terms:
        def find_one(self, *a, **k):
            return None

    class _Store:
        loan_terms = _Terms()

    n = snapshot.numbers(_Store(), "marmon", {"facts": [{"key": "Note amount", "value": "$133,215.59"}]})
    by = {x["key"]: x for x in n["main"]}
    assert not n["in_workbooks"]
    assert by["note_amount"]["from"] == "file" and by["note_amount"]["value"] == "$133,215.59"
    assert by["maturity"]["from"] == "missing" and by["daily_interest"]["value"] is None


# ------------------------------------------------- Buildertrend: the contractor's site diary and bills
def test_a_buildertrend_job_is_matched_to_its_property_by_house_number_and_street():
    from mangotree.brain.buildertrend import match_property
    assert match_property("1631 N Durham St") == "durham_st"
    assert match_property("24333 Narrow Gauge Rd") == "narrow_guage"       # the registry spells it "Guage"
    assert match_property("912 Decatur St. NW") == "decatur_st"
    assert match_property("904 Bayshore Dr") == "bayshore_904" and match_property("910 Bayshore Dr.") == "bayshore_910"
    assert match_property("7828 Michele Dr") is None                         # not one of the active properties
    assert match_property("") is None


def _bt_row(**over):
    row = {"id": 1, "jobId": 9, "billNumberLink": {"title": "BILL-0019-PO-0002"}, "billTitleLink": {"title": "General Remodel-5 (Hold-back)"},
           "payTo": {"payTo": "Tutt Painting"}, "paymentAmount": {"value": 836.86}, "amountPaid": {"value": 0}, "remainingBalance": {"value": 836.86},
           "purchaseOrderPaymentStatus": {"paymentStatus": 8}, "invoicedDate": "2026-11-11T18:15:36", "dueDate": {"date": "2026-11-12T23:59:00", "isPastDue": False},
           "datePaid": None, "approvers": {"approvers": [{"status": 1, "approverName": "Manjunath RKB"}]}, "documentType": "Bill"}
    row.update(over)
    return row


def test_a_bill_is_in_review_only_when_buildertrend_lists_it_under_in_review():
    from mangotree.brain.buildertrend import Buildertrend, IN_REVIEW, PAID, READY
    b = Buildertrend._bill(_bt_row(), "durham_st", True, None)
    assert b["status"] == IN_REVIEW and b["in_review"] and b["amount"] == 836.86 and b["approvers"] == ["Manjunath RKB"]
    assert Buildertrend._bill(_bt_row(purchaseOrderPaymentStatus={"paymentStatus": 1}), "durham_st", False, None)["status"] == READY
    paid = Buildertrend._bill(_bt_row(purchaseOrderPaymentStatus={"paymentStatus": 2}, datePaid="2026-11-01T00:00:00", amountPaid={"value": 836.86}, remainingBalance={"value": 0}), "durham_st", False, None)
    assert paid["status"] == PAID and not paid["in_review"]
    # A status this code does not know is shown as unknown, never guessed into a known one.
    assert Buildertrend._bill(_bt_row(purchaseOrderPaymentStatus={"paymentStatus": 7}), "durham_st", False, None)["status"].startswith("Other")


def test_a_bill_in_review_is_given_to_the_model_line_by_line():
    from mangotree.brain.buildertrend import Buildertrend
    detail = {"createdByName": "Kelly Stone", "description": {"value": ""}, "attachedFiles": {"files": [{"title": "invoice.pdf"}]},
              "lineItems": {"value": [{"title": "Garage door", "description": "Material and installation", "costCodeTitle": "21- Miscellaneous Exterior ",
                                       "quantity": 1, "unitCost": 4012.94, "builderCost": 4012.94, "varianceCodeTitle": "-- Not a Variance --"},
                                      {"title": "Extra framing", "costCodeTitle": "07 - Framing", "builderCost": 500, "varianceCodeTitle": "72 - Customer change order"}]}}
    text = Buildertrend.render_bill(Buildertrend._bill(_bt_row(), "durham_st", True, detail))
    assert "BILL-0019-PO-0002" in text and "$836.86" in text and "Manjunath RKB" in text and "entered by Kelly Stone" in text
    assert "Garage door — $4,012.94 [21- Miscellaneous Exterior]" in text
    assert "variance: 72 - Customer change order" in text and "Not a Variance" not in text
    assert "invoice.pdf" in text and "not read here" in text


# ------------------------------------------------- how the site work is going
def _log(log_id, day, n_photos, first_photo):
    return {"log_id": log_id, "date": datetime(2026, 11, day, 12, tzinfo=timezone.utc), "photos": [{"file_id": first_photo + i} for i in range(n_photos)]}


def test_every_log_keeps_a_place_when_there_are_more_photos_than_fit():
    from mangotree.brain.sitework import pick_photos
    logs = [_log(1, 1, 30, 100), _log(2, 5, 30, 200), _log(3, 9, 2, 300)]
    have = {p["file_id"] for l in logs for p in l["photos"]}
    chosen = pick_photos(logs, have, budget=20)
    assert sum(len(v) for v in chosen.values()) == 20
    assert chosen[3] == [300, 301]                      # the small newest log is shown whole
    assert len(chosen[1]) >= 8 and len(chosen[2]) >= 8  # the older ones share the rest, neither dropped
    assert pick_photos(logs, {100}, budget=20) == {1: [100], 2: [], 3: []}   # a photo not on file is never "shown"


def test_a_remark_about_workmanship_with_no_photo_behind_it_is_dropped():
    from mangotree.brain.sitework import SiteWork
    d = {"observations": [{"kind": "defect", "what": "cracked tile", "photo_ids": [7, 999]}, {"kind": "concern", "what": "wiring looks unsafe", "photo_ids": [999]}],
         "work": [{"work": "tile", "status": "done", "photo_ids": [999]}, {"work": "paint", "status": "done", "photo_ids": [7]}],
         "mismatches": [{"log_says": "done", "photos_show": "not done", "photo_ids": [7, 8]}], "best_photos": [{"photo_id": 999, "caption": "x"}, {"photo_id": 7, "caption": "y"}]}
    out = SiteWork._clean(d, shown={7})
    assert [o["what"] for o in out["observations"]] == ["cracked tile"] and out["observations"][0]["photo_ids"] == [7]
    assert out["dropped_unsupported"] == 1
    assert out["work"][0]["status"] == "claimed_not_shown" and out["work"][1]["status"] == "done"
    assert out["mismatches"][0]["photo_ids"] == [7] and [b["photo_id"] for b in out["best_photos"]] == [7]


def test_the_days_before_a_job_began_are_not_a_gap_in_the_work():
    from mangotree.brain import sitework
    logs = [{"date": sitework.now() - timedelta(days=d)} for d in (12, 6, 1)]
    whole = sitework.stats(logs, days=31)
    began = sitework.stats(logs, days=31, job_began=logs[0]["date"])
    assert whole["longest_gap_days"] == 19 and began["longest_gap_days"] == 6
    assert began["days_with_a_log"] == 3 and began["days_since_last_log"] == 1


# ------------------------------------------------- bills: RKB's four steps and the time each took
def _bill_tracker():
    import mongomock
    from mangotree.brain.billtrack import BillTracker

    class _M:
        db = mongomock.MongoClient().db

    t = BillTracker(_M())
    t.bills.insert_many([
        {"bill_id": 1, "property_id": "durham_st", "title": "Framing", "amount": 462.14, "in_review": True, "status": "In review", "invoice_date": datetime(2026, 10, 29, 17, 40, tzinfo=timezone.utc)},
        {"bill_id": 2, "property_id": "lodestone", "title": "Materials", "amount": 4289.4, "in_review": True, "status": "In review", "invoice_date": datetime(2026, 11, 11, 10, 3, tzinfo=timezone.utc)},
        {"bill_id": 3, "property_id": "varnum", "title": "Old paid bill", "amount": 10.0, "in_review": False, "status": "Paid", "invoice_date": datetime(2026, 1, 1, tzinfo=timezone.utc)},
    ])
    return t


def test_the_bills_list_is_what_buildertrend_has_in_review_newest_bill_first():
    t = _bill_tracker()
    assert [r["bill_id"] for r in t.rows("open")] == [2, 1] and t.open_count() == 2
    assert t.rows("paid") == []


def test_a_tick_records_when_and_who_and_a_paid_bill_moves_to_paid():
    t = _bill_tracker()
    user = {"user_id": "manjunath", "name": "Manjunath Sir"}
    r = t.set_step(1, "manju_approved", True, user)
    assert r["steps"]["manju_approved"]["by_name"] == "Manjunath Sir" and r["steps"]["manju_approved"]["at"] and r["steps"]["paid"] is None
    t.set_step(1, "manju_approved", False, user)                           # unticking removes the time
    assert t.rows("open")[1]["steps"]["manju_approved"] is None
    t.set_step(1, "paid", True, user)
    assert [r["bill_id"] for r in t.rows("open")] == [2] and [r["bill_id"] for r in t.rows("paid")] == [1] and t.open_count() == 1
    assert len(t.track.find_one({"bill_id": 1})["history"]) == 3            # every tick and untick is kept


def test_a_bill_part_way_through_stays_on_the_list_after_buildertrend_moves_it_on_or_deletes_it():
    t = _bill_tracker()
    t.set_step(2, "team_reviewed", True, {"user_id": "jp", "name": "JP Sir"})
    t.bills.update_one({"bill_id": 2}, {"$set": {"in_review": False, "status": "Ready for payment"}})
    assert 2 in [r["bill_id"] for r in t.rows("open")]                      # no longer "in review" there, still ours to finish
    t.bills.delete_one({"bill_id": 2})
    row = next(r for r in t.rows("open") if r["bill_id"] == 2)
    assert row["title"] == "Materials" and row["in_buildertrend"] is False and row["steps"]["team_reviewed"]["by_name"] == "JP Sir"


def test_an_unknown_step_or_bill_is_refused():
    import pytest
    t = _bill_tracker()
    with pytest.raises(ValueError):
        t.set_step(1, "approved_by_someone", True, {"user_id": "x"})
    with pytest.raises(KeyError):
        t.set_step(999, "paid", True, {"user_id": "x"})


# ------------------------------------------------- Outlook: a draft is a reply-all inside the conversation
class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body, self.text = code, body or {}, ""

    def json(self):
        return self._body


def _outlook_writer(monkeypatch, *, original_found=True):
    from types import SimpleNamespace
    from mangotree.brain.drafts import DraftWriter
    calls = []

    class Sess:
        def get(self, url, **k):
            calls.append(("GET", url))
            return _Resp(200 if original_found else 404, {"id": "orig", "value": []})

        def post(self, url, **k):
            calls.append(("POST", url))
            return _Resp(201, {"id": "new1", "body": {"content": "<html><body dir=\"ltr\"><div>QUOTED ORIGINAL</div></body></html>"},
                               "toRecipients": [{"emailAddress": {"address": "wes@roiblocks.com"}}],
                               "ccRecipients": [{"emailAddress": {"address": "kelly@roiblocks.com"}}, {"emailAddress": {"address": "jp@mtreh.com"}}]})

        def patch(self, url, json=None, **k):
            calls.append(("PATCH", url, json))
            return _Resp(200, {"id": "new1", "webLink": "https://outlook/x", "conversationId": "conv-1", "subject": "RE: 1631 N Durham",
                               "toRecipients": json.get("toRecipients", [{"emailAddress": {"address": "wes@roiblocks.com"}}]),
                               "ccRecipients": json.get("ccRecipients", [{"emailAddress": {"address": "kelly@roiblocks.com"}}, {"emailAddress": {"address": "jp@mtreh.com"}}])})

        def delete(self, url, **k):
            calls.append(("DELETE", url))
            return _Resp(204)

    graph = SimpleNamespace(mailbox="rakesh@mtreh.com", timeout=30, _session=Sess(), auth=SimpleNamespace(access_token=lambda scopes: "token"))
    saved_new = []

    class FakeOutbox:
        def __init__(self, mongo):
            pass

        def _graph(self):
            return graph

        def save_draft(self, **kw):
            saved_new.append(kw)
            return {"graph_message_id": "plain1", "web_link": "https://outlook/plain"}

    monkeypatch.setattr("mangotree.mail.outbox.Outbox", FakeOutbox)
    draft = {"draft_id": "d1", "reply_to_sha": "sha1", "subject": "RE: 1631 N Durham", "body": "Wes,\n\nThe ALTA confirms it.",
             "to": [{"email": "wes@roiblocks.com", "name": "Wes Stone"}], "cc": [{"email": "Manjunath@mtreh.com"}, {"email": "rakesh@mtreh.com"}]}
    updates = {}
    import mongomock
    db = mongomock.MongoClient().db
    db.occurrences.insert_one({"artifact_sha": "sha1", "provider": "gmail"})
    w = DraftWriter.__new__(DraftWriter)
    w.mongo = SimpleNamespace(artifacts=db.artifacts, occurrences=db.occurrences)
    w.records = SimpleNamespace(provider_message_id=lambda sha: "orig" if original_found else None)
    w.store = SimpleNamespace(get_draft=lambda i: dict(draft), update_draft=lambda i, f: updates.update(f))
    return w, calls, saved_new, updates


def test_an_outlook_draft_is_a_reply_all_in_the_conversation_of_the_email_it_answers(monkeypatch):
    w, calls, saved_new, updates = _outlook_writer(monkeypatch)
    info = w.to_outlook("d1")
    assert info["as_reply"] and info["conversation_id"] == "conv-1" and not saved_new          # Outlook made the reply; no new email
    assert any(c[0] == "POST" and c[1].endswith("/messages/orig/createReplyAll") for c in calls)
    patch = next(c[2] for c in calls if c[0] == "PATCH")
    body = patch["body"]["content"]
    assert body.index("The ALTA confirms it.") < body.index("QUOTED ORIGINAL") and body.startswith("<html><body dir=\"ltr\"><p>Wes,")   # our text above the quote
    assert "toRecipients" not in patch                                         # everyone Outlook addressed stays as addressed
    cc = [x["emailAddress"]["address"].lower() for x in patch["ccRecipients"]]
    assert cc == ["kelly@roiblocks.com", "jp@mtreh.com", "manjunath@mtreh.com"]  # the draft's extra person added; the mailbox itself never
    assert updates["outlook"]["as_reply"] is True


def test_when_the_original_is_not_in_outlook_it_says_so_instead_of_pretending(monkeypatch):
    w, calls, saved_new, updates = _outlook_writer(monkeypatch, original_found=False)
    info = w.to_outlook("d1")
    assert info["as_reply"] is False and len(saved_new) == 1 and "Gmail" in info["why_not"]
    assert not any(c[0] == "POST" for c in calls)


def test_hold_back_bills_are_never_bills_to_process():
    from mangotree.brain.buildertrend import is_holdback
    assert is_holdback("General Remodel / Repair-5 (Hold-back)") and is_holdback("Materials (Holdback)") and is_holdback("Retainage release")
    assert not is_holdback("General Remodel / Repair-5 (Contractor)") and not is_holdback(None)
    t = _bill_tracker()
    t.bills.insert_one({"bill_id": 9, "property_id": "durham_st", "title": "Repair-5 (Hold-back)", "amount": 836.86, "balance": 836.86, "in_review": True, "holdback": True,
                        "status": "In review", "invoice_date": datetime(2026, 11, 11, 18, 15, tzinfo=timezone.utc)})
    assert 9 not in [r["bill_id"] for r in t.rows("open")] and t.open_count() == 2
    t.set_step(9, "manju_approved", True, {"user_id": "rakesh", "name": "Rakesh Sir"})       # even if somebody ticks it
    assert 9 not in [r["bill_id"] for r in t.rows("open")] and t.open_count() == 2
    h = t.held_back()
    assert h["in_review"] == 1 and h["in_review_amount"] == 836.86 and h["held_amount"] == 836.86


# ------------------------------------------------- site photos live on the server's storage only
class _FakeSpaces:
    def __init__(self):
        self.objects = {}

    def exists(self, sha):
        return sha in self.objects

    def put(self, sha, data, filename, metadata):
        self.objects[sha] = data
        return "s3://bucket/" + sha

    def get(self, sha):
        return self.objects.get(sha)

    def presigned_url(self, sha, *, expires=900):
        return f"https://spaces.example/{sha}?expires={expires}"


def _bt(monkeypatch, store):
    import mongomock
    from mangotree.brain import buildertrend as b

    class _M:
        db = mongomock.MongoClient().db

    monkeypatch.setattr(b, "photo_store", lambda: store)
    bt = b.Buildertrend(_M())
    # The in-memory test database does not accept this pymongo's bulk operations; apply them one by one.
    for coll in (bt.jobs, bt.logs, bt.bills):
        monkeypatch.setattr(coll, "bulk_write", lambda ops, ordered=False, coll=coll: [coll.update_one(o._filter, o._doc, upsert=o._upsert) for o in ops])
    return bt, b


def test_a_photo_goes_to_the_server_and_is_refused_when_the_server_cannot_be_reached(monkeypatch):
    import pytest
    spaces = _FakeSpaces()
    bt, b = _bt(monkeypatch, spaces)
    assert bt.add_photo(11, 500, b"jpeg-bytes") is True and len(spaces.objects) == 1
    assert bt.add_photo(11, 500, b"jpeg-bytes") is False                       # already there: not stored twice
    doc = bt.photos.find_one({"file_id": 11})
    assert doc["where"] == "server" and bt.photo(11) == b"jpeg-bytes"
    assert bt.photo_url(11).startswith("https://spaces.example/")              # the browser is sent to the server's storage
    no_keys, _ = _bt(monkeypatch, None)
    with pytest.raises(b.ServerStorageMissing):
        no_keys.add_photo(12, 500, b"x")                                       # never kept on the laptop instead
    assert no_keys.photos.count_documents({}) == 0 and no_keys.photo_url(12) is None


def test_photos_still_on_the_laptop_are_moved_and_only_confirmed_ones_are_deleted(monkeypatch, tmp_path):
    import hashlib
    spaces = _FakeSpaces()
    bt, b = _bt(monkeypatch, spaces)
    folder = tmp_path / "pull"
    (folder / "photos" / "500").mkdir(parents=True)
    for fid, blob in ((1, b"one"), (2, b"two")):
        (folder / "photos" / "500" / f"{fid}.jpg").write_bytes(blob)
        bt.photos.insert_one({"file_id": fid, "job_id": 500, "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)})
    bt.photos.insert_one({"file_id": 3, "job_id": 500, "sha256": "0" * 64, "bytes": 1})          # its file is gone from the laptop
    monkeypatch.setattr(b.Buildertrend, "_local_bytes", lambda self, d, folder=None: (folder / "photos" / "500" / f"{d['file_id']}.jpg").read_bytes()
                        if folder and (folder / "photos" / "500" / f"{d['file_id']}.jpg").exists() else None)
    r = bt.move_photos_to_server(folder)
    assert r["moved"] == 2 and r["not_found_on_this_machine"] == 1 and r["on_server"] == 2 and r["on_this_machine_only"] == 1
    del spaces.objects[hashlib.sha256(b"two").hexdigest()]                      # pretend one did not really arrive
    monkeypatch.setattr("mangotree.storage.objectstore.LocalObjectStore._path", lambda self, sha: tmp_path / "nowhere" / sha)
    d = bt.delete_local_copies(folder)
    assert d["files_removed"] == 1 and d["photos_kept_because_not_confirmed_on_server"] == 1
    assert not (folder / "photos" / "500" / "1.jpg").exists() and (folder / "photos" / "500" / "2.jpg").exists()


def test_only_properties_with_new_logs_are_read_again_after_a_pull(monkeypatch):
    spaces = _FakeSpaces()
    bt, b = _bt(monkeypatch, spaces)
    bt.jobs.insert_many([{"job_id": 500, "property_id": "durham_st"}, {"job_id": 600, "property_id": "lane_pl"}])
    log = lambda i, d: {"dailyLogId": i, "logDate": f"2026-11-{d:02d}T10:00:00", "logTitle": "t", "logNotes": "n", "addedBy": "x", "images": [], "files": []}  # noqa: E731
    first = bt.import_logs({"pulled_at": "2026-11-12T12:00:00Z", "logs": {"500": [log(1, 10)], "600": [log(2, 10)]}})
    assert first["new_logs"] == {"durham_st": 1, "lane_pl": 1}
    bt.pulls.update_many({}, {"$set": {"analysed": True}})
    second = bt.import_logs({"pulled_at": "2026-11-13T12:00:00Z", "logs": {"500": [log(1, 10), log(3, 12)], "600": [log(2, 10)]}})
    assert second["new_logs"] == {"durham_st": 1}                               # Lane Pl got nothing new

    from mangotree.brain import sitework
    read = []

    class _SW:
        def __init__(self, mongo):
            self.bt, self.store = bt, None

        def analyse(self, pid):
            read.append(pid)
            return {"overall": "acceptable", "pace": "on_track", "headline": "h", "cost_usd": 0.5}

    monkeypatch.setattr(sitework, "SiteWork", _SW)
    monkeypatch.setattr("mangotree.brain.snapshot.refresh_stage", lambda mongo, store, pid: None)
    out = sitework.after_pull(None)
    assert read == ["durham_st"] and out["properties_with_new_logs"] == ["durham_st"] and out["cost_usd"] == 0.5
    assert sitework.after_pull(None)["properties_with_new_logs"] == []           # the pull is marked: not read twice


# ------------------------------------------------- bill emails: Eastern time, sent by the tick, never twice
def test_eastern_time_and_the_time_a_bill_took():
    from mangotree.core import eastern
    tick = datetime(2026, 11, 12, 21, 10, tzinfo=timezone.utc)                 # a tick, stored in UTC
    assert eastern.label(eastern.to_et(tick)) == "12 Nov 2026, 4:10 PM EST"
    assert eastern.label(eastern.to_et(datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc))) == "1 Jul 2026, 12:00 PM EDT"   # summer is EDT, not EST
    bill = datetime(2026, 11, 11, 18, 3, 0, tzinfo=timezone.utc)                # Buildertrend's wall clock, stored as it came
    at = eastern.wall_clock_et(bill)
    assert eastern.label(at) == "11 Nov 2026, 6:03 PM EST"                      # read as Eastern, not shifted five hours
    assert eastern.took(at, eastern.to_et(tick)) == "22 hours 7 minutes"
    assert eastern.took(at, at + timedelta(days=2, hours=5, minutes=30)) == "2 days 5 hours"
    assert eastern.took(at, at + timedelta(minutes=12)) == "12 minutes" and eastern.label(None) == "not recorded"


def _bill_mail(monkeypatch):
    import mongomock
    from mangotree.brain import billmail as bm
    from mangotree.mail.outbox import Outbox

    class _M:
        db = mongomock.MongoClient().db

    class _Store:
        def setting(self, key, default=None):
            return default

    mail = bm.BillMail(_M(), _Store(), outbox=Outbox(_M()))
    monkeypatch.setattr(bm.BillMail, "property_rows", lambda self, pid: [("Address", "1631 N Durham St"), ("RKB invested", "$25,000.00")])
    monkeypatch.setattr(bm.BillMail, "property_bill_rows", lambda self, pid, this: ([("Billed in Buildertrend", "$67,050.23 across 16 bills")], []))
    row = {"bill_id": 77, "property_id": "durham_st", "number": "BILL-0018-PO-0002", "title": "General Remodel / Repair-5 (Contractor)", "pay_to": "Tutt Painting and Remodeling LLC",
           "amount": 7531.88, "bill_date": datetime(2026, 11, 11, 18, 3, tzinfo=timezone.utc), "line_items": [{"title": "Framing", "amount": 7531.88, "cost_code": "07 - Framing"}],
           "steps": {"manju_approved": {"at": datetime(2026, 11, 12, 14, 15, tzinfo=timezone.utc), "by_name": "Manjunath Sir"}, "team_reviewed": None,
                     "ready_to_pay": {"at": datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc), "by_name": "Rakesh Sir"},
                     "paid": {"at": datetime(2026, 11, 12, 21, 10, tzinfo=timezone.utc), "by_name": "Rakesh Sir"}}}
    return mail, row


def test_ready_to_pay_emails_jp_sir_with_the_bill_and_the_property(monkeypatch):
    mail, row = _bill_mail(monkeypatch)
    m = mail.compose(row, "ready_to_pay", "Rakesh Sir")
    assert m["to"] == [("JP Sir", "jp@mtreh.com")] and m["cc"] == []
    assert m["subject"].startswith("Ready to pay — General Remodel / Repair-5 (Contractor) — ") and "$7,531.88" in m["subject"]
    for piece in ("$7,531.88", "11 Nov 2026, 6:03 PM EST", "Tutt Painting and Remodeling LLC", "Framing", "THE PROPERTY", "RKB invested", "$25,000.00",
                  "12 Nov 2026, 9:15 AM EST — Manjunath Sir", "ticked by Rakesh Sir"):
        assert piece in m["text"], piece
    import re
    assert re.search(r"Team reviewed\s+not recorded", m["text"])               # a step nobody ticked is said to be missing, not left out


def test_paid_emails_wes_and_kelly_with_every_time_in_eastern_and_how_long_it_took(monkeypatch):
    mail, row = _bill_mail(monkeypatch)
    m = mail.compose(row, "paid", "Rakesh Sir")
    assert [a for _, a in m["to"]] == ["wes@roiblocks.com", "kelly@roiblocks.com"] and [a for _, a in m["cc"]] == ["manjunath@mtreh.com", "shivansh@mtreh.com"]
    assert m["text"].startswith("Wes, Kelly, this bill has been paid.")
    for piece in ("BILL-0018-PO-0002", "$7,531.88", "Bill date     11 Nov 2026, 6:03 PM EST", "Paid          12 Nov 2026, 4:10 PM EST", "Paid within   22 hours 7 minutes of the bill date",
                  "Ready to pay         12 Nov 2026, 3:00 PM EST — Rakesh Sir"):
        assert piece in m["text"], piece
    assert "UTC" not in m["text"] and "RKB invested" not in m["text"]            # Eastern only; the property's money is not sent to the contractor


def test_a_confirmed_tick_sends_at_once_and_an_outage_only_delays_the_email(monkeypatch):
    mail, row = _bill_mail(monkeypatch)
    user = {"user_id": "rakesh", "name": "Rakesh Sir"}
    ob = mail.outbox()
    tried = []

    def accepted(doc):
        tried.append(doc["outbox_id"])
        ob.coll.update_one({"outbox_id": doc["outbox_id"]}, {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc)}})
        return {"status": "sent"}

    def timing_out(doc):
        tried.append(doc["outbox_id"])
        ob.coll.update_one({"outbox_id": doc["outbox_id"]}, {"$set": {"error": "HTTP 504"}, "$inc": {"attempts": 1}})
        return {"status": "queued", "error": "HTTP 504"}

    assert mail.on_step(row, "manju_approved", True, user) is None                # only two steps send anything
    monkeypatch.setattr(ob, "send_one", timing_out)                               # the mail service is down
    st = mail.on_step(row, "ready_to_pay", True, user)
    assert st["now"] == "not_accepted" and st["status"] == "queued" and st["to"] == ["JP Sir"] and len(tried) == 1   # tried at once
    assert ob.coll.find_one({"ref": "77:ready_to_pay"})["status"] == "queued"     # not lost: kept for the retry
    monkeypatch.setattr(ob, "send_one", accepted)                                 # the service is back
    assert mail.on_step(row, "ready_to_pay", True, user)["now"] == "sent"         # the same email goes; not a second one
    assert ob.coll.count_documents({"ref": "77:ready_to_pay"}) == 1 and len(tried) == 2
    assert mail.on_step(row, "ready_to_pay", True, user)["now"] == "already_sent" and len(tried) == 2   # once gone, never again
    st = mail.on_step(row, "paid", True, user)
    assert st["now"] == "sent" and st["to"] == ["Wes Stone", "Kelly Stone"] and st["cc"] == ["Manjunath Sir", "Shivansh"]
    monkeypatch.setattr(ob, "send_one", timing_out)
    other = dict(row, bill_id=79)
    mail.on_step(other, "paid", True, user)
    assert mail.on_step(other, "paid", False, user) == {"status": "cancelled"}    # unticked while it had not managed to go: stopped
    hold = dict(row, bill_id=78, title="General Remodel / Repair-5 (Hold-back)")
    assert mail.on_step(hold, "paid", True, user)["status"] == "not_sent" and ob.coll.count_documents({"ref": "78:paid"}) == 0
    assert mail.recipients() == {"ready_to_pay": {"to": ["JP Sir"], "cc": []}, "paid": {"to": ["Wes Stone", "Kelly Stone"], "cc": ["Manjunath Sir", "Shivansh"]}}


def test_a_mail_service_outage_never_drops_an_email_but_a_refused_email_ends(monkeypatch):
    import mongomock
    from types import SimpleNamespace
    from mangotree.mail.outbox import Outbox

    class _M:
        db = mongomock.MongoClient().db

    ob = Outbox(_M())
    answer = {"code": 504}

    class Sess:
        def get(self, url, **k):
            return _Resp(200, {"value": []})

        def post(self, url, **k):
            return _Resp(answer["code"])

    ob._client = SimpleNamespace(mailbox="rakesh@mtreh.com", timeout=30, _session=Sess(), auth=SimpleNamespace(access_token=lambda scopes=None: "t"))
    q = ob.queue(kind="bill_step", ref="77:paid", to=[("Wes Stone", "wes@roiblocks.com")], subject="Paid — X", html="<p>x</p>", text="x")
    doc = ob.coll.find_one({"outbox_id": q["outbox_id"]})
    for _ in range(9):                                                            # far more failures than the old limit of six
        assert ob.send_one(doc)["status"] == "queued"
    assert ob.coll.find_one({"outbox_id": q["outbox_id"]})["attempts"] == 9
    answer["code"] = 400                                                          # the email itself is refused: that does end it
    assert ob.send_one(doc)["status"] == "failed"


# ------------------------------------------------- queued mail goes on time, whatever else the scheduler is doing
def test_the_outbox_is_sent_by_its_own_pass_and_only_one_pass_runs_at_a_time():
    import threading
    from mangotree.briefing.morning import Scheduler
    s = Scheduler.__new__(Scheduler)                      # no database, no model: only the sending logic
    s._outbox_lock = threading.Lock()
    s.intake_enabled = True
    sent, recorded = [], []
    s._due_outbox = lambda: True
    s.flush_outbox = lambda: (sent.append(1) or {"sent": 1})
    s._record = lambda job, ok, detail: recorded.append((job, ok))
    assert s.outbox_pass() == {"sent": 1} and sent == [1] and recorded == [("outbox", True)] and s._last_outbox is not None
    s._outbox_lock.acquire()                              # a pass is already running
    assert s.outbox_pass() == {} and sent == [1]          # a second one does nothing, so an email cannot be sent twice
    s._outbox_lock.release()
    s._due_outbox = lambda: False                         # nothing due
    assert s.outbox_pass() == {} and sent == [1]
    s.intake_enabled = False; s._due_outbox = lambda: True
    assert s.outbox_pass() == {} and sent == [1]          # a machine that is not the mail server never sends


def test_a_send_that_timed_out_is_not_sent_twice_if_it_did_go(monkeypatch):
    import mongomock
    from types import SimpleNamespace
    from mangotree.mail.outbox import Outbox

    class _M:
        db = mongomock.MongoClient().db

    ob = Outbox(_M())
    posts = []
    sent_items = {"value": []}

    class Sess:
        def get(self, url, **k):
            return _Resp(200, sent_items)

        def post(self, url, **k):
            posts.append(url)
            return _Resp(504)

    ob._client = SimpleNamespace(mailbox="rakesh@mtreh.com", timeout=30, _session=Sess(), auth=SimpleNamespace(access_token=lambda scopes=None: "t"))
    monkeypatch.setattr(ob, "_find_sent", lambda doc, since: None)
    q = ob.queue(kind="bill_step", ref="77:ready_to_pay", to=[("JP Sir", "jp@mtreh.com")], subject="Ready to pay — X", html="<p>x</p>", text="x")
    doc = ob.coll.find_one({"outbox_id": q["outbox_id"]})
    assert ob.send_one(doc)["status"] == "queued" and len(posts) == 1            # the mail service timed out: kept for another try
    # Sent Items cannot be read either: we cannot tell whether it went, so this try does not send.
    readable = Sess.get
    Sess.get = lambda self, url, **k: _Resp(504)
    assert ob.send_one(doc)["status"] == "queued" and len(posts) == 1
    assert ob.coll.find_one({"outbox_id": q["outbox_id"]})["attempts"] == 1
    Sess.get = readable
    # ...but the email did in fact go out. The next try finds it in Sent Items and does not send again.
    sent_items["value"] = [{"id": "m1", "subject": "Ready to pay — X", "internetMessageId": "<a@b>", "conversationId": "c1",
                            "toRecipients": [{"emailAddress": {"address": "JP@mtreh.com"}}]}]
    assert ob.send_one(doc)["status"] == "sent" and len(posts) == 1
    assert ob.coll.find_one({"outbox_id": q["outbox_id"]})["found_in_sent_items"] is True


def test_a_failed_send_is_retried_within_minutes(monkeypatch):
    import mongomock
    import threading
    from mangotree.briefing.morning import Scheduler
    from mangotree.mail.outbox import Outbox

    class _M:
        db = mongomock.MongoClient().db

    s = Scheduler.__new__(Scheduler)
    s.mongo, s._outbox, s._outbox_lock = _M(), Outbox(_M()), threading.Lock()
    now = datetime.now(timezone.utc)
    s._last_outbox = now - timedelta(minutes=3)                                   # the ten-minute round is not due
    s._outbox.coll.insert_one({"outbox_id": "ob-1", "status": "queued", "attempts": 1, "queued_at": now - timedelta(minutes=9),
                               "send_after": now - timedelta(minutes=7), "last_attempt_at": now - timedelta(seconds=20)})
    assert s._due_outbox() is False                                               # tried 20 seconds ago: wait the minute
    s._outbox.coll.update_one({"outbox_id": "ob-1"}, {"$set": {"last_attempt_at": now - timedelta(seconds=75)}})
    assert s._due_outbox() is True                                                # a minute has passed: try again
    s._outbox.coll.update_one({"outbox_id": "ob-1"}, {"$set": {"attempts": 3}})
    assert s._due_outbox() is False                                               # third failure waits four minutes
