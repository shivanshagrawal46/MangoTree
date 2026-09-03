"""
End-to-end smoke for the Sprint-3-finish verified-answer pipeline.

Runs 3 representative queries:
  1. Timeline  — "summarize Mango Tree settlement"
  2. Document  — "what does the Global Stipulation say about appeals?"
  3. Money     — "find every reference to $450,000"

For each query, prints:
  • outcome      (VERIFIED_FIRST_PASS / VERIFIED_AFTER_RETRY / KEPT_ORIGINAL)
  • n_facts and n_verified
  • each fact's verdict (verified / unverified) with score + reason
  • the prose answer

Goals:
  • Confirm structured-output tool-use actually fires (Opus must call submit_answer)
  • Confirm the verifier catches drift
  • Confirm the retry loop self-corrects when possible
  • Confirm the answer is preserved when 2nd verification also fails
"""
from __future__ import annotations
import os
import sys
import textwrap
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force the verified pipeline ON regardless of .env (so we can run this
# script standalone). The user's .env already enables it, but be explicit.
os.environ.setdefault("RAG_V2_ENABLED", "true")
os.environ["RAG_V2_STRUCTURED_OUTPUT"] = "true"
os.environ["RAG_V2_CITATION_VERIFIER"] = "true"
os.environ["RAG_V2_VERIFIER_RETRY"] = "true"
os.environ.setdefault("RAG_V2_VERIFIER_LOG", "false")  # skip log writes for smoke

# Use config that points to the v2 corpus we just built.
os.environ.setdefault("RAG_V2_CHUNKS_COLLECTION", "email_chunks_v2")
os.environ.setdefault("RAG_V2_VECTOR_INDEX_NAME", "email_chunks_v2_vector")

from api.rag_singleton import make_chat
from src.utils.logger import configure_logger
from config.settings import Settings


def main() -> int:
    configure_logger(Settings.load().logs_dir)

    queries = [
        ("[timeline]", "Summarize the events of the Mango Tree settlement over time"),
        ("[document]", "What does the Global Stipulation say about appeals?"),
        ("[money]", "Find every reference to $450,000"),
    ]

    chat = make_chat()
    print(f"chat model: {chat.model}  "
          f"structured={chat.use_structured_output}  "
          f"verifier={chat.use_citation_verifier}  "
          f"retry={chat.use_verifier_retry}  "
          f"threshold={chat.verifier_threshold}")
    print("=" * 80)

    for tag, q in queries:
        print(f"\n{tag} {q}")
        print("-" * 80)
        turn = chat.ask(q)

        # Reset history each loop so each query is independent.
        chat.history = []

        print(f"outcome:    {turn.verification_outcome}")
        print(f"chunks:     {len(turn.chunks)}")
        print(f"facts:      {len(turn.facts)}")
        n_verified = sum(
            1 for v in turn.fact_verdicts if v.get("verdict") == "VERIFIED"
        )
        print(f"verified:   {n_verified}/{len(turn.fact_verdicts)}")
        if turn.fact_verdicts:
            print("\nper-fact verdicts:")
            for v in turn.fact_verdicts:
                mark = "OK" if v.get("verdict") == "VERIFIED" else "!!"
                stage = v.get("stage") or ""
                score = v.get("score") or 0
                claim = (v.get("claim") or "")[:70]
                quote = (v.get("verbatim_quote") or "")[:80]
                print(
                    f"  [{mark}] fid={v.get('fact_id'):<4s} "
                    f"chunk=#{v.get('source_chunk_id')} "
                    f"score={score:>5.1f} "
                    f"stage={stage}"
                )
                print(f"        claim:  {claim}")
                print(f"        quote:  {quote!r}")
                if v.get("verdict") != "VERIFIED" and v.get("reason"):
                    print(f"        reason: {v.get('reason')[:120]}")

        print("\nanswer:")
        print(textwrap.indent(turn.answer, "  "))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
