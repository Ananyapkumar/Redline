"""Confirm every golden-set label against the actual corpus text.

    python scripts/verify_golden.py

Shows each case with the FULL TEXT of every clause labelled correct, and asks whether the
label is right. Confirmations are written back to the file.

Why this exists: a golden set is ground truth. Every eval number you publish is measured
against it, so a wrong label does not produce a wrong score — it produces a confident score
of the wrong thing, and nothing ever reveals the error.

These labels were drafted by Claude, which wrote the corpus and therefore knows it. That
makes them probably right and definitely unverified. Twenty minutes here is what turns
"an AI told me these are the answers" into "I checked."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.policies import load_corpus  # noqa: E402

GOLDEN = Path("evals/baseline.jsonl")
POLICIES = Path("data/policies")


def wrap(text: str, width: int = 68, indent: str = "        ") -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(indent + line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(indent + line)
    return "\n".join(lines)


def main() -> int:
    clauses = {c.clause_id: c for c in load_corpus(POLICIES)}
    all_rows = [json.loads(ln) for ln in GOLDEN.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    # Metadata rows (all keys prefixed "_") are documentation, not cases. They must survive
    # the rewrite at the end — dropping them would quietly delete the caveats the file
    # exists to carry.
    meta = [r for r in all_rows if all(k.startswith("_") for k in r)]
    rows = [r for r in all_rows if r not in meta]

    print(f"{len(rows)} cases. Enter = correct, 'n' = wrong, 'q' = stop and save.\n")
    changed = 0

    for i, row in enumerate(rows, 1):
        if row.get("verified"):
            continue
        print("=" * 76)
        print(f"{row['id']}   [{i}/{len(rows)}]")
        print("=" * 76)
        print("\n  REGULATORY OBLIGATION:")
        print(wrap(row["query"], indent="    "))
        print(f"\n  WHY THIS CASE EXISTS:\n{wrap(row['note'], indent='    ')}")

        if not row["relevant"]:
            print("\n  LABELLED: no clause in the corpus addresses this.")
            print("  Check that is true — if some clause does cover it, this case is wrong")
            print("  and the abstention measurement built on it would be meaningless.")
        else:
            print(f"\n  LABELLED AS AFFECTED ({len(row['relevant'])}):")
            for ref in row["relevant"]:
                c = clauses.get(ref)
                if not c:
                    print(f"\n    {ref}  *** NO SUCH CLAUSE — label is broken ***")
                    continue
                print(f"\n    {ref}  {c.policy_title}")
                print(f"    {c.section_ref}. {c.section_heading} / {c.heading}")
                print(wrap(c.text))

        answer = input("\n  Correct? [Enter/n/q] ").strip().lower()
        if answer == "q":
            break
        if answer == "n":
            print("  Marked wrong. Note what should change, then edit evals/baseline.jsonl.")
            row["verified"] = False
            row["disputed"] = True
        else:
            row["verified"] = True
            row.pop("disputed", None)
            changed += 1
        print()

    GOLDEN.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in meta + rows) + "\n",
        encoding="utf-8",
    )
    verified = sum(1 for r in rows if r.get("verified"))
    print(f"\n{verified}/{len(rows)} cases verified. Saved to {GOLDEN}.")
    if verified < len(rows):
        print("Re-run to continue; verified cases are skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
