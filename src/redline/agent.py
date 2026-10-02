"""The agent: obligation in, drafted amendments out, uncertain cases to a human.

This is the part the README has been describing since Day 30. Everything before it —
ingestion, normalisation, the corpus, retrieval, the evals — exists to make this step
possible and to make its output measurable.

Three design decisions, each with a reason worth being able to defend.

1. RETRIEVAL GATES THE MODEL CALL.
   If nothing is retrieved above the threshold, the agent abstains WITHOUT calling the
   model at all. This is not only a quota saving (though on a 20-call-a-day free tier that
   matters): asking a language model "which of these unrelated clauses is affected?" invites
   it to find one. The cheapest way to avoid a confident wrong answer is not to ask the
   question.

2. CONFIDENCE IS DERIVED, NEVER REQUESTED.  (ADR-001 D5)
   The model is never asked how sure it is. Self-reported certainty correlates with fluency
   rather than correctness. Confidence here is computed from things that can be checked:
   how far the top retrieval score sits above the measured abstention threshold, how much
   daylight there is between the first and second candidate, and whether every citation the
   model produced survived validation.

3. THE THRESHOLD IS MEASURED, NOT CHOSEN.
   0.67 comes from the 27-case merged eval set, where answerable queries scored 0.717-0.825
   and unanswerable ones 0.522-0.620. The ranges do not overlap; 0.67 sits in the gap. On
   Day 35 this looked impossible — but that judgement was made on a contaminated set whose
   negative cases were written by the same author as the corpus. Real regulatory text as the
   negative half made the signal clean. The number is evidence, and it moves when the
   evidence moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from redline.config import require_api_key, settings
from redline.resilience import CallBudget, ModelUnavailable, call_with_backoff
from redline.schemas import ImpactAnalysis, ImpactVerdict, Severity

# Measured on evals/golden-merged.jsonl, 2026-10-02. See the module docstring.
RETRIEVAL_THRESHOLD = 0.67

# Below this, the item goes to a human regardless of what the model concluded.
CONFIDENCE_THRESHOLD = 0.60


class Route(str):
    AUTO = "auto_filed"
    REVIEW = "human_review"


@dataclass
class AgentResult:
    """What happened for one obligation. Everything needed to audit the decision."""

    obligation: str
    route: str
    confidence: float
    reason: str                                   # why it routed the way it did
    analysis: ImpactAnalysis | None = None
    candidates: list[dict] = field(default_factory=list)
    top_score: float = 0.0
    margin: float = 0.0
    citation_failures: list[str] = field(default_factory=list)
    model_calls: int = 0
    attempts: list[str] = field(default_factory=list)

    @property
    def affected_clauses(self) -> list[str]:
        return [a.clause_id for a in self.analysis.affected] if self.analysis else []


SYSTEM_PROMPT = """\
You are a compliance analyst assessing whether a regulatory obligation affects specific
clauses of a company's internal policy library.

You will be given one obligation and a numbered list of candidate clauses retrieved from
that library. Assess EVERY candidate. Do not skip any.

For each clause decide:

  affected       The obligation engages this clause — it imposes a requirement the clause
                 does not meet, contradicts it, or the clause is the company's statement of
                 exactly this duty and must be checked against the new wording.
  not_affected   You read it and it is genuinely about something else.
  ambiguous      You cannot decide from the text in front of you — for example the clause
                 defers to a document you have not been given, or the obligation's scope
                 does not clearly reach this organisation.

Rules:

1. `clause_id` must be copied EXACTLY from the candidate list. Never invent an identifier,
   never cite a clause that was not offered to you.
2. For `affected` clauses only, write `proposed_text`: the complete amended clause, changing
   as little as possible. Preserve the clause's voice and numbering. Do not rewrite it.
3. `reasoning` says why, referring to what the clause actually says and what the obligation
   actually requires. "This clause is related" is not a reason.
4. Jurisdiction matters. An obligation addressed to depository institutions does not affect
   a health-technology company's policies merely because both mention recordkeeping. When
   scope is genuinely unclear, that is `ambiguous`, not `affected`.
5. Do not state how confident you are. That is computed elsewhere from evidence.

6. ONE OBLIGATION USUALLY ENGAGES SEVERAL CLAUSES, OFTEN IN DIFFERENT POLICIES.
   A retention requirement touches the retention policy AND the logging policy. A
   minimum-necessary rule touches the access control policy AND the PHI handling policy.
   Finding the single best match and stopping is the most common way to be wrong here, and
   it is the failure this instruction exists to prevent.

   After you have assessed every candidate, re-read the obligation and ask: does any clause
   I marked not_affected state the same duty in different words, or cover a different part
   of the same requirement? Policies written by different teams describe the same obligation
   using different vocabulary — "least privilege" and "minimum necessary" are the same rule.

   Completeness matters more than tidiness. A compliance team can dismiss a clause you
   included unnecessarily in seconds. A clause you missed is an unmet obligation nobody
   knows about.
