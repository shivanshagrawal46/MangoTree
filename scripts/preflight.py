"""Everything that must be true before an unattended overnight run starts.

Checks only. Nothing is written, no billed API call is made.

An unattended run that dies at 4am because a key was missing or a disk was full
wastes the whole window, and the failure is invisible until morning. Each check
here corresponds to a way previous runs have actually stalled.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, ".")

OK, WARN, FAIL = "  ok  ", " warn ", " FAIL "


def line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label:<34}{detail}")


def main() -> int:
    problems = 0
    warnings = 0

    print("\n=== Preflight ===\n")

    # ---------------------------------------------------------------- config
    try:
        from mangotree.config.settings import SETTINGS
    except Exception as exc:
        line(FAIL, "settings", str(exc)[:80])
        return 1

    # ---------------------------------------------------------------- disk
    # Two drives matter and they are usually different: originals accumulate on
    # whichever drive RAW_STORE points at, while OCR renders each PDF page to a
    # temp file on the working drive and deletes it again.
    store_root = Path(SETTINGS.raw_store)
    store_root.mkdir(parents=True, exist_ok=True)
    for label, path, want_gb in (
        ("disk — object store", store_root, 20),
        ("disk — working/temp", Path.cwd(), 5),
    ):
        free_gb = shutil.disk_usage(path).free / (1024 ** 3)
        drive = str(Path(path).anchor) or str(path)
        if free_gb < want_gb:
            line(FAIL, label, f"{drive} has {free_gb:.1f} GB — want {want_gb} GB+")
            problems += 1
        else:
            line(OK, label, f"{drive} {free_gb:.1f} GB free")

    for label, value, required in (
        ("Anthropic key", SETTINGS.anthropic_api_key, True),
        ("Voyage key", SETTINGS.voyage_api_key, True),
        ("OpenAI key (OCR fallback)", SETTINGS.openai_api_key, False),
    ):
        if value:
            line(OK, label, f"set, {len(value)} chars")
        elif required:
            line(FAIL, label, "missing — required")
            problems += 1
        else:
            line(WARN, label, "missing — GPT-5 OCR fallback unavailable")
            warnings += 1

    # ---------------------------------------------------------------- mongo
    try:
        from mangotree.storage.mongo import get_mongo

        mongo = get_mongo()
        mongo.ping()
        counts = {
            name: mongo.db[name].estimated_document_count()
            for name in ("artifacts", "chunks", "occurrences", "skipped")
        }
        line(OK, "MongoDB", f"reachable — {counts}")
    except Exception as exc:
        line(FAIL, "MongoDB", str(exc)[:80])
        problems += 1

    # ---------------------------------------------------------------- gmail
    token = Path(SETTINGS.gmail_token_path)
    if token.exists():
        line(OK, "Gmail token", str(token))
    else:
        line(FAIL, "Gmail token", f"not found at {token}")
        problems += 1

    # ---------------------------------------------------------------- graph
    if not SETTINGS.graph_configured:
        line(FAIL, "Outlook config", "GRAPH_TENANT_ID/CLIENT_ID/MAILBOX incomplete")
        problems += 1
    else:
        try:
            from mangotree.ingest.graph_auth import GraphDelegatedAuth

            auth = GraphDelegatedAuth(
                tenant_id=SETTINGS.graph_tenant_id,
                client_id=SETTINGS.graph_client_id,
                mailbox=SETTINGS.graph_mailbox,
            )
            if auth.is_authenticated:
                line(OK, "Outlook token", f"signed in as {auth.signed_in_account()}")
            else:
                line(FAIL, "Outlook token", "not signed in — run outlook-auth")
                problems += 1
        except Exception as exc:
            line(FAIL, "Outlook token", str(exc)[:80])
            problems += 1

    # ---------------------------------------------------------------- object store
    store = Path(SETTINGS.raw_store)
    try:
        store.mkdir(parents=True, exist_ok=True)
        probe = store / ".preflight"
        probe.write_bytes(b"x")
        probe.unlink()
        line(OK, "object store", f"writable at {store}")
    except Exception as exc:
        line(FAIL, "object store", str(exc)[:80])
        problems += 1

    # ---------------------------------------------------------------- rules
    try:
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            tail = [l for l in result.stdout.strip().splitlines() if l.strip()][-1]
            line(OK, "test suite", tail)
        else:
            line(FAIL, "test suite", "failing — rules are not trustworthy")
            problems += 1
    except Exception as exc:
        line(WARN, "test suite", str(exc)[:80])
        warnings += 1

    print()
    if problems:
        print(f"  {problems} blocking problem(s). Do not start the run.\n")
        return 1
    print(f"  Clear to run. {warnings} warning(s).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
