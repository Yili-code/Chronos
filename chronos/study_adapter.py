"""Small, read-only browser-session state contract for the Study Module.

This module intentionally contains no browser automation or TronClass HTTP
client.  The fake connector is a deterministic seam for testing state
classification before a real connector is considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


class ConnectorSignal(str, Enum):
    """Safe observations supplied by a browser connector."""

    AUTHENTICATED_PAGE = "authenticated_page"
    CAS_REDIRECT = "cas_redirect"
    TIMEOUT = "timeout"


class SessionStatus(str, Enum):
    READY = "ready"
    REAUTH_REQUIRED = "reauth_required"
    UNKNOWN = "session_unknown"


@dataclass(frozen=True)
class SessionState:
    status: SessionStatus
    observed_at: datetime
    evidence: ConnectorSignal


class BrowserConnector(Protocol):
    def observe(self) -> ConnectorSignal:
        """Return a redacted, read-only observation of the current tab."""


class FakeBrowserConnector:
    """Deterministic connector for tests; it never handles secrets."""

    def __init__(self, *signals: ConnectorSignal) -> None:
        self._signals = list(signals)

    def observe(self) -> ConnectorSignal:
        if not self._signals:
            return ConnectorSignal.TIMEOUT
        return self._signals.pop(0)


class BrowserSessionAdapter:
    """Classify connector observations without attempting authentication."""

    def __init__(self, connector: BrowserConnector) -> None:
        self._connector = connector

    def session(self) -> SessionState:
        evidence = self._connector.observe()
        status = {
            ConnectorSignal.AUTHENTICATED_PAGE: SessionStatus.READY,
            ConnectorSignal.CAS_REDIRECT: SessionStatus.REAUTH_REQUIRED,
            ConnectorSignal.TIMEOUT: SessionStatus.UNKNOWN,
        }[evidence]
        return SessionState(
            status=status,
            observed_at=datetime.now(timezone.utc),
            evidence=evidence,
        )
