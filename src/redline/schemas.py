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

from enum import Enum

from pydantic import BaseModel, Field, field_validator


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
