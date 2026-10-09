from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from chronos.ai_usage import AIUsageRecorder, format_ai_usage
from chronos.db import Database
from chronos.firestore_db import FirestoreDatabase
from chronos.repository import missing_task_repository_methods
from chronos.runtime_health import (
    RuntimeConfigurationError,
    readiness_snapshot,
    validate_runtime_security,
)
from chronos.settings import Settings
from chronos.web import render_page


TAIPEI = ZoneInfo("Asia/Taipei")


def public_settings(**changes):
    values = {
        "database_backend": "firestore",
        "telegram_bot_token": "token",
        "telegram_webhook_secret": "hook",
        "telegram_chat_id": 123,
        "scheduler_secret": "scheduler",
        "web_password": "password",
    }
    values.update(changes)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "field",
    [
        "telegram_bot_token",
        "telegram_webhook_secret",
        "telegram_chat_id",
        "scheduler_secret",
        "web_password",
    ],
)
def test_public_mode_fails_closed_when_required_security_is_missing(field):
    with pytest.raises(RuntimeConfigurationError):
        validate_runtime_security(public_settings(**{field: None if field == "telegram_chat_id" else ""}))


def test_local_mode_allows_explicitly_local_insecure_setup(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "local.db")
    db = Database(settings.database_path)
    db.initialize()
    assert readiness_snapshot(db, settings)["mode"] == "local"


def test_task_repository_contract_matches_both_backends(tmp_path):
    assert missing_task_repository_methods(Database(tmp_path / "contract.db")) == set()
    assert missing_task_repository_methods(FirestoreDatabase.__new__(FirestoreDatabase)) == set()


def test_web_page_uses_configured_timezone():
    page = render_page("UTC")
    assert 'const timeZone="UTC"' in page
    assert "Loading tasks..." in page
    assert "Retry" in page


def test_ai_usage_is_aggregate_and_contains_no_prompt(tmp_path):
    db = Database(tmp_path / "usage.db")
    db.initialize()
    recorder = AIUsageRecorder(db, TAIPEI)
    now = datetime(2026, 10, 9, 12, tzinfo=TAIPEI)
    recorder.record(
        "task", now=now, attempts=2, status="success", input_chars=18,
        prompt_tokens=30, output_tokens=10, total_tokens=40,
    )
    recorder.record("task", now=now, attempts=1, status="http_503", input_chars=8)
    records = recorder.report(now)
    assert records["task"] == {
        "requests": 2,
        "attempts": 3,
        "input_chars": 26,
        "prompt_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
        "statuses": {"success": 1, "http_503": 1},
    }
    assert "Requests: 2" in format_ai_usage(records, "2026-10-09")
