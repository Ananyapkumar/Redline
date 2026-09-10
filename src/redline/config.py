"""Configuration, loaded once from the environment.

Everything tunable lives here rather than scattered through the code as literals, so the
run limits from ADR-001 D6 are visible in one place and enforceable from one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env from the project root into os.environ


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    embedding_model: str
    database_url: str

    # Run limits — ADR-001 D6
    max_cost_per_run_usd: float
    run_timeout_seconds: int
    max_agent_steps: int

    # How many times the extractor may re-prompt after a validation or grounding failure
    # before giving up loudly. Bounded so a stubbornly-wrong model cannot loop forever.
    max_extraction_attempts: int

    # Transport-level retries. Entirely separate from the above: these are for when the
    # API itself fails (overloaded, rate limited, timed out) and the correct response is
    # to wait and send the SAME request again, not to change the prompt.
    transient_max_attempts: int
    transient_backoff_seconds: float

    # Hard ceiling on model calls for ONE extraction, across every retry of every kind.
    # Without this, 3 semantic attempts x 4 transient retries = 12 requests for a single
    # passage — which is how a retry loop quietly destroys a rate-limited quota.
    # This is the request-count half of ADR-001 D6's cost ceiling.
    max_model_calls_per_run: int

    # Never sleep longer than this, even if the server asks us to. A scheduled job that
    # obediently waits 20 minutes has hung, as far as anyone watching is concerned.
    max_backoff_seconds: float

    # Embedding a whole corpus is many calls. Budgeted separately from extraction so one
    # runaway indexing job cannot consume the quota the agent needs.
    max_embedding_calls: int


def load_settings() -> Settings:
    # Deliberately does NOT raise on a missing key. Importing this module must never
    # fail, or the test suite cannot import anything that transitively depends on it.
    # The key is checked at the point of use instead — see require_api_key().
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://redline:redline_local_dev@localhost:5433/redline",
        ),
        max_cost_per_run_usd=_float("MAX_COST_PER_RUN_USD", 0.50),
        run_timeout_seconds=_int("RUN_TIMEOUT_SECONDS", 120),
        max_agent_steps=_int("MAX_AGENT_STEPS", 12),
        max_extraction_attempts=_int("MAX_EXTRACTION_ATTEMPTS", 3),
        transient_max_attempts=_int("TRANSIENT_MAX_ATTEMPTS", 4),
        transient_backoff_seconds=_float("TRANSIENT_BACKOFF_SECONDS", 2.0),
        max_model_calls_per_run=_int("MAX_MODEL_CALLS_PER_RUN", 6),
        max_backoff_seconds=_float("MAX_BACKOFF_SECONDS", 60.0),
        max_embedding_calls=_int("MAX_EMBEDDING_CALLS", 60),
    )


settings = load_settings()


def require_api_key() -> str:
    """Return the API key, or fail with a message that says what to do about it.

    Called at the moment the model is used rather than at import, so that code paths
    which never touch the model — the whole test suite — run without one.
    """
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return settings.gemini_api_key
