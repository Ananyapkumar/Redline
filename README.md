# Redline

An unattended agent that reads new regulatory publications, determines which clauses of an
organisation's internal policy library each change affects, drafts the required amendment,
and **routes the cases it is not confident about to a human instead of guessing.**

> **Status: in development.** Started 2026-09-03. This README will carry measured results
> once there are any. Until then it describes intent, and says so.

---

## The problem

Regulated companies must track a continuous stream of regulatory change and map each change
onto their own policies, controls and procedures. Today a compliance analyst does this by
hand — reading regulator bulletins and cross-referencing an internal policy library.
Horizon scanning and impact mapping account for roughly 30–50% of that analyst's time, the
work does not scale with regulatory volume, and a missed obligation is a financial penalty.

An established commercial category exists (Ascent, Corlytics, Compliance.ai), which confirms
organisations pay for this. The opening is not the category — it is the reliability
characteristics. A compliance team cannot use a tool that is confidently wrong, and cannot
audit one that will not show its working.

## What it does

```
Regulator sources ──▶ Watcher ──▶ Normaliser ──┐
  (2 feeds, hashed,    (fetch,     (PDF/HTML →  │
   deduplicated)        cache)      structured)  │
                                                 ▼
                                          ┌─────────────────────┐
Policy library ──▶ Clause index ─────────▶│     AGENT LOOP      │
  (~25 docs,       (pgvector,             │ 1 extract obligations│
   ~400 clauses)    hybrid + rerank)      │ 2 retrieve clauses   │
                                          │ 3 judge impact, cite │
                                          │ 4 draft, score conf. │
                                          └──────────┬──────────┘
                                                     │
                                    conf ≥ τ ────────┴──────── conf < τ
                                        │                        │
                                        ▼                        ▼
                                   Audit log              Review queue
                                   (auto-filed)           (human decides)

        Tracing, eval harness and golden set wrap the entire loop.
```

## Design commitments

These are decided, not aspirational. Reasoning and rejected alternatives are in
[`docs/ADR-001.md`](docs/ADR-001.md).

| | |
|---|---|
| **Citations are verified, not requested** | Every clause identifier the model returns is checked against the corpus before the output is accepted. A fabricated citation is structurally impossible, not merely discouraged. |
| **The system abstains** | Below a tuned confidence threshold, or on detected ambiguity, an item is routed to a human with a stated reason rather than answered. Hand-off rate is a reported metric. |
| **Every model output is schema-validated** | Pydantic models via structured tool calling, with bounded retry on validation failure. No parsing of prose. |
| **Reliability is designed in, not added** | Retries with backoff, per-run timeouts, idempotency on content hash, a cost ceiling, and a dead-letter table. The system runs unattended; unhandled failure is expensive there. |
| **Quality claims come with measurements** | A hand-labelled golden set, a reproducible eval runner, and — for outputs with no single correct answer — a judge whose agreement with human grading is itself measured and reported. |

## Targets

Nothing below is claimed yet. Each will be filled in with a measured value or marked as missed.

| Property | Target | Measured |
|---|---|---|
| Retrieval recall@10 | ≥ 0.85 | — |
| Hallucinated citations | 0 | — |
| Unattended completion rate | ≥ 90% | — |
| Traced runs before publication | ≥ 1,000 | — |
| Judge–human agreement | ≥ 0.80 | — |
| Cost per run | recorded | — |
| Latency p95 | recorded | — |

## Local setup

Requires Docker Desktop.

```bash
git clone https://github.com/Ananyapkumar/Redline.git
cd Redline
cp .env.example .env          # then add your API key
docker compose up -d
```

Verify the database and the vector extension:

```bash
docker compose exec db psql -U redline -d redline -c "SELECT extname FROM pg_extension;"
```

`vector` should appear in the output.

## Repository layout

```
docs/            Architecture decision records
db/init/         SQL run once on first database start
docker-compose.yml
```

Directories are added as they earn their place, not scaffolded in advance.

## Honest limitations

- The internal policy corpus is constructed rather than drawn from a real organisation, so
  retrieval difficulty may not fully match production. Contradictions and stale clauses are
  deliberately seeded to partially compensate.
- Coverage is limited to two regulatory sources.
- The system proposes amendments. It does not give legal advice, and a qualified human
  approves every change.

## Related work

[**Clause**](https://github.com/Ananyapkumar/clause) — structured extraction from lighting
datasheets, with an evaluation harness reporting 99.4% field accuracy across 162 judgements,
variance analysis against a stated noise floor, and a pre-registered prediction. Redline is
the deliberate step up from it: real inputs instead of synthetic, a multi-step loop instead
of single-shot, unattended operation instead of user-invoked, and abstention instead of
always answering.

## Licence

MIT
