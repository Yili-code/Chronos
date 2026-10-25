"""The live verification command is opt-in and rate bounded before credentials load."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "flags,detail", [([], "--live"), (["--live", "--interval", "0"], "ten seconds")]
)
def test_live_harness_rejects_unsafe_invocation_before_loading_settings(
    tmp_path, flags, detail
):
    output = tmp_path / "evidence.json"
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "scripts/verify_reliability_model.py"
            ),
            "--env-file",
            str(tmp_path / "nonexistent.env"),
            "--output",
            str(output),
            *flags,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert detail in result.stderr
    assert not output.exists()
