from http.client import HTTPConnection
import json
import threading
import pytest
from chronos.local_observation_server import LocalObservationServer


@pytest.mark.parametrize("origin,expected", [
    ("chrome-extension://" + "a" * 32, 202),
    ("chrome-extension://" + "b" * 32, 403),
    ("chrome-extension://" + "a" * 32 + ".evil", 403),
    ("https://example.invalid", 403),
    ("null", 403),
])
def test_receiver_accepts_only_exact_configured_extension(origin, expected):
    server = LocalObservationServer(0, extension_id="a" * 32)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("POST", "/v1/browser-materials",
            body=json.dumps({"status": "unknown", "materials": []}),
            headers={"Origin": origin, "X-Chronos-Bridge": "1", "Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == expected
        assert response.getheader("Access-Control-Allow-Origin") == (origin if expected == 202 else None)
        response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_invalid_extension_id_is_rejected_before_binding():
    with pytest.raises(ValueError):
        LocalObservationServer(0, extension_id="not-an-extension")
