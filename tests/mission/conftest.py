from pathlib import Path
from typing import Any

import pytest

from guardrun.events import EventBus
from guardrun.factory import AgentOptions
from guardrun.messages import ModelResponse
from guardrun.mission import (
    CheckRunner,
    Inbox,
    Mission,
    MissionSettings,
    MissionStore,
    Supervisor,
    agent_maker,
    mission_from_plan,
)
from guardrun.providers.scripted import ScriptedProvider, text_turn, tool_turn


def task(task_id: str = "t1", **extra: Any) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "instructions": f"Do {task_id}.",
        "acceptance": [f"{task_id} is done"],
        **extra,
    }


def mission(*tasks: dict[str, Any]) -> Mission:
    return mission_from_plan({"goal": "Ship it", "tasks": list(tasks or [task()])})


def verdict(passed: bool, feedback: str = "looks good", **extra: Any) -> list[ModelResponse]:
    """An inspector that submits one verdict and then says so."""
    args = {"passed": passed, "feedback": feedback, **extra}
    return [tool_turn(("submit_verdict", args)), text_turn("verdict submitted")]


def work(path: str = "out.txt", content: str = "done") -> list[ModelResponse]:
    """A worker that writes one file and reports."""
    return [
        tool_turn(("write_file", {"path": path, "content": content})),
        text_turn(f"wrote {path}"),
    ]


class Harness:
    def __init__(self, root: Path, turns: list[ModelResponse], **settings: Any) -> None:
        self.root = root
        self.provider = ScriptedProvider(turns)
        self.events = EventBus()
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.events.subscribe(lambda e: self.seen.append((e.kind, e.data)))
        self.store = MissionStore(root / ".guardrun" / "missions")
        self.inbox = Inbox(root / ".guardrun" / "inbox.jsonl")
        options = AgentOptions(audit=False)
        self.supervisor = Supervisor(
            agent_maker(root, self.provider, options, self.events),
            CheckRunner(root),
            self.store,
            self.inbox,
            settings=MissionSettings(**settings),
            events=self.events,
        )

    def kinds(self, kind: str) -> list[dict[str, Any]]:
        return [data for k, data in self.seen if k == kind]

    def prompt(self, index: int) -> str:
        """The first user text of the ``index``-th model request's latest user turn."""
        message = self.provider.requests[index].messages[-1]
        return str(message["content"][0].get("text") or message["content"][0].get("content"))


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    def make(*turns: ModelResponse | list[ModelResponse], **settings: Any) -> Harness:
        flat: list[ModelResponse] = []
        for turn in turns:
            flat.extend(turn if isinstance(turn, list) else [turn])
        return Harness(tmp_path, flat, **settings)

    return make
