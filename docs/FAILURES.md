# Failure taxonomy

Every failure mode observed while building Redline, what caused it, and what was done.
Started Day 31 rather than Day 56, because five distinct failures appeared in the first
afternoon of running the extractor against a live API.

Categories are separated deliberately: an **infrastructure** failure means the system
could not complete, and a **quality** failure means it completed and was wrong. The second
kind is more dangerous, because nothing crashes.

---

## Infrastructure failures

### F1 — Transient server overload
**Observed:** Day 31. `HTTP 500 — gemini-3.8-flash is currently experiencing high demand.`
**Cause:** Provider-side capacity, unrelated to the request.
**Impact before fix:** Killed the entire run. One busy moment lost all work.
**Fix:** `_call_model_resilient()` — exponential backoff with jitter (2s, 4s, 8s). Jitter
matters: without it, many failing clients wake simultaneously and hammer the recovering
service in lockstep.
**Status:** Handled.

### F2 — Quota exhaustion
**Observed:** Day 31. `HTTP 429 — Quota exceeded, limit: 20, model: gemini-3.8-flash.`
**Cause:** Free-tier request cap reached.
**Impact before fix:** Treated as generic overload and retried 4x, consuming the very
quota that was exhausted.
**Fix:** `_is_rate_limit()` classifies quota errors separately and short-circuits after one
call. `_suggested_delay()` parses the server's own retry-after hint and obeys it in
preference to our guess.
**Lesson:** Exponential backoff is correct for "the server is busy" and actively wrong for
"you have run out of allowance." They look alike and demand opposite responses.
**Status:** Handled.

### F3 — Retry amplification
**Observed:** Day 31, by arithmetic on the logs rather than a crash.
**Cause:** 3 semantic attempts x 4 transport retries = up to 12 API calls for one passage.
Two independent retry loops, neither aware of the other.
**Impact:** More than half a 20-request daily quota consumable by a single command.
**Fix:** `_CallBudget`, threaded through every retry path, hard cap of 6 calls per
extraction. A budget that only one loop respects is not a budget.
**Lesson:** Nested retries multiply. Count the product, not the terms.
**Status:** Handled.

### F4 — Per-model quota, inversely proportional to model recency
**Observed:** Day 31. `gemini-3.8-flash` allowed 20 requests/day; older flash variants allow
far more.
**Cause:** Newest models carry the tightest free-tier caps.
**Fix:** Model is configuration (`GEMINI_MODEL`), never a literal, so switching is a
one-line change with no code edit.
**Open:** Evals must pin an exact model so scores stay comparable between runs. High-volume
batch work may prefer a `-latest` alias. These are opposing requirements and the choice
should be explicit in `EVALS.md`.
**Status:** Handled by configuration.

### F5 — Model deprecation
**Observed:** Day 31. `HTTP 404 — This model models/gemini-2.5-flash is no longer available
to new users. Please update your code to use models/gemini-3.6-flash.`
**Cause:** Model retired for accounts created after some cutoff.
**Notable:** Correctly classified as permanent, so it failed on the first call instead of
retrying into a wall — `_is_transient()` behaving as designed.
**Fix:** `_suggested_replacement_model()` extracts the successor the API names and surfaces
it in the error message. Deprecation is one of the few permanent failures that arrives with
its own fix attached; burying that inside a stack trace wastes it.
**Status:** Handled.

### F6 — Model listing is a catalogue, not a permission check
**Observed:** Day 31. `client.models.list()` returned `gemini-2.5-flash`; calling it
returned 404 "no longer available to new users."
**Cause:** The listing endpoint enumerates models that exist, not models this account may
call.
**Fix:** Documented in `scripts/list_models.py`. The list is candidates to try, never a
guarantee.
**Lesson:** The only real capability test is a request.
**Status:** Documented.

---

## Quality failures

