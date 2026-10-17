"""Approval-gated study blocks and durable, owner-reported learning feedback."""
import math
import re
from random import choice
from datetime import datetime, timedelta, time
from hashlib import sha256
from html import escape
from uuid import uuid4

from .course_tracking import COURSE_SCHEDULE
from .google_calendar import CalendarError
from .task_actions import task_fields
from .task_timing import display_time


def overlaps(start, end, busy):
    return any(start < stop and end > begin for begin, stop in busy)


def class_windows(day, tz, policy=None):
    if policy and (day.isoformat() < policy["first_day"] or day.isoformat() > policy["last_regular_day"]
                   or day.isoformat() in policy.get("excluded_dates", [])):
        return []
    return [(datetime.combine(day, slot.start, tz), datetime.combine(day, slot.end, tz))
            for slot in COURSE_SCHEDULE if slot.weekday == day.weekday()]


def find_slot(task, now, busy, reservations, config, minutes, booked_minutes=None, course_policy=None, randomize=False):
    """Search 15-minute boundaries; class plus study load never exceeds the cap."""
    due = task.get("due_at") or (task.get("timing") or {}).get("due_date")
    deadline = (datetime.fromisoformat(due).astimezone(config.tz) if due and len(due) > 10 else
                datetime.combine(datetime.fromisoformat(due).date(), time.max, config.tz) if due else
                now + timedelta(days=7))
    horizon = min(deadline, now + timedelta(days=7))
    candidates = []
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        classes = class_windows(day, config.tz, course_policy)
        load = sum((end - start).total_seconds() / 60 for start, end in classes)
        load += (booked_minutes or {}).get(day.isoformat(), 0)
        load += sum((end - start).total_seconds() / 60 for start, end in reservations if start.date() == day)
        if load + minutes > config.learning_daily_minutes:
            continue
        start = max(now + timedelta(minutes=10), datetime.combine(day, time(config.learning_start_hour), config.tz))
        start = start.replace(second=0, microsecond=0)
        start += timedelta(minutes=(-start.minute) % 15)
        stop = min(horizon, datetime.combine(day, time(config.learning_end_hour % 24), config.tz)
                   + (timedelta(days=1) if config.learning_end_hour == 24 else timedelta()))
        while start + timedelta(minutes=minutes) <= stop:
            end = start + timedelta(minutes=minutes)
            if not overlaps(start, end, [*busy, *classes, *reservations]):
                if not randomize:
                    return start, end
                candidates.append((start, end))
            start += timedelta(minutes=15)
    return choice(candidates) if candidates else None


