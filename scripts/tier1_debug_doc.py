"""Why does one document return zero Tier-1 summaries, every time?

'DOT-Note- DMV SAMPLE.pdf' returned 0/8 on every batch and every individual
retry, which is not a truncation or a rate limit — those are intermittent. A
deterministic zero means the model is answering something other than what the
parser expects, so this prints the raw reply instead of guessing.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.chunk.chunker import chunk_artifact
from mangotree.config.models import CONTEXT as CTX
from mangotree.config.settings import SETTINGS
from mangotree.context.tier1 import Tier1Writer, _parse_numbered
from mangotree.storage.mongo import get_mongo

TARGET = "DOT-Note- DMV SAMPLE"


def main() -> None:
    db = get_mongo().db
    artifact = db["artifacts"].find_one({"filename": {"$regex": TARGET, "$options": "i"}})
    if not artifact:
        print(f"not found: {TARGET}")
        return

    print(f"file        {artifact.get('filename')}")
    print(f"sha         {artifact.get('sha256', '')[:16]}")
    print(f"class       {artifact.get('doc_class')}")
    print(f"properties  {artifact.get('property_ids')}")
    text = artifact.get("text") or ""
    print(f"text chars  {len(text)}")
    print(f"pages       {len(artifact.get('pages') or [])}")
    print("\n--- first 600 chars of text ---")
    print(repr(text[:600]))

    chunks = chunk_artifact(
        text,
        artifact_sha=artifact.get("sha256", ""),
        property_ids=artifact.get("property_ids") or [],
        default_ref=artifact.get("relative_path") or "",
    )
    print(f"\nchunks      {len(chunks)}")

    writer = Tier1Writer(SETTINGS.anthropic_api_key)
    from mangotree.context.tier1 import _document_header

    header = _document_header({
        "display_name": artifact.get("filename"),
        "doc_class": artifact.get("doc_class"),
        "property_ids": artifact.get("property_ids"),
        "date": artifact.get("date"),
    })
    document_block = (
        "Here is the full source document. Use it only as reference for "
        "situating the excerpts that follow.\n\n"
        f"{header}\n\n<document>\n{text[:CTX.max_document_chars]}\n</document>"
    )
    print(f"doc block   {len(document_block)} chars")

    window = [c.text for c in chunks[:CTX.batch_size]]
    print(f"\ncalling with {len(window)} excerpts...")

    # Straight to the API rather than through _call: the parsed result is already
    # known to be empty, so the only useful evidence is the untouched reply.
    numbered = "\n\n".join(
        f"[{i}] {t[:1200]}" for i, t in enumerate(window, start=1)
    )
    response = writer.client.messages.create(
        model=writer.model,
        max_tokens=CTX.max_output_tokens,
        system=[{"type": "text", "text": writer_system()}],
        messages=[{"role": "user", "content": [
            {"type": "text", "text": document_block,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text":
                f"Write the context line for each of these {len(window)} excerpts. "
                f"Output exactly {len(window)} lines, each starting with its "
                f"number in brackets.\n\n{numbered}"},
        ]}],
    )
    raw = "".join(b.text for b in response.content if b.type == "text")

    print(f"\nstop_reason {response.stop_reason}")
    print(f"out tokens  {response.usage.output_tokens}")
    print(f"raw length  {len(raw)}")
    print("\n=== RAW REPLY (first 3000 chars) ===")
    print(raw[:3000])
    print("\n=== PARSE ATTEMPT ===")
    parsed = _parse_numbered(raw, len(window))
    print(f"{len(parsed)} parsed: {sorted(parsed)}")


def writer_system() -> str:
    from mangotree.context.tier1 import _SYSTEM

    return _SYSTEM


if __name__ == "__main__":
    main()
