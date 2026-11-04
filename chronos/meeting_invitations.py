"""Reviewed external invitations; organizer confirmation is not an RSVP."""
import asyncio
import re
import time
import uuid
from datetime import datetime

from .gmail import GmailError, reviewed_envelope, email_address
from .google_calendar import CalendarError
from .mail_correspondence import MailCorrespondence, fingerprint
from .mail_recipients import preferences_allow


RSVP_POLL_SECONDS = 45


INVITE_STATES = {"invite_review", "inviting", "invite_uncertain", "invite_pending", "invite_accepted",
    "invite_declined", "invite_unverified", "invite_cancel_review", "invite_cancelling", "invite_cancel_uncertain"}


def reply(text):
    return lambda: "會面協調\n" + text


def preview(state):
    if state.get("rsvp_read_unverified"):
        return "最新 Calendar 狀態未能驗證；以下為上次觀察，不代表目前已確認：\n" + preview({**state, "rsvp_read_unverified": False})
    status = state.get("status")
    if status == "invite_review":
        return (("會面邀請預覽（將轉換原私人暫留）\n" if state.get("invite_previous_status") == "held" else "會面邀請預覽\n") + f"{state['title']}\n受邀者：{', '.join(state['invitees'])}\n"
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

    def registry(self):
        return self.mail.get("meeting-invitations") or {}

    def register(self, identifier, state):
        def transition(old):
            old = old or {}
            current = self.state(identifier)
            if (current.get("version") != state["version"] or current.get("status") != "invite_review"
                    or current.get("invite_token") != state["invite_token"]):
                return old
            retained = {}
            for key, entry in old.items():
                saved = self.state(key)
                if saved.get("version") == entry.get("version") and saved.get("status") not in {"review", "cancelled", "invite_cancelled"}:
                    retained[key] = entry
            if identifier not in retained and len(retained) >= 100:
                return old
            return {**retained, identifier: {"version": state["version"], "event_id": state["event_id"]}}
        result = self.mail.db.mutate_mail_state(self.mail.key("meeting-invitations"), transition)
        return result.get(identifier, {}).get("version") == state["version"]

    def evidence(self, event, state):
        outcome = self.outcome(event, state)
        owned = self.owned(event, state)
        if (isinstance(event, dict) and event.get("id") == state["event_id"]
                and event.get("status") == "cancelled" and state.get("provider_verified")):
            outcome = "invite_cancelled"
        responses = {email_address(a["email"]): a.get("responseStatus") if isinstance(a.get("responseStatus"), str) else "unknown"
            for a in event["attendees"] if not a.get("organizer")} if owned else {}
        snapshot = fingerprint({"outcome": outcome, "responses": responses})
        return outcome, responses, snapshot, owned

    async def reconcile(self, identifier, state):
        event = await self.calendar.event(state["event_id"])
        if (event is None and state["status"] in {"inviting", "invite_uncertain", "invite_cancelling", "invite_cancel_uncertain"}):
            return state, False
        outcome, responses, snapshot, owned = self.evidence(event, state)
        if state["status"] in {"invite_cancelling", "invite_cancel_uncertain"} and outcome != "invite_cancelled":
            return state, False
        status = state["status"] if state["status"] == "invite_cancel_review" and owned else outcome
        def transition(old):
            if (not old or old.get("version") != state["version"] or old.get("status") != state["status"]
                    or old.get("rsvp_revision", 0) != state.get("rsvp_revision", 0)
                    or old.get("invite_token") != state.get("invite_token")):
                return old or {}
            changed = old.get("rsvp_snapshot") != snapshot
            return {**old, "status": status, "rsvp_status": outcome, "rsvp_responses": responses,
                "rsvp_snapshot": snapshot, "rsvp_revision": old.get("rsvp_revision", 0) + int(changed),
                "provider_verified": bool(old.get("provider_verified") or owned), "rsvp_read_unverified": False,
                "rsvp_checked_at": time.time()}
        current = self.mail.db.mutate_mail_state(self.mail.key("meeting:" + identifier), transition)
        return current, True

    async def refresh(self, now=None):
        result = {"checked": 0, "notified": 0, "errors": 0, "deferred": 0}
        registry = self.registry()
        if not registry:
            return result
        if not self.calendar or not self.calendar.configured():
            result["errors"] = len(registry)
            self.mail.patch("meeting-rsvp-health", **result, checked_at=time.time())
            return result
        entries = sorted(registry.items())
        offset = int((self.mail.get("meeting-rsvp-health") or {}).get("cursor", 0)) % len(entries)
        entries = entries[offset:] + entries[:offset]
        deadline = time.monotonic() + RSVP_POLL_SECONDS
        visited = 0
        owner_verified = False
        for identifier, entry in entries:
            if time.monotonic() >= deadline:
                result["deferred"] = len(entries) - visited
                break
            visited += 1
            state = self.state(identifier)
            if state.get("version") != entry.get("version") or state.get("status") not in INVITE_STATES - {"invite_review"}:
                continue
            result["checked"] += 1
            reading = True
            try:
                async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                    if (state.get("calendar_account") != self.mail.settings.calendar_account
                            or state.get("calendar_id") != self.mail.settings.calendar_id):
                        raise CalendarError("Invitation calendar binding changed")
                    if not owner_verified:
                        await self.calendar.verify_owner()
                        owner_verified = True
                    current, verified = await self.reconcile(identifier, state)
                    revision = current.get("rsvp_revision", 0)
                    if not verified or revision <= current.get("last_notified_revision", 0):
                        continue
                    delivery_key = f"meeting-rsvp:{identifier}:{current['version']}:{revision}"
                    claim = uuid.uuid4().hex
                    def transition(old):
                        if (not old or old.get("version") != current["version"] or old.get("rsvp_revision") != revision
                                or old.get("status") != current["status"] or old.get("rsvp_snapshot") != current.get("rsvp_snapshot")
                                or old.get("rsvp_read_unverified") or old.get("last_notified_revision", 0) >= revision):
                            return old or {}
                        delivery = self.mail.get(delivery_key) or {}
                        if old.get("notice_claim_revision") == revision and delivery.get("delivery") == "sent":
                            return {**old, "last_notified_revision": max(old.get("last_notified_revision", 0), revision), "rsvp_notice_error": False}
                        if old.get("notice_claim_revision") == revision and delivery.get("delivery") != "retry":
                            return old
                        return {**old, "notice_claim_revision": revision, "notice_claim": claim}
                    claimed = self.mail.db.mutate_mail_state(self.mail.key("meeting:" + identifier), transition)
                    if claimed.get("notice_claim") != claim:
                        continue
                    observed = {**current, "status": current.get("rsvp_status", current["status"])}
                    reading = False
                    checked = datetime.fromtimestamp(current.get("rsvp_checked_at", time.time()), self.mail.settings.tz)
                    await self.mail.deliver(delivery_key, f"Calendar 狀態觀察（{checked:%m/%d %H:%M}）：" + current["title"] + "\n" + current["start"] + "\n" + preview(observed))
                    def complete(old):
                        if old and old.get("version") == current["version"] and old.get("notice_claim") == claim:
                            return {**old, "last_notified_revision": max(old.get("last_notified_revision", 0), revision), "rsvp_notice_error": False}
                        return old or {}
                    self.mail.db.mutate_mail_state(self.mail.key("meeting:" + identifier), complete)
                    delivery = self.mail.get(delivery_key) or {}
                    if delivery.get("message_id"):
                        self.mail.patch(f"telegram:{self.mail.settings.telegram_chat_id}:{delivery['message_id']}", mail_id=identifier)
                    result["notified"] += 1
            except (CalendarError, GmailError, TimeoutError):
                field = "rsvp_read_unverified" if reading else "rsvp_notice_error"
                def failed(old):
                    if not old or old.get("version") != state["version"]:
                        return old or {}
                    if reading and old.get("rsvp_revision", 0) != state.get("rsvp_revision", 0):
                        return old
                    if not reading and old.get("notice_claim") != claim:
                        return old
                    return {**old, field: True}
                self.mail.db.mutate_mail_state(self.mail.key("meeting:" + identifier), failed)
                result["errors"] += 1
                if not owner_verified:
                    result["deferred"] = len(entries) - visited
                    break
        self.mail.patch("meeting-rsvp-health", **result, cursor=(offset + visited) % len(entries), checked_at=time.time())
        return result

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
        if state.get("invite_previous_status") == "held":
            from .meeting_hold_conversion import verify_conversion
            return await verify_conversion(self, state)
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
            if state.get("status") not in {"review", "held"} or state.get("version") != prepare[1]:
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
                "invite_token": uuid.uuid4().hex[:8], "invite_expires": time.time() + 900,
                "invite_previous_status": state["status"]}
            if state["status"] == "held":
                from .meeting_hold_conversion import clean_hold
                event = await self.calendar.event(state["event_id"])
                if not clean_hold(self, event, state):
                    return reply("私人暫留已變更或含未審查內容；沒有轉成邀請，也沒有移除內容。")
                proposed["hold_etag"] = event["etag"]
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
                status = state.get("cancel_previous_status") if field == "cancel_token" else state.get("invite_previous_status", "review")
                current = self.transition(identifier, state, {"status": status, field: None})
                return "會面協調\n" + ("已取消本次準備，沒有對外提交。" if current.get(field) is None else "確認已變更，沒有取消其他準備。")
            return apply
        if show:
            if state["status"] in {"invite_review", "invite_cancel_review", "invite_cancelled"}:
                return reply(preview(state))
            current, _ = await self.reconcile(identifier, state)
            return reply(preview(current))
        if confirm:
            if (state["status"] != "invite_review" or state.get("invite_token") != confirm[1]
                    or state.get("invite_expires", 0) <= time.time()):
                return reply("邀請確認已過期或不符；沒有提交。")
            if not await self.verify(identifier, state, reference) or (state.get("invite_previous_status") != "held"
                    and await self.calendar.event(state["event_id"]) is not None):
                return reply("來源、偏好、空檔或事件狀態已變更；沒有提交邀請。")
            if not self.register(identifier, state):
                return reply("邀請索引已變更或達到 100 個追蹤上限；沒有提交外部邀請。")
            claim = uuid.uuid4().hex
            def transition(old):
                registered = self.registry().get(identifier, {})
                if (registered.get("version") == state["version"] and registered.get("event_id") == state["event_id"]
                        and old and old.get("version") == state["version"] and old.get("status") == "invite_review"
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
                "visibility": "private", "transparency": "opaque", "guestsCanModify": False, "guestsCanInviteOthers": False,
                "guestsCanSeeOtherGuests": False,
                "attendees": [{"email": address, "responseStatus": "needsAction"} for address in state["invitees"]],
                "extendedProperties": {"private": {"chronosMeeting": state["version"],
                    "chronosAccount": self.mail.namespace, "chronosInvitation": state["invite_token"]}}}
            try:
                if state.get("invite_previous_status") == "held":
                    event = await self.calendar.invite_existing(state["event_id"], {k: v for k, v in body.items() if k != "id"}, state["hold_etag"])
                else:
                    event = await self.calendar.invite(body)
                outcome = self.outcome(event, state)
                if outcome == "invite_unverified":
                    outcome = "invite_uncertain"
            except CalendarError as exc:
                outcome = state.get("invite_previous_status", "review") if exc.status in {400, 401, 403, 412, 429} else "invite_uncertain"
            values = {"status": outcome}
            if outcome not in {"review", "held", "invite_uncertain"}:
                _, responses, snapshot, owned = self.evidence(event, state)
                values.update(rsvp_snapshot=snapshot, rsvp_responses=responses, rsvp_status=outcome,
                    rsvp_revision=0, last_notified_revision=0, provider_verified=owned)
            current = self.transition(identifier, current, values)
            return reply(preview(current) if outcome not in {"review", "held"} else "Calendar 明確拒絕邀請，沒有確認完成；原候選或私人暫留保留，請重新核對後準備並授權。")
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
        values = {"status": outcome}
        if outcome == "invite_cancelled":
            revision = current.get("rsvp_revision", 0) + 1
            values.update(rsvp_status=outcome, rsvp_snapshot=fingerprint({"outcome": outcome, "responses": {}}),
                rsvp_read_unverified=False, rsvp_checked_at=time.time(), rsvp_revision=revision,
                last_notified_revision=revision)
        current = self.transition(identifier, current, values)
        if outcome == "invite_unverified":
            return reply("Calendar 明確拒絕取消；原邀請須重新核對，請先查看會面邀請再準備取消。")
        return reply(preview(current))
