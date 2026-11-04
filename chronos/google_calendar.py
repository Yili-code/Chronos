"""Owner-only Calendar transport with deterministic event IDs and safe errors."""
import asyncio
from datetime import datetime, timezone
import time
from urllib.parse import quote

import httpx


class CalendarError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def validate_range(start, end):
    if (start.utcoffset() is None or end.utcoffset() is None
            or start.astimezone(timezone.utc) >= end.astimezone(timezone.utc)):
        raise CalendarError('Calendar query needs a positive timezone-aware range.')


class GoogleCalendar:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport
        self._token = ""
        self._expires = 0
        self._lock = asyncio.Lock()

    def configured(self):
        return all((self.settings.calendar_client_id, self.settings.calendar_client_secret,
                    self.settings.calendar_refresh_token, self.settings.calendar_account))

    async def token(self):
        async with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            if not self.configured():
                raise CalendarError("Google Calendar is not connected. Complete Calendar authorization first.")
            try:
                async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
                    r = await client.post("https://oauth2.googleapis.com/token", data={
                        "client_id": self.settings.calendar_client_id,
                        "client_secret": self.settings.calendar_client_secret,
                        "refresh_token": self.settings.calendar_refresh_token, "grant_type": "refresh_token"})
                if not r.is_success:
                    raise CalendarError("Google Calendar authorization expired or was rejected. Reconnect Calendar.")
                data = r.json()
                self._token = data["access_token"]
                self._expires = time.monotonic() + max(0, int(data.get("expires_in", 3600)) - 60)
                return self._token
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise CalendarError("Google Calendar authorization is temporarily unavailable.") from None

    async def request(self, method, path, *, body=None, params=None, missing=False, etag=None):
        token = await self.token()
        try:
            async with httpx.AsyncClient(timeout=25, transport=self.transport) as client:
                r = await client.request(method, "https://www.googleapis.com/calendar/v3/" + path,
                    headers={"Authorization": "Bearer " + token, **({"If-Match": etag} if etag else {})}, json=body, params=params)
            if missing and r.status_code in {404, 410}:
                return None
            if not r.is_success:
                raise CalendarError(f"Google Calendar request failed (HTTP {r.status_code}).", r.status_code)
            if method == 'DELETE' and r.status_code == 204:
                return {}
            return r.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise CalendarError("Google Calendar request failed; its outcome may need reconciliation.") from None

    def events_path(self):
        return "calendars/" + quote(self.settings.calendar_id, safe="") + "/events"

    async def busy(self, start, end):
        validate_range(start, end)
        # Google may coalesce a primary email and the `primary` alias into one
        # response entry. Normalize before checking every requested calendar.
        ids = sorted({"primary" if value.strip().casefold() == self.settings.calendar_account.casefold()
                      else value.strip() for value in
                      [self.settings.calendar_id, *self.settings.calendar_busy_ids.split(",")]
                      if value.strip()})
        data = await self.request("POST", "freeBusy", body={"timeMin": start.isoformat(),
            "timeMax": end.isoformat(), "timeZone": self.settings.timezone,
            "items": [{"id": value} for value in ids]})
        try:
            calendars = data['calendars']
            if not isinstance(calendars, dict) or not ids:
                raise ValueError()
            result = []
            for calendar_id in ids:
                value = calendars[calendar_id]
                if value.get('errors') or not isinstance(value.get('busy'), list):
                    raise ValueError()
                for slot in value['busy']:
                    begin, stop = (datetime.fromisoformat(slot[key].replace('Z', '+00:00'))
                                   for key in ('start', 'end'))
                    validate_range(begin, stop)
                    result.append((begin, stop))
            return result
        except (KeyError, TypeError, AttributeError, ValueError, CalendarError):
            raise CalendarError('Some calendars could not be checked. No work block was scheduled.') from None

    async def event(self, event_id):
        return await self.request("GET", self.events_path() + "/" + event_id, missing=True)

    async def agenda(self, start, end):
        """Read every expanded occurrence overlapping the bounded local day."""
        validate_range(start, end)
        ids = sorted({"primary" if value.strip().casefold() == self.settings.calendar_account.casefold()
                      else value.strip() for value in
                      [self.settings.calendar_id, *self.settings.calendar_busy_ids.split(",")] if value.strip()})
        events, seen = [], set()
        for calendar_id in ids:
            page = None
            pages = set()
            for _ in range(40):
                params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                          "timeZone": self.settings.timezone, "singleEvents": "true",
                          "orderBy": "startTime", "maxResults": 250}
                if page:
                    params["pageToken"] = page
                data = await self.request("GET", "calendars/" + quote(calendar_id, safe="") + "/events", params=params)
                for event in data.get("items", []):
                    key = (event.get("iCalUID") or calendar_id + ":" + event["id"],
                           event.get("originalStartTime", event.get("start", {})).get("dateTime") or
                           event.get("originalStartTime", event.get("start", {})).get("date"))
                    declined = any(a.get("self") and a.get("responseStatus") == "declined"
                                   for a in event.get("attendees", []))
                    if event.get("status") != "cancelled" and not declined and key not in seen:
                        events.append({**event, "_calendar_id": calendar_id})
                        seen.add(key)
                page = data.get("nextPageToken")
                if not page:
                    break
                if not isinstance(page, str) or page in pages:
                    raise CalendarError('Calendar pagination is inconsistent. No partial agenda was returned.')
                pages.add(page)
            else:
                raise CalendarError('Too many Calendar events. Narrow the requested date range.')
        return events

    async def search_events(self, query):
        """Target the writable calendar, with explicit bounded pagination."""
        events, page = [], None
        for _ in range(4):
            params = {"q": query, "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
            if page:
                params['pageToken'] = page
            data = await self.request('GET', self.events_path(), params=params)
            events.extend({**event, '_calendar_id': self.settings.calendar_id}
                          for event in data.get('items', []) if event.get('status') != 'cancelled')
            page = data.get('nextPageToken')
            if not page:
                return events
        raise CalendarError('Too many matching Calendar events. Please narrow the group name; nothing changed.')

    async def move_event(self, event_id, body, etag):
        if not etag:
            raise CalendarError("Event version is missing. Prepare a new proposal.")
        return await self.request("PATCH", self.events_path() + "/" + quote(event_id, safe=""),
            body=body, params={"sendUpdates": "none"}, etag=etag)

    async def insert(self, body):
        return await self.request("POST", self.events_path(), body=body, params={"sendUpdates": "none"})

    async def invite(self, body):
        return await self.request("POST", self.events_path(), body=body, params={"sendUpdates": "all"})

    async def invite_existing(self, event_id, body, etag):
        if not etag:
            raise CalendarError("Event version is missing; invitation paused.")
        return await self.request("PATCH", self.events_path() + "/" + quote(event_id, safe=""),
            body=body, params={"sendUpdates": "all"}, etag=etag)

    async def cancel_invitation(self, event_id, etag):
        if not etag:
            raise CalendarError("Event version is missing; cancellation paused.")
        return await self.request("DELETE", self.events_path() + "/" + quote(event_id, safe=""),
            params={"sendUpdates": "all"}, etag=etag)

    async def delete_event(self, event_id, etag):
        if not etag:
            raise CalendarError('Event version is missing; deletion paused.')
        return await self.request('DELETE', self.events_path() + '/' + quote(event_id, safe=''),
                                  params={'sendUpdates': 'none'}, etag=etag)

    async def task_events(self, task_id):
        """Find every managed session, including historical and distant bookings."""
        return await self.property_events('chronosTask', str(task_id))

    async def category_events(self, category, *, start=None, end=None):
        return await self.property_events('chronosEventCategory', category, start=start, end=end)

    async def property_events(self, name, value, *, start=None, end=None):
        events, page = [], None
        for _ in range(4):
            params = {'privateExtendedProperty': name + '=' + value,
                      'singleEvents': 'true', 'maxResults': 250}
            if start:
                params['timeMin'] = start.isoformat()
            if end:
                params['timeMax'] = end.isoformat()
            if page:
                params['pageToken'] = page
            data = await self.request('GET', self.events_path(), params=params)
            events.extend({**e, '_calendar_id': self.settings.calendar_id}
                          for e in data.get('items', []) if e.get('status') != 'cancelled')
            page = data.get('nextPageToken')
            if not page:
                return events
        raise CalendarError('Too many managed sessions; narrow the rebuild scope before making changes.')

    async def verify_owner(self):
        data = await self.request("GET", "calendars/primary")
        if data.get("id", "").casefold() != self.settings.calendar_account.casefold():
            raise CalendarError("The connected Calendar account differs from the configured owner.")
        return data
