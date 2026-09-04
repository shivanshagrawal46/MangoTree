"""Set the login names and passwords for the three users (admin directive 2026-09-05).

    python scripts/set_logins.py

Users live in Atlas, so this applies to the laptop and the droplet at once.
Passwords are hashed with PBKDF2-SHA256; nothing is stored in clear.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.api.auth import DEFAULT_USERS, ensure_users, hash_password
from mangotree.storage.mongo import get_mongo

PASSWORD = "rkb@0902"


def main() -> int:
    mongo = get_mongo()
    ensure_users(mongo)
    users = mongo.db["users"]
    now = datetime.now(timezone.utc)
    for d in DEFAULT_USERS:
        r = users.update_one(
            {"user_id": d["user_id"]},
            {"$set": {"login": d["login"], "name": d["name"], "role": d["role"], "full_name": d["full_name"],
                      "password_hash": hash_password(PASSWORD), "password_changed_at": now, "active": True},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        print(f"  {d['login']:<14} (internal id {d['user_id']:<10}) {'updated' if r.matched_count else 'created'}")
    print("\n  all three now sign in with their login name and the shared password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
