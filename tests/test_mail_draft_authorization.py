import copy

import pytest

from chronos import main
from chronos.gmail import GmailError
from chronos.mail_correspondence import requires_mail_context
from test_correspondence_webhook import configure
from test_mail_correspondence import act, propose, setup
from test_mail_workflow import workflow
from test_mail_webhook import post
from test_system import system


@pytest.mark.parametrize('text', ['存成 Gmail 草稿就好', '只存成 Gmail 草稿', 'save as a Gmail draft only',
    'Reply all，附件帶上內部財報，BCC 我主管'])
def test_native_and_sensitive_requests_require_mail_context(text):
    assert requires_mail_context(text)


@pytest.mark.asyncio
async def test_no_preview_version_shows_review_without_native_write(workflow):
    setup(workflow)
    saved = await propose(workflow)
    text = await act(workflow, '存成 Gmail 草稿就好')
    assert saved['body'] in text and '沒有寄出' in text
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_preview_save_timeout_stays_uncertain_without_duplicate(workflow):
    setup(workflow)
    saved = await propose(workflow)
    accepted = []
    async def accept_then_timeout(*args, **kwargs):
        accepted.append({'draft_id':'remote-accepted', 'args':args, 'kwargs':kwargs})
        raise GmailError('response timeout', 408)
    workflow.gmail.create_reply_draft.side_effect = accept_then_timeout
    for _ in range(2):
        output = (await workflow.prepare_action('ab12', '存成 Gmail 草稿就好', context_kind='reply', context_version=saved['version']))()
    assert len(accepted) == 1
    assert workflow.get('reply:ab12')['status'] == 'uncertain'
    assert '核對' in output
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_sensitive_reply_all_preserves_state_and_never_reads_attachment(workflow):
    setup(workflow)
    await propose(workflow)
    before = copy.deepcopy(workflow.get('reply:ab12'))
    workflow.ai._generate_output.reset_mock()
    result = await act(workflow, 'Reply all，附件帶上內部財報，BCC 我主管')
    assert '不支援' in result and '附件' in result and 'BCC' in result
    assert 'Email' in result and '授權' in result
    assert workflow.get('reply:ab12') == before
    workflow.gmail.read_attachment.assert_not_awaited()
    workflow.gmail.create_reply_draft.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    workflow.ai._generate_output.assert_not_awaited()


def test_only_actual_review_message_binds_version_for_natural_save(system, monkeypatch):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    assert post(client, 93001, text='草擬回覆：簡潔英文').status_code == 200
    saved = mail.get('reply:ab12')
    assert mail.binding(123,700)['context_version'] == saved['version']
    assert 'inline_keyboard' in bot.send_message.call_args.kwargs['reply_markup']
    assert post(client,93002,text='存成 Gmail 草稿就好',card=700).status_code == 200
    gmail.create_reply_draft.assert_awaited_once()
    gmail.send_reviewed_draft.assert_not_awaited()
    assert not tasks.list_open()


@pytest.mark.parametrize('instruction', ['存成 Gmail 草稿就好', 'Reply all，附件帶上內部財報，BCC 我主管'])
def test_unbound_native_request_never_becomes_generic_task(system, monkeypatch, instruction):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    assert post(client,93003,text=instruction,card=999).status_code == 200
    main.ai.parse.assert_not_awaited()
    gmail.create_reply_draft.assert_not_awaited()
    assert not tasks.list_open()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['needs_information','stale','cancelled'])
async def test_bound_natural_request_cannot_save_incomplete_or_invalid_draft(workflow,status):
    setup(workflow)
    saved = await propose(workflow)
    workflow.patch('reply:ab12',status=status)
    await workflow.prepare_action('ab12','存成 Gmail 草稿就好',context_kind='reply',context_version=saved['version'])
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_preview_version_cannot_save_replacement(workflow):
    setup(workflow)
    saved = await propose(workflow)
    workflow.patch('reply:ab12',version='abcd9999')
    before = copy.deepcopy(workflow.get('reply:ab12'))
    (await workflow.prepare_action('ab12','存成 Gmail 草稿就好',context_kind='reply',context_version=saved['version']))()
    assert workflow.get('reply:ab12') == before
    workflow.gmail.create_reply_draft.assert_not_awaited()


def test_old_preview_binding_is_not_silently_retargeted(system,monkeypatch):
    client,tasks,bot,gmail,mail=configure(system,monkeypatch)
    post(client,93401,text='草擬回覆：英文')
    old=mail.binding(123,700)
    mail.patch('reply:ab12',version='abcd9999')
    post(client,93402,text='存成 Gmail 草稿就好',card=700)
    assert mail.binding(123,700)==old
    gmail.create_reply_draft.assert_not_awaited()
    assert not tasks.list_open()
