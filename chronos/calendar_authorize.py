"""One-time desktop OAuth consent. Writes credentials to ignored .env, never stdout."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
import subprocess
import tempfile
import webbrowser

import httpx


SCOPE = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly"


class AuthorizationError(RuntimeError):
    """Only fixed, credential-free diagnostics may be shown to the user."""


def save_env(path, values):
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    lines, remaining = [], dict(values)
    for line in content.splitlines():
        key = line.split("=", 1)[0].strip()
        if key in values:
            if key in remaining:
                lines.append(f"{key}={json.dumps(remaining.pop(key))}")
        else:
            lines.append(line)
    lines.extend(f"{key}={json.dumps(value)}" for key, value in remaining.items())
    # Atomic replace, restrictive Unix permissions; Windows inherits the user's directory ACL.
    temporary = path.with_name(path.name + ".gmail-" + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def authorize(client_path, account, env_path):
    client = json.loads(client_path.read_text(encoding="utf-8")).get("installed")
    if not client or not client.get("client_id") or not client.get("client_secret"):
        raise AuthorizationError("Use a Google OAuth Desktop app client JSON (installed), not a service-account key")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    callback = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # The callback URL includes a temporary authorization code.

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            valid = parsed.path == "/callback" and hmac.compare_digest(query.get("state", [""])[0], state)
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if valid:
                callback.update(query)
                self.wfile.write(b"Authorization received. Return to the terminal; you may close this tab.")
            else:
                self.wfile.write(b"Invalid authorization callback.")

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "login_hint": account,
        })
        consent_path = env_path.parent / "tmp" / "calendar-consent-url.txt"
        consent_path.parent.mkdir(parents=True, exist_ok=True)
        consent_path.write_text(url, encoding="utf-8")
        print("Opening Google Calendar consent in your browser. No calendar event will be created.", flush=True)
        if not webbrowser.open(url):
            raise AuthorizationError("Could not open your browser; run this helper on a desktop with a default browser")
        server.timeout = 1
        deadline = time.monotonic() + 900
        while not callback and time.monotonic() < deadline:
            server.handle_request()
    consent_path.unlink(missing_ok=True)
    if "code" not in callback:
        raise AuthorizationError("Authorization cancelled or timed out; nothing was saved")
    print("Google callback received; validating the Calendar grant.", flush=True)
    with httpx.Client(timeout=30) as session:
        token = session.post("https://oauth2.googleapis.com/token", data={
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "code": callback["code"][0], "code_verifier": verifier, "redirect_uri": redirect,
            "grant_type": "authorization_code",
        })
        if not token.is_success:
            raise AuthorizationError(f"Google token exchange failed (HTTP {token.status_code}); nothing was saved")
        data = token.json()
        if not data.get("refresh_token") or not data.get("access_token"):
            raise AuthorizationError("Google did not grant offline access; nothing was saved")
        if not set(SCOPE.split()).issubset(data.get("scope", "").split()):
            raise AuthorizationError("Required Calendar modify scope was not granted; nothing was saved")
        profile = session.get("https://www.googleapis.com/calendar/v3/calendars/primary",
                              headers={"Authorization": "Bearer " + data["access_token"]})
        if not profile.is_success:
            response = profile.json()
            reasons = {item.get("reason") for item in response.get("error", {}).get("details", []) if isinstance(item, dict)}
            reasons.update(item.get("reason") for item in response.get("error", {}).get("errors", []) if isinstance(item, dict))
            if reasons & {"SERVICE_DISABLED", "accessNotConfigured"}:
                raise AuthorizationError("Calendar API is disabled in the OAuth client project. Enable calendar-json.googleapis.com and retry; nothing was saved")
            raise AuthorizationError(f"Calendar profile verification failed (HTTP {profile.status_code}); nothing was saved")
        if profile.json().get("id", "").lower() != account.lower():
            raise AuthorizationError("The signed-in Calendar account differs from the requested account; nothing was saved")
    save_env(env_path, {
        "CHRONOS_CALENDAR_CLIENT_ID": client["client_id"], "CHRONOS_CALENDAR_CLIENT_SECRET": client["client_secret"],
        "CHRONOS_CALENDAR_REFRESH_TOKEN": data["refresh_token"], "CHRONOS_CALENDAR_ACCOUNT": account,
        "CHRONOS_ENABLE_LEARNING_CALENDAR": "false",
    })
    print("Calendar connected. Credentials saved in .env. Cloud publishing and activation are next.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-secret", type=Path)
    parser.add_argument("--cloud-project")
    parser.add_argument("--gcloud-account")
    parser.add_argument("--account", required=True)
    args = parser.parse_args()
    try:
        if args.cloud_project:
            def read_secret(name):
                command = ["gcloud.cmd", "secrets", "versions", "access", "latest", "--secret=" + name,
                    "--project=" + args.cloud_project]
                if args.gcloud_account:
                    command.append("--account=" + args.gcloud_account)
                result = subprocess.run(command, capture_output=True, text=True)
                if result.returncode:
                    raise AuthorizationError("Could not read the existing OAuth client from Secret Manager")
                return result.stdout.strip()
            client = {"installed": {"client_id": read_secret("chronos-gmail-client-id"),
                                    "client_secret": read_secret("chronos-gmail-client-secret")}}
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "client.json"
                path.write_text(json.dumps(client), encoding="utf-8")
                authorize(path, args.account, Path(".env"))
        elif args.client_secret:
            authorize(args.client_secret, args.account, Path(".env"))
        else:
            raise AuthorizationError("Provide --client-secret or --cloud-project")
    except AuthorizationError as error:
        parser.exit(1, str(error) + "\n")
    except (ValueError, OSError, httpx.HTTPError, KeyError):
        # Do not print raw HTTP errors or parsed credential data.
        parser.exit(1, "Authorization failed or was cancelled. Check the Desktop client, Calendar API, test user and account; retry.\n")


if __name__ == "__main__":
    main()
