import copy

import pytest

from chronos.mail_recipients import envelope, transport_options
from test_mail_correspondence import setup, act, propose, thread
from test_mail_send import saved
from test_mail_workflow import workflow


async def with_roles(workflow):
    setup(workflow)
    state = await propose(workflow)
    text = await act(workflow, f"設定回覆收件者 {state['version']}：CC：boss@example.org；BCC：private@example.net")
    return workflow.get("reply:ab12"), text


@pytest.mark.asyncio
async def test_explicit_cc_bcc_are_previewed_without_flattening_roles_or_api_writes(workflow):
    state, text = await with_roles(workflow)
    assert state["to"] == ["client@example.org"] and state["cc"] == ["boss@example.org"]
    assert state["bcc"] == ["private@example.net"]
    assert "CC：boss@example.org" in text and "BCC（僅你可見" in text and "private@example.net" in text
    workflow.gmail.create_reply_draft.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_recipient_change_invalidates_old_native_save_code(workflow):
    setup(workflow)
    old = await propose(workflow)
    await act(workflow, f"設定回覆收件者 {old['version']}：CC：boss@example.org")
    await act(workflow, "儲存 Gmail 草稿 " + old["version"])
    workflow.gmail.create_reply_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["version"] != old["version"]


@pytest.mark.asyncio
async def test_native_create_and_send_keep_the_entire_approved_envelope(workflow):
    state, text = await with_roles(workflow)
    assert "尚未寄出" in await act(workflow, "儲存 Gmail 草稿 " + state["version"])
    options = transport_options(state)
    assert workflow.gmail.create_reply_draft.call_args.kwargs == options
    workflow.gmail.read_plain_draft.return_value = {**{k: state[k] for k in ("recipient", "subject", "body", "parent_id", "thread_id")}, **envelope(state), "message_id": "native1"}
    workflow.gmail.send_reviewed_draft.return_value = "sent1"
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    reviewed = workflow.get("reply:ab12")
    assert workflow.gmail.read_plain_draft.call_args.kwargs == {"expected_envelope": envelope(state)}
    assert "SENT" in await act(workflow, "確認寄出回覆 " + reviewed["send_token"])
    assert workflow.gmail.send_reviewed_draft.call_args.kwargs == options


@pytest.mark.asyncio
async def test_reply_all_uses_visible_headers_and_excludes_owner_aliases(workflow):
    setup(workflow)
    messages = thread()
    messages[0].update(sender="alias@example.com", labels=["SENT"])
    messages[-1].update(to=["owner@example.com", "teammate@example.org", "client@example.org"],
                        cc=["alias@example.com", "manager@example.org", "teammate@example.org"])
    workflow.gmail.thread.return_value = messages
    await act(workflow, "回覆全體草稿：英文，先確認條件")
    state = workflow.get("reply:ab12")
    assert state["to"] == ["client@example.org", "teammate@example.org"]
    assert state["cc"] == ["manager@example.org"] and state["bcc"] == []


@pytest.mark.asyncio
async def test_outgoing_thread_followup_targets_original_to_not_owner(workflow):
    setup(workflow)
    messages = thread()
    messages[-1].update(sender="owner@example.com", labels=["SENT"], to=["client@example.org"])
    workflow.gmail.thread.return_value = messages
    await act(workflow, "草擬回覆：英文，禮貌詢問是否需要補充資料")
    assert workflow.get("reply:ab12")["recipient"] == "client@example.org"


@pytest.mark.asyncio
async def test_source_header_ambiguity_does_not_silently_choose_recipients(workflow):
    setup(workflow)
    messages = thread()
    messages[-1].update(to=["other@example.org"], envelope_ambiguous=True)
    workflow.gmail.thread.return_value = messages
    await act(workflow, "回覆全體草稿：簡潔英文")
    workflow.ai._generate_output.assert_not_awaited()
    assert not workflow.get("reply:ab12")


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    "CC：boss@example.org；CC：other@example.org", "BCC：client@example.org", "To：", "To：not-an-address",
    "CC：boss@example.org\nBcc: hidden@example.net", "CC：ali\x00ce@example.org", "CC：user@раypal.example",
])
async def test_invalid_or_injected_recipient_edits_preserve_original_state(workflow, fields):
    setup(workflow)
    state = await propose(workflow)
    await act(workflow, f"設定回覆收件者 {state['version']}：{fields}")
    assert workflow.get("reply:ab12") == state
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_empty_role_clears_bcc_without_moving_it_to_cc(workflow):
    state, text = await with_roles(workflow)
    await act(workflow, f"設定回覆收件者 {state['version']}：BCC：")
    current = workflow.get("reply:ab12")
    assert not current["bcc"] and current["cc"] == state["cc"]


