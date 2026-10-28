from __future__ import annotations

import argparse
import os
import threading
import time
from http.server import ThreadingHTTPServer

from pydantic import ValidationError

from .http_utils import JsonHandler
from .models import BehaviorName, ExecutionRequest, ExecutionResponse


class DryRunExecutor:
    """Safe executable boundary; replace this backend with the ROS 2 action client."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_request_id: str | None = None

    def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        if request.plan.requires_confirmation:
            return ExecutionResponse(
                request_id=request.request_id,
                accepted=False,
                state="REJECTED",
                message="Plan still requires confirmation",
            )
        if request.plan.behavior is BehaviorName.STOP:
            return self.stop(request.request_id)
        with self.lock:
            if self.active_request_id is not None:
                return ExecutionResponse(
                    request_id=request.request_id,
                    accepted=False,
                    state="BUSY",
                    message=f"Request {self.active_request_id} is active",
                )
            self.active_request_id = request.request_id
        print(f"[DRY RUN] {request.request_id}: {request.plan.model_dump_json()}", flush=True)
        # This service validates orchestration only. A ROS action server owns real motion.
        time.sleep(0.01)
        with self.lock:
            self.active_request_id = None
        return ExecutionResponse(
            request_id=request.request_id,
            accepted=True,
            state="DRY_RUN_COMPLETED",
            message="Validated plan reached the executor boundary; no robot motion was commanded",
        )

    def stop(self, request_id: str = "stop") -> ExecutionResponse:
        with self.lock:
            cancelled = self.active_request_id
            self.active_request_id = None
        return ExecutionResponse(
            request_id=request_id,
            accepted=True,
            state="STOPPED",
            message=f"Cancelled {cancelled}" if cancelled else "No active task; safe stop recorded",
        )


class ExecutorHandler(JsonHandler):
    executor = DryRunExecutor()
    available_objects = [
        item.strip() for item in os.getenv("AVAILABLE_OBJECTS", "").split(",") if item.strip()
    ]

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "backend": "dry-run"})
            return
        if self.path == "/objects":
            self.send_json(200, {"objects": self.available_objects})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            if self.path == "/stop":
                response = self.executor.stop()
            elif self.path == "/execute":
                response = self.executor.execute(ExecutionRequest.model_validate(self.read_json()))
            else:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, response.model_dump(mode="json"))
        except (ValidationError, ValueError) as error:
            self.send_json(400, {"error": str(error)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run robot task executor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ExecutorHandler)
    print(f"Dry-run executor: http://{args.host}:{args.port}")
    print("Routes: GET /health, POST /execute, POST /stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExecutor stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
