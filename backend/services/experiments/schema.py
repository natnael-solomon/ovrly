"""Versioned BE-01 experiment types, not the shared product API contract."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "be01-experimental-v2"
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Offset = Annotated[int, Field(ge=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Envelope(StrictModel):
    start_ms: Offset
    end_ms: Offset
    basis: Literal[
        "user-timed-caption-not-media-verified",
        "coarse-parent-envelope-not-subwindow-timing",
        "source-subtitle-cue-not-media-verified",
    ]

    @model_validator(mode="after")
    def ordered(self) -> "Envelope":
        if self.end_ms <= self.start_ms:
            raise ValueError("Empty or inverted timing envelope")
        return self


class Observation(StrictModel):
    id: Text
    role: Literal["target", "context"]
    text: Text
    source_type: Literal["supplied-caption", "source-subtitle"]
    speaker_id: Text | None
    envelope: Envelope


class Window(StrictModel):
    window_id: Identifier
    context_status: Literal["window-only", "additional-context-supplied"]
    observations: Annotated[list[Observation], Field(min_length=1)]

    @model_validator(mode="after")
    def consistent(self) -> "Window":
        ids = [observation.id for observation in self.observations]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate observation IDs")
        roles = {observation.role for observation in self.observations}
        if "target" not in roles:
            raise ValueError("A window must have target observations")
        if ("context" in roles) != (self.context_status == "additional-context-supplied"):
            raise ValueError("Context declaration does not match observations")
        return self


class Dataset(StrictModel):
    schema_version: Literal["be01-experimental-v2"]
    version: Identifier
    kind: Literal["synthetic", "real"]
    split: Literal["dev"]
    hosted_processing_approved: bool
    provenance: Text
    windows: Annotated[list[Window], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def consistent(self) -> "Dataset":
        if len({window.window_id for window in self.windows}) != len(self.windows):
            raise ValueError("Duplicate window IDs")
        target_texts = [
            "\n".join(item.text for item in window.observations if item.role == "target")
            for window in self.windows
        ]
        if len(set(target_texts)) != len(target_texts):
            raise ValueError("Duplicate target transcript windows")
        observations: dict[str, Observation] = {}
        for window in self.windows:
            for item in window.observations:
                previous = observations.get(item.id)
                if previous is not None and previous.model_dump(
                    exclude={"role"}
                ) != item.model_dump(exclude={"role"}):
                    raise ValueError(
                        "Reused observation ID has different source content or metadata"
                    )
                observations[item.id] = item
        return self


class SourceRef(StrictModel):
    observation_id: Text
    start_char: Offset
    end_char: Offset

    @model_validator(mode="after")
    def ordered(self) -> "SourceRef":
        if self.end_char <= self.start_char:
            raise ValueError("Empty or inverted text span")
        return self


class Occurrence(StrictModel):
    proposition: Text
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
    def consistent(self) -> "Occurrence":
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


class Extraction(StrictModel):
    occurrences: list[Occurrence]


def grounding_errors(output: Extraction, window: Window) -> list[str]:
    observations = {observation.id: observation for observation in window.observations}
    errors: set[str] = set()
    for occurrence in output.occurrences:
        for refs, role in (
            (occurrence.source_refs, "target"),
            (occurrence.context_refs, "context"),
        ):
            seen: set[tuple[str, int, int]] = set()
            for ref in refs:
                observation = observations.get(ref.observation_id)
                if observation is None:
                    errors.add("unknown_observation_id")
                    continue
                if observation.role != role:
                    errors.add("wrong_reference_role")
                if ref.end_char > len(observation.text):
                    errors.add("span_out_of_bounds")
                elif not observation.text[ref.start_char : ref.end_char].strip():
                    errors.add("whitespace_only_span")
                identity = (ref.observation_id, ref.start_char, ref.end_char)
                if identity in seen:
                    errors.add("duplicate_source_span")
                seen.add(identity)
        speakers = {
            observations[ref.observation_id].speaker_id
            for ref in occurrence.source_refs
            if ref.observation_id in observations
        }
        if len(speakers) > 1:
            errors.add("mixed_source_speakers")
    return sorted(errors)
