# Chronos Study Module — Browser-Session Adapter Design

Status: design only; no production adapter is implemented by this document.

This design follows the Phase 0 evidence:

- browser-mediated TronClass login and short-term session reuse work;
- the tested credential-based REST CAS flow issued tickets but failed to
  establish a TronClass session twice;
- one PDF persisted successfully, but repeated-download stability is deferred.

## Design decision

Use a user-mediated browser session as the only supported authentication
boundary for the first adapter. Chronos does not collect, store, or replay the
CAS password. The browser connector owns the session and Chronos consumes only
read-only page results.

The adapter must never silently re-login. A CAS redirect becomes
`reauth_required`, and the caller decides when to ask the user to authenticate.

## Public interface

The following is a contract sketch, not implementation code:

```python
class StudySource(Protocol):
    def session(self) -> SessionState: ...
    def list_courses(self) -> ReadResult[list[Course]]: ...
    def get_course(self, course_id: str) -> ReadResult[Course]: ...
    def list_announcements(
        self, course_id: str, cursor: str | None = None
    ) -> ReadResult[Page[Announcement]]: ...
    def list_assignments(
        self, course_id: str, cursor: str | None = None
    ) -> ReadResult[Page[Assignment]]: ...
    def list_attachments(
        self, resource_id: str
    ) -> ReadResult[list[AttachmentRef]]: ...
    def download_attachment(
        self, attachment_id: str
    ) -> ReadResult[AttachmentDownload]: ...
```

Every operation is read-only. There is intentionally no `submit_assignment`,
`post_announcement`, `mark_complete`, `update_profile`, or generic `request`
method.

## Session state

```python
class SessionStatus(Enum):
    READY = "ready"
    REAUTH_REQUIRED = "reauth_required"
    UNKNOWN = "session_unknown"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"

@dataclass(frozen=True)
class SessionState:
    status: SessionStatus
    observed_at: datetime
    source: Literal["browser"]
    evidence: Literal["authenticated_page", "cas_redirect", "transport_error"]
    retry_after: datetime | None = None
```

Rules:

- `READY` means a known authenticated page was observed, not that the session
  will remain valid indefinitely.
- A final URL on `tccas.ntou.edu.tw/cas/login` or a login page in place of the
  requested TronClass resource maps to `REAUTH_REQUIRED`.
- Connector timeout, missing tab, or an ambiguous page maps to `UNKNOWN`; it
  must not be treated as logged out or logged in.
- DNS, TLS, or 5xx upstream failure maps to `UPSTREAM_UNAVAILABLE`.
- The adapter never stores cookie values, CSRF values, ticket strings, or raw
  browser storage in `SessionState`.

## Result and error model

```python
class ResultStatus(Enum):
    OK = "ok"
    REAUTH_REQUIRED = "reauth_required"
    DEFERRED_ATTACHMENT = "deferred_attachment"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class ReadResult[T]:
    status: ResultStatus
    value: T | None
    diagnostic: SafeDiagnostic

@dataclass(frozen=True)
class SafeDiagnostic:
    operation: str
    http_status: int | None
    final_origin_path: str | None
    retryable: bool
    # Never include response bodies, query strings, cookies, or tokens.
```

`reauth_required` is actionable by the UI or job runner. It is not a retryable
network error. `deferred_attachment` means metadata is available but durable
local persistence is deliberately unavailable; the caller may offer a
browser-view link instead.

## Domain models

