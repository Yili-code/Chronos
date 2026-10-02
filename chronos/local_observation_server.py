"""Loopback-only receiver for explicit Chrome observation handoffs.

This process is intentionally separate from the deployable web application.
It accepts only redacted, visible-page observations from the extension popup.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Mapping
from urllib.parse import urlsplit

from .chrome_bridge import BrowserBridgeError, BrowserObservation, parse_observation
from .material_bridge import MaterialObservationStore


MAX_BODY_BYTES = 256_000
MAX_VISIBLE_TEXT = 10_000
ALLOWED_HOSTS = frozenset({"tronclass.ntou.edu.tw", "tccas.ntou.edu.tw"})


class ObservationStore:
    """In-memory store for the latest accepted observation."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest: BrowserObservation | None = None

    def put(self, observation: BrowserObservation) -> None:
        with self._lock:
            self._latest = observation

    def latest(self) -> BrowserObservation | None:
        with self._lock:
            return self._latest


def _validate_payload(payload: Mapping[str, object]) -> BrowserObservation:
    observation = parse_observation(payload)
    hostname = (urlsplit(observation.url).hostname or "").lower()
    if hostname not in ALLOWED_HOSTS:
        raise BrowserBridgeError("unsupported observation host")
    if len(observation.visible_text) > MAX_VISIBLE_TEXT:
        raise BrowserBridgeError("visible text exceeds limit")
    return observation


class _ObservationHandler(BaseHTTPRequestHandler):
    server_version = "ChronosLocalObservation/0.1"

    def log_message(self, _format: str, *_args: object) -> None:
        # Never log URLs, page text, or request metadata.
        return

    def _send_json(self, status: int, body: Mapping[str, object]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._allowed_origin():
            self._send_json(403, {"error": "origin_not_allowed"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Chronos-Bridge")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/v1/browser-observation", "/v1/browser-materials"}:
            self._send_json(404, {"error": "not_found"})
            return
        if not self._allowed_origin() or self.headers.get("X-Chronos-Bridge") != "1":
            self._send_json(403, {"error": "bridge_permission_required"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
            if content_length < 0 or content_length > MAX_BODY_BYTES:
                raise BrowserBridgeError("request body exceeds limit")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise BrowserBridgeError("observation must be an object")
            if self.path == "/v1/browser-materials":
                self.server.material_store.put(payload)
            else:
                observation = _validate_payload(payload)
                self.server.observation_store.put(observation)  # type: ignore[attr-defined]
        except (BrowserBridgeError, ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_observation"})
            return
        self._send_json(202, {"status": "accepted"})

    def _allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin.startswith("chrome-extension://")


class LocalObservationServer(ThreadingHTTPServer):
    """A loopback-only HTTP server for explicit popup handoffs."""

    def __init__(self, port: int = 8765) -> None:
        super().__init__(("127.0.0.1", port), _ObservationHandler)
        self.observation_store = ObservationStore()
        self.material_store = MaterialObservationStore()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Chronos observation receiver")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = LocalObservationServer(args.port)
    print(f"Chronos local observation receiver listening on 127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
