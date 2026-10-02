# Engineering decisions

Every significant choice in Redline, what was rejected, and — where it exists — the evidence
that settled it. Decisions that measurement overturned are kept with the original reasoning
intact, because the reasoning being wrong is the useful part.

Written for a reviewer with fifteen minutes. If you read one section, read D7.

---

## D1 — Postgres with pgvector, not a dedicated vector database

**Rejected:** ChromaDB (used in my previous project), Pinecone, Weaviate, Qdrant.

The corpus is ~400 clauses. At that scale a dedicated vector store solves a problem I do not
have, while adding a second system to operate and a second consistency boundary. Hybrid
retrieval needs keyword and vector search over the same rows; Postgres does both natively,
ChromaDB does not.

**What it cost me to get right:** pgvector's HNSW index caps at 2,000 dimensions and the
embedding model returns 3,072 by default. Store the default and every column works, the data
looks correct, and `CREATE INDEX` fails — or you skip the index and every query becomes a
sequential scan, fast at 131 rows and unusable later. Dimensions are now requested
explicitly at 1,536.

**Reversal cost:** low. Retrieval sits behind one function.

---

## D2 — Hand-written agent loop, no framework

**Rejected:** LangGraph (which I had used before without writing the underlying loop),
CrewAI, AutoGen.

I could build with LangGraph and could not confidently debug it. Writing the state machine
directly — roughly eighty lines — removed that gap.

**Changed from the original plan:** a port back to LangGraph was planned, to make the
framework's contribution measurable. Cut when the project was rescoped. Stated here rather
than quietly dropped.

---

## D3 — Schema-validated outputs, never parsed from prose

Every model output the system acts on is a Pydantic model returned through structured
output. Validation failure triggers a bounded retry; there is no text-parsing fallback.

**Rejected:** asking for JSON in the prompt and parsing the reply.

Prompt instructions are not guarantees. A parser that succeeds on malformed input is worse
than one that fails, because it propagates corruption silently.

---

## D4 — Citations are verified in code, not requested in the prompt

Two validators, both enforced rather than instructed:

- **Quotes.** Every `verbatim_quote` the extractor produces is checked against the source
  text, after normalising whitespace and typography so reflowed PDF text is not mistaken for
  fabrication. A quote that is not in the document is rejected and re-prompted.
- **Clause identifiers.** Every clause id the agent cites must be one that was offered to
  it. Inventing `P-999 §9.9` is impossible, not discouraged.

**Rejected:** instructing the model to cite only real sources; sampling citations for manual
review.

In a compliance context a fabricated citation is the worst possible output — worse than no
answer, because it is authoritative, plausible and would be acted on. This class of error
can be eliminated deterministically, so it should be.

**Evidence:** `scripts/prove_grounding.py` runs five cases including a "frankenquote"
assembled from real fragments of two different sentences. It is rejected.

---

## D5 — The system abstains rather than guessing

Below a confidence threshold, or on detected ambiguity, an item is routed to a human with a
stated reason.

**Rejected:** always producing a best-effort answer with a confidence label attached.

For this user a system that hands off some cases and is reliable on the rest is usable. One
that answers everything at 85% accuracy is not, because the analyst must then re-check all
of it — which is the work being replaced.

**Measured:** on 27 cases, 18 were declined at the retrieval gate. All 18 were genuinely
unanswerable. No false abstentions.

---

## D6 — Retrieval gates the model call

If no candidate scores above the threshold, the agent abstains **without calling the model
at all.**

Partly cost — on a 20-call-a-day free tier this is the difference between finishing a run
and not. Mostly this: asking a language model "which of these unrelated clauses is affected?"
invites it to find one. **The cheapest way to avoid a confident wrong answer is not to ask
the question.**

**Measured:** 18 of 27 obligations never reached the model. Zero tokens, zero risk of a
fabricated match.

---

## D7 — Confidence is derived from evidence, and the first version was inverted

**This is the section to read.**

The model is never asked how certain it is. Self-reported confidence tracks fluency, not
correctness. Instead three checkable signals, combined with `min()` rather than an average
so that one broken signal sinks the result instead of being averaged away:

- how far the top retrieval score sits above the measured threshold
- the margin between the first and second candidate
- whether every citation survived validation, and whether anything was flagged ambiguous

**Then it was measured against ground truth, and it was backwards.**

```
auto-filed       n=3   mean precision 1.00   mean recall 0.50   exact 0/3
routed to human  n=6   mean precision 1.00   mean recall 0.78   exact 3/6
```

The cases the agent was most confident about were *less* complete than the ones it declined.

**Diagnosis, in two linked parts.** Precision was perfect everywhere — no clause was ever
wrongly named. Recall collapsed on multi-clause obligations. Every case that achieved recall
1.00 was a single-clause obligation, and those got the *lowest* confidence.

The system prompt contained "not_affected — this will be the correct verdict for most
candidates", written to prevent over-matching. It over-corrected: the agent found the
strongest match and dismissed the rest. And confidence was driven largely by retrieval
top-score, which measures *"is there one obvious match"* — precisely the condition under
which the agent latches onto one clause and misses the others. **The signal was
anti-correlated with the property it claimed to measure.**

**Why it was invisible.** Every individual output looked right: sound reasoning, validated
citations, perfect precision. Nothing in any single result revealed that a second affected
clause existed and had been dismissed. Only joining decisions to hand-labelled ground truth
exposed it.

**Fixed:** the prompt now states that one obligation usually engages several clauses across
different policies, requires a second pass over the dismissed set looking for the same duty
in different vocabulary ("least privilege" and "minimum necessary" are the same rule), and
states the cost asymmetry — a clause wrongly included costs a reviewer seconds; a clause
missed is an unmet obligation nobody knows about.

