from __future__ import annotations

import argparse
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from pydantic import ValidationError

from .http_utils import JsonHandler
from .models import BehaviorName, ExecutionRequest

try:
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from voice_task_executor.action import ExecuteTask
except ImportError as error:
    rclpy = None
    Node = object  # type: ignore[assignment,misc]
    ROS_IMPORT_ERROR = error
else:
    ROS_IMPORT_ERROR = None


class RosActionBridge(Node):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__("voice_task_http_bridge")
        self.client = ActionClient(self, ExecuteTask, "execute_task")

    def execute(self, request: ExecutionRequest, timeout: float = 300.0) -> dict[str, Any]:
        if request.plan.requires_confirmation:
            raise ValueError("Plan still requires confirmation")
        if not self.client.wait_for_server(timeout_sec=5.0):
            raise RuntimeError("ROS execute_task action server is unavailable")

        args = request.plan.arguments
        coordinates = args.coordinates
        goal = ExecuteTask.Goal()
        goal.request_id = request.request_id
        goal.behavior = request.plan.behavior.value
        goal.object = args.object or ""
        goal.destination = args.destination or ""
        goal.frame_id = coordinates.frame_id if coordinates else ""
        goal.has_coordinates = coordinates is not None
        goal.x = coordinates.x if coordinates else 0.0
        goal.y = coordinates.y if coordinates else 0.0
        goal.z = coordinates.z if coordinates else 0.0

        finished = threading.Event()
        response: dict[str, Any] = {}

        def result_done(future: Any) -> None:
            wrapped = future.result()
            result = wrapped.result
            response.update(
                accepted=True,
                state="COMPLETED" if result.success else "FAILED",
                message=result.message,
                error_code=result.error_code,
            )
            finished.set()

        def goal_done(future: Any) -> None:
            handle = future.result()
            if not handle.accepted:
                response.update(accepted=False, state="REJECTED", message="ROS action rejected")
                finished.set()
                return
            handle.get_result_async().add_done_callback(result_done)

        self.client.send_goal_async(goal).add_done_callback(goal_done)
        if not finished.wait(timeout):
            raise TimeoutError("Timed out waiting for the ROS action result")
        return {"request_id": request.request_id, **response}


class RosBridgeHandler(JsonHandler):
    bridge: RosActionBridge

    def do_GET(self) -> None:
        if self.path == "/health":
            ready = self.bridge.client.server_is_ready()
            self.send_json(200 if ready else 503, {"status": "ok" if ready else "unavailable"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            if self.path == "/execute":
                request = ExecutionRequest.model_validate(self.read_json())
            elif self.path == "/stop":
                from .models import TaskPlan

                request = ExecutionRequest(
                    request_id="stop", plan=TaskPlan(behavior=BehaviorName.STOP)
                )
            else:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, self.bridge.execute(request))
        except (ValidationError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            self.send_json(503, {"error": str(error)})


def main() -> None:
    if rclpy is None:
        raise SystemExit(f"ROS 2 Python packages are unavailable: {ROS_IMPORT_ERROR}")
    parser = argparse.ArgumentParser(description="HTTP to ROS 2 ExecuteTask action bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()

    rclpy.init()
    bridge = RosActionBridge()
    RosBridgeHandler.bridge = bridge
    spin_thread = threading.Thread(target=rclpy.spin, args=(bridge,), daemon=True)
    spin_thread.start()
    server = ThreadingHTTPServer((args.host, args.port), RosBridgeHandler)
    print(f"ROS bridge: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        bridge.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
