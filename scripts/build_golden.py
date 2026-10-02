"""Mine a golden set from REAL regulatory text instead of paraphrase.

    python scripts/build_golden.py --sections 8

Day 36 measured recall@10 of 0.944 and the number was not trustworthy: Claude wrote the
policy corpus AND the eval queries, so they shared vocabulary and several cases were
near-verbatim matches. That measures duplicate detection, not retrieval.

This closes the loop instead. The Day 31 extractor runs over the Federal Register
documents already stored and normalised, producing obligations phrased by federal
drafters. Those become the queries — uncontaminated, harder, and identical in shape to
what the system will receive in production.

Output is a DRAFT: obligations plus retrieved candidate clauses, deliberately UNLABELLED.
Labelling is scripts/label_golden.py, and it is a human's job. A golden set labelled by
the system it measures is a mirror, not a test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.embeddings import embed_query  # noqa: E402
from redline.extract import ExtractionFailed, extract_obligations  # noqa: E402
from redline.resilience import CallBudget, ModelUnavailable  # noqa: E402
from redline.store import connect, index_is_ready, search_chunks  # noqa: E402

DRAFT = Path("evals/golden-draft.jsonl")

# Vocabulary the policy corpus actually covers: security, privacy, retention, access,
# billing, model governance. Added on Day 37 after the first run produced ten obligations
# about cattle feed purity specifications — every one correctly labelled "no applicable
# clause", and the resulting golden set measured nothing about retrieval.
#
# This filter is a real design statement, not a convenience. A regulatory monitor DOES
# receive a large volume of rules that affect nothing, and handling that well is the
# product. But a set of 100% unanswerable cases cannot measure whether retrieval finds
# what exists. The two halves are mined separately and deliberately.
DOMAIN_TERMS = (
    r"record|retain|retention|disclos|privacy|confidential|security|safeguard|"
    r"audit|\mlog\M|logging|access|authoriz|consent|encrypt|breach|notif|"
    r"\mclaim\M|payment|overpayment|billing|reimburse|"
    r"algorithm|automated|software|\mmodel\M|electronic"
)

BOILERPLATE = (
    r"^(AGENCY|ACTION|DATES|ADDRESSES|FOR FURTHER|SUMMARY|SUPPLEMENTARY|"
    r"List of Subjects|Authority|Note|DEPARTMENT|PART )"
)

# DISTINCT ON (document_id) is the fix for the first failure: the original query ordered
# by "shall" density and took the top 8, which all came from ONE document because that
# document happened to be dense with obligations. One section per document first, then
# rank across documents.
CANDIDATE_SQL = f"""
    SELECT * FROM (
        SELECT DISTINCT ON (s.document_id)
               s.id, s.ref, s.heading, s.body, d.title, d.external_id,
               (length(s.body) - length(regexp_replace(s.body, 'shall', '', 'gi'))) AS density
        FROM document_sections s
        JOIN source_documents d ON d.id = s.document_id
        WHERE length(s.body) BETWEEN 300 AND 4000
          AND s.body ~* '\\mshall\\M|\\mmust\\M|\\mrequired to\\M'
          AND ({{domain_clause}})
          AND s.heading IS NOT NULL
          AND s.heading !~* %(boilerplate)s
        ORDER BY s.document_id, density DESC
    ) t
    ORDER BY density DESC
    LIMIT %(limit)s
"""

INVENTORY_SQL = f"""
    SELECT d.external_id, d.title,
           count(s.id)                                    AS sections,
           count(*) FILTER (WHERE s.body ~* %(domain)s)    AS domain_sections,
           count(*) FILTER (WHERE s.body ~* '\\mshall\\M'
                              AND s.body ~* %(domain)s)    AS usable
    FROM source_documents d
    LEFT JOIN document_sections s ON s.document_id = d.id
    GROUP BY d.id
    ORDER BY usable DESC NULLS LAST, sections DESC
