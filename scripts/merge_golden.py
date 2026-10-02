"""Combine the two golden sets into one that can measure both halves.

    python scripts/merge_golden.py

Why this exists. Two sets were built, each broken in the opposite direction:

  evals/baseline.jsonl   9 answerable, 1 unanswerable.  Queries written by Claude, who also
                         wrote the policy corpus — so they share vocabulary and several are
                         near-verbatim matches. Recall measured on it is inflated.

  evals/golden.jsonl    17 unanswerable, 0 answerable.  Obligations extracted from real
                         Federal Register rules, so uncontaminated — but every one of them
                         turned out to concern explosives storage, pesticide tolerances or
                         aircraft airworthiness, which a health-tech policy library has
                         nothing to say about. Recall is undefined on it.

Separation — the gap between how confident the system sounds with an answer and without one
— needs both kinds in one set. It is the number that decides the abstention threshold on
Day 46, and neither set alone can produce it.

The merged set is honest about its two halves rather than pretending to be one clean set:
every case keeps a `provenance` field, and the metrics file records the caveat. An
interviewer reading this should be able to see exactly which number came from where.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASELINE = Path("evals/baseline.jsonl")
GOLDEN = Path("evals/golden.jsonl")
MERGED = Path("evals/golden-merged.jsonl")

META = {
    "_definition": (
        "'relevant' means: a clause a compliance analyst reviewing this obligation would "
        "want in front of them. NOT 'a clause that must be amended' — impact classification "
        "is scored separately."
    ),
    "_provenance": (
        "TWO HALVES, DELIBERATELY. Cases B* are answerable; their queries were written by "
        "Claude, who also wrote the policy corpus, so they share vocabulary and recall "
        "measured on them is inflated — treat recall as an upper bound, not a result. "
        "Cases G* are unanswerable; their queries are obligations extracted by the project's "
        "own extractor from real Federal Register rules, so they are uncontaminated. "
        "Separation is the metric this merged set exists to produce, and it is the honest "
        "one: the unanswerable half is real regulatory text."
    ),
    "_limitation": (
        "No uncontaminated ANSWERABLE cases yet. Producing them needs regulatory documents "
        "whose subject matter actually overlaps a health-tech policy library — recordkeeping, "
        "PHI, access control, breach notification. The watcher now searches for those terms; "
        "the next mining run should yield some."
    ),
}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if not all(k.startswith("_") for k in r)]


def main() -> int:
    answerable = load(BASELINE)
    unanswerable = load(GOLDEN)

    if not answerable and not unanswerable:
        print("Neither golden set exists. Run build_golden.py / label_golden.py first.")
        return 2

    cases = []
    for r in answerable:
        cases.append({**r, "provenance": "claude-written (contaminated)"})
    for r in unanswerable:
        cases.append({**r, "provenance": "extracted from real regulation"})

    lines = [json.dumps(META, ensure_ascii=False)]
    lines += [json.dumps(c, ensure_ascii=False) for c in cases]
    MERGED.write_text("\n".join(lines) + "\n", encoding="utf-8")

    n_ans = sum(1 for c in cases if c["relevant"])
    print(f"  {len(cases)} cases -> {MERGED}")
    print(f"    {n_ans} answerable   ({sum(1 for c in cases if c['relevant'] and 'contaminated' in c['provenance'])} contaminated)")
    print(f"    {len(cases) - n_ans} unanswerable ({sum(1 for c in cases if not c['relevant'] and 'real' in c['provenance'])} from real regulation)")
    print()
    print("  Now:  python scripts/run_evals.py --set evals/golden-merged.jsonl --mode vector")
    print("        python scripts/run_evals.py --set evals/golden-merged.jsonl --mode hybrid")
    print("        python scripts/run_evals.py --set evals/golden-merged.jsonl --mode rerank")
    print("        python scripts/run_evals.py --compare")
    print()
    print("  Read the SEPARATION column, not recall. Recall on this set is an upper bound")
    print("  because its answerable half is contaminated. Separation is the honest number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
