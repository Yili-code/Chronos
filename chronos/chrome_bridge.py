"""Secret-free contract between a local browser bridge and Chronos.

This module validates observations only. It does not open sockets, control a
browser, or provide a way to submit data to TronClass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit


FORBIDDEN_FIELDS = frozenset({
    "cookie", "cookies", "headers", "local_storage", "session_storage",
    "password", "token", "csrf", "authorization",
})


class BrowserBridgeError(ValueError):
    """The bridge returned an invalid or secret-bearing observation."""


def safe_origin_path(raw_url: str) -> str:
    """Strip query, fragment, and userinfo before a URL enters the app layer."""
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BrowserBridgeError("invalid observation URL")
    if parsed.username or parsed.password:
        raise BrowserBridgeError("observation URL contains userinfo")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


@dataclass(frozen=True)
class BrowserObservation:
    url: str
    visible_text: str
    observed_at: datetime


class LocalBrowserBridge(Protocol):
    def observe_tab(self, tab_id: str) -> Mapping[str, object]:
        """Return URL and visible text only; never return browser secrets."""


def parse_observation(payload: Mapping[str, object]) -> BrowserObservation:
    """Validate and normalize one bridge payload without retaining secrets."""
    if FORBIDDEN_FIELDS.intersection(payload):
        raise BrowserBridgeError("observation contains forbidden field")
    raw_url = payload.get("url")
    visible_text = payload.get("visible_text")
    if not isinstance(raw_url, str) or not isinstance(visible_text, str):
        raise BrowserBridgeError("observation requires url and visible_text")
    return BrowserObservation(
        url=safe_origin_path(raw_url),
        visible_text=visible_text,
        observed_at=datetime.now(timezone.utc),
    )