"""


def diagnose(conn) -> int:
    """Show what the store can actually contribute, before spending a single call."""
    rows = conn.execute(INVENTORY_SQL, {"domain": DOMAIN_TERMS}).fetchall()
    print(f"{'document':<14} {'secs':>5} {'domain':>7} {'usable':>7}  title")
    print("-" * 92)
    total = 0
    for r in rows:
        total += r["usable"] or 0
        print(f"  {r['external_id']:<12} {r['sections'] or 0:>5} "
              f"{r['domain_sections'] or 0:>7} {r['usable'] or 0:>7}  {r['title'][:52]}")
    contributing = sum(1 for r in rows if (r["usable"] or 0) > 0)
    print(f"\n  {contributing} of {len(rows)} documents can contribute an on-topic")
    print(f"  obligation; {total} usable sections in total.")
    if contributing < 4:
        print("\n  WARNING: too few documents overlap the policy corpus. Widen the")
        print("  watcher (scripts/watch.py: more agencies, or PRORULE as well as RULE)")
        print("  before mining, or the golden set will be narrow and unrepresentative.")
    return 0


def main() -> int:
    args = sys.argv[1:]
    n = int(args[args.index("--sections") + 1]) if "--sections" in args else 8
    append = "--append" in args
    # --any drops the domain filter, for mining the unanswerable half on purpose.
    domain_clause = "TRUE" if "--any" in args else "s.body ~* %(domain)s"

    with connect() as conn:
        ready, reason = index_is_ready(conn)
        if not ready:
            print(reason)
            return 2

        if "--diagnose" in args:
            return diagnose(conn)

        sections = conn.execute(
            CANDIDATE_SQL.format(domain_clause=domain_clause),
            {"domain": DOMAIN_TERMS, "boilerplate": BOILERPLATE, "limit": n},
        ).fetchall()
        if not sections:
            print("No obligation-bearing sections. Run scripts/normalise.py first.")
            return 2

        print(f"{len(sections)} candidate section(s) selected from stored regulations\n")

        # Keep anything already labelled. The ten "no applicable clause" cases from the
        # first run are not waste — they are the abstention half of the set, and Day 36
        # had exactly one.
        existing: list[dict] = []
        if append and DRAFT.exists():
            existing = [json.loads(ln) for ln in
                        DRAFT.read_text(encoding="utf-8").splitlines() if ln.strip()]
            print(f"  keeping {len(existing)} existing draft(s), "
                  f"{sum(1 for e in existing if e.get('verified'))} already labelled\n")

        seen_docs = {e.get("source_document") for e in existing}
        sections = [s for s in sections if s["external_id"] not in seen_docs] \
            if append else sections
        print(f"  mining from: {', '.join(sorted({s['external_id'] for s in sections}))}\n")

        drafts: list[dict] = list(existing)
        budget = CallBudget(limit=n * 4)

        for i, sec in enumerate(sections, 1):
            label = f"{sec['title'][:48]} — {sec['heading'][:36]}"
            print(f"  [{i}/{len(sections)}] {label}")
            try:
                run = extract_obligations(
                    sec["body"], source_ref_hint=f"{sec['title'][:60]} ({sec['external_id']})"
                )
            except ExtractionFailed as e:
                print(f"      extraction failed: {e}\n")
                continue
            except ModelUnavailable as e:
                print(f"\n      STOPPED: {e}")
                print(f"      {len(drafts)} obligations drafted so far are saved below.")
                break

            obligations = run.result.obligations
            print(f"      {len(obligations)} obligation(s), {run.attempts_used} attempt(s)")

            for ob in obligations:
                # The query is the obligation as the regulator stated it. Not a summary,
                # not Claude's paraphrase — the grounded quote plus the structured action,
                # which is exactly what the agent will hold at retrieval time on Day 45.
                query = f"{ob.action}. {ob.verbatim_quote}"
                try:
                    vector = embed_query(query, budget=budget)
                except ModelUnavailable as e:
                    print(f"      STOPPED embedding: {e}")
                    break
                rows = search_chunks(conn, vector, corpus="policy", limit=10)
                drafts.append({
                    "id": f"G{len(drafts) + 1:02d}",
                    "domain_filtered": "--any" not in args,
                    "source_document": sec["external_id"],
                    "source_ref": ob.source_ref,
                    "obliged_party": ob.obliged_party.value,
                    "modality": ob.modality.value,
                    "action": ob.action,
                    "query": query,
                    "verbatim_quote": ob.verbatim_quote,
                    "candidates": [
                        {"ref": r["ref"], "similarity": round(float(r["similarity"]), 3),
                         "context": r["context"], "body": r["body"]}
                        for r in rows
                    ],
                    "relevant": None,      # None = not yet labelled. [] = no clause applies.
                    "verified": False,
                })
            print()

    DRAFT.parent.mkdir(parents=True, exist_ok=True)
    DRAFT.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in drafts) + "\n", encoding="utf-8"
    )
    print("=" * 70)
    new_count = len(drafts) - len(existing) if append else len(drafts)
    print(f"  {new_count} new obligation(s), {len(drafts)} total -> {DRAFT}")
    print(f"  extraction + embedding calls: {budget.used}/{budget.limit}")
    print("\n  Next: python scripts/label_golden.py")
    print("  Nothing is labelled yet. A golden set labelled by the system it measures")
    print("  is a mirror, not a test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
