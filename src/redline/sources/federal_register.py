"""Watcher for the US Federal Register.

Chosen as the first source because it is genuinely public, documented, needs no API key,
and explicitly permits programmatic access — nothing here depends on scraping a site that
would rather we did not, which matters when the project is about compliance.
https://www.federalregister.gov/developers/documentation/api/v1

It publishes final rules from every US federal agency: exactly the kind of document that
forces an internal policy to change.
"""

from __future__ import annotations

from datetime import date, datetime

import httpx

from redline.schemas import SourceDocument

SOURCE_NAME = "federal_register"
BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"

# Agencies whose rules plausibly touch an information-security policy library.
# Narrow on purpose: a watcher that returns everything returns noise, and noise costs
# money once every document reaches the agent.
DEFAULT_AGENCIES = [
    "health-and-human-services-department",
    "securities-and-exchange-commission",
    "federal-trade-commission",
]

FIELDS = [
    "document_number", "title", "type", "abstract",
    "publication_date", "html_url", "pdf_url", "agencies",
]


def _parse_date(value: str | None) -> date | None:
    """Parse the API's date, tolerating absence and malformation.

    A malformed date is not worth failing the whole fetch over — the document is still
    useful, it just sorts oddly. Dropping the document would be the larger loss.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def fetch(
    per_page: int = 20,
    agencies: list[str] | None = None,
    doc_types: tuple[str, ...] = ("RULE",),
    timeout_seconds: float = 20.0,
) -> list[SourceDocument]:
    """Fetch the newest matching documents.

    Args:
        per_page: How many to request. The API caps this at 1000; keep it small.
        agencies: Agency slugs to filter to. Defaults to DEFAULT_AGENCIES.
        doc_types: "RULE" (final rules), "PRORULE" (proposed), "NOTICE", "PRESDOCU".
        timeout_seconds: Hard limit. An unbounded HTTP call inside a scheduled job is
            how an overnight run hangs until morning. (ADR-001 D6)

    Raises:
        httpx.HTTPError: on network failure or a non-2xx response. Deliberately not
            swallowed — a source that silently returns zero documents is indistinguishable
            from a quiet regulatory week, and that is the worst possible failure mode for
            a monitoring system.
    """
    params: list[tuple[str, str]] = [
        ("per_page", str(per_page)),
        ("order", "newest"),
    ]
    params += [("fields[]", f) for f in FIELDS]
    params += [("conditions[type][]", t) for t in doc_types]
    params += [
        ("conditions[agencies][]", slug)
        for slug in (agencies if agencies is not None else DEFAULT_AGENCIES)
    ]

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(
            BASE_URL,
            params=params,
            headers={"User-Agent": "Redline/0.1 (regulatory change monitoring; research)"},
        )
        response.raise_for_status()
        payload = response.json()

    return [to_document(item) for item in payload.get("results", [])]


def to_document(item: dict) -> SourceDocument:
    """Map one API result onto our own shape.

    Doing this here, rather than passing raw API dictionaries around, means the rest of
    the system never learns the Federal Register's field names. Adding a second source
    then requires a new file, not edits scattered through the codebase.

    Uses .get() with fallbacks throughout: an API that adds or drops an optional field
    should not crash a scheduled run.
    """
    return SourceDocument(
        source=SOURCE_NAME,
        external_id=item["document_number"],  # the one field we genuinely cannot proceed without
        title=item.get("title") or "(untitled)",
        doc_type=item.get("type"),
        published_on=_parse_date(item.get("publication_date")),
        html_url=item.get("html_url"),
        pdf_url=item.get("pdf_url"),
        abstract=item.get("abstract"),
        agencies=[a.get("name", "") for a in item.get("agencies", []) if a.get("name")],
    )
