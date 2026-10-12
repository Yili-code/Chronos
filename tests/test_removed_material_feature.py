import sqlite3

from chronos.db import Database, REMOVED_MATERIAL_TABLES


def test_initialize_removes_legacy_material_tables(tmp_path):
    database_path = tmp_path / "chronos.db"
    with sqlite3.connect(database_path) as connection:
        for table in REMOVED_MATERIAL_TABLES:
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

    assert set(REMOVED_MATERIAL_TABLES).isdisjoint(remaining)
