"""Activate the authorized course-only schedule without exposing credentials.

Uses the existing Cloud Run secret binding; never prints provider responses,
headers, access tokens, user task contents or Telegram credentials.
"""
import base64
import json
import logging
import os
from pathlib import Path
import subprocess

import httpx

PROJECT = "yili-chronos-prod"
REGION = "asia-east1"
SERVICE = "chronos"
JOB = "chronos-course-progress"


def main():
    logging.disable(logging.CRITICAL)
    evidence = {}
    try:
        gcloud = str(Path(os.environ["LOCALAPPDATA"]) /
                     "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
        def cli(*args):
            return subprocess.run([gcloud, *args], check=True, capture_output=True,
                                  text=True, timeout=60).stdout.strip()
        service = json.loads(cli("run", "services", "describe", SERVICE,
                                "--project", PROJECT, "--region", REGION, "--format=json"))
        env = {item["name"]: item for item in service["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert env["CHRONOS_ENABLE_STUDY_TRACKING"]["value"] == "true"
        assert env["CHRONOS_ENABLE_INTERNAL_SCHEDULER"]["value"] == "false"
        evidence["revision"] = service["status"]["latestReadyRevisionName"]
        url = service["status"]["url"]
        token = cli("auth", "print-access-token")
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            auth = {"Authorization": "Bearer " + token}
            binding = env["CHRONOS_SCHEDULER_SECRET"]["valueFrom"]["secretKeyRef"]
            secret_path = f"projects/{PROJECT}/secrets/{binding['name']}/versions/{binding['key']}"
            response = client.get(f"https://secretmanager.googleapis.com/v1/{secret_path}:access", headers=auth)
            response.raise_for_status()
            secret = base64.b64decode(response.json()["payload"]["data"]).decode()
            health = client.get(url + "/health")
            health.raise_for_status()
            evidence["health_ok"] = health.json().get("status") == "ok"
            denied = client.post(url + "/internal/study")
            evidence["unauthenticated_rejected"] = denied.status_code == 403
            assert evidence["health_ok"] and evidence["unauthenticated_rejected"]
            name = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}"
            endpoint = f"https://cloudscheduler.googleapis.com/v1/{name}"
            payload = {
                "name": name, "schedule": "* * * * *", "timeZone": "Asia/Taipei",
                "description": "Course surveys and review tasks only; AI generation remains deferred.",
                "httpTarget": {"uri": url + "/internal/study", "httpMethod": "POST",
                               "headers": {"X-Chronos-Scheduler-Secret": secret}},
                "attemptDeadline": "60s", "retryConfig": {"retryCount": 0},
            }
            existing = client.get(endpoint, headers=auth)
            if existing.status_code == 404:
                response = client.post(endpoint.rsplit("/", 1)[0], headers=auth, json=payload)
            else:
                existing.raise_for_status()
                assert existing.json()["httpTarget"]["uri"] == url + "/internal/study"
                mask = "schedule,timeZone,description,httpTarget,attemptDeadline,retryConfig"
                response = client.patch(endpoint, headers=auth, params={"updateMask": mask}, json=payload)
            response.raise_for_status()
            if response.json().get("state") == "PAUSED":
                response = client.post(endpoint + ":resume", headers=auth, json={})
                response.raise_for_status()
            state = client.get(endpoint, headers=auth)
            state.raise_for_status()
            evidence["scheduler_enabled"] = state.json()["state"] == "ENABLED"
            evidence["schedule"] = state.json()["schedule"]
            # A real current-time tick can send today's due survey, never synthetic
            # historical sessions. Repeating it exercises persisted send claims.
            headers = {"X-Chronos-Scheduler-Secret": secret}
            for label in ("initial_tick", "repeat_tick"):
                result = client.post(url + "/internal/study", headers=headers)
                result.raise_for_status()
                data = result.json()
                evidence[label] = {key: data[key] for key in (
                    "sessions_reconciled", "reminders_reconciled",
                    "failure_notices_sent", "failure_notices_unresolved")}
        print(json.dumps(evidence))
        return 0
    except Exception as error:
        evidence["error_type"] = type(error).__name__
        print(json.dumps(evidence))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
