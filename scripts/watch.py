"""Run every watcher once, store what is new, report what happened.

    python scripts/watch.py

Run it twice in a row. The second run must store zero documents. That is the whole test
of Day 32 — a watcher that cannot tell new from already-seen will re-process the same
regulation every night, and pay the model to do it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from redline.sources import federal_register  # noqa: E402
from redline.store import (  # noqa: E402
    connect, count_documents, init_db, recent_documents, save_document,
)

# Add sources here as they are built. One entry per source; nothing else changes.
WATCHERS = [
    (federal_register.SOURCE_NAME, federal_register.fetch),
]


def main() -> int:
    init_db()

    total_new = 0
    total_seen = 0
    failures: list[str] = []

    with connect() as conn:
        before = count_documents(conn)

        for name, fetch in WATCHERS:
            try:
                documents = fetch()
            except httpx.HTTPError as e:
                # One source failing must not stop the others, and must never be
                # silently ignored — a source that quietly stops working looks exactly
                # like a quiet regulatory week.
                failures.append(f"{name}: {type(e).__name__}: {e}")
                print(f"  {name:<20} FETCH FAILED — {e}")
                continue

            new = sum(1 for doc in documents if save_document(conn, doc))
            total_new += new
            total_seen += len(documents)
            print(f"  {name:<20} fetched {len(documents):>3}, new {new:>3}")

        after = count_documents(conn)

        print()
        print(f"  documents in store: {before} -> {after}")
        print(f"  fetched this run  : {total_seen}")
        print(f"  newly stored      : {total_new}")

        if failures:
            print(f"\n  {len(failures)} source(s) failed:")
            for f in failures:
                print(f"    - {f}")

        rows = recent_documents(conn, limit=5)
        if rows:
            print("\n  most recently seen:")
            for r in rows:
                published = r["published_on"] or "?"
                print(f"    [{published}] {(r['doc_type'] or '?'):<6} {r['title'][:70]}")

    # Non-zero exit when every source failed, so a scheduler can alert on it.
    return 1 if failures and total_seen == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
