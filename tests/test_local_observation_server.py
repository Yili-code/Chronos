import json
from http.client import HTTPConnection

from chronos.local_observation_server import LocalObservationServer


def test_material_http_rejection_preserves_previous_snapshot():
    import threading

    server = LocalObservationServer(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = {"status": "observed", "materials": [{
        "source_id": "1", "course_id": "2", "activity_id": "3",
        "filename": "lecture.pdf", "uploaded_at": None,
    }]}
    try:
        for value, expected in ((payload, 202), ({**payload, "cookies": "rejected"}, 400)):
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request(
                "POST", "/v1/browser-materials", body=json.dumps(value),
                headers={"Content-Type": "application/json", "X-Chronos-Bridge": "1"},
            )
            response = connection.getresponse()
            assert response.status == expected
            response.read()
            connection.close()
        assert server.material_store.course_materials("2") == payload["materials"]
    finally:
        server.shutdown()
        server.server_close()


def test_loopback_receiver_accepts_redacted_observation():
    server = LocalObservationServer(0)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        server.handle_request  # keep the server API explicit in this test
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        payload = {
            "url": "https://tronclass.ntou.edu.tw/user/index?ticket=secret",
            "visible_text": "課程首頁",
        }
        connection.request(
            "POST",
            "/v1/browser-observation",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json", "X-Chronos-Bridge": "1"},
        )
        response = connection.getresponse()
        assert response.status == 202
        assert server.observation_store.latest().url.endswith("/user/index")
    finally:
        server.shutdown()
        server.server_close()


def test_loopback_receiver_rejects_secret_fields():
    server = LocalObservationServer(0)
    try:
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/v1/browser-observation",
            body=json.dumps({
                "url": "https://tronclass.ntou.edu.tw/user/index",
                "visible_text": "課程首頁",
                "cookies": "must-not-cross-boundary",
            }),
            headers={"Content-Type": "application/json", "X-Chronos-Bridge": "1"},
        )
        assert connection.getresponse().status == 400
        assert server.observation_store.latest() is None
    finally:
        server.shutdown()
        server.server_close()
