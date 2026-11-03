"""Reviewed external invitations; organizer confirmation is not an RSVP."""
import re
import time
import uuid
from datetime import datetime

from .gmail import GmailError, reviewed_envelope, email_address
from .google_calendar import CalendarError
from .mail_correspondence import MailCorrespondence, fingerprint
from .mail_recipients import preferences_allow


INVITE_STATES = {"invite_review", "inviting", "invite_uncertain", "invite_pending", "invite_accepted",
    "invite_declined", "invite_unverified", "invite_cancel_review", "invite_cancelling", "invite_cancel_uncertain"}


def reply(text):
    return lambda: "會面協調\n" + text


def preview(state):
    status = state.get("status")
    if status == "invite_review":
        return (f"會面邀請預覽\n{state['title']}\n受邀者：{', '.join(state['invitees'])}\n"
            f"時間：{state['start']} 至 {state['end']}（{state['timezone']}）\n地點：{state['location']}\n"
            "僅核對你的設定行事曆；對方可用時間與接受狀態尚未確認。尚未提交邀請。\n"
            f"這會通知所有列出的受邀者。核對後回覆：確認會面邀請 {state['invite_token']}\n確認碼 15 分鐘內有效。\n"
            f"取消本次準備：取消會面邀請準備 {state['invite_token']}")
    if status == "invite_pending":
        return "已提交 Calendar 邀請，尚未全部接受；未確認通知送達，不能宣稱會面已確認。"
    if status == "invite_accepted":
        return "Calendar 顯示所有列出的受邀者已接受此時間與事件；請仍留意後續改期或取消。"
    if status == "invite_declined":
        return "至少一位受邀者已拒絕；這場會面尚未協調成功，沒有自動取消或另寄邀請。"
    if status == "invite_cancelled":
        return "Calendar 已確認本次邀請取消；不代表所有人都已讀取取消通知。"
    if status == "invite_cancel_review":
        return (f"將取消 {state['title']}（{state['start']}），並通知 {', '.join(state['invitees'])}。\n"
            f"尚未取消；核對後回覆：確認取消會面邀請 {state['cancel_token']}")
    return "邀請或取消結果尚待核對；不會自動重試外部操作。請回覆「查看會面邀請」。"


