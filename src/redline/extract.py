"""Turn a passage of regulatory text into validated Obligation objects.

Two guarantees this module makes, and they are the whole point of it:

  1. Anything returned is schema-valid. Not "usually", not "after some cleanup" —
     it passed Pydantic or it was never returned. There is no text-parsing fallback,
     no regex rescue, no partial result. (ADR-001 D3)

  2. Every obligation's verbatim_quote genuinely appears in the source text. The model
     cannot invent an obligation and cite words that were never written. (ADR-001 D4)

When either guarantee cannot be met within the retry budget, this module raises. It never
degrades quietly, because a silently-degraded compliance system is worse than one that
stops.

Failure handling lives in redline.resilience and is shared with embedding. Several names
are re-exported here under their original private spellings so that code and tests written
against this module before the split keep working.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from redline.config import require_api_key, settings
from redline.resilience import (  # noqa: F401 - re-exported for callers and tests
    CallBudget as _CallBudget,
    ModelUnavailable,
    call_with_backoff,
    is_rate_limit as _is_rate_limit,
    is_transient as _is_transient,
    suggested_delay as _suggested_delay,
    suggested_replacement_model as _suggested_replacement_model,
)
from redline.schemas import ExtractionResult


class ExtractionFailed(RuntimeError):
    """The model could not produce a valid, grounded result within budget.

    Distinct from ModelUnavailable: this says "this passage cannot be extracted
    correctly", not "come back later". A scheduler should dead-letter this one and
    retry the other.
    """

    def __init__(self, message: str, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass
class ExtractionRun:
    """What happened during one call to extract_obligations.

    Reliability data is kept beside the result rather than inside the domain objects, so
    an Obligation stays a description of a legal duty rather than a record of how hard it
    was to get. On Day 51 this is what gets written to the trace.
    """

    result: ExtractionResult
    attempts_used: int
    errors_encountered: list[str] = field(default_factory=list)
    transient_retries: int = 0


SYSTEM_PROMPT = """\
You are a regulatory analyst. You extract discrete legal obligations from regulatory text.

Rules you must follow:

1. Extract every DISTINCT obligation. A paragraph with sub-points (a), (b), (c) usually
   contains one obligation per sub-point, not one for the paragraph.
2. `verbatim_quote` must be copied EXACTLY from the passage, character for character.
   Do not paraphrase it. Do not tidy the punctuation. Do not join text from two places
   with an ellipsis. If you cannot quote it exactly, do not report the obligation.
3. `action` states the duty in under 15 words. It is not a summary of the clause.
4. Take `modality` from the operative verb: "shall" is mandatory, "shall not" is
   prohibited, "may" is permitted, "should" is recommended. Where the duty applies only
   under a stated condition, use conditional.
5. If the passage is definitional, procedural or introductory and imposes no duty at all,
   return an empty obligations list and set passage_contains_no_obligations to true.
   That is a correct answer, not a failure.
6. Do not infer obligations that the text does not state. Do not import knowledge of the
   regulation from memory. Only what is in the passage in front of you.
