"""Loopback-only receiver for explicit Chrome observation handoffs.

This process is intentionally separate from the deployable web application.
It accepts only redacted, visible-page observations from the extension popup.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Mapping
from urllib.parse import urlsplit
from pathlib import Path

from .chrome_bridge import BrowserBridgeError, BrowserObservation, parse_observation
from .material_bridge import MaterialObservationStore
from .assignment_bridge import AssignmentObservationStore
from .pdf_store import PdfStore, MAX_PDF_BYTES
from .native_pdf_import import import_native_pdf


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
        origin = self.headers.get("Origin")
        if origin and self._allowed_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
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
        if self.path not in {"/v1/browser-observation", "/v1/browser-materials", "/v1/browser-pdf", "/v1/browser-native-pdf", "/v1/browser-assignment"}:
            self._send_json(404, {"error": "not_found"})
            return
        if not self._allowed_origin() or self.headers.get("X-Chronos-Bridge") != "1":
            self._send_json(403, {"error": "bridge_permission_required"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
            limit = 4 * ((MAX_PDF_BYTES + 2) // 3) + 1024 if self.path == "/v1/browser-pdf" else MAX_BODY_BYTES
            if content_length < 0 or content_length > limit:
                raise BrowserBridgeError("request body exceeds limit")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise BrowserBridgeError("observation must be an object")
            if self.path == "/v1/browser-assignment":
                receipt = self.server.assignment_store.put(payload, datetime.now(timezone.utc))
                self._send_json(202, receipt)
                return
            if self.path == "/v1/browser-native-pdf":
                if not isinstance(payload.get("course_id"), str):
                    raise ValueError("invalid course")
                receipt = import_native_pdf(payload, self.server.native_download_root,
                    self.server.pdf_store, self.server.material_store.course_materials(payload["course_id"]))
                self._send_json(202, receipt)
                return
            if self.path == "/v1/browser-pdf":
                if set(payload) != {"course_id", "download"} or not isinstance(payload["course_id"], str) or not isinstance(payload["download"], dict):
                    raise ValueError("invalid PDF handoff")
                receipt = self.server.pdf_store.accept(payload["download"], self.server.material_store.course_materials(payload["course_id"]))
                self._send_json(202, receipt)
                return
            elif self.path == "/v1/browser-materials":
                self.server.material_store.put(payload)
            else:
                observation = _validate_payload(payload)
                self.server.observation_store.put(observation)  # type: ignore[attr-defined]
        except (BrowserBridgeError, ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_observation"})
            return
        except OSError:
            self._send_json(503, {"error": "local_storage_unavailable"})
            return
        self._send_json(202, {"status": "accepted"})

    def _allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == self.server.extension_origin


class LocalObservationServer(ThreadingHTTPServer):
    """A loopback-only HTTP server for explicit popup handoffs."""

    def __init__(self, port: int = 8765, pdf_directory: Path | None = None, catalog_path: Path | None = None,
                 extension_id: str | None = None, native_download_root: Path | None = None) -> None:
        if extension_id is not None and not re.fullmatch(r"[a-p]{32}", extension_id):
            raise ValueError("invalid Chrome extension ID")
        self.extension_origin = f"chrome-extension://{extension_id}" if extension_id else None
        self.native_download_root = native_download_root
        super().__init__(("127.0.0.1", port), _ObservationHandler)
        self.observation_store = ObservationStore()
        self.material_store = MaterialObservationStore(catalog_path)
        self.assignment_store = AssignmentObservationStore(
            catalog_path.with_name("assignments.sqlite3") if catalog_path else Path(".study-data/assignments.sqlite3"))
        self.pdf_store = PdfStore(pdf_directory or Path(".study-data/pdfs"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Chronos observation receiver")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--catalog-path", type=Path, default=Path(".study-data/catalog.sqlite3"))
    parser.add_argument("--extension-id", required=True, help="Exact installed Chronos extension ID, not a secret")
    parser.add_argument("--native-download-root", type=Path, help="Only the Chronos subdirectory of Chrome Downloads")
    args = parser.parse_args()
    server = LocalObservationServer(args.port, catalog_path=args.catalog_path, extension_id=args.extension_id,
        native_download_root=args.native_download_root)
    print(f"Chronos local observation receiver listening on 127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
