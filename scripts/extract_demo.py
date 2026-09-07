"""Run the extractor against a text file and print the result.

    python scripts/extract_demo.py data/samples/eu_ai_act_art12.txt "EU AI Act, Article 12"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.extract import (  # noqa: E402
    ExtractionFailed,
    ModelUnavailable,
    extract_obligations,
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    path = Path(sys.argv[1])
    hint = sys.argv[2] if len(sys.argv) > 2 else path.stem

    if not path.exists():
        print(f"No such file: {path}")
        return 2

    text = path.read_text(encoding="utf-8")
    print(f"Source: {hint}  ({len(text)} chars)\n")

    try:
        run = extract_obligations(text, source_ref_hint=hint)
    except ModelUnavailable as e:
        # Distinct exit code: the passage is fine, the API was not. A scheduler should
        # treat this as "try again later", not as a document that cannot be processed.
        print(f"MODEL UNAVAILABLE: {e}\n")
        print("The passage is fine; the API would not serve it. Options:")
        print("  - quota exhausted  -> set a higher-quota GEMINI_MODEL in .env")
        print("                        (gemini-2.5-flash: 250/day, -flash-lite: 1000/day)")
        print("  - server overload  -> wait a few minutes and re-run")
        print("  - out for the day  -> enable billing, or switch provider in _call_model()")
        return 3
    except ExtractionFailed as e:
        print(f"EXTRACTION FAILED: {e}\n")
        for a in e.attempts:
            print(f"  - {a}")
        return 1

    r = run.result
    print(f"Attempts used: {run.attempts_used}")
    if run.transient_retries:
        print(f"Transient API retries: {run.transient_retries}")
    if run.errors_encountered:
        print("Recovered from:")
        for err in run.errors_encountered:
            print(f"  - {err[:200]}")
    print()

    if r.passage_contains_no_obligations:
        print("Model reports: this passage imposes no obligations.")
        return 0

    print(f"{len(r.obligations)} obligation(s) extracted:\n")
    for i, ob in enumerate(r.obligations, 1):
        print(f"[{i}] {ob.source_ref}")
        print(f"    party      : {ob.obliged_party.value}")
        print(f"    modality   : {ob.modality.value}")
        print(f"    action     : {ob.action}")
        print(f"    subject    : {ob.subject_matter}")
        print(f"    applies to : {ob.applies_to}")
        if ob.trigger:
            print(f"    trigger    : {ob.trigger}")
        if ob.duration_or_deadline:
            print(f"    timing     : {ob.duration_or_deadline}")
        if ob.exceptions:
            print(f"    exceptions : {'; '.join(ob.exceptions)}")
        print(f"    quote      : {ob.verbatim_quote[:160]}...")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
