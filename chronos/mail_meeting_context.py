"""Minimal meeting evidence bound to correspondence, separate from thread identity."""
from datetime import datetime
from .google_calendar import CalendarError
from .gmail import GmailError
from .mail_correspondence import fingerprint


FIELDS = ("version", "status", "event_id", "title", "start", "end", "timezone", "recipient", "invitees",
    "location", "calendar_account", "calendar_id", "invite_token", "reschedule", "reschedule_epoch")


def context(mail, identifier):
    state = mail.get("meeting:" + identifier) or {}
    return {name: state[name] for name in FIELDS if name in state}


def matches(mail, identifier, draft):
    current = context(mail, identifier)
    # Legacy drafts may remain usable only when no related meeting exists.
    return draft.get("meeting_source", fingerprint({})) == fingerprint(current)


async def verify(mail, identifier, draft):
    if not matches(mail, identifier, draft):
        return False
    state = mail.get("meeting:" + identifier) or {}
    status = state.get("status")
    from .meeting_rescheduling import active
    if active(state):
        return False
    if not state or status in {"needs_information", "cancelled", "invite_cancelled"}:
        return True
    # A metadata-only legacy review makes no concrete availability claim.
    if status == "review" and not state.get("start") and not state.get("end"):
        return True
    if status not in {"review", "held", "invite_pending", "invite_accepted", "invite_declined"}:
        return False
    calendar = getattr(mail, "calendar", None)
    if (not calendar or not calendar.configured()
            or state.get("calendar_account") != mail.settings.calendar_account
            or state.get("calendar_id") != mail.settings.calendar_id):
        return False
    try:
        await calendar.verify_owner()
        if status == "review":
            start, end = datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"])
            if not start.tzinfo or not end.tzinfo or start >= end or start <= datetime.now(mail.settings.tz):
                return False
            busy = await calendar.busy(start, end)
            return not any(start < stop and end > begin for begin, stop in busy) and matches(mail, identifier, draft)
        event = await calendar.event(state["event_id"])
        if status == "held":
            from .meeting_coordination import MeetingCoordination
            valid = MeetingCoordination(mail, calendar).matches(event, state)
        else:
            from .meeting_invitations import MeetingInvitations
            valid = MeetingInvitations(mail).outcome(event, state) == status
        return valid and matches(mail, identifier, draft)
    except (CalendarError, KeyError, TypeError, AttributeError, ValueError):
        raise GmailError("Related Calendar state could not be verified; mail operation paused") from None
