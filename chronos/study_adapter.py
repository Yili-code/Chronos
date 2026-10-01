"""Small, read-only browser-session state contract for the Study Module.

This module intentionally contains no browser automation or TronClass HTTP
client.  The fake connector is a deterministic seam for testing state
classification before a real connector is considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Generic, Protocol, TypeVar
from urllib.parse import urlparse


class ConnectorSignal(str, Enum):
    """Safe observations supplied by a browser connector."""

    AUTHENTICATED_PAGE = "authenticated_page"
    CAS_REDIRECT = "cas_redirect"
    TIMEOUT = "timeout"


class SessionStatus(str, Enum):
    READY = "ready"
    REAUTH_REQUIRED = "reauth_required"
    UNKNOWN = "session_unknown"


class ResultStatus(str, Enum):
    OK = "ok"
    REAUTH_REQUIRED = "reauth_required"
    DEFERRED_ATTACHMENT = "deferred_attachment"
    UNKNOWN = "unknown"


STATUS_LABELS_ZH = {
    SessionStatus.READY: "登入正常",
    SessionStatus.REAUTH_REQUIRED: "需要重新登入",
    SessionStatus.UNKNOWN: "暫時無法確認登入狀態",
    ResultStatus.DEFERRED_ATTACHMENT: "附件暫緩保存，請先用瀏覽器查看",
}


@dataclass(frozen=True)
class SafeDiagnostic:
    operation: str
    retryable: bool


T = TypeVar("T")


@dataclass(frozen=True)
class ReadResult(Generic[T]):
    status: ResultStatus
    value: T | None
    diagnostic: SafeDiagnostic


@dataclass(frozen=True)
class SessionState:
    status: SessionStatus
    observed_at: datetime
    evidence: ConnectorSignal


class BrowserConnector(Protocol):
    def observe(self) -> ConnectorSignal:
        """Return a redacted, read-only observation of the current tab."""


class BrowserTabTransport(Protocol):
    """The narrow, secret-free operations a real Chrome connector needs."""

    def current_url(self) -> str: ...

    def visible_text(self) -> str: ...


class ChromeBrowserConnector:
    """Classify a user-owned Chrome tab without reading browser secrets."""

    def __init__(self, tab: BrowserTabTransport) -> None:
        self._tab = tab

    def observe(self) -> ConnectorSignal:
        try:
            url = self._tab.current_url()
            text = self._tab.visible_text()
        except (ConnectionError, TimeoutError):
            return ConnectorSignal.TIMEOUT

        parsed = urlparse(url)
        if parsed.netloc == "tccas.ntou.edu.tw" and parsed.path.startswith("/cas/login"):
            return ConnectorSignal.CAS_REDIRECT
        if (
            parsed.netloc == "tronclass.ntou.edu.tw"
            and "學生" in text
            and ("我的課程" in text or "/user/index" in parsed.path)
        ):
            return ConnectorSignal.AUTHENTICATED_PAGE
        return ConnectorSignal.TIMEOUT


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


def classify_read(
    session: SessionState, value: T | None, *, operation: str
) -> ReadResult[T]:
    """Convert a safe session observation into a read result."""
    if session.status is SessionStatus.READY:
        return ReadResult(
            status=ResultStatus.OK,
            value=value,
            diagnostic=SafeDiagnostic(operation=operation, retryable=False),
        )
    if session.status is SessionStatus.REAUTH_REQUIRED:
        return ReadResult(
            status=ResultStatus.REAUTH_REQUIRED,
            value=None,
            diagnostic=SafeDiagnostic(operation=operation, retryable=False),
        )
    return ReadResult(
        status=ResultStatus.UNKNOWN,
        value=None,
        diagnostic=SafeDiagnostic(operation=operation, retryable=True),
    )


def classify_attachment(
    session: SessionState,
    attachment: T | None,
    *,
    persisted: bool,
    operation: str,
) -> ReadResult[T]:
    """Classify attachment metadata without treating failed persistence as OK."""
    read_result = classify_read(session, attachment, operation=operation)
    if read_result.status is not ResultStatus.OK:
        return read_result
    if not persisted:
        return ReadResult(
            status=ResultStatus.DEFERRED_ATTACHMENT,
            value=None,
            diagnostic=SafeDiagnostic(operation=operation, retryable=False),
        )
    return read_result
