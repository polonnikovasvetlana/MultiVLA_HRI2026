from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any


MAX_JSON_BYTES = 64 * 1024


class JsonHandler(BaseHTTPRequestHandler):
    def read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if length <= 0 or length > MAX_JSON_BYTES:
            raise ValueError("JSON body is empty or too large")
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON: {error}") from error

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return
