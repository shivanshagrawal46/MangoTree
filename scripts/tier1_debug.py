"""Show the RAW Tier-1 reply for a document that misaligned, to see what the model actually said."""
from mangotree.chunk.chunker import chunk_artifact
from mangotree.config.models import CONTEXT as CTX
from mangotree.config.settings import SETTINGS
from mangotree.context.tier1 import _SYSTEM, _MAX_EXCERPT_CHARS, _parse_numbered
from mangotree.storage.mongo import get_mongo

TARGET = "Guarantee Sharon.pdf"


def main() -> None:
    db = get_mongo().db
    doc = db.artifacts.find_one(
        {"filename": TARGET},
        {"sha256": 1, "filename": 1, "text": 1, "doc_class": 1, "property_ids": 1},
    )
    print("doc chars:", len(doc.get("text") or ""))

    chunks = chunk_artifact(
        doc["text"], artifact_sha=doc["sha256"],
        property_ids=doc.get("property_ids") or [], default_ref="document",
    )
    print("chunks:", len(chunks))

    window = [c.text for c in chunks[:12]]

    import anthropic
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key)

    listing = "\n\n".join(
        f"[{i}]\n{(t or '').strip()[:_MAX_EXCERPT_CHARS]}"
        for i, t in enumerate(window, start=1)
    )
    instruction = (
        f"Below are {len(window)} excerpt(s) taken from the document above.\n\n"
        f"{listing}\n\n"
        f"Reply with exactly {len(window)} line(s), one per excerpt, in order, "
        f"formatted as:\n[1] <context>\n[2] <context>\n"
        f"Nothing else — no headings, no blank commentary."
    )
    document_block = (
        "Here is the full source document. Use it only as reference for "
        "situating the excerpts that follow.\n\n"
        f"<document>\n{(doc.get('text') or '')[:CTX.max_document_chars]}\n</document>"
    )

    resp = client.messages.create(
        model=CTX.model,
        max_tokens=CTX.max_output_tokens,
        system=_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": document_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": instruction},
        ]}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")

    print("\nstop_reason:", resp.stop_reason)
    print("usage:", resp.usage)
    print("raw length:", len(raw))
    print("\n----- RAW REPLY -----")
    print(raw[:4000])
    print("----- END -----")
    print("\nparsed:", len(_parse_numbered(raw, len(window))), "of", len(window))


if __name__ == "__main__":
    main()
