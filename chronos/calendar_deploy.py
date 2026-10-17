"""Publish a verified Calendar grant to Secret Manager and activate cloud planning."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime

from .google_calendar import GoogleCalendar, CalendarError
from .settings import Settings


def publish(project, account, region="asia-east1", service="chronos"):
    config = Settings()
    calendar = GoogleCalendar(config)
    asyncio.run(calendar.verify_owner())
    calendars = asyncio.run(calendar.request("GET", "users/me/calendarList", params={"maxResults": 250}))
    busy_ids = {"primary"}
    for item in calendars.get("items", []):
        if item.get("accessRole") in {"owner", "writer"}:
            busy_ids.add(item["id"])
    # Incomplete calendar discovery must not silently omit conflicts.
    if calendars.get("nextPageToken"):
        raise CalendarError("Calendar discovery needs pagination before activation.")
    def run(*args, allow_failure=False):
        result = subprocess.run(["gcloud.cmd", *args, "--project=" + project, "--account=" + account],
                                capture_output=True, text=True)
        if result.returncode and not allow_failure:
            raise CalendarError("Cloud Calendar provisioning failed. No credentials were printed.")
        return result
    current = json.loads(run("run", "services", "describe", service, "--region=" + region, "--format=json").stdout)
    env = current["spec"]["template"]["spec"]["containers"][0].get("env", [])
    enabled_since = next((e.get("value", "") for e in env if e["name"] == "CHRONOS_LEARNING_ENABLED_SINCE"), "")
    bindings = []
    for suffix in ("client_id", "client_secret", "refresh_token", "account"):
        secret = "chronos-calendar-" + suffix.replace("_", "-")
        if run("secrets", "describe", secret, allow_failure=True).returncode:
            run("secrets", "create", secret, "--replication-policy=automatic", "--quiet")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(getattr(config, "calendar_" + suffix))
            version = run("secrets", "versions", "add", secret, "--data-file=" + str(path), "--format=value(name)").stdout.strip().split("/")[-1]
        if not version.isdigit():
            raise CalendarError("Secret version verification failed.")
        run("secrets", "add-iam-policy-binding", secret,
            "--member=serviceAccount:chronos-runtime@" + project + ".iam.gserviceaccount.com",
            "--role=roles/secretmanager.secretAccessor", "--quiet")
        bindings.append("CHRONOS_CALENDAR_" + suffix.upper() + "=" + secret + ":" + version)
    values = {"CHRONOS_ENABLE_LEARNING_CALENDAR": "true", "CHRONOS_CALENDAR_ID": config.calendar_id,
        "CHRONOS_CALENDAR_BUSY_IDS": ",".join(sorted(busy_ids)),
        "CHRONOS_LEARNING_ENABLED_SINCE": enabled_since or datetime.now(config.tz).isoformat(),
        "CHRONOS_LEARNING_START_HOUR": str(config.learning_start_hour),
        "CHRONOS_LEARNING_END_HOUR": str(config.learning_end_hour),
        "CHRONOS_LEARNING_MINUTES": str(config.learning_minutes),
        "CHRONOS_LEARNING_DAILY_MINUTES": str(config.learning_daily_minutes)}
    run("run", "services", "update", service, "--region=" + region,
        "--update-secrets=" + ",".join(bindings),
        "--update-env-vars=^~^" + "~".join(key + "=" + value for key, value in values.items()), "--quiet")
    print("Calendar grant verified and published. Cloud planning is enabled; event writes still require Allow.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--account", required=True, help="Existing gcloud deployment account")
    parser.add_argument("--region", default="asia-east1")
    args = parser.parse_args()
    try:
        publish(args.project, args.account, args.region)
    except (CalendarError, ValueError, OSError, KeyError):
        parser.exit(1, "Calendar activation failed. Check authorization and cloud access; no credentials were printed.\n")


if __name__ == "__main__":
    main()
