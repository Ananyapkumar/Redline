"""Retrieval strategies, each measurable against the others.

Three modes, added on Days 35, 39 and 40. Every one is selectable at run time so the eval
harness can score them separately — the whole point of Day 36's baseline was to have
something to compare against, and a change that cannot be isolated cannot be attributed.

  vector  cosine nearest neighbours. Understands meaning, blind to exact figures.
  hybrid  vector + keyword, fused by Reciprocal Rank Fusion.
  rerank  hybrid, then Maximal Marginal Relevance for diversity.
"""

from __future__ import annotations

from redline.store import search_chunks, search_keyword, search_vector_with_embeddings

# RRF's damping constant. 60 is the value from the original paper and is not tuned here:
# tuning it before measuring the untuned version would be exactly the mistake Day 36's
# baseline exists to prevent.
RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Combine several ranked lists into one score per document.

    score(d) = sum over rankers of 1 / (k + rank(d))

    The reason this beats averaging the raw scores: cosine similarity and ts_rank_cd are
    not on the same scale and have no meaningful conversion between them — one lives
    around 0.6-0.8, the other is unbounded and depends on term frequency. RRF throws the
    scores away and uses only POSITION, which is comparable across any two rankers.

    A document ranked first by one and absent from the other still scores well, so a
    match that only keyword search can find is not lost.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, ref in enumerate(ranking, 1):
            if ref:
                scores[ref] = scores.get(ref, 0.0) + 1.0 / (k + rank)
    return scores


def as_vector(value) -> list[float]:
    """Coerce whatever the driver handed back into a list of floats.

    Day 40 bug. pgvector columns come back from psycopg as a STRING — "[0.013,-0.2,...]" —
    unless the pgvector type adapter is registered on the connection. The code did
    list(row["embedding"]) and got a list of single CHARACTERS, so MMR's cosine then tried
    to multiply '0' by '.' and raised:

        TypeError: can't multiply sequence by non-int of type 'str'

    The error is loud, which is lucky. The quieter version of this bug is a driver that
    returns something list-shaped but wrong, where similarity numbers come out plausible
    and meaningless.

    Parsing here rather than registering the adapter keeps the fix in one place and makes
    the function work whether the driver is configured or not.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [float(x) for x in value.strip().strip("[]").split(",") if x.strip()]
    return [float(x) for x in value]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def maximal_marginal_relevance(candidates: list[dict], query_embedding: list[float],
                               limit: int, lambda_: float = 0.7) -> list[dict]:
    """Select results that are relevant AND unlike each other.

    Day 36 case B04 is the reason this exists. The query was "limit access to PHI to the
    minimum necessary". Retrieval returned P-005 §1.2 and four more clauses from P-005,
    and missed P-003 §1.1 — the same obligation stated in a different policy in different
    words. Day 35 observation O2 saw the same pattern: five results, all from one document.

    Pure similarity search cannot avoid this. It returns the k nearest points, and points
    from one document cluster together because they share vocabulary, structure, and the
    context prefix we deliberately prepend.

    MMR picks iteratively, each time choosing the candidate that maximises

        lambda * relevance(query, d)  -  (1 - lambda) * max similarity(d, already chosen)

    so a clause nearly identical to one already selected is penalised however relevant it
    is. lambda_=0.7 leans toward relevance; 0.0 would be pure diversity and useless.

    The cost is real and worth stating: MMR can push a genuinely relevant clause down
    because a similar one was chosen first. Day 41 decides with a number whether the
    coverage gained is worth the precision lost.
    """
    if not candidates:
        return []

    pool = list(candidates)
    embeddings = {c["ref"]: as_vector(c["embedding"]) for c in pool if c.get("embedding")}
    selected: list[dict] = []

    while pool and len(selected) < limit:
        best, best_score = None, float("-inf")
        for c in pool:
            # rank_score when the caller supplied one (hybrid fusion), else the raw cosine.
            relevance = float(c.get("rank_score", c.get("similarity", 0.0)) or 0.0)
            emb = embeddings.get(c["ref"])
            if selected and emb:
                redundancy = max(
                    (_cosine(emb, embeddings[s["ref"]])
                     for s in selected if s["ref"] in embeddings),
                    default=0.0,
                )
            else:
                redundancy = 0.0
            score = lambda_ * relevance - (1 - lambda_) * redundancy
            if score > best_score:
                best, best_score = c, score
        selected.append(best)
        pool.remove(best)

    return selected


def retrieve(conn, query: str, query_embedding: list[float], mode: str = "vector",
             corpus: str | None = "policy", limit: int = 10,
             pool: int = 30, lambda_: float = 0.7) -> list[dict]:
    """One entry point, three strategies, so the eval harness can switch by flag."""
    if mode == "vector":
        return search_chunks(conn, query_embedding, corpus=corpus, limit=limit)

    vector_rows = search_vector_with_embeddings(conn, query_embedding, corpus, limit=pool)
    keyword_rows = search_keyword(conn, query, corpus, limit=pool)

    fused = reciprocal_rank_fusion([
        [r["ref"] for r in vector_rows],
        [r["ref"] for r in keyword_rows],
    ])

    by_ref = {r["ref"]: dict(r) for r in vector_rows}
    for r in keyword_rows:
        by_ref.setdefault(r["ref"], dict(r))       # keyword-only hits must survive
    for ref, row in by_ref.items():
        row["fused_score"] = fused.get(ref, 0.0)

    ordered = sorted(by_ref.values(), key=lambda r: r["fused_score"], reverse=True)

    if mode == "hybrid":
        return ordered[:limit]

    if mode == "rerank":
        # MMR needs a relevance number commensurate with its cosine redundancy term, so
        # the fused score is rescaled into [0, 1].
        #
        # Day 40 bug: the first version wrote that rescaled value over `similarity`. The
        # top result then always scored exactly 1.000 — in every mode, for every query,
        # including the ones with no answer at all. That silently destroyed the one metric
        # this golden set exists to measure: whether the system sounds less confident when
        # it has nothing to say.
        #
        # A score must mean the same thing in every retrieval mode or the comparison
        # between modes is meaningless. So the true cosine stays in `similarity`, and
        # MMR's input lives in `rank_score`.
        top = ordered[:pool]
        if top:
            hi = max(r["fused_score"] for r in top) or 1.0
            for r in top:
                r["rank_score"] = r["fused_score"] / hi
        return maximal_marginal_relevance(top, query_embedding, limit, lambda_)

    raise ValueError(f"Unknown retrieval mode {mode!r}. Use vector, hybrid or rerank.")
