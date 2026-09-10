# Retrieval observations — Day 35 baseline (by hand, before measurement)

Three queries run against 131 embedded policy clauses, judged by reading. Day 36 replaces
this with a scored golden set; recorded here because impressions formed before measurement
are worth keeping — they are what the numbers will confirm or contradict.

Model: gemini-embedding-001 · 1536 dimensions · cosine similarity · vector search only.

---

## O1 — The seeded contradiction surfaced correctly

Query: *"how long must audit logs be kept"*

```
0.770  P-004 §3.1  Retention period      "not less than twelve (12) months"
0.753  P-002 §2.2  Audit and access logs "a minimum of six (6) years"
```

Both sides of the Day 34 contradiction appear at ranks 1 and 2. This is the correct
behaviour and it is not obvious: a retrieval system that surfaced only one would hide a
conflict a compliance analyst must see. Verdict: working.

---

## O2 — Results cluster inside a single document

Query: *"what happens when a vendor has a breach"*

```
0.691  P-006 §2.3   <- correct answer
0.654  P-006 §3.2
0.652  P-006 §3.1
0.650  P-006 §1.1
0.641  P-006 §4.2
```

**All five results come from P-006.** P-007 (Incident Response and Breach Notification)
does not appear at all, despite being the other policy a breach touches.

Why this matters: the agent's actual question is "which clauses does this regulation
affect?", and the answer often spans several policies. Five near-neighbours from one
document is high precision and poor coverage. Vector similarity has no notion of
diversity — it returns the k nearest points, and points from one document cluster together
because they share vocabulary, structure and the context prefix we deliberately added.

Candidate fix for Day 40: diversity-aware reranking (MMR), or capping results per policy
before reranking. **Not applied yet** — this is an observation, and Day 41 decides with a
number whether the fix helps.

---

## O3 — Similarity score cannot tell "found it" from "nothing here"

This is the finding that matters most.

```
query                                        top score   answer exists?
"what happens when a vendor has a breach"      0.691      yes, P-006 §2.3
"can we use patient data to train a model"     0.671      NO — no clause answers this
```

**A 0.02 gap between a clean hit and an unanswerable question.** The top result for the
unanswerable query (P-011 §1.1, model scope) is plausible, well-ranked and does not answer
the question. Nothing in the score says so.

The full observed range across all three queries is 0.641–0.770 — everything the system
returns looks moderately confident. Absolute cosine similarity on these embeddings is
compressed into a narrow band and is therefore near-useless as a confidence signal on its
own.

### Margin is better, but not sufficient

Gap between rank 1 and rank 2:

```
0.037   vendor breach          clean single answer
0.017   audit log retention    two clauses genuinely both apply
0.008   patient data / model   no answer exists
```

Margin separates the unanswerable case better than absolute score does. But it is also
small for the audit-log query, where a small margin is *correct* — two clauses really do
both apply. So margin alone would abstain on a question the system answered well.

**Conclusion, carried to Day 47:** no single scalar distinguishes these cases. Confidence
must be derived from several signals together — retrieval margin, self-consistency across
samples, and whether a validator could ground the answer — which is why D5 in ADR-001 says
confidence is derived and not asked for.

---

## What this predicts for Day 36

Recall@10 on straightforward queries should be high; the corpus is small and the context
prefix is doing real work. The interesting numbers will be the queries where the answer
spans policies (O2) and the queries with no answer at all (O3). If the golden set contains
only clean single-answer cases, it will report a flattering number that means nothing.

---

# Day 36 — baseline measured, and a problem with the measurement

```
recall@1   0.648      recall@10  0.944      MRR 1.000
mean top score answerable 0.768 · unanswerable 0.620 · separation 0.148
```

Recorded, committed, unchanged. Then read sceptically.

## O4 — The golden set is contaminated, and the score reflects it

recall@10 of 0.944 on day one, against a Day 41 gate of 0.85. Every answerable case put a
correct clause at rank 1 (MRR 1.000). That is not a system performing well. That is a test
that is too easy, and the cause is structural:

**Claude wrote the corpus and Claude wrote the queries.** Compare B05:

```
query    "Records supporting a claim submitted to a federal health care program
          shall be retained for a period of ten years from the date of service."
clause   "Records supporting a claim submitted to a federal health care program
          shall be retained for a minimum of ten (10) years from the date of service."
```

Near-verbatim. Retrieved at rank 1, similarity 0.816 — as it would be by any method,
including keyword matching. This is measuring near-duplicate detection, not retrieval.

Two consequences, one of which threatens the project narrative:

1. **The number is not credible.** An interviewer who reads the golden set will see the
   same author in both halves within a minute.
2. **There is no headroom.** Days 39–40 add hybrid retrieval and reranking, and the
   before/after table was to be the strongest artifact in the repo. From 0.944 the largest
   possible gain is 0.056, most of it noise at n=10.

**Correction for Day 37: queries come from real regulatory text, not from paraphrase.**
Twenty Federal Register documents are already stored and normalised, and the Day 31
extractor already turns regulatory text into structured obligations. Running that over the
stored documents produces queries in production's own vocabulary, written by federal
drafters rather than by the same author as the corpus. Harder, uncontaminated, and a closer
match to what the system will actually receive.

The Day 41 gate of recall@10 ≥ 0.85 is retained but now applies to that set. This baseline
is kept and labelled for what it is: a floor measured on an easy set.

## O5 — B04 confirms the clustering prediction

The only miss across ten cases:

```
B04   found P-005 §1.2 (minimum necessary)   missed P-003 §1.1 (least privilege)
```

The same obligation stated in two policies, in different vocabulary. Retrieval found the
one whose wording matched and missed the one whose meaning matched. Exactly what Day 35
observation O2 predicted, now with a case id attached.

## O6 — Separation is larger than the hand check suggested, and still not enough

Day 35 measured a 0.02 gap by hand on one pair. Measured properly: **0.148**, unanswerable
at 0.620 against an answerable mean of 0.768.

Better than feared, and still not a usable threshold. The lowest answerable score is B08 at
0.717 and there is one unanswerable case at 0.620 — a gap of 0.097 with a sample of one.
A threshold set between them would be fitted to a single data point. Day 37 needs several
unanswerable cases before this number means anything.
