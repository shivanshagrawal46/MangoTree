"""Per-user login — three people, three home screens.

Users live in the ``users`` collection, seeded from ``MT_USERS`` in .env as
``id:name:role:password,...`` or from the defaults below on first start (the
default passwords are printed once so they can be changed). Passwords are
PBKDF2-SHA256; sessions are HMAC-signed tokens in an HttpOnly cookie. No
third-party auth dependency, nothing stored in plain text.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, Response

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo, get_mongo

COOKIE = "mt_session"
SESSION_DAYS = 14

DEFAULT_USERS = [
    {"user_id": "rakesh", "name": "Rakesh Sir", "role": "ceo", "full_name": "Rakesh Bhargava"},
    {"user_id": "jp", "name": "JP Sir", "role": "accountant", "full_name": "Jaspreet Pahwa"},
    {"user_id": "manjunath", "name": "Manjunath Sir", "role": "operations", "full_name": "Manjunath"},
]
ROLE_HOME = {"ceo": "decisions", "accountant": "review", "operations": "verification"}


def _secret() -> bytes:
    s = os.environ.get("MT_SESSION_SECRET", "").strip()
    if not s:
        # Stable per install: derived from the Mongo URI so restarts keep sessions,
        # without writing a secret to disk. Set MT_SESSION_SECRET in .env to rotate.
        from mangotree.config.settings import SETTINGS
        s = hashlib.sha256(("mt-session|" + SETTINGS.mongo_uri).encode()).hexdigest()
    return s.encode()


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, dk_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64)
        return hmac.compare_digest(hash_password(password, salt), stored)
    except Exception:
        return False


def ensure_users(mongo: Mongo) -> None:
    users = mongo.db["users"]
    users.create_index("user_id", unique=True, name="ux_user_id")
    if users.count_documents({}) > 0:
        return
    spec = os.environ.get("MT_USERS", "").strip()
    seeded: List[Dict[str, Any]] = []
    if spec:
        for item in spec.split(","):
            parts = item.strip().split(":")
            if len(parts) >= 4:
                seeded.append({"user_id": parts[0], "name": parts[1], "role": parts[2], "password": ":".join(parts[3:])})
    if not seeded:
        for u in DEFAULT_USERS:
            seeded.append({**u, "password": f"{u['user_id']}-mangotree"})
        logger.warning("Seeding default users — change these passwords: %s",
                       ", ".join(f"{u['user_id']} / {u['password']}" for u in seeded))
    now = datetime.now(timezone.utc)
    for u in seeded:
        pw = u.pop("password")
        base = next((d for d in DEFAULT_USERS if d["user_id"] == u["user_id"]), {})
        users.insert_one({**base, **u, "password_hash": hash_password(pw), "created_at": now, "active": True})


def issue_token(user_id: str) -> str:
    payload = {"u": user_id, "exp": int(time.time()) + SESSION_DAYS * 86400, "n": secrets.token_hex(6)}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(token: str) -> Optional[str]:
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest(), sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("u")
    except Exception:
        return None


def public_user(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"user_id": doc["user_id"], "name": doc.get("name"), "full_name": doc.get("full_name"),
            "role": doc.get("role"), "home": ROLE_HOME.get(doc.get("role"), "decisions")}


def login(mongo: Mongo, user_id: str, password: str, response: Response) -> Dict[str, Any]:
    doc = mongo.db["users"].find_one({"user_id": user_id.strip().lower(), "active": {"$ne": False}})
    if not doc or not verify_password(password, doc.get("password_hash", "")):
        raise HTTPException(401, "wrong user id or password")
    token = issue_token(doc["user_id"])
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_DAYS * 86400, path="/")
    mongo.db["users"].update_one({"user_id": doc["user_id"]}, {"$set": {"last_login": datetime.now(timezone.utc)}})
    return public_user(doc)


def logout(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request) -> Dict[str, Any]:
    token = request.cookies.get(COOKIE) or (request.headers.get("authorization") or "").replace("Bearer ", "")
    uid = read_token(token) if token else None
    if not uid:
        raise HTTPException(401, "not signed in")
    doc = get_mongo().db["users"].find_one({"user_id": uid, "active": {"$ne": False}})
    if not doc:
        raise HTTPException(401, "user disabled")
    return public_user(doc)


def change_password(mongo: Mongo, user_id: str, old: str, new: str) -> None:
    doc = mongo.db["users"].find_one({"user_id": user_id})
    if not doc or not verify_password(old, doc.get("password_hash", "")):
        raise HTTPException(400, "current password is wrong")
    if len(new) < 8:
        raise HTTPException(400, "new password must be at least 8 characters")
    mongo.db["users"].update_one({"user_id": user_id}, {"$set": {"password_hash": hash_password(new)}})


CurrentUser = Depends(current_user)
