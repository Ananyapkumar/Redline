"""Load the internal policy corpus from markdown into addressable clauses.

The corpus is stored as markdown rather than in the database or as JSON for one reason:
a policy library is a set of documents that people read. Keeping it readable means the
clause labels in an eval result can be checked against something a human can open, and it
means the corpus can be reviewed, corrected and extended without a migration.

Clause identity is the whole point. "P-002 §2.2" must mean exactly one clause, forever,
because that string appears in golden-set labels, in retrieval results, and in every
citation the agent produces. If clause IDs shift when the corpus is edited, every stored
label silently points at the wrong text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
_SECTION = re.compile(r"^##\s+(\d+)\.?\s+(.*)$")          # ## 2. Retention Periods
_CLAUSE = re.compile(r"^###\s+(\d+(?:\.\d+)*)\s+(.*)$")   # ### 2.2 Audit and access logs


@dataclass(frozen=True)
class Clause:
    """One addressable clause of one policy document."""

    policy_id: str          # P-002
    policy_title: str
    owner: str
    domain: str
    effective: date | None
    last_reviewed: date | None
    section_ref: str        # 2
    section_heading: str    # Retention Periods
    ref: str                # 2.2
    heading: str            # Audit and access logs
    text: str

    @property
    def clause_id(self) -> str:
        """The stable identifier used in labels, citations and eval results."""
        return f"{self.policy_id} §{self.ref}"

    @property
    def retrieval_text(self) -> str:
        """What actually gets embedded.

        Includes the policy title and section heading, not just the clause body. A clause
        reading "Records shall be retained for six years" is nearly meaningless on its own;
        the same text prefixed with "Data Retention and Disposal Policy — Retention
        Periods — Audit and access logs" is findable. Embedding a clause stripped of its
        context is one of the most common reasons RAG systems retrieve badly.
        """
        return (
            f"{self.policy_title} — {self.section_heading} — {self.heading}\n"
            f"{self.text}"
        )

    def is_stale(self, as_of: date, months: int = 24) -> bool:
        """Has this policy gone past its own review cadence?

        P-001 §2.3 requires review at least every 24 months. A corpus that violates its
        own policy is realistic — real libraries do — and those clauses make good hard
        cases for the golden set.
        """
        if not self.last_reviewed:
            return True
        elapsed = (as_of.year - self.last_reviewed.year) * 12 + (
            as_of.month - self.last_reviewed.month
        )
        return elapsed > months


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_policy(text: str) -> list[Clause]:
    """Parse one policy document into its clauses."""
    match = _FRONT_MATTER.match(text)
    if not match:
        raise ValueError("Policy file has no front matter block")

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    for required in ("id", "title", "owner", "domain"):
        if required not in meta:
            raise ValueError(f"Policy front matter is missing '{required}'")

    body = text[match.end():]
    clauses: list[Clause] = []
    section_ref = section_heading = ""
    ref = heading = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if ref and buffer:
            clauses.append(
                Clause(
                    policy_id=meta["id"],
                    policy_title=meta["title"],
                    owner=meta["owner"],
                    domain=meta["domain"],
                    effective=_parse_date(meta.get("effective")),
                    last_reviewed=_parse_date(meta.get("last_reviewed")),
                    section_ref=section_ref,
                    section_heading=section_heading,
                    ref=ref,
                    heading=heading,
                    text=" ".join(" ".join(buffer).split()),
                )
            )
        buffer = []

    for line in body.splitlines():
        sec = _SECTION.match(line)
        if sec:
            flush()
            ref = heading = ""
            section_ref, section_heading = sec.group(1), sec.group(2).strip()
            continue
        cls = _CLAUSE.match(line)
        if cls:
            flush()
            ref, heading = cls.group(1), cls.group(2).strip()
            continue
        if line.strip():
            buffer.append(line.strip())

    flush()
    return clauses


def load_corpus(directory: Path) -> list[Clause]:
    """Load every policy in a directory, sorted by policy id then clause ref.

    Raises on a duplicate clause_id rather than silently keeping one: two clauses sharing
    an identifier would make every label referring to it ambiguous, and the failure would
    only surface as inexplicable eval results.
    """
    clauses: list[Clause] = []
    for path in sorted(directory.glob("P-*.md")):
        try:
            clauses.extend(parse_policy(path.read_text(encoding="utf-8")))
        except ValueError as e:
            raise ValueError(f"{path.name}: {e}") from e

    seen: dict[str, str] = {}
    for c in clauses:
        if c.clause_id in seen:
            raise ValueError(
                f"Duplicate clause id {c.clause_id!r} in {c.policy_title!r}. "
                "Clause ids must be unique across the whole corpus."
            )
        seen[c.clause_id] = c.policy_title

    return sorted(clauses, key=lambda c: (c.policy_id, [int(p) for p in c.ref.split(".")]))
