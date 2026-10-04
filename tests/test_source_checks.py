import pytest
from chronos.source_checks import validate_weekday_presence, requires_weekday_check
from chronos.study_notes import SummaryDraft
from chronos.pdf_validation import isolated_pdf_page_count
from test_pdf_validation import make_pdf


def make_draft(text, page=1):
    point = {'text': text, 'citations': [{'source_id': 'a', 'page': page}]}
    return SummaryDraft.model_validate(dict(scope=[point], concepts=[point],
        relationships=[], exam_inferences=[], uncertainties=[]))


def test_weekday_translation_and_cited_page_boundary():
    draft = make_draft('週五上課。')
    assert requires_weekday_check(draft)
    validate_weekday_presence(draft, {'a': ['Friday class']})
    with pytest.raises(ValueError):
        validate_weekday_presence(draft, {'a': ['No weekday', 'Friday class']})


def test_invented_weekday_in_uncertainty_rejected():
    draft = make_draft('期末考 12/24。')
    draft.uncertainties = ['12/25 為週三。']
    with pytest.raises(ValueError):
        validate_weekday_presence(draft, {'a': ['12/24 Final Exam; 12/25 holiday']})


def test_blank_pdf_text_extraction_is_bounded():
    assert isolated_pdf_page_count(make_pdf(pages=2), extract_text=True) == ['', '']
    with pytest.raises(ValueError):
        isolated_pdf_page_count(make_pdf(pages=5), extract_text=True)
