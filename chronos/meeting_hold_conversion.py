"""Conservative conversion of an unchanged private hold into an invitation."""
from datetime import datetime

from .google_calendar import CalendarError
from .meeting_coordination import MeetingCoordination
from .study_reflow import bounds


SAFE_HOLD_FIELDS = {"kind", "id", "etag", "status", "htmlLink", "created", "updated", "summary",
    "creator", "organizer", "start", "end", "transparency", "visibility", "iCalUID", "sequence",
    "reminders", "extendedProperties", "eventType", "locked", "_calendar_id"}


def clean_hold(service, event, state):
    if (not isinstance(event, dict) or set(event) - SAFE_HOLD_FIELDS
            or state.get("calendar_account") != service.mail.settings.calendar_account
            or state.get("calendar_id") != service.mail.settings.calendar_id):
        return False
    try:
        return (MeetingCoordination(service.mail, service.calendar).matches(event, state)
            and event.get("organizer", {}).get("self") is True
            and event.get("visibility") == "private" and not event.get("locked")
            and event.get("transparency", "opaque") == "opaque"
            and isinstance(event.get("etag"), str) and bool(event["etag"])
            and event.get("extendedProperties") == {"private": {
                "chronosMeeting": state["version"], "chronosAccount": service.mail.namespace}})
    except (AttributeError, TypeError, KeyError):
        return False


def same_calendar(service, identifier):
    normalize = lambda value: "primary" if value.strip().casefold() == service.mail.settings.calendar_account.casefold() else value.strip()
    return isinstance(identifier, str) and normalize(identifier) == normalize(service.mail.settings.calendar_id)


async def verify_conversion(service, state):
    event = await service.calendar.event(state["event_id"])
    if not clean_hold(service, event, state) or event["etag"] != state.get("hold_etag"):
        return False
    start, end = (datetime.fromisoformat(state[k]) for k in ("start", "end"))
    events = await service.calendar.agenda(start, end)
    if not isinstance(events, list):
        raise CalendarError("Calendar agenda is incomplete; conversion paused")
    found = 0
    for item in events:
        if not isinstance(item, dict) or not isinstance(item.get("_calendar_id"), str):
            raise CalendarError("Calendar event provenance is missing; conversion paused")
        if item.get("id") == state["event_id"] and same_calendar(service, item["_calendar_id"]):
            if not clean_hold(service, {k: v for k, v in item.items() if k != "_calendar_id"}, state) or item.get("etag") != state["hold_etag"]:
                return False
            found += 1
            continue
        if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
            continue
        try:
            begin, stop = bounds(item, service.mail.settings.tz)
            if stop <= begin:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise CalendarError("Calendar interval is invalid; conversion paused") from None
        if start < stop and end > begin:
            return False
    return found == 1
