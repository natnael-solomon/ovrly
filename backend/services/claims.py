"""Source-grounded interpretation vocabulary shared by extraction and its experiment."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Offset = Annotated[int, Field(ge=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceRef(StrictModel):
    observation_id: Text
    start_char: Offset
    end_char: Offset

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("Empty or inverted text span")
        return self


class Interpretation(StrictModel):
    taxonomy: Literal[
        "empirical", "causal", "documentary", "predictive", "normative", "mixed", "unclear"
    ]
    source_refs: Annotated[list[SourceRef], Field(min_length=1)]
    context_refs: list[SourceRef]
    assertion_mode: Literal[
        "asserted", "reported", "questioned", "hypothetical", "counterfactual", "unclear"
    ]
    speaker_commitment: Literal["endorsed", "rejected", "uncommitted", "unclear"]
    attributed_to: Text | None
    eligibility_reason: Literal[
        "factual-claim",
        "factual-premise",
        "opinion",
        "quoted-not-endorsed",
        "insufficient-context",
        "not-a-claim",
    ]
    uncertainty_flags: list[
        Literal[
            "unresolved-reference",
            "missing-context",
            "ambiguous-attribution",
            "ambiguous-commitment",
            "ambiguous-meaning",
            "source-text-conflict",
        ]
    ]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        eligible = self.eligibility_reason in {"factual-claim", "factual-premise"}
        if self.taxonomy == "normative" and eligible:
            raise ValueError("A pure normative judgment is not empirically eligible")
        if self.assertion_mode in {"questioned", "hypothetical"} and eligible:
            raise ValueError("A question or invented scenario is not an asserted factual event")
        if (
            self.eligibility_reason == "quoted-not-endorsed"
            and self.speaker_commitment == "endorsed"
        ):
            raise ValueError("An endorsed claim cannot be excluded as not endorsed")
        if len(set(self.uncertainty_flags)) != len(self.uncertainty_flags):
            raise ValueError("Duplicate uncertainty flags")
        return self


class _Proposition(StrictModel):
    proposition: Text


class Occurrence(Interpretation, _Proposition):
    # Preserve the frozen BE-01 schema's required-field order.
    pass


class Extraction(StrictModel):
    occurrences: list[Occurrence]


class ReconciledClaim(StrictModel):
    claim_id: Text
    proposition: Text
    interpretation: Interpretation
    corrects: Text | None


class Reconciliation(StrictModel):
    updates: Annotated[list[ReconciledClaim], Field(max_length=4096)]
    explanation: Annotated[str, Field(min_length=1, max_length=600, pattern=r"\S")]


def reject_constant(value: str) -> object:
    raise ValueError("Non-finite JSON number")


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_json(text: str) -> object:
    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
