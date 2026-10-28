import math
import unittest

from so101_protocol import (
    CoordinateFrame,
    ExecutionRequest,
    PoseMM,
    RobotStep,
    RobotTask,
    build_pick_steps,
    build_place_steps,
    canonical_detection_name,
)


class SO101ProtocolTests(unittest.TestCase):
    def test_home_can_use_uncompensated_robot_frame(self) -> None:
        step = RobotStep(
            action="move", frame=CoordinateFrame.ROBOT, x=-78.7, y=6.7, z=474.7
        )
        self.assertEqual(step.x, -78.7)

    def test_stop_cannot_be_hidden_inside_a_task(self) -> None:
        with self.assertRaises(ValueError):
            RobotTask(
                task_id="unsafe",
                steps=[RobotStep(action="stop"), RobotStep(action="open")],
            )

    def test_unknown_and_extra_step_fields_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RobotStep.from_dict({"action": "open", "x": 10})

    def test_non_finite_coordinates_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PoseMM(x=math.nan, y=0, z=0)

    def test_cv_half_height_is_sent_as_exact_pick_and_place_z(self) -> None:
        pick_steps = build_pick_steps(PoseMM(x=201.4, y=-57.0, z=25.0), 150.0)
        place_steps = build_place_steps(PoseMM(x=110.0, y=185.8, z=38.0))
        self.assertEqual(pick_steps[1].z, 25.0)
        self.assertEqual(place_steps[0].action, "move_xy")
        self.assertEqual(place_steps[1].action, "move")
        self.assertEqual(place_steps[1].z, 38.0)

    def test_detection_name_matches_planner_inventory(self) -> None:
        self.assertEqual(canonical_detection_name("traffic light", "blue"), "blue_traffic_light")

    def test_high_level_wire_request_is_validated_without_pydantic(self) -> None:
        request = ExecutionRequest.from_dict(
            {
                "request_id": "task-1",
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
        self.assertEqual(request.plan.object, "blue_mouse")


if __name__ == "__main__":
    unittest.main()
