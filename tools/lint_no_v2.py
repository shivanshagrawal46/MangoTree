"""Import guard: nothing in ``mangotree/`` may import a v2 pipeline or the reference tree.

Admin directive (2026-08-30): the system is built on the v3 agent architecture.
v2 capabilities are re-implemented as agent tools, never imported as a fixed
pipeline. ``src_reference/`` and ``scripts_reference/`` are reading material,
not dependencies.

Exit code 1 on any violation, so it can sit in CI or a pre-commit hook.

    python tools/lint_no_v2.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / "mangotree", ROOT / "scripts", ROOT / "tools"]
FORBIDDEN = (
    re.compile(r"^\s*(from|import)\s+src_reference\b", re.M),
    re.compile(r"^\s*(from|import)\s+scripts_reference\b", re.M),
    re.compile(r"^\s*from\s+\S*\brag\.v2\b", re.M),
    re.compile(r"^\s*import\s+\S*\brag\.v2\b", re.M),
    re.compile(r"^\s*from\s+\S*\bsrc\.rag\b", re.M),
    re.compile(r"^\s*import\s+\S*\bsrc\.rag\b", re.M),
    re.compile(r"^\s*from\s+mangotree\.\S*v2\S*\s+import", re.M),
)


def main() -> int:
    bad = []
    for base in SCAN:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in FORBIDDEN:
                for m in pattern.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    bad.append((path.relative_to(ROOT), line, m.group(0).strip()))
    if bad:
        print("\n  v2 / reference imports are forbidden (v3-only directive):")
        for p, line, src in bad:
            print(f"    {p}:{line}: {src}")
        print()
        return 1
    print("  lint_no_v2: OK — no v2 or reference-tree imports in mangotree/, scripts/, tools/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
