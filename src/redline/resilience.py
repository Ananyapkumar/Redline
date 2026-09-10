"""Shared failure handling for every external call.

Lifted out of extract.py on Day 35 because embedding needs exactly the same protection.
Duplicating a retry policy is how two code paths quietly diverge until one of them burns
a quota the other one respects.

The distinction this module exists to enforce: a call can fail because the service was
briefly unable to answer (retry it), because you have run out of allowance (do NOT retry
it), or because the request was wrong (never retry it). Treating all three the same is
the single most expensive mistake in a client library.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass

from redline.config import settings


class ModelUnavailable(RuntimeError):
    """The API could not be reached, or kept failing, within budget.

    Distinct from a content failure: this says "come back later", not "this input cannot
    be processed". A scheduler should retry the first and not waste money on the second.
    """


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

_DELAY_PATTERNS = (
    re.compile(r"retry in ([\d.]+)\s*s", re.I),
    re.compile(r"retry_?delay[\"'\s:]+([\d.]+)\s*s", re.I),
)

_REPLACEMENT_PATTERN = re.compile(r"use\s+models/([\w.\-]+)", re.I)


def is_transient(exc: Exception) -> bool:
    """Worth waiting and retrying, or permanent?

    Matches on class name and message rather than importing the SDK's error classes,
    because those live under private modules whose paths change between versions.
    """
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(m in blob for m in _TRANSIENT_MARKERS)


def is_rate_limit(exc: Exception) -> bool:
    """A quota problem specifically. Retrying sooner makes this one worse."""
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(m in blob for m in _RATE_LIMIT_MARKERS)


def suggested_delay(exc: Exception) -> float | None:
    """The server's own retry-after hint, if it gave one. Obeying beats guessing."""
    blob = str(exc)
    for pattern in _DELAY_PATTERNS:
        match = pattern.search(blob)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def suggested_replacement_model(exc: Exception) -> str | None:
    """When a model is retired the API names its successor. Deprecation is permanent, but
    it is one of the few permanent failures that arrives with the fix attached."""
    match = _REPLACEMENT_PATTERN.search(str(exc))
    return match.group(1) if match else None


@dataclass
class CallBudget:
    """How many API calls one unit of work may make, across every retry path.

    A budget that only one loop respects is not a budget.
    """

    limit: int
    used: int = 0

    def spend(self) -> None:
        if self.used >= self.limit:
            raise ModelUnavailable(
                f"Call budget exhausted ({self.limit} calls). Raise the limit in .env if "
                "this work genuinely needs more, but first check for a retry loop."
            )
        self.used += 1


def call_with_backoff(fn, budget: CallBudget, label: str = "call",
                      rate_limit_is_fatal: bool = True):
    """Run fn(), waiting and resending when the service itself fails.

    Backoff is exponential with jitter. The jitter matters when many calls fail at once:
    without it they all wake together and hammer the recovering service in lockstep.

    `rate_limit_is_fatal` encodes a distinction learned on Day 35: **a rate limit means
    different things to interactive work and to batch work.**

      Interactive (extraction): someone is waiting. A 429 that says "retry in 37s" should
      surface now, so the caller can pick a different model or come back later. Sitting
      in a sleep for half a minute inside a request is worse than failing.

      Batch (embedding a corpus): nobody is waiting. A per-minute quota is not an
      obstacle, it is a speed limit. Aborting a 131-item job because item 97 arrived too
      fast throws away the 96 that succeeded. Waiting 37 seconds costs 37 seconds.

    Returns (result, transient_retries).
    """
    last: Exception | None = None
    attempts = settings.transient_max_attempts * (1 if rate_limit_is_fatal else 4)
    for attempt in range(1, attempts + 1):
        budget.spend()
        try:
            return fn(), attempt - 1
        except Exception as exc:  # noqa: BLE001 - classified immediately
            if not is_transient(exc):
                replacement = suggested_replacement_model(exc)
                if replacement:
                    raise ModelUnavailable(
                        f"Model '{settings.gemini_model}' is retired or unavailable to "
                        f"this account. The API recommends '{replacement}'. Set "
                        f"GEMINI_MODEL={replacement} in .env. Original error: {exc}"
                    ) from exc
                raise
            last = exc

            if is_rate_limit(exc):
                hint = suggested_delay(exc)
                if rate_limit_is_fatal:
                    raise ModelUnavailable(
                        f"Rate limited or out of quota during {label}. "
                        + (f"The server asks you to wait {hint:.0f}s. " if hint else "")
                        + f"Original error: {exc}"
                    ) from exc
                # Batch mode: a per-minute quota is a speed limit, not a wall. Obey the
                # server's own hint, do not count this against the budget (nothing was
                # served), and try the same request again.
                wait = min(hint if hint else 60.0, settings.max_backoff_seconds)
                budget.used -= 1
                print(f"  [rate limit] {label}: waiting {wait:.0f}s as instructed")
                time.sleep(wait + 1)
                continue

            if attempt == attempts:
                break

            delay = suggested_delay(exc)
            if delay is None:
                delay = settings.transient_backoff_seconds * (2 ** (attempt - 1))
                delay *= 1 + random.random() * 0.25
            delay = min(delay, settings.max_backoff_seconds)
            print(f"  [transient] {label}: {type(exc).__name__}, retrying in {delay:.1f}s "
                  f"(attempt {attempt}/{attempts}, "
                  f"calls {budget.used}/{budget.limit})")
            time.sleep(delay)

    raise ModelUnavailable(
        f"{label} failed after {attempts} attempts. Last error: {last}"
    ) from last
