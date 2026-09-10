"""Turn text into vectors, in batches, with the same failure policy as everything else.

One constraint drives the configuration here and it is easy to discover the hard way:

    pgvector's HNSW and IVFFlat indexes only support vectors up to 2000 dimensions.

Gemini's embedding model returns 3072 by default. Store those and every column works, the
data looks right, and then CREATE INDEX fails — or worse, you skip the index and every
search becomes a full scan that is fast at 131 rows and unusable at 100,000. So the
dimension is requested explicitly at 1536: comfortably under the limit, and these models
are trained so that truncated dimensions remain useful rather than arbitrary.
"""

from __future__ import annotations

import time

from redline.config import require_api_key, settings
from redline.resilience import CallBudget, call_with_backoff

# Must stay below pgvector's 2000-dimension index ceiling.
EMBED_DIM = 1536

# Documents and queries are embedded for different purposes. Telling the model which is
# which measurably improves retrieval on these models — a query is a question, a document
# is an answer, and they should not be projected identically.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"

# Requests per API call.
#
# Day 35 correction: batching does NOT reduce quota consumption here. The free-tier metric
# is `embed_content_free_tier_requests` and the observed limit is 100 PER MINUTE — counted
# per text embedded, not per HTTP call. Four calls of 32 texts consumed 128 requests and
# tripped the limit at item 97. Batch size only controls how much work is lost when a
# batch fails, so it is now smaller.
BATCH_SIZE = 20

# Texts per minute to stay under. Set below the observed free-tier ceiling of 100 so the
# job paces itself instead of sprinting into a 429 and waiting anyway.
RATE_LIMIT_PER_MINUTE = 90


def _client():
    from google import genai

    return genai.Client(api_key=require_api_key())


def _embed_raw(client, texts: list[str], task: str) -> list[list[float]]:
    """The single place the embedding API is touched.

    If your installed google-genai differs, this is the one function to change.
    """
    response = client.models.embed_content(
        model=settings.embedding_model,
        contents=texts,
        config={"task_type": task, "output_dimensionality": EMBED_DIM},
    )
    return [e.values for e in response.embeddings]


def embed_documents(texts: list[str], budget: CallBudget | None = None,
                    on_batch=None, progress: bool = True) -> list[list[float]]:
    """Embed a list of documents, batched and paced.

    `on_batch(start_index, texts_slice, vectors_slice)` is called after EVERY successful
    batch, before the next one is attempted.

    That callback exists because of a real bug on Day 35. The first version returned all
    vectors at the end, and the caller stored them afterwards:

        vectors = embed_documents(texts)     # <- raised on batch 4
        for chunk, vector in zip(...):       # <- never ran
            upsert_chunk(...)

    Ninety-six embeddings were paid for, held in memory, and thrown away when batch four
    hit a quota error — while the program printed "Chunks embedded before this point are
    saved. Re-run to resume." Nothing was saved. The message was worse than useless
    because it was confidently wrong.

    Persisting inside the loop is the only version of "resumable" that is actually true.
    """
    if not texts:
        return []
    client = _client()
    budget = budget or CallBudget(limit=settings.max_embedding_calls)
    vectors: list[list[float]] = []
    window_start = time.monotonic()
    window_count = 0

    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]

        # Pace ahead of the per-minute quota rather than sprinting into it. Being told to
        # wait 37 seconds costs the same as waiting voluntarily, but arrives as an error.
        if window_count + len(batch) > RATE_LIMIT_PER_MINUTE:
            elapsed = time.monotonic() - window_start
            if elapsed < 60:
                pause = 60 - elapsed
                print(f"  [pacing] {window_count} embedded this minute, "
                      f"pausing {pause:.0f}s to stay under the quota")
                time.sleep(pause)
            window_start, window_count = time.monotonic(), 0

        result, _ = call_with_backoff(
            lambda b=batch: _embed_raw(client, b, TASK_DOCUMENT),
            budget,
            label=f"embed {start + 1}-{start + len(batch)} of {len(texts)}",
            rate_limit_is_fatal=False,   # batch work waits; nobody is blocked on it
        )
        if len(result) != len(batch):
            raise RuntimeError(
                f"Embedding API returned {len(result)} vectors for {len(batch)} inputs. "
                "Refusing to continue — misaligned vectors would attach each embedding "
                "to the wrong text, which is undetectable downstream."
            )

        vectors.extend(result)
        window_count += len(batch)
        if on_batch:
            on_batch(start, batch, result)     # persist NOW, not at the end
        if progress:
            print(f"  embedded {len(vectors)}/{len(texts)} "
                  f"({budget.used} API calls, {window_count} this minute)")

    return vectors


def embed_query(text: str, budget: CallBudget | None = None) -> list[float]:
    """Embed one search query, with the query task type rather than the document one."""
    client = _client()
    budget = budget or CallBudget(limit=4)
    result, _ = call_with_backoff(
        lambda: _embed_raw(client, [text], TASK_QUERY), budget, label="embed query"
    )
    return result[0]
