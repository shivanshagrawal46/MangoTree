"""
RAG v3 — Agentic Legal Investigator.

Sprint 3 made one-shot answers trustworthy (every fact verified against a
verbatim quote). Sprint 4 layers an agent on top of all the v2 retrieval
and v3 verification machinery so the system can reason iteratively:

  - plan        : decide what evidence is still missing
  - search      : query the corpus through any of the v2 retrieval modes
  - compare     : pull multiple versions of a document side-by-side
  - verify      : use the Sprint 3 verifier as a TOOL mid-investigation
  - synthesise  : emit the final verified answer in the same shape as
                  Sprint 3's submit_answer schema

The agent's output is byte-compatible with what the Sprint-3 verified
pipeline emits, so the existing WebSocket layer, evidence drawer, and
citation chips all "just work" — they only see additional `agent_trace`
metadata when the v3 pipeline is engaged.

Public entry point: `v3.agent.AgentRunner.run(query) -> AgentResult`.
"""
from src.rag.v3.scratchpad import AgentScratchpad, AgentStep, BudgetTracker
from src.rag.v3.agent import AgentRunner, AgentResult

__all__ = [
    "AgentScratchpad",
    "AgentStep",
    "BudgetTracker",
    "AgentRunner",
    "AgentResult",
]
