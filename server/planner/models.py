from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BehaviorName(str, Enum):
    PICK = "pick"
    PLACE = "place"
    PICK_AND_PLACE = "pick_and_place"
    MOVE_TO = "move_to"
    HOME = "home"
    STOP = "stop"


class Coordinates(StrictModel):
    frame_id: str = Field(min_length=1, max_length=100)
    x: float
    y: float
    z: float


class TaskArguments(StrictModel):
    object: str | None = Field(default=None, max_length=100)
    destination: str | None = Field(default=None, max_length=100)
    coordinates: Coordinates | None = None


class TaskPlan(StrictModel):
    behavior: BehaviorName
    arguments: TaskArguments = Field(default_factory=TaskArguments)
    requires_confirmation: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_behavior_arguments(self) -> "TaskPlan":
        args = self.arguments
        if self.behavior in {BehaviorName.PICK, BehaviorName.PICK_AND_PLACE} and not args.object:
            raise ValueError("object is required for this behavior")
        if self.behavior in {BehaviorName.PLACE, BehaviorName.PICK_AND_PLACE} and not args.destination:
            raise ValueError("destination is required for this behavior")
        if self.behavior is BehaviorName.MOVE_TO and args.coordinates is None:
            raise ValueError("coordinates are required for move_to")
        if self.requires_confirmation and not self.clarification_question:
            raise ValueError("clarification_question is required when confirmation is needed")
        return self


class PlanRequest(StrictModel):
    text: str = Field(min_length=2, max_length=500)
    available_objects: list[str] | None = None


class PlanResponse(StrictModel):
    status: str
    plan: TaskPlan | None = None
    message: str | None = None
    reply_text: str | None = Field(default=None, max_length=500)


class ExecutionRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=100)
    plan: TaskPlan


class ExecutionResponse(StrictModel):
    request_id: str
    accepted: bool
    state: str
    message: str
