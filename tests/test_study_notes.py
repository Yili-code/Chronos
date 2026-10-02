import pytest
from pydantic import ValidationError

from chronos.study_notes import SummaryDraft, render_note


def payload():
    point = {"text": "Process 與 thread 的資源共享範圍不同。",
             "citations": [{"source_id": "a", "page": 2}]}
    return {"scope": [point], "concepts": [point], "relationships": [point],
            "exam_inferences": [{**point, "rationale": "投影片比較兩者。"}],
            "uncertainties": []}


def test_render_preserves_provenance_and_inference_labels():
    draft = SummaryDraft.model_validate(payload())
    markdown = render_note(draft, filenames={"a": "os.pdf"}, page_counts={"a": 3})
    assert "os.pdf, p. 2" in markdown
    assert "推測，非教師承諾" in markdown
    assert "推測依據：投影片比較兩者。" in markdown
    assert "不表示內容已經人工核實" in markdown


@pytest.mark.parametrize("counts", [{"a": 1}, {"b": 3}, {}, {"a": True}])
def test_rejects_unknown_sources_or_out_of_bounds_pages(counts):
    with pytest.raises(ValueError):
        SummaryDraft.model_validate(payload()).validate_sources(counts)


def test_requires_citations_and_rejects_extra_model_fields():
    value = payload()
    value["scope"][0]["citations"] = []
    with pytest.raises(ValidationError):
        SummaryDraft.model_validate(value)
    with pytest.raises(ValidationError):
        SummaryDraft.model_validate({**payload(), "teacher_preference": "invented"})


def test_generated_links_are_rendered_as_data():
    value = payload()
    value["uncertainties"] = ["[click](https://example.invalid)"]
    markdown = render_note(SummaryDraft.model_validate(value), filenames={"a": "os.pdf"}, page_counts={"a": 3})
    assert "\\[click\\]\\(" in markdown
