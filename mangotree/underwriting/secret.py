"""RKB's confirmation password.

A person at RKB may confirm any line of a file with or without a paper. Every
such confirmation asks for this password. Only its salted hash is kept, in the
``settings`` collection; the password itself is never written to the code, the
log or a file. Wrong tries are counted per person: after five in ten minutes
the person waits.

Set or change it (asked for at the prompt, not echoed):

    python -m mangotree.underwriting.secret
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

KEY = "uw_confirm_password"
MAX_TRIES, WINDOW = 5, timedelta(minutes=10)


def set_password(mongo, password: str, by: str) -> None:
    from mangotree.api.auth import hash_password
    if len(password or "") < 8:
        raise ValueError("the confirmation password must be at least 8 characters")
    mongo.db["settings"].update_one({"_id": KEY}, {"$set": {"value": hash_password(password), "by": by, "at": datetime.now(timezone.utc)}}, upsert=True)


def is_set(mongo) -> bool:
    return bool((mongo.db["settings"].find_one({"_id": KEY}) or {}).get("value"))


def check(mongo, password: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """{"ok": bool, "why": str}. A wrong try is recorded; too many close together are refused without checking."""
    from mangotree.api.auth import verify_password
    tries = mongo.db["uw_confirm_tries"]
    now = datetime.now(timezone.utc)
    uid = user.get("user_id") or ""
    recent = tries.count_documents({"user_id": uid, "at": {"$gt": now - WINDOW}})
    if recent >= MAX_TRIES:
        return {"ok": False, "why": "Too many wrong tries. Wait ten minutes."}
    stored = (mongo.db["settings"].find_one({"_id": KEY}) or {}).get("value")
    if not stored:
        return {"ok": False, "why": "RKB's confirmation password has not been set yet."}
    if verify_password(password or "", stored):
        return {"ok": True, "why": ""}
    tries.insert_one({"user_id": uid, "at": now})
    return {"ok": False, "why": "That is not the confirmation password."}


if __name__ == "__main__":
    import getpass
    import os
    from mangotree.storage.mongo import get_mongo
    pw = os.environ.get("UW_CONFIRM_PASSWORD") or getpass.getpass("RKB confirmation password: ")
    set_password(get_mongo(), pw, os.environ.get("USERNAME") or "cli")
    print("Saved (only its hash is kept).")
