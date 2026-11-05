"""Reproduce a bounded slice of ORIGINAL draft requests without mailbox writes.

By default models are schema-aware doubles. --live uses normal AI environment
settings only; application services are initialized with safe static defaults
and every mailbox/Calendar/Telegram boundary remains a test double.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


class SQLiteLiveOnly:
    def pytest_collection_modifyitems(self, config, items):
        kept = [item for item in items if "firestore" not in item.nodeid]
        rejected = [item for item in items if item not in kept]
        items[:] = kept
        config.hook.pytest_deselected(items=rejected)


def audit(output, basetemp, ai=None):
    from chronos import settings as settings_module
    from chronos.settings import Settings
    output, basetemp = Path(output), Path(basetemp)
    source_files = [*sorted((ROOT / "chronos").glob("*.py")), ROOT / "tests/test_original_drafting_language.py", Path(__file__).resolve()]
    source_snapshot = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Explicit static defaults override every possible real environment field.
    defaults = {name: field.get_default(call_default_factory=True) for name, field in Settings.model_fields.items()}
    defaults.update(database_backend="sqlite", database_path=basetemp.parent / "unused-safe-import.db")
    safe_settings = Settings(_env_file=None, **defaults)
    with ExitStack() as stack:
        stack.enter_context(patch.object(settings_module, "settings", safe_settings))
        import test_original_drafting_language as scenarios
        from executive_scenarios import SCENARIOS
        scenarios.RECORDS.clear()
        stack.enter_context(patch.object(scenarios, "LIVE_AI", ai))
        code = pytest.main([str(ROOT / "tests/test_original_drafting_language.py"), "-q",
                           "--basetemp=" + str(basetemp.resolve())], plugins=[SQLiteLiveOnly()] if ai else [])
    records = []
    for index, (text, condition, expected) in enumerate(SCENARIOS["drafting"], 1):
        executions = [row for row in scenarios.RECORDS if row["id"] == f"DRAFTING-{index:02}"]
        status = ("Pass" if all(row["status"] == "Pass" for row in executions)
                  else "Fail" if any(row["status"] == "Fail" for row in executions) else "Blocked") if executions else (
                      "Deferred" if index == 10 else "NotExecuted")
        records.append({"id": f"DRAFTING-{index:02}", "input": text, "original_precondition": condition,
            "expected": expected, "status": status, "executions": executions,
            "reason": None if executions else "Owner-deferred LINE integration" if index == 10 else "Outside this explicit four-case slice"})
    report = {"updated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "source_base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "mode": "live configured model with synthetic external boundaries" if ai else "schema-aware model and external doubles",
        "source_sha256": source_snapshot,
        "source_stable": all(hashlib.sha256(p.read_bytes()).hexdigest() == source_snapshot[str(p.relative_to(ROOT)).replace("\\", "/")] for p in source_files),
        "original_corpus_sha256": hashlib.sha256((ROOT / "tests/executive_scenarios.py").read_bytes()).hexdigest(),
        "executed_original_case_ids": [row["id"] for row in records if row["executions"]],
        "counts": dict(Counter(row["status"] for row in records)), "pytest_exit_code": int(code),
        "original_acceptance_complete": False, "production_case_execution": False,
        "semantic_evaluation": "Only the decline case uses a factual model rubric, additionally gated by exact tool/state/privacy assertions",
        "cases": records}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("mode", "counts", "executed_original_case_ids", "original_acceptance_complete")}))
    return 0 if code == 0 and report["counts"].get("Pass") == 4 and report["source_stable"] else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--basetemp", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Use configured AI credentials; all other services remain synthetic")
    args = parser.parse_args()
    from chronos.ai import ExternalAI
    from chronos.settings import Settings
    raise SystemExit(audit(args.output, args.basetemp, ExternalAI(Settings(_env_file=None)) if args.live else None))
