"""Validate generated note structure and source references before persistence.

Structural validity does not establish factual correctness: citations still need
semantic review against the verified PDFs. This module performs no model calls.
"""
from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1)
    page: int = Field(ge=1)


class NotePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=2500)
    citations: list[Citation] = Field(min_length=1, max_length=20)


class ExamInference(NotePoint):
    rationale: str = Field(min_length=1, max_length=1000)


class SummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: list[NotePoint] = Field(min_length=1, max_length=20)
    concepts: list[NotePoint] = Field(min_length=1, max_length=30)
    relationships: list[NotePoint] = Field(max_length=20)
    exam_inferences: list[ExamInference] = Field(max_length=20)
    uncertainties: list[str] = Field(max_length=20)

    def validate_sources(self, page_counts: dict[str, int]) -> None:
        """The caller must derive page counts from verified, selected PDFs."""
        if not page_counts or any(type(count) is not int or count < 1 for count in page_counts.values()):
            raise ValueError("verified source page counts are required")
        for point in (*self.scope, *self.concepts, *self.relationships, *self.exam_inferences):
            for citation in point.citations:
                if citation.source_id not in page_counts or citation.page > page_counts[citation.source_id]:
                    raise ValueError("citation outside verified sources")


def _plain(value: str) -> str:
    # Model/source strings are data, not Markdown links, images, or HTML.
    value = value.replace("\r", " ").replace("\n", " ")
    for char in "\\`*_{}[]<>()#!|":
        value = value.replace(char, "\\" + char)
    return value


def render_note(draft: SummaryDraft, *, filenames: dict[str, str], page_counts: dict[str, int]) -> str:
    draft.validate_sources(page_counts)
    if set(filenames) != set(page_counts):
        raise ValueError("source metadata mismatch")

    def point_line(point: NotePoint) -> str:
        sources = "; ".join(
            f"{_plain(filenames[c.source_id])}, p. {c.page}" for c in point.citations
        )
        return f"- {_plain(point.text)}（來源：{sources}）"

    sections = []
    for title, points in (("今日範圍", draft.scope), ("核心概念", draft.concepts),
                          ("概念關係", draft.relationships)):
        sections.append(f"## {title}\n\n" + "\n".join(point_line(p) for p in points))
    exams = [point_line(p) + f"；推測依據：{_plain(p.rationale)}" for p in draft.exam_inferences]
    sections.append("## 可能考點（推測，非教師承諾）\n\n" + ("\n".join(exams) or "沒有足夠依據提出考點推測。"))
    sections.append("## 不確定之處\n\n" + (
        "\n".join(f"- {_plain(value)}" for value in draft.uncertainties)
        or "模型未列出不確定事項；這不表示內容已經人工核實。"
    ))
    return "\n\n".join(sections) + "\n"
