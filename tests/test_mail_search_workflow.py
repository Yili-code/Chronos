import copy

import pytest

from chronos.gmail import GmailError
from chronos.ai import AIError
from chronos.mail_correspondence import ReplyText
from chronos.mail_search import command, prepare, deliver
from test_mail_correspondence import thread
from test_mail_workflow import workflow
from test_system import system


REQUEST='找出報價信，草擬回覆並追蹤，但先不要寄'


def seed(mail):
    messages=thread()
    for message in messages: message['subject']='報價'
    messages[-1]['body']='Quotation USD12,000. Terms are not agreed; please acknowledge receipt.'
    mail.gmail.search_page.return_value=(['ab12'],None)
    mail.gmail.read.side_effect=None
    mail.gmail.read.return_value=messages[-1]
    mail.gmail.thread.return_value=messages
    mail.ai._generate_output.return_value=ReplyText(body='Thank you for the quotation. I confirm receipt.')


@pytest.mark.asyncio
async def test_unique_search_prepares_atomic_local_draft_and_untimed_tracking(workflow):
    seed(workflow)
    assert command(REQUEST)
    action=await prepare(workflow,REQUEST,123)
    assert workflow.get('message:ab12') is None and workflow.get('reply:ab12') is None and workflow.get('followups') is None
    receipt=workflow.db.process_update(95001,action)
    workflow.db.process_update(95001,lambda:pytest.fail('Replayed workflow'))
    state=workflow.get('reply:ab12');tracking=workflow.get('followups')['ab12']
    assert state['status']=='review' and state['to']==['client@example.org']
    assert tracking['status']=='waiting_owner' and tracking['target']=='client@example.org'
    assert tracking['duration']==0 and tracking['due_at'] is None
    assert state['workflow_owner_request']==REQUEST
    assert '僅本機' in receipt['reply'] and '尚未設定提醒時間' in receipt['reply']
    workflow.gmail.create_reply_draft.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    await deliver(workflow,123,95001,receipt)
    assert workflow.db.get_update(95001)['delivered']


@pytest.mark.asyncio
@pytest.mark.parametrize('ids,page',[([],None),(['ab12','ab13'],None),(['ab12'],'more')])
async def test_empty_or_ambiguous_search_has_no_local_partial_results(workflow,ids,page):
    seed(workflow);workflow.gmail.search_page.return_value=(ids,page)
    (await prepare(workflow,REQUEST,123))()
    assert workflow.get('message:ab12') is None and workflow.get('reply:ab12') is None and workflow.get('followups') is None
    workflow.ai._generate_output.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['message:ab12','reply:ab12','followups','contacts','meeting:ab12','search'])
async def test_concurrent_change_aborts_every_local_step(workflow,key):
    seed(workflow)
    action=await prepare(workflow,REQUEST,123)
    workflow.patch(key,version='concurrent')
    before={k:copy.deepcopy(workflow.get(k)) for k in ('message:ab12','reply:ab12','followups')}
    output=action()
    assert '未套用' in str(output)
    assert before=={k:workflow.get(k) for k in before}


@pytest.mark.asyncio
@pytest.mark.parametrize('step',['verify_account','search_page','read','thread'])
async def test_provider_failure_is_not_reported_as_empty_or_success(workflow,step):
    seed(workflow);getattr(workflow.gmail,step).side_effect=GmailError('provider unavailable',503)
    output=(await prepare(workflow,REQUEST,123))()
    assert '失敗' in str(output) or '未能' in str(output)
    assert not workflow.get('reply:ab12') and not workflow.get('followups') and not workflow.get('message:ab12')


@pytest.mark.asyncio
async def test_source_changed_during_generation_never_persists_partial(workflow):
    seed(workflow)
    async def generate(*args):
        messages=copy.deepcopy(workflow.gmail.thread.return_value)
        messages.append({**messages[-1],'id':'ab13','body':'Offer cancelled'})
        workflow.gmail.thread.return_value=messages
        return ReplyText(body='Thank you.')
    workflow.ai._generate_output.side_effect=generate
    output=(await prepare(workflow,REQUEST,123))()
    assert '變更' in str(output)
    assert not workflow.get('reply:ab12') and not workflow.get('followups') and not workflow.get('message:ab12')


