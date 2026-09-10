"""Tests for chunking.

The two failures these guard against are both silent. A chunk split mid-sentence embeds as
noise and merely retrieves badly. A chunk that loses its context retrieves badly for a
reason no one can see by reading the stored text, because the text looks fine.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.chunking import MAX_CHARS, Chunk, chunk_clause, split_text  # noqa: E402
from redline.policies import parse_policy  # noqa: E402

CONTEXT = "Data Retention Policy — Retention Periods — Audit logs"

SHORT = "Audit logs shall be retained for a minimum of six (6) years."
PARAS = "\n\n".join(f"Paragraph {i} states an obligation. " * 12 for i in range(6))
WALL = " ".join(f"Sentence {i} states a distinct obligation of some length." for i in range(90))


# --- when not to split -------------------------------------------------------

def test_short_text_is_one_chunk():
    chunks = split_text(SHORT, CONTEXT)
    assert len(chunks) == 1
    assert chunks[0].is_split is False


def test_context_is_prefixed_to_the_embedded_text():
    """A clause reading "retained for six years" is meaningless alone. What gets embedded
    must carry the policy and section it belongs to."""
    chunk = split_text(SHORT, CONTEXT)[0]
    assert chunk.text.startswith(CONTEXT)
    assert SHORT in chunk.text
    assert chunk.body == SHORT      # body stays clean for display and citation


def test_empty_text_produces_nothing():
    assert split_text("", CONTEXT) == []
    assert split_text("   \n  ", CONTEXT) == []


# --- when to split -----------------------------------------------------------

def test_long_text_is_split():
    chunks = split_text(PARAS, CONTEXT)
    assert len(chunks) > 1
    assert all(c.is_split for c in chunks)
    assert [c.part for c in chunks] == list(range(len(chunks)))


def test_every_piece_carries_the_context():
    """Otherwise piece 4 of 7 is an orphan fragment that matches nothing."""
    assert all(c.text.startswith(CONTEXT) for c in split_text(PARAS, CONTEXT))


def test_no_chunk_exceeds_the_maximum():
    assert all(len(c.body) <= MAX_CHARS for c in split_text(PARAS, CONTEXT))


def test_splitting_loses_no_text():
    """Truncation would drop obligations silently — the worst possible outcome here."""
    rejoined = " ".join(" ".join(c.body.split()) for c in split_text(PARAS, CONTEXT))
    for i in range(6):
        assert f"Paragraph {i} states" in rejoined


def test_a_single_huge_paragraph_splits_on_sentences_not_mid_sentence():
    """A fragment beginning "shall be retained for a period of" has lost its subject."""
    chunks = split_text(WALL, CONTEXT)
    assert len(chunks) > 1
    for c in chunks:
        assert c.body.rstrip().endswith(".")


def test_a_trailing_scrap_is_merged_rather_than_embedded_alone():
    text = "\n\n".join(["A paragraph of reasonable length. " * 30, "Tiny."])
    chunks = split_text(text, CONTEXT)
    assert all(len(c.body) > 20 for c in chunks)


# --- policy clauses ----------------------------------------------------------

SAMPLE = """---
id: P-099
title: Test Policy
owner: X
effective: 2024-01-01
last_reviewed: 2025-06-01
domain: testing
---

## 2. Retention

### 2.2 Audit logs
Audit logs shall be retained for a minimum of six (6) years.
"""


def test_a_policy_clause_is_never_split():
    """Clause identity is the contract: "P-099 §2.2" must address exactly one unit,
    because that string appears in golden-set labels and agent citations."""
    clause = parse_policy(SAMPLE)[0]
    chunks = chunk_clause(clause)
    assert len(chunks) == 1
    assert chunks[0].ref == "P-099 §2.2"
    assert chunks[0].is_split is False


def test_clause_chunk_embeds_the_contextualised_text():
    chunk = chunk_clause(parse_policy(SAMPLE)[0])[0]
    assert "Test Policy" in chunk.text
    assert "Retention" in chunk.text
