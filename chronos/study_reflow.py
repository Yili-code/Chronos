"""Preview personal calendar moves, then reconcile each approved move by version."""
from datetime import datetime, timedelta, time
import re

from .google_calendar import CalendarError


def bounds(event, tz):
    start, end = event.get("start", {}), event.get("end", {})
    if start.get("dateTime") and end.get("dateTime"):
        return tuple(datetime.fromisoformat(v["dateTime"].replace("Z", "+00:00")).astimezone(tz)
                     for v in (start, end))
    if start.get("date") and end.get("date"):
        return tuple(datetime.combine(datetime.fromisoformat(v["date"]).date(), time(), tz)
                     for v in (start, end))
    raise CalendarError("A calendar event has no usable time. Earlier planning paused.")


def movable(event, settings, now):
    owner = settings.calendar_account.casefold()
    primary = settings.calendar_id.strip().casefold()
    source = event.get("_calendar_id", "").casefold()
    return (source in {primary, "primary" if primary == owner else primary}
        and event.get("creator", {}).get("email", "").casefold() == owner
        and (event.get("organizer", {}).get("self") or
             event.get("organizer", {}).get("email", "").casefold() == owner)
        and event.get("etag") and event.get("eventType", "default") == "default"
        and not event.get("recurringEventId") and not event.get("recurrence")
        and not event.get("attendees") and not event.get("attendeesOmitted")
        and event.get("start", {}).get("dateTime") and bounds(event, settings.tz)[0] > now
        and 0 < (bounds(event, settings.tz)[1] - bounds(event, settings.tz)[0]).total_seconds() <= 180 * 60
        and not re.search(r"上課|課表|課程|\b(class|lecture|laboratory)\b", event.get("summary", ""), re.I))


