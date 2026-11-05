import pytest

from chronos.ai import AIError
from chronos.gmail import GmailError
from chronos.mail_screening import MailDecision, decide, digest, evidence_units, format_deadline, format_decision, ground_facts
from test_mail_workflow import workflow, message  # noqa: F401


def information():
    return MailDecision(tier='information', confidence=.96, reason='Routine status update; no owner action.',
                        summary='測試流程已完成，所有檢查通過。', next_step='')


@pytest.mark.asyncio
async def test_readable_text_html_notification_is_actually_classified(workflow):
    workflow.ai._generate_output.return_value=information()
    mail=message(subject='Workflow completed', body='All checks passed. No action required.',
                 alternative_body='All checks passed. View details and unsubscribe.', unsubscribe=False)
    result=await decide(workflow,mail,{})
    workflow.ai._generate_output.assert_awaited_once()
    assert result.tier=='information'


@pytest.mark.asyncio
async def test_nonbulk_protected_receipt_does_not_become_owner_decision(workflow):
    workflow.ai._generate_output.return_value=information()
    result=await decide(workflow,message(subject='Payment receipt',body='Payment received.',
                                        unsubscribe=False,labels=['INBOX','UNREAD']),{})
    assert result.tier=='information'


@pytest.mark.asyncio
async def test_attachment_or_truncation_does_not_hide_readable_notification(workflow):
    workflow.ai._generate_output.return_value=information()
    result=await decide(workflow,message(subject='Project build complete',body='Build passed.',
        body_incomplete=True,body_truncated=True,attachments=[{'filename':'logo.png'}]),{})
    workflow.ai._generate_output.assert_awaited_once()
    assert result.tier=='information'


@pytest.mark.asyncio
async def test_provider_outage_pauses_instead_of_inventing_owner_decision(workflow):
    workflow.ai._generate_output.side_effect=AIError('503')
    with pytest.raises(GmailError):
        await decide(workflow,message(),{})
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_card_has_concrete_facts_action_deadline_and_no_internal_warnings(workflow):
    workflow.ai._generate_output.return_value=MailDecision(tier='important',confidence=.97,
        reason='PROTECTED keyword internal explanation',summary='面試邀請',next_step='回覆可出席的時段。',
        action_required=True,source_kind='personal',facts=['軟體實習面試採線上形式。','可選11/10上午或11/11下午。'],
        fact_evidence=['軟體實習面試採線上形式。','可選11/10上午或11/11下午。'],
        deadline='11/08前回覆',missing_information=['會議連結尚未提供。'])
    mail=message(subject='Software internship interview',body='軟體實習面試採線上形式。可選11/10上午或11/11下午。',
        body_incomplete=True,alternative_body='<different rendering>',unsubscribe=False)
    result=await decide(workflow,mail,{})
    assert result.tier=='important'
    card=format_decision(mail,result,workflow.settings.gmail_account)
    assert all(text in card for text in ['軟體實習面試','11/10上午','回覆可出席','11/08前回覆','會議連結尚未提供'])
    assert all(text not in card for text in ['PROTECTED','不同版本','未自動清理','分類把握','寄件者標頭'])


@pytest.mark.asyncio
async def test_generic_reading_suggestion_is_not_a_required_owner_action(workflow):
    workflow.ai._generate_output.return_value=MailDecision(tier='important',confidence=.97,
        reason='A newsletter arrived',summary='工具更新介紹。',next_step='請查看郵件',action_required=True)
    result=await decide(workflow,message(),{})
    assert result.tier=='information'
    assert not result.action_required and not result.next_step


@pytest.mark.asyncio
async def test_review_requires_a_specific_owner_question(workflow):
    workflow.ai._generate_output.return_value=MailDecision(tier='review',confidence=.8,
        reason='incomplete HTML',summary='本週活動彙整。',next_step='')
    assert (await decide(workflow,message(),{})).tier=='information'
    workflow.ai._generate_output.return_value=MailDecision(tier='review',confidence=.9,
        reason='Two conflicting offered times',summary='對方提供兩個互斥時段。',next_step='',
        owner_question='請選11/10上午或11/11下午其中一個面試時段。',facts=['兩個時段都需要你先確認。'],
        fact_evidence=['兩個時段都需要你先確認。'])
    assert (await decide(workflow,message(body='兩個時段都需要你先確認。'),{})).tier=='review'


