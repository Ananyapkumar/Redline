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

-- One row per addressable section of a document, with its place in the hierarchy kept.
-- Flat text would be simpler and would destroy the thing that makes retrieval work:
-- "Article 12(3)(a)" means nothing without knowing it sits under 12(3), under 12.
CREATE TABLE IF NOT EXISTS document_sections (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT      NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    ref             TEXT,                   -- the document's own label, e.g. "II.A.3", "3.2.1"
    heading         TEXT,
    body            TEXT        NOT NULL,
    level           INT         NOT NULL,   -- 0 = top, deeper = more nested
    ordinal         INT         NOT NULL,   -- position in reading order
    parent_ordinal  INT,                    -- ordinal of the enclosing section
    char_count      INT         NOT NULL,
    extracted_from  TEXT        NOT NULL,   -- xml | html | pdf — which path produced this

    -- Re-normalising a document must replace its sections, never duplicate them.
    CONSTRAINT document_sections_doc_ordinal_key UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS document_sections_doc_idx ON document_sections (document_id, ordinal);

-- One row per retrievable unit, from either corpus.
--
-- corpus = 'policy'     -> ref is a clause id, "P-002 §2.2". These are the things the
--                          agent cites, so ref must be exact and stable.
-- corpus = 'regulation' -> ref is a section label within a regulatory document.
--
-- Both live in one table so a single query can search either or both, and so the
-- embedding dimension is enforced in one place.
CREATE TABLE IF NOT EXISTS chunks (
    id            BIGSERIAL PRIMARY KEY,
    corpus        TEXT          NOT NULL CHECK (corpus IN ('policy', 'regulation')),
    ref           TEXT,
    context       TEXT          NOT NULL,
    body          TEXT          NOT NULL,
    text          TEXT          NOT NULL,   -- context + body; exactly what was embedded
    part          INT           NOT NULL DEFAULT 0,
    of_parts      INT           NOT NULL DEFAULT 1,
    source_id     BIGINT,                   -- source_documents.id, for regulation chunks
    embedding     vector(1536),
    indexed_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),

    -- Re-indexing must replace, never duplicate.
    CONSTRAINT chunks_identity_key UNIQUE (corpus, ref, part)
);

CREATE INDEX IF NOT EXISTS chunks_corpus_idx ON chunks (corpus);

-- Approximate nearest-neighbour index. Without it every search is a full scan: fine at
-- 131 rows, unusable later. HNSW caps at 2000 dimensions, which is why embeddings are
-- requested at 1536 rather than the model default of 3072.
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Keyword half of hybrid retrieval, ready for Day 39. Built now because it costs nothing
-- and needs the same table.
CREATE INDEX IF NOT EXISTS chunks_fts_idx
    ON chunks USING gin (to_tsvector('english', text));
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


# --- sections ---------------------------------------------------------------


def replace_sections(conn: psycopg.Connection, document_id: int, sections: list) -> int:
    """Store a document's sections, replacing any previous extraction.

    Delete-then-insert rather than upsert: re-normalising with a better parser usually
    changes how many sections there are, so matching old rows to new ones is meaningless.
    Both statements share one transaction, so a document is never left half-normalised.
    """
    conn.execute("DELETE FROM document_sections WHERE document_id = %s", (document_id,))
    for sec in sections:
        conn.execute(
            """
            INSERT INTO document_sections
                (document_id, ref, heading, body, level, ordinal,
                 parent_ordinal, char_count, extracted_from)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                document_id, sec.ref, sec.heading, sec.body, sec.level,
                sec.ordinal, sec.parent_ordinal, len(sec.body), sec.extracted_from,
            ),
        )
    return len(sections)


def documents_needing_normalisation(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    """Documents with no sections yet. The unit of work for scripts/normalise.py."""
    return conn.execute(
        """
        SELECT d.id, d.external_id, d.title, d.html_url, d.pdf_url
        FROM source_documents d
        LEFT JOIN document_sections s ON s.document_id = d.id
        WHERE s.id IS NULL
        GROUP BY d.id
        ORDER BY d.published_on DESC NULLS LAST
        LIMIT %s
        """,
        (limit,),
    ).fetchall()


def sections_for(conn: psycopg.Connection, document_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT ref, heading, body, level, ordinal, parent_ordinal, char_count, extracted_from
        FROM document_sections WHERE document_id = %s ORDER BY ordinal
        """,
        (document_id,),
    ).fetchall()


