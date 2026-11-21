import copy

import pytest

from chronos.gmail import GmailError
from chronos.mail_correspondence import requires_mail_context
from test_mail_correspondence import act, propose, setup, thread
from test_mail_workflow import workflow
from test_system import system


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['用剛剛那份草稿回覆', '查看回覆草稿', 'show reply draft'])
async def test_changed_thread_blocks_cached_body_and_marks_stale(workflow, command):
    setup(workflow)
    saved = await propose(workflow)
    workflow.gmail.thread.return_value = thread() + [{**thread()[-1], 'id':'ab13', 'body':'New price; old offer withdrawn', 'received_at':3}]
    result = await act(workflow, command)
    assert workflow.get('reply:ab12')['status'] == 'stale'
    assert saved['body'] not in result
    assert '重新草擬' in result
    workflow.gmail.create_reply_draft.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_draft_reuse_is_review_only_not_permission_to_send(workflow):
    setup(workflow)
    saved = await propose(workflow)
    workflow.ai._generate_output.reset_mock()
    result = await act(workflow, '用剛剛那份草稿回覆')
    assert saved['body'] in result and '沒有寄出' in result
    assert workflow.get('reply:ab12') == saved
    workflow.ai._generate_output.assert_not_awaited()
    workflow.gmail.create_reply_draft.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_freshness_read_never_shows_old_body_or_changes_draft(workflow):
    setup(workflow)
    saved = await propose(workflow)
    workflow.gmail.thread.side_effect = GmailError('temporary failure', 503)
    with pytest.raises(GmailError):
        await workflow.prepare_action('ab12', '查看回覆草稿')
    assert workflow.get('reply:ab12') == saved


@pytest.mark.asyncio
async def test_state_changed_after_read_cannot_render_or_invalidate_new_draft(workflow):
    setup(workflow)
    saved = await propose(workflow)
    workflow.gmail.thread.return_value = thread() + [{**thread()[-1], 'id':'ab13', 'body':'New offer'}]
    action = await workflow.prepare_action('ab12', '用剛剛那份草稿回覆')
    updated = {**saved, 'version':'beef1234', 'body':'New reviewed reply'}
    workflow.patch('reply:ab12', **updated)
    result = action()
    assert workflow.get('reply:ab12') == updated
    assert saved['body'] not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['saving','uncertain','gmail_draft','send_review','sending','send_uncertain','sent','stale','cancelled'])
async def test_nonlocal_review_states_are_preserved_without_remote_probe(workflow, status):
    setup(workflow)
    await propose(workflow)
    workflow.patch('reply:ab12', status=status)
    before = copy.deepcopy(workflow.get('reply:ab12'))
    workflow.gmail.reset_mock()
    result = await act(workflow, '用剛剛那份草稿回覆')
    assert workflow.get('reply:ab12') == before
    assert before['body'] not in result
    assert not workflow.gmail.mock_calls


def test_reuse_requires_bound_mail_context():
    assert requires_mail_context('用剛剛那份草稿回覆')


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['meeting','expired','binding'])
async def test_nonthread_staleness_or_changed_binding_cannot_show_cached_body(workflow, change):
    setup(workflow)
    saved = await propose(workflow)
    if change == 'expired':
        workflow.patch('reply:ab12', created_at=0)
    elif change == 'meeting':
        workflow.patch('meeting:ab12', version='changed', status='cancelled')
    action = await workflow.prepare_action('ab12', '查看回覆草稿')
    if change == 'binding':
        workflow.patch('message:ab12', mail={**thread()[-1], 'thread_id':'other'})
    output = action()
    assert saved['body'] not in str(output)
    assert workflow.get('reply:ab12')['status'] == ('review' if change == 'binding' else 'stale')


def test_webhook_original_reuse_invalidates_once_without_task_or_native_operation(system, monkeypatch):
    from chronos import main
    from test_correspondence_webhook import configure
    from test_mail_webhook import post
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    post(client, 92001, text='草擬回覆：簡潔英文')
    before = mail.get('reply:ab12')
    gmail.thread.return_value = thread() + [{**thread()[-1], 'id':'ab13', 'body':'Offer cancelled', 'received_at':3}]
    for _ in range(2):
        assert post(client, 92002, text='用剛剛那份草稿回覆', card=700).status_code == 200
    assert mail.get('reply:ab12')['status'] == 'stale'
    assert before['body'] not in main.db.get_update(92002)['reply']
    assert gmail.thread.await_count == 2
    gmail.create_reply_draft.assert_not_awaited()
    gmail.send_reviewed_draft.assert_not_awaited()
    assert not tasks.list_open()
