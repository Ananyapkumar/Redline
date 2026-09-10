"""Chunk and embed both corpora into the vector index.

    python scripts/index_corpus.py policy        # the 131 policy clauses
    python scripts/index_corpus.py regulation    # normalised Federal Register sections
    python scripts/index_corpus.py both
    python scripts/index_corpus.py policy --force   # re-embed even if already indexed

Skips anything already embedded, so a failed run can be resumed without paying twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.chunking import chunk_clause, chunk_section  # noqa: E402
from redline.config import settings  # noqa: E402
from redline.embeddings import embed_documents  # noqa: E402
from redline.policies import load_corpus  # noqa: E402
from redline.resilience import CallBudget, ModelUnavailable  # noqa: E402
from redline.store import (  # noqa: E402
    chunk_stats, connect, existing_chunk_refs, init_db, upsert_chunk,
)

POLICIES = Path("data/policies")


def index_policies(conn, budget, force: bool) -> int:
    clauses = load_corpus(POLICIES)
    done = set() if force else existing_chunk_refs(conn, "policy")
    todo = [c for c in clauses if c.clause_id not in done]

    print(f"policy    : {len(clauses)} clauses, {len(done)} already indexed, "
          f"{len(todo)} to embed")
    if not todo:
        return 0

    chunks = [chunk_clause(c)[0] for c in todo]
    stored = 0

    def persist(start, _texts, vectors):
        """Write each batch as it arrives, and commit.

        Both halves matter. Writing without committing means the rollback in connect()
        discards everything when a later batch raises. Committing without writing per
        batch means there is nothing to commit.
        """
        nonlocal stored
        for chunk, vector in zip(chunks[start:start + len(vectors)], vectors):
            upsert_chunk(conn, "policy", chunk, vector)
        conn.commit()
        stored += len(vectors)

    embed_documents([c.text for c in chunks], budget=budget, on_batch=persist)
    return stored


def index_regulations(conn, budget, force: bool) -> int:
    rows = conn.execute(
        """
        SELECT s.id, s.ref, s.heading, s.body, s.document_id, d.title
        FROM document_sections s JOIN source_documents d ON d.id = s.document_id
        WHERE length(s.body) > 0 ORDER BY s.document_id, s.ordinal
        """
    ).fetchall()
    if not rows:
        print("regulation: no normalised sections. Run scripts/normalise.py first.")
        return 0

    done = set() if force else existing_chunk_refs(conn, "regulation")
    chunks, owners = [], []
    for row in rows:
        # Ref must be unique per chunk row. Section refs repeat across documents, so
        # namespace by document id — otherwise the UNIQUE constraint silently overwrites
        # one document's section with another's.
        for chunk in chunk_section(row, row["title"]):
            ref = f"{row['document_id']}:{row['id']}:{chunk.ref or 'x'}"
            if ref in done:
                continue
            chunks.append(chunk.__class__(**{**chunk.__dict__, "ref": ref}))
            owners.append(row["document_id"])

    split = sum(1 for c in chunks if c.is_split)
    print(f"regulation: {len(rows)} sections -> {len(chunks)} chunks "
          f"({split} from split sections), {len(chunks)} to embed")
    if not chunks:
        return 0

    stored = 0

    def persist(start, _texts, vectors):
        nonlocal stored
        for chunk, vector, doc_id in zip(chunks[start:start + len(vectors)],
                                         vectors, owners[start:start + len(vectors)]):
            upsert_chunk(conn, "regulation", chunk, vector, source_id=doc_id)
        conn.commit()
        stored += len(vectors)

    embed_documents([c.text for c in chunks], budget=budget, on_batch=persist)
    return stored


def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    which = next((a for a in args if not a.startswith("-")), "both")

    init_db()
    budget = CallBudget(limit=settings.max_embedding_calls)
    total = 0

    with connect() as conn:
        try:
            if which in ("policy", "both"):
                total += index_policies(conn, budget, force)
            if which in ("regulation", "both"):
                total += index_regulations(conn, budget, force)
        except ModelUnavailable as e:
            print(f"\nSTOPPED: {e}")
            # This claim is now true: persist() commits after every batch.
            conn.commit()
            for row in chunk_stats(conn):
                print(f"  saved so far: {row['corpus']} {row['chunks']} chunks "
                      f"({row['unembedded']} without embeddings)")
            print("Re-run to resume from here — indexed refs are skipped.")
            return 3

        print(f"\n  embedded this run : {total}")
        print(f"  API calls used    : {budget.used}/{budget.limit}")
        print("\n  index contents:")
        for row in chunk_stats(conn):
            print(f"    {row['corpus']:<11} {row['chunks']:>4} chunks  "
                  f"{row['split_chunks']:>3} split  "
                  f"avg {row['avg_body_chars']:>5} chars  "
                  f"max {row['max_body_chars']:>6}  "
                  f"unembedded {row['unembedded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