"""


def _client():
    from google import genai

    return genai.Client(api_key=require_api_key())


def _call_model(client, prompt: str) -> str:
    interaction = client.interactions.create(
        model=settings.gemini_model,
        input=prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": ImpactAnalysis.model_json_schema(),
        },
    )
    return interaction.output_text


def _format_candidates(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(f"[{i}] {c['ref']}\n    {c['context']}\n    {c['body']}")
    return "\n\n".join(lines)


def _validate_citations(analysis: ImpactAnalysis, candidates: list[dict]) -> list[str]:
    """Every cited clause must be one that was offered. (ADR-001 D4)

    The same guarantee the extractor makes about quotes, applied to clause identifiers. A
    fabricated clause reference in a compliance recommendation is the most damaging output
    this system could produce: it looks authoritative, it would be acted on, and nothing in
    the text reveals that the clause does not exist.
    """
    offered = {c["ref"] for c in candidates}
    return [a.clause_id for a in analysis.assessments if a.clause_id not in offered]


def _derive_confidence(top_score: float, margin: float, analysis: ImpactAnalysis,
                       citation_failures: list[str]) -> tuple[float, str]:
    """Compute confidence from checkable signals. Never ask the model for it.

    Three components, deliberately interpretable rather than tuned:

      retrieval   how far the best candidate sits above the measured threshold, scaled so
                  0.67 maps to 0 and 0.85 maps to 1.
      margin      daylight between the first and second candidate. A tie means retrieval
                  could not distinguish them, so the judgement rests on less than it appears.
      integrity   1.0 if every citation validated and nothing was flagged ambiguous.

    The MINIMUM is taken, not the average. A single broken signal should sink the result:
    averaging lets a strong retrieval score paper over a fabricated citation, which is
    precisely the failure this system exists to prevent.
    """
    retrieval = max(0.0, min(1.0, (top_score - RETRIEVAL_THRESHOLD) / 0.18))
    margin_component = max(0.0, min(1.0, margin / 0.05))

    integrity = 1.0
    notes = []
    if citation_failures:
        integrity = 0.0
        notes.append(f"{len(citation_failures)} fabricated citation(s)")
    if analysis.ambiguous:
        integrity = min(integrity, 0.4)
        notes.append(f"{len(analysis.ambiguous)} clause(s) marked ambiguous")
    if analysis.ambiguity_note:
        integrity = min(integrity, 0.4)
        notes.append("obligation itself is ambiguous")

    confidence = min(retrieval, margin_component, integrity)

    why = (f"retrieval {retrieval:.2f} (top {top_score:.3f}), "
           f"margin {margin_component:.2f} ({margin:.3f}), "
           f"integrity {integrity:.2f}")
    if notes:
        why += " — " + "; ".join(notes)
    return round(confidence, 3), why


def assess(conn, obligation: str, obligation_embedding: list[float],
           retrieve_fn, budget: CallBudget | None = None,
           mode: str = "vector", limit: int = 8) -> AgentResult:
    """Assess one obligation against the policy corpus.

    Args:
        conn: open database connection
        obligation: the obligation text, as the regulator wrote it
        obligation_embedding: its embedding, already computed
        retrieve_fn: redline.retrieval.retrieve, injected so the agent can be tested
            without a database and so retrieval mode stays a run-time choice
        budget: shared call budget; one call per obligation in the normal path
    """
    budget = budget or CallBudget(limit=settings.max_model_calls_per_run)

    candidates = retrieve_fn(conn, obligation, obligation_embedding,
                             mode=mode, corpus="policy", limit=limit)

    if not candidates:
        return AgentResult(obligation=obligation, route=Route.REVIEW, confidence=0.0,
                           reason="retrieval returned nothing")

    scores = [float(c.get("similarity") or 0.0) for c in candidates]
    top_score = scores[0]
    margin = top_score - (scores[1] if len(scores) > 1 else 0.0)

    # Decision 1: gate the model call on retrieval.
    if top_score < RETRIEVAL_THRESHOLD:
        return AgentResult(
            obligation=obligation, route=Route.REVIEW, confidence=0.0,
            reason=(f"no candidate above the measured threshold "
                    f"(best {top_score:.3f} < {RETRIEVAL_THRESHOLD}); "
                    "abstained without calling the model"),
            candidates=candidates, top_score=top_score, margin=margin,
        )

    prompt = (f"{SYSTEM_PROMPT}\n\nOBLIGATION:\n{obligation}\n\n"
              f"CANDIDATE CLAUSES:\n{_format_candidates(candidates)}\n")

    attempts: list[str] = []
    feedback = ""
    for attempt in range(1, settings.max_extraction_attempts + 1):
        full = prompt + (f"\nYour previous attempt was rejected:\n{feedback}\n"
                         "Fix exactly these problems.\n" if feedback else "")
        raw, _ = call_with_backoff(lambda: _call_model(_client(), full), budget,
                                   label="impact assessment", rate_limit_is_fatal=True)
        try:
            analysis = ImpactAnalysis.model_validate_json(raw)
        except ValidationError as e:
            attempts.append(f"attempt {attempt}: schema invalid")
            feedback = str(e)
            continue

        failures = _validate_citations(analysis, candidates)
        if failures:
            attempts.append(f"attempt {attempt}: cited {failures} which were not offered")
            feedback = (f"You cited clause ids that were not in the candidate list: "
                        f"{failures}. Use only the identifiers given to you.")
            continue

        confidence, why = _derive_confidence(top_score, margin, analysis, failures)
        route = Route.AUTO if confidence >= CONFIDENCE_THRESHOLD else Route.REVIEW
        return AgentResult(
            obligation=obligation, route=route, confidence=confidence, reason=why,
            analysis=analysis, candidates=candidates, top_score=top_score, margin=margin,
            model_calls=budget.used, attempts=attempts,
        )

    return AgentResult(
        obligation=obligation, route=Route.REVIEW, confidence=0.0,
        reason=f"no valid grounded assessment after {settings.max_extraction_attempts} attempts",
        candidates=candidates, top_score=top_score, margin=margin,
        model_calls=budget.used, attempts=attempts,
    )
