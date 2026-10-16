"""Read-only, secret-safe NTOU CAS to TronClass authentication primitives."""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from contextlib import contextmanager
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit

import httpx


CAS_ORIGIN = "https://tccas.ntou.edu.tw"
CAS_TICKETS_PATH = "/cas/v1/tickets"
CAS_TICKETS_URL = f"{CAS_ORIGIN}{CAS_TICKETS_PATH}"
TRONCLASS_ORIGIN = "https://tronclass.ntou.edu.tw"
TRONCLASS_LOGIN_URL = f"{TRONCLASS_ORIGIN}/login?next=/user/index"
TRONCLASS_INDEX_URL = f"{TRONCLASS_ORIGIN}/user/index"


@dataclass
class ProbeFailure(Exception):
    stage: str
    status_code: int | None = None


def create_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        context.verify_flags &= ~strict
    return context


def sanitized_origin_path(url: str) -> str:
    """Return a query-free origin/path with ticket-shaped segments redacted."""
    parsed = urlsplit(url)
    path = parsed.path.split(";", 1)[0]
    path = re.sub(r"/(?:TGT|ST)-[^/]+", "/[redacted-ticket]", path, flags=re.IGNORECASE)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def valid_tgt_location(location: str) -> bool:
    parsed = urlsplit(location)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "tccas.ntou.edu.tw"
        and parsed.path.startswith(f"{CAS_TICKETS_PATH}/TGT-")
        and not parsed.username
        and not parsed.password
    )


def valid_service_ticket(ticket: str) -> bool:
    return bool(re.fullmatch(r"ST-[A-Za-z0-9._~-]+", ticket.strip()))


def service_from_cas_redirect(location: str) -> str:
    """Extract and validate the exact TronClass service declared by CAS."""
    parsed_location = urlsplit(urljoin(TRONCLASS_ORIGIN, location))
    if (
        parsed_location.scheme != "https"
        or parsed_location.netloc != "tccas.ntou.edu.tw"
        or parsed_location.path != "/cas/login"
    ):
        raise ProbeFailure("preflight_redirect_shape")
    services = parse_qs(parsed_location.query, keep_blank_values=True).get("service", [])
    if len(services) != 1:
        raise ProbeFailure("preflight_service_shape")
    service = services[0]
    parsed_service = urlsplit(service)
    service_query = parse_qs(parsed_service.query, keep_blank_values=True)
    if (
        parsed_service.scheme != "https"
        or parsed_service.netloc != "tronclass.ntou.edu.tw"
        or parsed_service.path != "/login"
        or parsed_service.username
        or parsed_service.password
        or parsed_service.fragment
        or service_query != {"next": ["/user/index"]}
    ):
        raise ProbeFailure("preflight_service_shape")
    return service


def service_callback_url(service: str, ticket: str) -> str:
    """Append a ticket without replacing or normalizing the service query."""
    if not valid_service_ticket(ticket):
        raise ProbeFailure("service_ticket_shape")
    parsed = urlsplit(service)
    if "ticket" in parse_qs(parsed.query, keep_blank_values=True):
        raise ProbeFailure("preflight_service_shape")
    separator = "&" if parsed.query else "?"
    return f"{service}{separator}ticket={quote(ticket, safe='')}"


def authenticated_tronclass_response(response: httpx.Response) -> bool:
    final = urlsplit(str(response.url))
    return (
        response.status_code == 200
        and final.scheme == "https"
        and final.netloc == "tronclass.ntou.edu.tw"
        and final.path.startswith("/user/")
    )


@contextmanager
def authenticated_tronclass_client(username: str, password: str):
    """Yield a fresh, read-only authenticated TronClass session.

    Credentials, CAS tickets and cookies stay inside the client process.  The
    caller must not log response bodies or request URLs with query strings.
    """
    context = create_tls_context()
    service_ticket = ""
    tgt_location = ""
    with httpx.Client(verify=context, follow_redirects=True, timeout=20) as tronclass:
        try:
            try:
                preflight = tronclass.get(TRONCLASS_LOGIN_URL, follow_redirects=False)
            except httpx.HTTPError:
                raise ProbeFailure("preflight_transport") from None
            if preflight.status_code not in {301, 302, 303, 307, 308}:
                raise ProbeFailure("preflight_redirect", preflight.status_code)
            service = service_from_cas_redirect(preflight.headers.get("location", ""))

            with httpx.Client(verify=context, follow_redirects=False, timeout=20) as cas:
                try:
                    tgt_response = cas.post(
                        CAS_TICKETS_URL,
                        data={"username": username, "password": password},
                        headers={"Accept": "text/plain"},
                    )
                except httpx.HTTPError:
                    raise ProbeFailure("cas_transport") from None
                if tgt_response.status_code != 201:
                    raise ProbeFailure("cas_credentials", tgt_response.status_code)
                tgt_location = tgt_response.headers.get("location", "")
                if not valid_tgt_location(tgt_location):
                    raise ProbeFailure("cas_tgt_shape", tgt_response.status_code)

                try:
                    ticket_response = cas.post(tgt_location, data={"service": service})
                except httpx.HTTPError:
                    raise ProbeFailure("service_ticket_transport") from None
                if ticket_response.status_code != 200:
                    raise ProbeFailure("service_ticket", ticket_response.status_code)
                service_ticket = ticket_response.text.strip()
                if not valid_service_ticket(service_ticket):
                    raise ProbeFailure("service_ticket_shape", ticket_response.status_code)

            callback = service_callback_url(service, service_ticket)
            try:
                login_response = tronclass.get(callback)
            except httpx.HTTPError:
                raise ProbeFailure("tronclass_login_transport") from None
            if not authenticated_tronclass_response(login_response):
                raise ProbeFailure("tronclass_session", login_response.status_code)
            yield tronclass
        finally:
            service_ticket = ""
            tgt_location = ""
            password = ""


def run_probe(username: str, password: str) -> dict[str, object]:
    """Authenticate once and make one read-only session-reuse request."""
    report: dict[str, object] = {
        "preflight_service_confirmed": False,
        "tgt_issued": False,
        "service_ticket_issued": False,
        "tronclass_authenticated": False,
        "session_reuse_authenticated": False,
    }
    try:
        with authenticated_tronclass_client(username, password) as tronclass:
            report["preflight_service_confirmed"] = True
            report["tgt_issued"] = True
            report["service_ticket_issued"] = True
            report["tronclass_status"] = 200
            report["tronclass_final_origin_path"] = TRONCLASS_INDEX_URL
            report["tronclass_authenticated"] = True
            report["cookie_count"] = len(tronclass.cookies)

            try:
                reuse_response = tronclass.get(TRONCLASS_INDEX_URL)
            except httpx.HTTPError:
                raise ProbeFailure("session_reuse_transport") from None
            report["session_reuse_status"] = reuse_response.status_code
            report["session_reuse_final_origin_path"] = sanitized_origin_path(
                str(reuse_response.url)
            )
            report["session_reuse_authenticated"] = authenticated_tronclass_response(
                reuse_response
            )
            if not report["session_reuse_authenticated"]:
                raise ProbeFailure("session_reuse", reuse_response.status_code)
    except ProbeFailure as failure:
        report["failure_stage"] = failure.stage
        if failure.status_code is not None:
            report["failure_status"] = failure.status_code
    finally:
        password = ""
    return report