**Deliberately not fixed:** the confidence formula. Recalibrating against outcomes produced
by a broken judgement would fit the new formula to the old bug. Re-measure after the prompt
change, then recalibrate.

**The generalisable lesson.** A confidence signal must be validated against outcomes, not
assumed from plausibility. "Top retrieval score" *sounds* like a proxy for certainty. It is
a proxy for one strong match, which on a completeness task is closer to a warning sign.

---

## D8 — The abstention threshold is measured, not chosen

0.67, from the 27-case evaluation set where answerable queries scored 0.717–0.825 and
unanswerable ones 0.522–0.620. The ranges do not overlap; the threshold sits in the gap. A
test asserts this, so if the evidence moves the test fails.

**This reversed an earlier conclusion.** On Day 35 I measured separation of 0.02 and
concluded similarity could not be thresholded. That measurement was taken on a contaminated
set whose negative cases were written by the same author as the corpus. Replacing them with
real regulatory text made the signal clean — 0.215 separation. The earlier conclusion was
not wrong reasoning; it was correct reasoning over bad data.

---

## D9 — Two chunking strategies, because two corpora

```
policy clauses     median   130 characters
regulatory text    average 5,099 characters   (one document averaged 9,862)
```

Policy clauses are never split: the author already chunked them by writing numbered clauses,
and clause identity is the contract — `P-002 §2.2` must address exactly one retrievable
unit. Regulatory sections split at paragraph boundaries, never mid-sentence, with the
section's heading carried into every piece.

An embedding is a fixed-length summary of whatever it is given. Hand it 9,862 characters
spanning four subtopics and the vector lands in the average of all of them — near
everything, precisely near nothing. It still gets retrieved sometimes, which is worse than
never, because the failure looks like bad luck.

---

## D10 — The golden set is hand-labelled, and the first one was audited and rebuilt

The first evaluation reported recall@10 of 0.944 against a 0.85 target. I did not trust it,
and the reason was structural: the same author wrote both the policy corpus and the test
queries, so they shared vocabulary and several cases were near-verbatim matches.

**Confirmed by measurement.** Real regulatory text scores ~0.51 similarity against the same
corpus; the author-written queries scored 0.72–0.83.

The set was rebuilt from obligations extracted by the project's own extractor from stored
Federal Register rules — queries written by federal drafters, not by me. The original is
kept and labelled for what it is: a floor measured on an easy set.

**A second failure, and the fix.** The first mining run produced ten obligations, all from
one document, all about cattle-feed additive specifications. The selection query ordered by
density of obligation language and took the top results, which all came from the single
densest document, and never asked whether the subject matter overlapped the policy corpus at
all. Fixed with one-section-per-document selection and a topical filter. The ten cases were
kept — they are the abstention half of the set, and the previous version had exactly one
such case.

---

## D11 — Hybrid retrieval was measured, and the first implementation was a silent no-op

Vector search understands that "twelve months" and "one year" mean the same thing, which is
usually an advantage and occasionally a disaster — a compliance clause turns on the exact
figure. Keyword search has the opposite bias. Fused by Reciprocal Rank Fusion, which uses
rank position rather than score magnitude, because cosine similarity and `ts_rank_cd` have
no meaningful conversion between them.

**The bug.** The first version used `websearch_to_tsquery`, which joins every term with AND.
The queries are whole regulatory sentences — forty-odd words — so the query became a
forty-term conjunction matching nothing. Keyword search returned an empty list, RRF had only
the vector ranking to fuse, and hybrid mode produced results byte-identical to vector mode.

Nothing errored. Three evaluation runs reported identical numbers and looked like evidence
that hybrid retrieval does not help. It was never running. Terms are now OR-ed: AND is for
filtering, OR is for ranking.

---

## D12 — MMR diversity reranking was measured and rejected

Built to address a specific observed failure: retrieval returning five near-neighbours from
one policy document and missing the clause stating the same obligation elsewhere in
different words.

**Measured:** recall@3 fell from 0.944 to 0.833 while recall@10 stayed flat. MMR pushed a
genuinely relevant clause down because a similar one was selected first — the cost I expected
and could not quantify beforehand. On this corpus the trade is not worth it.

Kept in the codebase, selectable by flag, not the default. A change measured and rejected is
worth as much as one kept.

---

## D13 — Reliability was designed in, not added afterwards

Retries with exponential backoff and jitter; per-run timeouts; idempotency keyed on content
hash; a hard cap on model calls per unit of work; failures persisted rather than dropped.

**One distinction worth stating.** A rate limit means opposite things to interactive and
batch work. Extraction is interactive — a 429 saying "retry in 37s" should surface so the
caller can act. Embedding a corpus is batch — nobody is waiting, and aborting a 131-item job
because item 97 arrived too fast throws away 96 successes to save 37 seconds. The shared
backoff helper takes `rate_limit_is_fatal` and the two callers pass opposite values.

---

## Open questions

1. **Does the prompt fix restore recall?** Measured on the next run. If recall improves and
   confidence is still inverted, the score needs rebuilding rather than recalibrating.
2. **Is `min()` still right once the components are on comparable scales?** It is correct
   for safety and currently selects whichever signal is systematically lowest, which is not
   the same thing.
3. **The answerable half of the evaluation set is still contaminated.** Producing clean
   positives needs regulatory documents whose subject matter genuinely overlaps a
   health-technology policy library. The watcher now searches for the right terms; it has
   not yet caught enough of them.

## Revision policy

Decisions are not edited in place. A changed decision gets a new entry that supersedes the
old one, so the reasoning trail survives — including the reasoning that turned out wrong.
