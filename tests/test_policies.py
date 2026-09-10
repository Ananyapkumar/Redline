"""Tests for the policy corpus loader.

Clause identity is what these protect. "P-002 §2.2" appears in golden-set labels, in
retrieval results and in agent citations. If an id shifts, or two clauses share one, every
stored label silently points at the wrong text and the eval scores become meaningless
without anything appearing to break.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.policies import load_corpus, parse_policy  # noqa: E402

CORPUS = Path(__file__).resolve().parents[1] / "data" / "policies"

SAMPLE = """---
id: P-099
title: Test Policy
owner: Someone
effective: 2024-01-01
last_reviewed: 2025-06-01
domain: testing
---

## 1. Purpose

### 1.1 Scope
This clause spans
two source lines.

### 1.2 Exceptions
Another clause.

## 2. Retention

### 2.1 Period
Records shall be retained for six (6) years.
"""


# --- parsing -----------------------------------------------------------------

def test_clauses_and_ids():
    clauses = parse_policy(SAMPLE)
    assert [c.clause_id for c in clauses] == ["P-099 §1.1", "P-099 §1.2", "P-099 §2.1"]


def test_multi_line_clause_text_is_joined():
    assert parse_policy(SAMPLE)[0].text == "This clause spans two source lines."


def test_section_context_is_attached_to_each_clause():
    clauses = parse_policy(SAMPLE)
    assert clauses[0].section_heading == "Purpose"
    assert clauses[2].section_heading == "Retention"


def test_front_matter_is_read():
    c = parse_policy(SAMPLE)[0]
    assert c.owner == "Someone"
    assert c.domain == "testing"
    assert c.effective == date(2024, 1, 1)


def test_missing_front_matter_raises():
    with pytest.raises(ValueError, match="front matter"):
        parse_policy("## 1. Purpose\n\n### 1.1 Scope\nText.")


def test_incomplete_front_matter_raises():
    with pytest.raises(ValueError, match="missing 'domain'"):
        parse_policy("---\nid: P-1\ntitle: T\nowner: O\n---\n\n### 1.1 X\nY.\n")


# --- retrieval text ----------------------------------------------------------

def test_retrieval_text_carries_context_not_just_the_clause():
    """A clause reading "retained for six years" is nearly meaningless alone. Embedding
    it stripped of its policy and section is a top cause of bad retrieval."""
    c = parse_policy(SAMPLE)[2]
    assert "Test Policy" in c.retrieval_text
    assert "Retention" in c.retrieval_text
    assert "six (6) years" in c.retrieval_text


# --- staleness ---------------------------------------------------------------

def test_stale_policy_detected_against_its_own_cadence():
    c = parse_policy(SAMPLE)[0]                       # last reviewed 2025-06-01
    assert c.is_stale(date(2027, 9, 1)) is True       # 27 months
    assert c.is_stale(date(2027, 1, 1)) is False      # 19 months


# --- the real corpus ---------------------------------------------------------

def test_real_corpus_loads():
    clauses = load_corpus(CORPUS)
    assert len(clauses) > 100
    assert len({c.policy_id for c in clauses}) == 12


def test_every_clause_id_is_unique():
    """A duplicate id makes every label referring to it ambiguous, and the failure would
    only surface as inexplicable eval results much later."""
    ids = [c.clause_id for c in load_corpus(CORPUS)]
    assert len(ids) == len(set(ids))


def test_no_clause_is_empty():
    assert all(c.text.strip() for c in load_corpus(CORPUS))


def test_no_clause_is_too_long_to_embed_meaningfully():
    """Unlike the Federal Register sections, policy clauses must stay short enough that
    one embedding represents one idea."""
    assert max(len(c.text) for c in load_corpus(CORPUS)) < 600


def test_corpus_contains_a_stale_policy_on_purpose():
    """A corpus where everything is current cannot produce a hard eval case."""
    clauses = load_corpus(CORPUS)
    assert any(c.is_stale(date(2026, 9, 10)) for c in clauses)


def test_corpus_contains_a_deferral_on_purpose():
    """Clauses pointing outside the corpus are where the agent must abstain rather than
    answer confidently. Day 46 tunes the threshold against cases like these."""
    clauses = load_corpus(CORPUS)
    assert any("governed by the applicable" in c.text for c in clauses)
