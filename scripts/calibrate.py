"""Measure the confidence signals against stored runs, before tuning any of them.

    python scripts/calibrate.py

Costs nothing — it reads agent_runs, which already holds every decision.

Why this exists. The first run routed 6 of 9 answerable cases to a human. That is not
obviously wrong — in compliance a missed obligation is a fine and an unnecessary review is
five minutes of an analyst's time, so erring toward review is correct. But two thirds is a
lot, and the question is WHICH signal is doing the rejecting.

`_derive_confidence` takes the MINIMUM of three components. Minimum is the right operator
for safety: one broken signal should sink the result rather than being averaged away by two
good ones. But minimum only behaves sensibly when the components are on comparable scales.
If one of them systematically reads lower than the others, it becomes the answer every time
and the other two are decoration.

This script prints the distribution of each component so that question is settled by
measurement rather than by me picking a different divisor and declaring it better.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.agent import CONFIDENCE_THRESHOLD, RETRIEVAL_THRESHOLD  # noqa: E402
from redline.store import connect  # noqa: E402

MARGIN_DIVISOR = 0.05      # the value currently in _derive_confidence
RETRIEVAL_SPAN = 0.18      # ditto


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))]


def main() -> int:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT obligation, route, confidence, top_score, margin, model_calls, affected
            FROM agent_runs ORDER BY top_score DESC NULLS LAST
            """
        ).fetchall()

    if not rows:
        print("No stored runs. Run scripts/run_agent.py --set first.")
        return 2

    gated = [r for r in rows if r["model_calls"] == 0 and (r["top_score"] or 0) < RETRIEVAL_THRESHOLD]
    judged = [r for r in rows if r["model_calls"] > 0]

    print(f"{len(rows)} stored runs: {len(gated)} abstained at the retrieval gate, "
          f"{len(judged)} reached the model\n")

    print("=" * 78)
    print("THE RETRIEVAL GATE")
    print("=" * 78)
    if gated:
        tops = [r["top_score"] for r in gated if r["top_score"] is not None]
        print(f"  gated cases      : top score {min(tops):.3f} – {max(tops):.3f}")
    if judged:
        tops = [r["top_score"] for r in judged if r["top_score"] is not None]
        print(f"  cases judged     : top score {min(tops):.3f} – {max(tops):.3f}")
    print(f"  threshold        : {RETRIEVAL_THRESHOLD}")
    print("  -> a clean split means the gate is doing its job and costs nothing.")

    if not judged:
        return 0

    print()
    print("=" * 78)
    print("THE THREE CONFIDENCE COMPONENTS, ON CASES THAT REACHED THE MODEL")
    print("=" * 78)
    print("  Each is clamped to [0,1]. confidence = min(all three).")
    print(f"  {'case':<34} {'top':>6} {'retr':>6} {'margin':>7} {'marg_c':>7} {'conf':>6}  route")
    print("  " + "-" * 76)

    retr_vals, marg_vals = [], []
    for r in judged:
        top = r["top_score"] or 0.0
        margin = r["margin"] or 0.0
        retr = max(0.0, min(1.0, (top - RETRIEVAL_THRESHOLD) / RETRIEVAL_SPAN))
        marg_c = max(0.0, min(1.0, margin / MARGIN_DIVISOR))
        retr_vals.append(retr)
        marg_vals.append(marg_c)
        label = " ".join(r["obligation"].split())[:33]
        flag = "AUTO" if r["route"] == "auto_filed" else "review"
        print(f"  {label:<34} {top:>6.3f} {retr:>6.2f} {margin:>7.3f} {marg_c:>7.2f} "
              f"{r['confidence']:>6.3f}  {flag}")

    print()
    print("  component summary (min / median / max):")
    print(f"    retrieval : {min(retr_vals):.2f} / {pct(retr_vals, 0.5):.2f} / {max(retr_vals):.2f}")
    print(f"    margin    : {min(marg_vals):.2f} / {pct(marg_vals, 0.5):.2f} / {max(marg_vals):.2f}")

    binding = sum(1 for r, m in zip(retr_vals, marg_vals) if m < r)
    print(f"\n  margin was the binding (lowest) component in {binding}/{len(judged)} cases.")
    if binding > len(judged) * 0.6:
        raw = [r["margin"] or 0.0 for r in judged]
        print()
        print("  ^ MARGIN IS DOMINATING. The other two signals are not reaching the")
        print("    decision, so confidence is effectively 'margin' wearing a disguise.")
        print(f"    Raw margins here run {min(raw):.3f} – {max(raw):.3f}, median {pct(raw, 0.5):.3f},")
        print(f"    against a divisor of {MARGIN_DIVISOR}. The divisor was chosen by intuition")
        print("    before any margin had been measured.")
        print()
        print(f"    A divisor near the 75th percentile ({pct(raw, 0.75):.3f}) would let a")
        print("    typical case score around 0.75 on margin, putting all three components")
        print("    on comparable scales so min() means what it is supposed to mean.")
        print()
        print("    DO NOT just change the number. Change it, re-run --set, and compare the")
        print("    routing split. A tuning change with no before-and-after is a guess.")

    auto = sum(1 for r in judged if r["route"] == "auto_filed")
    print()
    print("=" * 78)
    print(f"  of {len(judged)} cases that reached the model: {auto} auto-filed, "
          f"{len(judged) - auto} routed to a human")
    print(f"  confidence threshold: {CONFIDENCE_THRESHOLD}")
    print()
    print("  There is no correct hand-off rate in the abstract. It depends on the cost")
    print("  asymmetry: a missed obligation is a regulatory fine, an unnecessary review is")
    print("  a few minutes of an analyst's time. Erring toward review is right. The question")
    print("  is whether THIS rate is deliberate or an artefact of a miscalibrated signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
