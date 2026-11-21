"""Google Maps transit estimate for reaching a source-grounded invitation venue."""
from datetime import timedelta, timezone
from html import escape
import math
import re
from urllib.parse import urlencode

import httpx

from .gmail import clean


def physical_location(text):
    text = clean(text or "", 250)
    return text if text and not re.search(r"https?://|www\.|線上|視訊|\bonline\b|\bzoom\b|google meet", text, re.I) else ""


async def transit_duration(api_key, origin, destination, arrival):
    body = {
        "origin": {"address": origin}, "destination": {"address": destination},
        "travelMode": "TRANSIT", "regionCode": "TW",
        "arrivalTime": arrival.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            response = await client.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes", json=body,
                headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": "routes.duration"},
            )
        if response.is_success:
            routes = response.json().get("routes") or []
            if routes and (duration := re.fullmatch(r"(\d{1,6}(?:\.\d{1,9})?)s", routes[0].get("duration", ""))):
                return timedelta(minutes=math.ceil(float(duration[1]) / 60))
    except (httpx.HTTPError, AttributeError, ValueError, TypeError):
        pass
    return None


async def travel_context(settings, mail, venue, events, arrival):
    """Return the display and the estimated departure, if Maps supplied a route."""
    destination = physical_location(venue)
    if not destination or not any(destination in clean(mail.get(key) or "", 12000)
                                  for key in ("body", "alternative_body", "snippet", "subject")):
        return [], None
    previous = max(((end, event) for event, start, end in events
                    if start < arrival and start.date() == arrival.date() and end <= arrival
                    and event.get("transparency") != "transparent"),
                   key=lambda row: row[0], default=None)
    if previous and (origin := physical_location(previous[1].get("location"))):
        previous_end = previous[0]
    else:
        previous_end = None
        origin = physical_location(settings.mail_event_default_origin)
    if not origin:
        return ["", "<b>交通 · 大眾運輸</b>", "出發地待確認。"], None
    url = "https://www.google.com/maps/dir/?" + urlencode({
        "api": 1, "origin": origin, "destination": destination, "travelmode": "transit",
    })
    lines = ["", "<b>交通 · 大眾運輸</b>", f"{escape(origin)} → {escape(destination)}"]
    if previous and previous_end is None:
        lines.append("上一個行程地點未確認，此處以預設出發地估算。")
    duration = (await transit_duration(settings.google_maps_routes_api_key, origin, destination, arrival)
                if settings.google_maps_routes_api_key else None)
    if duration is None:
        lines.append("目前無法取得車程估算。")
        depart = None
    else:
        depart = arrival - duration
        clock = f"{depart:%H:%M}" if depart.date() == arrival.date() else f"{depart:%m/%d %H:%M}"
        lines.append(f"Google Maps 估計約 {duration // timedelta(minutes=1)} 分鐘 · 約 {clock} 前出發")
        if previous_end and previous_end > depart:
            lines.append(f"上一場 {previous_end:%H:%M} 結束，轉場時間不足。")
    return [*lines, f'<a href="{escape(url, quote=True)}">Google Maps 路線</a>'], depart
