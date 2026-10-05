"""Bounded public-source refresh with durable daily attempts and a worker lease."""
from datetime import datetime, timedelta
from uuid import uuid4

import httpx

from .academic_calendar import OFFICIAL_CALENDAR_URL, create_tls_context
from .calendar_snapshot import make_snapshot, is_current
from .course_tracking import TAIPEI


async def fetch_calendar():
    # Fixed URL, no credential forwarding or redirects, bounded decompressed bytes.
    async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                 verify=create_tls_context()) as client:
        async with client.stream("GET", OFFICIAL_CALENDAR_URL) as response:
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise ValueError("calendar response is not HTML")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 1_000_000:
                    raise ValueError("calendar response too large")
            return data.decode("utf-8", errors="strict")


async def sync_calendar(db, now, *, fetch=fetch_calendar):
    if now.utcoffset() is None:
        raise ValueError("calendar clock must be timezone-aware")
    if is_current(db.get_calendar_snapshot(), now):
        return {"calendar_sync": "current"}
    day = now.astimezone(TAIPEI).date().isoformat()
    token = uuid4().hex

    def claim(previous):
        if previous and previous["day"] == day:
            if previous["attempts"] >= 3 or now < datetime.fromisoformat(previous["next_attempt_at"]):
                return previous
            attempts = previous["attempts"] + 1
        else:
            attempts = 1
        return {"day": day, "attempts": attempts, "claim": token, "status": "fetching",
                "next_attempt_at": (now + timedelta(minutes=30)).isoformat()}

    state = db.mutate_calendar_sync(claim)
    if state["claim"] != token:
        return {"calendar_sync": "waiting", "calendar_attempts": state["attempts"]}
    try:
        html = await fetch()
        snapshot = make_snapshot(html, now)
    except (httpx.HTTPError, ValueError, UnicodeError):
        # Never persist exception bodies, request headers, or arbitrary upstream text.
        status = "source_unavailable"
    else:
        db.save_calendar_snapshot(snapshot)
        status = "updated"

    def finish(previous):
        return {**previous, "status": status} if previous["claim"] == token else previous
    db.mutate_calendar_sync(finish)
    return {"calendar_sync": status, "calendar_attempts": state["attempts"]}