@pytest.mark.asyncio
async def test_ungrounded_specific_facts_are_not_pushed(workflow):
    workflow.ai._generate_output.return_value=MailDecision(tier='information',confidence=.98,
        reason='event invitation',summary='週末活動',next_step='',facts=['免費參加。'],fact_evidence=['Registration is free.'])
    with pytest.raises(GmailError,match='source evidence'):
        await decide(workflow,message(body='Registration costs 1000 TWD.'),{})
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_ids_expand_to_original_spans_without_model_quote_copying(workflow):
    mail=message(subject='Registration confirmation',body='The workshop is on November 22. Admission is free.')
    units=evidence_units(mail)
    selected=next(unit for unit in units if 'Admission is free' in unit['text'])
    workflow.ai._generate_output.return_value=MailDecision(tier='information',confidence=.96,
        reason='Registration confirmed',summary='活動確認通知。',next_step='',
        facts=['活動已確認，免費入場。'],fact_evidence_ids=[[selected['id']]])
    result=await decide(workflow,mail,{})
    assert result.tier=='information' and result.fact_evidence==[selected['text']]


def test_nonexistent_source_references_are_rejected():
    choice=MailDecision(tier='information',confidence=.99,reason='event',summary='活動',next_step='',
                        facts=['免費入場。'],fact_evidence_ids=[[512]])
    with pytest.raises(GmailError,match='source evidence'):
        ground_facts(message(),choice)


def test_evidence_spans_preserve_large_source_and_have_stable_bounded_ids():
    mail=message(body=('First paragraph. ' * 800)[:12000],alternative_body='Alternate date November 22.')
    units=evidence_units(mail)
    assert units==evidence_units(mail)
    assert len(units)<512 and all(len(unit['text'])<=260 for unit in units)
    assert any('Alternate date November 22' in unit['text'] for unit in units)
    assert units[-1]['id']==len(units)


def test_deadline_display_removes_machine_timestamp_clutter_without_inference():
    assert format_deadline('2026-11-07 08:00:00')=='2026/11/07 08:00'
    assert format_deadline('2026-11-07')=='2026/11/07'
    assert format_deadline('比賽開始前五分鐘')=='比賽開始前五分鐘'
    assert format_deadline('11/07 08:00 UTC')=='11/07 08:00 UTC'


@pytest.mark.asyncio
async def test_digest_preserves_event_cost_and_eligibility_in_later_facts(workflow):
    day='daily:2026-11-05'
    mail=message(subject='<Workshop> (abcdef123)',body='Event announcement')
    choice=MailDecision(tier='information',confidence=.96,source_kind='newsletter',
        reason='Optional event',summary='Optional workshop',next_step='',deadline='11/08',
        facts=['主題為軟體測試。','11/10於線上舉行。','大學生可參加。','免費，限50名。'])
    workflow.patch('message:ab12',mail=mail)
    workflow.patch(day+':ab12',screening_done=True,screening=choice.model_dump())
    await digest(workflow,day,{'ids':['ab12'],'more':False})
    call=workflow.telegram.send_message.call_args
    assert call.kwargs['parse_mode']=='HTML'
    assert all(fact in call.args[1] for fact in choice.facts)
    assert '&lt;Workshop&gt;' in call.args[1]
    assert 'abcdef123' not in call.args[1]
    assert '11/08' in call.args[1] and '電子報與活動' in call.args[1]
    assert 'mail.google.com' in call.args[1]  # Linked title, not a separate raw URL line.


@pytest.mark.asyncio
async def test_expired_events_are_labeled_without_exposing_batch_identifiers(workflow):
    day='replay:2026-11-05:private-batch-token'
    workflow.patch('message:ab12',mail=message())
    choice=information().model_copy(update={'expired':True,'source_kind':'newsletter'})
    workflow.patch(day+':ab12',screening_done=True,screening=choice.model_dump())
    await digest(workflow,day,{'ids':['ab12'],'more':False})
    text=workflow.telegram.send_message.call_args.args[1]
    assert '過期資訊，無需處理' in text
    assert '11/05' in text and 'private-batch-token' not in text


@pytest.mark.asyncio
async def test_old_plain_digest_snapshot_keeps_its_original_delivery_contract(workflow):
    workflow.patch('daily:2026-11-05:screening-digest',pages=['Old plain A & B'])
    await digest(workflow,'daily:2026-11-05',{'ids':[],'more':False})
    call=workflow.telegram.send_message.call_args
    assert call.args[1]=='Old plain A & B' and call.kwargs['parse_mode'] is None
