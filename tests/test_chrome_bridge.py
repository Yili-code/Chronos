import pytest

from chronos.chrome_bridge import (
    BrowserBridgeError,
    BridgeTabTransport,
    parse_observation,
    safe_origin_path,
)
from chronos.study_adapter import ChromeBrowserConnector, ConnectorSignal


def test_observation_strips_query_and_fragment_before_app_use():
    observation = parse_observation({
        "url": "https://tronclass.ntou.edu.tw/user/index?ticket=secret#home",
        "visible_text": "張壹理 學生 我的課程",
    })

    assert observation.url == "https://tronclass.ntou.edu.tw/user/index"
    assert "ticket" not in observation.url


def test_bridge_rejects_secret_bearing_payload():
    with pytest.raises(BrowserBridgeError, match="forbidden"):
        parse_observation({
            "url": "https://tronclass.ntou.edu.tw/user/index",
            "visible_text": "學生",
            "cookies": "must-not-cross-boundary",
        })


def test_bridge_rejects_invalid_or_userinfo_urls():
    with pytest.raises(BrowserBridgeError):
        safe_origin_path("not-a-url")
    with pytest.raises(BrowserBridgeError):
        safe_origin_path("https://user:password@tronclass.ntou.edu.tw/user/index")


class FakeBridge:
    def __init__(self, payload):
        self.payload = payload
        self.requested_tab = None

    def observe_tab(self, tab_id):
        self.requested_tab = tab_id
        return self.payload


def test_bridge_tab_transport_returns_redacted_observation():
    bridge = FakeBridge({
        "url": "https://tronclass.ntou.edu.tw/user/index?ticket=secret",
        "visible_text": "張壹理 學生 我的課程",
    })
    transport = BridgeTabTransport(bridge, "tab-1")

    assert transport.current_url() == "https://tronclass.ntou.edu.tw/user/index"
    assert transport.visible_text() == "張壹理 學生 我的課程"
    assert bridge.requested_tab == "tab-1"


def test_bridge_transport_feeds_chrome_connector_without_secrets():
    bridge = FakeBridge({
        "url": "https://tronclass.ntou.edu.tw/user/index?ticket=secret",
        "visible_text": "張壹理 學生 我的課程",
    })

    signal = ChromeBrowserConnector(BridgeTabTransport(bridge, "tab-1")).observe()

    assert signal is ConnectorSignal.AUTHENTICATED_PAGE
