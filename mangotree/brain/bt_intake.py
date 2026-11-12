"""Receive a Buildertrend pull and load it — without keeping any of it on this machine.

The pull is made in the browser that is signed in to Buildertrend. The page hands
each piece to this receiver (an ordinary form post to 127.0.0.1), and each piece
goes where it belongs at once, from memory:

    jobs.json, daily_logs.json, bills.json  ->  the database
    a pack of photos                        ->  the server's storage (DigitalOcean Spaces)
    done                                    ->  every property that got new logs is read
                                                again (site-work analysis and progress)

Nothing is written to disk here. If this machine has no keys to the server's
storage, photos are refused and the reply says so — they are not kept locally.

Run:  python -m mangotree.cli brain buildertrend-receive
"""
from __future__ import annotations

import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from .buildertrend import Buildertrend, ServerStorageMissing

PHOTO_PATH = re.compile(r"^photos/(\d+)/(\d+)\.jpe?g$", re.I)


def handle(bt: Buildertrend, mongo: Mongo, name: str, kind: str, data: str) -> str:
    """Load one piece of a pull. Returns one plain line saying what happened."""
    if kind == "pack":
        ok = already = bad = size = 0
        for path, b64 in json.loads(data).items():
            m = PHOTO_PATH.match(path)
            if not m:
                bad += 1
                continue
            blob = base64.b64decode(b64)
            try:
                if bt.add_photo(int(m.group(2)), int(m.group(1)), blob):
                    ok += 1
                    size += len(blob)
                else:
                    already += 1
            except ServerStorageMissing as exc:
                return f"PHOTOS REFUSED: {exc}"
        return f"pack {name}: {ok} photos stored on the server ({size} bytes), {already} already there, {bad} refused"
    if name == "jobs.json":
        r = bt.import_jobs(json.loads(data))
        return f"jobs: {r['jobs']} read, {r['matched']} matched to a property"
    if name == "daily_logs.json":
        r = bt.import_logs(json.loads(data))
        new = ", ".join(f"{p} +{n}" for p, n in sorted(r["new_logs"].items())) or "none"
        return f"daily logs: {r['logs']} loaded; new logs: {new}"
    if name == "estimates.json":
        r = bt.import_estimates(json.loads(data))
        return (f"estimates: {r['estimates']} proposals loaded, {r['lines']} lines; with no lines: {', '.join(r['empty']) or 'none'}; "
                f"jobs not matched to a property: {r['unmatched'] or 'none'}")
    if name == "bill_details.json":
        r = bt.import_bill_details(json.loads(data))
        return f"bill lines: {r['details']} bills read, {r['matched']} matched to a bill on file"
    if name == "bills.json":
        r = bt.import_bills(json.loads(data))
        return f"bills: {r['bills']} loaded, {r['in_review']} in review"
    if name == "done":
        from .sitework import after_pull
        r = after_pull(mongo)
        return (f"done: {len(r['read_again'])} properties read again ({', '.join(x['property_id'] for x in r['read_again']) or 'none had new logs'}); "
                f"failed: {', '.join(r['failed']) or 'none'}; cost ${r['cost_usd']}")
    return f"unknown piece {name!r}: nothing done"


def serve(mongo: Mongo, port: int = 8765) -> None:
    bt = Buildertrend(mongo)

    class H(BaseHTTPRequestHandler):
        def _reply(self, code: int, text: str) -> None:
            body = f"<!doctype html><meta charset=utf-8><title>saved</title><p id=result>{text}</p>".encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            st: Dict[str, Any] = bt.photos_status()
            self._reply(200, f"receiver is up; server storage reachable: {st['server_storage_reachable']}; photos on the server: {st['on_server']} of {st['photos']}")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not self.path.startswith("/form"):
                return self._reply(404, "unknown")
            f = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            name, kind, data = (f.get("name") or [""])[0], (f.get("kind") or ["text"])[0], (f.get("data") or [""])[0]
            try:
                self._reply(200, handle(bt, mongo, name, kind, data))
            except Exception as exc:
                logger.exception("buildertrend intake: %s failed", name)
                self._reply(500, f"FAILED {name}: {type(exc).__name__}: {str(exc)[:300]}")

        def log_message(self, *a):
            pass

    print(f"Buildertrend receiver on http://127.0.0.1:{port} — nothing it receives is written to this machine's disk. Ctrl+C to stop.", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
