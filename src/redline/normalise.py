"""Turn published documents into sections that keep their hierarchy.

The single most important decision in this file is which input to parse.

A Federal Register document is available as PDF, as HTML, and as XML. The instinct is to
parse the PDF, because that is the "real" document. That instinct is wrong and expensive.
A PDF stores glyphs at coordinates; the fact that one line is a heading and the next is
body text exists only in the visual design, and recovering it means guessing from font
sizes. The XML stores that same structure explicitly, because the publisher already did
the work.

So: **the best PDF parser is not parsing the PDF.** Check for a structured format first.
Three paths, in strict order of preference:

    1. XML   — structure is stated. Use whenever offered.
    2. HTML  — structure is implied by heading tags. Good enough.
    3. PDF   — structure must be inferred from font size. Last resort, and the only
               option for sources like EUR-Lex or a university policy PDF.

Why hierarchy matters at all: "Article 12(3)(a)" is meaningless without knowing it sits
inside 12(3), inside 12. Flatten the document and a retrieved chunk loses the scope that
determines whether an obligation even applies. That loss is invisible until eval scores
come back bad on Day 41 with no obvious cause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches the label a legal document gives a section, at the start of a heading:
#   "3.2.1"  "II.A"  "(a)"  "Article 12"  "§ 5.4"  "Sec. 12"
# Matches the label a legal document gives a section, at the start of a heading.
#
# Two bugs found on Day 33 by running this against real Federal Register documents, both
# caused by being too permissive. They are worth keeping in the comments because the
# lesson generalises: a ref detector that fires too often is worse than one that fires
# too rarely, since a wrong ref silently corrupts the hierarchy while a missing one
# merely leaves a section unlabelled.
#
#   1. re.I applied to the roman-numeral branch, so "List of Subjects" matched "Li"
#      and "CFTC Appendix" matched "C". Fixed by making roman numerals case-sensitive
#      and requiring a separator after them.
#   2. Bare numbers matched anywhere, so "310-TELEMARKETING" yielded "310" and
#      "54 PART 54" yielded "54". Fixed by requiring a single number to be followed by
#      "." or ")" — which "3. Information Security" satisfies and "2026 Annual Report"
#      does not.
_REF_PATTERN = re.compile(
    r"^\s*(?:"
    r"(?:§+\s*)?(\d+(?:\.\d+)+)"           # 3.2.1 — multi-part, distinctive on its own
    r"|(?:§+\s*)?(\d+)(?=[.)])"              # 3.  or 3)  — bare number needs a separator
    r"|([IVXLCDM]+(?:\.[A-Z0-9]+)*)(?=[.)\s])"  # II.A — uppercase only, separator required
    r"|\(([a-z]{1,3}|[ivxlcdm]{1,4}|\d{1,3})\)"  # (a) (iv) (12)
    r"|(?i:Article|Section|Sec\.|Part|Rule)\s+([\w.\-]+)"
    r")[\s.\-\u2014:)]*",
)

@dataclass
class Section:
    """One addressable piece of a document."""

    body: str
    level: int
    ordinal: int
    ref: str | None = None
    heading: str | None = None
    parent_ordinal: int | None = None
    extracted_from: str = "unknown"

    @property
    def label(self) -> str:
        """Display label. Avoids repeating the ref when the heading already opens with it,
        which produced output like "I I. Discussion"."""
        if self.heading and self.ref and self.heading.lstrip().startswith(self.ref):
            return self.heading
        parts = [p for p in (self.ref, self.heading) if p]
        return " ".join(parts) or f"[section {self.ordinal}]"


def extract_ref(heading: str | None) -> str | None:
    """Pull the document's own label out of a heading, if it has one.

    Kept separate and pure so it can be tested against real heading strings without
    parsing a document. Most hierarchy bugs are really ref-detection bugs.
    """
    if not heading:
        return None
    match = _REF_PATTERN.match(heading)
    if not match:
        return None
    return next((g for g in match.groups() if g), None)


def _assign_parents(sections: list[Section]) -> list[Section]:
    """Link each section to the nearest preceding section one level shallower.

    A stack, not a search: walking backwards for a shallower level would be quadratic and
    would also get it wrong when levels skip (a document going straight from level 1 to
    level 3, which real documents do).
    """
    stack: list[Section] = []
    for sec in sections:
        while stack and stack[-1].level >= sec.level:
            stack.pop()
        sec.parent_ordinal = stack[-1].ordinal if stack else None
        stack.append(sec)
    return sections


def _finalise(chunks: list[tuple[str | None, int, list[str]]], origin: str) -> list[Section]:
    """Turn (heading, level, paragraphs) triples into numbered, parented Sections."""
    sections: list[Section] = []
    for heading, level, paragraphs in chunks:
        body = "\n\n".join(p.strip() for p in paragraphs if p.strip()).strip()
        if not body and not heading:
            continue  # nothing addressable here
        sections.append(
            Section(
                body=body,
                level=level,
                ordinal=len(sections),
                ref=extract_ref(heading),
                heading=heading.strip() if heading else None,
                extracted_from=origin,
            )
        )
    return _assign_parents(sections)


# --- 1. XML: structure is stated -------------------------------------------
#
# Day 33, corrected after running scripts/inspect_xml.py against a real document.
#
# The first version assumed SOURCE="HD1"/"HD2" encoded depth. It does not. In this
# dialect 10 of 11 headings carry SOURCE="HED", which means "this is a heading" and says
# nothing about level. Every section therefore landed at level 0 and the hierarchy was
# silently flat — the parser reported success the whole time.
#
# Depth is actually carried by ELEMENT POSITION: RULE contains PREAMB and SUPLINF, which
# contain PART, REGTEXT and SECTION, which contain AMDPAR. So level is computed from how
# many structural containers a heading sits inside, with SOURCE="HDn" adding a sub-level
# within its container when present.
#
# The general lesson: some XML dialects state depth in attributes, others in nesting.
# Assuming the first and getting the second produces a flat tree and no error at all.

# Elements that establish a level of containment. Anything else is content.
_CONTAINERS = {
    "RULE", "PRORULE", "NOTICE", "PRESDOCU",
    "PREAMB", "SUPLINF", "REGTEXT", "PART", "SUBPART", "SECTION",
    "APPENDIX", "EXTRACT", "SUBJGRP",
}

# Text-bearing elements. Deliberately excludes containers: taking text from both a
# container and its children would store every paragraph twice.
_CONTENT = {"P", "FP", "NOTE", "LI", "AMDPAR", "FTNT", "EXTRACT"}

_HD_LEVEL = re.compile(r"HD(\d+)", re.I)


def _localname(el) -> str:
    from lxml import etree

    if not isinstance(el.tag, str):
        return ""
    return etree.QName(el).localname.upper()


def _container_depth(el) -> int:
    """How many structural containers enclose this element."""
    return sum(1 for a in el.iterancestors() if _localname(a) in _CONTAINERS)


def from_xml(xml_bytes: bytes) -> list[Section]:
    """Parse regulatory XML, taking depth from nesting and from HDn when present."""
    from lxml import etree

    root = etree.fromstring(xml_bytes, etree.XMLParser(recover=True, huge_tree=True))

    chunks: list[tuple[str | None, int, list[str]]] = []
    heading: str | None = None
    level = 0
    paragraphs: list[str] = []
    pending_sectno: str | None = None

    def flush() -> None:
        nonlocal heading, paragraphs
        if heading is not None or paragraphs:
            chunks.append((heading, level, paragraphs))
        heading, paragraphs = None, []

    for el in root.iter():
        tag = _localname(el)
        if not tag:
            continue

        if tag == "HD":
            text = " ".join("".join(el.itertext()).split())
            if not text:
                continue
            flush()
            source = el.get("SOURCE", "HED")
            match = _HD_LEVEL.search(source)
            # SOURCE="HED" is the container's own title, so it sits AT the container's
            # depth. SOURCE="HDn" is a subsection, so it sits n levels below.
            level = _container_depth(el) + (int(match.group(1)) if match else 0)
            heading = text

        elif tag == "SECTNO":
            # "§ 310.8" — the section number. Its title arrives next as SUBJECT.
            pending_sectno = " ".join("".join(el.itertext()).split())

        elif tag == "SUBJECT":
            text = " ".join("".join(el.itertext()).split())
            if not text:
                continue
            flush()
            level = _container_depth(el)
            # Join "§ 310.8" with "[Amended]" so the ref and its title stay together.
            heading = f"{pending_sectno} {text}".strip() if pending_sectno else text
            pending_sectno = None

        elif tag in _CONTENT:
            text = " ".join("".join(el.itertext()).split())
            if text:
                paragraphs.append(text)

    flush()

    sections = _finalise(chunks, "xml")

    # Normalise so the shallowest heading is level 0. Absolute container depth is an
    # artefact of the dialect; only relative depth means anything downstream.
    if sections:
        floor = min(s.level for s in sections)
        if floor:
            for sec in sections:
                sec.level -= floor
            _assign_parents(sections)
    return sections


# --- 2. HTML: structure is implied -----------------------------------------

def from_html(html: str) -> list[Section]:
    """Parse HTML, using h1..h6 as the hierarchy.

    BeautifulSoup rather than lxml directly because published HTML is frequently invalid,
    and a parser that refuses malformed input refuses most of the real web.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for junk in soup(["script", "style", "nav", "footer", "header", "aside"]):
        junk.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    chunks: list[tuple[str | None, int, list[str]]] = []
    heading: str | None = None
    level = 0
    paragraphs: list[str] = []

    for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name[0] == "h" and el.name[1:].isdigit():
            if heading is not None or paragraphs:
                chunks.append((heading, level, paragraphs))
            level = int(el.name[1]) - 1
            heading, paragraphs = text, []
        else:
            paragraphs.append(text)

    if heading is not None or paragraphs:
        chunks.append((heading, level, paragraphs))
    return _finalise(chunks, "html")


