"""Server-side command orchestration after Whisper transcription."""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Protocol

import requests


class HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


class CommandPipeline:
    """Resolve scene inventory, create a plan, and optionally execute it."""

    def __init__(
        self,
        planner_url: str | None = None,
        executor_url: str | None = None,
        auto_execute: bool | None = None,
        duplicate_window_seconds: float | None = None,
        http: HttpClient = requests,
    ) -> None:
        self.planner_url = planner_url or os.getenv(
            "PLANNER_URL", "http://127.0.0.1:8010/plan"
        )
        self.executor_url = (executor_url or os.getenv(
            "EXECUTOR_URL", "http://127.0.0.1:8020"
        )).rstrip("/")
        self.auto_execute = (
            auto_execute
            if auto_execute is not None
            else os.getenv("AUTO_EXECUTE", "0").casefold() in {"1", "true", "yes"}
        )
        self.duplicate_window_seconds = (
            duplicate_window_seconds
            if duplicate_window_seconds is not None
            else float(os.getenv("DUPLICATE_WINDOW_SECONDS", "3"))
        )
        self.http = http
        self.lock = threading.Lock()
        self.last_command = ""
        self.last_command_at = 0.0

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(text.casefold().split())

    def process(self, command: str) -> dict[str, Any]:
        normalized = self.normalize(command)
        is_stop = normalized.startswith(("стоп", "останов", "stop"))
        now = time.monotonic()
        with self.lock:
            duplicate = (
                bool(normalized)
                and normalized == self.last_command
                and now - self.last_command_at < self.duplicate_window_seconds
                and not is_stop
            )
            if duplicate:
                return {
                    "pipeline_status": "duplicate",
                    "message": "Recent duplicate command was not planned or executed",
                }
            self.last_command = normalized
            self.last_command_at = now

        if is_stop:
            plan = {
                "behavior": "stop",
                "arguments": {"object": None, "destination": None, "coordinates": None},
                "requires_confirmation": False,
                "clarification_question": None,
            }
            return {
                "pipeline_status": "ready",
                "available_objects": [],
                "plan": plan,
                "planner_message": "Emergency stop bypassed inventory and planner",
                "reply_text": "Stopping now.",
                "execution": self.execute_plan(plan),
            }

        available_objects = self.fetch_available_objects()
        plan_response = self.create_plan(command, available_objects)
        result: dict[str, Any] = {
            "pipeline_status": plan_response.get("status", "error"),
            "available_objects": available_objects,
            "plan": plan_response.get("plan"),
            "planner_message": plan_response.get("message"),
            "reply_text": plan_response.get("reply_text"),
        }
        plan = plan_response.get("plan")
        if plan_response.get("status") != "ready" or not isinstance(plan, dict):
            return result

        result["preview"] = self.preview_plan(plan)

        if plan.get("behavior") == "stop" or self.auto_execute:
            result["execution"] = self.execute_plan(plan)
        else:
            result["execution"] = {
                "accepted": False,
                "state": "AUTO_EXECUTE_DISABLED",
                "message": "Plan validated on the server but execution is disabled",
            }
        return result

    def preview_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "request_id": f"preview-{uuid.uuid4()}",
            "plan": plan,
        }
        try:
            response = self.http.post(
                f"{self.executor_url}/preview", json=payload, timeout=5
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("executor returned an invalid preview JSON")
            return result
        except (requests.RequestException, ValueError) as error:
            return {
                "preview_only": True,
                "available": False,
                "error": str(error),
            }

    def fetch_available_objects(self) -> list[str]:
        try:
            response = self.http.get(f"{self.executor_url}/objects", timeout=2)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as error:
            raise RuntimeError(
                f"cannot fetch scene objects from {self.executor_url}/objects: {error}"
            ) from error
        objects = payload.get("objects", []) if isinstance(payload, dict) else []
        if not isinstance(objects, list) or not all(isinstance(item, str) for item in objects):
            raise ValueError("executor returned an invalid object inventory")
        return objects

    def create_plan(self, command: str, available_objects: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": command, "available_objects": available_objects}
        response = self.http.post(self.planner_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("planner returned invalid JSON")
        return result

    def execute_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        behavior = plan.get("behavior")
        endpoint = "/stop" if behavior == "stop" else "/execute"
        payload = {} if behavior == "stop" else {
            "request_id": str(uuid.uuid4()),
            "plan": plan,
        }
        response = self.http.post(
            f"{self.executor_url}{endpoint}", json=payload, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("executor returned invalid JSON")
        return result
