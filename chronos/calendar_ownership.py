"""Shared ownership boundary for owner-only, non-recurring event mutations."""


def owned_single_event(event, cfg):
    aliases = {cfg.calendar_id.casefold(), cfg.calendar_account.casefold(), 'primary'}
    return (event.get('_calendar_id', cfg.calendar_id).casefold() in aliases
        and event.get('creator', {}).get('email', '').casefold() == cfg.calendar_account.casefold()
        and (event.get('organizer', {}).get('self') or event.get('organizer', {}).get('email', '').casefold() == cfg.calendar_account.casefold())
        and event.get('etag') and event.get('status') != 'cancelled'
        and event.get('eventType', 'default') == 'default' and not event.get('recurrence') and not event.get('recurringEventId')
        and not event.get('attendees') and not event.get('attendeesOmitted') and event.get('start', {}).get('dateTime'))
