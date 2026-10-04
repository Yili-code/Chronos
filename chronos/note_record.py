"""Canonical completed study notes; generation failures belong to job records."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NoteSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1, le=10000)
    uploaded_at: datetime | None = None

    @field_validator("uploaded_at")
    @classmethod
    def aware_upload(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("upload time requires timezone")
        return value


class NoteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    course_id: str = Field(min_length=1, max_length=100)
    course: str = Field(min_length=1, max_length=200)
    class_date: date
    reported_progress: str = Field(min_length=1, max_length=4000)
    sources: list[NoteSource] = Field(min_length=1, max_length=100)
    markdown: str = Field(min_length=1, max_length=100000)
    model: str = Field(min_length=1, max_length=100)
    prompt_version: str = Field(min_length=1, max_length=100)
    generation_status: Literal["completed"] = "completed"
    created_at: datetime
    last_error: None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    segment_index: int | None = Field(default=None, ge=1)
    segment_total: int | None = Field(default=None, ge=1)
    pagination_version: str | None = None

    @model_validator(mode="after")
    def validate_record(self):
        segment = (self.page_start, self.page_end, self.segment_index, self.segment_total, self.pagination_version)
        if any(value is not None for value in segment):
            if any(value is None for value in segment) or len(self.sources) != 1:
                raise ValueError("incomplete segment metadata")
            if not self.page_start <= self.page_end <= self.sources[0].page_count or self.page_end - self.page_start > 3 or self.segment_index > self.segment_total:
                raise ValueError("invalid segment metadata")
        if self.created_at.utcoffset() is None:
            raise ValueError("creation time requires timezone")
        if len({s.source_id for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate selected source")
        if not self.markdown.strip() or not self.reported_progress.strip():
            raise ValueError("empty note content")
        return self

    def export(self) -> tuple[str, bytes]:
        # Stable safe filename, canonical Markdown unchanged; no local export file.
        return f"note-{self.content_fingerprint}.md", self.markdown.encode("utf-8")
