# Resume — projects section (draft 1)

This is the half of your resume that depends only on the work. Paste it in, edit the voice
to sound like you, and send me your background so I can write the rest around it.

**Formatting rules I followed, and why:**
- Every bullet has a number or a named engineering decision. A bullet without one is a claim.
- No adjectives. "Robust", "scalable", "cutting-edge" are what people write when they have
  no measurement. You have measurements, so use them.
- Lead each project with the business problem, not the tech stack. The stack goes in a tail.
- Written for a 20-second skim, because that is what it gets.

---

## PROJECTS

### Redline — Regulatory Change Impact Agent
*Python · FastAPI · Postgres/pgvector · Gemini · Pydantic · Docker*
`github.com/Ananyapkumar/Redline`

Unattended agent that monitors federal regulators, determines which clauses of a company's
internal policy library each new rule affects, drafts the amendment, and routes
low-confidence cases to human review rather than guessing.

- Built a **citation-grounding validator that makes fabricated legal references structurally
  impossible** — every quote the model produces is verified against the source document in
  code, with bounded re-prompting on failure. Hallucinated-citation rate is enforced at zero
  rather than mitigated by prompt instructions.
- Ingested **75 real Federal Register rules** through a watcher with content-hash
  deduplication; re-running it stores zero duplicates, so scheduled runs cannot reprocess and
  re-bill the same regulation.
- Built a **hierarchy-preserving document normaliser** that parses regulatory XML, HTML and
  PDF, recovering section nesting from element position after discovering the publisher's
  heading attributes did not encode depth. Flat extraction would have degraded retrieval
  invisibly.
- Indexed a **131-clause policy corpus** in pgvector at 1,536 dimensions — chosen because
  pgvector cannot index above 2,000, so the model's 3,072-dimension default would have
  silently forced full table scans.
- Wrote a hand-labelled evaluation harness measuring recall@k, MRR and answerable/unanswerable
  score separation; **audited my own baseline, found it overstated because the test queries
  shared an author with the corpus, and rebuilt the golden set from real regulatory text.**
  Measured similarity fell from 0.77 to 0.51, confirming the contamination.
- Measured that **retrieval confidence cannot be thresholded** — mean top similarity 0.768
  when an answer exists versus 0.620 when none does, with overlapping ranges — and designed
  confidence as a derived signal rather than a model self-report.
- Implemented production failure handling: exponential backoff with jitter, separate
  treatment of rate limits for interactive versus batch work, per-run API call budgets, and
  idempotent writes. Documented **10 distinct failure modes** in a published taxonomy.
- **94 tests**, including hostile-input tests that assert the extractor fails loudly rather
  than returning partially-parsed output.

### Clause — Structured Extraction with Measured Accuracy
*Python · Pydantic · FastAPI · Gemini · LangGraph · ChromaDB · Render*
`github.com/Ananyapkumar/clause` · live API deployed

Extracts nine structured fields from lighting product datasheets, where manufacturers format
inconsistently and several plausible values compete for the same field.

- **99.4% field accuracy (161/162 judgements)** across an 18-document evaluation set, with a
  **pre-registered accuracy prediction that matched the observed result.**
- Ran **variance analysis establishing a one-field noise floor**, so the reported accuracy is
  distinguishable from run-to-run randomness rather than a single lucky run.
- Documented schema decisions against domain reasoning — e.g. system wattage versus LED load,
  warranty versus rated life — where the correct value requires domain judgement, not parsing.
- Retained **failed experiments and discarded components in the repository with measurements
  and verdicts**, rather than deleting what did not work.
- Deployed as a live FastAPI service with regression tests and ablations.

---

## SUGGESTED HEADLINE / SUMMARY

Pick one. The first is stronger if you can defend it; the second is safer.

> **AI Engineer — production LLM systems with measured reliability.**
> I build systems that know when they are wrong. Two shipped projects with published
> evaluation harnesses, hand-labelled golden sets, and documented failure taxonomies —
> including one where I audited my own benchmark and found it overstated.

> **AI Engineer.** I build LLM systems for production rather than demos: structured outputs
> with schema validation, retrieval over real documents, evaluation harnesses that measure
> whether the system is actually right, and failure handling for when it is not.

---

## WHY THESE BULLETS WORK

Worth understanding, because you will be asked about them.

**The audit bullet is the strongest thing on your resume.** "I measured 0.944, distrusted it,
found the cause, and rebuilt the test" is a sentence almost nobody writes. Most portfolios
report a flattering number. Yours reports a number *and* the reason it was not trustworthy.
Interviewers remember it because it signals you would catch the same problem in their codebase.

**The abstention design is the second.** "The system declines to answer when confidence is
insufficient" is the opposite of what most AI portfolios demonstrate, and it is what
distinguishes a tool a regulated business can actually deploy.

**The dimension bullet (1,536 vs 3,072) is small and does disproportionate work.** It proves
you read the constraints of the tools you use rather than accepting defaults — which is the
difference between someone who assembles libraries and someone who engineers with them.

---

## STATUS — be honest about this

Redline is **in progress**, not finished. Everything listed above is built, measured and
committed. Still to come: hybrid retrieval measurement, the agent loop with abstention
thresholds, and deployment.

Say "in progress" on the resume. A reviewer who clones the repo and finds it mid-build after
you called it complete will discount everything else on the page. One who finds it mid-build
after you said so sees an honest engineer with a live project.
