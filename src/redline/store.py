"""Persistence for fetched source documents.

Why the table is created here in Python rather than in db/init/:
files in db/init/ run exactly once, on the first start of an empty volume. Your volume
already has data in it, so anything added there now would never run. Application-level
`CREATE TABLE IF NOT EXISTS` is idempotent and works whether the database is new or not.

The dedupe guarantee (ADR-001 D6) lives in the database, not in Python: `content_hash`
carries a UNIQUE constraint, so two processes racing to insert the same document cannot
both succeed. Checking "does this exist?" in Python before inserting would look correct
and still lose that race.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from redline.config import settings
from redline.schemas import SourceDocument

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_documents (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT        NOT NULL,   -- which watcher found it
    external_id     TEXT        NOT NULL,   -- the source's own identifier
    title           TEXT        NOT NULL,
    doc_type        TEXT,                   -- Rule, Proposed Rule, Notice, ...
    published_on    DATE,
    html_url        TEXT,
    pdf_url         TEXT,
    abstract        TEXT,
    agencies        TEXT[]      NOT NULL DEFAULT '{}',
    content_hash    TEXT        NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The idempotency guarantee. Re-running the watcher can never duplicate a document.
    CONSTRAINT source_documents_content_hash_key UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS source_documents_source_idx    ON source_documents (source);
CREATE INDEX IF NOT EXISTS source_documents_published_idx ON source_documents (published_on DESC);
"""


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Open a connection, commit on success, roll back on any exception.

    The rollback matters: without it a failure midway through a batch would leave some
    documents stored and some not, with no record of where it stopped.
    """
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they do not exist. Safe to call on every startup."""
    with connect() as conn:
        conn.execute(SCHEMA)


def save_document(conn: psycopg.Connection, doc: SourceDocument) -> bool:
    """Insert one document. Returns True if it was new, False if already seen.

    ON CONFLICT DO NOTHING makes the insert a no-op when the hash already exists, and
    RETURNING id yields a row only when something was actually written — which is how we
    learn whether it was new without a separate SELECT, and without a race between the
    check and the insert.
    """
    row = conn.execute(
        """
        INSERT INTO source_documents
            (source, external_id, title, doc_type, published_on,
             html_url, pdf_url, abstract, agencies, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO NOTHING
        RETURNING id
        """,
        (
            doc.source, doc.external_id, doc.title, doc.doc_type, doc.published_on,
            doc.html_url, doc.pdf_url, doc.abstract, doc.agencies, doc.content_hash,
        ),
    ).fetchone()
    return row is not None


def count_documents(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT count(*) AS n FROM source_documents").fetchone()
    return int(row["n"])


def recent_documents(conn: psycopg.Connection, limit: int = 10) -> list[dict]:
    return conn.execute(
        """
        SELECT source, external_id, title, doc_type, published_on
        FROM source_documents
        ORDER BY first_seen_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
