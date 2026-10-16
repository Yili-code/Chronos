"""Interactive local wrapper for the secret-safe CAS authentication probe."""

from __future__ import annotations

import getpass
import json

from chronos.tronclass_auth import run_probe


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
