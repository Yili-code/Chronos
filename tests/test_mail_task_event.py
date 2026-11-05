import asyncio
import copy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from chronos.google_calendar import CalendarError
from chronos.mail_task_event import MailTaskIntent, grounded, prepare
from test_mail_workflow import workflow, message

NOW = datetime.fromisoformat('2026-11-05T10:00:00+08:00')
START = '2026-11-08T12:00:00+00:00'
END = '2026-11-08T14:00:00+00:00'
EVIDENCE = '2026年11月08日 12:00 UTC，持續 2 hours'


def event_setup(workflow, **changes):
    source = message(subject='Weekend Practice #31', body=EVIDENCE, snippet='Join the contest')
    workflow.patch('message:ab12', mail=source)
    plan = MailTaskIntent(kind='event', title=source['subject'], start=START, end=END,
        start_evidence=EVIDENCE, end_evidence='2 hours', **changes)
    workflow.ai._generate_output.side_effect = None
    workflow.ai._generate_output.return_value = plan
    workflow.calendar = AsyncMock()
    workflow.calendar.configured = Mock(return_value=True)
    workflow.calendar.busy.return_value = []
    events = {}
    async def read(identifier):
        return copy.deepcopy(events.get(identifier))
    async def insert(body):
        events[body['id']] = copy.deepcopy(body)
        return copy.deepcopy(body)
    workflow.calendar.event.side_effect = read
    workflow.calendar.insert.side_effect = insert
    return plan, events


def text(action):
    return action()['messages'][0]['text']


@pytest.mark.asyncio
async def test_task_books_invitation_at_source_time_without_reading_task(workflow):
    plan, events = event_setup(workflow)
    result = await workflow.prepare_action('ab12', 'task', NOW)
    assert '已加入行事曆' in text(result) and '11/08 20:00' in text(result)
    assert workflow.db.list_open_tasks() == []
    body = workflow.calendar.insert.await_args.args[0]
    assert body['summary'] == 'Weekend Practice #31'
    assert datetime.fromisoformat(body['start']['dateTime']) == datetime.fromisoformat(START)
    assert datetime.fromisoformat(body['end']['dateTime']) == datetime.fromisoformat(END)
    assert 'mail.google.com' in body['description']
    assert body['visibility'] == 'private' and 'attendees' not in body
    workflow.calendar.invite.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    assert '未重複新增' in text(await workflow.prepare_action('ab12', 'task', NOW))
    workflow.calendar.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_end_is_bound_question_and_owner_reply_supplies_facts(workflow):
    plan, events = event_setup(workflow)
    plan.end = None
    prompt = (await workflow.prepare_action('ab12', 'task', NOW))()
    context = prompt['messages'][0]['mail_context']
    assert context['kind'] == 'task_event'
    workflow.calendar.insert.assert_not_awaited()
    assert not workflow.db.list_open_tasks()
    owner = '2026-11-08 20:00–22:00 台北'
    workflow.ai._generate_output.return_value = MailTaskIntent(kind='event', title=plan.title,
        start=START, end=END, start_evidence=owner, end_evidence=owner)
    result = await workflow.prepare_action('ab12', owner, NOW, context_kind='task_event', context_version=context['version'])
    assert '已加入行事曆' in text(result)
    workflow.calendar.insert.assert_awaited_once()
    result = await workflow.prepare_action('ab12', owner, NOW, context_kind='task_event', context_version=context['version'])
    assert '過期' in text(result)
    workflow.calendar.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_conflict_preserves_fixed_time_and_requires_owner_override(workflow):
    plan, events = event_setup(workflow)
    workflow.calendar.busy.return_value = [(datetime.fromisoformat(START), datetime.fromisoformat(END))]
    prompt = (await workflow.prepare_action('ab12', 'task', NOW))()
    assert '衝突' in prompt['messages'][0]['text']
    workflow.calendar.insert.assert_not_awaited()
    context = prompt['messages'][0]['mail_context']
    result = await workflow.prepare_action('ab12', '仍要加入', NOW, context_kind='task_event', context_version=context['version'])
    assert '已加入行事曆' in text(result)
    assert workflow.calendar.insert.await_args.args[0]['start']['dateTime'] == START


