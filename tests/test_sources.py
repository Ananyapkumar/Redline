"""Tests for the watcher's mapping and deduplication logic.

No network and no database. These test the two things most likely to break silently:
how an API payload becomes a SourceDocument, and whether the content hash correctly
distinguishes "already seen" from "new".

A hash that is unstable causes the same regulation to be processed every night. A hash
that is too stable causes a revised regulation to be missed entirely. Both are quiet
failures, which is why they get tests rather than trust.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.schemas import SourceDocument  # noqa: E402
from redline.sources.federal_register import _parse_date, to_document  # noqa: E402

PAYLOAD = {
    "document_number": "2026-18282",
    "title": "Safeguarding Customer Records and Information",
    "type": "Rule",
    "abstract": "The Commission is adopting amendments...",
    "publication_date": "2026-09-02",
    "html_url": "https://www.federalregister.gov/documents/2026/09/02/2026-18282/x",
    "pdf_url": "https://www.govinfo.gov/content/pkg/FR-2026-09-02/pdf/2026-18282.pdf",
    "agencies": [{"name": "Securities and Exchange Commission", "id": 466}],
}


# --- mapping -----------------------------------------------------------------

def test_payload_maps_onto_our_shape():
    doc = to_document(PAYLOAD)
    assert doc.source == "federal_register"
    assert doc.external_id == "2026-18282"
    assert doc.doc_type == "Rule"
    assert doc.published_on == date(2026, 9, 2)
    assert doc.agencies == ["Securities and Exchange Commission"]


def test_missing_optional_fields_do_not_crash():
    """The API adding or dropping an optional field must not break a scheduled run."""
    doc = to_document({"document_number": "2026-00001"})
    assert doc.title == "(untitled)"
    assert doc.doc_type is None
    assert doc.published_on is None
    assert doc.agencies == []


def test_agencies_without_names_are_dropped_not_blanked():
    doc = to_document({**PAYLOAD, "agencies": [{"id": 1}, {"name": "FTC"}]})
    assert doc.agencies == ["FTC"]


def test_malformed_date_becomes_none_rather_than_raising():
    """Losing a sort order beats losing the document."""
    assert _parse_date("02-09-2026") is None
    assert _parse_date("") is None
    assert _parse_date(None) is None
    assert _parse_date("2026-09-02") == date(2026, 9, 2)


# --- deduplication -----------------------------------------------------------

def test_same_payload_gives_the_same_hash():
    """If this is unstable, every run re-processes every document and pays for it."""
    assert to_document(PAYLOAD).content_hash == to_document(PAYLOAD).content_hash


def test_different_document_number_gives_a_different_hash():
    other = to_document({**PAYLOAD, "document_number": "2026-99999"})
    assert to_document(PAYLOAD).content_hash != other.content_hash


def test_retitled_document_is_treated_as_new():
    """A changed title is a changed document. Better to re-examine than to miss an
    amendment that arrived under the same number."""
    other = to_document({**PAYLOAD, "title": "Safeguarding Customer Records — Correction"})
    assert to_document(PAYLOAD).content_hash != other.content_hash


def test_hash_is_namespaced_by_source():
    """Two sources could legitimately use the same identifier. The hash must not collide."""
    a = to_document(PAYLOAD)
    b = SourceDocument(**{**a.__dict__, "source": "eur_lex"})
    assert a.content_hash != b.content_hash


def test_irrelevant_field_changes_do_not_change_the_hash():
    """The abstract being reworded is not a new document. Hashing it would cause churn."""
    other = to_document({**PAYLOAD, "abstract": "Completely different wording here."})
    assert to_document(PAYLOAD).content_hash == other.content_hash
