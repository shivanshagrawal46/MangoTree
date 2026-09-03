"""Deep manual test — run ~30 real questions across diverse properties through
the FULL live agent (retrieve -> fan-out -> Opus rerank -> answer -> verify),
cross-check each answer vs ground truth (grounded facts, findings), and write a
readable report to _manual_test.md. Resumable: skips questions already logged.
"""
import sys
import re
from collections import Counter
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
import api.rag_singleton as S

REPORT = "_manual_test.md"

# (property_id, address, question)
PROPS = [
    ("ent_prop_0200468000500010000", "183 Mark Tree Rd, Centereach"),
    ("ent_prop_0500322000100012000", "59 Beecher Avenue, East Islip"),
    ("ent_prop_0200316000900027000", "12 Mallard Path, Coram"),
    ("ent_prop_6220351", "83 Ann Drive S, Freeport"),
    ("ent_prop_0200974600400065000", "26 Appel Dr E, Shirley"),
    ("ent_prop_0400026000200048000", "91 West Shore Road, Huntington"),
    ("ent_prop_0102002000200081000", "230 Ralph, Babylon"),
    ("ent_prop_2179310301", "904 Bayshore Dr, Terra Ceia FL"),
]
TEMPLATES = [
    "Who owns {a} and is it connected to David DeRosa or his network?",
    "What mortgages, liens, or judgments are recorded against {a}?",
    "Give me the chronological timeline of {a}.",
    "Are there any suspicious or voidable transfers involving {a}?",
]

def build_questions():
    qs = []
    for pid, a in PROPS:
        for t in TEMPLATES:
            qs.append((pid, a, t.format(a=a)))
    return qs  # 8 x 4 = 32


def ground_truth(m, pid):
    d = m.db["property_dossier"].find_one({"_id": pid}) or {}
    fnd = list(m.db["findings"].find({"property_id": pid}, {"title": 1, "severity": 1}))
    return {"address": d.get("canonical_address"), "is_david": d.get("is_david"),
            "owners": [o.get("name") for o in (d.get("owners") or [])],
            "fact_counts": d.get("fact_counts", {}),
            "findings": [f"{f.get('severity')}:{f.get('title')[:40]}" for f in fnd],
            "events": m.db["events"].count_documents({"property_id": pid})}


def main():
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    questions = build_questions()

    # resume: which questions already in report
    done = set()
    try:
        with open(REPORT, encoding="utf-8") as fh:
            for line in fh:
                mo = re.match(r"### Q(\d+)\b", line)
                if mo:
                    done.add(int(mo.group(1)))
    except FileNotFoundError:
        with open(REPORT, "w", encoding="utf-8") as fh:
            fh.write("# Deep Manual Test — system answers vs ground truth\n\n")

    for i, (pid, addr, q) in enumerate(questions, 1):
        if i in done:
            continue
        gt = ground_truth(m, pid)
        chat = S.make_chat()  # fresh (no history bleed)
        try:
            turn = chat.ask(q)
            ans = turn.answer or ""
            vouts = turn.verification_outcome
            nf = len(turn.facts or [])
            nv = sum(1 for v in (turn.fact_verdicts or []) if (v.get("verdict") == "VERIFIED"))
            st = Counter((c.source_type or "?") for c in (turn.chunks or []))
        except Exception as exc:  # noqa: BLE001
            ans, vouts, nf, nv, st = f"ERROR: {exc}", "ERROR", 0, 0, {}
        with open(REPORT, "a", encoding="utf-8") as fh:
            fh.write(f"### Q{i}. {q}\n")
            fh.write(f"- **Property:** {addr} ({pid}) · is_david={gt['is_david']}\n")
            fh.write(f"- **Ground truth:** owners={gt['owners']} · facts={gt['fact_counts']} · "
                     f"events={gt['events']} · findings={gt['findings']}\n")
            fh.write(f"- **Verification:** {vouts} · facts={nf} verified={nv} · sources={dict(st)}\n")
            fh.write(f"- **Answer:**\n\n{ans}\n\n---\n\n")
        print(f"[{i}/{len(questions)}] {addr[:24]:26s} | {vouts} | v{nv}/{nf} | {dict(st)}")
        sys.stdout.flush()
    print("MANUAL TEST DONE")
    m.close()


if __name__ == "__main__":
    main()