class MeetingInvitations:
    def __init__(self, mail):
        self.mail, self.calendar = mail, mail.calendar

    def state(self, identifier):
        return self.mail.get("meeting:" + identifier) or {}

    def transition(self, identifier, old, values):
        return self.mail.db.mutate_mail_state(self.mail.key("meeting:" + identifier), lambda current:
            {**current, **values} if current and current.get("version") == old.get("version")
            and current.get("status") == old.get("status")
            and current.get("invite_token") == old.get("invite_token")
            and current.get("cancel_token") == old.get("cancel_token") else (current or {}))

    def selected(self, state):
        return reviewed_envelope(state["invitees"][0], to=state["invitees"])

    def owned(self, event, state):
        if (not isinstance(event, dict) or event.get("attendeesOmitted")
                or event.get("recurrence") or event.get("recurringEventId") or event.get("privateCopy")
                or event.get("eventType", "default") != "default"
                or not isinstance(event.get("etag"), str) or not event["etag"]
                or event.get("guestsCanModify", False) is not False
                or any(event.get(name) is not False for name in ("guestsCanInviteOthers", "guestsCanSeeOtherGuests"))):
            return False
        try:
            props = event.get("extendedProperties", {}).get("private", {})
            emails = [email_address(a["email"]) for a in event["attendees"] if not a.get("organizer")]
            owners = {email_address(self.mail.settings.calendar_account), email_address(event.get("organizer", {}).get("email", ""))}
            if any(a.get("organizer") and email_address(a["email"]) not in owners for a in event["attendees"]):
                return False
            if any(a.get("additionalGuests", 0) != 0 for a in event["attendees"]):
                return False
            return (event.get("id") == state["event_id"] and event.get("organizer", {}).get("self") is True
                and props.get("chronosMeeting") == state["version"]
                and props.get("chronosAccount") == self.mail.namespace
                and props.get("chronosInvitation") == state["invite_token"]
                and event.get("summary") == state["title"] and event.get("location") == state["location"]
                and len(emails) == len(set(emails)) and set(emails) == set(state["invitees"])
                and all(datetime.fromisoformat(event[k]["dateTime"].replace("Z", "+00:00")) == datetime.fromisoformat(state[k])
                    for k in ("start", "end")))
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    def outcome(self, event, state):
        if not self.owned(event, state) or event.get("status") not in {"confirmed", "tentative"}:
            return "invite_unverified"
        responses = [a.get("responseStatus") for a in event["attendees"] if not a.get("organizer")]
        if any(value == "declined" for value in responses):
            return "invite_declined"
        if event["status"] == "confirmed" and responses and all(value == "accepted" for value in responses):
            return "invite_accepted"
        if all(isinstance(value, str) and value in {"needsAction", "tentative", "accepted"} for value in responses):
            return "invite_pending"
        return "invite_unverified"

    async def verify(self, identifier, state, now):
        _, messages = await MailCorrespondence(self.mail).context(identifier)
        if (fingerprint(messages) != state["source"] or any(m.get("sender_ambiguous") or m.get("envelope_ambiguous") for m in messages)
                or not preferences_allow(self.mail, self.selected(state))):
            return False
        start, end = (datetime.fromisoformat(state[k]) for k in ("start", "end"))
        if start <= now:
            return False
        return not any(start < stop and end > begin for begin, stop in await self.calendar.busy(start, end))

    async def prepare(self, identifier, instruction, now=None):
        prepare = re.fullmatch(r"準備會面邀請\s+([a-f0-9]{8})\s*[:：]\s*(.+)", instruction, re.S)
        confirm = re.fullmatch(r"確認會面邀請\s+([a-f0-9]{8})", instruction)
        cancel = re.fullmatch(r"準備取消會面邀請\s+([a-f0-9]{8})", instruction)
        cancel_confirm = re.fullmatch(r"確認取消會面邀請\s+([a-f0-9]{8})", instruction)
        abandon = re.fullmatch(r"取消會面邀請準備\s+([a-f0-9]{8})", instruction)
        state = self.state(identifier)
        show = instruction in {"查看會面邀請", "查看會面狀態"}
        if instruction == "查看會面狀態" and state.get("status") not in INVITE_STATES | {"invite_cancelled"}:
            return None
        if not (prepare or confirm or cancel or cancel_confirm or abandon or show):
            return None
        if not self.calendar or not self.calendar.configured():
            return reply("Calendar 尚未授權，沒有提交任何邀請。")
        await self.calendar.verify_owner()
        state = self.state(identifier)
        reference = now or datetime.now(self.mail.settings.tz)
        if (state.get("calendar_account") is not None and
                (state["calendar_account"] != self.mail.settings.calendar_account or state.get("calendar_id") != self.mail.settings.calendar_id)):
            return reply("Calendar 帳號或行事曆設定已變更；請先核對原邀請，沒有修改其他行事曆。")
        if prepare:
            if state.get("status") != "review" or state.get("version") != prepare[1]:
                return reply("請先選擇最新候選時間；私人暫留須先核對或取消，沒有自動轉為外部邀請。")
            fields = re.fullmatch(r"([^；;\r\n]+)[；;]\s*地點\s*[:：]\s*([^\r\n]{1,300})", prepare[2])
            if not fields:
                return reply("請明確列出 Email 與地點，例如：準備會面邀請 <候選碼>：alice@example.org,bob@example.org；地點：台北辦公室。")
            try:
                addresses = [value.strip() for value in fields[1].split(",")]
                addresses = reviewed_envelope(addresses[0], to=addresses)["to"]
                if any(address in {email_address(self.mail.settings.gmail_account), email_address(self.mail.settings.calendar_account)} for address in addresses):
                    raise GmailError("Select external guests, not the organizer")
            except GmailError:
                return reply("邀請名單無法明確核對；沒有提交或猜測收件者。")
            proposed = {**state, "invitees": addresses, "location": fields[2].strip(),
                "calendar_account": self.mail.settings.calendar_account, "calendar_id": self.mail.settings.calendar_id,
                "invite_token": uuid.uuid4().hex[:8], "invite_expires": time.time() + 900}
            if not proposed["location"] or not await self.verify(identifier, proposed, reference):
                return reply("郵件、聯絡偏好或行事曆已變更；沒有準備外部邀請。")
            def apply():
                current = self.transition(identifier, state, {**proposed, "status": "invite_review"})
                return "會面協調\n" + (preview(current) if current.get("invite_token") == proposed["invite_token"] else "候選時間已變更；沒有改寫。")
            return apply
        if not state or state.get("status") not in INVITE_STATES | {"invite_cancelled"}:
            return reply("尚無已審查的外部邀請。")
        if abandon:
            field = "cancel_token" if state["status"] == "invite_cancel_review" else "invite_token"
            if state["status"] not in {"invite_review", "invite_cancel_review"} or state.get(field) != abandon[1]:
                return reply("準備確認已變更或外部操作已開始；沒有撤回任何已提交操作。")
            def apply():
                status = state.get("cancel_previous_status") if field == "cancel_token" else "review"
                current = self.transition(identifier, state, {"status": status, field: None})
                return "會面協調\n" + ("已取消本次準備，沒有對外提交。" if current.get(field) is None else "確認已變更，沒有取消其他準備。")
            return apply
        if show:
            if state["status"] in {"invite_review", "invite_cancel_review", "invite_cancelled"}:
                return reply(preview(state))
            event = await self.calendar.event(state["event_id"])
            if state["status"] in {"invite_cancelling", "invite_cancel_uncertain"} or (event is None and state["status"] in {"inviting", "invite_uncertain"}):
                return reply(preview(state))
            outcome = self.outcome(event, state)
            current = self.transition(identifier, state, {"status": outcome})
            return reply(preview(current))
        if confirm:
            if (state["status"] != "invite_review" or state.get("invite_token") != confirm[1]
                    or state.get("invite_expires", 0) <= time.time()):
                return reply("邀請確認已過期或不符；沒有提交。")
            if not await self.verify(identifier, state, reference) or await self.calendar.event(state["event_id"]) is not None:
                return reply("來源、偏好、空檔或事件狀態已變更；沒有提交邀請。")
            claim = uuid.uuid4().hex
            def transition(old):
                if (old and old.get("version") == state["version"] and old.get("status") == "invite_review"
                        and old.get("invite_token") == confirm[1] and old.get("invite_expires", 0) > time.time()
                        and preferences_allow(self.mail, self.selected(state))):
                    return {**old, "status": "inviting", "invite_claim": claim}
                return old or {}
            current = self.mail.db.mutate_mail_state(self.mail.key("meeting:" + identifier), transition)
            if current.get("invite_claim") != claim:
                return reply("確認或聯絡偏好已變更；沒有提交邀請。")
            body = {"id": state["event_id"], "summary": state["title"], "location": state["location"],
                "start": {"dateTime": state["start"], "timeZone": state["timezone"]},
                "end": {"dateTime": state["end"], "timeZone": state["timezone"]}, "status": "confirmed",
                "visibility": "private", "guestsCanModify": False, "guestsCanInviteOthers": False,
                "guestsCanSeeOtherGuests": False,
                "attendees": [{"email": address, "responseStatus": "needsAction"} for address in state["invitees"]],
                "extendedProperties": {"private": {"chronosMeeting": state["version"],
                    "chronosAccount": self.mail.namespace, "chronosInvitation": state["invite_token"]}}}
            try:
                event = await self.calendar.invite(body)
                outcome = self.outcome(event, state)
                if outcome == "invite_unverified":
                    outcome = "invite_uncertain"
            except CalendarError as exc:
                outcome = "review" if exc.status in {400, 401, 403, 412, 429} else "invite_uncertain"
            current = self.transition(identifier, current, {"status": outcome})
            return reply(preview(current) if outcome != "review" else "Calendar 明確拒絕邀請，沒有確認完成；請重新準備並授權。")
        if cancel:
            if state.get("version") != cancel[1] or state["status"] not in {"invite_pending", "invite_accepted", "invite_declined"}:
                return reply("請先核對已提交邀請的最新狀態；沒有取消。")
            event = await self.calendar.event(state["event_id"])
            if not self.owned(event, state) or not event.get("etag"):
                return reply("事件名單或內容已被修改，無法核對；沒有取消。")
            token = uuid.uuid4().hex[:8]
            def apply():
                current = self.transition(identifier, state, {"status": "invite_cancel_review", "cancel_token": token,
                    "cancel_expires": time.time() + 900, "cancel_previous_status": state["status"]})
                return "會面協調\n" + preview(current)
            return apply
        if (state["status"] != "invite_cancel_review" or state.get("cancel_token") != cancel_confirm[1]
                or state.get("cancel_expires", 0) <= time.time()):
            return reply("取消確認已失效；沒有提交取消。")
        event = await self.calendar.event(state["event_id"])
        if not self.owned(event, state) or not event.get("etag"):
            return reply("事件內容或名單已變更；沒有取消。")
        claim = uuid.uuid4().hex
        current = self.transition(identifier, state, {"status": "invite_cancelling", "cancel_claim": claim})
        if current.get("cancel_claim") != claim:
            return reply("邀請狀態已變更；沒有提交取消。")
        try:
            result = await self.calendar.cancel_invitation(state["event_id"], event["etag"])
            if not isinstance(result, dict) or result:
                raise CalendarError("Cancellation response is unverified")
            outcome = "invite_cancelled"
        except CalendarError as exc:
            outcome = "invite_unverified" if exc.status in {400, 401, 403, 412, 429} else "invite_cancel_uncertain"
        current = self.transition(identifier, current, {"status": outcome})
        if outcome == "invite_unverified":
            return reply("Calendar 明確拒絕取消；原邀請須重新核對，請先查看會面邀請再準備取消。")
        return reply(preview(current))
