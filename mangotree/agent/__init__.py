"""Agent v3 — the only agent architecture.

Retrieval is a set of tools the agent calls and can call again. The pipeline
does not decide what the agent gets; the agent decides what it needs, observes
what came back, and goes again. Nothing here imports a v2 pipeline module, and
``tools/lint_no_v2.py`` enforces that.
"""
