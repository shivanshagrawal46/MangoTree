"""New mail must always be found, however many records have come in since the files were built.
(On 2026-11-18 the reader looked only at the OLDEST records since then; once there were more than
its cap, every new email was silently left unread.)"""
from datetime import datetime, timedelta, timezone

import mongomock

from mangotree.brain import updater as up
from mangotree.brain.store import BrainStore

UTC = timezone.utc


class FakeMongo:
    def __init__(self):
        self.db = mongomock.MongoClient().db
        self.artifacts = self.db["artifacts"]


def test_an_unread_email_is_found_behind_any_number_of_read_ones(monkeypatch):
    BrainStore._indexed = True
    m = FakeMongo()
    store = BrainStore(m)
    monkeypatch.setattr(up, "active_property_ids", lambda: ["durham_st"])
    built = datetime(2026, 11, 1, tzinfo=UTC)
    store.files.insert_one({"property_id": "durham_st", "built_at": built})
    arts = []
    for i in range(400):                                                  # 400 records already read into the file
        sha = f"old{i:04d}"
        at = built + timedelta(hours=1, minutes=i)
        arts.append({"sha256": sha, "source_type": "email", "property_ids": ["durham_st"], "created_at": at, "date": at, "placement": {}, "indexing": {"indexed_at": at}})
        store.mark_event(sha, "durham_st", status="done")
    new_at = built + timedelta(days=5)
    arts.append({"sha256": "new0001", "source_type": "email", "property_ids": ["durham_st"], "created_at": new_at, "date": new_at, "placement": {}, "indexing": {"indexed_at": new_at}})
    m.artifacts.insert_many(arts)
    u = up.Updater(m, store)
    assert [r["sha256"] for r in u.pending(limit=40)] == ["new0001"]       # what run_pending asks for
    assert [r["sha256"] for r in u.pending(limit=25)] == ["new0001"]       # what the scheduler asks for
    assert [r["sha256"] for r in u.pending()] == ["new0001"]
    # Oldest first when several are waiting, and no more than asked for.
    more = [{"sha256": f"new{i:04d}", "source_type": "email", "property_ids": ["durham_st"], "created_at": new_at + timedelta(minutes=i), "date": new_at + timedelta(minutes=i),
             "placement": {}, "indexing": {"indexed_at": new_at}} for i in range(2, 60)]
    m.artifacts.insert_many(more)
    got = [r["sha256"] for r in u.pending(limit=40)]
    assert len(got) == 40 and got[0] == "new0001" and got == sorted(got)
