from chronos.study_adapter import (
    BrowserSessionAdapter,
    ConnectorSignal,
    FakeBrowserConnector,
    SessionStatus,
)


def test_session_state_transitions_ready_to_reauth_to_unknown():
    connector = FakeBrowserConnector(
        ConnectorSignal.AUTHENTICATED_PAGE,
        ConnectorSignal.CAS_REDIRECT,
        ConnectorSignal.TIMEOUT,
    )
    adapter = BrowserSessionAdapter(connector)

    states = [adapter.session() for _ in range(3)]

    assert [state.status for state in states] == [
        SessionStatus.READY,
        SessionStatus.REAUTH_REQUIRED,
        SessionStatus.UNKNOWN,
    ]
    assert states[0].evidence is ConnectorSignal.AUTHENTICATED_PAGE
    assert states[1].evidence is ConnectorSignal.CAS_REDIRECT
    assert states[2].evidence is ConnectorSignal.TIMEOUT


def test_empty_connector_fails_closed_as_unknown():
    state = BrowserSessionAdapter(FakeBrowserConnector()).session()

    assert state.status is SessionStatus.UNKNOWN
    assert state.evidence is ConnectorSignal.TIMEOUT


def test_state_model_contains_no_secret_material():
    state = BrowserSessionAdapter(
        FakeBrowserConnector(ConnectorSignal.CAS_REDIRECT)
    ).session()

    rendered = repr(state)
    assert "cookie" not in rendered.lower()
    assert "ticket" not in rendered.lower()
    assert "password" not in rendered.lower()
