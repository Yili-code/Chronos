import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.page_segments import page_segments
from chronos.pdf_validation import isolated_pdf_page_count
from chronos.summary_pipeline import PdfInput, generate_selected_segments
from chronos.study_materials import MaterialSelection, PdfMaterial
from chronos.db import Database
from test_pdf_validation import make_pdf


def test_partitions_cover_every_page_once():
    for count in range(1, 1001):
        spans = page_segments(count)
        assert [p for a, b in spans for p in range(a, b + 1)] == list(range(1, count + 1))
        assert all(b - a == 2 for a, b in spans[:-1])
    assert page_segments(8, boundaries=(4, 6)) == ((1, 4), (5, 6), (7, 8))


@pytest.mark.parametrize("count", [0, -1, True, 1001, 3.0])
def test_invalid_counts(count):
    with pytest.raises(ValueError):
        page_segments(count)


def test_slice_is_actual_small_pdf():
    sliced = isolated_pdf_page_count(make_pdf(pages=8), page_range=(4, 6))
    assert isolated_pdf_page_count(sliced) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['category', 'weekday'])
async def test_category_failure_never_persists_sends_or_retries(tmp_path, failure):
    db = Database(tmp_path / 'quality.db')
    db.initialize()
    data = make_pdf(pages=3)
    item = PdfMaterial('a', 'course', 'lecture.pdf', None, hashlib.sha256(data).hexdigest())
    selection = MaterialSelection('session', 'course', 'lecture', (item,)).choose('a', selected=True).confirm()
    point = {'text': '課程概念', 'citations': [{'source_id': 'a', 'page': 1}]}
    generator, bot = AsyncMock(), AsyncMock()
    generator.generate.return_value = {'scope': [point], 'concepts': [point], 'relationships': [],
        'exam_inferences': [{**point, 'text': '期末考預定於 12/24 舉行。', 'rationale': '表格明載日期'}],
        'uncertainties': []}
    if failure == 'weekday':
        generator.generate.return_value['exam_inferences'] = []
        generator.generate.return_value['uncertainties'] = ['12/25 為週三。']
    now = datetime.now(timezone.utc)
    args = dict(selection=selection, pdfs={'a': PdfInput(data, 3)}, course='OS', class_date=now.date(),
                chat_id=123, model='test', prompt_version='quality-test', now=now)
    assert await generate_selected_segments(db, generator, bot, **args) == 'uncertain'
    assert db.list_study_notes() == []
    bot.send_message.assert_not_awaited()
    assert await generate_selected_segments(db, generator, bot, **args) == 'uncertain'
    assert generator.generate.await_count == 1
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_second", [False, True])
@pytest.mark.parametrize("two_files", [False, True])
async def test_segments_persist_send_and_resume_without_duplicates(tmp_path, reject_second, two_files):
    db = Database(tmp_path / "segments.db")
    db.initialize()
    data = make_pdf(pages=5)
    item = PdfMaterial("a", "course", "lecture.pdf", None, hashlib.sha256(data).hexdigest())
    selection = MaterialSelection("session", "course", "lecture", (item,)).choose("a", selected=True).confirm()
    if two_files:
        second = PdfMaterial("b", "course", "second.pdf", None, item.sha256)
        selection = MaterialSelection("session", "course", "lecture", (second, item)).choose("a", selected=True).choose("b", selected=True).confirm()
    point = {"text": "重點", "citations": [{"source_id": "a", "page": 1}]}
    generator, bot = AsyncMock(), AsyncMock()
    generator.generate.return_value = {"scope": [point], "concepts": [point], "relationships": [point],
                                      "exam_inferences": [], "uncertainties": []}
    draft = generator.generate.return_value
    import copy
    second_draft = copy.deepcopy(draft)
    for section in ('scope', 'concepts', 'relationships'):
        second_draft[section][0]['citations'][0]['source_id'] = 'b'
    generator.generate.side_effect = [draft, draft] + ([second_draft, second_draft] if two_files else [])
    if reject_second:
        from chronos.summary_pipeline import GenerationRejected
        generator.generate.side_effect = [draft, GenerationRejected(), draft] + ([second_draft, second_draft] if two_files else [])
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 9}}
    now = datetime.now(timezone.utc)
    args = dict(selection=selection, pdfs={"a": PdfInput(data, 5)}, course="OS", class_date=now.date(),
                chat_id=123, model="test", prompt_version="study-segment-v2", now=now)
    if two_files:
        args['pdfs']['b'] = PdfInput(data, 5)
    first = await generate_selected_segments(db, generator, bot, **args)
    if reject_second:
        from datetime import timedelta
        assert first == "retry"
        assert bot.send_message.await_count == 1
        args['now'] += timedelta(minutes=6)
    else:
        assert first == "sent"
    assert await generate_selected_segments(db, generator, bot, **args) == "sent"
    assert generator.generate.await_count == (3 if reject_second else 2) + (2 if two_files else 0)
    assert bot.send_message.await_count == (4 if two_files else 2)
    calls = generator.generate.await_args_list
    assert all(len(call.kwargs['pdfs']) == 1 for call in calls)
    assert [next(iter(call.kwargs['pdfs'].values())).page_count for call in calls] == (([3, 2, 2] if reject_second else [3, 2]) + ([3, 2] if two_files else []))
    messages = str(bot.send_message.await_args_list)
    assert "p. 4–5" in messages and "p. 4" in messages