async def preview(planner, task, state, now):
    # Import here to keep the scheduling constraints in one implementation.
    from .learning_calendar import find_slot, overlaps
    config = planner.settings
    old_start = datetime.fromisoformat(state["start"])
    horizon = now + timedelta(days=7)
    events = await planner.calendar.agenda(now, horizon)
    opaque = [e for e in events if e.get("transparency") != "transparent"]
    tasks = {t["id"]: t for t in planner.db.list_open_tasks()}
    states = {key: planner.get(key) for key in tasks}
    candidates, hard = [], []
    for event in opaque:
        window = bounds(event, config.tz)
        props = event.get("extendedProperties", {}).get("private", {})
        linked = next((key for key, s in states.items() if s and s.get("event_id") == event["id"]), None)
        eligible = movable(event, config, now)
        if props.get("chronosTask") or linked is not None:
            linked_state = states.get(linked) or {}
            eligible = (eligible and linked_state.get("status") == "scheduled"
                and props.get("chronosToken") == linked_state.get("token")
                and props.get("chronosTask") == str(linked)
                and window == tuple(datetime.fromisoformat(linked_state[k]) for k in ("start", "end")))
        if eligible:
            candidates.append((event, window, linked))
        else:
            hard.append(window)
    hard += [(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
             for s in states.values() if s and s.get("start") and s["status"] == "creating"]
    original_budget = {(now + timedelta(days=i)).date().isoformat():
        sum((planner.db.get_task_action_state("learning:day:" + (now + timedelta(days=i)).date().isoformat()) or {}).values())
        for i in range(8)}
    relaxed = dict(original_budget)
    for _, window, linked in candidates:
        if linked is not None:
            day = window[0].date().isoformat()
            relaxed[day] = max(0, relaxed.get(day, 0) - states[linked]["minutes"])
    policy = planner.db.get_task_action_state("learning:course-calendar-policy")
    cursor = now
    while cursor < old_start:
        slot = find_slot(task, cursor, hard, [], config, state["minutes"], relaxed, policy,
                         window=(cursor, old_start))
        if not slot:
            return None
        displaced = [(e, w, key) for e, w, key in candidates if overlaps(*slot, [w])]
        if not displaced or len(displaced) > 5:
            cursor = slot[0] + timedelta(minutes=15)
            continue
        budget = dict(original_budget)
        for _, w, key in displaced:
            if key is not None:
                day = w[0].date().isoformat()
                budget[day] = max(0, budget.get(day, 0) - states[key]["minutes"])
        # Validate the target again with only its actual conflicts removed.
        valid = find_slot(task, now, hard, [], config, state["minutes"], budget, policy, window=slot)
        if not valid:
            cursor = slot[0] + timedelta(minutes=15)
            continue
        occupied = hard + [w for e, w, key in candidates if (e, w, key) not in displaced] + [slot]
        budget[slot[0].date().isoformat()] = budget.get(slot[0].date().isoformat(), 0) + state["minutes"]
        moves = []
        for event, window, key in displaced:
            minutes = int((window[1] - window[0]).total_seconds() / 60)
            # Personal activities stay on their original day; tracked study respects its own deadline.
            destination_task = tasks[key] if key is not None else {
                "due_at": datetime.combine(window[0].date(), time.max, config.tz).isoformat()}
            begin = max(slot[1], datetime.combine(window[0].date(), time(config.learning_start_hour), config.tz))
            stop = horizon if key is not None else datetime.combine(window[0].date() + timedelta(days=1), time(), config.tz)
            destination = find_slot(destination_task, now, occupied, [], config, minutes,
                                    budget if key is not None else {}, policy, window=(begin, stop))
            if not destination:
                break
            occupied.append(destination)
            if key is not None:
                day = destination[0].date().isoformat()
                budget[day] = budget.get(day, 0) + minutes
            moves.append({"event_id": event["id"], "etag": event["etag"],
                "title": event.get("summary", "Personal activity")[:100], "task_id": key,
                "task_token": states[key]["token"] if key is not None else None,
                "old_start": window[0].isoformat(), "old_end": window[1].isoformat(),
                "start": destination[0].isoformat(), "end": destination[1].isoformat()})
        if len(moves) == len(displaced):
            return slot, moves
        cursor = slot[0] + timedelta(minutes=15)
    return None


async def apply(planner, task, state, now):
    """Replay uncertain writes using an event marker; never repeat a different edit."""
    from .learning_calendar import overlaps
    moves = state["calendar_moves"]
    marker = state["token"]
    # Re-read every event before the first write. Changed events invalidate approval.
    current = {}
    for move in moves:
        event = await planner.calendar.event(move["event_id"])
        props = (event or {}).get("extendedProperties", {}).get("private", {})
        applied = props.get("chronosReflow") == marker
        desired = (datetime.fromisoformat(move["start"]), datetime.fromisoformat(move["end"]))
        if not event or event.get("status") == "cancelled":
            raise CalendarError("A reviewed activity was removed. Reflow paused; check /plan.")
        if applied:
            if bounds(event, planner.settings.tz) != desired:
                raise CalendarError("A moved activity changed again. Reflow paused; check /plan.")
        elif event.get("etag") != move["etag"] or not movable(
                {**event, "_calendar_id": planner.settings.calendar_id}, planner.settings, now):
            raise CalendarError("A reviewed activity changed. Reflow paused; check /plan.")
        if move["task_id"] is not None:
            saved = planner.get(move["task_id"])
            if not saved or saved.get("token") != move["task_token"] or saved["status"] != "scheduled":
                raise CalendarError("A study booking changed. Reflow paused; check /plan.")
        current[move["event_id"]] = event
    # Check destinations against all calendars again, including overlapping fixed events.
    begin = min(datetime.fromisoformat(m["start"]) for m in moves)
    end = max(datetime.fromisoformat(m["end"]) for m in moves)
    agenda = await planner.calendar.agenda(min(begin, datetime.fromisoformat(state["start"])),
                                           max(end, datetime.fromisoformat(state["end"])))
    moving_ids = {m["event_id"] for m in moves}
    fixed = [bounds(e, planner.settings.tz) for e in agenda
             if e.get("transparency") != "transparent" and not (
                 e.get("id") in moving_ids and e.get("_calendar_id", "").casefold() in
                 {planner.settings.calendar_id.casefold(), "primary" if planner.settings.calendar_id.casefold() == planner.settings.calendar_account.casefold() else planner.settings.calendar_id.casefold()})]
    for start, stop in [(state["start"], state["end"]), *((m["start"], m["end"]) for m in moves)]:
        if datetime.fromisoformat(start) <= now or overlaps(datetime.fromisoformat(start), datetime.fromisoformat(stop), fixed):
            raise CalendarError("A reviewed destination is now busy or has passed. Reflow paused; check /plan.")
    for move in moves:
        event = current[move["event_id"]]
        props = event.get("extendedProperties", {}).get("private", {})
        if props.get("chronosReflow") != marker:
            body = {"start": {"dateTime": move["start"], "timeZone": planner.settings.timezone},
                    "end": {"dateTime": move["end"], "timeZone": planner.settings.timezone},
                    "extendedProperties": {**event.get("extendedProperties", {}),
                        "private": {**props, "chronosReflow": marker}}}
            await planner.calendar.move_event(move["event_id"], body, move["etag"])
        record_move(planner, move)


def record_move(planner, move):
    if move["task_id"] is None:
        return
    def record():
        saved = planner.get(move["task_id"])
        if not saved or saved.get("token") != move["task_token"]:
            raise CalendarError("The moved study record changed. Check /plan.")
        days = {move["old_start"][:10], move["start"][:10]}
        buckets = {day: planner.db.get_task_action_state("learning:day:" + day) or {} for day in days}
        buckets[move["old_start"][:10]].pop(move["task_token"], None)
        buckets[move["start"][:10]][move["task_token"]] = saved["minutes"]
        for day, bucket in buckets.items():
            planner.db._save_task_action_state("learning:day:" + day, bucket)
        planner.db._save_task_action_state(f"learning:task:{move['task_id']}",
            {**saved, "start": move["start"], "end": move["end"]})
    planner.db._run_task_action(record)


async def reconcile(planner, state):
    for move in state["calendar_moves"]:
        event = await planner.calendar.event(move["event_id"])
        props = (event or {}).get("extendedProperties", {}).get("private", {})
        if props.get("chronosReflow") == state["token"]:
            if bounds(event, planner.settings.tz) != tuple(datetime.fromisoformat(move[k]) for k in ("start", "end")):
                raise CalendarError("A partially moved activity changed; reconciliation needs attention.")
            record_move(planner, move)
