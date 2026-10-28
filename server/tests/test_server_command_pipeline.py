import unittest

import requests

from server_command_pipeline import CommandPipeline


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse({"objects": ["blue_mouse", "blue_traffic_light"]})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/plan"):
            return FakeResponse(
                {
                    "status": "ready",
                    "reply_text": "Okay, I will move the object.",
                    "plan": {
                        "behavior": "pick_and_place",
                        "arguments": {
                            "object": "blue_mouse",
                            "destination": "blue_traffic_light",
                            "coordinates": None,
                        },
                        "requires_confirmation": False,
                        "clarification_question": None,
                    },
                }
            )
        if url.endswith("/preview"):
            return FakeResponse(
                {
                    "preview_only": True,
                    "perception": {
                        "blue_mouse": {
                            "score": 0.95,
                            "frame": "camera",
                            "x_mm": 100.0,
                            "y_mm": 50.0,
                            "z_mm": 20.0,
                        }
                    },
                    "task": {
                        "task_id": "preview-test",
                        "steps": [{"action": "open"}],
                    },
                }
            )
        return FakeResponse({"accepted": True, "state": "SAMPLING", "message": "ok"})


class FailingInventoryHttp(FakeHttp):
    def get(self, url, **kwargs):
        raise requests.ConnectionError("client is unreachable")


class ServerCommandPipelineTests(unittest.TestCase):
    def test_inventory_planning_and_execution_are_all_server_side(self):
        http = FakeHttp()
        pipeline = CommandPipeline(
            planner_url="http://planner/plan",
            executor_url="http://executor",
            auto_execute=True,
            http=http,
        )
        result = pipeline.process("возьми синюю мышь и поставь на синий светофор")
        self.assertEqual(result["pipeline_status"], "ready")
        self.assertEqual(result["execution"]["state"], "SAMPLING")
        self.assertEqual(result["reply_text"], "Okay, I will move the object.")
        self.assertEqual(
            [call[0] for call in http.calls], ["GET", "POST", "POST", "POST"]
        )
        self.assertTrue(result["preview"]["preview_only"])
        planner_payload = http.calls[1][2]["json"]
        self.assertEqual(
            planner_payload["available_objects"], ["blue_mouse", "blue_traffic_light"]
        )

    def test_stop_bypasses_inventory_planner_and_auto_execute_gate(self):
        http = FakeHttp()
        pipeline = CommandPipeline(
            planner_url="http://planner/plan",
            executor_url="http://executor",
            auto_execute=False,
            http=http,
        )
        result = pipeline.process("стоп немедленно")
        self.assertEqual(result["execution"]["state"], "SAMPLING")
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0][1], "http://executor/stop")

    def test_recent_duplicate_is_not_planned_twice(self):
        http = FakeHttp()
        pipeline = CommandPipeline(auto_execute=False, http=http)
        pipeline.process("домой")
        call_count = len(http.calls)
        result = pipeline.process("  ДОМОЙ ")
        self.assertEqual(result["pipeline_status"], "duplicate")
        self.assertEqual(len(http.calls), call_count)

    def test_auto_execute_is_disabled_on_server(self):
        http = FakeHttp()
        pipeline = CommandPipeline(auto_execute=False, http=http)
        result = pipeline.process("возьми синюю мышь")
        self.assertEqual(result["execution"]["state"], "AUTO_EXECUTE_DISABLED")
        self.assertEqual([call[0] for call in http.calls], ["GET", "POST", "POST"])
        self.assertTrue(result["preview"]["preview_only"])

    def test_inventory_connection_failure_is_not_reported_as_empty_scene(self):
        pipeline = CommandPipeline(
            executor_url="http://robot-client:8020",
            http=FailingInventoryHttp(),
        )
        with self.assertRaisesRegex(RuntimeError, "cannot fetch scene objects"):
            pipeline.process("pick the blue mouse")


if __name__ == "__main__":
    unittest.main()
