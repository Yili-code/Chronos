import sqlite3

from chronos.db import Database, REMOVED_STUDY_TABLES
from chronos.settings import Settings


def test_initialize_removes_legacy_study_tables(tmp_path):
    database_path = tmp_path / "chronos.db"
    with sqlite3.connect(database_path) as connection:
        for table in REMOVED_STUDY_TABLES:
            connection.execute(f"CREATE TABLE {table} (value TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES ('legacy')")

    Database(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert set(REMOVED_STUDY_TABLES).isdisjoint(remaining)


def test_assignment_preparation_settings_are_removed():
    settings = Settings(_env_file=None)

    assert not hasattr(settings, "preparation_gemini_model")
    assert not hasattr(settings, "preparation_daily_request_limit")
    assert not hasattr(settings, "preparation_daily_token_limit")