def section_stats(conn: psycopg.Connection) -> dict:
    row = conn.execute(
        """
        SELECT count(DISTINCT document_id) AS documents,
               count(*)                    AS sections,
               coalesce(round(avg(char_count)), 0) AS avg_chars,
               coalesce(max(level), 0)     AS max_depth
        FROM document_sections
        """
    ).fetchone()
    return dict(row)


# --- chunks -----------------------------------------------------------------


def upsert_chunk(conn: psycopg.Connection, corpus: str, chunk, embedding: list[float],
                 source_id: int | None = None) -> None:
    """Store one chunk and its vector, replacing any previous version of the same unit.

    ON CONFLICT ... DO UPDATE rather than DO NOTHING: re-indexing after a chunking change
    must overwrite. Skipping silently would leave the old vector attached to new text.
    """
    conn.execute(
        """
        INSERT INTO chunks (corpus, ref, context, body, text, part, of_parts,
                            source_id, embedding, indexed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (corpus, ref, part) DO UPDATE SET
            context = EXCLUDED.context, body = EXCLUDED.body, text = EXCLUDED.text,
            of_parts = EXCLUDED.of_parts, source_id = EXCLUDED.source_id,
            embedding = EXCLUDED.embedding, indexed_at = now()
        """,
        (corpus, chunk.ref, chunk.context, chunk.body, chunk.text,
         chunk.part, chunk.of_parts, source_id, str(embedding)),
    )


def search_chunks(conn: psycopg.Connection, query_embedding: list[float],
                  corpus: str | None = None, limit: int = 10) -> list[dict]:
    """Nearest neighbours by cosine distance.

    pgvector's <=> operator returns cosine DISTANCE: 0 is identical, 2 is opposite. It is
    converted to a similarity here so the number reads the way people expect, while the
    ORDER BY still uses the raw distance — that is what the HNSW index can accelerate.
    An ORDER BY over the derived similarity would silently fall back to a full scan.
    """
    vector = str(query_embedding)
    if corpus:
        sql = """
            SELECT ref, context, body, part, of_parts, corpus,
                   1 - (embedding <=> %(v)s::vector) AS similarity
            FROM chunks
            WHERE corpus = %(corpus)s AND embedding IS NOT NULL
            ORDER BY embedding <=> %(v)s::vector
            LIMIT %(limit)s
        """
        params = {"v": vector, "corpus": corpus, "limit": limit}
    else:
        sql = """
            SELECT ref, context, body, part, of_parts, corpus,
                   1 - (embedding <=> %(v)s::vector) AS similarity
            FROM chunks
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %(v)s::vector
            LIMIT %(limit)s
        """
        params = {"v": vector, "limit": limit}
    return conn.execute(sql, params).fetchall()


def index_is_ready(conn: psycopg.Connection) -> tuple[bool, str]:
    """Is there anything to search? Returns (ready, reason).

    Added on Day 35 after `search.py` answered "have you indexed anything yet?" with a
    psycopg traceback about a missing relation. A tool that fails should say what to do
    next — the same rule the extractor follows for a missing API key.
    """
    exists = conn.execute(
        "SELECT to_regclass('public.chunks') IS NOT NULL AS present"
    ).fetchone()["present"]
    if not exists:
        return False, ("The chunks table does not exist yet — nothing has been indexed. "
                       "Run: python scripts/index_corpus.py policy")

    n = conn.execute(
        "SELECT count(*) AS n FROM chunks WHERE embedding IS NOT NULL"
    ).fetchone()["n"]
    if not n:
        return False, ("The chunks table exists but holds no embeddings. "
                       "Run: python scripts/index_corpus.py policy")
    return True, f"{n} embedded chunks"


def chunk_stats(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        SELECT corpus,
               count(*)                              AS chunks,
               count(*) FILTER (WHERE of_parts > 1)  AS split_chunks,
               round(avg(length(body)))              AS avg_body_chars,
               max(length(body))                     AS max_body_chars,
               count(*) FILTER (WHERE embedding IS NULL) AS unembedded
        FROM chunks GROUP BY corpus ORDER BY corpus
        """
    ).fetchall()


def existing_chunk_refs(conn: psycopg.Connection, corpus: str) -> set[str]:
    """Refs already embedded, so re-running indexing skips work rather than repeating it."""
    rows = conn.execute(
        "SELECT DISTINCT ref FROM chunks WHERE corpus = %s AND embedding IS NOT NULL",
        (corpus,),
    ).fetchall()
    return {r["ref"] for r in rows if r["ref"]}
