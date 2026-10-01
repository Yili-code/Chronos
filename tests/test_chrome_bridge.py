import pytest

from chronos.chrome_bridge import (
    BrowserBridgeError,
    parse_observation,
    safe_origin_path,
)


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
