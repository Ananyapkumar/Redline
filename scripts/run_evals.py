"""Score retrieval against the golden set and save the run.

    python scripts/run_evals.py                      # score, print, save
    python scripts/run_evals.py --label "hybrid"     # name the run for comparison
    python scripts/run_evals.py --compare            # show every saved run as a table

Costs one embedding call per case. Ten cases, ten calls.

DAY 36 RULE: run this, read it, write the number down, and change nothing. Every
improvement you can see from here is worth more once there is a "before" to compare it to.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.config import settings  # noqa: E402
from redline.embeddings import EMBED_DIM, embed_query  # noqa: E402
from redline.evals import CaseResult, load_cases, score, write_run  # noqa: E402
from redline.resilience import CallBudget, ModelUnavailable  # noqa: E402
from redline.store import connect, index_is_ready, search_chunks  # noqa: E402

GOLDEN = Path("evals/baseline.jsonl")
RESULTS = Path("evals/results")


def compare() -> int:
    runs = sorted(RESULTS.glob("*.json"))
    if not runs:
        print("No saved runs yet.")
        return 1
    print(f"{'run':<28} {'r@1':>6} {'r@5':>6} {'r@10':>6} {'h@10':>6} {'mrr':>6} {'sep':>7}")
    print("-" * 72)
    for path in runs:
        d = json.loads(path.read_text())
        s = d["summary"]
        print(f"{d['label'][:27]:<28} "
              f"{s.get('recall@1', 0):>6.3f} {s.get('recall@5', 0):>6.3f} "
              f"{s.get('recall@10', 0):>6.3f} {s.get('hit@10', 0):>6.3f} "
              f"{s.get('mrr', 0):>6.3f} {s.get('score_separation', 0):>7.3f}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--compare" in args:
        return compare()

    label = "baseline-vector-only"
    if "--label" in args:
        label = args[args.index("--label") + 1]

    cases = load_cases(GOLDEN)
    unverified = [c.id for c in cases if not c.verified]

    with connect() as conn:
        ready, reason = index_is_ready(conn)
        if not ready:
            print(reason)
            return 2

        print(f"Scoring {len(cases)} cases against {reason}\n")
        if unverified:
            print(f"  WARNING: {len(unverified)} case(s) not yet verified by a human: "
                  f"{', '.join(unverified)}")
            print("  Run scripts/verify_golden.py. Unverified labels are assumptions,")
            print("  and a score measured against assumptions is not evidence.\n")

        budget = CallBudget(limit=len(cases) * 3)
        results: list[CaseResult] = []

        for case in cases:
            try:
                vector = embed_query(case.query, budget=budget)
            except ModelUnavailable as e:
                print(f"STOPPED at {case.id}: {e}")
                return 3
            rows = search_chunks(conn, vector, corpus="policy", limit=10)
            results.append(CaseResult(
                case=case,
                retrieved=[r["ref"] for r in rows],
                top_score=float(rows[0]["similarity"]) if rows else 0.0,
                scores=[float(r["similarity"]) for r in rows],
            ))

    # --- per case -----------------------------------------------------------
    print(f"{'case':<6} {'r@10':>5} {'rank':>5}  {'top':>5}  detail")
    print("-" * 74)
    for r in results:
        if r.case.is_unanswerable:
            print(f"{r.case.id:<6} {'n/a':>5} {'n/a':>5}  {r.top_score:>5.3f}  "
                  f"NO ANSWER EXISTS — returned {r.retrieved[0]} at {r.top_score:.3f}")
            continue
        rank = r.first_hit_rank or 0
        detail = f"found {len(set(r.retrieved[:10]) & set(r.case.relevant))}/{len(r.case.relevant)}"
        if r.missed:
            detail += f", missed {', '.join(r.missed)}"
        print(f"{r.case.id:<6} {r.recall_at(10):>5.2f} "
              f"{(rank or '-'):>5}  {r.top_score:>5.3f}  {detail}")

    summary = score(results)
    print("\n" + "=" * 74)
    print(f"SUMMARY — {label}")
    print("=" * 74)
    for k in (1, 3, 5, 10):
        print(f"  recall@{k:<3} {summary[f'recall@{k}']:.3f}      "
              f"hit@{k:<3} {summary[f'hit@{k}']:.3f}")
    print(f"  MRR       {summary['mrr']:.3f}")
    print()
    print(f"  mean top score, answerable   : {summary.get('mean_top_score_answerable')}")
    print(f"  mean top score, unanswerable : {summary.get('mean_top_score_unanswerable')}")
    sep = summary.get("score_separation")
    print(f"  separation                   : {sep}")
    if sep is not None and sep < 0.10:
        print("\n  ^ The system sounds almost as confident when it has no answer as when")
        print("    it has one. Similarity cannot be thresholded. This is the measured")
        print("    version of Day 35 observation O3, and it is why ADR-001 D5 derives")
        print("    confidence from several signals instead of asking for one.")

    out = RESULTS / f"{label}.json"
    write_run(out, label, {
        "embedding_model": settings.embedding_model,
        "dimensions": EMBED_DIM,
        "retrieval": "vector only, cosine",
        "corpus": "policy",
    }, results)
    print(f"\n  saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