@pytest.mark.asyncio
@pytest.mark.parametrize('text',[
    '找出報價信，草擬回覆並追蹤，然後寄出',
    '找出報價信，草擬回覆並追蹤，但先不要寄，BCC boss@example.org',
    '找出報價信，不要草擬回覆並追蹤，但先不要寄',
    '找出報價信，草擬回覆並追蹤 0 天，但先不要寄',
    '找出報價信，草擬回覆並追蹤 91 天，但先不要寄',
    '找出報價信，草擬回覆並追蹤，但先不要寄且刪除原信',
    '找出from:attacker報價信，草擬回覆並追蹤，但先不要寄'])
async def test_unconsumed_or_unsafe_clauses_cannot_be_silently_omitted(workflow,text):
    seed(workflow)
    (await prepare(workflow,text,123))()
    workflow.gmail.search_page.assert_not_awaited()
    workflow.ai._generate_output.assert_not_awaited()
    assert not workflow.get('reply:ab12') and not workflow.get('followups')


@pytest.mark.asyncio
async def test_explicit_duration_and_no_reminder_guess(workflow):
    seed(workflow)
    (await prepare(workflow,'找出報價信，草擬回覆並追蹤 3 天，但先不要寄',123))()
    state=workflow.get('followups')['ab12']
    assert state['duration']==72*3600 and state['due_at']>state['created_at']


@pytest.mark.asyncio
async def test_existing_tracking_and_ai_error_preserve_everything(workflow):
    seed(workflow)
    old={'ab12':{'status':'waiting_other','target':'client@example.org','duration':3600,'due_at':123,'version':'old'}}
    workflow.patch('followups',**old)
    (await prepare(workflow,REQUEST,123))()
    assert workflow.get('followups')==old and not workflow.get('reply:ab12')
    workflow.ai._generate_output.assert_not_awaited()
    workflow.patch('followups',ab12={'status':'cancelled'})
    workflow.ai._generate_output.side_effect=AIError('unavailable')
    before=copy.deepcopy(workflow.get('followups'))
    (await prepare(workflow,REQUEST,123))()
    assert workflow.get('followups')==before and not workflow.get('reply:ab12') and not workflow.get('message:ab12')


@pytest.mark.asyncio
async def test_account_change_aborts_staged_draft(workflow):
    seed(workflow)
    action=await prepare(workflow,REQUEST,123)
    workflow.settings.gmail_account='other@example.com'
    assert '未套用' in str(action())
    assert not workflow.get('reply:ab12') and not workflow.get('followups')


@pytest.mark.asyncio
async def test_followup_capacity_failure_does_not_save_prepared_draft(workflow):
    seed(workflow)
    existing={f'item{n}':{'status':'cancelled','version':str(n)} for n in range(100)}
    workflow.patch('followups',**existing)
    output=(await prepare(workflow,REQUEST,123))()
    assert '未套用' in str(output) and workflow.get('followups')==existing
    assert not workflow.get('message:ab12') and not workflow.get('reply:ab12')
    workflow.ai._generate_output.assert_awaited_once()


def test_parser_does_not_capture_explicit_read_only_queries():
    from chronos.mail_search_workflow import parse,command as composite_command
    assert not composite_command('搜尋郵件：subject:"草擬回覆追蹤"')
    assert parse("Find the quote email, draft a reply and track it, don't send")==('quote','draft a reply',None)


def test_webhook_receipt_retry_delivers_without_repreparing(system,monkeypatch):
    from chronos import main
    from test_correspondence_webhook import configure
    from test_mail_webhook import post
    from chronos.telegram import TelegramError
    client,tasks,bot,gmail,mail=configure(system,monkeypatch)
    seed(mail)
    results=iter([TelegramError('unavailable'),{'ok':True,'result':{'message_id':970}}])
    async def send(*args,**kwargs):
        value=next(results)
        if isinstance(value,Exception):raise value
        return value
    bot.send_message.side_effect=send
    assert post(client,95301,text=REQUEST,card=999).status_code==503
    before=copy.deepcopy(mail.get('reply:ab12'))
    assert post(client,95301,text=REQUEST,card=999).status_code==200
    assert mail.get('reply:ab12')==before and mail.binding(123,970)['context_version']==before['version']
    assert bot.send_message.call_args.kwargs['requires_response'] is True
    gmail.search_page.assert_awaited_once()
    main.ai._generate_output.assert_awaited_once()
    gmail.create_reply_draft.assert_not_awaited();gmail.send_reviewed_draft.assert_not_awaited()
    assert not tasks.list_open()


