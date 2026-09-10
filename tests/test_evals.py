"""Tests for eval scoring.

An eval harness that scores wrongly is worse than none: it produces confident numbers that
nobody checks, because checking the checker is exactly the step people skip.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.evals import CaseResult, EvalCase, load_cases, score  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "baseline.jsonl"


def case(relevant, retrieved, top=0.7):
    return CaseResult(case=EvalCase(id="X", query="q", relevant=relevant),
                      retrieved=retrieved, top_score=top)


# --- the three metrics disagree on purpose -----------------------------------

def test_hit_is_generous_and_recall_is_strict():
    """One of three correct answers: hit says perfect, recall says a third. Reporting
    hit@k alone is how an eval flatters itself."""
    r = case(["A", "B", "C"], ["A", "X", "Y"])
    assert r.hit_at(10) == 1.0
    assert r.recall_at(10) == 1 / 3


def test_recall_at_k_respects_the_cutoff():
    r = case(["A", "B"], ["X", "A", "Y", "B"])
    assert r.recall_at(1) == 0.0
    assert r.recall_at(2) == 0.5
    assert r.recall_at(4) == 1.0


def test_mrr_measures_rank_which_recall_ignores():
    """Rank 1 and rank 9 are identical to recall@10 and very different to a reader."""
    assert case(["A"], ["A"] + list("BCDEFGHIJ")).reciprocal_rank == 1.0
    assert case(["A"], list("BCDEFGHIJ") + ["A"]).reciprocal_rank == 0.1


def test_complete_miss_scores_zero_everywhere():
    r = case(["A"], list("BCDE"))
    assert (r.hit_at(10), r.recall_at(10), r.reciprocal_rank) == (0.0, 0.0, 0.0)
    assert r.missed == ["A"]


# --- unanswerable cases are held apart ---------------------------------------

def test_unanswerable_cases_are_excluded_from_recall():
    """Recall of an empty set is undefined. Averaging it in would hide the number that
    matters — how confident the system sounds with nothing to say."""
    s = score([case(["A"], ["A"], top=0.8), case([], ["Z"], top=0.6)])
    assert s["answerable"] == 1 and s["unanswerable"] == 1
    assert s["recall@10"] == 1.0
    assert s["score_separation"] == 0.2


def test_separation_is_reported_only_when_both_kinds_are_present():
    assert "score_separation" not in score([case(["A"], ["A"])])


# --- the real golden set ------------------------------------------------------

def test_golden_set_loads_and_skips_metadata_rows():
    """The file carries its own label definition and contamination warning as "_" rows."""
    cases = load_cases(GOLDEN)
    assert len(cases) == 10
    assert all(c.id.startswith("B") for c in cases)


def test_golden_set_is_fully_verified_by_a_human():
    assert all(c.verified for c in load_cases(GOLDEN))


def test_golden_set_contains_an_unanswerable_case():
    """Without one there is nothing to measure abstention against."""
    assert any(c.is_unanswerable for c in load_cases(GOLDEN))


def test_metadata_rows_survive_a_rewrite():
    rows = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = [r for r in rows if all(k.startswith("_") for k in r)]
    assert meta, "the label definition and contamination warning must stay in the file"
