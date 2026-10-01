"""Secret-safe Phase 0 probe for the NTOU CAS -> TronClass trust chain.

Credentials are read from a TTY with echo disabled.  The probe makes one CAS
credential attempt, never logs response bodies, tickets, cookie names/values,
or request URLs containing credentials, and performs only authentication plus
read-only GET requests.
"""

from __future__ import annotations

import getpass
import json
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx


CAS_ORIGIN = "https://tccas.ntou.edu.tw"
CAS_TICKETS_PATH = "/cas/v1/tickets"
CAS_TICKETS_URL = f"{CAS_ORIGIN}{CAS_TICKETS_PATH}"
TRONCLASS_ORIGIN = "https://tronclass.ntou.edu.tw"
TRONCLASS_SERVICE_URL = f"{TRONCLASS_ORIGIN}/login?next=/user/index"


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
    """Return only a safe origin/path and redact ticket-shaped path segments."""
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


def _run_single_attempt(username: str, password: str, context: ssl.SSLContext) -> dict:
    report: dict[str, object] = {
        "tgt_issued": False,
        "service_ticket_issued": False,
        "tronclass_authenticated": False,
        "session_reuse_authenticated": False,
        "cookie_count": 0,
    }
    try:
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
            report["tgt_issued"] = True

            try:
                ticket_response = cas.post(tgt_location, data={"service": TRONCLASS_SERVICE_URL})
            except httpx.HTTPError:
                raise ProbeFailure("service_ticket_transport") from None
            if ticket_response.status_code != 200:
                raise ProbeFailure("service_ticket", ticket_response.status_code)
            service_ticket = ticket_response.text.strip()
            if not valid_service_ticket(service_ticket):
                raise ProbeFailure("service_ticket_shape", ticket_response.status_code)
            report["service_ticket_issued"] = True

        with httpx.Client(verify=context, follow_redirects=True, timeout=20) as tronclass:
            try:
                login_response = tronclass.get(TRONCLASS_SERVICE_URL, params={"ticket": service_ticket})
            except httpx.HTTPError:
                raise ProbeFailure("tronclass_login_transport") from None
            finally:
                service_ticket = ""
                tgt_location = ""
            final = urlsplit(str(login_response.url))
            authenticated = (
                login_response.status_code == 200
                and final.scheme == "https"
                and final.netloc == "tronclass.ntou.edu.tw"
                and final.path not in {"/login", "/"}
            )
            report["tronclass_status"] = login_response.status_code
            report["tronclass_final_origin_path"] = sanitized_origin_path(str(login_response.url))
            report["tronclass_authenticated"] = authenticated
            report["cookie_count"] = len(tronclass.cookies)
            if not authenticated:
                raise ProbeFailure("tronclass_session", login_response.status_code)

            try:
                reuse_response = tronclass.get(f"{TRONCLASS_ORIGIN}/user/index")
            except httpx.HTTPError:
                raise ProbeFailure("session_reuse_transport") from None
            reuse_final = urlsplit(str(reuse_response.url))
            report["session_reuse_authenticated"] = (
                reuse_response.status_code == 200
                and reuse_final.netloc == "tronclass.ntou.edu.tw"
                and reuse_final.path != "/login"
            )
            report["session_reuse_status"] = reuse_response.status_code
            report["session_reuse_final_origin_path"] = sanitized_origin_path(str(reuse_response.url))
    except ProbeFailure as failure:
        report["failure_stage"] = failure.stage
        if failure.status_code is not None:
            report["failure_status"] = failure.status_code
    return report


def run_probe(username: str, password: str) -> dict:
    """Run two independent login attempts without exposing credential material."""
    context = create_tls_context()
    attempts = [_run_single_attempt(username, password, context) for _ in range(2)]
    successful = [
        attempt for attempt in attempts if attempt.get("session_reuse_authenticated")
    ]
    return {
        "cas_rest_endpoint": "available",
        "credential_attempts": len(attempts),
        "successful_attempts": len(successful),
        "repeatable_login": len(successful) == len(attempts),
        "attempts": attempts,
    }


def main() -> int:
    username = getpass.getpass("CAS username (hidden): ")
    password = getpass.getpass("CAS password (hidden): ")
    if not username or not password:
        print(json.dumps({"failure_stage": "missing_credentials"}, indent=2))
        return 2
    report = run_probe(username, password)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report.get("session_reuse_authenticated") else 1


if __name__ == "__main__":
    raise SystemExit(main())
