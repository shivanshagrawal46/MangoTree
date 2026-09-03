"""Chunking guarantees.

Two of these are admin requirements rather than tuning knobs — 1000-token chunks
with 200 of overlap, and no chunk ever spanning two properties — so they are
pinned here. The property-purity rule is the one that protects answers: a chunk
carrying both Varnum and Decatur text would surface under a Varnum-filtered
query and hand the analyst Decatur's numbers.
"""
from __future__ import annotations

import unittest

from mangotree.chunk.chunker import (
    MAX_TOKENS,
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    chunk_artifact,
)
from mangotree.chunk.tokens import count_tokens, split_by_tokens, tail_tokens


def _long_property_text(alias: str, sentences: int) -> str:
    return " ".join(
        f"At {alias} the contractor completed phase {i} of the renovation and "
        f"submitted invoice number {1000 + i} for review by the accountant."
        for i in range(sentences)
    )


class TestTokenBudget(unittest.TestCase):
    def test_admin_budget_is_1000_200(self):
        self.assertEqual(TARGET_TOKENS, 1000)
        self.assertEqual(OVERLAP_TOKENS, 200)

    def test_no_chunk_exceeds_the_hard_ceiling(self):
        text = _long_property_text("1512 Varnum", 200)
        chunks = chunk_artifact(text, artifact_sha="a" * 64, property_ids=["varnum"])
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertLessEqual(chunk.token_count, MAX_TOKENS, chunk.text[:120])

    def test_long_document_actually_fills_the_budget(self):
        # A chunker that emitted 50-token chunks would satisfy every ceiling
        # above while destroying retrieval, so assert the floor too.
        text = _long_property_text("1512 Varnum", 200)
        chunks = chunk_artifact(text, artifact_sha="b" * 64, property_ids=["varnum"])
        self.assertGreater(len(chunks), 1)
        body = chunks[:-1]  # the last chunk is legitimately short
        mean = sum(c.token_count for c in body) / len(body)
        self.assertGreater(mean, TARGET_TOKENS * 0.5, f"chunks too small: {mean:.0f}")

    def test_token_count_is_recorded(self):
        chunks = chunk_artifact(
            _long_property_text("1512 Varnum", 40),
            artifact_sha="c" * 64,
            property_ids=["varnum"],
        )
        for chunk in chunks:
            self.assertEqual(chunk.token_count, count_tokens(chunk.text))


class TestPropertyPurity(unittest.TestCase):
    def test_a_chunk_never_carries_two_properties(self):
        text = (
            _long_property_text("1512 Varnum", 60)
            + "\n\n"
            + _long_property_text("1330 Decatur", 60)
        )
        chunks = chunk_artifact(text, artifact_sha="d" * 64)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertLessEqual(
                len(chunk.property_ids), 1,
                f"chunk spans {chunk.property_ids}: {chunk.text[:160]}",
            )

    def test_overlap_never_leaks_across_a_property_boundary(self):
        text = (
            _long_property_text("1512 Varnum", 60)
            + "\n\n"
            + _long_property_text("1330 Decatur", 60)
        )
        chunks = chunk_artifact(text, artifact_sha="e" * 64)
        for chunk in chunks:
            if chunk.property_ids == ["decatur_st"]:
                self.assertNotIn("Varnum", chunk.text)
            if chunk.property_ids == ["varnum"]:
                self.assertNotIn("Decatur", chunk.text)


class TestTokenHelpers(unittest.TestCase):
    def test_tail_returns_roughly_the_requested_window(self):
        text = _long_property_text("1512 Varnum", 100)
        tail = tail_tokens(text, 200)
        self.assertTrue(tail)
        # Sentence snapping trims the head, so allow slack below but not above.
        self.assertLessEqual(count_tokens(tail), 200)
        self.assertGreater(count_tokens(tail), 80)
        self.assertTrue(text.endswith(tail[-40:]))

    def test_tail_of_short_text_is_the_whole_text(self):
        self.assertEqual(tail_tokens("Short line.", 200), "Short line.")

    def test_split_respects_the_limit_even_for_one_huge_sentence(self):
        # No sentence boundary to split on — the splitter still must not exceed.
        text = "word " * 4000
        for piece in split_by_tokens(text, 300):
            self.assertLessEqual(count_tokens(piece), 300)

    def test_empty_inputs_are_safe(self):
        self.assertEqual(count_tokens(""), 0)
        self.assertEqual(tail_tokens("", 200), "")
        self.assertEqual(split_by_tokens("", 200), [])


if __name__ == "__main__":
    unittest.main()