# --- 3. PDF: structure must be inferred ------------------------------------

def from_pdf(pdf_bytes: bytes, heading_ratio: float = 1.15) -> list[Section]:
    """Extract sections from a PDF by inferring headings from font size.

    This is genuinely lossy and it is why the other two paths are preferred. The method:
    find the most common font size (that is body text), then treat any short line set
    noticeably larger, or bold, as a heading. Level comes from ranking the distinct
    heading sizes largest-first.

    It works on well-typeset documents — legislation, corporate policy — and fails on
    tables, multi-column layouts and scanned pages. Anything it gets wrong should be
    recorded in docs/FAILURES.md rather than quietly accepted.
    """
    import fitz  # pymupdf

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    lines: list[tuple[str, float, bool]] = []  # (text, size, is_bold)

    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                size = max((s["size"] for s in spans), default=0.0)
                bold = any("bold" in s.get("font", "").lower() for s in spans)
                lines.append((text, round(size, 1), bold))
    doc.close()

    if not lines:
        return []

    # The most common size is body text, by definition — there is more of it.
    sizes: dict[float, int] = {}
    for _, size, _ in lines:
        sizes[size] = sizes.get(size, 0) + 1
    body_size = max(sizes, key=lambda k: sizes[k])

    def is_heading(text: str, size: float, bold: bool) -> bool:
        if len(text) > 120:
            return False  # a long line is a paragraph, whatever it is set in
        return size >= body_size * heading_ratio or (bold and size >= body_size)

    heading_sizes = sorted({s for t, s, b in lines if is_heading(t, s, b)}, reverse=True)
    level_of = {size: i for i, size in enumerate(heading_sizes)}

    chunks: list[tuple[str | None, int, list[str]]] = []
    heading: str | None = None
    level = 0
    paragraphs: list[str] = []

    for text, size, bold in lines:
        if is_heading(text, size, bold):
            if heading is not None or paragraphs:
                chunks.append((heading, level, paragraphs))
            level = level_of.get(size, 0)
            heading, paragraphs = text, []
        else:
            paragraphs.append(text)

    if heading is not None or paragraphs:
        chunks.append((heading, level, paragraphs))
    return _finalise(chunks, "pdf")


def render_tree(sections: list[Section], max_body: int = 70) -> str:
    """Print the hierarchy so a human can compare it against the real document.

    Day 33 has no automated success criterion — correctness here means "matches what the
    document actually looks like", which only eyes can confirm. So make it easy to look.
    """
    out = []
    for sec in sections:
        indent = "  " * sec.level
        snippet = " ".join(sec.body.split())[:max_body]
        out.append(f"{indent}{sec.ordinal:>3} L{sec.level} {sec.label[:60]}")
        if snippet:
            out.append(f"{indent}      {snippet}{'...' if len(sec.body) > max_body else ''}")
    return "\n".join(out)
