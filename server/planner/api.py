from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer

from pydantic import ValidationError

from .http_utils import JsonHandler
from .models import PlanRequest
from .service import TaskPlanner


class PlannerHandler(JsonHandler):
    planner = TaskPlanner.create_default()

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "llm_configured": self.planner.llm.configured,
                    "mode": "llm" if self.planner.llm.configured else "deterministic-fallback",
                },
            )
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/plan":
            self.send_json(404, {"error": "not found"})
            return
        try:
            request = PlanRequest.model_validate(self.read_json())
            response = self.planner.plan(request)
            self.send_json(200, response.model_dump(mode="json"))
        except (ValidationError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            self.send_json(500, {"error": f"planner failure: {error}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe robot task planner API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), PlannerHandler)
    print(f"Planner API: http://{args.host}:{args.port}")
    print("Routes: GET /health, POST /plan")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPlanner stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
