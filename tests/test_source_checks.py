import pytest
from chronos.source_checks import validate_weekday_presence, requires_weekday_check, validate_inference_evidence
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


def inference_draft(quote='Trace a system call across the kernel boundary.'):
    draft = make_draft('系統呼叫')
    from chronos.study_notes import ExamInference
    draft.exam_inferences = [ExamInference.model_validate(dict(
        text='考試可能要求追蹤系統呼叫。', rationale='課程目標包含此技能。',
        citations=[{'source_id': 'a', 'page': 1}],
        evidence=[{'source_id': 'a', 'page': 1, 'quote': quote}]))]
    return draft


def test_excerpt_tolerates_pdf_line_wrap_but_not_invention():
    draft = inference_draft()
    validate_inference_evidence(draft, {'a': ['Trace a system call\nacross the kernel boundary.']})
    with pytest.raises(ValueError, match='absent'):
        validate_inference_evidence(draft, {'a': ['Implement a system call across the kernel boundary.']})


@pytest.mark.parametrize('pages', [{}, {'a': ['']}, {'a': ['Other content',
    'Trace a system call across the kernel boundary.']}])
def test_excerpt_must_exist_on_exact_cited_page(pages):
    with pytest.raises(ValueError):
        validate_inference_evidence(inference_draft(), pages)


def test_missing_excerpt_rejected_but_historical_schema_still_readable():
    draft = inference_draft()
    draft.exam_inferences[0].evidence = []
    with pytest.raises(ValueError, match='requires source excerpts'):
        validate_inference_evidence(draft, {'a': ['Trace a system call across the kernel boundary.']})


def test_quote_presence_does_not_establish_entailment():
    # Explicit counterexample: provenance is necessary, not sufficient.
    draft = inference_draft('Assignments and quizzes account for 30%.')
    validate_inference_evidence(draft, {'a': ['Assignments and quizzes account for 30%.']})


def test_excerpt_cannot_reference_uncited_source():
    draft = inference_draft()
    draft.exam_inferences[0].evidence[0].source_id = 'b'
    with pytest.raises(ValueError, match='outside cited'):
        draft.validate_sources({'a': 1, 'b': 1})
