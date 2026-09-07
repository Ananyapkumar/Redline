"""Demonstrate the citation-grounding guarantee against the real Article 12 text.

    python scripts/prove_grounding.py

Makes no API calls and costs no quota. It builds obligations by hand — some honest, some
fabricated — and runs the same _check_grounding() the extractor uses, so you can watch the
guarantee work rather than take it on trust.

Note on what a real test looks like here: editing the source file and then extracting from
that same edited file proves nothing. The model would quote the edited text, and grounding
would correctly pass. The guarantee is about a quote that is NOT in the document it is
checked against — so that is what this script constructs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.extract import _check_grounding  # noqa: E402
from redline.schemas import (  # noqa: E402
    ExtractionResult,
    Modality,
    Obligation,
    ObligedParty,
)

SOURCE_PATH = Path("data/samples/eu_ai_act_art12.txt")


def obligation(quote: str, ref: str = "Article 12(1)") -> Obligation:
    return Obligation(
        source_ref=ref,
        obliged_party=ObligedParty.UNSPECIFIED,
        modality=Modality.MANDATORY,
        action="allow automatic recording of events",
        subject_matter="automatic recording of events (logs)",
        applies_to="high-risk AI systems",
        verbatim_quote=quote,
    )


def check(label: str, quote: str, source: str, expect_pass: bool) -> bool:
    problems = _check_grounding(ExtractionResult(obligations=[obligation(quote)]), source)
    passed = not problems
    correct = passed == expect_pass
    verdict = "GROUNDED" if passed else "REJECTED"
    print(f"  [{'ok ' if correct else 'BAD'}] {verdict:<9} {label}")
    print(f"        quote: {quote[:88]}{'...' if len(quote) > 88 else ''}")
    if problems:
        print(f"        why  : {problems[0].split(' You wrote')[0]}")
    print()
    return correct


def main() -> int:
    if not SOURCE_PATH.exists():
        print(f"Missing {SOURCE_PATH}")
        return 2
    source = SOURCE_PATH.read_text(encoding="utf-8")

    # Taken verbatim from your Article 12 text.
    real = (
        "High-risk AI systems shall technically allow for the automatic recording "
        "of events (logs) over the lifetime of the system."
    )

    print(f"Source: {SOURCE_PATH}  ({len(source)} chars)\n")
    print("Each case below states what SHOULD happen. '[ok]' means it did.\n")
    results = []

    print("1. An honest quote, copied exactly. Must be accepted.")
    results.append(check("exact copy from the document", real, source, expect_pass=True))

    print("2. Same quote, line-wrapped as a PDF would break it. Must still be accepted —")
    print("   reflowed whitespace is not fabrication, and rejecting it would be a false alarm.")
    wrapped = (
        "High-risk AI systems shall technically\n   allow for the automatic recording\n"
        "of events (logs) over the lifetime of the system."
    )
    results.append(check("reflowed across lines", wrapped, source, expect_pass=True))

    print("3. One word changed: 'lifetime' -> 'first six months'. Must be REJECTED.")
    print("   This is the dangerous case — a plausible, professional-sounding citation")
    print("   that the document does not actually contain.")
    altered = real.replace("over the lifetime of the system", "over the first six months of operation")
    results.append(check("one clause silently altered", altered, source, expect_pass=False))

    print("4. A retention period invented from nothing. Must be REJECTED.")
    invented = (
        "Providers shall retain all automatically generated logs for a period of not "
        "less than twenty-four (24) months following decommissioning of the system."
    )
    results.append(check("entirely fabricated obligation", invented, source, expect_pass=False))

    print("5. Real words from the document, stitched together from separate places.")
    print("   Must be REJECTED — each fragment exists, the sentence never did.")
    frankenquote = (
        "High-risk AI systems shall technically allow for the automatic recording of "
        "events (logs) for which the search has led to a match;"
    )
    results.append(check("fragments spliced into a new sentence", frankenquote, source, expect_pass=False))

    passed = sum(results)
    print("-" * 72)
    if passed == len(results):
        print(f"All {len(results)} cases behaved as specified.")
        print("Honest citations pass. Altered, invented and spliced ones are rejected in code,")
        print("not requested in a prompt. That is the difference between 'the model said so'")
        print("and 'the document says so'.")
        return 0

    print(f"{passed}/{len(results)} behaved as specified — grounding is NOT trustworthy.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
