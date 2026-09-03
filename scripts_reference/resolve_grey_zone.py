"""Sprint 8 · 8.11 — grey-zone entity-merge resolver (LLM cross-encoder).

The fuzzy merge step parks 0.70-0.95 name pairs in `entity_review`. This judges
each pending pair with a pairwise LLM call (the Claude-compatible equivalent of
a cross-encoder): "are these the SAME real-world entity?" -> yes/no/uncertain +
reason. High-confidence yes -> status=confirmed (then apply_entity_review merges
+ learns the alias); no -> rejected; uncertain stays for a human. Idempotent.

  python -m scripts.resolve_grey_zone            # judge pending pairs
"""
import json
import sys
from datetime import datetime, timezone
import api.rag_singleton as S
from src.utils.logger import logger

_SYS = ("You are resolving whether two entity names in a real-estate fraud "
        "matter refer to the SAME real-world entity. Many are address-coded "
        "single-purpose LLCs (e.g. '520E LLC' = 520 East...). Consider spacing/"
        "punctuation/OCR variants as same; genuinely different street/number "
        "codes as different. Return ONLY the tool call.")
_TOOL = {"name": "judge", "description": "Same entity?", "input_schema": {"type": "object",
         "properties": {"same": {"type": "string", "enum": ["yes", "no", "uncertain"]},
                        "confidence": {"type": "number"}, "reason": {"type": "string"}},
         "required": ["same", "confidence"]}}


def main():
    client = S.get_anthropic_client()
    model = S.get_settings().rag_v2_summary_model  # Sonnet — cheap pairwise judge
    m = S.get_mongo()
    review = m.db["entity_review"]
    now = datetime.now(timezone.utc)
    pend = list(review.find({"kind": "entity_merge_candidate", "status": "pending"}))
    logger.info(f"{len(pend)} grey-zone pairs to judge")
    conf = rej = unc = 0
    for r in pend:
        prompt = (f"Name A: {r.get('a_name')!r}\nName B: {r.get('b_name')!r}\n"
                  f"(fuzzy score {r.get('score')}). Same real-world entity?")
        try:
            resp = client.messages.create(model=model, max_tokens=400, system=_SYS,
                tools=[_TOOL], tool_choice={"type": "tool", "name": "judge"},
                messages=[{"role": "user", "content": prompt}])
            j = {}
            for b in resp.content:
                if getattr(b, "type", None) == "tool_use":
                    j = dict(b.input or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"judge failed for {r['_id']}: {str(exc)[:80]}")
            continue
        same, c = j.get("same"), float(j.get("confidence") or 0)
        if same == "yes" and c >= 0.8:
            review.update_one({"_id": r["_id"]}, {"$set": {"status": "confirmed",
                "judge": j, "judged_at": now, "judged_by": "llc_cross_encoder"}})
            conf += 1
            logger.info(f"  CONFIRM same: {r.get('a_name')} == {r.get('b_name')} ({c})")
        elif same == "no" and c >= 0.7:
            review.update_one({"_id": r["_id"]}, {"$set": {"status": "rejected",
                "judge": j, "judged_at": now}})
            rej += 1
            logger.info(f"  REJECT diff: {r.get('a_name')} != {r.get('b_name')}")
        else:
            unc += 1
            logger.info(f"  UNCERTAIN (human): {r.get('a_name')} ? {r.get('b_name')}")
    logger.info(f"grey-zone: confirmed={conf} rejected={rej} uncertain={unc}")
    logger.info("  -> run `python -m scripts.apply_entity_review` to execute confirmed merges")
    m.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
