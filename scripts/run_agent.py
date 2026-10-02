"""Run the agent on obligations and show what it decided.

    python scripts/run_agent.py --case B01              # one golden-set case
    python scripts/run_agent.py --obligation "text..."  # ad hoc
    python scripts/run_agent.py --set --limit 12        # whole merged set, capped

Watch BOTH paths. The answerable cases (B*) should draft amendments. The unanswerable ones
(G*) should abstain before the model is ever called — those cost nothing and are the point
of the system.

Quota: one model call per obligation that passes the retrieval gate. Abstentions are free.
`--limit` caps model calls so a full run cannot exhaust a 20-a-day free tier.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.agent import CONFIDENCE_THRESHOLD, RETRIEVAL_THRESHOLD, Route, assess  # noqa: E402
from redline.embeddings import embed_query  # noqa: E402
from redline.resilience import CallBudget, ModelUnavailable  # noqa: E402
from redline.retrieval import retrieve  # noqa: E402
from redline.store import (  # noqa: E402
    agent_stats, connect, index_is_ready, init_db, save_agent_run,
)

GOLDEN = Path("evals/golden-merged.jsonl")


def wrap(text: str, width: int = 74, indent: str = "      ") -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(indent + line); line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(indent + line)
    return "\n".join(lines)


def show_diff(original: str, proposed: str) -> None:
    """Word-level diff, so the amendment is visible rather than described.

    A reviewer approving a policy change needs to see exactly which words move. Printing
    the new clause alone hides the edit inside a paragraph of unchanged text.
    """
    a, b = original.split(), proposed.split()
    out, buf, kind = [], [], " "
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            out.append(" ".join(a[i1:i2]))
        elif tag in ("delete", "replace"):
            out.append(f"[-{' '.join(a[i1:i2])}-]")
            if tag == "replace":
                out.append(f"[+{' '.join(b[j1:j2])}+]")
        elif tag == "insert":
            out.append(f"[+{' '.join(b[j1:j2])}+]")
    print(wrap(" ".join(out), indent="        "))


def render(result, case_id: str = "") -> None:
    head = f"{case_id}  " if case_id else ""
    print("=" * 80)
    print(f"{head}OBLIGATION")
    print(wrap(result.obligation, indent="    "))
    print()

    if result.candidates:
        print(f"  RETRIEVAL  {len(result.candidates)} candidates · "
              f"top {result.top_score:.3f} ({result.candidates[0]['ref']}) · "
              f"margin {result.margin:.3f}")

    badge = "AUTO-FILED" if result.route == Route.AUTO else "ROUTED TO HUMAN"
    print(f"  DECISION   {badge}   confidence {result.confidence:.3f} "
          f"(threshold {CONFIDENCE_THRESHOLD})")
    print(f"  WHY        {result.reason}")
    print(f"  COST       {result.model_calls} model call(s)")

    if not result.analysis:
        print()
        return

    affected = result.analysis.affected
    other = [a for a in result.analysis.assessments if a not in affected]
    print(f"\n  {len(affected)} affected, {len(other)} not affected or ambiguous\n")

    for a in affected:
        print(f"    {a.clause_id}   AFFECTED · severity {a.severity.value}")
        print(wrap(a.reasoning))
        if a.proposed_text:
            original = next((c["body"] for c in result.candidates
                             if c["ref"] == a.clause_id), "")
            print("      PROPOSED AMENDMENT  ([-removed-] [+added+]):")
            show_diff(original, a.proposed_text)
        print()

    for a in other:
        print(f"    {a.clause_id}   {a.verdict.value.upper()}")
        print(wrap(a.reasoning))
    if result.analysis.ambiguity_note:
        print(f"\n    NOTE ON THE OBLIGATION ITSELF:")
        print(wrap(result.analysis.ambiguity_note))
    print()


def load_cases() -> list[dict]:
    if not GOLDEN.exists():
        return []
    rows = [json.loads(ln) for ln in GOLDEN.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if not all(k.startswith("_") for k in r)]


def main() -> int:
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 12
    mode = args[args.index("--mode") + 1] if "--mode" in args else "vector"

    targets: list[tuple[str, str]] = []
    if "--obligation" in args:
        targets = [("", args[args.index("--obligation") + 1])]
    elif "--case" in args:
        cid = args[args.index("--case") + 1].upper()
        case = next((c for c in load_cases() if c["id"].upper() == cid), None)
        if not case:
            print(f"No case {cid} in {GOLDEN}")
            return 2
        targets = [(case["id"], case["query"])]
    elif "--set" in args:
        targets = [(c["id"], c["query"]) for c in load_cases()]
    else:
        print(__doc__)
        return 2

    init_db()
    budget = CallBudget(limit=limit)
    auto = routed = free = 0

    with connect() as conn:
        ready, reason = index_is_ready(conn)
        if not ready:
            print(reason)
            return 2

        for case_id, obligation in targets:
            try:
                vector = embed_query(obligation)
                result = assess(conn, obligation, vector, retrieve_fn=retrieve,
                                budget=budget, mode=mode)
            except ModelUnavailable as e:
                print(f"\nSTOPPED at {case_id or 'obligation'}: {e}")
                print("Decisions made before this point are saved.")
                break

            render(result, case_id)
            save_agent_run(conn, result, mode)
            conn.commit()

            if result.route == Route.AUTO:
                auto += 1
            else:
                routed += 1
            if result.model_calls == 0:
                free += 1

        print("=" * 80)
        print(f"  auto-filed       : {auto}")
        print(f"  routed to human  : {routed}")
        print(f"  abstained free   : {free}  (retrieval gate, no model call)")
        print(f"  model calls used : {budget.used}/{budget.limit}")
        stats = agent_stats(conn)
        print(f"\n  all time: {stats['runs']} runs, {stats['auto_filed']} auto-filed, "
              f"{stats['routed']} routed, {stats['model_calls']} model calls, "
              f"mean confidence {stats['mean_confidence']}")
        print(f"\n  thresholds: retrieval {RETRIEVAL_THRESHOLD} (measured), "
              f"confidence {CONFIDENCE_THRESHOLD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
