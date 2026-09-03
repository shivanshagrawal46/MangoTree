"""One-call live smoke of the OpenAI entailment judge (Sprint 4).
Costs a few cents. Proves the cross-family judge path works end-to-end."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings  # loads .env (OPENAI_API_KEY)
from src.rag.v2.entailment import judge_facts, OpenAIEntailmentJudge

FACTS = [
    # SUPPORTED: quote backs the claim
    {"id": "ok", "claim": "The note carries a 9% rate",
     "verbatim_quote": "the Note ($6,450,990.55 at 9%, dated July 17, 2023)"},
    # NOT_SUPPORTED: quote contradicts the claim
    {"id": "bad", "claim": "CrossCountry is fully paid up with no arrears",
     "verbatim_quote": "they are $480k behind in payments to CrossCountry"},
]

judge = OpenAIEntailmentJudge()
print("model:", judge.model)
rep = judge_facts(FACTS, judge_fn=judge)
for i in rep.items:
    print(f"  {i.fact_id:4s} -> {i.label:14s} | {i.reason[:90]}")
print("all_ok:", rep.all_ok, "| failed:", [i.fact_id for i in rep.failed])
