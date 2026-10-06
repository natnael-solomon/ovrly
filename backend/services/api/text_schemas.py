"""Backend-only v1 completed-upload text handoff; not the live-capture chunk protocol."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.api.schemas import Sha256

Count = Annotated[int, Field(ge=0, le=1_000_000)]
Coordinate = Annotated[int, Field(ge=0, le=10_000)]


class TextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TextRecognizer(TextModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)


class TextSampling(TextModel):
    policy: Literal["change_triggered"]
    probe_interval_ms: int = Field(ge=1, le=60_000)
    thumbnail_edge: int = Field(ge=1, le=1024)
    change_threshold: int = Field(ge=0, le=255)
    heartbeat_ms: int = Field(ge=1, le=600_000)
    max_frames_per_minute: int = Field(ge=1, le=6000)
    frame_long_edge: int = Field(ge=1, le=8192)


class TextSource(TextModel):
    protocol_version: Literal[1]
    upload_id: UUID
    source_sha256: Sha256
    timebase: Literal["media"]
    rotation_degrees: Literal[0]
    box_space: Literal["normalized_10000"]
    recognizer: TextRecognizer | None = None
    sampling: TextSampling | None = None

    @field_validator("protocol_version", "rotation_degrees", mode="before")
    @classmethod
    def integer_constants(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Protocol and rotation must be integer constants")
        return value


class TextObservation(TextModel):
    id: UUID
    text: str = Field(min_length=1, max_length=4096)
    box: list[Coordinate] = Field(min_length=4, max_length=4)
    frame_pts: Count

    @model_validator(mode="after")
    def valid_box(self) -> "TextObservation":
        left, top, right, bottom = self.box
        if left >= right or top >= bottom or not self.text.strip():
            raise ValueError("Text requires a nonempty box and nonblank wording")
        return self


class TextFrame(TextModel):
    frame_pts: Count
    status: Literal["recognized", "no_text_regions", "failed"]
    regions: int = Field(ge=0, le=100)
    recognition_ms: Count
    failed_regions: int = Field(ge=0, le=100)
    text_observations: list[TextObservation] = Field(max_length=100)

    @model_validator(mode="after")
    def consistent_outcome(self) -> "TextFrame":
        if self.failed_regions > self.regions:
            raise ValueError("Failed regions exceed attempted regions")
        if self.status == "no_text_regions" and (
            self.regions or self.failed_regions or self.text_observations
        ):
            raise ValueError("No-text-regions is a heuristic outcome, not recognition")
        if self.status == "failed" and (
            not self.regions or self.failed_regions != self.regions or self.text_observations
        ):
            raise ValueError("Failed frames have no successful recognition regions")
        if self.status == "recognized" and self.regions <= self.failed_regions:
            raise ValueError("Recognized frames require a successful region")
        if any(item.frame_pts != self.frame_pts for item in self.text_observations):
            raise ValueError("Observation timestamps must match their frame")
        return self


class TextBatch(TextSource):
    frames: list[TextFrame] = Field(max_length=100)


class TextCompletion(TextSource):
    batch_count: int = Field(ge=0, le=64)
    dropped_frames: Count
    capped_frames: Count
    unfinished_frames: Count


class TextBatchRead(TextModel):
    batch_id: int = Field(ge=0, le=63)
    body: TextBatch


class DeviceTextRead(BaseModel):
    protocol_version: Literal[1] = 1
    investigation_id: UUID
    upload_id: UUID
    source_sha256: Sha256
    timebase: Literal["media"] = "media"
    status: Literal["not_started", "receiving", "completed", "cancelled"]
    job_id: UUID | None
    batches: list[TextBatchRead]
    completion: TextCompletion | None
