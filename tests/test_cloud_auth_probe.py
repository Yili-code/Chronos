import json

from chronos import cloud_auth_probe


def test_cloud_probe_requires_both_secret_values(monkeypatch, capsys):
    monkeypatch.delenv("CHRONOS_TRONCLASS_USERNAME", raising=False)
    monkeypatch.delenv("CHRONOS_TRONCLASS_PASSWORD", raising=False)

    assert cloud_auth_probe.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "failure_stage": "cloud_credentials_unavailable"
    }


def test_cloud_probe_emits_only_the_sanitized_report(monkeypatch, capsys):
    monkeypatch.setenv("CHRONOS_TRONCLASS_USERNAME", "test-user")
    monkeypatch.setenv("CHRONOS_TRONCLASS_PASSWORD", "test-password")
    monkeypatch.setattr(
        cloud_auth_probe,
        "run_probe",
        lambda username, password: {"session_reuse_authenticated": True, "cookie_count": 1},
    )

    assert cloud_auth_probe.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "cookie_count": 1,
        "session_reuse_authenticated": True,
    }
