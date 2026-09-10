"""Search the index by meaning.

    python scripts/search.py "how long must audit logs be kept"
    python scripts/search.py "log retention" --corpus policy --limit 5

Day 35's success criterion: a plain-English question about log retention returns the
retention clause in the top five. Try the hard ones too — this is the last chance to
form an honest impression before Day 36 measures it properly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.embeddings import embed_query  # noqa: E402
from redline.store import connect, index_is_ready, search_chunks  # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    corpus = None
    limit = 10
    if "--corpus" in args:
        i = args.index("--corpus"); corpus = args[i + 1]; del args[i:i + 2]
    if "--limit" in args:
        i = args.index("--limit"); limit = int(args[i + 1]); del args[i:i + 2]

    query = " ".join(args).strip()
    if not query:
        print(__doc__)
        return 2

    # Check the index BEFORE embedding the query. Otherwise an unindexed corpus costs an
    # API call to discover, which is exactly the sort of pointless spend the call budget
    # exists to prevent.
    with connect() as conn:
        ready, reason = index_is_ready(conn)
    if not ready:
        print(f"\n{reason}")
        return 2

    print(f'Query: "{query}"' + (f"  [corpus: {corpus}]" if corpus else "") + f"  ({reason})")
    vector = embed_query(query)

    with connect() as conn:
        results = search_chunks(conn, vector, corpus=corpus, limit=limit)

    if not results:
        print(f"\nNo results in corpus {corpus!r}. Indexed corpora may differ — "
              "run scripts/index_corpus.py without --corpus to see what is stored.")
        return 1

    print()
    for rank, r in enumerate(results, 1):
        part = f" (part {r['part'] + 1}/{r['of_parts']})" if r["of_parts"] > 1 else ""
        print(f"  {rank:>2}. {r['similarity']:.3f}  [{r['corpus']}] {r['ref'] or '-'}{part}")
        print(f"      {r['context'][:88]}")
        print(f"      {' '.join(r['body'].split())[:88]}...")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