### Q1 — Sub-point obligations cite the parent paragraph, not their own text
**Observed:** Day 31, first successful extraction of EU AI Act Article 12.
**Symptom:** Obligations 12(2)(a), (b) and (c) all carry an identical `verbatim_quote` — the
lead-in sentence of paragraph 2 rather than the text of each sub-point. Same for 12(3)(a)
through (d).
**Why it passed every check:** The quote genuinely appears in the source, so
`_check_grounding` is satisfied. The `source_ref` values are distinct, so the duplicate-ref
validator is satisfied. The output is *valid* and *grounded* and still *imprecise*.
**Why it matters:** For a compliance tool, "which exact words create this obligation" is the
product. A reviewer asked to approve a redline needs the sub-point, not its preamble.
**Candidate fixes, none yet applied:**
  - Prompt: require the quote to be the sub-point's own text where sub-points exist.
  - Validator: reject an extraction where distinct obligations share an identical quote —
    but carefully, since two obligations can legitimately arise from one sentence.
  - Chunking: split at sub-point boundaries before extraction, so the parent text is not
    available to quote.
**Deliberately not fixed yet.** Fixing by intuition is how you convince yourself something
improved. This becomes a measured case on Day 42, and whichever fix moves the number stays.
**Status:** Open, logged, unmeasured.

### Q2 — `obliged_party` is `unspecified` on every obligation
**Observed:** Day 31, same run.
**Symptom:** All 8 obligations returned `unspecified`.
**Assessment:** Arguably correct behaviour. Article 12 is drafted as a property of systems
("High-risk AI systems shall...") rather than a duty on a named actor, and the prompt
explicitly forbids inferring what the text does not state. But the AI Act does place these
duties on providers elsewhere, so a compliance analyst would expect "provider."
**The real question:** should extraction stay strictly literal, or resolve the obliged party
from surrounding context? Literal is defensible and auditable. Resolved is more useful and
harder to verify.
**Deliberately unresolved.** Belongs in the golden set as a labelling decision on Day 37 —
what the correct answer *is* must be settled before measuring whether the system gives it.
**Status:** Open, logged, needs a labelling decision before it needs a code change.


### Q5 — Confidence was inverted: the agent was most confident when it was least complete
**Observed:** Day 48, by joining stored agent decisions to the golden labels.

```
auto-filed       n=3   mean precision 1.00   mean recall 0.50   exact 0/3
routed to human  n=6   mean precision 1.00   mean recall 0.78   exact 3/6
```

The cases the agent was most confident about were *less* correct than the ones it declined.

**Cause, in two linked parts.**

*The judgement.* Precision was 1.00 everywhere — no clause was ever wrongly named. Recall was
0.50 on multi-clause obligations. The system prompt contained the line "not_affected — this
will be the correct verdict for most candidates", written to prevent over-matching. It
over-corrected: the agent found the strongest match and marked the rest not_affected. Every
case that achieved recall 1.00 was a single-clause obligation.

*The score.* Confidence was derived largely from retrieval top-score, which measures "is
there one obvious match". For an obligation that engages several clauses, one obvious match
is precisely the condition under which the agent latches onto it and misses the others. So
the signal was **anti-correlated** with the thing it claimed to measure.

**Why it was invisible until measured.** Every individual output looked right. The reasoning
was sound, the citations all validated, precision was perfect. Nothing in any single result
revealed that a second affected clause existed and had been dismissed. Only joining the
decisions to hand-labelled ground truth exposed it.

**Fix applied:** the prompt now states that one obligation usually engages several clauses
across different policies, requires a second pass over the not_affected set looking for the
same duty in different vocabulary, and states the cost asymmetry explicitly — a clause
wrongly included costs seconds, a clause missed is an unmet obligation nobody knows about.

**Deliberately NOT fixed yet:** the confidence formula. Recalibrating it against outcomes
produced by a broken judgement would fit the new formula to the old bug. Re-measure after
the prompt change, then recalibrate.

**Generalisable lesson.** A confidence signal has to be validated against outcomes, not
assumed from plausibility. "Top retrieval score" sounds like a reasonable proxy for
certainty and is in fact a proxy for *one strong match*, which on a completeness task is
closer to a warning sign than a reassurance.
