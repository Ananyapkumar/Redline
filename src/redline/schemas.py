"""Data shapes for regulatory obligations.

These models are the contract between the language model and the rest of the system.
Nothing the model produces enters the pipeline without passing through here first.

Design note (see ADR-001 D3 and D4): every Obligation carries the exact source sentence
it was derived from, in `verbatim_quote`. The extractor checks that quote actually appears
in the source document before accepting the object. That single field is what makes an
extraction auditable — a compliance reviewer can trace any obligation back to the words
that produced it, and a fabricated obligation cannot survive the check.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator


@dataclass(frozen=True)
class SourceDocument:
    """One document as a watcher found it, before any parsing.

    Lives here rather than in store.py so a source module can describe what it returns
    without importing a database driver. Data shapes should not depend on where the data
    happens to be persisted — that coupling is how a project ends up unable to unit-test
    its HTTP layer without a running Postgres.
    """

    source: str
    external_id: str
    title: str
    doc_type: str | None
    published_on: date | None
    html_url: str | None
    pdf_url: str | None
    abstract: str | None
    agencies: list[str]

    @property
    def content_hash(self) -> str:
        """Stable fingerprint of identity, used for deduplication.

        Built from source + external_id + title rather than from the document's full
        text: the watcher only sees metadata, and downloading every document just to
        decide whether it is new would be slow and wasteful. A revised document receives
        a new external_id from the Federal Register, so genuine revisions are still seen.
        """
        payload = f"{self.source}|{self.external_id}|{self.title}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ObligedParty(str, Enum):
    """Who the duty falls on.

    Regulations are precise about this and it changes who must act. `UNSPECIFIED` is a
    legitimate answer, not a failure — some provisions describe a system property rather
    than assigning a duty to a named party.
    """

    PROVIDER = "provider"
    DEPLOYER = "deployer"
    IMPORTER = "importer"
    DISTRIBUTOR = "distributor"
    AUTHORISED_REPRESENTATIVE = "authorised_representative"
    NOTIFIED_BODY = "notified_body"
    MEMBER_STATE = "member_state"
    UNSPECIFIED = "unspecified"


class Modality(str, Enum):
    """The strength of the duty.

    "shall" is not "may", and "shall not" is not "shall". Collapsing these loses the
    entire legal meaning, so the model is forced to choose one.
    """

    MANDATORY = "mandatory"          # shall, must, is required to
    CONDITIONAL = "conditional"      # shall, where X applies
    PERMITTED = "permitted"          # may
    PROHIBITED = "prohibited"        # shall not, must not
    RECOMMENDED = "recommended"      # should


class Obligation(BaseModel):
    """One discrete thing a party is required (or permitted, or forbidden) to do."""

    source_ref: str = Field(
        description=(
            "Precise citation within the source document, e.g. 'Article 12(3)(a)'. "
            "Use the document's own numbering exactly as printed."
        )
    )
    obliged_party: ObligedParty = Field(
        description="Who carries the duty. Use 'unspecified' if the text does not name a party."
    )
    modality: Modality = Field(
        description="Strength of the duty, taken from the operative verb."
    )
    action: str = Field(
        description=(
            "What must be done, as a short verb phrase in the active voice. "
            "e.g. 'retain automatically generated logs'. Under 15 words."
        )
    )
    subject_matter: str = Field(
        description="What the duty operates on, e.g. 'automatically generated logs'."
    )
    applies_to: str = Field(
        description=(
            "The scope condition — which systems or situations this applies to, "
            "e.g. 'high-risk AI systems referred to in Annex III, point 1(a)'. "
            "Write 'all systems in scope of the document' if unrestricted."
        )
    )
    trigger: str | None = Field(
        default=None,
        description=(
            "The event that makes the duty bite, if any, e.g. 'a reasoned request from a "
            "competent authority'. Null when the duty is continuous."
        ),
    )
    duration_or_deadline: str | None = Field(
        default=None,
        description=(
            "Any time requirement, quoted in the document's own units, "
            "e.g. 'at least six months' or 'within 30 days'. Null if none is stated."
        ),
    )
    exceptions: list[str] = Field(
        default_factory=list,
        description="Carve-outs stated in the text. Empty list if none.",
    )
    verbatim_quote: str = Field(
        description=(
            "The exact, unaltered sentence or clause from the source document that this "
            "obligation was derived from. Copy it character for character. Do not "
            "paraphrase, do not join separated passages, do not add ellipses."
        )
    )

    @field_validator("verbatim_quote")
    @classmethod
    def quote_must_be_substantial(cls, v: str) -> str:
        """A quote too short to identify a passage cannot be verified against the source.

        Rejecting these here means the grounding check downstream is never fooled by a
        three-word fragment that happens to appear somewhere in the document.
        """
        if len(v.strip()) < 25:
            raise ValueError(
                f"verbatim_quote is too short to verify ({len(v.strip())} chars, need 25+). "
                "Quote the whole clause, not a fragment."
            )
        return v

    @field_validator("action")
    @classmethod
    def action_must_be_concise(cls, v: str) -> str:
        """Long 'actions' are a symptom of the model restating the clause rather than
        identifying the duty inside it."""
        words = v.split()
        if len(words) > 15:
            raise ValueError(
                f"action is {len(words)} words, maximum 15. State the duty, do not "
                "restate the clause."
            )
        if not v.strip():
            raise ValueError("action cannot be empty.")
        return v.strip()


class ExtractionResult(BaseModel):
    """Everything extracted from one passage.

    The model returns this wrapper rather than a bare list so it has somewhere to put
    an honest 'I found nothing', which is a valid and useful answer.
    """

    obligations: list[Obligation] = Field(
        default_factory=list,
        description="Every distinct obligation in the passage. Empty list if there are none.",
    )
    passage_contains_no_obligations: bool = Field(
        default=False,
        description=(
            "Set true when the passage is definitional, procedural or introductory and "
            "imposes no duty. When true, obligations must be empty."
        ),
    )

    @field_validator("obligations")
    @classmethod
    def no_duplicate_refs(cls, v: list[Obligation]) -> list[Obligation]:
        """The same citation appearing twice means the passage was split inconsistently."""
        refs = [o.source_ref for o in v]
        duplicates = {r for r in refs if refs.count(r) > 1}
        if duplicates:
            raise ValueError(
                f"Duplicate source_ref values: {sorted(duplicates)}. "
                "Each obligation needs its own distinct citation."
            )
        return v


# =============================================================================
# Agent output — Days 44-48
# =============================================================================


class ImpactVerdict(str, Enum):
    """What a regulatory obligation does to one internal policy clause."""

    AFFECTED = "affected"            # the clause must change, or is directly engaged
    NOT_AFFECTED = "not_affected"    # retrieved, read, and genuinely unrelated
    AMBIGUOUS = "ambiguous"          # cannot be decided from the text available


class Severity(str, Enum):
    HIGH = "high"       # non-compliance is likely and consequential
    MEDIUM = "medium"   # the clause needs amendment but exposure is limited
    LOW = "low"         # wording alignment, no substantive gap
    NONE = "none"


class ClauseAssessment(BaseModel):
    """The agent's judgement on one candidate clause."""

    clause_id: str = Field(
        description="Exactly as given in the candidate list, e.g. 'P-002 §2.2'. Never invent one."
    )
    verdict: ImpactVerdict
    severity: Severity = Field(
        default=Severity.NONE,
        description="Only meaningful when verdict is 'affected'. Use 'none' otherwise."
    )
    reasoning: str = Field(
        description=(
            "One or two sentences on WHY, referring to what the clause says and what the "
            "obligation requires. Not a restatement of either."
        )
    )
    proposed_text: str | None = Field(
        default=None,
        description=(
            "When verdict is 'affected': the full amended clause text, ready for review. "
            "Change as little as possible. Null for any other verdict."
        ),
    )

    @field_validator("reasoning")
    @classmethod
    def reasoning_must_be_substantive(cls, v: str) -> str:
        if len(v.strip()) < 30:
            raise ValueError(
                f"reasoning is {len(v.strip())} chars. State why the clause is or is not "
                "engaged — a verdict without a reason cannot be reviewed."
            )
        return v.strip()


class ImpactAnalysis(BaseModel):
    """Everything the agent concluded about one obligation.

    One model call produces this whole object: assessments for every candidate clause plus
    the drafted amendments. Splitting judgement and drafting into separate calls would
    double the quota cost for no gain, because drafting needs the same reasoning that
    produced the verdict.
    """

    assessments: list[ClauseAssessment] = Field(
        description="One entry per candidate clause considered. Do not omit any."
    )
    ambiguity_note: str | None = Field(
        default=None,
        description=(
            "Set only when something about the obligation itself cannot be resolved from "
            "the corpus — e.g. it defers to a document that is not present. Null otherwise."
        ),
    )

    @property
    def affected(self) -> list[ClauseAssessment]:
        return [a for a in self.assessments if a.verdict == ImpactVerdict.AFFECTED]

    @property
    def ambiguous(self) -> list[ClauseAssessment]:
        return [a for a in self.assessments if a.verdict == ImpactVerdict.AMBIGUOUS]
