"""Fetch stored documents and break them into sections.

    python scripts/normalise.py           # process up to 5 not yet normalised
    python scripts/normalise.py 20        # process up to 20
    python scripts/normalise.py --show 3  # process 3 and print their section trees

Prefers each document's XML, falls back to HTML, then PDF. Which path was used is stored
per section, so you can measure later whether PDF-derived sections retrieve worse than
XML-derived ones — a question worth having data on by Day 41.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from redline.normalise import from_html, from_pdf, from_xml, render_tree  # noqa: E402
from redline.store import (  # noqa: E402
    connect, documents_needing_normalisation, init_db, replace_sections, section_stats,
)

TIMEOUT = 30.0
HEADERS = {"User-Agent": "Redline/0.1 (regulatory change monitoring; research)"}
DETAIL_URL = "https://www.federalregister.gov/api/v1/documents/{}.json"


def _get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, headers=HEADERS, follow_redirects=True)
    r.raise_for_status()
    return r


def fetch_sections(client: httpx.Client, row: dict) -> list:
    """Try XML, then HTML, then PDF. Return the first path that yields sections.

    Falling through on an empty result matters as much as falling through on an error:
    a parser that returns zero sections has failed just as completely as one that raised,
    it has simply been quieter about it.
    """
    external_id = row["external_id"]

    # 1. XML — structure is stated by the publisher.
    try:
        detail = _get(client, DETAIL_URL.format(external_id)).json()
        xml_url = detail.get("full_text_xml_url")
        if xml_url:
            sections = from_xml(_get(client, xml_url).content)
            if sections:
                return sections
            print("      xml produced no sections, falling back")
    except (httpx.HTTPError, ValueError) as e:
        print(f"      xml path failed ({type(e).__name__}), falling back")

    # 2. HTML — structure is implied by heading tags.
    if row.get("html_url"):
        try:
            sections = from_html(_get(client, row["html_url"]).text)
            if sections:
                return sections
            print("      html produced no sections, falling back")
        except httpx.HTTPError as e:
            print(f"      html path failed ({type(e).__name__}), falling back")

    # 3. PDF — structure must be guessed from font size. Lossy, and last.
    if row.get("pdf_url"):
        try:
            return from_pdf(_get(client, row["pdf_url"]).content)
        except (httpx.HTTPError, RuntimeError) as e:
            print(f"      pdf path failed ({type(e).__name__})")

    return []


def main() -> int:
    args = [a for a in sys.argv[1:]]
    show = "--show" in args
    if show:
        args.remove("--show")
    limit = int(args[0]) if args else 5

    init_db()
    processed = failed = 0

    with connect() as conn:
        pending = documents_needing_normalisation(conn, limit=limit)
        if not pending:
            print("Nothing to normalise. Run scripts/watch.py first, or all documents "
                  "already have sections.")
            return 0

        print(f"{len(pending)} document(s) to normalise\n")
        with httpx.Client(timeout=TIMEOUT) as client:
            for row in pending:
                print(f"  [{row['external_id']}] {row['title'][:64]}")
                sections = fetch_sections(client, row)
                if not sections:
                    print("      NO SECTIONS from any path — left unnormalised\n")
                    failed += 1
                    continue

                replace_sections(conn, row["id"], sections)
                origin = sections[0].extracted_from
                depth = max(s.level for s in sections)
                avg = sum(len(s.body) for s in sections) // len(sections)
                print(f"      {len(sections):>3} sections via {origin}, "
                      f"max depth {depth}, avg {avg} chars\n")
                processed += 1

                if show:
                    print(render_tree(sections[:25]))
                    print()

        stats = section_stats(conn)
        print("-" * 68)
        print(f"  normalised this run : {processed}")
        print(f"  failed              : {failed}")
        print(f"  documents with sections: {stats['documents']}")
        print(f"  sections total         : {stats['sections']}")
        print(f"  avg section length     : {stats['avg_chars']} chars")
        print(f"  deepest nesting        : level {stats['max_depth']}")

    return 1 if failed and not processed else 0


if __name__ == "__main__":
    raise SystemExit(main())
