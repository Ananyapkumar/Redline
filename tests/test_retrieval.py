"""Tests for hybrid fusion and diversity reranking.

Both are ranking algorithms, which means a subtle bug produces a plausible order rather
than an error. These pin the behaviour that makes each one worth having.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.retrieval import (  # noqa: E402
    _cosine, as_vector, maximal_marginal_relevance, reciprocal_rank_fusion,
)


# --- reciprocal rank fusion ---------------------------------------------------

def test_agreement_between_rankers_wins():
    scores = reciprocal_rank_fusion([["A", "B", "C"], ["A", "C", "B"]])
    assert max(scores, key=scores.get) == "A"


def test_a_hit_only_one_ranker_found_survives():
    """The point of hybrid search. An exact figure that only keyword matching finds must
    not be discarded because the vector ranker never saw it."""
    scores = reciprocal_rank_fusion([["A", "B"], ["Z", "A"]])
    assert "Z" in scores and scores["Z"] > 0


def test_position_is_used_not_score_magnitude():
    """Cosine similarity and ts_rank_cd are not on the same scale and have no meaningful
    conversion. RRF uses rank alone, which is comparable across any two rankers."""
    a = reciprocal_rank_fusion([["X", "Y"]])
    b = reciprocal_rank_fusion([["X", "Y"]])
    assert a == b
    assert a["X"] > a["Y"]


def test_empty_rankings_are_harmless():
    assert reciprocal_rank_fusion([[], []]) == {}


def test_k_damps_the_advantage_of_rank_one():
    """A large k flattens the curve, so being first matters less."""
    tight = reciprocal_rank_fusion([["A", "B"]], k=1)
    loose = reciprocal_rank_fusion([["A", "B"]], k=1000)
    assert (tight["A"] - tight["B"]) > (loose["A"] - loose["B"])


# --- MMR ----------------------------------------------------------------------

def _c(ref, sim, emb):
    return {"ref": ref, "similarity": sim, "embedding": emb}


def test_mmr_breaks_up_a_cluster():
    """Day 36 case B04: four near-identical clauses from one policy crowded out the
    genuinely different clause that answered the same obligation."""
    candidates = [
        _c("P-005 §1.2", 0.90, [1.0, 0.0, 0.0]),
        _c("P-005 §2.1", 0.89, [0.99, 0.01, 0.0]),
        _c("P-005 §2.2", 0.88, [0.98, 0.02, 0.0]),
        _c("P-003 §1.1", 0.70, [0.0, 1.0, 0.0]),
    ]
    picked = [c["ref"] for c in maximal_marginal_relevance(candidates, [1.0, 0.0, 0.0], 2)]
    assert picked[0] == "P-005 §1.2"
    assert picked[1] == "P-003 §1.1", "the different clause should beat the near-duplicate"


def test_lambda_one_is_pure_relevance():
    """With no diversity term MMR must reproduce the original ranking exactly."""
    candidates = [
        _c("A", 0.9, [1.0, 0.0]), _c("B", 0.8, [0.99, 0.01]), _c("C", 0.7, [0.0, 1.0]),
    ]
    picked = [c["ref"] for c in
              maximal_marginal_relevance(candidates, [1.0, 0.0], 3, lambda_=1.0)]
    assert picked == ["A", "B", "C"]


def test_mmr_returns_at_most_the_limit():
    cands = [_c(str(i), 0.5, [float(i), 1.0]) for i in range(10)]
    assert len(maximal_marginal_relevance(cands, [1.0, 1.0], 3)) == 3


def test_mmr_handles_missing_embeddings():
    """A keyword-only hit arrives without an embedding. It must not crash the reranker."""
    cands = [{"ref": "A", "similarity": 0.9}, _c("B", 0.8, [1.0, 0.0])]
    assert len(maximal_marginal_relevance(cands, [1.0, 0.0], 2)) == 2


def test_mmr_on_empty_input():
    assert maximal_marginal_relevance([], [1.0], 5) == []


# --- cosine -------------------------------------------------------------------

def test_cosine_basics():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0      # no division by zero


# --- driver coercion ----------------------------------------------------------
# Regression, Day 40: pgvector returns its columns as a string unless the type adapter is
# registered. list() on that gives single characters, and the cosine then multiplies two
# strings. Loud failure this time; the quiet version returns plausible nonsense.


def test_pgvector_string_is_parsed():
    assert as_vector("[0.5,-0.25,1.0]") == [0.5, -0.25, 1.0]


def test_pgvector_string_with_spaces():
    assert as_vector("[0.5, -0.25, 1.0]") == [0.5, -0.25, 1.0]


def test_a_real_list_passes_through():
    assert as_vector([0.5, -0.25]) == [0.5, -0.25]


def test_none_and_empty_are_safe():
    assert as_vector(None) == []
    assert as_vector("[]") == []


def test_mmr_works_on_string_embeddings():
    """The end-to-end shape of the bug: candidates straight from the database."""
    candidates = [
        {"ref": "A", "similarity": 0.9, "embedding": "[1.0,0.0,0.0]"},
        {"ref": "B", "similarity": 0.89, "embedding": "[0.99,0.01,0.0]"},
        {"ref": "C", "similarity": 0.70, "embedding": "[0.0,1.0,0.0]"},
    ]
    picked = [c["ref"] for c in maximal_marginal_relevance(candidates, [1.0, 0.0, 0.0], 2)]
    assert picked == ["A", "C"], "near-duplicate B should lose to the diverse C"


# --- keyword query construction -----------------------------------------------
# Regression, Day 40: websearch_to_tsquery ANDs every term. A forty-word regulatory
# sentence became a forty-term conjunction matching nothing, keyword search returned an
# empty list, and hybrid mode silently produced results identical to vector mode. No error,
# three eval runs reporting the same numbers, and an apparent finding that hybrid "doesn't
# help" when it was never running.

def test_terms_are_or_ed_not_and_ed():
    from redline.store import _or_tsquery
    q = _or_tsquery("records shall be retained for six years")
    assert "|" in q and "&" not in q


def test_noise_words_are_dropped():
    from redline.store import _or_tsquery
    q = _or_tsquery("the provider shall and must have all such records")
    for noise in ("the", "shall", "must", "and", "all", "such"):
        assert f" {noise} " not in f" {q.replace('|', ' ')} "
    assert "provider" in q and "records" in q


def test_duplicates_collapse():
    from redline.store import _or_tsquery
    assert _or_tsquery("records records records").count("records") == 1


def test_punctuation_and_numbers_do_not_break_the_query():
    from redline.store import _or_tsquery
    q = _or_tsquery("§ 310.8(a) — retain logs; notify, within 60 days.")
    assert "retain" in q and "logs" in q and "notify" in q


def test_empty_input_yields_no_query():
    from redline.store import _or_tsquery
    assert _or_tsquery("") == ""
    assert _or_tsquery("the and for") == ""
