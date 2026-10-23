"""Approval-gated study blocks and durable, owner-reported learning feedback."""
import math
import re
from random import choice
from datetime import datetime, timedelta, time
from hashlib import sha256
from html import escape
from uuid import uuid4

from .ai import AIError
from .course_tracking import COURSE_SCHEDULE
from .google_calendar import CalendarError
from .task_actions import task_fields
from .task_timing import display_time
from .tasks import is_course_scope_task, task_title_lines

COURSE_SCOPE_MINUTES = 10


def overlaps(start, end, busy):
    return any(start < stop and end > begin for begin, stop in busy)


def short_task_windows(day, tz, minutes):
    """Reserve lunch and dinner hours for tasks of at most ten minutes."""
    if minutes <= 10:
        return []
    return [(datetime.combine(day, time(hour), tz), datetime.combine(day, time(hour + 1), tz))
            for hour in (12, 17)]


def study_window(instruction, now, default_minutes):
    """Resolve a date/weekday period as availability, never as a task deadline."""
    text = instruction.strip().casefold()
    text = re.sub(r"^(?:改成|改到|安排在|move to|change to)\s*", "", text)
    # Permission wording requests a later slot; it never grants the exception.
    text = re.sub(r"[（(](?:允許|允许|可以|可)(?:超過|超过)(?:九點|九点|9點|9点|21:00)[）)]$", "", text).strip()
    if text in {"tonight", "今天晚上", "今晚"}:
        text = "today evening"
    duration = re.search(r"(?:\s+)(\d{1,3})(?:\s*(?:minutes|min|分鐘|分钟))?$|(?<!\d)(\d{1,3})(?:分鐘|分钟|minutes|min)$", text)
    minutes = int(duration[1] or duration[2]) if duration else default_minutes
    if duration:
        text = text[:duration.start()].strip()
    period = (9, 21)
    for expression, hours in [(r"早上|上午|morning", (9, 12)), (r"下午|afternoon", (12, 18)),
                              (r"晚上|晚間|evening", (18, 21))]:
        if re.search(expression, text):
            text = re.sub(expression, "", text)
            period = hours
            break
    text = re.sub(r"\s+", "", text)
    day = None
    if text in {"明天", "tomorrow", "今天", "today"}:
        day = now.date() + timedelta(days=int(text in {"明天", "tomorrow"}))
    else:
        match = re.fullmatch(r"(下|這|这|本)?(?:星期|週|周)([一二三四五六日天])", text)
        english = re.fullmatch(r"(next|this)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", text)
        if match or english:
            weekday = "一二三四五六日".index(match[2].replace("天", "日")) if match else (
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday").index(english[2])
            prefix = match[1] if match else english[1]
            if prefix in {"下", "next", "這", "这", "本", "this"}:
                day = now.date() - timedelta(days=now.weekday()) + timedelta(days=weekday + (7 if prefix in {"下", "next"} else 0))
            else:
                day = now.date() + timedelta(days=(weekday - now.weekday()) % 7)
                if day == now.date() and now.hour >= period[1]:
                    day += timedelta(days=7)
        else:
            match = re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}", text)
            if not match:
                return None
            try:
                day = datetime.fromisoformat(text if "-" in text else f"{now.year}-{int(text.split('/')[0]):02d}-{int(text.split('/')[1]):02d}").date()
            except ValueError:
                return None
    return (datetime.combine(day, time(period[0]), now.tzinfo),
            datetime.combine(day, time(period[1]), now.tzinfo), minutes)


def class_windows(day, tz, policy=None):
    if policy and (day.isoformat() < policy["first_day"] or day.isoformat() > policy["last_regular_day"]
                   or day.isoformat() in policy.get("excluded_dates", [])):
        return []
    return [(datetime.combine(day, slot.start, tz), datetime.combine(day, slot.end, tz))
            for slot in COURSE_SCHEDULE if slot.weekday == day.weekday()]


def find_slot(task, now, busy, reservations, config, minutes, booked_minutes=None, course_policy=None, randomize=False, window=None):
    """Search 15-minute boundaries; class plus study load never exceeds the cap."""
    due = task.get("due_at") or (task.get("timing") or {}).get("due_date")
    deadline = (datetime.fromisoformat(due).astimezone(config.tz) if due and len(due) > 10 else
                datetime.combine(datetime.fromisoformat(due).date(), time.max, config.tz) if due else
                now + timedelta(days=7))
    horizon = min(deadline, window[1] if window else now + timedelta(days=7))
    candidates = []
    for offset in range(8):
        day = (max(now, window[0]) if window else now).date() + timedelta(days=offset)
        classes = class_windows(day, config.tz, course_policy)
        load = sum((end - start).total_seconds() / 60 for start, end in classes)
        load += (booked_minutes or {}).get(day.isoformat(), 0)
        load += sum((end - start).total_seconds() / 60 for start, end in reservations if start.date() == day)
        if load + minutes > config.learning_daily_minutes:
            continue
        start = max(now + timedelta(minutes=10), datetime.combine(day, time(config.learning_start_hour), config.tz))
        if window:
            start = max(start, window[0])
        start = start.replace(second=0, microsecond=0)
        start += timedelta(minutes=(-start.minute) % 15)
        stop = min(horizon, datetime.combine(day, time(config.learning_end_hour % 24), config.tz)
                   + (timedelta(days=1) if config.learning_end_hour == 24 else timedelta()))
        while start + timedelta(minutes=minutes) <= stop:
            end = start + timedelta(minutes=minutes)
            if not overlaps(start, end, [*busy, *classes, *reservations, *short_task_windows(day, config.tz, minutes)]):
                if not randomize:
                    return start, end
                candidates.append((start, end))
            start += timedelta(minutes=15)
    return choice(candidates) if candidates else None


