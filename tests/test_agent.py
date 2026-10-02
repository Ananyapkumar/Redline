"""Tests for the agent's decision logic.

Every test here runs WITHOUT the model or the database. That is deliberate: the parts worth
testing are the parts that decide — when to abstain, how confidence is derived, whether a
fabricated citation is caught. Those are pure functions of their inputs, and a test suite
that needed an API key would never be run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from redline.agent import (  # noqa: E402
    CONFIDENCE_THRESHOLD, RETRIEVAL_THRESHOLD, Route,
    _derive_confidence, _validate_citations, assess,
)
from redline.resilience import CallBudget  # noqa: E402
from redline.schemas import (  # noqa: E402
    ClauseAssessment, ImpactAnalysis, ImpactVerdict, Severity,
)


def candidate(ref, sim):
    return {"ref": ref, "similarity": sim, "context": "Policy — Section — Heading",
            "body": "Some clause text long enough to be meaningful."}


def assessment(clause_id, verdict=ImpactVerdict.AFFECTED, **kw):
    return ClauseAssessment(
        clause_id=clause_id, verdict=verdict,
        severity=kw.get("severity", Severity.MEDIUM),
        reasoning=kw.get("reasoning", "The clause states a six year retention period where "
                                      "the obligation requires twelve months at minimum."),
        proposed_text=kw.get("proposed_text"),
    )


# --- abstention: the behaviour the project is named for -----------------------

def test_abstains_without_calling_the_model_when_retrieval_is_weak():
    """The cheapest way to avoid a confident wrong answer is not to ask the question."""
    called = []

    def retrieve(*a, **k):
        return [candidate("P-001 §1.1", 0.52), candidate("P-002 §2.2", 0.51)]

    result = assess(None, "an obligation about aircraft airworthiness", [0.1],
                    retrieve_fn=retrieve, budget=CallBudget(limit=5))

    assert result.route == Route.REVIEW
    assert result.confidence == 0.0
    assert result.model_calls == 0, "no model call should have been made"
    assert "without calling the model" in result.reason


def test_abstains_when_retrieval_returns_nothing():
    result = assess(None, "x", [0.1], retrieve_fn=lambda *a, **k: [],
                    budget=CallBudget(limit=5))
    assert result.route == Route.REVIEW
    assert result.model_calls == 0


def test_threshold_sits_in_the_measured_gap():
    """0.67 is not a guess. Answerable queries measured 0.717-0.825 and unanswerable ones
    0.522-0.620 on the 27-case merged set; the threshold sits in the gap between them."""
    assert 0.620 < RETRIEVAL_THRESHOLD < 0.717


# --- citation validation ------------------------------------------------------

def test_fabricated_clause_id_is_caught():
    """The most damaging output this system could produce: an authoritative-looking
    reference to a clause that does not exist."""
    analysis = ImpactAnalysis(assessments=[assessment("P-999 §9.9")])
    assert _validate_citations(analysis, [candidate("P-002 §2.2", 0.8)]) == ["P-999 §9.9"]


def test_offered_clause_ids_pass():
    analysis = ImpactAnalysis(assessments=[assessment("P-002 §2.2")])
    assert _validate_citations(analysis, [candidate("P-002 §2.2", 0.8)]) == []


# --- confidence is derived, not requested -------------------------------------

def test_fabricated_citation_sinks_confidence_to_zero():
    """The minimum is taken, not the average. Averaging would let a strong retrieval score
    paper over a fabricated citation — exactly the failure this system exists to prevent."""
    analysis = ImpactAnalysis(assessments=[assessment("P-002 §2.2")])
    conf, why = _derive_confidence(0.85, 0.10, analysis, ["P-999 §9.9"])
    assert conf == 0.0
    assert "fabricated" in why


def test_ambiguous_clause_caps_confidence_below_the_routing_threshold():
    analysis = ImpactAnalysis(
        assessments=[assessment("P-002 §4.1", verdict=ImpactVerdict.AMBIGUOUS)])
    conf, why = _derive_confidence(0.85, 0.10, analysis, [])
    assert conf < CONFIDENCE_THRESHOLD
    assert "ambiguous" in why


def test_a_tie_between_candidates_reduces_confidence():
    """A margin near zero means retrieval could not distinguish the top two, so the
    judgement rests on less than the top score suggests."""
    analysis = ImpactAnalysis(assessments=[assessment("P-002 §2.2")])
    tight, _ = _derive_confidence(0.85, 0.001, analysis, [])
    clear, _ = _derive_confidence(0.85, 0.080, analysis, [])
    assert tight < clear


def test_a_clean_strong_result_is_confident():
    analysis = ImpactAnalysis(assessments=[assessment("P-002 §2.2")])
    conf, _ = _derive_confidence(0.86, 0.09, analysis, [])
    assert conf >= CONFIDENCE_THRESHOLD


def test_barely_above_the_retrieval_threshold_is_not_confident():
    analysis = ImpactAnalysis(assessments=[assessment("P-002 §2.2")])
    conf, _ = _derive_confidence(RETRIEVAL_THRESHOLD + 0.005, 0.09, analysis, [])
    assert conf < CONFIDENCE_THRESHOLD


# --- schema guarantees --------------------------------------------------------

def test_a_verdict_without_a_reason_is_rejected():
    """A verdict nobody can review is not a verdict."""
    with pytest.raises(Exception):
        ClauseAssessment(clause_id="P-1 §1", verdict=ImpactVerdict.AFFECTED,
                         reasoning="related")


def test_invented_verdict_values_are_rejected():
    with pytest.raises(Exception):
        ClauseAssessment(clause_id="P-1 §1", verdict="probably affected",
                         reasoning="a sufficiently long explanation of the engagement here")


def test_affected_and_ambiguous_helpers_partition_correctly():
    analysis = ImpactAnalysis(assessments=[
        assessment("P-1 §1", ImpactVerdict.AFFECTED),
        assessment("P-2 §2", ImpactVerdict.NOT_AFFECTED),
        assessment("P-3 §3", ImpactVerdict.AMBIGUOUS),
    ])
    assert [a.clause_id for a in analysis.affected] == ["P-1 §1"]
    assert [a.clause_id for a in analysis.ambiguous] == ["P-3 §3"]
