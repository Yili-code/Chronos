from chronos.study_adapter import (
    BrowserSessionAdapter,
    ChromeBrowserConnector,
    ConnectorSignal,
    FakeBrowserConnector,
    ResultStatus,
    SessionStatus,
    STATUS_LABELS_ZH,
    classify_attachment,
    classify_read,
)


class FakeTab:
    def __init__(self, url: str = "", text: str = "", error: Exception | None = None):
        self.url = url
        self.text = text
        self.error = error

    def current_url(self):
        if self.error:
            raise self.error
        return self.url

    def visible_text(self):
        if self.error:
            raise self.error
        return self.text


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


def test_read_result_is_ok_only_for_authenticated_session():
    state = BrowserSessionAdapter(
        FakeBrowserConnector(ConnectorSignal.AUTHENTICATED_PAGE)
    ).session()

    result = classify_read(state, ["course-1"], operation="list_courses")

    assert result.status is ResultStatus.OK
    assert result.value == ["course-1"]
    assert result.diagnostic.operation == "list_courses"
    assert result.diagnostic.retryable is False


def test_read_result_requires_reauth_after_cas_redirect():
    state = BrowserSessionAdapter(
        FakeBrowserConnector(ConnectorSignal.CAS_REDIRECT)
    ).session()

    result = classify_read(state, ["should-not-leak"], operation="list_courses")

    assert result.status is ResultStatus.REAUTH_REQUIRED
    assert result.value is None
    assert result.diagnostic.retryable is False


def test_attachment_persistence_failure_is_deferred():
    state = BrowserSessionAdapter(
        FakeBrowserConnector(ConnectorSignal.AUTHENTICATED_PAGE)
    ).session()

    result = classify_attachment(
        state,
        {"name": "lab.pdf"},
        persisted=False,
        operation="download_attachment",
    )

    assert result.status is ResultStatus.DEFERRED_ATTACHMENT
    assert result.value is None
    assert result.diagnostic.retryable is False


def test_attachment_deferral_does_not_hide_reauth():
    state = BrowserSessionAdapter(
        FakeBrowserConnector(ConnectorSignal.CAS_REDIRECT)
    ).session()

    result = classify_attachment(
        state,
        {"name": "lab.pdf"},
        persisted=False,
        operation="download_attachment",
    )

    assert result.status is ResultStatus.REAUTH_REQUIRED
    assert result.value is None


def test_chrome_connector_classifies_authenticated_tab():
    connector = ChromeBrowserConnector(FakeTab(
        "https://tronclass.ntou.edu.tw/user/index#/",
        "張壹理 學生 我的課程",
    ))

    assert connector.observe() is ConnectorSignal.AUTHENTICATED_PAGE


def test_chrome_connector_classifies_cas_redirect():
    connector = ChromeBrowserConnector(FakeTab(
        "https://tccas.ntou.edu.tw/cas/login?service=redacted",
        "請登入",
    ))

    assert connector.observe() is ConnectorSignal.CAS_REDIRECT


def test_chrome_connector_classifies_transport_failure_as_timeout():
    connector = ChromeBrowserConnector(FakeTab(error=TimeoutError()))

    assert connector.observe() is ConnectorSignal.TIMEOUT


def test_user_facing_status_labels_are_plain_language():
    assert STATUS_LABELS_ZH[SessionStatus.READY] == "登入正常"
    assert STATUS_LABELS_ZH[SessionStatus.REAUTH_REQUIRED] == "需要重新登入"
    assert STATUS_LABELS_ZH[SessionStatus.UNKNOWN] == "暫時無法確認登入狀態"
    assert "附件" in STATUS_LABELS_ZH[ResultStatus.DEFERRED_ATTACHMENT]