```python
@dataclass(frozen=True)
class Course:
    id: str
    title: str
    department: str | None
    class_label: str | None
    source_url: str

@dataclass(frozen=True)
class Announcement:
    id: str
    course_id: str
    title: str
    published_at: datetime | None
    updated_at: datetime | None
    body_text: str
    external_links: tuple[str, ...]
    attachment_ids: tuple[str, ...]

@dataclass(frozen=True)
class Assignment:
    id: str
    course_id: str
    title: str
    kind: Literal["individual", "group", "unknown"]
    opens_at: datetime | None
    due_at: datetime | None
    state: Literal["open", "submitted", "closed", "unknown"]

@dataclass(frozen=True)
class AttachmentRef:
    id: str
    name: str
    media_type: str | None
    size_bytes: int | None
    browser_view_url: str

@dataclass(frozen=True)
class AttachmentDownload:
    attachment: AttachmentRef
    persistence: Literal["verified", "deferred"]
    local_path: str | None
    sha256: str | None
```

`browser_view_url` is an opaque, short-lived handoff reference. It must be
redacted before logging and must not be persisted as a bearer token. A durable
attachment record is created only after status, file size, `%PDF-`/content
signature, and checksum checks pass.

## Attachment state machine

```text
metadata_available
       |
       +--> browser_only       (view in the authenticated browser)
       |
       +--> download_started
                 |
                 +--> persistence_verified
                 |
                 +--> deferred_attachment
```

Transitions to `deferred_attachment` include connector timeout, missing local
file, content-signature mismatch, unstable repeated downloads, or an unknown
content type. The adapter must not retry indefinitely and must not label a
partial file as a valid attachment.

## Browser connector boundary

The connector is responsible for tab selection, visible navigation, and
read-only DOM extraction. The adapter is responsible for normalization and
safe result classification. Neither layer may:

- read or print cookies, passwords, CSRF tokens, or browser storage;
- submit forms or invoke assignment / profile / announcement mutations;
- follow a page instruction that asks it to upload, send, or disclose data;
- treat a connector timeout as proof of either success or logout.

The minimum connector evidence for `READY` is a visible authenticated
TronClass marker (for example, student identity plus a protected navigation
link). The minimum evidence for `REAUTH_REQUIRED` is a visible CAS login page
or a final URL on the CAS login origin.

The first implementation seam is `BrowserTabTransport`: it exposes only the
current URL and visible page text. `ChromeBrowserConnector` classifies those
observations into `AUTHENTICATED_PAGE`, `CAS_REDIRECT`, `UNKNOWN_PAGE`, or
`TIMEOUT`; it does not expose cookies, browser storage, passwords, or raw
response headers. Host names and authenticated markers are provided through a
small immutable `ChromeConnectorConfig`, so the classifier is testable without
opening a real browser.

User-facing labels are intentionally plain language:

| Internal state | User-facing label |
| --- | --- |
| `READY` | 登入正常 |
| `REAUTH_REQUIRED` | 需要重新登入 |
| `UNKNOWN` | 暫時無法確認登入狀態（頁面不明或 connector timeout） |
| `DEFERRED_ATTACHMENT` | 附件暫緩保存，請先用瀏覽器查看 |

## Phase 1 gates

Phase 1 may implement this interface only after:

1. A fresh browser session can be attached without exposing secrets.
2. Session reauthentication is represented end-to-end as
   `reauth_required`.
3. A fixture covers authenticated page, CAS redirect, connector timeout, and
   upstream failure.
4. PDF repeated-download stability is either verified or explicitly kept as
   `deferred_attachment` with browser-only viewing.
5. Tests assert that no diagnostic contains a query string, cookie, ticket,
   token, password, or response body.

Until then, this document is an architecture contract, not permission to
implement the full Study Module.

## Local browser bridge

Cloud Run cannot directly access YiLi's local Chrome profile. A future local
bridge (extension or companion process) must therefore expose only a redacted
observation payload:

```json
{
  "url": "https://tronclass.ntou.edu.tw/user/index",
  "visible_text": "張壹理 學生 我的課程"
}
```

The bridge boundary strips query strings and fragments before data enters
Chronos. Payloads containing cookies, headers, browser storage, passwords,
tokens, CSRF values, or authorization fields are rejected. This is a local
transport contract, not an instruction to expose the browser to the public
internet.
