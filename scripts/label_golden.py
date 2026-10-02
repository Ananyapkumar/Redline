"""Label the drafted obligations by hand, then write the golden set.

    python scripts/label_golden.py            # label unlabelled drafts
    python scripts/label_golden.py --export   # write evals/golden.jsonl from labels

For each obligation you see the regulator's own words and the ten clauses retrieval
proposed, with full text. You choose which are genuinely relevant.

  numbers  the candidates that apply, e.g.  1 4 7
  n        none of them apply — the corpus has no clause for this obligation
  s        skip, decide later
  q        stop and save

"Relevant" means: a clause a compliance analyst reviewing this obligation would want in
front of them. NOT "a clause that must be textually amended" — deciding that is impact
classification, scored separately from Day 44. Merging the two into one label makes a low
score impossible to attribute to either half.

Answering 'n' matters as much as any other answer. Day 36 had one unanswerable case, which
made the abstention threshold a line fitted to a single point. Several are needed before
that number means anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DRAFT = Path("evals/golden-draft.jsonl")
GOLDEN = Path("evals/golden.jsonl")

META = {
    "_definition": (
        "'relevant' means: a clause a compliance analyst reviewing this obligation would "
        "want in front of them. NOT 'a clause that must be textually amended' — impact "
        "classification is scored separately from Day 44."
    ),
    "_provenance": (
        "Queries are obligations extracted by scripts/build_golden.py from Federal "
        "Register documents stored by the watcher, phrased in the regulator's own words. "
        "Labels were chosen by a human reading each candidate clause in full. This "
        "replaces evals/baseline.jsonl, whose queries shared an author with the corpus "
        "and therefore overstated recall."
    ),
}


def wrap(text: str, width: int = 66, indent: str = "         ") -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(indent + line); line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(indent + line)
    return "\n".join(lines)


def load() -> list[dict]:
    if not DRAFT.exists():
        print(f"No draft at {DRAFT}. Run scripts/build_golden.py first.")
        sys.exit(2)
    return [json.loads(ln) for ln in DRAFT.read_text(encoding="utf-8").splitlines() if ln.strip()]


def export(rows: list[dict]) -> int:
    """Write the labelled cases out in the shape the eval runner reads.

    Candidates are dropped deliberately. Keeping them would freeze one retrieval
    configuration into the ground truth, and every later run would be graded against what
    today's retriever happened to surface rather than against what is actually correct.
    """
    labelled = [r for r in rows if r.get("relevant") is not None and r.get("verified")]
    if not labelled:
        print("Nothing labelled yet.")
        return 1

    lines = [json.dumps(META, ensure_ascii=False)]
    for r in labelled:
        lines.append(json.dumps({
            "id": r["id"],
            "query": r["query"],
            "relevant": r["relevant"],
            "note": (f"{r['modality']} obligation on {r['obliged_party']}, "
                     f"{r['source_ref']} of {r['source_document']}"),
            "verified": True,
        }, ensure_ascii=False))

    GOLDEN.write_text("\n".join(lines) + "\n", encoding="utf-8")
    answerable = sum(1 for r in labelled if r["relevant"])
    print(f"  {len(labelled)} cases -> {GOLDEN}")
    print(f"    {answerable} answerable, {len(labelled) - answerable} with no applicable clause")
    if len(labelled) - answerable < 3:
        print("\n  WARNING: fewer than 3 unanswerable cases. The abstention threshold on")
        print("  Day 46 will be fitted to too few points to mean anything.")
    return 0


def main() -> int:
    rows = load()
    if "--export" in sys.argv:
        return export(rows)

    todo = [r for r in rows if r.get("relevant") is None or not r.get("verified")]
    print(f"{len(rows)} drafted, {len(todo)} to label.\n")

    for r in rows:
        if r.get("relevant") is not None and r.get("verified"):
            continue
        print("=" * 76)
        print(f"{r['id']}   {r['source_ref']}   [{r['source_document']}]")
        print("=" * 76)
        print(f"\n  OBLIGATION  ({r['modality']}, on {r['obliged_party']})")
        print(f"    {r['action']}")
        print("\n  AS THE REGULATOR WROTE IT:")
        print(wrap(r["verbatim_quote"], indent="    "))
        print(f"\n  CANDIDATE CLAUSES (retrieval's top {len(r['candidates'])}):")
        for i, c in enumerate(r["candidates"], 1):
            print(f"\n    {i:>2}. {c['ref']}   sim {c['similarity']}")
            print(f"        {c['context'][:70]}")
            print(wrap(c["body"]))

        answer = input("\n  Which apply? [numbers / n=none / s=skip / q=quit] ").strip().lower()
        if answer == "q":
            break
        if answer == "s":
            print("  skipped\n"); continue
        if answer == "n":
            r["relevant"] = []
            r["verified"] = True
            print("  -> no applicable clause (abstention case)\n")
            continue
        try:
            picks = [int(x) for x in answer.replace(",", " ").split()]
            refs = [r["candidates"][i - 1]["ref"] for i in picks
                    if 1 <= i <= len(r["candidates"])]
        except ValueError:
            print("  not understood, skipping\n"); continue
        if not refs:
            print("  nothing selected, skipping\n"); continue
        r["relevant"] = refs
        r["verified"] = True
        print(f"  -> {', '.join(refs)}\n")

    DRAFT.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                     encoding="utf-8")
    done = sum(1 for r in rows if r.get("verified"))
    print(f"\n{done}/{len(rows)} labelled. Saved to {DRAFT}.")
    print("Run with --export to write evals/golden.jsonl when you are finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
