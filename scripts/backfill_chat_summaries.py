"""Write the rolling summary for any chat that has answers but no summary yet.

Replays each chat's question/answer pairs in order through the same summariser
the live path uses, so older conversations carry the same context as new ones.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

import anthropic

from mangotree.agent.summary import update_summary
from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key, max_retries=3)
    done = 0
    for chat in mongo.db["chats"].find({"summary": {"$in": [None, ""]}, "messages.role": "assistant"}):
        summary = ""
        msgs = chat.get("messages", [])
        for i, m in enumerate(msgs):
            if m.get("role") != "assistant":
                continue
            q = next((x.get("content") for x in reversed(msgs[:i]) if x.get("role") == "user"), "")
            new = update_summary(client, previous=summary, question=q, answer=m.get("answer") or {})
            if new:
                summary = new
        if summary:
            mongo.db["chats"].update_one({"chat_id": chat["chat_id"]}, {"$set": {"summary": summary, "summary_at": datetime.now(timezone.utc)}})
            done += 1
            print(f"  {chat['chat_id']}: summary written ({len(summary)} chars)\n{summary}\n")
    print(f"  {done} chat(s) backfilled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