"""


def _normalise(text: str) -> str:
    """Collapse whitespace so quote matching survives line wrapping and PDF artefacts.

    A quote copied from a PDF often differs from the source only by where the line breaks
    fell. Comparing normalised strings catches genuine fabrication while tolerating
    harmless reflowing. Curly quotes and dashes are folded for the same reason.
    """
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("—", "-").replace("–", "-").replace("‑", "-")
    text = text.replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _check_grounding(result: ExtractionResult, source_text: str) -> list[str]:
    """Verify every quote exists in the source. Returns problems, empty if clean.

    This is the check that separates "the model said so" from "the document says so".
    """
    haystack = _normalise(source_text)
    problems: list[str] = []
    for ob in result.obligations:
        if _normalise(ob.verbatim_quote) not in haystack:
            problems.append(
                f"{ob.source_ref}: verbatim_quote does not appear in the source passage. "
                f"You wrote: {ob.verbatim_quote[:120]!r}. "
                "Copy the exact wording from the passage."
            )
    return problems


def _check_consistency(result: ExtractionResult) -> list[str]:
    """Cross-field rules Pydantic cannot express on a single field."""
    problems: list[str] = []
    if result.passage_contains_no_obligations and result.obligations:
        problems.append(
            "passage_contains_no_obligations is true but obligations is not empty. "
            "Choose one: either the passage imposes duties, or it does not."
        )
    return problems


def _client():
    """Build the Gemini client, importing the SDK lazily so the test suite and the
    watcher can import this module without it."""
    from google import genai

    return genai.Client(api_key=require_api_key())


def _call_model(client, prompt: str) -> str:
    """The single place the generation API is touched.

    If your installed google-genai is older and has no `interactions`, the equivalent is:
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={"response_mime_type": "application/json",
                    "response_schema": ExtractionResult},
        )
        return resp.text
    """
    interaction = client.interactions.create(
        model=settings.gemini_model,
        input=prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": ExtractionResult.model_json_schema(),
        },
    )
    return interaction.output_text


def _call_model_resilient(client, prompt: str, budget: _CallBudget) -> tuple[str, int]:
    """Call the model through the shared backoff policy.

    rate_limit_is_fatal=True because extraction is interactive: someone is waiting, and a
    429 saying "retry in 37s" should surface rather than sleep inside the request. Batch
    embedding passes False for the opposite reason.
    """
    return call_with_backoff(
        lambda: _call_model(client, prompt), budget,
        label="extraction", rate_limit_is_fatal=True,
    )


def extract_obligations(source_text: str, source_ref_hint: str = "") -> ExtractionRun:
    """Extract obligations from one passage.

    Args:
        source_text: The passage. Keep it to one article or section — feeding a whole
            regulation produces shallow extraction and unverifiable quotes.
        source_ref_hint: What to call this passage in citations, e.g.
            "EU AI Act, Article 12". Improves source_ref accuracy considerably.

    Raises:
        ExtractionFailed: no valid, grounded result within the retry budget.
        ModelUnavailable: the API could not serve the request at all.
    """
    if not source_text or not source_text.strip():
        raise ValueError("source_text is empty. Nothing to extract.")

    client = _client()
    budget = _CallBudget(limit=settings.max_model_calls_per_run)
    errors: list[str] = []
    feedback = ""
    transient_total = 0

    for attempt in range(1, settings.max_extraction_attempts + 1):
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Document: {source_ref_hint or 'unnamed source'}\n\n"
            f"Passage:\n---\n{source_text}\n---\n"
        )
        if feedback:
            # Re-prompt with the SPECIFIC failure, not a generic "try again". A model
            # told exactly what was wrong corrects it far more often than one told only
            # that it failed.
            prompt += (
                f"\nYour previous attempt was rejected for these reasons:\n{feedback}\n"
                "Fix exactly these problems and return the corrected result.\n"
            )

        raw, transient = _call_model_resilient(client, prompt, budget)
        transient_total += transient

        # Gate 1: does it parse into the schema at all?
        try:
            result = ExtractionResult.model_validate_json(raw)
        except ValidationError as e:
            errors.append(f"attempt {attempt}: schema validation failed - {e}")
            feedback = str(e)
            continue

        # Gate 2: are the quotes real, and is the result internally consistent?
        problems = _check_grounding(result, source_text) + _check_consistency(result)
        if problems:
            errors.append(
                f"attempt {attempt}: {len(problems)} grounding/consistency problem(s) - "
                + "; ".join(problems)
            )
            feedback = "\n".join(problems)
            continue

        return ExtractionRun(
            result=result,
            attempts_used=attempt,
            errors_encountered=errors,
            transient_retries=transient_total,
        )

    raise ExtractionFailed(
        f"No valid grounded extraction after {settings.max_extraction_attempts} attempts. "
        "The passage may be malformed, or the model may be unable to quote it accurately.",
        attempts=errors,
    )
