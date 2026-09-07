"""Hostile-input tests for the extractor.

These do not check that extraction is *good*. Quality is measured on Day 42 with a
labelled set. These check that the extractor never returns something the rest of the
system cannot trust — which is a different property, and the one that matters first.

Run:  python -m pytest tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.extract import (  # noqa: E402
    _CallBudget,
    _check_consistency,
    _check_grounding,
    _is_rate_limit,
    _is_transient,
    _normalise,
    _suggested_delay,
)
from redline.extract import ModelUnavailable  # noqa: E402
from redline.schemas import (  # noqa: E402
    ExtractionResult,
    Modality,
    Obligation,
    ObligedParty,
)

SOURCE = (
    "The provider shall retain automatically generated logs for a period of not less "
    "than six (6) months, and shall make them available to the competent authority "
    "upon a reasoned request."
)


def _obligation(**overrides) -> Obligation:
    base = dict(
        source_ref="Article 12(1)",
        obliged_party=ObligedParty.PROVIDER,
        modality=Modality.MANDATORY,
        action="retain automatically generated logs",
        subject_matter="automatically generated logs",
        applies_to="high-risk AI systems",
        verbatim_quote=(
            "The provider shall retain automatically generated logs for a period of "
            "not less than six (6) months"
        ),
    )
    base.update(overrides)
    return Obligation(**base)


# --- schema-level rejections -------------------------------------------------

def test_short_quote_is_rejected():
    """A fragment too small to verify must not get through."""
    with pytest.raises(ValidationError, match="too short"):
        _obligation(verbatim_quote="shall retain")


def test_rambling_action_is_rejected():
    """An 'action' that restates the clause is a symptom of a bad extraction."""
    with pytest.raises(ValidationError, match="maximum 15"):
        _obligation(action=" ".join(["word"] * 20))


def test_empty_action_is_rejected():
    with pytest.raises(ValidationError):
        _obligation(action="   ")


def test_invalid_modality_is_rejected():
    """Free text where an enum is required must fail, not be coerced."""
    with pytest.raises(ValidationError):
        _obligation(modality="sort of mandatory")


def test_duplicate_source_refs_are_rejected():
    with pytest.raises(ValidationError, match="Duplicate"):
        ExtractionResult(obligations=[_obligation(), _obligation()])


# --- grounding ---------------------------------------------------------------

def test_real_quote_passes_grounding():
    result = ExtractionResult(obligations=[_obligation()])
    assert _check_grounding(result, SOURCE) == []


def test_fabricated_quote_is_caught():
    """The core guarantee: invented text cannot pass as a citation."""
    result = ExtractionResult(
        obligations=[
            _obligation(
                verbatim_quote=(
                    "The provider shall retain logs for a period of not less than "
                    "twenty-four (24) months as required by statute"
                )
            )
        ]
    )
    problems = _check_grounding(result, SOURCE)
    assert len(problems) == 1
    assert "does not appear" in problems[0]


def test_grounding_tolerates_reflowed_whitespace():
    """A quote broken across PDF lines is not a fabrication and must not be treated as one."""
    result = ExtractionResult(
        obligations=[
            _obligation(
                verbatim_quote=(
                    "The provider shall retain\n   automatically generated logs\n"
                    "for a period of not less than six (6) months"
                )
            )
        ]
    )
    assert _check_grounding(result, SOURCE) == []


def test_grounding_tolerates_curly_quotes_and_dashes():
    """Typographic substitution by the model is cosmetic, not fabrication."""
    src = "The provider shall notify the authority — without undue delay — of any incident."
    result = ExtractionResult(
        obligations=[
            _obligation(
                verbatim_quote=(
                    "The provider shall notify the authority - without undue delay - "
                    "of any incident."
                )
            )
        ]
    )
    assert _check_grounding(result, src) == []


# --- consistency -------------------------------------------------------------

def test_no_obligations_flag_conflicting_with_content_is_caught():
    result = ExtractionResult(
        obligations=[_obligation()], passage_contains_no_obligations=True
    )
    problems = _check_consistency(result)
    assert len(problems) == 1


def test_honest_empty_result_is_valid():
    """Finding nothing is a legitimate answer and must not be treated as a failure."""
    result = ExtractionResult(obligations=[], passage_contains_no_obligations=True)
    assert _check_consistency(result) == []
    assert _check_grounding(result, SOURCE) == []


# --- normalisation -----------------------------------------------------------

def test_normalise_collapses_whitespace_and_case():
    assert _normalise("  The   PROVIDER\n\tshall  ") == "the provider shall"


# --- failure classification --------------------------------------------------
# Getting these wrong is expensive in both directions: retrying a bad API key wastes
# time and still fails, while giving up on a momentary overload throws away a run that
# would have succeeded two seconds later.


class _FakeRateLimit(Exception):
    pass


def test_quota_error_is_recognised_as_rate_limit():
    exc = _FakeRateLimit(
        "Error code: 429 - Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
        "limit: 20. Please retry in 33.368069941s."
    )
    assert _is_rate_limit(exc) is True
    assert _is_transient(exc) is True


def test_server_overload_is_transient_but_not_rate_limit():
    exc = Exception("Error code: 500 - gemini is currently experiencing high demand")
    assert _is_transient(exc) is True
    assert _is_rate_limit(exc) is False


def test_bad_api_key_is_neither():
    """A permanent error must fail immediately, not consume the retry budget."""
    exc = Exception("Error code: 401 - API key not valid. Please pass a valid API key.")
    assert _is_transient(exc) is False
    assert _is_rate_limit(exc) is False


def test_server_suggested_delay_is_parsed():
    exc = Exception("Please retry in 33.368069941s.")
    assert _suggested_delay(exc) == pytest.approx(33.368069941)


def test_missing_delay_hint_returns_none():
    assert _suggested_delay(Exception("something went wrong")) is None


# --- call budget -------------------------------------------------------------


def test_budget_allows_up_to_its_limit():
    budget = _CallBudget(limit=3)
    for _ in range(3):
        budget.spend()
    assert budget.used == 3


def test_budget_refuses_the_call_past_its_limit():
    """The guarantee that one passage cannot quietly cost twelve requests."""
    budget = _CallBudget(limit=2)
    budget.spend()
    budget.spend()
    with pytest.raises(ModelUnavailable, match="budget exhausted"):
        budget.spend()
