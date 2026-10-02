"""Scoring for retrieval.

Written from scratch rather than pulled from a framework, for the same reason the golden
set is hand-labelled: this is about 150 lines, and knowing exactly what each number counts
is the difference between reporting a metric and understanding one.

Three metrics, because each hides something the others reveal.

  hit@k     Did ANY correct clause appear in the top k? Generous. A case with three
            correct answers scores 1.0 for finding one of them. Useful as a floor, and
            flattering enough that it should never be reported alone.

  recall@k  What FRACTION of the correct clauses appeared? Strict, and the one that
            matters here: the agent's job is to find every affected clause, and finding
            one of three means missing two obligations a compliance team must act on.

  MRR       Mean reciprocal rank of the FIRST correct answer. 1.0 at rank 1, 0.5 at rank
            2, 0.1 at rank 10. Measures ranking quality, which the other two ignore —
            an answer at rank 9 counts the same as rank 1 for recall@10 and is far worse
            for anyone reading the list.

Unanswerable cases are scored separately. Averaging them into recall is meaningless
(recall of an empty set is undefined) and hides the number that matters: how confident the
system sounds when it has nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class EvalCase:
    id: str
    query: str
    relevant: list[str]
    note: str = ""
    verified: bool = False

    @property
    def is_unanswerable(self) -> bool:
        return not self.relevant


@dataclass
class CaseResult:
    case: EvalCase
    retrieved: list[str]           # clause refs, best first
    top_score: float
    scores: list[float] = field(default_factory=list)

    def hit_at(self, k: int) -> float:
        return 1.0 if set(self.retrieved[:k]) & set(self.case.relevant) else 0.0

    def recall_at(self, k: int) -> float:
        if not self.case.relevant:
            return 0.0
        found = set(self.retrieved[:k]) & set(self.case.relevant)
        return len(found) / len(self.case.relevant)

    @property
    def reciprocal_rank(self) -> float:
        for i, ref in enumerate(self.retrieved, 1):
            if ref in self.case.relevant:
                return 1.0 / i
        return 0.0

    @property
    def first_hit_rank(self) -> int | None:
        for i, ref in enumerate(self.retrieved, 1):
            if ref in self.case.relevant:
                return i
        return None

    @property
    def missed(self) -> list[str]:
        return [r for r in self.case.relevant if r not in self.retrieved]


def load_cases(path: Path) -> list[EvalCase]:
    """Load cases, skipping metadata rows.

    A row whose keys all begin with "_" is documentation rather than a case — the label
    definition and the contamination warning live in the file itself, so anyone reading
    the golden set sees the caveats without having to find a separate document.
    """
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if all(k.startswith("_") for k in d):
            continue
        cases.append(EvalCase(id=d["id"], query=d["query"], relevant=d["relevant"],
                              note=d.get("note", ""), verified=d.get("verified", False)))
    return cases


def score(results: list[CaseResult], ks: tuple[int, ...] = (1, 3, 5, 10)) -> dict:
    """Aggregate. Answerable and unanswerable cases are reported apart, never averaged."""
    answerable = [r for r in results if not r.case.is_unanswerable]
    unanswerable = [r for r in results if r.case.is_unanswerable]

    summary: dict = {
        "cases": len(results),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "verified": sum(1 for r in results if r.case.verified),
    }

    for k in ks:
        summary[f"hit@{k}"] = (
            round(sum(r.hit_at(k) for r in answerable) / len(answerable), 3)
            if answerable else None
        )
        summary[f"recall@{k}"] = (
            round(sum(r.recall_at(k) for r in answerable) / len(answerable), 3)
            if answerable else None
        )
    summary["mrr"] = (
        round(sum(r.reciprocal_rank for r in answerable) / len(answerable), 3)
        if answerable else None
    )

    # The separation question: does a real hit score higher than a question with no answer?
    if answerable:
        summary["mean_top_score_answerable"] = round(
            sum(r.top_score for r in answerable) / len(answerable), 3)
    if unanswerable:
        summary["mean_top_score_unanswerable"] = round(
            sum(r.top_score for r in unanswerable) / len(unanswerable), 3)
    # Separation needs BOTH kinds. With only one, there is nothing to separate from, and
    # reporting a number would invent a contrast that was never measured.
    if answerable and unanswerable:
        summary["score_separation"] = round(
            summary["mean_top_score_answerable"] - summary["mean_top_score_unanswerable"], 3)
    else:
        summary["score_separation"] = None

    return summary


def write_run(path: Path, label: str, config: dict, results: list[CaseResult]) -> dict:
    """Persist one scored run. Every run is kept, so before-and-after is reconstructable."""
    summary = score(results)
    payload = {
        "label": label,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": config,
        "summary": summary,
        "cases": [
            {
                "id": r.case.id,
                "verified": r.case.verified,
                "relevant": r.case.relevant,
                "retrieved": r.retrieved[:10],
                "first_hit_rank": r.first_hit_rank,
                "missed": r.missed,
                "top_score": round(r.top_score, 3),
                "recall@10": round(r.recall_at(10), 3),
            }
            for r in results
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return summary
