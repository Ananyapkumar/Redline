"""Split text into units small enough that one embedding means one thing.

Day 34 produced a number that makes this day's problem concrete:

    policy clauses        median   130 characters
    Federal Register      average 5,099 characters   (one document averaged 9,862)

Two orders of magnitude apart, both destined for the same index. So the two corpora get
two different treatments, and the reason is worth stating precisely.

An embedding is a fixed-length summary of whatever you hand it. Give it 130 characters
about log retention and the vector lands squarely on "log retention". Give it 9,862
characters covering background, economic analysis, four subtopics and a severability
clause, and the vector lands in the average of all of them — near everything, precisely
near nothing. The chunk still gets retrieved sometimes, which is worse than never, because
the failure looks like bad luck rather than bad design.

  Policy clauses      already the right size. The author chunked them for us by writing
                      numbered clauses. Splitting further would break clause identity,
                      and "P-002 §2.2" must address exactly one thing.

  Regulatory sections split at paragraph boundaries, never mid-sentence, carrying the
                      section's ref and heading into every piece so a fragment still
                      knows it belongs to "II.B, Prohibition on Medicaid Payment".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Target sizes in characters. Deliberately not tuned yet — these are a starting point to
# be moved on Day 39/40 based on what the eval scores say, not on what feels right.
TARGET_CHARS = 1200
MAX_CHARS = 2000
MIN_CHARS = 120     # below this a chunk is usually a heading fragment, not an idea

_SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-Z(])")


@dataclass(frozen=True)
class Chunk:
    """One unit of retrieval."""

    text: str            # what gets embedded, context included
    body: str            # the raw text without the context prefix
    context: str         # "Policy title — Section — Heading"
    ref: str | None      # the source's own label, if it has one
    part: int            # 0 for the whole thing, else the index of this piece
    of_parts: int        # how many pieces the source unit produced

    @property
    def is_split(self) -> bool:
        return self.of_parts > 1


def _paragraphs(text: str) -> list[str]:
    """Prefer blank-line boundaries; fall back to sentence boundaries for a wall of text.

    Splitting mid-sentence is the one thing never to do — a fragment beginning "shall be
    retained for a period of" has lost the subject it applies to and will embed as noise.
    """
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) > 1:
        return parts
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def split_text(
    text: str,
    context: str,
    ref: str | None = None,
    target: int = TARGET_CHARS,
    maximum: int = MAX_CHARS,
) -> list[Chunk]:
    """Split one unit of text into chunks, repeating the context in each.

    Repeating the context costs a few tokens per chunk and buys the thing that makes a
    fragment retrievable at all. A chunk that says only "(b) facilitating the post-market
    monitoring referred to in Article 72" matches nothing a human would search for.
    """
    text = text.strip()
    if not text:
        return []

    prefix = f"{context}\n" if context else ""

    if len(text) <= maximum:
        return [Chunk(text=prefix + text, body=text, context=context,
                      ref=ref, part=0, of_parts=1)]

    pieces: list[str] = []
    current = ""
    for para in _paragraphs(text):
        # A single paragraph longer than the maximum is split on sentences rather than
        # dropped or truncated. Truncating loses obligations silently.
        if len(para) > maximum:
            if current:
                pieces.append(current)
                current = ""
            sentences = [s.strip() for s in _SENTENCE_END.split(para) if s.strip()]
            buf = ""
            for sentence in sentences:
                if buf and len(buf) + len(sentence) + 1 > target:
                    pieces.append(buf)
                    buf = sentence
                else:
                    buf = f"{buf} {sentence}".strip()
            if buf:
                current = buf
            continue

        if current and len(current) + len(para) + 2 > target:
            pieces.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}".strip()

    if current:
        pieces.append(current)

    # A trailing scrap is appended to its predecessor rather than embedded alone.
    if len(pieces) > 1 and len(pieces[-1]) < MIN_CHARS:
        pieces[-2] = f"{pieces[-2]}\n\n{pieces[-1]}"
        pieces.pop()

    total = len(pieces)
    return [
        Chunk(text=prefix + piece, body=piece, context=context,
              ref=ref, part=i, of_parts=total)
        for i, piece in enumerate(pieces)
    ]


def chunk_clause(clause) -> list[Chunk]:
    """A policy clause is one chunk. Always.

    Clause identity is the contract: "P-002 §2.2" appears in golden-set labels, retrieval
    results and agent citations, and it must address exactly one retrievable unit. The
    corpus loader already guarantees no clause exceeds 600 characters.
    """
    context = f"{clause.policy_title} — {clause.section_heading} — {clause.heading}"
    return [Chunk(text=clause.retrieval_text, body=clause.text, context=context,
                  ref=clause.clause_id, part=0, of_parts=1)]


def chunk_section(section_row: dict, document_title: str) -> list[Chunk]:
    """A regulatory section is split if it is large, kept whole if it is not."""
    heading = section_row.get("heading") or ""
    ref = section_row.get("ref")
    label = " — ".join(p for p in (document_title[:90], ref, heading) if p)
    return split_text(section_row["body"], context=label, ref=ref)
