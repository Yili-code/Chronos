import json
from http.client import HTTPConnection

from chronos.local_observation_server import LocalObservationServer


def test_announcement_handoff_is_origin_checked_and_durable(tmp_path):
    import threading
    from test_announcements import payload
    extension = 'a' * 32
    server = LocalObservationServer(0, catalog_path=tmp_path / 'catalog.sqlite3', extension_id=extension)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for origin, body, expected, count in [
            ('https://example.test', payload(), 403, None),
            ('chrome-extension://' + extension, {**payload(), 'cookies':'synthetic'}, 400, None),
            ('chrome-extension://' + extension, payload(), 202, 1),
            ('chrome-extension://' + extension, payload(), 202, 0),
        ]:
            connection = HTTPConnection('127.0.0.1', server.server_port)
            connection.request('POST', '/v1/browser-announcements', body=json.dumps(body),
                headers={'Origin':origin, 'X-Chronos-Bridge':'1', 'Content-Type':'application/json'})
            response = connection.getresponse()
            receipt = json.loads(response.read())
            connection.close()
            assert response.status == expected
            if count is not None:
                assert receipt == {'status':'observed_partial', 'inserted':count}
        assert len(server.announcement_store.snapshots()) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_pdf_handoff_requires_catalog_and_persists_bytes(tmp_path):
    import base64
    import threading
    server = LocalObservationServer(0, pdf_directory=tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    data = b"%PDF-1.7\n" + b"x" * 40 + b"\n%%EOF"
    payload = {"course_id": "2", "download": {"status": "downloaded", "source_id": "1",
               "byte_count": len(data), "data_base64": base64.b64encode(data).decode()}}
    try:
        for expected in (400, 202):
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("POST", "/v1/browser-pdf", body=json.dumps(payload),
                               headers={"X-Chronos-Bridge": "1", "Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == expected
            receipt = json.loads(response.read())
            connection.close()
            if expected == 400:
                assert list(tmp_path.iterdir()) == []
                server.material_store.put({"status": "observed", "materials": [{
                    "source_id": "1", "course_id": "2", "activity_id": "3",
                    "filename": "lecture.pdf", "uploaded_at": None}]})
            else:
                assert receipt["status"] == "persisted"
                assert (tmp_path / f"{receipt['sha256']}.pdf").read_bytes() == data
    finally:
        server.shutdown()
        server.server_close()


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