@pytest.mark.asyncio
async def test_cancel_and_stale_prompt_never_book(workflow):
    plan, events = event_setup(workflow)
    plan.end = None
    prompt = (await workflow.prepare_action('ab12', 'task', NOW))()
    context = prompt['messages'][0]['mail_context']
    assert '已取消' in text(await workflow.prepare_action('ab12', '取消', NOW, context_kind='task_event', context_version=context['version']))
    assert '過期' in text(await workflow.prepare_action('ab12', '仍要加入', NOW, context_kind='task_event', context_version=context['version']))
    workflow.calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('visible', [True, False])
async def test_ambiguous_insert_reconciles_without_second_write(workflow, visible):
    plan, events = event_setup(workflow)
    async def uncertain(body):
        if visible:
            events[body['id']] = copy.deepcopy(body)
        raise CalendarError('Timeout')
    workflow.calendar.insert.side_effect = uncertain
    assert '尚待核對' in text(await workflow.prepare_action('ab12', 'task', NOW))
    result = await workflow.prepare_action('ab12', 'task', NOW)
    assert ('未重複新增' if visible else '尚待核對') in text(result)
    workflow.calendar.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_clicks_create_at_most_one_calendar_event(workflow):
    plan, events = event_setup(workflow)
    gate = asyncio.Event()
    count = 0
    async def verify():
        nonlocal count
        count += 1
        if count == 2:
            gate.set()
        await gate.wait()
    workflow.calendar.verify_owner.side_effect = verify
    await asyncio.gather(*(workflow.prepare_action('ab12', 'task', NOW) for _ in range(2)))
    workflow.calendar.insert.assert_awaited_once()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_changed_source_before_reservation_causes_no_write(workflow):
    plan, events = event_setup(workflow)
    async def busy(*args):
        workflow.patch('message:ab12', mail=message(body='A changed source'))
        return []
    workflow.calendar.busy.side_effect = busy
    assert '更新' in text(await workflow.prepare_action('ab12', 'task', NOW))
    workflow.calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['wrong_time', 'missing_zone', 'fabricated_quote', 'past', 'calendar_failure', 'ai_failure'])
async def test_uncertain_or_invalid_facts_never_fall_back_to_reading(workflow, change):
    plan, events = event_setup(workflow)
    if change == 'wrong_time':
        plan.start = '2026-11-08T11:00:00+00:00'
    elif change == 'missing_zone':
        workflow.patch('message:ab12', mail=message(subject=plan.title, body='2026年11月08日 12:00'))
        plan.start_evidence = '2026年11月08日 12:00'
    elif change == 'fabricated_quote':
        plan.start_evidence = 'Invented source time'
    elif change == 'past':
        now = datetime.fromisoformat('2026-11-09T00:00:00+00:00')
    elif change == 'calendar_failure':
        workflow.calendar.verify_owner.side_effect = CalendarError('Unavailable')
    else:
        from chronos.ai import AIError
        workflow.ai._generate_output.side_effect = AIError('Unavailable')
    await workflow.prepare_action('ab12', 'task', now if change == 'past' else NOW)
    workflow.calendar.insert.assert_not_awaited()
    assert not workflow.db.list_open_tasks()


def test_english_month_and_utc_conversion_require_matching_source_facts():
    from zoneinfo import ZoneInfo
    quote = 'November 08, 2026 12:00 UTC'
    assert grounded(START, quote, quote, ZoneInfo('Asia/Taipei')) == datetime.fromisoformat(START)
    assert grounded('2026-11-08T12:00:00+08:00', quote, quote, ZoneInfo('Asia/Taipei')) is None


@pytest.mark.asyncio
async def test_real_weekend_practice_wording_uses_mail_year_and_two_hour_duration(workflow):
    plan, events = event_setup(workflow)
    actual = "We invite you to join Weekend Practice #31 this Sunday! It's a short 2-hour contest. The contest will take place on November 08 at 12:00 UTC."
    workflow.patch('message:ab12', mail=message(subject=plan.title, body=actual, date='Wed, 04 Nov 2026 12:00:00 +0000'))
    plan.start_evidence = 'November 08 at 12:00 UTC'
    plan.end_evidence = '2-hour'
    result = await workflow.prepare_action('ab12', 'task', NOW)
    assert '11/08 20:00–11/08 22:00' in text(result)
    workflow.calendar.insert.assert_awaited_once()


def test_yearless_past_date_is_not_rolled_forward():
    from zoneinfo import ZoneInfo
    quote = 'November 08 at 12:00 UTC'
    assert grounded('2027-11-08T12:00:00+00:00', quote, quote, ZoneInfo('Asia/Taipei'), reference=datetime(2026, 11, 9)) is None
