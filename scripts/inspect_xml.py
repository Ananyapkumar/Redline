"""Report the actual element structure of a source document's XML.

    python scripts/inspect_xml.py 2026-17428

Written on Day 33 after the normaliser produced 44 sections at a uniform depth of 0 —
it ran, reported success, and stored a completely flat hierarchy. The cause was that the
heading-level detection assumed a SOURCE="HD1" attribute that this publisher may not use.

The lesson worth keeping: when a parser silently produces wrong structure, stop editing
the parser and go read the input. This script exists so that is one command, not an
afternoon, and it will be needed again when a second source is added.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
from lxml import etree  # noqa: E402

HEADERS = {"User-Agent": "Redline/0.1 (regulatory change monitoring; research)"}
DETAIL = "https://www.federalregister.gov/api/v1/documents/{}.json"

# Elements that plausibly carry structure in any regulatory XML dialect.
STRUCTURAL = {
    "HD", "HED", "SECTION", "SECTNO", "SUBJECT", "SUBSECT", "PART", "SUBPART",
    "TITLE", "AMDPAR", "REGTEXT", "PREAMB", "SUPLINF", "RULE", "EXTRACT", "GPH",
}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    external_id = sys.argv[1]

    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as client:
        detail = client.get(DETAIL.format(external_id)).json()
        xml_url = detail.get("full_text_xml_url")
        if not xml_url:
            print(f"No full_text_xml_url for {external_id}")
            return 1
        print(f"Document : {detail.get('title', '')[:70]}")
        print(f"XML      : {xml_url}\n")
        raw = client.get(xml_url).content

    root = etree.fromstring(raw, etree.XMLParser(recover=True, huge_tree=True))

    tags = collections.Counter(
        etree.QName(e).localname for e in root.iter() if isinstance(e.tag, str)
    )
    print("=== element frequency (top 20) ===")
    for tag, count in tags.most_common(20):
        print(f"  {tag:<14} {count}")

    print("\n=== structural elements, with their attributes ===")
    print("    Look for the attribute that encodes DEPTH. If every one is identical,")
    print("    this dialect signals nesting by element position, not by attribute.\n")
    seen = 0
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = etree.QName(el).localname.upper()
        if name not in STRUCTURAL:
            continue
        text = " ".join("".join(el.itertext()).split())[:52]
        depth = sum(1 for _ in el.iterancestors())
        print(f"  depth={depth:<2} <{name} {dict(el.attrib) or ''}> {text}")
        seen += 1
        if seen >= 40:
            print("  ... (truncated at 40)")
            break

    print("\n=== distinct attribute values on heading elements ===")
    attrs: collections.Counter = collections.Counter()
    for el in root.iter():
        if isinstance(el.tag, str) and etree.QName(el).localname.upper() in {"HD", "HED"}:
            attrs[tuple(sorted(el.attrib.items()))] += 1
    for combo, count in attrs.most_common():
        print(f"  {dict(combo) or '(no attributes)'}  x{count}")
    if not attrs:
        print("  No HD/HED elements at all — headings are marked some other way.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
