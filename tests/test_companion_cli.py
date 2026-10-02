import argparse
import pytest
from chronos.run_summary_companion import parser, run, course_mapping


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
