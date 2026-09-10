"""Walk the policy corpus one clause at a time, so you actually read it.

    python scripts/review_corpus.py              # read every clause
    python scripts/review_corpus.py P-002        # one policy
    python scripts/review_corpus.py --defects    # only the automated checks
    python scripts/review_corpus.py --stats      # shape of the corpus

Day 34 is not finished when the files exist. It is finished when you have read every
clause, because on Day 37 you will hand-label a golden set against this corpus, and a
golden set built on documents you have not read is worthless — you would be encoding
guesses as ground truth and then measuring the system against them.

The corpus contains deliberately seeded defects: contradictions between policies, a policy
past its own review cadence, and gaps where no clause covers an obligation. They are here
because real policy libraries contain exactly these, and because the hard cases in an eval
set have to come from somewhere. Find them yourself before opening docs/corpus-defects.md.
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.policies import Clause, load_corpus  # noqa: E402

CORPUS = Path("data/policies")

# Durations expressed in the documents' own words, so contradictions can be compared.
_DURATION = re.compile(
    r"(?:not less than|at least|minimum of|no later than|within|for)\s+"
    r"(?:a period of\s+)?"
    r"(\w+)\s*(?:\(\d+\)\s*)?(year|month|day|business day|hour)s?",
    re.I,
)
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "sixty": 60, "ninety": 90,
}
_TO_DAYS = {"year": 365, "month": 30, "day": 1, "business day": 1.4, "hour": 1 / 24}


def _durations(text: str) -> list[tuple[float, str]]:
    found = []
    for raw, unit in _DURATION.findall(text):
        raw = raw.lower()
        n = _WORD_NUMBERS.get(raw, int(raw) if raw.isdigit() else None)
        if n is not None:
            found.append((n * _TO_DAYS[unit.lower()], f"{raw} {unit}s"))
    return found


def check_defects(clauses: list[Clause]) -> None:
    """Automated checks. These catch some defects, not all — the rest need your eyes."""
    today = date(2026, 9, 10)
    print("=" * 74)
    print("AUTOMATED CHECKS")
    print("=" * 74)

    stale = sorted({(c.policy_id, c.policy_title, c.last_reviewed) for c in clauses
                    if c.is_stale(today)})
    print(f"\n1. Policies past the 24-month review cadence required by P-001 §2.3: {len(stale)}")
    for pid, title, reviewed in stale:
        print(f"     {pid}  last reviewed {reviewed}  {title}")
    if not stale:
        print("     none")

    # Group clauses that talk about the same subject and state different durations.
    topics = defaultdict(list)
    for c in clauses:
        for days, phrase in _durations(c.text):
            for topic in ("audit log", "log", "retain", "retention", "notif", "review"):
                if topic in (c.text + " " + c.heading).lower():
                    topics[topic].append((days, phrase, c.clause_id, c.heading))
                    break

    print("\n2. Clauses stating a retention period for logs:")
    log_clauses = [t for t in topics.get("audit log", []) + topics.get("log", [])
                   if "retain" in t[3].lower() or "retention" in t[3].lower()
                   or "log" in t[3].lower()]
    for days, phrase, cid, heading in sorted(set(log_clauses)):
        print(f"     {cid:<12} {phrase:<16} {heading}")
    print("     ^ read these together. Do they agree?")

    deferrals = [c for c in clauses
                 if re.search(r"governed by|as set out in|in accordance with the applicable",
                              c.text, re.I)]
    print(f"\n3. Clauses that defer to a document outside this corpus: {len(deferrals)}")
    for c in deferrals:
        print(f"     {c.clause_id:<12} {c.heading}")
    print("     ^ these are where the agent cannot resolve an obligation. They should")
    print("       become abstention cases, not confident answers.")
    print()


def show_stats(clauses: list[Clause]) -> None:
    print("=" * 74)
    print("CORPUS SHAPE")
    print("=" * 74)
    by_policy = Counter(c.policy_id for c in clauses)
    by_domain = Counter(c.domain for c in clauses)
    lengths = [len(c.text) for c in clauses]

    print(f"\n  policies : {len(by_policy)}")
    print(f"  clauses  : {len(clauses)}")
    print(f"  chars    : min {min(lengths)}, median {sorted(lengths)[len(lengths)//2]}, "
          f"max {max(lengths)}")
    print("\n  by domain:")
    for domain, n in by_domain.most_common():
        print(f"    {domain:<24} {n}")
    print("\n  by policy:")
    for pid, n in sorted(by_policy.items()):
        title = next(c.policy_title for c in clauses if c.policy_id == pid)
        print(f"    {pid}  {n:>3} clauses  {title}")
    print()


def walk(clauses: list[Clause]) -> None:
    current_policy = current_section = None
    for i, c in enumerate(clauses, 1):
        if c.policy_id != current_policy:
            current_policy, current_section = c.policy_id, None
            print("\n" + "=" * 74)
            print(f"{c.policy_id}  {c.policy_title}")
            print(f"owner: {c.owner}   domain: {c.domain}   "
                  f"last reviewed: {c.last_reviewed}")
            print("=" * 74)
        if c.section_ref != current_section:
            current_section = c.section_ref
            print(f"\n  {c.section_ref}. {c.section_heading}")
        print(f"\n    {c.clause_id}  {c.heading}")
        for line in _wrap(c.text, 66):
            print(f"      {line}")
        print(f"\n      [{i}/{len(clauses)}]  Enter to continue, q to stop", end="")
        if input().strip().lower() == "q":
            print(f"\nStopped at clause {i} of {len(clauses)}.")
            return
    print(f"\nRead all {len(clauses)} clauses.")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


def main() -> int:
    if not CORPUS.exists():
        print(f"No corpus at {CORPUS}")
        return 2
    clauses = load_corpus(CORPUS)
    args = sys.argv[1:]

    if "--stats" in args:
        show_stats(clauses)
        return 0
    if "--defects" in args:
        check_defects(clauses)
        return 0

    policy = next((a for a in args if a.upper().startswith("P-")), None)
    if policy:
        clauses = [c for c in clauses if c.policy_id.upper() == policy.upper()]
        if not clauses:
            print(f"No policy {policy}")
            return 2

    show_stats(clauses)
    walk(clauses)
    check_defects(clauses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