class LearningCalendar:
    def __init__(self, db, calendar, telegram, settings, ai=None):
        self.db, self.calendar, self.telegram, self.settings = db, calendar, telegram, settings
        self.ai = ai

    def get(self, task_id):
        return self.db.get_task_action_state(f"learning:task:{task_id}")

    def update(self, task_id, transition):
        return self.db.mutate_task_action_state(f"learning:task:{task_id}", transition)

    def sync_queue(self):
        """Keep one durable approval turn, including during legacy migration."""
        tasks = self.db.list_open_tasks()
        def sync():
            states = {task["id"]: self.get(task["id"]) for task in tasks}
            queue = self.db.get_task_action_state("learning:proposal-queue") or {}
            active = queue.get("active_task_id")
            phases = {"planning", "proposed", "editing", "no_slot", "creating"}
            creating = [key for key, state in states.items() if state and state["status"] == "creating"]
            if creating:
                active = creating[0]
            elif not states.get(active) or states[active]["status"] not in phases | {"queued"}:
                active = next((key for key, state in states.items() if state and state["status"] == "editing"), None)
                if active is None:
                    active = next((key for key, state in states.items() if state and state["status"] in phases), None)
            retired = list(queue.get("retire_message_ids", []))
            updates = {}
            for key, state in states.items():
                if key == active or not state or state["status"] not in phases - {"creating"}:
                    continue
                retired.extend(state.get(name) for name in ("message_id", "edit_message_id") if state.get(name))
                replacement = {**state, "status": "queued", "token": uuid4().hex,
                               "requested_minutes": state.get("requested_minutes") or state.get("minutes")}
                for name in ("start", "end", "message_id", "edit_message_id"):
                    replacement.pop(name, None)
                updates[key] = replacement
            for key, state in updates.items():
                self.db._save_task_action_state(f"learning:task:{key}", state)
            self.db._save_task_action_state("learning:proposal-queue", {**queue, "active_task_id": active,
                "retire_message_ids": list(dict.fromkeys(retired))})
            return active
        return self.db._run_task_action(sync)

    def retire(self, *message_ids):
        self.db.mutate_task_action_state("learning:proposal-queue", lambda old: {**(old or {}),
            "retire_message_ids": list(dict.fromkeys([*(old or {}).get("retire_message_ids", []),
                                                     *(value for value in message_ids if value)]))})

    async def retire_cards(self):
        queue = self.db.get_task_action_state("learning:proposal-queue") or {}
        for message_id in queue.get("retire_message_ids", []):
            try:
                result = await self.telegram.request("editMessageReplyMarkup", {"chat_id": self.settings.telegram_chat_id,
                    "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
            except RuntimeError:
                continue  # Durable cleanup retries must not block the current turn.
            missing = result.get("error_code") == 400 and any(value in result.get("description", "").lower()
                for value in ("not modified", "not found", "can't be edited", "cannot be edited"))
            if result.get("ok") or missing:
                self.db.mutate_task_action_state("learning:proposal-queue", lambda old: {**old,
                    "retire_message_ids": [value for value in old.get("retire_message_ids", []) if value != message_id]})

    def request_proposal(self, task_id, *, immediate=False):
        """Atomically give an explicit owner request the next approval turn."""
        def request():
            state = self.get(task_id) or {}
            if state.get('status') == 'backward_managed':
                return f'This task belongs to a backward plan. Use /backward progress {task_id} to review progress.'
            queue = self.db.get_task_action_state("learning:proposal-queue") or {}
            active = queue.get("active_task_id")
            active_state = self.get(active) if active is not None else None
            if state.get("status") in {"creating", "scheduled", "awaiting_feedback"}:
                return "A work block is already scheduled or awaiting an outcome. Use the existing card first."
            if active_state and active_state.get("status") == "creating":
                return f"Calendar confirmation for task #{active} is in progress. Try /plan {task_id} after it finishes."
            retired = list(queue.get("retire_message_ids", []))
            updates = {}
            for task in self.db.list_open_tasks():
                old = self.get(task["id"])
                if not old or old.get("status") not in {"queued", "planning", "proposed", "editing", "no_slot"}:
                    continue
                retired.extend(old[key] for key in ("message_id", "edit_message_id") if old.get(key))
                replacement = {**old, "status": "queued", "token": uuid4().hex,
                    "requested_minutes": old.get("requested_minutes") or old.get("minutes")}
                for key in ("start", "end", "message_id", "edit_message_id"):
                    replacement.pop(key, None)
                updates[task["id"]] = replacement
            current = updates.get(task_id, state)
            if current.get('duration_source') == 'nightly_ai':
                current = dict(current)
                for key in ('nightly_slot', 'nightly_batch', 'nightly_overview', 'nightly_reason',
                            'duration_source', 'duration_estimate', 'requested_minutes'):
                    current.pop(key, None)
            for key, replacement in updates.items():
                self.db._save_task_action_state(f"learning:task:{key}", replacement)
            self.db._save_task_action_state(f"learning:task:{task_id}",
                {**current, "status": "queued", "token": uuid4().hex})
            self.db._save_task_action_state("learning:proposal-queue", {**queue,
                "active_task_id": task_id, "retire_message_ids": list(dict.fromkeys(retired))})
            hour = datetime.now(self.settings.tz).hour
            timing = ("Preparing its execution plan" if immediate or self.settings.learning_start_hour <= hour < self.settings.learning_end_hour
                else f"Execution planning resumes at {self.settings.learning_start_hour:02d}:00 after quiet hours")
            return f"Task #{task_id} is next. {timing}; Calendar writing requires confirmation."
        return self.db._run_task_action(request)

    async def nightly_proposals(self, now=None):
        from .nightly_planning import run
        return await run(self, now)

    def waiting_turn(self, active):
        return (f"Confirm or adjust the current execution plan first (task #{active}). This task is queued."
                if active else "This task is queued. Its execution plan will be prepared shortly.")

    async def send(self, task_id, state, text, buttons, kind):
        claim = uuid4().hex
        sending = self.update(task_id, lambda old: {**old, "delivery_claim": claim, "delivery": kind + "_sending"}
            if old and old.get("token") == state["token"] and old.get("delivery") == kind + "_pending" else old)
        if not sending or sending.get("delivery_claim") != claim:
            return
        try:
            result = await self.telegram.send_message(self.settings.telegram_chat_id, text,
                parse_mode="HTML", reply_markup={"inline_keyboard": [buttons[i:i+2] for i in range(0, len(buttons), 2)] if kind == "proposal" else [buttons]})
        except Exception:
            self.update(task_id, lambda old: {**old, "delivery": kind + "_uncertain"})
            raise CalendarError("Planning message delivery is uncertain. Inspect /plan before retrying.") from None
        if not result.get("ok"):
            self.update(task_id, lambda old: {**old, "delivery": kind + "_pending"})
            raise CalendarError("Planning message was rejected; it will be retried.")
        self.update(task_id, lambda old: {**old, "delivery": kind + "_sent",
            "message_id": result.get("result", {}).get("message_id")})
        if kind == "proposal":
            self.retire(*state.get("retire_on_send", []))

    def buttons(self, task_id, state, values):
        return [{"text": label, "callback_data": f"learn:{action}:{task_id}:{state['token']}"}
                for action, label in values]

    async def propose(self, task, now, reservations, booked_minutes=None):
        task_id = task["id"]
        claim = uuid4().hex
        def claim_turn():
            old = self.get(task_id)
            queue = self.db.get_task_action_state("learning:proposal-queue") or {}
            if queue.get("active_task_id") not in {None, task_id}:
                return None
            if old and old["status"] != "queued" and not (old["status"] == "planning" and old["lease_until"] < now.isoformat()):
                return None
            if old and old.get('duration_source') == 'nightly_ai' and old.get('snapshot') != task_fields(task):
                self.db._save_task_action_state(f"learning:task:{task_id}", {**old, 'status': 'needs_replan'})
                return None
            state = {"status": "planning", "token": claim,
                "lease_until": (now + timedelta(minutes=3)).isoformat(), "snapshot": task_fields(task),
                "nightly_batch": (old or {}).get("nightly_batch"),
                **{k: old[k] for k in ('nightly_slot', 'nightly_overview', 'nightly_reason') if old and k in old},
                "requested_minutes": (old or {}).get("requested_minutes"),
                "duration_source": (old or {}).get("duration_source"),
                "duration_estimate": (old or {}).get("duration_estimate"),
                "progress_note": (old or {}).get("progress_note", ""),
                "progress": (old or {}).get("progress"),
                "retire_on_send": [*((old or {}).get("retire_on_send", [])),
                    *((old or {}).get(key) for key in ("message_id", "edit_message_id") if (old or {}).get(key))]}
            self.db._save_task_action_state(f"learning:task:{task_id}", state)
            self.db._save_task_action_state("learning:proposal-queue", {**queue, "active_task_id": task_id})
            return state
        state = self.db._run_task_action(claim_turn)
        if not state:
            return
        try:
            await self.calendar.verify_owner()
            window = state.get('nightly_slot') if state.get('duration_source') == 'nightly_ai' else None
            if window:
                begin, stop = (datetime.fromisoformat(window[k]) for k in ('start', 'end'))
                busy = await self.calendar.busy(begin, stop)
            else:
                busy = await self.calendar.busy(now, now + timedelta(days=7))
            profile = self.db.get_task_action_state("learning:profile") or {}
            subject = task.get("project") or "general"
            evidence = profile.get(subject, {})
            minutes = (max(15, min(180, math.ceil(evidence["average_minutes"] / 15) * 15))
                       if evidence.get("samples", 0) >= 3 else self.settings.learning_minutes)
            minutes = state.get("requested_minutes") or minutes
            source = state.get("duration_source") or ("profile" if evidence.get("samples", 0) >= 3 else "default")
            owner_effort = (task.get("timing") or {}).get("estimated_minutes")
            if owner_effort and source not in {"owner", "nightly_ai"}:
                minutes, source = min(180, owner_effort), "owner"
            if is_course_scope_task(task) and source not in {"owner", "nightly_ai"}:
                minutes, source = COURSE_SCOPE_MINUTES, "course_scope_rule"
            elif source not in {"owner", "nightly_ai"} and self.ai is not None:
                estimate = state.get("duration_estimate")
                if not estimate or estimate.get("snapshot") != task_fields(task):
                    try:
                        output = await self.ai.estimate_duration(task, evidence)
                        estimate = {**output.model_dump(), "snapshot": task_fields(task)}
                    except AIError:
                        estimate = None
                if estimate:
                    minutes = min(180, math.ceil(estimate["estimated_minutes"] / 5) * 5)
                    source = "ai"
                    state["duration_estimate"] = estimate
                else:
                    state["duration_estimate"] = None
                    source = "profile" if evidence.get("samples", 0) >= 3 else "default"
                    minutes = (max(15, min(180, math.ceil(evidence["average_minutes"] / 15) * 15))
                               if evidence.get("samples", 0) >= 3 else self.settings.learning_minutes)
            policy = self.db.get_task_action_state("learning:course-calendar-policy")
            if source == 'nightly_ai':
                from .nightly_planning import slot_allowed
                slot = slot_allowed(self, task, state, now, [*busy, *reservations], booked_minutes)
            else:
                slot = find_slot(task, now, busy, reservations, self.settings, minutes, booked_minutes, policy)
            plan = {**state, "status": "proposed" if slot else "no_slot", "minutes": minutes,
                    "duration_source": source, "delivery": "proposal_pending", "estimate_samples": evidence.get("samples", 0)}
            if slot:
                plan.update(start=slot[0].isoformat(), end=slot[1].isoformat())
            else:
                # A deadline-triggered plan may need to displace a movable,
                # owner-only activity.  Reflow remains a preview: no Calendar
                # write happens until the owner confirms the full arrangement.
                due = task.get("due_at") or (task.get("timing") or {}).get("due_date")
                horizon = now + timedelta(days=7)
                if due:
                    deadline = (datetime.fromisoformat(due).astimezone(self.settings.tz)
                        if len(due) > 10 else
                        datetime.combine(datetime.fromisoformat(due).date(), time.max, self.settings.tz))
                    horizon = min(horizon, deadline)
                if horizon > now and source != 'nightly_ai':
                    from .study_reflow import preview
                    reflow = await preview(self, task, {**plan, "start": horizon.isoformat()}, now)
                    if reflow:
                        slot, moves = reflow
                        plan.update(status="proposed", start=slot[0].isoformat(), end=slot[1].isoformat(),
                                    calendar_moves=moves)
            self.update(task_id, lambda old: plan if old.get("token") == claim else old)
        except CalendarError:
            self.update(task_id, lambda old: {**old, "status": "queued"} if old.get("token") == claim else old)
            raise

    def proposal_content(self, task, state):
        if state["status"] == "no_slot":
            text = (f"<b>No viable execution window</b>\n{escape(task['title'])}\n"
                    "No viable work block fits before the deadline within your fixed commitments and workload limits. "
                    "Change the deadline or duration, then use /plan again.")
            buttons = self.buttons(task["id"], state, [("retry", "Replan"), ("postpone", "Defer")])
        else:
            end = datetime.fromisoformat(state["end"]).astimezone(self.settings.tz)
            text = (f"<b>Execution plan</b>\n{escape(task['title'])}\n"
                    f"{display_time(state['start'], self.settings.tz)}–{end:%H:%M}")
            if state.get('duration_source') == 'nightly_ai':
                estimate = state.get('duration_estimate', {}).get('estimated_minutes')
                text += f"\n{state['minutes']} min planned · estimated remaining work {estimate} min"
                text += '\n' + escape(state.get('nightly_reason', ''))
                if state.get('nightly_overview'):
                    text = state['nightly_overview'] + '\n\n' + text
            if state.get("progress_note"):
                text += f"\n\nExecution update · {escape(state['progress_note'])}"
            if state.get("calendar_moves"):
                text += "\n\n<b>Schedule changes requiring approval</b>"
                for move in state["calendar_moves"]:
                    text += (f"\n{escape(move['title'])}\n"
                        f"{display_time(move['old_start'], self.settings.tz)}–{datetime.fromisoformat(move['old_end']):%H:%M}"
                        f" → {display_time(move['start'], self.settings.tz)}–{datetime.fromisoformat(move['end']):%H:%M}")
            buttons = self.buttons(task["id"], state, [
                ("approve", "Confirm schedule"), ("edit", "Adjust plan"), ("postpone", "Defer")])
        return text, buttons

    async def deliver_proposal(self, task, state):
        text, buttons = self.proposal_content(task, state)
        await self.send(task["id"], state, text, buttons, "proposal")

    def edit_menu(self, task_id, state):
        extra = 5 if is_course_scope_task(state["snapshot"]) else 30
        more, lots = min(180, state["minutes"] + extra), min(180, state["minutes"] * 2)
        shorter = max(10, math.ceil(state["minutes"] * 0.75 / 5) * 5)
        values = []
        if more > state["minutes"]:
            values.append(("more", f"{more} min"))
        if lots > more:
            values.append(("lots", f"{lots} min"))
        if shorter < state["minutes"]:
            values.append(("shorter", f"{shorter} min"))
        options = self.buttons(task_id, state, [*values, ("earlier", "Earlier"), ("later", "Later"),
            ("shuffle", "Find another slot"), ("manual", "Set manually"), ("postpone", "Defer")])
        end = datetime.fromisoformat(state["end"]).astimezone(self.settings.tz)
        return {"messages": [{"text": f"Current execution plan · {display_time(state['start'], self.settings.tz)}–{end:%H:%M}\n"
            "How should I adjust this plan?\n"
            "A revised plan may move another eligible commitment and still requires confirmation.",
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
                return "The task or proposed time changed. A fresh execution plan will require approval."
            start, end = (datetime.fromisoformat(state[key]) for key in ("start", "end"))
            if state.get('duration_source') == 'nightly_ai':
                from .nightly_planning import slot_allowed
                current_busy = await self.calendar.busy(start, end)
                if slot_allowed(self, task, state, now, current_busy) is None:
                    self.update(task['id'], lambda old: {**old, 'status': 'needs_replan'})
                    return 'Tomorrow’s allocation no longer fits. No event was created; request a fresh plan.'
            if overlaps(start, end, short_task_windows(start.astimezone(self.settings.tz).date(), self.settings.tz,
                                                      (end - start).total_seconds() / 60)):
                self.update(task["id"], lambda old: {**old, "status": "queued"})
                return "12:00–13:00 and 17:00–18:00 allow only tasks of at most 10 minutes. A new execution plan will require approval."
            cutoff = datetime.combine(start.astimezone(self.settings.tz).date(), time(self.settings.learning_end_hour % 24), self.settings.tz)
            if self.settings.learning_end_hour == 24:
                cutoff += timedelta(days=1)
            if end > cutoff and state.get("late_window") != {"start": state["start"], "end": state["end"]}:
                self.update(task["id"], lambda old: {**old, "status": "queued"})
                return "A late work block needs confirmation. A fresh plan will require approval."
            if state.get("calendar_moves"):
                from .study_reflow import apply as apply_reflow
                try:
                    await apply_reflow(self, task, state, now)
                except CalendarError as error:
                    from .study_reflow import reconcile
                    await reconcile(self, state)
                    self.update(task["id"], lambda old, message=str(error): {**old, "status": "needs_replan", "reflow_error": message})
                    return ("Calendar rearrangement paused. No new work block was created. "
                        "Some reviewed moves may already have succeeded; they are retained for reconciliation. "
                        f"Use /plan {task['id']} to prepare a fresh execution plan. " + str(error))
            busy = current_busy if state.get('duration_source') == 'nightly_ai' else await self.calendar.busy(start, end)
            from .backward_planning import reserved_windows
            busy = [*busy, *reserved_windows(self.db)]
            if overlaps(start, end, busy):
                self.update(task["id"], lambda old: {**old, "status": "queued"})
                return "That time is now busy. A new execution plan will require approval."
            event = await self.calendar.insert({"id": event_id, "summary": task["title"], "colorId": "10",
                "description": "Chronos execution block. Confirmed by YiLi. Task #" + str(task["id"]),
                "start": {"dateTime": state["start"], "timeZone": self.settings.timezone},
                "end": {"dateTime": state["end"], "timeZone": self.settings.timezone},
                "visibility": "private", "transparency": "opaque",
                "extendedProperties": {"private": {"chronosTask": str(task["id"]), "chronosToken": state["token"]}}})
        props = event.get("extendedProperties", {}).get("private", {})
        if props.get("chronosToken") != state["token"] or props.get("chronosTask") != str(task["id"]):
            raise CalendarError("Calendar event ownership could not be verified.")
        if event.get("status") == "cancelled":
            self.update(task["id"], lambda old: {**old, "status": "cancelled"})
            return "This work block was cancelled. It has not been recreated."
        def record_booking():
            current = self.get(task["id"])
            day_key = "learning:day:" + datetime.fromisoformat(state["start"]).date().isoformat()
            budget = self.db.get_task_action_state(day_key) or {}
            queue = self.db.get_task_action_state("learning:proposal-queue") or {}
            self.db._save_task_action_state(day_key, {**budget, state["token"]: state["minutes"]})
            self.db._save_task_action_state(f"learning:task:{task['id']}", {**current,
                "status": "scheduled", "event_id": event_id, "event_url": event.get("htmlLink", "")})
            self.db._save_task_action_state("learning:proposal-queue", {**queue,
                "active_task_id": None if queue.get("active_task_id") == task["id"] else queue.get("active_task_id"),
                "retire_message_ids": list(dict.fromkeys([*queue.get("retire_message_ids", []),
                    *(current.get(key) for key in ("message_id", "edit_message_id") if current.get(key))]))})
        self.db._run_task_action(record_booking)
        return f"Time block scheduled\n{display_time(state['start'], self.settings.tz)}–{datetime.fromisoformat(state['end']):%H:%M}"

    async def callback(self, action, task_id, token, message_id):
        active = self.sync_queue()
        state = self.get(task_id)
        task = next((t for t in self.db.list_open_tasks() if t["id"] == task_id), None)
        if not task:
            return "This task is no longer open. No event was created."
        if not state:
            return f"No saved execution plan for this task. Use /plan {task_id}."
        if state.get('status') == 'backward_managed':
            return f'This task belongs to a backward plan. Use /backward progress {task_id}.'
        if state["status"] == "postponed":
            return f"This task is waiting for external conditions. Use /plan {task_id} when ready."
        if state["status"] in {"queued", "planning", "proposed", "editing", "no_slot", "rejected"} and task_id != active:
            return self.waiting_turn(active)
        menu_action = action in {"more", "lots", "shuffle", "manual"}
        expected_message = state.get("edit_message_id", state.get("message_id")) if menu_action else state.get("message_id")
        valid_message = (message_id in {state.get("message_id"), state.get("edit_message_id")}
                         if action in {"postpone", "earlier", "later", "longer", "shorter"} else expected_message == message_id)
        if state.get("token") != token or not valid_message:
            # Old buttons may request a fresh view, but can never approve or
            # apply an obsolete duration/time choice to a replacement proposal.
            if state["status"] in {"proposed", "editing"}:
                if action == "edit" or state["status"] == "editing":
                    changed = self.update(task_id, lambda old: {**old, "status": "editing"}
                        if old.get("token") == state["token"] and old["status"] in {"proposed", "editing"} else old)
                    if changed.get("token") == state["token"] and changed["status"] == "editing":
                        return self.edit_menu(task_id, changed)
                    return "The execution plan changed. Open Adjust plan on the latest card."
                text, buttons = self.proposal_content(task, state)
                return {"messages": [{"text": "This execution plan was updated. Review the latest version below.\n\n" + text,
                    "parse_mode": "HTML", "reply_markup": {"inline_keyboard": [buttons[i:i+2] for i in range(0, len(buttons), 2)]},
                    "learning_proposal": {"task_id": task_id, "token": state["token"]}}]}
            return "This execution plan has already been handled. Check /plan for its current status."
        now = datetime.now(self.settings.tz)
        if action == "postpone":
            def postpone():
                current = self.get(task_id)
                queue = self.db.get_task_action_state("learning:proposal-queue") or {}
                pending_key = f"learning:edit-chat:{self.settings.telegram_chat_id}"
                pending = self.db.get_task_action_state(pending_key)
                if (not current or current.get("token") != token
                        or current["status"] not in {"proposed", "editing", "no_slot"}
                        or queue.get("active_task_id") != task_id):
                    return False
                waiting = {**current, "status": "postponed", "token": uuid4().hex,
                    "requested_minutes": current.get("requested_minutes") or current.get("minutes"),
                    "postponed_at": now.isoformat()}
                for key in ("start", "end", "message_id", "edit_message_id"):
                    waiting.pop(key, None)
                self.db._save_task_action_state(f"learning:task:{task_id}", waiting)
                self.db._save_task_action_state("learning:proposal-queue", {**queue, "active_task_id": None,
                    "retire_message_ids": list(dict.fromkeys([*queue.get("retire_message_ids", []),
                        *(current.get(key) for key in ("message_id", "edit_message_id") if current.get(key))]))})
                if pending and pending.get("token") == token:
                    self.db._save_task_action_state(pending_key, {})
                return True
            if not self.db._run_task_action(postpone):
                return "This execution plan changed or was already approved. No scheduled event was changed."
            return (f"Deferred · {task['title']}\nWaiting for external conditions. The deadline is unchanged.\n"
                    f"Use /plan {task_id} when ready. The next task will be planned.")
        if action in {"earlier", "later", "longer", "shorter"}:
            return await self.revise(task_id, token, "more" if action == "longer" else action, now)
        if action == "edit":
            changed = self.update(task_id, lambda old: {**old, "status": "editing"}
                if old.get("token") == token and old["status"] == "proposed" else old)
            if changed["status"] != "editing":
                return "This execution plan has already been handled. Use /plan."
            return self.edit_menu(task_id, state)
        if action in {"more", "lots", "shuffle", "manual"}:
            if state["status"] != "editing":
                return "This adjustment menu is no longer active. Use Adjust plan on the latest execution plan."
            if action != "manual":
                return await self.revise(task_id, token, action, now)
            return {"messages": [{"text": "<b>Adjust execution plan</b>\n\n"
                "Reply with a start time, duration, or both.\n\n"
                "<b>Examples</b>\n"
                "• One hour on Sunday morning\n"
                "• Sunday morning — keep the current duration\n"
                "• <code>10/20 15:00 60</code> — 60 minutes\n\n"
                "Send your reply here. /cancel returns to the edit menu.\n\n"
                "Review the revised execution plan, then tap <b>Confirm schedule</b> to book.",
                "parse_mode": "HTML",
                "reply_markup": {"force_reply": True}, "learning_edit": {"task_id": task_id, "token": token}}]}
        if action == "partial" and state["status"] == "awaiting_feedback":
            return {"messages": [{"text": "Report focused minutes | completed pages/total pages | next page.\nExample: 135 | 20/48 | 21\nExclude breaks and phone time. /cancel keeps the session open.",
                "reply_markup": {"force_reply": True},
                "learning_edit": {"task_id": task_id, "token": token, "mode": "feedback"}}]}
        if action in {"done", "partial", "skipped"}:
            return self.feedback(task, token, action, state["minutes"] if action == "done" else 0 if action == "skipped" else None)
        if action in {"reject", "retry"}:
            permitted = state["status"] in {"proposed", "no_slot", "rejected"}
            if not permitted:
                return "This execution plan has already been handled."
            self.update(task_id, lambda old: {**old, "status": "queued" if action == "retry" else "rejected"}
                if old.get("token") == token and old["status"] == state["status"] else old)
            return "A new execution plan will be prepared." if action == "retry" else "Skipped. No calendar event was created."
        if action == "approve":
            approved = self.update(task_id, lambda old: {**old, "status": "creating", "approved_at": now.isoformat()}
                if old.get("token") == token and old["status"] == "proposed" else old)
            if approved["status"] == "scheduled":
                return "This work block is already on your calendar."
            if approved["status"] != "creating":
                return "This execution plan is no longer available."
            return await self.book(task, approved, now)
        return "Unknown planning action."

    async def revise(self, task_id, token, instruction, now=None):
        if not self.settings.enable_learning_calendar:
            return "Task planning is unavailable."
        state = self.get(task_id)
        booked_edit = state and state.get("status") == "scheduled" and state.get("token") == token
        active = self.sync_queue()
        if active != task_id and not booked_edit:
            return self.waiting_turn(active)
        now = now or datetime.now(self.settings.tz)
        claim = uuid4().hex
        lease = self.db.mutate_task_action_state("learning:tick", lambda old: {
            "claim": claim, "until": (now + timedelta(minutes=10)).isoformat()}
            if not old or old["until"] <= now.isoformat() else old)
        if lease["claim"] != claim:
            return "Another execution plan is being prepared. Reply again shortly."
        try:
            if booked_edit:
                from .study_reschedule import reschedule
                return await reschedule(self, task_id, token, instruction, now)
            return await self._revise(task_id, token, instruction, now)
        finally:
            self.db.mutate_task_action_state("learning:tick", lambda old: {**old, "until": now.isoformat()}
                if old["claim"] == claim else old)

    async def _revise(self, task_id, token, instruction, now, allow_late=False, calendar_moves=None):
        state = self.get(task_id)
        if not state or state.get("token") != token or state["status"] not in {"proposed", "editing"}:
            return "This execution plan has already been handled. Use /plan."
        task = next((t for t in self.db.list_open_tasks() if t["id"] == task_id), None)
        if not task:
            return "This task is no longer open."
        if re.fullmatch(r"(?:已完成|已經完成|已经完成|完成了|全部完成|done|already done|completed)[。.!！\s]*", instruction.strip(), re.IGNORECASE):
            return self.complete_unbooked(task, state, now)
        pending = state.get("pending_late")
        answer = instruction.strip().casefold()
        if pending and answer in {"yes", "confirm", "是", "確認", "no", "cancel", "否", "不要"}:
            if answer in {"no", "cancel", "否", "不要"}:
                self.update(task_id, lambda old: {k: v for k, v in old.items() if k != "pending_late"}
                            if old.get("token") == token else old)
                return "Late work-block exception cancelled. Your previous plan is unchanged. Keep typing or use /cancel."
            if pending.get("snapshot") != task_fields(task):
                return "The task changed. Send the requested time again."
            if pending["expires_at"] <= now.isoformat():
                return "That confirmation expired. Send the requested time again."
            return await self._revise(task_id, token, pending["instruction"], now, allow_late=True)
        if instruction in {"more", "lots", "shuffle", "earlier", "later", "shorter"}:
            return await self.suggest_revision(task, state, instruction, now)
        match = re.fullmatch(r"\s*((?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}/\d{1,2}))\s+(\d{1,2}:\d{2})(?:\s+(\d{1,3})(?:\s*(?:minutes|min|分鐘))?)?\s*", instruction, re.IGNORECASE)
        if not match:
            window = study_window(instruction, now, state["minutes"])
            if window:
                return await self.revise_window(task, state, window, now)
            if self.ai is not None:
                return await self.revise_natural(task, state, instruction, now)
            return "Use MM/DD HH:MM minutes or a weekday period such as Sunday morning. Keep typing here, or use /cancel."
        try:
            day = match[1] if "-" in match[1] else f"{now.year}-{int(match[1].split('/')[0]):02d}-{int(match[1].split('/')[1]):02d}"
            start = datetime.fromisoformat(day + "T" + match[2].zfill(5)).replace(tzinfo=self.settings.tz)
            minutes = int(match[3]) if match[3] else state["minutes"]
            end = start + timedelta(minutes=minutes)
        except (ValueError, OverflowError):
            return "Invalid date or time. Reply to the edit prompt with a valid future date."
        normal_stop = datetime.combine(start.date(), time(self.settings.learning_end_hour % 24), self.settings.tz)
        if self.settings.learning_end_hour == 24:
            normal_stop += timedelta(days=1)
        stop = datetime.combine(start.date() + timedelta(days=1), time(), self.settings.tz)
        minimum = 10
        if not minimum <= minutes <= 180 or start <= now or start.hour < self.settings.learning_start_hour or end > stop:
            return (f"Use a future {minimum}–180 minute block within {self.settings.learning_start_hour:02d}:00"
                    f"–{self.settings.learning_end_hour:02d}:00.")
        due = task.get("due_at") or task.get("timing", {}).get("due_date")
        deadline = (datetime.fromisoformat(due).astimezone(self.settings.tz) if due and len(due) > 10 else
                    datetime.combine(datetime.fromisoformat(due).date(), time.max, self.settings.tz) if due else None)
        if deadline and end > deadline:
            return f"That block ends after the task deadline {display_time(due, self.settings.tz)}. Choose an earlier time."
        if overlaps(start, end, short_task_windows(start.date(), self.settings.tz, minutes)):
            return "12:00–13:00 and 17:00–18:00 allow only tasks of at most 10 minutes. Choose another time or a shorter block."
        others = {t["id"]: self.get(t["id"]) for t in self.db.list_open_tasks() if t["id"] != task_id}
        hard, pending = [], {}
        for other_id, other in others.items():
            if not other or not other.get("start"):
                continue
            window = (datetime.fromisoformat(other["start"]), datetime.fromisoformat(other["end"]))
            if other["status"] == "creating":
                hard.append(window)
            elif other["status"] == "proposed" and window[0] > now:
                pending[other_id] = window
        budget = sum((self.db.get_task_action_state("learning:day:" + day) or {}).values())
        original_budget = budget
        for move in calendar_moves or []:
            if move["task_id"] is not None:
                other = self.get(move["task_id"])
                if move["old_start"][:10] == day:
                    budget -= other["minutes"]
                if move["start"][:10] == day:
                    budget += other["minutes"]
        policy = self.db.get_task_action_state("learning:course-calendar-policy")
        classes = class_windows(start.date(), self.settings.tz, policy)
        load = budget + minutes + sum((b-a).total_seconds()/60 for a,b in classes)
        load += sum((b-a).total_seconds()/60 for a,b in hard if a.date() == start.date())
        if load > self.settings.learning_daily_minutes:
            return "That day would exceed your workload limit. Choose another day or a shorter block."
        await self.calendar.verify_owner()
        busy = await self.calendar.busy(start, end)
        if overlaps(start, end, [*([] if calendar_moves else busy), *classes, *hard]):
            return "That time conflicts with a class, calendar event or approved booking. Choose another time."
        displaced = {other_id for other_id, window in pending.items() if overlaps(start, end, [window])}
        remaining = sorted(((other_id, window) for other_id, window in pending.items()
                            if other_id not in displaced and window[0].date() == start.date()),
                           key=lambda item: item[1][0], reverse=True)
        pending_load = sum((b-a).total_seconds()/60 for _, (a,b) in remaining)
        for other_id, (begin, stop) in remaining:
            if load + pending_load <= self.settings.learning_daily_minutes:
                break
            displaced.add(other_id)
            pending_load -= (stop-begin).total_seconds()/60
        if end > normal_stop and not allow_late:
            pending = {"snapshot": task_fields(task), "instruction": f"{start:%Y-%m-%d %H:%M} {minutes}",
                       "expires_at": (now + timedelta(minutes=15)).isoformat()}
            self.update(task_id, lambda old: {**old, "status": "editing", "pending_late": pending}
                        if old.get("token") == token and old.get("status") in {"proposed", "editing"} else old)
            return (f"This work block would run {display_time(start.isoformat(), self.settings.tz)}–{end:%H:%M}, "
                    f"past your {self.settings.learning_end_hour:02d}:00 limit.\n\n"
                    "Make an exception for this session only? Reply Yes or No.\n"
                    "Yes prepares a new execution plan; confirmation is still required to book it.")
        revised = {**state, "token": uuid4().hex, "status": "proposed", "start": start.isoformat(),
            "end": end.isoformat(), "minutes": minutes, "requested_minutes": minutes, "duration_source": "owner",
            "snapshot": task_fields(task), "delivery": "proposal_pending"}
        revised["retire_on_send"] = [state[key] for key in ("message_id", "edit_message_id") if state.get(key)]
        revised.pop("calendar_moves", None)
        if calendar_moves:
            revised["calendar_moves"] = calendar_moves
        revised.pop("pending_late", None)
        revised.pop("late_window", None)
        if end > normal_stop and allow_late:
            revised["late_window"] = {"start": start.isoformat(), "end": end.isoformat()}
        revised.pop("message_id", None)
        revised.pop("edit_message_id", None)
        def signature(value):
            return tuple((value or {}).get(key) for key in ("status", "token", "start", "end", "minutes"))
        def save():
            current = self.get(task_id)
            fresh = {other_id: self.get(other_id) for other_id in others}
            live_budget = sum((self.db.get_task_action_state("learning:day:" + day) or {}).values())
            if (not current or current.get("token") != token or current["status"] not in {"proposed", "editing"}
                    or live_budget != original_budget or any(signature(fresh[k]) != signature(v) for k,v in others.items())):
                return False
            self.db._save_task_action_state(f"learning:task:{task_id}", revised)
            for other_id in displaced:
                self.db._save_task_action_state(f"learning:task:{other_id}", {**fresh[other_id], "status": "queued",
                    "requested_minutes": fresh[other_id]["minutes"], "reflowed_by": task_id})
            return True
        if not self.db._run_task_action(save):
            return "Another execution plan or booking changed while editing. Choose the adjustment again."
        return ("Updated. Review the new execution plan and press Confirm schedule to book it."
                + (f" {len(displaced)} unapproved plans will be rearranged; they still need confirmation." if displaced else ""))

    def complete_unbooked(self, task, state, now):
        """Complete the current unapproved task only on explicit owner instruction."""
        def complete():
            current = self.get(task["id"])
            queue = self.db.get_task_action_state("learning:proposal-queue") or {}
            pending_key = f"learning:edit-chat:{self.settings.telegram_chat_id}"
            pending = self.db.get_task_action_state(pending_key)
            live = self.db._load_task_action(task["id"])
            if (not current or current.get("token") != state["token"]
                    or current.get("status") not in {"proposed", "editing"}
                    or queue.get("active_task_id") != task["id"]
                    or not live or live["task"] != state["snapshot"]):
                return False
            if not self.db.complete_task(task["id"], now):
                return False
            self.db._save_task_action_state(f"learning:task:{task['id']}", {**current,
                "status": "finished", "token": uuid4().hex,
                "feedback": {"outcome": "done", "actual_minutes": None,
                    "reason": "Owner reported completion before booking", "recorded_at": now.isoformat()}})
            self.db._save_task_action_state("learning:proposal-queue", {**queue, "active_task_id": None,
                "retire_message_ids": list(dict.fromkeys([*queue.get("retire_message_ids", []),
                    *(current.get(key) for key in ("message_id", "edit_message_id") if current.get(key))]))})
            if pending and pending.get("token") == state["token"]:
                self.db._save_task_action_state(pending_key, {})
            return True
        if not self.db._run_task_action(complete):
            return "The task or execution plan changed. Review its latest status."
        return f"Completed · {task['title']}\nProposal withdrawn. No calendar event was created."

    async def revise_natural(self, task, state, instruction, now):
        try:
            edit = await self.ai.parse_schedule_edit(instruction, task, state, now)
        except AIError:
            return "I could not interpret that adjustment. The plan is unchanged. Try again or send a date, time and minutes, e.g. 10/20 15:00 60."
        if not edit.actionable:
            return "Describe the duration, start time or both, e.g. \"One hour soon; partly completed.\" The plan is unchanged."
        minutes = edit.minutes if edit.minutes is not None else state["minutes"]
        if edit.timing == "window":
            try:
                begin, stop = datetime.fromisoformat(edit.start), datetime.fromisoformat(edit.window_end)
                if begin.tzinfo is None or stop.tzinfo is None or begin >= stop:
                    raise ValueError
            except (ValueError, TypeError):
                return "Please provide a date and time range. The plan is unchanged."
            reply = await self.revise_window(task, state, (begin.astimezone(self.settings.tz), stop.astimezone(self.settings.tz), minutes), now)
        elif edit.timing == "soon":
            reply = await self.revise_window(task, state, (now, now + timedelta(days=7), minutes), now)
        else:
            try:
                start = datetime.fromisoformat(edit.start) if edit.timing == "exact" and edit.start else (
                    datetime.fromisoformat(state["start"]) if edit.timing == "keep" else None)
                if start is None or start.tzinfo is None:
                    raise ValueError
                start = start.astimezone(self.settings.tz)
            except (ValueError, TypeError, KeyError):
                return "Please provide an exact date and time. The plan is unchanged."
            reply = await self._revise(task["id"], state["token"], f"{start:%Y-%m-%d %H:%M} {minutes}", now)
        current = self.get(task["id"])
        if current and current.get("token") != state["token"] and current.get("status") == "proposed":
            revised_token = current["token"]
            self.update(task["id"], lambda old: {**old, "progress_note": edit.progress_note} if
                        old.get("token") == revised_token and old.get("status") == "proposed" else old)
        return reply

    async def revise_window(self, task, state, window, now):
        begin, stop, minutes = window
        minimum = 10
        if not minimum <= minutes <= 180:
            return f"Choose {minimum}–180 minutes per block. Keep typing here or use /cancel."
        due = task.get("due_at") or (task.get("timing") or {}).get("due_date")
        if due:
            deadline = (datetime.fromisoformat(due).astimezone(self.settings.tz) if len(due) > 10 else
                        datetime.combine(datetime.fromisoformat(due).date(), time.max, self.settings.tz))
            if begin >= deadline:
                return f"That period is after the task deadline {display_time(due, self.settings.tz)}. Choose an earlier day."
        await self.calendar.verify_owner()
        busy = await self.calendar.busy(max(now, begin), stop) if stop > now else []
        booked = {begin.date().isoformat(): sum((self.db.get_task_action_state("learning:day:" + begin.date().isoformat()) or {}).values())}
        policy = self.db.get_task_action_state("learning:course-calendar-policy")
        slot = find_slot(task, now, busy, [], self.settings, minutes, booked, policy, window=(begin, stop))
        if not slot and stop.hour == self.settings.learning_end_hour and stop.minute == 0:
            late_stop = datetime.combine(stop.date() + timedelta(days=1), time(), self.settings.tz)
            late_busy = await self.calendar.busy(max(now, begin), late_stop) if late_stop > now else []
            late_config = self.settings.model_copy(update={"learning_end_hour": 24})
            slot = find_slot(task, now, late_busy, [], late_config, minutes, booked, policy, window=(begin, late_stop))
        if not slot:
            return (f"No continuous {minutes}-minute slot on {display_time(begin.date().isoformat(), self.settings.tz)} "
                    f"between {begin:%H:%M} and {stop:%H:%M} fits your calendar and daily limit. "
                    "Try a shorter block or another period; keep typing here.")
        return await self._revise(task["id"], state["token"], f"{slot[0]:%Y-%m-%d %H:%M} {minutes}", now)

    async def suggest_revision(self, task, state, option, now):
        if option == "earlier":
            stop = datetime.fromisoformat(state["start"])
            await self.calendar.verify_owner()
            busy = await self.calendar.busy(now, stop)
            hard = [(datetime.fromisoformat(other["start"]), datetime.fromisoformat(other["end"]))
                for item in self.db.list_open_tasks() if item["id"] != task["id"]
                for other in [self.get(item["id"])] if other and other.get("start") and other["status"] == "creating"]
            booked = {(now + timedelta(days=i)).date().isoformat():
                sum((self.db.get_task_action_state("learning:day:" + (now + timedelta(days=i)).date().isoformat()) or {}).values())
                for i in range(8)}
            slot = find_slot(task, now, busy, hard, self.settings, state["minutes"], booked,
                self.db.get_task_action_state("learning:course-calendar-policy"), window=(now, stop))
            if not slot:
                from .study_reflow import preview
                reflow = await preview(self, task, state, now)
                if not reflow:
                    return "No earlier arrangement fits while preserving classes, shared events, deadlines and daily limits. Try a shorter block in Edit."
                slot, moves = reflow
                return await self._revise(task["id"], state["token"],
                    f"{slot[0]:%Y-%m-%d %H:%M} {state['minutes']}", now, calendar_moves=moves)
            return await self._revise(task["id"], state["token"], f"{slot[0]:%Y-%m-%d %H:%M} {state['minutes']}", now)
        extra = 5 if is_course_scope_task(task) else 30
        minutes = min(180, state["minutes"] + extra if option == "more" else
                      state["minutes"] * 2 if option == "lots" else
                      max(10, math.ceil(state["minutes"] * 0.75 / 5) * 5) if option == "shorter" else state["minutes"])
        if option in {"more", "lots"} and minutes <= state["minutes"]:
            return "This block is already at the 180-minute limit. Set a time manually or request another work block."
        if option == "shorter" and minutes >= state["minutes"]:
            return "This block is already at the 10-minute minimum. Choose manually for another time."
        def instruction(start):
            return f"{start:%Y-%m-%d %H:%M} {minutes}"
        if option not in {"shuffle", "later"} and datetime.fromisoformat(state["start"]) > now:
            reply = await self._revise(task["id"], state["token"], instruction(datetime.fromisoformat(state["start"])), now)
            if reply.startswith("Updated"):
                return reply
        await self.calendar.verify_owner()
        busy = await self.calendar.busy(now, now + timedelta(days=7))
        if option == "shuffle":
            busy = [*busy, (datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"]))]
        states = [self.get(t["id"]) for t in self.db.list_open_tasks() if t["id"] != task["id"]]
        reservations = [(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                        for s in states if s and s.get("start") and (s["status"] == "creating"
                            or s["status"] == "proposed" and datetime.fromisoformat(s["start"]) > now)]
        booked = {(now + timedelta(days=i)).date().isoformat():
            sum((self.db.get_task_action_state("learning:day:" + (now + timedelta(days=i)).date().isoformat()) or {}).values())
            for i in range(8)}
        policy = self.db.get_task_action_state("learning:course-calendar-policy")
        window = (max(now, datetime.fromisoformat(state["start"]) + timedelta(minutes=5)), now + timedelta(days=7)) if option == "later" else None
        slot = find_slot(task, now, busy, reservations, self.settings, minutes, booked, policy,
                         randomize=option == "shuffle", window=window)
        if not slot:
            # Unapproved proposals can move. Only an approved/in-flight booking
            # may make actual Calendar availability unavailable to an owner edit.
            hard = [(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                    for s in states if s and s.get("start") and s["status"] == "creating"]
            slot = find_slot(task, now, busy, hard, self.settings, minutes, booked, policy,
                             randomize=option == "shuffle", window=window)
        if not slot:
            return (f"No alternative fits a continuous {minutes}-minute block before the deadline within your "
                    f"{self.settings.learning_start_hour:02d}:00–{self.settings.learning_end_hour:02d}:00 hours, "
                    "confirmed calendar events and the daily workload limit. Try a shorter block or another deadline.")
        return await self._revise(task["id"], state["token"], instruction(slot[0]), now)

    def partial_reply(self, task_id, token, instruction):
        match = re.fullmatch(r"\s*(\d+)\s*\|\s*(\d+)\s*/\s*(\d+)\s*\|\s*(\d+)\s*", instruction)
        task = next((t for t in self.db.list_open_tasks() if t["id"] == task_id), None)
        if not task:
            return "This task is no longer open."
        if not match:
            return "Use focused minutes | completed pages/total pages | next page. Example: 135 | 20/48 | 21"
        minutes, completed, total, next_page = map(int, match.groups())
        if not 0 < minutes <= 720 or not 0 < completed < total or next_page != completed + 1:
            return "Check minutes and page counts; next page should follow completed pages."
        state = self.get(task_id) or {}
        previous = (state.get("progress") or {}).get("completed_pages", 0)
        if state.get("status") == "awaiting_feedback" and completed <= previous:
            return "Completed pages must increase from the previous report."
        return self.feedback(task, token, "partial", minutes,
            f"Completed {completed}/{total} pages. Continue from page {next_page}.",
            {"completed_pages": completed, "total_pages": total, "next_page": next_page, "session_pages": max(1, completed - previous)})

    async def prepare_feedback(self, task, outcome, minutes, reason=""):
        state = self.get(task['id'])
        if not state or state.get('status') != 'scheduled':
            return lambda: self.feedback(task, (state or {}).get('token'), outcome, minutes, reason)
        try:
            event = await self.calendar.event(state['event_id'])
            if not event or event.get('status') == 'cancelled':
                return lambda: 'This booking was cancelled. Use /plan to review it.'
            if event.get('extendedProperties', {}).get('private', {}).get('chronosToken') != state['token']:
                return lambda: 'Work-block ownership could not be verified. Use /plan to review it.'
            end = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
            if end.tzinfo is None:
                raise ValueError('Missing timezone')
        except (CalendarError, KeyError, ValueError, TypeError):
            return lambda: 'Unable to verify this booking right now. Please try /taskupdate again shortly.'
        if end > datetime.now(self.settings.tz):
            return lambda: 'This work session has not ended yet. Report after ' + display_time(end.isoformat(), self.settings.tz) + '.'
        return lambda: self.feedback(task, state['token'], outcome, minutes, reason, verified_booking=state)

    def feedback(self, task, token, outcome, actual_minutes, reason="", progress=None, *, verified_booking=None):
        if outcome not in {"done", "partial", "skipped"} or (actual_minutes is not None and not 0 <= actual_minutes <= 720):
            return "Use /taskupdate <task-id> <minutes> done|partial|skipped [reason]."
        def record():
            state = self.get(task["id"])
            if not state or state.get("token") != token:
                return "This work session is no longer current. Use /plan to review it."
            if state.get('status') == 'scheduled':
                if verified_booking is None:
                    return 'This work session has not ended yet or needs booking verification. Use /taskupdate to check it.'
                if state != verified_booking:
                    return 'This booking changed while checking it. Please try /taskupdate again.'
            elif state.get('status') != 'awaiting_feedback':
                return 'This session has already been handled. Use /plan to review its current status.'
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
            recorded = {**state,
                "status": "finished" if outcome == "done" else "queued" if outcome == "partial" else "needs_replan",
                "feedback": {"outcome": outcome, "actual_minutes": actual_minutes,
                    "reason": reason[:1000], "recorded_at": datetime.now(self.settings.tz).isoformat()}}
            if outcome == "partial":
                recorded["progress_note"] = reason[:1000]
                recorded["duration_estimate"] = None
                if progress:
                    recorded["progress"] = progress
                    remaining = actual_minutes * (progress["total_pages"] - progress["completed_pages"]) / progress["session_pages"]
                    recorded["requested_minutes"] = max(15, min(180, math.ceil(remaining / 15) * 15))
                    recorded["duration_source"] = "owner"
                title = re.sub(r" · Continue.*$", "", task['title'])
                title += (f" · Continue pages {progress['next_page']}–{progress['total_pages']}"
                          if progress else f" · Continue: {reason[:200]}" if reason else " · Continue remaining work")
                successor = self.db.continue_task(task, title, datetime.now(self.settings.tz))
                recorded['status'] = 'finished'
                recorded['successor_task_id'] = successor['id']
                continuation = {key: recorded[key] for key in
                                ('progress', 'progress_note', 'requested_minutes', 'duration_source') if key in recorded}
                continuation.update(status='queued', predecessor_task_id=task['id'])
                self.db._save_task_action_state(f"learning:task:{successor['id']}", continuation)
            self.db._save_task_action_state("learning:profile", profile)
            self.db._save_task_action_state("learning:history:" + token, recorded)
            self.db._save_task_action_state(f"learning:task:{task['id']}", recorded)
            return ("Completed and recorded. Future plans will use your reported time." if outcome == "done"
                    else f"Recorded. Task #{task['id']} completed; remaining work saved as #{successor['id']}." if outcome == "partial"
                    else "Recorded. Use /plan to request another execution plan; writing still requires approval.")
        return self.db._run_task_action(record)

    async def follow_up(self, task, state, now):
        event = await self.calendar.event(state["event_id"])
        if not event or event.get("status") == "cancelled":
            self.update(task["id"], lambda old: {**old, "status": "cancelled"})
            return
        if event.get("extendedProperties", {}).get("private", {}).get("chronosToken") != state["token"]:
            raise CalendarError("Work-block ownership changed. Follow-up is paused.")
        end = event.get("end", {}).get("dateTime")
        if not end:
            raise CalendarError("The work block is no longer a timed event. Follow-up is paused.")
        if datetime.fromisoformat(end.replace("Z", "+00:00")) > now:
            return
        state = self.update(task["id"], lambda old: {**old, "status": "awaiting_feedback",
            "delivery": "feedback_pending"} if old["status"] == "scheduled" else old)
        await self.deliver_feedback(task, state)

    def feedback_content(self, task, state):
        text = (f"<b>Execution check-in</b>\n\n{escape(task['title'])}\n"
                f"{display_time(state['start'], self.settings.tz)} · {state['minutes']} minutes planned\n\n"
                "What is the outcome? Choose below.\n\n"
                "Different duration? For 15 minutes completed, send\n"
                f"<code>/taskupdate {task['id']} 15 done</code>")
        return text, self.buttons(task["id"], state,
            [("done", "Completed"), ("partial", "Partially completed"), ("skipped", "Skipped")])

    async def deliver_feedback(self, task, state):
        text, buttons = self.feedback_content(task, state)
        await self.send(task["id"], state, text, buttons, "feedback")

    async def tick(self, now=None, *, force_proposal=False):
        if not self.settings.enable_learning_calendar:
            return {"enabled": False}
        if not self.settings.learning_enabled_since or not self.calendar.configured():
            return {"enabled": True, "waiting_for": "calendar authorization and activation"}
        now = now or datetime.now(self.settings.tz)
        now = now.astimezone(self.settings.tz)
        claim = uuid4().hex
        lease = self.db.mutate_task_action_state("learning:tick", lambda old: {
            "claim": claim, "until": (now + timedelta(minutes=10)).isoformat()}
            if not old or old["until"] <= now.isoformat() else old)
        if lease["claim"] != claim:
            return {"already_running": True}
        try:
            result = await self._tick(now, force_proposal=force_proposal)
            self.db.mutate_task_action_state("learning:health", lambda old: {"failures": 0})
            return result
        except CalendarError as error:
            health = self.db.mutate_task_action_state("learning:health", lambda old, message=str(error): {
                "error": message, "failures": (old or {}).get("failures", 0) + 1,
                "notified": (old or {}).get("notified", False)})
            if health["failures"] >= 2 and not health["notified"]:
                self.db.mutate_task_action_state("learning:health", lambda old: {**old, "notified": True})
                await self.telegram.send_message(self.settings.telegram_chat_id,
                    "Planning assistant needs attention. " + str(error) + " Tasks are saved. Check /plan.")
            raise
        finally:
            self.db.mutate_task_action_state("learning:tick", lambda old: {**old, "until": now.isoformat()}
                if old["claim"] == claim else old)

    async def _tick(self, now, *, force_proposal=False):
        self.sync_queue()
        await self.retire_cards()
        tasks = self.db.list_open_tasks()
        # Check-ins continue independently of the one-at-a-time approval queue.
        for task in tasks:
            state = self.get(task["id"])
            if not state:
                continue
            if state["status"] == "creating":
                await self.book(task, state, now)
            elif state["status"] == "scheduled" and datetime.fromisoformat(state["end"]) <= now:
                await self.follow_up(task, state, now)
            elif state["status"] == "awaiting_feedback" and state.get("delivery") == "feedback_pending":
                await self.deliver_feedback(task, state)
        # The nightly batch may prepare tomorrow's blocks during quiet hours.
        # Approval, Calendar conflict checks and work-hour limits remain unchanged.
        active = self.sync_queue()
        nightly = [task for task in tasks if (state := self.get(task["id"]))
                   and state.get("nightly_batch") and state["status"] in
                   {"queued", "planning", "proposed", "editing", "no_slot"}]
        quiet = not self.settings.learning_start_hour <= now.hour < self.settings.learning_end_hour
        if quiet and not force_proposal:
            if not nightly or (active is not None and active not in {task["id"] for task in nightly}):
                return {"enabled": True, "quiet_hours": True, "proposals_checked": 0}
            tasks = nightly
        # Retain the active turn, then consume selected oldest tasks before other work.
        nightly_ids = {task["id"] for task in nightly}
        tasks = sorted(tasks, key=lambda task: (task["id"] not in nightly_ids,
            datetime.fromisoformat((self.get(task['id']) or {}).get('nightly_slot', {}).get('start')
                or task['created_at']), task["id"])) if nightly else tasks
        # Day buckets retain approved load after task completion without scanning
        # the ever-growing feedback history on every scheduler tick.
        booked_minutes = {}
        for offset in range(8):
            day = (now + timedelta(days=offset)).date().isoformat()
            booked_minutes[day] = sum((self.db.get_task_action_state("learning:day:" + day) or {}).values())
        processed = 0
        for task in tasks:
            if active is not None and task["id"] != active:
                continue
            state = self.get(task["id"])
            if not state and datetime.fromisoformat(task["created_at"]) < datetime.fromisoformat(self.settings.learning_enabled_since):
                continue
            if not state or state["status"] in {"queued", "planning"}:
                await self.propose(task, now, [], booked_minutes)
                state = self.get(task["id"])
                processed += 1
            if not state:
                continue
            if state["status"] in {"proposed", "no_slot"} and state.get("delivery") == "proposal_pending":
                await self.deliver_proposal(task, state)
            if state["status"] in {"queued", "planning", "proposed", "editing", "no_slot", "creating"}:
                break
        await self.retire_cards()
        return {"enabled": True, "proposals_checked": processed}

    def status(self, *, booked_only=False):
        lines = []
        if not self.calendar.configured():
            lines.append("Google Calendar authorization is still required.")
        labels = {"planning": "Scheduling", "queued": "Queued for planning",
            "proposed": "Awaiting approval", "creating": "Applying approved plan",
            "editing": "Awaiting your direction",
            "scheduled": "Scheduled", "awaiting_feedback": "Awaiting outcome",
            "no_slot": "No viable time block", "needs_replan": "Needs replanning",
            "postponed": "Deferred",
            "backward_managed": "Backward plan · /backward progress ID",
            "rejected": "Skipped", "cancelled": "Schedule removed", "finished": "Completed"}
        groups = {}
        for task in self.db.list_open_tasks():
            state = self.get(task["id"])
            if state:
                if (state["status"] == "scheduled") != booked_only:
                    continue
                status = labels.get(state["status"], "Review required")
                if state.get("delivery", "").endswith(("_uncertain", "_sending")):
                    status += " · message delivery needs review"
                title, *scope = task_title_lines(task['title'])
                item = f"• <b>#{task['id']}</b> {escape(title)}"
                if scope:
                    item += "\n  " + "\n  ".join(escape(line) for line in scope)
                groups.setdefault(status, []).append(item)
        for status, tasks in groups.items():
            lines.extend(["", f"<b>{escape(status)}</b>", *tasks])
        if booked_only:
            return "\n".join(lines).strip() if groups else ""
        profile = self.db.get_task_action_state("learning:profile") or {}
        reports = []
        for subject, evidence in profile.items():
            if evidence.get("samples", 0) >= 3:
                reports.append(f"{escape(subject)} · {evidence['samples']} completed reports · "
                             f"average {round(evidence['average_minutes'])} minutes")
        if reports:
            lines.extend(["", "<b>Execution history</b>", *reports])
        if groups:
            lines.extend(["", "<b>Commands</b>",
                "<code>/plan ID</code> — Request an execution plan or resume a task.",
                "<code>/planedit ID your request</code> — Describe your preferred time and duration naturally.",
                "<code>/taskupdate ID minutes done|partial|skipped</code> — Record the actual time and outcome."])
        return "\n".join(lines).strip() or "No task plans yet."
