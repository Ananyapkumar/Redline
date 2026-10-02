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

# Day 37 correction.
#
# The first version filtered by AGENCY: HHS, SEC, FTC. It worked — 20 real rules — and
# produced a corpus the policy library could not engage with. The diagnostic said it
# plainly: 2 of 20 documents contained an on-topic obligation, 5 usable sections in total.
# Everything else was Medicare payment schedules, device reclassifications and food
# additive purity specs.
#
# The mistake was filtering on WHO published a rule rather than WHAT it is about. HHS
# publishes hospice wage indexes and PHI safeguards alike; the agency tells you nothing
# about whether an infosec policy library is affected.
#
# So: full-text search terms, each run as its own query. A rule about recordkeeping from
# any agency is more useful here than every rule from one agency.
SEARCH_TERMS = [
    "recordkeeping requirements",
    "protected health information",
    "data security safeguards",
    "privacy of individually identifiable information",
    "electronic records retention",
    "breach notification",
    "audit trail records",
    "automated decision system",
    "access controls information system",
    "business associate agreement",
]

# Kept as an optional narrowing, no longer the primary filter.
DEFAULT_AGENCIES: list[str] = []

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
    per_page: int = 10,
    terms: list[str] | None = None,
    agencies: list[str] | None = None,
    doc_types: tuple[str, ...] = ("RULE", "PRORULE"),
    timeout_seconds: float = 20.0,
) -> list[SourceDocument]:
    """Fetch documents matching each search term, deduplicated.

    One HTTP request per term. That is more calls than a single agency query, but the
    Federal Register API needs no key and has no meaningful rate limit, so the cost is a
    few seconds — against a corpus that the policy library can actually engage with.

    PRORULE (proposed rules) is included alongside RULE. Proposed rules are exactly what a
    compliance team wants early warning of, and they roughly double the available volume,
    which Day 55's proof run will need.

    Args:
        per_page: Results per term. Ten terms x ten results is plenty.
        terms: Full-text queries. Defaults to SEARCH_TERMS.
        agencies: Optional further narrowing. Empty by default — see the note above.
        doc_types: RULE, PRORULE, NOTICE, PRESDOCU.
        timeout_seconds: Hard limit per request. An unbounded HTTP call inside a
            scheduled job is how an overnight run hangs until morning. (ADR-001 D6)

    Raises:
        httpx.HTTPError: if EVERY term fails. A single term failing is tolerated and
            reported, because losing one query is better than losing the whole run — but
            silence when all of them fail would be indistinguishable from a quiet
            regulatory week, which is the worst failure mode a monitor can have.
    """
    queries = terms if terms is not None else SEARCH_TERMS
    slugs = agencies if agencies is not None else DEFAULT_AGENCIES

    seen: dict[str, SourceDocument] = {}
    failures: list[str] = []

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        for term in queries:
            params: list[tuple[str, str]] = [
                ("per_page", str(per_page)),
                ("order", "newest"),
                ("conditions[term]", term),
            ]
            params += [("fields[]", f) for f in FIELDS]
            params += [("conditions[type][]", t) for t in doc_types]
            params += [("conditions[agencies][]", slug) for slug in slugs]

            try:
                response = client.get(
                    BASE_URL, params=params,
                    headers={"User-Agent": "Redline/0.1 (regulatory change monitoring; research)"},
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                failures.append(f"{term!r}: {type(e).__name__}")
                continue

            for item in response.json().get("results", []):
                doc = to_document(item)
                seen.setdefault(doc.external_id, doc)   # same rule can match many terms

    if failures and len(failures) == len(queries):
        raise httpx.HTTPError(f"every search term failed: {'; '.join(failures)}")
    if failures:
        print(f"      {len(failures)}/{len(queries)} search term(s) failed: "
              f"{', '.join(failures)}")

    return list(seen.values())


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
