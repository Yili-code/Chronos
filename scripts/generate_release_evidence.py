"""Create a commit-scoped, machine-readable CI evidence manifest."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    counts = junit_counts(args.junit)
    sha = os.getenv("GITHUB_SHA") or git("rev-parse", "HEAD")
    manifest = {
        "schema": 1,
        "commit_sha": sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ci_run_id": os.getenv("GITHUB_RUN_ID"),
        },
        "validation": {
            "python_tests": counts,
            "scope": "local-ci",
            "production_verified": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"release_evidence={args.output}; commit={sha}; tests={counts['tests']}")
    return int(bool(counts["failures"] or counts["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
