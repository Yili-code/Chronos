"""Run the read-only CAS authentication probe inside an isolated Cloud Run Job."""

from __future__ import annotations

import json
import os

from .tronclass_auth import run_probe


def main() -> int:
    username = os.environ.get("CHRONOS_TRONCLASS_USERNAME", "")
    password = os.environ.get("CHRONOS_TRONCLASS_PASSWORD", "")
    if not username or not password:
        print(json.dumps({"failure_stage": "cloud_credentials_unavailable"}))
        return 2
    try:
        report = run_probe(username, password)
    except Exception:
        # Do not emit exception details: they may contain sensitive transport data.
        report = {"failure_stage": "unexpected_cloud_probe_error"}
    finally:
        username = ""
        password = ""
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("session_reuse_authenticated") else 1


if __name__ == "__main__":
    raise SystemExit(main())
