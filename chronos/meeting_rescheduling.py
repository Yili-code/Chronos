"""Reviewed conditional time changes; never recreate or replay uncertain events."""
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .google_calendar import CalendarError
from .mail_correspondence import MailCorrespondence, fingerprint
from .meeting_hold_conversion import clean_hold, same_calendar
from .meeting_invitations import MeetingInvitations, reply
from .study_reflow import bounds

ACTIVE = {"review", "applying", "uncertain", "unverified"}
ELIGIBLE = {"held", "invite_pending", "invite_accepted", "invite_declined"}


def active(state):
    return (state.get("reschedule") or {}).get("status") in ACTIVE


def preview(state):
    change = state.get("reschedule") or {}
    if change.get("status") == "review":
        audience = ", ".join(state.get("invitees", [])) if change["previous_status"] != "held" else "僅你的私人暫留，沒有受邀者"
        return (f"會面改期預覽：{state['title']}\n對象：{audience}\n"
            f"原時間：{state['start']} 至 {state['end']}（{state['timezone']}）\n"
            f"新時間：{change['start']} 至 {change['end']}（{change['timezone']}）\n"
            "只核對你的行事曆；對方尚未接受新時間。確認後才更新原事件。\n"
            + ("只更新私人暫留，不寄送通知。\n" if change["previous_status"] == "held" else "確認後會向上述所有受邀者送出 Calendar 更新通知。\n") +
            f"確認會面改期 {change['token']}（15 分鐘內有效）\n取消會面改期準備 {change['token']}")
    if change.get("status") in {"applying", "uncertain", "unverified"}:
        return "改期結果或新時間的接受狀態待核對；不會重送。請回覆「查看會面改期」，或先在 Calendar 核對。"
    if change.get("status") == "completed":
        return ("已更新原私人暫留，沒有寄出通知。" if state["status"] == "held" else
            "Calendar 已更新原會議並接受通知要求；對方尚未確認新時間，未驗證通知送達。")
    return "本次改期準備已取消或遭明確拒絕；原事件未由本次確認改寫，請核對最新 Calendar 狀態。"


