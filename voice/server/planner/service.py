from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from .database import PlannerRepository, normalize_name
from .llm_client import LLMError, OpenAICompatibleClient
from .models import BehaviorName, Coordinates, PlanRequest, PlanResponse, TaskArguments, TaskPlan


STOP_PATTERN = re.compile(r"^(?:стоп|остановись|останови|stop|emergency stop)\b", re.IGNORECASE)
HOME_PATTERN = re.compile(r"\b(?:домой|исходн\w*|home)\b", re.IGNORECASE)
PICK_PATTERN = re.compile(
    r"\b(?:возьми|взять|подними|pick(?:\s+up)?|grab|take|get|give\s+me)\b",
    re.IGNORECASE,
)
PLACE_PATTERN = re.compile(r"\b(?:поставь|положи|помести|place|put)\b", re.IGNORECASE)
MOVE_PATTERN = re.compile(r"\b(?:перемести|двигай|точк\w*|move)\b", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


SYSTEM_PROMPT = """You are a task planner for a robotic arm.
Select exactly one behavior from ALLOWED_BEHAVIORS and return only a JSON object.
Never generate trajectories, joint positions, code, shell commands, or new behavior names.
Never invent objects, destinations, coordinate units, or reference frames.
If the command is ambiguous or unsafe, set requires_confirmation=true and provide clarification_question.

JSON schema:
{
  "behavior": "pick|place|pick_and_place|move_to|home|stop",
  "arguments": {
    "object": "canonical name or null",
    "destination": "canonical name or null",
    "coordinates": {"frame_id": "name", "x": 0.0, "y": 0.0, "z": 0.0} or null
  },
  "requires_confirmation": false,
  "clarification_question": null
}
"""


@dataclass
class TaskPlanner:
    repository: PlannerRepository
    llm: OpenAICompatibleClient

    @classmethod
    def create_default(cls) -> "TaskPlanner":
        return cls(PlannerRepository(), OpenAICompatibleClient())

    def plan(self, request: PlanRequest) -> PlanResponse:
        text = request.text.strip()
        if STOP_PATTERN.search(normalize_name(text)):
            return PlanResponse(
                status="ready",
                plan=TaskPlan(behavior=BehaviorName.STOP, arguments=TaskArguments()),
                message="Emergency stop recognized without calling the LLM.",
                reply_text="Stopping now.",
            )

        try:
            if self.llm.configured:
                try:
                    plan = self._plan_with_llm(request)
                except (LLMError, ValidationError, ValueError):
                    # A local model may time out, spend its output budget on
                    # reasoning, or return malformed JSON.  Keep the robot
                    # command path usable through the constrained parser; all
                    # normal scene and database checks still run below.
                    plan = self._fallback_plan(request)
            else:
                plan = self._fallback_plan(request)
            plan = self._canonicalize(plan)
            plan = self._require_scene_confirmation(plan, request.available_objects)
            self._validate_against_database(plan, request.available_objects)
        except (LLMError, ValidationError, ValueError) as error:
            return PlanResponse(
                status="rejected",
                message=str(error),
                reply_text="I could not safely execute that command.",
            )

        status = "confirmation_required" if plan.requires_confirmation else "ready"
        return PlanResponse(status=status, plan=plan, reply_text=_reply_text(plan))

    def _plan_with_llm(self, request: PlanRequest) -> TaskPlan:
        skills = self.repository.enabled_skills()
        entities = self.repository.matching_entities(
            request.text, request.available_objects
        )
        prompt = (
            f"ALLOWED_BEHAVIORS:\n{json.dumps(skills, ensure_ascii=False)}\n"
            f"RELEVANT_RULES:\n{json.dumps(self.repository.rules(), ensure_ascii=False)}\n"
            f"KNOWN_ALIASES:\n{json.dumps(self.repository.aliases(), ensure_ascii=False)}\n"
            f"AVAILABLE_OBJECTS:\n{json.dumps(request.available_objects, ensure_ascii=False)}\n"
            f"ENTITIES_FOUND_IN_COMMAND:\n{json.dumps(entities, ensure_ascii=False)}\n"
            f"USER_COMMAND:\n{request.text}"
        )
        raw = self.llm.complete(SYSTEM_PROMPT, prompt)
        try:
            return TaskPlan.model_validate_json(_extract_json(raw))
        except (ValidationError, ValueError) as first_error:
            repair_prompt = (
                f"Your previous response was invalid: {first_error}. "
                f"Return one corrected JSON object only. Previous response: {raw}"
            )
            repaired = self.llm.complete(SYSTEM_PROMPT, repair_prompt)
            return TaskPlan.model_validate_json(_extract_json(repaired))

    def _fallback_plan(self, request: PlanRequest) -> TaskPlan:
        text = normalize_name(request.text)
        entities = self.repository.matching_entities(text, request.available_objects)
        if HOME_PATTERN.search(text):
            return TaskPlan(behavior=BehaviorName.HOME)

        if MOVE_PATTERN.search(text):
            numbers = [float(value.replace(",", ".")) for value in NUMBER_PATTERN.findall(text)]
            has_centimeters = bool(re.search(r"\b(?:см|сантиметр\w*|centimeter\w*|cm)\b", text))
            has_meters = bool(re.search(r"\b(?:м|метр\w*|meter\w*)\b", text))
            has_frame = "base_link" in text or bool(re.search(r"\bбаз\w*\b", text))
            if len(numbers) < 3 or not (has_centimeters or has_meters) or not has_frame:
                return TaskPlan(
                    behavior=BehaviorName.MOVE_TO,
                    arguments=TaskArguments(
                        coordinates=Coordinates(frame_id="base_link", x=0, y=0, z=0)
                    ),
                    requires_confirmation=True,
                    clarification_question=(
                        "Назовите x, y, z, единицы измерения и систему координат, например base_link."
                    ),
                )
            scale = 0.01 if has_centimeters else 1.0
            return TaskPlan(
                behavior=BehaviorName.MOVE_TO,
                arguments=TaskArguments(
                    coordinates=Coordinates(
                        frame_id="base_link",
                        x=numbers[0] * scale,
                        y=numbers[1] * scale,
                        z=numbers[2] * scale,
                    )
                ),
            )

        wants_pick = bool(PICK_PATTERN.search(text))
        wants_place = bool(PLACE_PATTERN.search(text))
        if wants_pick and wants_place:
            if len(entities) < 2:
                return _clarification("pick_and_place", "Уточните объект и место назначения.")
            return TaskPlan(
                behavior=BehaviorName.PICK_AND_PLACE,
                arguments=TaskArguments(object=entities[0], destination=entities[1]),
            )
        if wants_pick:
            if not entities:
                return _clarification("pick", "Какой объект нужно взять?")
            return TaskPlan(behavior=BehaviorName.PICK, arguments=TaskArguments(object=entities[0]))
        if wants_place:
            if not entities:
                return _clarification("place", "Куда нужно поместить удерживаемый объект?")
            if len(entities) >= 2:
                return TaskPlan(
                    behavior=BehaviorName.PICK_AND_PLACE,
                    arguments=TaskArguments(object=entities[0], destination=entities[-1]),
                )
            return TaskPlan(
                behavior=BehaviorName.PLACE,
                arguments=TaskArguments(destination=entities[-1]),
            )
        raise ValueError("Команда не соответствует ни одному разрешённому навыку")

    def _canonicalize(self, plan: TaskPlan) -> TaskPlan:
        data = plan.model_dump()
        args = data["arguments"]
        if args["object"]:
            args["object"] = self.repository.canonical_name(args["object"])
        if args["destination"]:
            args["destination"] = self.repository.canonical_name(args["destination"])
        return TaskPlan.model_validate(data)

    def _require_scene_confirmation(
        self, plan: TaskPlan, available_objects: list[str] | None
    ) -> TaskPlan:
        uses_scene = plan.behavior in {
            BehaviorName.PICK,
            BehaviorName.PLACE,
            BehaviorName.PICK_AND_PLACE,
        }
        if not uses_scene or available_objects is not None or plan.requires_confirmation:
            return plan
        data = plan.model_dump()
        data["requires_confirmation"] = True
        data["clarification_question"] = (
            "Нет актуального списка объектов perception; подтвердить безопасное исполнение нельзя."
        )
        return TaskPlan.model_validate(data)

    def _validate_against_database(
        self, plan: TaskPlan, available_objects: list[str] | None
    ) -> None:
        if not self.repository.skill_enabled(plan.behavior.value):
            raise ValueError(f"Skill is disabled: {plan.behavior.value}")

        coordinates = plan.arguments.coordinates
        if coordinates:
            limits = self.repository.workspace(coordinates.frame_id)
            if limits is None:
                raise ValueError(f"Unknown coordinate frame: {coordinates.frame_id}")
            if not (
                limits["min_x"] <= coordinates.x <= limits["max_x"]
                and limits["min_y"] <= coordinates.y <= limits["max_y"]
                and limits["min_z"] <= coordinates.z <= limits["max_z"]
            ):
                raise ValueError("Target coordinates are outside the configured workspace")

        if available_objects is not None and not plan.requires_confirmation:
            available = {self.repository.canonical_name(item) for item in available_objects}
            requested = [plan.arguments.object, plan.arguments.destination]
            missing = [item for item in requested if item and item not in available]
            if missing:
                raise ValueError(f"Objects are not present in the current scene: {', '.join(missing)}")


def _clarification(behavior: str, question: str) -> TaskPlan:
    placeholder = "unknown"
    args = TaskArguments(
        object=placeholder if behavior in {"pick", "pick_and_place"} else None,
        destination=placeholder if behavior in {"place", "pick_and_place"} else None,
    )
    return TaskPlan(
        behavior=BehaviorName(behavior),
        arguments=args,
        requires_confirmation=True,
        clarification_question=question,
    )


def _spoken_name(value: str | None) -> str:
    return (value or "the object").replace("_", " ")


def _reply_text(plan: TaskPlan) -> str:
    if plan.requires_confirmation:
        questions = {
            BehaviorName.PICK: "Which object should I pick up?",
            BehaviorName.PLACE: "Where should I place the object?",
            BehaviorName.PICK_AND_PLACE: "Which object and destination should I use?",
            BehaviorName.MOVE_TO: "Please specify the complete target position.",
        }
        return questions.get(plan.behavior, "Please clarify the command.")

    args = plan.arguments
    replies = {
        BehaviorName.PICK: f"Okay, I will pick up {_spoken_name(args.object)}.",
        BehaviorName.PLACE: (
            f"Okay, I will place the object in {_spoken_name(args.destination)}."
        ),
        BehaviorName.PICK_AND_PLACE: (
            f"Okay, I will put {_spoken_name(args.object)} in "
            f"{_spoken_name(args.destination)}."
        ),
        BehaviorName.MOVE_TO: "Okay, I will move to the requested position.",
        BehaviorName.HOME: "Okay, I will return home.",
        BehaviorName.STOP: "Stopping now.",
    }
    return replies[plan.behavior]


def _extract_json(raw: str) -> str:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM response does not contain a JSON object")
    return value[start : end + 1]
