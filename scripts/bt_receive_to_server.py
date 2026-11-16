"""Receives a Buildertrend pull from the signed-in browser (form posts to 127.0.0.1) and loads it.

    pull.json  -> jobs, bills (with the "need your approval" list) and daily logs, into the database
    a pack     -> photos: sent from memory straight to the server's own disk (its raw_store) over ssh,
                  checked there, then recorded. Nothing is written to this laptop's disk.

Run:  python scripts/bt_receive_to_server.py        (port 8766)
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

os.environ.setdefault("MT_NO_WARM", "1")
sys.path.insert(0, r"C:\Users\SHIVANSH AGRAWAL\Desktop\MangoTree")
from mangotree.storage.mongo import get_mongo  # noqa: E402
from mangotree.brain.buildertrend import Buildertrend  # noqa: E402
from mangotree.brain.store import now  # noqa: E402

SERVER = "root@139.59.39.65"
STORE = "/rkb/MangoTree/raw_store"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", SERVER]
mongo = get_mongo()
bt = Buildertrend(mongo)
LOG = open(os.path.join(os.environ.get("TEMP", "."), "bt_receive_to_server.log"), "a", encoding="utf-8")


def say(text):
    LOG.write(time.strftime("%H:%M:%S ") + text + "\n")
    LOG.flush()
    return text


def rel(sha):
    return "%s/%s/%s" % (sha[:2], sha[2:4], sha)


def to_server(photos):
    """photos: [(file_id, job_id, bytes)]. Returns (stored, already, failed)."""
    known = {p["file_id"]: p for p in bt.photos.find({"file_id": {"$in": [f for f, _, _ in photos]}, "where": "server"}, {"file_id": 1, "sha256": 1})}
    items = []
    for fid, job, blob in photos:
        sha = hashlib.sha256(blob).hexdigest()
        if fid in known and known[fid].get("sha256") == sha:
            continue
        items.append((fid, job, blob, sha))
    already = len(photos) - len(items)
    if not items:
        return 0, already, 0
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        seen = set()
        for _, _, blob, sha in items:
            if sha in seen:
                continue
            seen.add(sha)
            info = tarfile.TarInfo(rel(sha))
            info.size, info.mtime, info.mode = len(blob), int(time.time()), 0o644
            tar.addfile(info, io.BytesIO(blob))
    r = subprocess.run(SSH + ["tar -xf - -C %s --no-same-owner" % STORE], input=buf.getvalue(), capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("the server did not take the photos: " + r.stderr.decode("utf-8", "replace")[:300])
    # Check each file is there with the right size before it is recorded.
    listing = subprocess.run(SSH + ["cd %s && xargs stat -c '%%n %%s'" % STORE], input="\n".join(rel(s) for _, _, _, s in items).encode(),
                             capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
    sizes = {line.split()[0].split("/")[-1]: int(line.split()[1]) for line in listing.splitlines() if len(line.split()) == 2}
    stored = failed = 0
    for fid, job, blob, sha in items:
        if sizes.get(sha) != len(blob):
            failed += 1
            continue
        bt.photos.update_one({"file_id": int(fid)}, {"$set": {"file_id": int(fid), "job_id": int(job), "sha256": sha, "bytes": len(blob), "where": "server", "stored_at": now()},
                                                     "$setOnInsert": {"added_at": now()}}, upsert=True)
        stored += 1
    return stored, already, failed


def handle(name, kind, data):
    if kind == "pack":
        photos = []
        for path, b64 in json.loads(data).items():
            _, job, fname = path.split("/")
            photos.append((int(fname.split(".")[0]), int(job), base64.b64decode(b64)))
        stored, already, failed = to_server(photos)
        return say("pack %s: %d photos stored on the server, %d already there, %d FAILED" % (name, stored, already, failed))
    if name == "pull.json":
        d = json.loads(data)
        out = []
        if d.get("jobs"):
            r = bt.import_jobs(d["jobs"])
            out.append("jobs: %d read, %d matched; not matched: %s" % (r["jobs"], r["matched"], ", ".join(r["unmatched"]) or "none"))
        if d.get("bills"):
            r = bt.import_bills({"pulled_at": d.get("pulled_at"), "bills": d["bills"], "details": d.get("details") or {}})
            out.append("bills: %d loaded, %d waiting for approval" % (r["bills"], r["in_review"]))
        if d.get("logs"):
            r = bt.import_logs({"pulled_at": d.get("pulled_at"), "logs": d["logs"]})
            out.append("daily logs: %d loaded; new: %s" % (r["logs"], ", ".join("%s +%d" % (p, n) for p, n in sorted(r["new_logs"].items())) or "none"))
        return say(" | ".join(out) or "nothing in the pull")
    if name == "details.json":
        # The lines of bills already on file, read again from each bill's own page (with the purchase-order line ids).
        r = bt.import_bill_details(json.loads(data))
        return say("bill lines: %d bills sent, %d matched on file" % (r["details"], r["matched"]))
    return say("unknown piece %r: nothing done" % name)


class H(BaseHTTPRequestHandler):
    def _reply(self, code, text):
        body = ("<!doctype html><meta charset=utf-8><title>saved</title><p id=result>%s</p>" % text).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200, "receiver is up; photos recorded on the server: %d of %d" % (bt.photos.count_documents({"where": "server"}), bt.photos.count_documents({})))

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        f = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        name, kind, data = (f.get("name") or [""])[0], (f.get("kind") or ["text"])[0], (f.get("data") or [""])[0]
        try:
            self._reply(200, handle(name, kind, data))
        except Exception as exc:
            self._reply(500, say("FAILED %s: %s: %s" % (name, type(exc).__name__, str(exc)[:300])))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("receiver on http://127.0.0.1:8766", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8766), H).serve_forever()
