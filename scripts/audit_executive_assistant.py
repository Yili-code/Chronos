"""Reproducible offline audit; exit 2 when acceptance is blocked.

Run with the dev environment. Test doubles never contact external recipients.
Component success is explicitly distinguished from end-to-end acceptance.
"""
import argparse
import asyncio
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from executive_scenarios import SCENARIOS
from test_executive_mail_evidence import CASES, sample_mail
from chronos.db import Database
from chronos.mail_workflow import MailWorkflow, filter_reason
from chronos.settings import Settings


async def audit(output, defer_messaging=False):
    results = []
    for identifier, instruction, part, expected in CASES:
        mail = sample_mail(part, security=identifier == "E09")
        actual = filter_reason(mail, discard_account_security_after=100)
        passed = actual == expected
        results.append({
            "id": identifier, "domain": "email", "input": instruction,
            "preconditions": {"decoded_mail": mail, "extra_mime_part": part, "security_rule_cutoff": 100},
            "expected": "Retain incomplete evidence" if expected is None else "Permit ordinary promotion cleanup",
            "actual_reply": None, "actual_component_result": actual,
            "tool_calls": [], "api_results": [], "database_state": "Not involved in component test",
            "difference": None if passed else "Unsafe classification",
            "status": "Pass" if passed else "Fail", "scope": "decoder-to-classifier component only",
            "severity": "High" if identifier != "E10" else None,
            "root_cause": "Ignored or undecodable MIME content was not propagated as incomplete evidence" if identifier != "E10" else None,
            "fix": "Propagate body_incomplete; retain before all automatic cleanup rules; skip attached message trees",
            "regression": "Passed" if passed else "Failed",
            "end_to_end_verified": False,
        })
    # Use a real workflow and SQLite store. All remote boundaries are observable
    # test doubles; this is not a statement about live platform authorization.
    with tempfile.TemporaryDirectory(prefix="executive-audit-", dir=output.parent) as temp:
        db = Database(Path(temp) / "audit.db")
        db.initialize()
        config = Settings(_env_file=None, enable_gmail=True, gmail_account="owner@example.com",
            gmail_client_id="synthetic-client", gmail_client_secret="synthetic-secret",
            gmail_refresh_token="synthetic-refresh", telegram_chat_id=123,
            telegram_bot_token="synthetic-token", telegram_webhook_secret="synthetic-hook")
        gmail, telegram, ai = AsyncMock(), AsyncMock(), AsyncMock()
        telegram.enabled = True
        workflow = MailWorkflow(db, gmail, telegram, ai, config)
        for domain, cases in SCENARIOS.items():
            for index, (instruction, precondition, expected) in enumerate(cases, 1):
                deferred = defer_messaging and (domain == "messaging" or
                    (domain, index) in {("drafting", 10), ("contacts", 10), ("relationships", 10),
                                       ("integration", 2), ("integration", 3), ("integration", 10)})
                if deferred:
                    results.append({"id": f"{domain.upper()}-{index:02}", "domain": domain,
                        "input": instruction, "preconditions": precondition, "expected": expected,
                        "actual_reply": None, "tool_calls": [], "api_results": [], "database_state": "Not executed",
                        "difference": "User explicitly deferred LINE/Instagram integration on 2026-11-03",
                        "status": "Deferred", "scope": "Excluded from current acceptance scope",
                        "severity": None, "root_cause": "User scope decision", "fix": "Not requested in current scope",
                        "regression": "Not executed", "end_to_end_verified": False})
                    continue
                for boundary in (gmail, telegram, ai):
                    boundary.reset_mock()
                workflow.patch("message:ab12", mail=sample_mail())
                before = workflow.get("message:ab12")
                tasks_before = db.list_open_tasks()
                action = await workflow.prepare_action("ab12", instruction)
                reply = action()
                after = workflow.get("message:ab12")
                calls = {name: [str(call) for call in boundary.mock_calls]
                         for name, boundary in (("gmail", gmail), ("telegram", telegram), ("ai", ai))}
                unchanged = before == after and tasks_before == db.list_open_tasks()
                safe = unchanged and all(not value for value in calls.values())
                results.append({
                    "id": f"{domain.upper()}-{index:02}", "domain": domain, "input": instruction,
                    "preconditions": {"scenario": precondition, "bound_mail_id": "ab12",
                        "note": "Scenario-dependent adapters/models are absent; external scenario not materialized"},
                    "expected": expected, "actual_reply": reply, "tool_calls": calls,
                    "api_results": [], "database_state": {"unchanged": unchanged, "open_tasks": len(db.list_open_tasks())},
                    "difference": "No requested domain workflow or state model; current mail-card fallback only",
                    "status": "Blocked" if safe else "Fail", "scope": "mail-card fallback safety; domain acceptance unsupported",
                    "severity": "High", "root_cause": "Missing application workflow/adapter/state model",
                    "fix": "Unresolved: implement domain workflow plus authorized official adapter and state reconciliation",
                    "regression": "Fallback produced no side effects" if safe else "Unexpected side effect",
                    "end_to_end_verified": False,
                })
    counts = dict(Counter(item["status"] for item in results))
    report = {"updated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "mode": "offline synthetic component and boundary audit; no live sends or mailbox writes",
        "acceptance_gate_passed": False,
        "warning": "Component Pass is not natural-language or live end-to-end acceptance. Blocked cases were not executed against missing domain adapters.",
        "total": len(results), "counts": counts,
        "component_pass_fraction_of_catalog": counts.get("Pass", 0) / len(results),
        "deferred_messaging": defer_messaging,
        "end_to_end_pass_count": 0, "cases": results}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": len(results), "counts": counts, "acceptance_gate_passed": False}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--defer-messaging", action="store_true", help="Exclude LINE/Instagram and dependent cases by user instruction")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(asyncio.run(audit(args.output, args.defer_messaging)))
