# Internal policy corpus — Kestrel Health Technologies, Inc.

A fictional US health-technology company. Twelve policies, 131 clauses.

**Why health-tech and not fintech:** the Federal Register watcher pulls from HHS, FDA, CMS
and FTC, and the documents it has actually stored are FDA device reclassifications, CMS
Medicaid rules and food-additive orders. A fintech policy library would be untouched by any
of them, and the golden set would have nothing real to label. The corpus is matched to the
regulator output that exists.

## Format

Each file is markdown with a front-matter block and two heading levels:

```
---
id: P-002
title: Data Retention and Disposal Policy
owner: Director of Information Governance
effective: 2024-03-01
last_reviewed: 2025-11-14
domain: information-governance
---

## 2. Retention Periods          <- section

### 2.2 Audit and access logs    <- clause, id becomes "P-002 §2.2"
Clause text.
```

**Clause ids are permanent.** `P-002 §2.2` appears in golden-set labels, in retrieval
results and in every citation the agent produces. Renumbering a clause silently invalidates
every stored label that points at it. Add new clauses at the end of a section rather than
inserting and renumbering.

## Deliberately seeded defects

This corpus contains contradictions between policies, at least one policy past its own
review cadence, clauses that defer to documents outside the corpus, and gaps where no
clause covers an obligation a regulator imposes.

They are here on purpose. Real policy libraries contain exactly these, and the hard cases
in an evaluation set have to come from somewhere — a corpus where every question has one
clean answer measures nothing worth knowing.

**Find them yourself before opening `docs/corpus-defects.md`.** Run:

```
python scripts/review_corpus.py --defects
```

The automated checks catch some. The rest need reading.
