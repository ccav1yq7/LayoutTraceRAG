"""Model gateway protocols and explicit offline fakes."""

from collections import deque
from typing import Protocol

from pydantic import TypeAdapter

from ..schemas import ID, Action, Contract, Text


class Observation(Contract):
    asset_id: ID
    description: Text
    model_mode: str = "fake"


class PlannerGateway(Protocol):
    model_mode: str

    def decide(self, public_state: dict) -> Action: ...


class VisionGateway(Protocol):
    model_mode: str

    def observe(
        self, asset_id: str, image_bytes: bytes, question: str
    ) -> Observation: ...


class ScriptedPolicy:
    model_mode = "fake"

    def __init__(self, actions: list[Action]):
        self._actions: deque[Action] = deque(
            TypeAdapter(Action).validate_python(a) for a in actions
        )

    def decide(self, public_state: dict) -> Action:
        if not self._actions:
            raise RuntimeError("script exhausted")
        return self._actions.popleft()


class FakeVisionObserver:
    model_mode = "fake"

    def __init__(self, descriptions: dict[str, str]):
        self._descriptions = dict(descriptions)

    def observe(self, asset_id: str, image_bytes: bytes, question: str) -> Observation:
        if not image_bytes or not question.strip():
            raise ValueError("image and question required")
        return Observation(asset_id=asset_id, description=self._descriptions[asset_id])
