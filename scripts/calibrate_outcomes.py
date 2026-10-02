"""Does confidence actually predict correctness?

    python scripts/calibrate_outcomes.py

Free — joins stored agent decisions to the hand-labelled golden set.

The question calibrate.py could not answer. It showed WHICH signal drives confidence
(retrieval, not margin — my prediction was wrong). It could not show whether confidence is
RIGHT, because that needs ground truth, and ground truth is exactly what the golden set is.

A confidence score only earns its name if high confidence means more often correct. If the
cases the agent routed to a human were just as correct as the ones it auto-filed, then the
score is sorting on something irrelevant and the threshold is costing analyst time for
nothing. If the routed ones really were worse, the threshold is doing its job.

That is a measurable claim, and these 9 cases have labels.

A caveat that has to be stated: the 9 answerable cases are the CONTAMINATED half of the
merged set — their queries were written by the same author as the policy corpus. So the
accuracy numbers here are an upper bound. The calibration SHAPE (does confidence track
correctness?) is still informative, because contamination affects all 9 equally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.agent import CONFIDENCE_THRESHOLD  # noqa: E402
from redline.store import connect  # noqa: E402

GOLDEN = Path("evals/golden-merged.jsonl")


def main() -> int:
    rows = [json.loads(ln) for ln in GOLDEN.read_text(encoding="utf-8").splitlines() if ln.strip()]
    truth = {" ".join(r["query"].split()): set(r["relevant"])
             for r in rows if not all(k.startswith("_") for k in r)}

    with connect() as conn:
        runs = conn.execute(
            "SELECT obligation, route, confidence, affected FROM agent_runs "
            "WHERE model_calls > 0 ORDER BY confidence DESC"
        ).fetchall()

    if not runs:
        print("No judged runs stored. Run scripts/run_agent.py --set first.")
        return 2

    print(f"{'conf':>6} {'route':>7} {'prec':>5} {'rec':>5}  case")
    print("-" * 78)

    scored = []
    for r in runs:
        key = " ".join(r["obligation"].split())
        expected = truth.get(key)
        if expected is None:
            continue
        got = set(r["affected"] or [])
        tp = len(got & expected)
        precision = tp / len(got) if got else (1.0 if not expected else 0.0)
        recall = tp / len(expected) if expected else 1.0
        exact = got == expected
        scored.append((r["confidence"], r["route"], precision, recall, exact))
        mark = "" if exact else "   <- not exact"
        print(f"{r['confidence']:>6.3f} {('AUTO' if r['route']=='auto_filed' else 'review'):>7} "
              f"{precision:>5.2f} {recall:>5.2f}  {key[:44]}{mark}")

    if not scored:
        print("\nNo stored run matched a labelled case.")
        return 1

    auto = [s for s in scored if s[1] == "auto_filed"]
    review = [s for s in scored if s[1] != "auto_filed"]

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    print()
    print("=" * 78)
    print("DOES CONFIDENCE TRACK CORRECTNESS?")
    print("=" * 78)
    print(f"  {'group':<22} {'n':>3} {'mean prec':>10} {'mean rec':>9} {'exact':>7}")
    print(f"  {'auto-filed':<22} {len(auto):>3} {mean([s[2] for s in auto]):>10.2f} "
          f"{mean([s[3] for s in auto]):>9.2f} {sum(s[4] for s in auto):>4}/{len(auto)}")
    print(f"  {'routed to human':<22} {len(review):>3} {mean([s[2] for s in review]):>10.2f} "
          f"{mean([s[3] for s in review]):>9.2f} {sum(s[4] for s in review):>4}/{len(review)}")

    print()
    print("  How to read this:")
    print("    auto-filed CLEARLY better  -> the threshold is earning its keep.")
    print("    both groups about the same -> confidence is sorting on something that does")
    print("                                  not predict correctness. Either fix the signal")
    print("                                  or lower the threshold; routing two thirds of")
    print("                                  correct answers to a human buys nothing.")
    print("    routed group better        -> the score is inverted. Investigate before")
    print("                                  touching the threshold.")

    print()
    print("=" * 78)
    print("WHAT DIFFERENT THRESHOLDS WOULD HAVE DONE")
    print("=" * 78)
    print(f"  {'threshold':>9} {'auto':>5} {'routed':>7} {'auto exact':>11} {'missed good':>12}")
    for t in (0.25, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
        a = [s for s in scored if s[0] >= t]
        rvw = [s for s in scored if s[0] < t]
        ok = sum(s[4] for s in a)
        missed = sum(s[4] for s in rvw)      # exact answers sent to a human anyway
        flag = "  <- current" if abs(t - CONFIDENCE_THRESHOLD) < 1e-9 else ""
        print(f"  {t:>9.2f} {len(a):>5} {len(rvw):>7} {ok:>7}/{len(a):<3} {missed:>12}{flag}")

    print()
    print("  'missed good' = correct answers routed to a human anyway. That is the cost of")
    print("  the threshold. 'auto exact' = auto-filed answers that were exactly right. That")
    print("  is what you are buying. Pick the row where the trade reads well for a compliance")
    print("  team, state the reasoning in DECISIONS.md, and move on.")
    print()
    print("  CAVEAT: these 9 cases are the contaminated half of the merged set. Treat the")
    print("  accuracy as an upper bound; the calibration SHAPE is the usable finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
