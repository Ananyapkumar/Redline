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

import random
import re
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from redline.config import require_api_key, settings
from redline.schemas import ExtractionResult


class ModelUnavailable(RuntimeError):
    """The API could not be reached, or kept failing, after the transport retry budget.

    Deliberately a different exception from ExtractionFailed. They mean different things
    and a caller should treat them differently: this one says "come back later", the
    other says "this passage could not be extracted correctly". A scheduled run should
    dead-letter and retry on this; on the other, it should not bother retrying.
    """


class ExtractionFailed(RuntimeError):
    """Raised when the model could not produce a valid, grounded result in budget.

    Carries the attempt history so the failure can be diagnosed rather than guessed at.
    """

    def __init__(self, message: str, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass
class ExtractionRun:
    """What happened during one call to extract_obligations.

    Kept separate from the result so that reliability data — how many retries, which
    errors — is available to the caller without polluting the domain objects. On Day 51
    this is what gets written to the trace.
    """

    result: ExtractionResult
    attempts_used: int
    errors_encountered: list[str] = field(default_factory=list)
    transient_retries: int = 0  # how many times the API itself had to be re-tried


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

    A quote copied from a PDF often differs from the source only by where the line
    breaks fell. Comparing normalised strings catches genuine fabrication while
    tolerating harmless reflowing. Curly quotes and dashes are folded for the same reason.
    """
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("—", "-").replace("–", "-").replace("‑", "-")
    text = text.replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _check_grounding(result: ExtractionResult, source_text: str) -> list[str]:
    """Verify every quote exists in the source. Returns a list of problems, empty if clean.

    This is the check that makes the difference between "the model said so" and
    "the document says so".
    """
    haystack = _normalise(source_text)
    problems: list[str] = []
    for ob in result.obligations:
        needle = _normalise(ob.verbatim_quote)
        if needle not in haystack:
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
    """Call the model, waiting and resending if the API itself fails.

    This is NOT the same retry as the one in extract_obligations. That one changes the
    prompt because the model produced something wrong. This one resends the identical
    prompt because the model never got a chance to answer. Conflating them would mean
    telling a server that returned HTTP 500 that its quote was ungrounded — nonsense.

    Backoff is exponential with jitter: waits of roughly 2s, 4s, 8s, each nudged by a
    random fraction. The jitter matters when many requests fail at once — without it
    they all wake up together and hammer the recovering service in lockstep.

    Returns:
        (raw response text, number of transient retries it took)
    """
    last: Exception | None = None
    for attempt in range(1, settings.transient_max_attempts + 1):
        budget.spend()
        try:
            return _call_model(client, prompt), attempt - 1
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            if not _is_transient(exc):
                # Permanent: bad key, malformed request, retired model. Retrying cannot
                # help, so fail now rather than spending the budget proving it.
                replacement = _suggested_replacement_model(exc)
                if replacement:
                    raise ModelUnavailable(
                        f"Model '{settings.gemini_model}' is retired or unavailable to "
                        f"this account. The API recommends '{replacement}'. "
                        f"Set GEMINI_MODEL={replacement} in .env. Original error: {exc}"
                    ) from exc
                raise
            last = exc

            # A quota error is not a hiccup. Retrying into a rate limit consumes the
            # very budget that is exhausted, and on a hard daily cap no amount of
            # waiting inside this run will help. Surface it immediately with the
            # server's own advice rather than burning three more requests.
            if _is_rate_limit(exc):
                hint = _suggested_delay(exc)
                raise ModelUnavailable(
                    f"Rate limited or out of quota on '{settings.gemini_model}'. "
                    + (
                        f"The server asks you to wait {hint:.0f}s. "
                        if hint
                        else ""
                    )
                    + f"Original error: {exc}"
                ) from exc

            if attempt == settings.transient_max_attempts:
                break

            # Obey the server if it told us how long to wait; otherwise back off
            # exponentially with jitter. Either way, never longer than the cap.
            delay = _suggested_delay(exc)
            if delay is None:
                delay = settings.transient_backoff_seconds * (2 ** (attempt - 1))
                delay *= 1 + random.random() * 0.25
            delay = min(delay, settings.max_backoff_seconds)
            print(
                f"  [transient] {type(exc).__name__}: retrying in {delay:.1f}s "
                f"(attempt {attempt}/{settings.transient_max_attempts}, "
                f"calls used {budget.used}/{budget.limit})"
            )
            time.sleep(delay)

    raise ModelUnavailable(
        f"Model '{settings.gemini_model}' unavailable after "
        f"{settings.transient_max_attempts} attempts. Last error: {last}"
    ) from last


def _call_model(client, prompt: str) -> str:
    """The single place the model API is touched.

    Isolated deliberately: if the SDK's call signature changes, or you swap providers,
    exactly one function needs editing rather than the whole module.

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
