"""Which Claude model ids does this key actually accept?

The admin asked for "Opus 4.6" for email segregation, which is not among the ids
verified on this key. Pinning a model that 404s would fail the whole email
pipeline at run time, so this establishes the real name first.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS

CANDIDATES = [
    "claude-opus-4-6",
    "claude-opus-4.6",
    "claude-4-6-opus",
    "claude-opus-4-6-20260401",
    "claude-opus-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-fable-5",
]


def main() -> None:
    import anthropic

    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key)

    print("=== listing models the key can see ===")
    try:
        listed = client.models.list(limit=50)
        for model in listed.data:
            print(f"  {model.id}")
    except Exception as exc:
        print(f"  models.list unavailable: {type(exc).__name__}: {exc}")

    print("\n=== probing specific ids with a 1-token call ===")
    for name in CANDIDATES:
        try:
            client.messages.create(
                model=name,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            print(f"  OK        {name}")
        except Exception as exc:
            detail = str(exc)
            short = "not_found" if "not_found" in detail or "404" in detail else \
                type(exc).__name__
            print(f"  FAIL      {name:<32} {short}")


if __name__ == "__main__":
    main()
