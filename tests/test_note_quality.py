"""Known semantic category failures; these tests are not factual verification."""
import pytest
from chronos.study_notes import SummaryDraft, render_note


def draft(text):
    point = {'text': '課程內容', 'citations': [{'source_id': 'a', 'page': 1}]}
    return SummaryDraft.model_validate(dict(scope=[point], concepts=[point], relationships=[],
        exam_inferences=[{**point, 'text': text, 'rationale': '來源列明課程目標'}], uncertainties=[]))


@pytest.mark.parametrize('text', [
    '期中考預定於 10/30 舉行，期末考預定於 12/24 舉行。',
    '考試日期具有不確定性，可能會隨課程進度進行調整。',
    '評分比重為作業 30%。',
    'Exam schedule may change.',
])
def test_administrative_claim_cannot_render_as_exam_inference(text):
    with pytest.raises(ValueError, match='administrative fact'):
        render_note(draft(text), filenames={'a': 'lecture.pdf'}, page_counts={'a': 1})


def test_topic_prediction_with_rationale_remains_supported():
    result = render_note(draft('考試可能要求追蹤系統呼叫跨越使用者與核心邊界。'),
                         filenames={'a': 'lecture.pdf'}, page_counts={'a': 1})
    assert '系統呼叫' in result
