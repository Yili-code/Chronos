import argparse
import pytest
from chronos.run_summary_companion import parser, run, course_mapping


def test_explicit_firestore_route_does_not_mutate_settings():
    from chronos.settings import Settings
    from chronos.run_summary_companion import routed_settings
    original = Settings(_env_file=None, database_backend="sqlite")
    args = parser().parse_args(["--firestore-project", "test-project",
        "--firestore-database", "(default)", "--firestore-prefix", "test-prefix"])
    configured = routed_settings(original, args)
    assert configured.database_backend == "firestore"
    assert configured.firestore_project_id == "test-project"
    assert configured.firestore_database == "(default)"
    assert configured.firestore_collection_prefix == "test-prefix"
    assert original.database_backend == "sqlite"


@pytest.mark.parametrize("arguments", [
    ["--firestore-project", "test-project"],
    ["--firestore-prefix", "test-prefix"],
    ["--firestore-project", "p", "--firestore-database", "d", "--firestore-prefix", "a/b"],
])
def test_partial_or_invalid_route_is_rejected(arguments):
    from chronos.settings import Settings
    from chronos.run_summary_companion import routed_settings
    with pytest.raises(ValueError):
        routed_settings(Settings(_env_file=None), parser().parse_args(arguments))


@pytest.mark.asyncio
async def test_default_launcher_is_disabled(capsys):
    assert await run(parser().parse_args([])) == 0
    assert "disabled" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_enable_alone_cannot_start_provider(capsys):
    assert await run(parser().parse_args(["--enable"])) == 2
    assert "configuration_required" in capsys.readouterr().out


@pytest.mark.parametrize("value", ['[]', '{}', '{"unknown":"1"}', '{"operating-systems":"https://example.invalid"}'])
def test_mapping_rejects_untrusted_or_unknown_destinations(value):
    with pytest.raises(argparse.ArgumentTypeError):
        course_mapping(value)


@pytest.mark.asyncio
async def test_check_is_read_only_even_with_enable(tmp_path, monkeypatch, capsys):
    from chronos.settings import Settings
    import chronos.settings as settings_module
    import chronos.db as database_module
    config = Settings(_env_file=None, database_path=tmp_path / "absent.db",
                      telegram_bot_token="synthetic-private-token", telegram_chat_id=123,
                      gemini_api_key="synthetic-private-key")
    monkeypatch.setattr(settings_module, "settings", config)
    def forbidden(*args, **kwargs):
        pytest.fail("preflight must not construct a database")
    monkeypatch.setattr(database_module, "create_database", forbidden)
    assert await run(parser().parse_args(["--check", "--enable"])) == 0
    output = capsys.readouterr().out
    assert '"remote_readiness": "not_checked"' in output
    assert '"sqlite_file_exists": false' in output
    assert "synthetic-private" not in output
    assert not config.database_path.exists()