@pytest.mark.asyncio
async def test_body_revision_preserves_explicit_roles_and_invalidates_version(workflow):
    state, text = await with_roles(workflow)
    await act(workflow, "修改回覆草稿：語氣再簡潔一些")
    current = workflow.get("reply:ab12")
    assert envelope(current) == envelope(state) and current["version"] != state["version"]


@pytest.mark.asyncio
async def test_native_save_blocks_late_recipient_edits(workflow):
    state = await saved(workflow)
    before = copy.deepcopy(workflow.get("reply:ab12"))
    await act(workflow, f"設定回覆收件者 {state['version']}：BCC：new@example.org")
    assert workflow.get("reply:ab12") == before


@pytest.mark.asyncio
async def test_contact_opt_out_blocks_addition_and_revocation_blocks_send(workflow):
    setup(workflow)
    state = await propose(workflow)
    workflow.patch("contacts", items={"boss": {"emails": ["boss@example.org"], "do_not_contact": True}})
    assert "不主動聯絡" in await act(workflow, f"設定回覆收件者 {state['version']}：CC：boss@example.org")
    assert workflow.get("reply:ab12") == state
    workflow.patch("contacts", items={})
    state = await saved(workflow)
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    reviewed = workflow.get("reply:ab12")
    workflow.patch("contacts", items={"client": {"emails": ["client@example.org"], "do_not_contact": True}})
    assert "阻擋" in await act(workflow, "確認寄出回覆 " + reviewed["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_recipient_edit_is_receipt_transaction_compatible(workflow):
    setup(workflow)
    state = await propose(workflow)
    action = await workflow.prepare_action("ab12", f"設定回覆收件者 {state['version']}：CC：boss@example.org")
    receipt = workflow.db.process_update(28001, action)
    assert "CC：boss@example.org" in receipt["reply"]
    assert workflow.get("reply:ab12")["cc"] == ["boss@example.org"]


@pytest.mark.asyncio
async def test_preference_revoked_during_native_read_is_checked_again_in_send_claim(workflow):
    state = await saved(workflow)
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    reviewed = workflow.get("reply:ab12")
    native = dict(workflow.gmail.read_plain_draft.return_value)
    async def read(*args, **kwargs):
        workflow.patch("contacts", items={"client": {"emails": ["client@example.org"], "do_not_contact": True}})
        return native
    workflow.gmail.read_plain_draft.side_effect = read
    assert "沒有提交寄送" in await act(workflow, "確認寄出回覆 " + reviewed["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"


@pytest.mark.asyncio
async def test_ambiguous_envelope_is_retained_and_not_given_a_confident_ai_summary(workflow):
    from chronos.mail_workflow import filter_reason, format_mail_card
    from test_mail_workflow import message
    mail = message(sender_ambiguous=True)
    assert filter_reason(mail) is None
    summary = await workflow.summary(mail)
    workflow.ai._generate_output.assert_not_awaited()
    assert summary.startswith("原文摘錄：")
    assert "標頭有歧義" in format_mail_card(mail, summary, workflow.settings.gmail_account)


@pytest.mark.asyncio
async def test_new_source_context_does_not_carry_unreviewed_old_cc_bcc(workflow):
    state, text = await with_roles(workflow)
    messages = thread() + [{**thread()[-1], "id": "newreply", "body": "New terms", "received_at": 3}]
    workflow.gmail.thread.return_value = messages
    reply = await act(workflow, "修改回覆草稿：根據新條件簡短回覆")
    current = workflow.get("reply:ab12")
    assert not current["cc"] and not current["bcc"]
    assert "先前另設的收件者未沿用" in reply
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_delayed_cancel_cannot_cancel_new_send_consent(workflow):
    state = await saved(workflow)
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    previous = workflow.get("reply:ab12")
    cancel = await workflow.prepare_action("ab12", "stop_send_" + previous["send_token"])
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    current = workflow.get("reply:ab12")
    assert current["send_token"] != previous["send_token"]
    cancel()
    assert workflow.get("reply:ab12") == current
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
