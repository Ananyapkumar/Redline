"""Score retrieval against the golden set and save the run.

    python scripts/run_evals.py                          # vector, on the golden set
    python scripts/run_evals.py --mode hybrid            # + keyword, fused by RRF
    python scripts/run_evals.py --mode rerank            # + MMR diversity
    python scripts/run_evals.py --set evals/baseline.jsonl   # the old contaminated set
    python scripts/run_evals.py --compare                # every saved run, side by side

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
from redline.retrieval import retrieve  # noqa: E402
from redline.store import connect, index_is_ready  # noqa: E402

# Default is the real-regulation set from Day 37. The Day 36 baseline is kept and can be
# selected with --set, but it is contaminated and its number is a floor, not a result.
GOLDEN = Path("evals/golden.jsonl")
FALLBACK = Path("evals/baseline.jsonl")
RESULTS = Path("evals/results")


def compare() -> int:
    runs = sorted(RESULTS.glob("*.json"))
    if not runs:
        print("No saved runs yet.")
        return 1
    print(f"{'run':<30} {'set':>10} {'r@1':>6} {'r@5':>6} {'r@10':>6} "
          f"{'h@10':>6} {'mrr':>6} {'sep':>7}")
    print("-" * 86)
    for path in runs:
        d = json.loads(path.read_text())
        s = d["summary"]
        which = d.get("config", {}).get("golden_set", "?")[:10]
        def c(key, width=6):
            v = s.get(key)
            return f"{v:>{width}.3f}" if isinstance(v, (int, float)) else f"{'n/a':>{width}}"
        print(f"{d['label'][:29]:<30} {which:>10} "
              f"{c('recall@1')} {c('recall@5')} {c('recall@10')} {c('hit@10')} "
              f"{c('mrr')} {c('score_separation', 7)}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--compare" in args:
        return compare()

    mode = args[args.index("--mode") + 1] if "--mode" in args else "vector"
    golden = Path(args[args.index("--set") + 1]) if "--set" in args else GOLDEN
    if not golden.exists():
        if golden == GOLDEN and FALLBACK.exists():
            print(f"{GOLDEN} not found — falling back to {FALLBACK}.")
            print("Build the real set first: scripts/build_golden.py, then label_golden.py\n")
            golden = FALLBACK
        else:
            print(f"No such golden set: {golden}")
            return 2

    label = args[args.index("--label") + 1] if "--label" in args \
        else f"{golden.stem}-{mode}"

    cases = load_cases(golden)
    unverified = [c.id for c in cases if not c.verified]

    with connect() as conn:
        ready, reason = index_is_ready(conn)
        if not ready:
            print(reason)
            return 2

        print(f"Scoring {len(cases)} cases from {golden.name} "
              f"in '{mode}' mode against {reason}\n")
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
            rows = retrieve(conn, case.query, vector, mode=mode, corpus="policy", limit=10)
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
    def fmt(v):
        """None means the metric is undefined for this set, not zero.

        Recall over an empty set has no value. Printing 0.000 would read as "the system
        found nothing", which is a completely different claim from "there was nothing to
        find" — and it is the claim an interviewer would reasonably object to.
        """
        return f"{v:.3f}" if isinstance(v, (int, float)) else "  n/a"

    if summary["answerable"] == 0:
        print("  No answerable cases in this set — recall and MRR are undefined.")
        print("  This set measures abstention only. See the scores below.")
    else:
        for k in (1, 3, 5, 10):
            print(f"  recall@{k:<3} {fmt(summary[f'recall@{k}'])}      "
                  f"hit@{k:<3} {fmt(summary[f'hit@{k}'])}")
        print(f"  MRR       {fmt(summary['mrr'])}")
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
        "retrieval": mode,
        "golden_set": golden.name,
        "corpus": "policy",
    }, results)
    print(f"\n  saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
