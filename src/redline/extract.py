"""Turn a passage of regulatory text into validated Obligation objects.

Two guarantees this module makes, and they are the whole point of it:

  1. Anything returned is schema-valid. Not "usually", not "after some cleanup" —
     it passed Pydantic or it was never returned. There is no text-parsing fallback,
     no regex rescue, no partial result. (ADR-001 D3)

  2. Every obligation's verbatim_quote genuinely appears in the source text. The model
     cannot invent an obligation and cite words that were never written. (ADR-001 D4,
     applied one stage earlier than planned because it costs almost nothing here.)

When either guarantee cannot be met within the retry budget, this module raises.
It never degrades quietly, because a silently-degraded compliance system is worse
than one that stops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from redline.config import require_api_key, settings
from redline.resilience import (  # re-exported so existing imports keep working
    CallBudget as _CallBudget,
    ModelUnavailable,
    call_with_backoff,
    is_rate_limit as _is_rate_limit,
    is_transient as _is_transient,
    suggested_delay as _suggested_delay,
    suggested_replacement_model as _suggested_replacement_model,
)
from redline.schemas import ExtractionResult


def _client():
    """Build the Gemini client. Imported lazily so the SDK is only needed when the model
    is actually called — the test suite imports this module without it."""
    from google import genai

    return genai.Client(api_key=require_api_key())


# Markers that a failure is the API's problem rather than ours. Matched on the exception
# class name and message rather than by importing the SDK's error classes, because those
# live under a private `_gaos` module whose path changes between versions — importing it
# would make this file break on an SDK upgrade for no benefit.
_TRANSIENT_MARKERS = (
    "servererror", "internalserver", "serviceunavailable", "unavailable",
    "resourceexhausted", "toomanyrequests", "deadlineexceeded", "timeout",
    "high demand", "overloaded", "try again later", "rate limit", "quota",
    "connectionerror", "remoteprotocol", " 429", " 500", " 502", " 503", " 504",
    "code: 429", "code: 500", "code: 502", "code: 503", "code: 504",
)


_RATE_LIMIT_MARKERS = (
    "ratelimit", "resourceexhausted", "toomanyrequests", "too_many_requests",
    "quota", "rate limit", " 429", "code: 429",
)

# Servers tell us how long to wait. Two phrasings seen in the wild from this API:
#   "Please retry in 33.368069941s."      and      "retryDelay": "33s"
_DELAY_PATTERNS = (
    re.compile(r"retry in ([\d.]+)\s*s", re.I),
    re.compile(r"retry_?delay[\"'\s:]+([\d.]+)\s*s", re.I),
)


def _is_rate_limit(exc: Exception) -> bool:
    """Is this a quota/rate problem specifically, rather than a server hiccup?

    Worth separating from the general transient case because the correct behaviour is
    the opposite of intuition: when you are being rate limited, retrying sooner makes
    things worse, and the server usually tells you exactly how long to wait.
    """
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(marker in blob for marker in _RATE_LIMIT_MARKERS)


def _suggested_delay(exc: Exception) -> float | None:
    """Pull the server's own retry-after hint out of the error, if it gave one.

    Obeying the server beats guessing. Our exponential backoff is a fallback for when
    the server says nothing, not a substitute for what it does say.
    """
    blob = str(exc)
    for pattern in _DELAY_PATTERNS:
        match = pattern.search(blob)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


_REPLACEMENT_PATTERN = re.compile(r"use\s+models/([\w.\-]+)", re.I)


def _suggested_replacement_model(exc: Exception) -> str | None:
    """When a model is retired the API names its successor. Extract it.

    Deprecation is permanent, so no retry can help — but it is one of the few permanent
    failures that comes with the fix attached. Losing that inside a stack trace wastes
    the one useful thing the error contained. On Day 54 an unattended run that dies on a
    retired model should be able to say what to switch to.
    """
    match = _REPLACEMENT_PATTERN.search(str(exc))
    return match.group(1) if match else None


def _is_transient(exc: Exception) -> bool:
    """Is this failure worth waiting and retrying, or is it permanent?

    Getting this distinction wrong is expensive in both directions: retrying a bad API
    key wastes a minute and still fails, while giving up on a momentary overload throws
    away a run that would have succeeded two seconds later.
    """
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(marker in blob for marker in _TRANSIENT_MARKERS)


@dataclass
class _CallBudget:
    """How many model calls one extraction is still allowed to make.

    Threaded through every retry path so that no combination of semantic retries and
    transport retries can exceed it. A budget that only one loop respects is not a budget.
    """

    limit: int
    used: int = 0

    def spend(self) -> None:
        if self.used >= self.limit:
            raise ModelUnavailable(
                f"Model call budget exhausted ({self.limit} calls for one extraction). "
                "Raise MAX_MODEL_CALLS_PER_RUN in .env if this passage genuinely needs "
                "more, but first check whether something is failing in a loop."
            )
        self.used += 1


def _call_model_resilient(client, prompt: str, budget: _CallBudget) -> tuple[str, int]:
    """Call the model through the shared backoff policy in redline.resilience."""
    return call_with_backoff(lambda: _call_model(client, prompt), budget, label="extraction")



def extract_obligations(source_text: str, source_ref_hint: str = "") -> ExtractionRun:
    """Extract obligations from one passage.

    Args:
        source_text: The passage. Keep it to one article or section — feeding a whole
            regulation produces shallow extraction and unverifiable quotes.
        source_ref_hint: What to call this passage in citations, e.g.
            "EU AI Act, Article 12". Improves source_ref accuracy considerably.

    Returns:
        ExtractionRun with the validated result and what it took to get there.

    Raises:
        ExtractionFailed: if no valid, grounded result was produced within budget.
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
            # Re-prompting with the SPECIFIC failure, not a generic "try again".
            # A model told exactly what was wrong corrects it far more often than one
            # told only that it failed.
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
            msg = f"attempt {attempt}: schema validation failed — {e}"
            errors.append(msg)
            feedback = str(e)
            continue

        # Gate 2: are the quotes real, and is the result internally consistent?
        problems = _check_grounding(result, source_text) + _check_consistency(result)
        if problems:
            msg = f"attempt {attempt}: {len(problems)} grounding/consistency problem(s)"
            errors.append(msg + " — " + "; ".join(problems))
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
