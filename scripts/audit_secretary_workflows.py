"""Observe 70 executable workflow scenarios and 10 explicitly deferred platform cases.

This catalog supplements, never regrades, the original natural-language audit.
It runs existing behavioral assertions and records actual synthetic command/API/
state traces. A passing workflow test is not a live-model or Production acceptance.
"""
import argparse
import ast
from collections import Counter
from datetime import datetime
import functools
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

GROUPS = {
    "email": ("test_mail_search.py", """read_only_search_binds_card_and_enables_follow_on_draft
next_page_is_versioned_and_old_cursor_cannot_replay
missing_unbounded_or_control_query_never_reads_mail
partial_or_provider_failure_is_not_an_empty_result
concurrent_local_change_preserved
delivery_resumes_after_last_known_part_without_refetching
injection_and_long_metadata_are_only_escaped_excerpt
expired_or_other_chat_cursor_reads_nothing
account_switch_cannot_apply_or_bind_prior_search
webhook_search_replay_then_bound_draft"""),
    "drafting": ("test_mail_correspondence.py", """full_thread_review_has_explicit_recipient_no_remote_write
confirmed_native_draft_and_duplicate_confirmation
missing_stale_or_cancelled_confirmation_does_not_create
new_message_invalidates_reviewed_draft
uncertain_create_is_not_replayed_or_replaced
definite_permission_rejection_preserves_review
two_save_requests_share_one_remote_create
prepared_revision_cannot_overwrite_inflight_native_save
questions_require_new_review_before_gmail_save
unsupported_recipient_or_send_operations_are_not_partially_executed"""),
    "contacts": ("test_contact_relationships.py", """identity_uses_owner_name_and_explicit_mail_provenance_without_ai
duplicate_email_is_not_added_or_overwritten
same_name_distinct_people_remain_separate
concurrent_duplicate_creations_have_one_winner
contact_update_requires_current_version
deletion_is_confirmed_and_replay_does_not_delete_other_people
concurrent_update_invalidates_delete_confirmation
conflicting_people_cannot_be_silently_merged
contact_creation_and_update_work_inside_webhook_receipt_transaction
ambiguous_source_never_creates_or_resolves_contact"""),
    "relationships": ("test_contact_relationships.py", """relationship_suggestions_require_history_and_honor_opt_out
invalid_birthdays_are_not_guessed_and_known_birthday_is_only_suggestion
large_interaction_history_is_paginated_without_data_loss
contact_email_pages_preserve_all_explicitly_merged_addresses
birthday_uses_owner_date_when_scheduler_time_is_utc
do_not_contact_overrides_even_explicit_known_birthday
overfrequent_cadence_rejected_without_changing_existing_preference
email_claimed_birthday_does_not_become_relationship_fact
contact_account_isolation
explicit_merge_keeps_all_addresses_and_stricter_contact_preference"""),
    "meetings": ("test_meeting_invitations.py", """invitation_preparation_is_explicit_and_unsent
organizer_confirmed_does_not_mean_everyone_accepted
stale_invitation_approval_never_submits
rsvp_is_verified_for_every_invitee
omitted_guests_and_foreign_edits_are_not_accepted
unknown_create_is_never_replayed_and_can_be_reconciled
cancel_requires_its_own_review_and_provider_ack
source_change_and_opt_out_prevent_invitation
concurrent_confirmations_submit_one_invitation
external_conversion_to_recurring_event_blocks_series_cancellation"""),
    "followups": ("test_mail_followups.py", """direction_is_grounded_in_actual_latest_message
human_reply_resolves_wait_without_chasing
owner_response_closes_waiting_owner
auto_reply_pauses_and_later_human_reply_resolves
due_reminder_is_owner_only_once_and_never_external_send
cancel_prevents_future_reminders
read_timeout_suppresses_reminders_from_stale_state
ambiguous_notification_is_not_replayed
concurrent_refresh_claims_one_notification
parallel_track_preparations_do_not_overwrite"""),
    "integration": ("test_secretary_integration.py", """draft_tracking_and_actual_owner_response_resolve_without_sending
contact_opt_out_prevents_meeting_generation
cancelled_thread_blocks_both_stale_draft_and_meeting_hold
same_name_contacts_on_distinct_cards_are_not_linked
email_body_forged_authority_does_not_modify_contacts
native_gmail_draft_does_not_claim_meeting_or_create_event
explicit_private_hold_and_contact_update_do_not_send_invitation
private_hold_cancel_does_not_remove_contact_history
birthday_suggestion_is_not_an_outgoing_message_or_draft
account_switch_does_not_reuse_private_contact_or_draft_binding"""),
}


def safe(value):
    if isinstance(value, type):
        return value.__name__
    if hasattr(value, "model_dump") and not isinstance(value, AsyncMock):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return f"<{type(value).__name__}>"


