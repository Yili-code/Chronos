import ssl

import httpx
import pytest

from chronos.tronclass_auth import (
    ProbeFailure,
    authenticated_tronclass_response,
    create_tls_context,
    sanitized_origin_path,
    service_callback_url,
    service_from_cas_redirect,
    valid_service_ticket,
    valid_tgt_location,
)


def test_callback_preserves_the_service_query():
    callback = service_callback_url(
        "https://tronclass.ntou.edu.tw/login?next=/user/index",
        "ST-redacted_123.example",
    )
    assert callback == (
        "https://tronclass.ntou.edu.tw/login?next=/user/index"
        "&ticket=ST-redacted_123.example"
    )


def test_service_is_taken_from_the_live_cas_redirect_shape():
    location = (
        "https://tccas.ntou.edu.tw/cas/login?ui_locales=zh-TW&"
        "service=https%3A%2F%2Ftronclass.ntou.edu.tw%2Flogin%3Fnext%3D%2Fuser%2Findex"
    )
    assert service_from_cas_redirect(location) == (
        "https://tronclass.ntou.edu.tw/login?next=/user/index"
    )


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/cas/login?service=https%3A%2F%2Ftronclass.ntou.edu.tw",
        "https://tccas.ntou.edu.tw/cas/login?service=https%3A%2F%2Fevil.example%2Flogin",
        "https://tccas.ntou.edu.tw/cas/login",
    ],
)
def test_service_redirect_rejects_unknown_origins_and_shapes(location):
    with pytest.raises(ProbeFailure):
        service_from_cas_redirect(location)


def test_sensitive_ticket_material_is_removed_from_urls():
    assert sanitized_origin_path(
        "https://tccas.ntou.edu.tw/cas/v1/tickets/TGT-secret-value?ticket=ST-secret"
    ) == "https://tccas.ntou.edu.tw/cas/v1/tickets/[redacted-ticket]"
    assert sanitized_origin_path(
        "https://tronclass.ntou.edu.tw/user/index;jsessionid=secret?ticket=ST-secret"
    ) == "https://tronclass.ntou.edu.tw/user/index"


def test_ticket_shapes_and_tgt_origin_are_strict():
    assert valid_tgt_location("https://tccas.ntou.edu.tw/cas/v1/tickets/TGT-opaque")
    assert not valid_tgt_location("https://example.com/cas/v1/tickets/TGT-opaque")
    assert valid_service_ticket("ST-opaque_123.example")
    assert not valid_service_ticket("not-a-ticket")


def test_tls_compatibility_keeps_verification_enabled():
    context = create_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_authenticated_response_requires_the_tronclass_user_surface():
    assert authenticated_tronclass_response(
        httpx.Response(200, request=httpx.Request("GET", "https://tronclass.ntou.edu.tw/user/index"))
    )
    assert not authenticated_tronclass_response(
        httpx.Response(200, request=httpx.Request("GET", "https://tccas.ntou.edu.tw/cas/login"))
    )
    assert not authenticated_tronclass_response(
        httpx.Response(200, request=httpx.Request("GET", "https://tronclass.ntou.edu.tw/login"))
    )