class LearningCalendar:
    def __init__(self, db, calendar, telegram, settings):
        self.db, self.calendar, self.telegram, self.settings = db, calendar, telegram, settings

    def get(self, task_id):
        return self.db.get_task_action_state(f"learning:task:{task_id}")

    def update(self, task_id, transition):
        return self.db.mutate_task_action_state(f"learning:task:{task_id}", transition)

    async def send(self, task_id, state, text, buttons, kind):
        claim = uuid4().hex
        sending = self.update(task_id, lambda old: {**old, "delivery_claim": claim, "delivery": kind + "_sending"}
            if old and old.get("token") == state["token"] and old.get("delivery") == kind + "_pending" else old)
        if not sending or sending.get("delivery_claim") != claim:
            return
        try:
            result = await self.telegram.send_message(self.settings.telegram_chat_id, text,
                parse_mode="HTML", reply_markup={"inline_keyboard": [buttons]})
        except Exception:
            self.update(task_id, lambda old: {**old, "delivery": kind + "_uncertain"})
            raise CalendarError("Study message delivery is uncertain. Inspect /plan before retrying.") from None
        if not result.get("ok"):
            self.update(task_id, lambda old: {**old, "delivery": kind + "_pending"})
            raise CalendarError("Study message was rejected; it will be retried.")
        self.update(task_id, lambda old: {**old, "delivery": kind + "_sent",
            "message_id": result.get("result", {}).get("message_id")})

    def buttons(self, task_id, state, values):
        return [{"text": label, "callback_data": f"learn:{action}:{task_id}:{state['token']}"}
                for action, label in values]

    async def propose(self, task, now, reservations, booked_minutes=None):
        task_id = task["id"]
        claim = uuid4().hex
        state = self.update(task_id, lambda old: {"status": "planning", "token": claim,
            "lease_until": (now + timedelta(minutes=3)).isoformat(), "snapshot": task_fields(task)}
            if not old or old.get("status") == "queued" or
            (old.get("status") == "planning" and old["lease_until"] < now.isoformat()) else old)
        if state.get("token") != claim:
            return
        try:
            await self.calendar.verify_owner()
            busy = await self.calendar.busy(now, now + timedelta(days=7))
            profile = self.db.get_task_action_state("learning:profile") or {}
            subject = task.get("project") or "general"
            evidence = profile.get(subject, {})
            minutes = (max(15, min(180, math.ceil(evidence["average_minutes"] / 15) * 15))
                       if evidence.get("samples", 0) >= 3 else self.settings.learning_minutes)
            policy = self.db.get_task_action_state("learning:course-calendar-policy")
            slot = find_slot(task, now, busy, reservations, self.settings, minutes, booked_minutes, policy)
            plan = {**state, "status": "proposed" if slot else "no_slot", "minutes": minutes,
                    "delivery": "proposal_pending", "estimate_samples": evidence.get("samples", 0)}
            if slot:
                plan.update(start=slot[0].isoformat(), end=slot[1].isoformat())
            self.update(task_id, lambda old: plan if old.get("token") == claim else old)
        except CalendarError:
            self.update(task_id, lambda old: {**old, "status": "queued"} if old.get("token") == claim else old)
            raise

    def proposal_content(self, task, state):
        if state["status"] == "no_slot":
            text = (f"<b>No study slot available</b>\n{escape(task['title'])}\n"
                    "No block fits before the deadline under your class, calendar and daily limits. "
                    "Change the deadline or duration, then use /plan again.")
            buttons = self.buttons(task["id"], state, [("retry", "Replan")])
        else:
            start, end = (datetime.fromisoformat(state[key]).astimezone(self.settings.tz) for key in ("start", "end"))
            text = (f"<b>Study proposal</b>\n{escape(task['title'])}\n"
                    f"When {display_time(state['start'], self.settings.tz)}–{end:%H:%M}\nDuration {state['minutes']} minutes\n"
                    "Calendar write waits for your approval.")
            buttons = self.buttons(task["id"], state, [("approve", "Allow"), ("edit", "Edit")])
        return text, buttons

    async def deliver_proposal(self, task, state):
        text, buttons = self.proposal_content(task, state)
        await self.send(task["id"], state, text, buttons, "proposal")

    def edit_menu(self, task_id, state):
        more, lots = min(180, state["minutes"] + 30), min(180, state["minutes"] * 2)
        options = self.buttons(task_id, state, [("more", f"A bit longer · {more} min"),
            ("lots", f"Much longer · {lots} min"), ("shuffle", "Random other time"), ("manual", "Choose manually")])
        return {"messages": [{"text": "How would you like to change this proposal?\n"
            "Longer blocks may move to another available time. A new Allow is still required.",
            "reply_markup": {"inline_keyboard": [[button] for button in options]},
            "learning_menu": {"task_id": task_id, "token": state["token"]}}]}

    async def book(self, task, state, now):
        """Reconcile a deterministic event ID before retrying a calendar insertion."""
        event_id = sha256(f"chronos:{task['id']}:{state['token']}".encode()).hexdigest()
        await self.calendar.verify_owner()
        event = await self.calendar.event(event_id)
        if event is None:
            if task_fields(task) != state["snapshot"] or datetime.fromisoformat(state["start"]) <= now:
                self.update(task["id"], lambda old: {**old, "status": "queued"})
                return "The task or proposed time changed. A fresh proposal will require approval."
            start, end = (datetime.fromisoformat(state[key]) for key in ("start", "end"))
            busy = await self.calendar.busy(start, end)
            if overlaps(start, end, busy):
                self.update(task["id"], lambda old: {**old, "status": "queued"})
                return "That time is now busy. A new proposal will require approval."
            event = await self.calendar.insert({"id": event_id, "summary": task["title"],
                "description": "Chronos study block. Approved by the owner. Task #" + str(task["id"]),
                "start": {"dateTime": state["start"], "timeZone": self.settings.timezone},
                "end": {"dateTime": state["end"], "timeZone": self.settings.timezone},
                "visibility": "private", "transparency": "opaque",
                "extendedProperties": {"private": {"chronosTask": str(task["id"]), "chronosToken": state["token"]}}})
        props = event.get("extendedProperties", {}).get("private", {})
        if props.get("chronosToken") != state["token"] or props.get("chronosTask") != str(task["id"]):
            raise CalendarError("Calendar event ownership could not be verified.")
        if event.get("status") == "cancelled":
            self.update(task["id"], lambda old: {**old, "status": "cancelled"})
            return "This study event was cancelled. It has not been recreated."
        def record_booking():
            current = self.get(task["id"])
            day_key = "learning:day:" + datetime.fromisoformat(state["start"]).date().isoformat()
            budget = self.db.get_task_action_state(day_key) or {}
            self.db._save_task_action_state(day_key, {**budget, state["token"]: state["minutes"]})
            self.db._save_task_action_state(f"learning:task:{task['id']}", {**current,
                "status": "scheduled", "event_id": event_id, "event_url": event.get("htmlLink", "")})
        self.db._run_task_action(record_booking)
        return f"Study time booked\n{state['minutes']} minutes · {display_time(state['start'], self.settings.tz)}"

    async def callback(self, action, task_id, token, message_id):
        state = self.get(task_id)
        if not state or state.get("token") != token or state.get("message_id") != message_id:
            return "This study card is no longer active. Use /plan."
        task = next((t for t in self.db.list_open_tasks() if t["id"] == task_id), None)
        if not task:
            return "This task is no longer open. No event was created."
        now = datetime.now(self.settings.tz)
        if action == "edit":
            changed = self.update(task_id, lambda old: {**old, "status": "editing"}
                if old.get("token") == token and old["status"] == "proposed" else old)
            if changed["status"] != "editing":
                return "This proposal has already been handled. Use /plan."
            return self.edit_menu(task_id, state)
        if action in {"more", "lots", "shuffle", "manual"}:
            if state["status"] != "editing":
                return "This edit menu is no longer active. Use Edit on the latest proposal."
            if action != "manual":
                return await self.revise(task_id, token, action, now)
            return {"messages": [{"text": "Reply with date, start time and duration in minutes.\n"
                "Example 10/20 15:00 90\nYou can also use YYYY-MM-DD HH:MM minutes.\n"
                "The revised proposal still needs Allow before booking.",
                "reply_markup": {"force_reply": True}, "learning_edit": {"task_id": task_id, "token": token}}]}
        if action in {"done", "partial", "skipped"}:
            return self.feedback(task, token, action, state["minutes"] if action == "done" else 0 if action == "skipped" else None)
        if action in {"reject", "retry"}:
            permitted = state["status"] in {"proposed", "no_slot", "rejected"}
            if not permitted:
                return "This proposal has already been handled."
            self.update(task_id, lambda old: {**old, "status": "queued" if action == "retry" else "rejected"}
                if old.get("token") == token and old["status"] == state["status"] else old)
            return "A new proposal will be prepared." if action == "retry" else "Skipped. No calendar event was created."
        if action == "approve":
            approved = self.update(task_id, lambda old: {**old, "status": "creating", "approved_at": now.isoformat()}
                if old.get("token") == token and old["status"] == "proposed" else old)
            if approved["status"] == "scheduled":
                return "This study block is already on your calendar."
            if approved["status"] != "creating":
                return "This proposal is no longer available."
            return await self.book(task, approved, now)
        return "Unknown study action."

    async def revise(self, task_id, token, instruction, now=None):
        if not self.settings.enable_learning_calendar:
            return "Learning calendar is disabled."
        now = now or datetime.now(self.settings.tz)
        claim = uuid4().hex
        lease = self.db.mutate_task_action_state("learning:tick", lambda old: {
            "claim": claim, "until": (now + timedelta(minutes=10)).isoformat()}
            if not old or old["until"] <= now.isoformat() else old)
        if lease["claim"] != claim:
            return "Another proposal is being prepared. Reply again shortly."
        try:
            return await self._revise(task_id, token, instruction, now)
        finally:
            self.db.mutate_task_action_state("learning:tick", lambda old: {**old, "until": now.isoformat()}
                if old["claim"] == claim else old)

    async def _revise(self, task_id, token, instruction, now):
        state = self.get(task_id)
        if not state or state.get("token") != token or state["status"] not in {"proposed", "editing"}:
            return "This proposal has already been handled. Use /plan."
        task = next((t for t in self.db.list_open_tasks() if t["id"] == task_id), None)
        if not task:
            return "This task is no longer open."
        if instruction in {"more", "lots", "shuffle"}:
            return await self.suggest_revision(task, state, instruction, now)
        match = re.fullmatch(r"\s*((?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}/\d{1,2}))\s+(\d{1,2}:\d{2})(?:\s+(\d{1,3})(?:\s*(?:minutes|min|分鐘))?)?\s*", instruction, re.IGNORECASE)
        if not match:
            return "Use MM/DD HH:MM minutes, for example 10/20 15:00 90. Reply to the same edit prompt."
        try:
            day = match[1] if "-" in match[1] else f"{now.year}-{int(match[1].split('/')[0]):02d}-{int(match[1].split('/')[1]):02d}"
            start = datetime.fromisoformat(day + "T" + match[2].zfill(5)).replace(tzinfo=self.settings.tz)
            minutes = int(match[3]) if match[3] else state["minutes"]
            end = start + timedelta(minutes=minutes)
        except (ValueError, OverflowError):
            return "Invalid date or time. Reply to the edit prompt with a valid future date."
        stop = datetime.combine(start.date(), time(self.settings.learning_end_hour % 24), self.settings.tz)
        if self.settings.learning_end_hour == 24:
            stop += timedelta(days=1)
        if not 15 <= minutes <= 180 or start <= now or start.hour < self.settings.learning_start_hour or end > stop:
            return (f"Use a future 15–180 minute block within {self.settings.learning_start_hour:02d}:00"
                    f"–{self.settings.learning_end_hour:02d}:00.")
        due = task.get("due_at") or task.get("timing", {}).get("due_date")
        deadline = (datetime.fromisoformat(due).astimezone(self.settings.tz) if due and len(due) > 10 else
                    datetime.combine(datetime.fromisoformat(due).date(), time.max, self.settings.tz) if due else None)
        if deadline and end > deadline:
            return "That block ends after the task deadline. Choose an earlier time."
        others = [self.get(t["id"]) for t in self.db.list_open_tasks() if t["id"] != task_id]
        reservations = [(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                        for s in others if s and s.get("start") and s["status"] in {"proposed", "creating"}]
        budget = sum((self.db.get_task_action_state("learning:day:" + day) or {}).values())
        policy = self.db.get_task_action_state("learning:course-calendar-policy")
        classes = class_windows(start.date(), self.settings.tz, policy)
        load = budget + minutes + sum((b-a).total_seconds()/60 for a,b in classes)
        load += sum((b-a).total_seconds()/60 for a,b in reservations if a.date() == start.date())
        if load > self.settings.learning_daily_minutes:
            return "That day would exceed your class + study limit. Choose another day or a shorter block."
        await self.calendar.verify_owner()
        busy = await self.calendar.busy(start, end)
        if overlaps(start, end, [*busy, *classes, *reservations]):
            return "That time conflicts with a class, calendar event or another proposal. Choose another time."
        revised = {**state, "token": uuid4().hex, "status": "proposed", "start": start.isoformat(),
            "end": end.isoformat(), "minutes": minutes, "snapshot": task_fields(task), "delivery": "proposal_pending"}
        revised.pop("message_id", None)
        saved = self.update(task_id, lambda old: revised if old.get("token") == token and old["status"] in {"proposed", "editing"} else old)
        return ("Updated. Review the new proposal and press Allow to book it." if saved["token"] == revised["token"]
                else "The proposal changed while editing. Use /plan.")

    async def suggest_revision(self, task, state, option, now):
        minutes = min(180, state["minutes"] + 30 if option == "more" else
                      state["minutes"] * 2 if option == "lots" else state["minutes"])
        if option != "shuffle" and minutes <= state["minutes"]:
            return "This block is already at the 180-minute limit. Choose manually or request another study block."
        def instruction(start):
            return f"{start:%Y-%m-%d %H:%M} {minutes}"
        if option != "shuffle" and datetime.fromisoformat(state["start"]) > now:
            reply = await self._revise(task["id"], state["token"], instruction(datetime.fromisoformat(state["start"])), now)
            if reply.startswith("Updated"):
                return reply
        await self.calendar.verify_owner()
        busy = await self.calendar.busy(now, now + timedelta(days=7))
        if option == "shuffle":
            busy = [*busy, (datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"]))]
        states = [self.get(t["id"]) for t in self.db.list_open_tasks() if t["id"] != task["id"]]
        reservations = [(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                        for s in states if s and s.get("start") and s["status"] in {"proposed", "creating"}]
        booked = {(now + timedelta(days=i)).date().isoformat():
            sum((self.db.get_task_action_state("learning:day:" + (now + timedelta(days=i)).date().isoformat()) or {}).values())
            for i in range(8)}
        policy = self.db.get_task_action_state("learning:course-calendar-policy")
        slot = find_slot(task, now, busy, reservations, self.settings, minutes, booked, policy,
                         randomize=option == "shuffle")
        if not slot:
            return "No alternative fits before the deadline under your class and daily limits. Choose another edit option."
        return await self._revise(task["id"], state["token"], instruction(slot[0]), now)

    def feedback(self, task, token, outcome, actual_minutes, reason=""):
        if outcome not in {"done", "partial", "skipped"} or (actual_minutes is not None and not 0 <= actual_minutes <= 720):
            return "Use /studyfeedback <task-id> <minutes> done|partial|skipped [reason]."
        def record():
            state = self.get(task["id"])
            if not state or state.get("token") != token or state.get("status") != "awaiting_feedback":
                return "This session has already been handled or has not ended."
            live = next((item for item in self.db.list_open_tasks() if item["id"] == task["id"]), None)
            if not live or task_fields(live) != state["snapshot"]:
                return "The task changed after booking. Use /plan to review before marking it complete."
            profile = self.db.get_task_action_state("learning:profile") or {}
            if outcome == "done" and actual_minutes is not None and actual_minutes > 0:
                subject = task.get("project") or "general"
                previous = profile.get(subject, {})
                samples = previous.get("samples", 0)
                profile[subject] = {"samples": samples + 1,
                    "average_minutes": (previous.get("average_minutes", actual_minutes) * min(samples, 4)
                                        + actual_minutes) / (min(samples, 4) + 1)}
            if outcome == "done":
                self.db.complete_task(task["id"], datetime.now(self.settings.tz))
            self.db._save_task_action_state("learning:profile", profile)
            recorded = {**state,
                "status": "finished" if outcome == "done" else "queued" if outcome == "partial" else "needs_replan",
                "feedback": {"outcome": outcome, "actual_minutes": actual_minutes,
                    "reason": reason[:1000], "recorded_at": datetime.now(self.settings.tz).isoformat()}}
            self.db._save_task_action_state("learning:history:" + token, recorded)
            self.db._save_task_action_state(f"learning:task:{task['id']}", recorded)
            return ("Completed and recorded. Future duration suggestions will use your reported time." if outcome == "done"
                    else "Recorded. Another proposal will be prepared; writing still requires approval." if outcome == "partial"
                    else "Recorded. Use /plan to request another proposal; writing still requires approval.")
        return self.db._run_task_action(record)

    async def follow_up(self, task, state, now):
        event = await self.calendar.event(state["event_id"])
        if not event or event.get("status") == "cancelled":
            self.update(task["id"], lambda old: {**old, "status": "cancelled"})
            return
        if event.get("extendedProperties", {}).get("private", {}).get("chronosToken") != state["token"]:
            raise CalendarError("Study event ownership changed. Follow-up is paused.")
        end = event.get("end", {}).get("dateTime")
        if not end:
            raise CalendarError("Study event is no longer a timed event. Follow-up is paused.")
        if datetime.fromisoformat(end.replace("Z", "+00:00")) > now:
            return
        state = self.update(task["id"], lambda old: {**old, "status": "awaiting_feedback",
            "delivery": "feedback_pending"} if old["status"] == "scheduled" else old)
        await self.deliver_feedback(task, state)

    async def deliver_feedback(self, task, state):
        text = (f"<b>Study check-in</b>\n{escape(task['title'])}\n"
                f"When {display_time(state['start'], self.settings.tz)}\n"
                f"Planned {state['minutes']} minutes\nHow did it go?\n"
                "For actual time or an interruption reason, send\n"
                f"/studyfeedback {task['id']} 60 done [reason]")
        await self.send(task["id"], state, text, self.buttons(task["id"], state,
            [("done", "Done as planned"), ("partial", "Need more time"), ("skipped", "Did not start")]), "feedback")

    async def tick(self, now=None):
        if not self.settings.enable_learning_calendar:
            return {"enabled": False}
        if not self.settings.learning_enabled_since or not self.calendar.configured():
            return {"enabled": True, "waiting_for": "calendar authorization and activation"}
        now = now or datetime.now(self.settings.tz)
        now = now.astimezone(self.settings.tz)
        if not self.settings.learning_start_hour <= now.hour < self.settings.learning_end_hour:
            return {"quiet_hours": True}
        claim = uuid4().hex
        lease = self.db.mutate_task_action_state("learning:tick", lambda old: {
            "claim": claim, "until": (now + timedelta(minutes=10)).isoformat()}
            if not old or old["until"] <= now.isoformat() else old)
        if lease["claim"] != claim:
            return {"already_running": True}
        try:
            result = await self._tick(now)
            self.db.mutate_task_action_state("learning:health", lambda old: {"failures": 0})
            return result
        except CalendarError as error:
            health = self.db.mutate_task_action_state("learning:health", lambda old: {
                "error": str(error), "failures": (old or {}).get("failures", 0) + 1,
                "notified": (old or {}).get("notified", False)})
            if health["failures"] >= 2 and not health["notified"]:
                self.db.mutate_task_action_state("learning:health", lambda old: {**old, "notified": True})
                await self.telegram.send_message(self.settings.telegram_chat_id,
                    "Learning calendar needs attention. " + str(error) + " Tasks are saved. Check /plan.")
            raise
        finally:
            self.db.mutate_task_action_state("learning:tick", lambda old: {**old, "until": now.isoformat()}
                if old["claim"] == claim else old)

    async def _tick(self, now):
        tasks = self.db.list_open_tasks()
        states = {task["id"]: self.get(task["id"]) for task in tasks}
        # Day buckets retain approved load after task completion without scanning
        # the ever-growing feedback history on every scheduler tick.
        booked_minutes = {}
        for offset in range(8):
            day = (now + timedelta(days=offset)).date().isoformat()
            booked_minutes[day] = sum((self.db.get_task_action_state("learning:day:" + day) or {}).values())
        reservations = [(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
            for s in states.values() if s and s.get("start") and s["status"] in {"proposed", "creating"}]
        processed = 0
        for task in tasks:
            if processed >= 3:
                break
            state = states[task["id"]]
            if not state and datetime.fromisoformat(task["created_at"]) < datetime.fromisoformat(self.settings.learning_enabled_since):
                continue
            if not state or state["status"] in {"queued", "planning"}:
                await self.propose(task, now, reservations, booked_minutes)
                state = self.get(task["id"])
                if state and state.get("start"):
                    reservations.append(tuple(datetime.fromisoformat(state[key]) for key in ("start", "end")))
                processed += 1
            if not state:
                continue
            if state["status"] in {"proposed", "no_slot"} and state.get("delivery") == "proposal_pending":
                await self.deliver_proposal(task, state)
            elif state["status"] == "creating":
                await self.book(task, state, now)
            elif state["status"] == "scheduled":
                if datetime.fromisoformat(state["end"]) <= now:
                    await self.follow_up(task, state, now)
            elif state["status"] == "awaiting_feedback" and state.get("delivery") == "feedback_pending":
                await self.deliver_feedback(task, state)
        return {"enabled": True, "proposals_checked": processed}

    def status(self):
        lines = ["<b>Learning calendar</b>",
            f"Hours {self.settings.learning_start_hour:02d}:00–{self.settings.learning_end_hour:02d}:00",
            f"Default duration {self.settings.learning_minutes} minutes",
            f"Class + study limit {self.settings.learning_daily_minutes // 60} hours/day",
            "Every calendar write requires your Allow button."]
        if not self.calendar.configured():
            lines.append("Google Calendar authorization is still required.")
        labels = {"planning": "Finding a time", "queued": "Waiting for a proposal",
            "proposed": "Waiting for your Allow", "creating": "Calendar confirmation pending",
            "editing": "Waiting for your revised time",
            "scheduled": "Booked", "awaiting_feedback": "Waiting for your progress",
            "no_slot": "No available study time", "needs_replan": "Needs another study block",
            "rejected": "Skipped", "cancelled": "Calendar event removed", "finished": "Completed"}
        for task in self.db.list_open_tasks():
            state = self.get(task["id"])
            if state:
                status = labels.get(state["status"], "Review required")
                if state.get("delivery", "").endswith(("_uncertain", "_sending")):
                    status += " · message delivery needs review"
                lines.append(f"#{task['id']} {escape(task['title'])} · {status}")
        profile = self.db.get_task_action_state("learning:profile") or {}
        for subject, evidence in profile.items():
            if evidence.get("samples", 0) >= 3:
                lines.append(f"{escape(subject)} · {evidence['samples']} completed reports · "
                             f"average {round(evidence['average_minutes'])} minutes")
        return "\n".join(lines)
