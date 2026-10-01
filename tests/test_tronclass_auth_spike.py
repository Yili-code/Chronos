import ssl

from scripts.spikes.tronclass_auth_probe import (
    create_tls_context,
    sanitized_origin_path,
    valid_service_ticket,
    valid_tgt_location,
)


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