class MeetingRescheduling:
    def __init__(self, mail):
        self.mail = mail
        self.calendar = getattr(mail, "calendar", None)
        self.invitations = MeetingInvitations(mail)

    def state(self, identifier):
        return self.invitations.state(identifier)

    def update(self, identifier, state, values):
        change = state.get("reschedule") or {}
        def transition(old):
            if (not old or old.get("version") != state.get("version") or old.get("status") != state.get("status")
                    or (old.get("reschedule") or {}) != change):
                return old or {}
            if values.get("reschedule", {}).get("status") == "applying" and old["reschedule"].get("expires", 0) <= time.time():
                return old
            if values.get("reschedule", {}).get("status") == "applying" and old["status"] != "held":
                from .mail_recipients import preferences_allow
                if not preferences_allow(self.mail, self.invitations.selected(old)):
                    return old
            return {**old, **values}
        return self.mail.db.mutate_mail_state(self.mail.key("meeting:" + identifier), transition)

    def expected(self, state):
        change = state["reschedule"]
        return {**state, **{k: change[k] for k in ("start", "end", "timezone")}, "reschedule_epoch": change["token"]}

    def owned(self, event, state):
        return clean_hold(self.invitations, event, state) if state["status"] == "held" else isinstance(event, dict) and event.get("status") in {"confirmed", "tentative"} and self.invitations.owned(event, state)

    async def available(self, state, start, end):
        events = await self.calendar.agenda(start, end)
        if not isinstance(events, list):
            raise CalendarError("Incomplete calendar agenda")
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("_calendar_id"), str):
                raise CalendarError("Calendar provenance missing")
            if event.get("id") == state["event_id"] and same_calendar(self.invitations, event["_calendar_id"]):
                if not self.owned(event, state):
                    return False
                continue
            if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                continue
            try:
                begin, stop = bounds(event, self.mail.settings.tz)
                if stop <= begin:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise CalendarError("Invalid calendar interval") from None
            if start < stop and end > begin:
                return False
        return True

    async def verify(self, identifier, state, reference):
        change = state["reschedule"]
        if (state.get("status") not in ELIGIBLE or state.get("calendar_account") != self.mail.settings.calendar_account
                or state.get("calendar_id") != self.mail.settings.calendar_id):
            return None
        _, messages = await MailCorrespondence(self.mail).context(identifier)
        if fingerprint(messages) != change["source"]:
            return None
        event = await self.calendar.event(state["event_id"])
        if not self.owned(event, state) or event.get("etag") != change["etag"]:
            return None
        if state["status"] != "held":
            from .mail_recipients import preferences_allow
            if not preferences_allow(self.mail, self.invitations.selected(state)):
                return None
        start, end = (datetime.fromisoformat(change[k]) for k in ("start", "end"))
        return event if start > reference and await self.available(state, start, end) else None

    def complete(self, identifier, state, event, acknowledged):
        expected = self.expected(state)
        if (not self.owned(event, expected) or any(event.get(k, {}).get("timeZone") != expected["timezone"] for k in ("start", "end"))):
            return None
        if state["status"] != "held" and any(a.get("responseStatus") != "needsAction" for a in event["attendees"] if not a.get("organizer")):
            return None
        values = {k: expected[k] for k in ("start", "end", "timezone", "reschedule_epoch")}
        values["minutes"] = int((datetime.fromisoformat(expected["end"]) - datetime.fromisoformat(expected["start"])).total_seconds() // 60)
        values["reschedule"] = {**state["reschedule"], "status": "completed"}
        if state["status"] != "held":
            outcome, responses, snapshot, owned = self.invitations.evidence(event, expected)
            revision = state.get("rsvp_revision", 0) + 1
            values.update(status=outcome, rsvp_status=outcome, rsvp_responses=responses, rsvp_snapshot=snapshot,
                rsvp_revision=revision, last_notified_revision=revision if acknowledged else state.get("last_notified_revision", 0),
                provider_verified=owned, rsvp_read_unverified=False)
        current = self.update(identifier, state, values)
        return current if current.get("reschedule", {}).get("token") == state["reschedule"]["token"] and current["reschedule"]["status"] == "completed" else None

    async def reconcile(self, identifier, state):
        if (state.get("calendar_account") != self.mail.settings.calendar_account
                or state.get("calendar_id") != self.mail.settings.calendar_id):
            return state, False
        event = await self.calendar.event(state["event_id"])
        current = self.complete(identifier, state, event, False)
        return (current, True) if current and current.get("reschedule", {}).get("status") == "completed" else (state, False)

    async def prepare(self, identifier, instruction, now=None):
        propose = re.fullmatch(r"準備會面改期\s+([a-f0-9]{8})\s*[:：]\s*(\S+)[；;]\s*時區\s*[:：]\s*([^；;\s]+)[；;]\s*分鐘\s*[:：]\s*(\d{1,3})", instruction)
        confirm = re.fullmatch(r"確認會面改期\s+([a-f0-9]{8})", instruction)
        cancel = re.fullmatch(r"取消會面改期準備\s+([a-f0-9]{8})", instruction)
        state = self.state(identifier)
        show = instruction == "查看會面改期" or instruction == "查看會面狀態" and active(state)
        if not (propose or confirm or cancel or show):
            if instruction.startswith("準備會面改期"):
                return reply("請提供原候選碼、含 UTC offset 的新時間、IANA 時區與長度，例如：準備會面改期 <碼>：2026-11-06T15:00:00+08:00；時區：Asia/Taipei；分鐘：30。沒有修改。")
            return None
        if not self.calendar or not self.calendar.configured():
            return reply("Calendar 尚未授權；沒有改期。")
        await self.calendar.verify_owner()
        reference = now or datetime.now(self.mail.settings.tz)
        change = state.get("reschedule") or {}
        if show:
            if change.get("status") in {"applying", "uncertain", "unverified"}:
                state, _ = await self.reconcile(identifier, state)
            return reply(preview(state) if state.get("reschedule") else "目前沒有改期準備。")
        if cancel:
            if change.get("status") != "review" or change.get("token") != cancel[1]:
                return reply("改期確認已變更或外部操作已開始；沒有撤回已提交操作。")
            def abandon():
                current = self.update(identifier, state, {"reschedule": {**change, "status": "cancelled"}})
                return "會面協調\n" + preview(current)
            return abandon
        if propose:
            if state.get("status") not in ELIGIBLE or state.get("version") != propose[1] or active(state):
                return reply("請先核對原會議或完成既有改期準備；沒有覆蓋原事件。")
            try:
                start = datetime.fromisoformat(propose[2])
                zone = ZoneInfo(propose[3])
                minutes = int(propose[4])
                if (start.utcoffset() is None or start.utcoffset() != start.astimezone(zone).utcoffset()
                        or not 15 <= minutes <= 240 or not reference < start <= reference + timedelta(days=365)):
                    raise ValueError()
                end = (start.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(zone)
                if start == datetime.fromisoformat(state["start"]) and end == datetime.fromisoformat(state["end"]) and propose[3] == state["timezone"]:
                    return reply("新舊時間相同，沒有準備或寄出更新。")
            except (ValueError, ZoneInfoNotFoundError):
                return reply("新時間、UTC offset、時區或長度無法確認；沒有改期。")
            event = await self.calendar.event(state["event_id"])
            if not self.owned(event, state) or not isinstance(event.get("etag"), str) or not event["etag"]:
                return reply("原事件已變更或無法核對；沒有準備改期。")
            _, messages = await MailCorrespondence(self.mail).context(identifier)
            proposed = {"status": "review", "token": uuid.uuid4().hex[:8], "expires": time.time() + 900,
                "start": start.isoformat(), "end": end.isoformat(), "timezone": propose[3],
                "source": fingerprint(messages), "etag": event["etag"], "previous_status": state["status"]}
            if not await self.verify(identifier, {**state, "reschedule": proposed}, reference):
                return reply("來源、聯絡偏好或新時間有衝突；沒有準備改期。")
            def apply():
                current = self.update(identifier, state, {"reschedule": proposed})
                return "會面協調\n" + (preview(current) if current.get("reschedule") == proposed else "會面狀態已變更；沒有覆蓋。")
            return apply
        if change.get("status") != "review" or change.get("token") != confirm[1] or change.get("expires", 0) <= time.time():
            return reply("改期確認已失效；沒有提交。")
        event = await self.verify(identifier, state, reference)
        if not event:
            return reply("事件、來源、偏好或可用時間已變更；沒有提交改期。")
        claim = uuid.uuid4().hex
        claimed = self.update(identifier, state, {"reschedule": {**change, "status": "applying", "claim": claim}})
        if claimed.get("reschedule", {}).get("claim") != claim:
            return reply("另一個操作已處理改期；沒有重送。")
        body = {k: {"dateTime": change[k], "timeZone": change["timezone"]} for k in ("start", "end")}
        props = event["extendedProperties"]["private"]
        body["extendedProperties"] = {"private": {**props, "chronosReschedule": change["token"]}}
        if state["status"] != "held":
            body["attendees"] = [{"email": a, "responseStatus": "needsAction"} for a in state["invitees"]]
        try:
            updated = await (self.calendar.move_event(state["event_id"], body, change["etag"]) if state["status"] == "held"
                else self.calendar.invite_existing(state["event_id"], body, change["etag"]))
            completed = self.complete(identifier, claimed, updated, True)
            if completed:
                return reply(preview(completed))
            outcome = "unverified"
        except CalendarError as exc:
            outcome = "rejected" if exc.status in {400, 401, 403, 412, 429} else "uncertain"
        current = self.update(identifier, claimed, {"reschedule": {**claimed["reschedule"], "status": outcome}})
        return reply(preview(current))
