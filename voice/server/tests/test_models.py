import unittest

from pydantic import ValidationError

from planner.models import BehaviorName, Coordinates, TaskArguments, TaskPlan


class TaskPlanModelTests(unittest.TestCase):
    def test_pick_requires_object(self) -> None:
        with self.assertRaises(ValidationError):
            TaskPlan(behavior=BehaviorName.PICK)

    def test_move_requires_coordinates(self) -> None:
        with self.assertRaises(ValidationError):
            TaskPlan(behavior=BehaviorName.MOVE_TO)

    def test_extra_fields_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TaskPlan.model_validate(
                {"behavior": "home", "arguments": {}, "shell_command": "rm -rf /tmp/x"}
            )

    def test_valid_move(self) -> None:
        plan = TaskPlan(
            behavior=BehaviorName.MOVE_TO,
            arguments=TaskArguments(
                coordinates=Coordinates(frame_id="base_link", x=0.2, y=0.1, z=0.15)
            ),
        )
        self.assertEqual(plan.arguments.coordinates.z, 0.15)


if __name__ == "__main__":
    unittest.main()