def assertions(node):
    checks = []
    for entry in ast.walk(node):
        if isinstance(entry, ast.Assert):
            checks.append(ast.unparse(entry.test))
        elif isinstance(entry, ast.Call) and isinstance(entry.func, ast.Attribute) and (
                entry.func.attr.startswith("assert_") or entry.func.attr == "raises"):
            checks.append(ast.unparse(entry))
    return checks


class Observer:
    def __init__(self):
        self.catalog, self.runs, self.current = {}, {}, None
        for domain, (filename, names) in GROUPS.items():
            source = (ROOT / "tests" / filename).read_text(encoding="utf-8")
            nodes = {n.name: n for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert len(names.splitlines()) == 10
            for index, name in enumerate(names.splitlines(), 1):
                function = "test_" + name
                node = nodes[function]
                assert assertions(node)
                key = (filename, function)
                assert key not in self.catalog
                self.catalog[key] = {"id": f"{domain.upper()}-{index:02}", "domain": domain,
                    "expected_behavior": name.replace("_", " "), "test": filename + "::" + function,
                    "assertions": assertions(node),
                    "source_line": node.lineno}

    def pytest_collection_modifyitems(self, config, items):
        selected, rejected = [], []
        for item in items:
            key = (item.path.name, item.originalname or item.name)
            (selected if key in self.catalog else rejected).append(item)
        items[:] = selected
        config.hook.pytest_deselected(items=rejected)

    def pytest_runtest_setup(self, item):
        self.current = {"node": item.nodeid, "parameters": safe(getattr(item, "callspec", None).params)
                        if hasattr(item, "callspec") else {}, "commands": [], "actual_replies": [],
                        "api_results": [], "database_transitions": [], "workflows": []}

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = (yield).get_result()
        if report.when != "call" and not report.failed:
            return
        key = (item.path.name, item.originalname or item.name)
        row = self.current
        if key not in self.catalog or row is None:
            return
        row["status"] = "Pass" if report.passed else "Fail" if report.failed else "Blocked"
        row["difference"] = None if report.passed else str(report.longrepr)
        row["tool_calls"] = [{k: event[k] for k in ("method", "args", "kwargs")} for event in row["api_results"]]
        workflows = row.pop("workflows", [])
        row["database_final"] = {}
        for mail in workflows:
            keys = {change["key"] for change in row["database_transitions"] if change["namespace"] == mail.namespace}
            keys.update(mail.key(suffix) for suffix in ("message:ab12", "reply:ab12", "meeting:ab12", "contacts", "followups", "search"))
            row["database_final"].update({key: mail.db.get_mail_state(key) for key in keys})
        self.runs.setdefault(key, []).append(row)
        self.current = None

    def patches(self):
        from chronos.mail_workflow import MailWorkflow
        from chronos.contact_relationships import ContactRelationships
        from chronos.db import Database
        from chronos.firestore_db import FirestoreDatabase
        from chronos import mail_search
        observer = self
        original_init = MailWorkflow.__init__
        def init(mail, *args, **kwargs):
            original_init(mail, *args, **kwargs)
            if observer.current is not None:
                observer.current["workflows"].append(mail)
        original_async = AsyncMock._execute_mock_call
        async def api(mock, *args, **kwargs):
            row = observer.current
            event = {"method": mock._extract_mock_name(), "args": safe(args), "kwargs": safe(kwargs)}
            try:
                value = await original_async(mock, *args, **kwargs)
                event["result"] = safe(value)
                return value
            except Exception as error:
                event["exception"] = {"type": type(error).__name__, "message": str(error)}
                raise
            finally:
                if row is not None:
                    row["api_results"].append(event)
        def observed(original, asynchronous):
            def result(value):
                if not callable(value):
                    return value
                def apply():
                    answer = value()
                    if observer.current is not None:
                        observer.current["actual_replies"].append(safe(answer))
                    return answer
                return apply
            def record(args, kwargs):
                if observer.current is not None:
                    mail = args[0] if isinstance(args[0], MailWorkflow) else getattr(args[0], "mail", None)
                    if mail is not None and mail not in observer.current["workflows"]:
                        observer.current["workflows"].append(mail)
                    observer.current["commands"].append({"entrypoint": original.__qualname__, "args": safe(args[1:]), "kwargs": safe(kwargs)})
            if asynchronous:
                @functools.wraps(original)
                async def wrapped(*args, **kwargs):
                    record(args, kwargs)
                    return result(await original(*args, **kwargs))
            else:
                @functools.wraps(original)
                def wrapped(*args, **kwargs):
                    record(args, kwargs)
                    return result(original(*args, **kwargs))
            return wrapped
        def mutation(original):
            @functools.wraps(original)
            def wrapped(db, key, transition):
                value = original(db, key, transition)
                if observer.current is not None:
                    namespace = key.split(":", 2)
                    observer.current["database_transitions"].append({"key": key,
                        "namespace": ":".join(namespace[:2]) + ":", "result": safe(value)})
                return value
            return wrapped
        def comparison(original):
            @functools.wraps(original)
            def wrapped(db, expected, changes):
                committed = original(db, expected, changes)
                if observer.current is not None:
                    for key, value in changes.items():
                        observer.current["database_transitions"].append({"key": key,
                            "namespace": ":".join(key.split(":", 2)[:2]) + ":",
                            "expected": safe(expected[key]), "committed": committed, "result": safe(value)})
                return committed
            return wrapped
        return [patch.object(MailWorkflow, "__init__", init), patch.object(AsyncMock, "_execute_mock_call", api),
            patch.object(MailWorkflow, "prepare_action", observed(MailWorkflow.prepare_action, True)),
            patch.object(ContactRelationships, "prepare", observed(ContactRelationships.prepare, False)),
            patch.object(mail_search, "prepare", observed(mail_search.prepare, True)),
            patch.object(Database, "compare_mail_states", comparison(Database.compare_mail_states)),
            patch.object(FirestoreDatabase, "compare_mail_states", comparison(FirestoreDatabase.compare_mail_states)),
            patch.object(Database, "mutate_mail_state", mutation(Database.mutate_mail_state)),
            patch.object(FirestoreDatabase, "mutate_mail_state", mutation(FirestoreDatabase.mutate_mail_state))]

    def report(self, output, exit_code):
        from executive_scenarios import SCENARIOS
        records = []
        for key, definition in self.catalog.items():
            runs = self.runs.get(key, [])
            status = "Pass" if runs and all(r["status"] == "Pass" for r in runs) else "Fail" if any(r["status"] == "Fail" for r in runs) else "Blocked"
            records.append({**definition, "status": status, "preconditions": "Synthetic fixtures, parameters and ordered DB transitions in executions",
                "original_input": [r["commands"] for r in runs], "executions": runs,
                "severity": None if status == "Pass" else "High", "root_cause": None if status == "Pass" else "See failed executable assertion or missing execution",
                "fix_and_regression": "Behavioral assertions passed on the observed source" if status == "Pass" else "Unresolved; do not change expectations to pass",
                "scope": "Executable workflow/service regression with mocked external/model boundaries", "live_model_verified": False, "production_case_verified": False})
        for i, (text, condition, expected) in enumerate(SCENARIOS["messaging"], 1):
            records.append({"id": f"MESSAGING-{i:02}", "domain": "messaging", "original_input": text,
                "preconditions": condition, "expected_behavior": expected, "actual_reply": None,
                "executions": [], "status": "Deferred", "severity": None, "root_cause": "Owner deferred LINE/Instagram",
                "fix_and_regression": "No adapter or live test claimed", "live_model_verified": False, "production_case_verified": False})
        counts = dict(Counter(r["status"] for r in records))
        report = {"updated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
            "source_base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip()),
            "source_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(list((ROOT / "chronos").glob("*.py"))
                    + [ROOT / "tests" / name for name in sorted({v[0] for v in GROUPS.values()})]
                    + [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / ".github/workflows/ci.yml"]
                    + [Path(__file__).resolve()])},
            "total": len(records), "counts": counts, "pytest_exit_code": exit_code,
            "workflow_regression_gate": exit_code == 0 and counts.get("Pass") == 70
                and all(run["commands"] and run["database_final"] for record in records if record["status"] == "Pass" for run in record["executions"]),
            "all_requested_capabilities_accepted": False,
            "limits": ["Original executive-audit-results.json expectations/outcomes remain unchanged",
                       "This catalog covers bounded implemented workflows, not arbitrary natural-language intent acceptance",
                       "LINE/Instagram deferred; only configured Gmail account; no Google Contacts sync",
                       "No real recipient test sends, Calendar writes, or live case-level Production execution",
                       "Broader search/draft/meeting composition and full original acceptance remain incomplete"],
            "cases": records}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(safe(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("total", "counts", "workflow_regression_gate", "all_requested_capabilities_accepted")}))
        return 0 if report["workflow_regression_gate"] else 2


def main():
    from contextlib import ExitStack
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--basetemp", type=Path, required=True)
    args = parser.parse_args()
    observer = Observer()
    with ExitStack() as stack:
        for item in observer.patches():
            stack.enter_context(item)
        code = pytest.main([str(ROOT / "tests" / file) for file in sorted({v[0] for v in GROUPS.values()})]
            + ["-q", "--basetemp=" + str(args.basetemp.resolve())], plugins=[observer])
    return observer.report(args.output, int(code))


if __name__ == "__main__":
    raise SystemExit(main())
