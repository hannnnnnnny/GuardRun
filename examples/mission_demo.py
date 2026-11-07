"""A whole mission, offline: the planner publishes tasks, a worker does them,
the inspector sends one back, and the second attempt passes.

The scripted provider stands in for the model so this runs without a key. With
a real provider the same flow is ``guardrun mission plan "GOAL" --run``.

    python examples/mission_demo.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from guardrun import AgentOptions
from guardrun.cli.mission import MissionRenderer
from guardrun.events import EventBus
from guardrun.messages import ModelResponse
from guardrun.mission import (
    CheckRunner,
    Inbox,
    MissionStore,
    Supervisor,
    agent_maker,
)
from guardrun.mission.report import mission_report
from guardrun.providers.scripted import ScriptedProvider, text_turn, tool_turn

PLAN = [
    {
        "id": "greeting",
        "title": "Write the greeting",
        "instructions": "Create hello.txt containing a friendly greeting.",
        "acceptance": ["hello.txt exists", "it greets the reader by name"],
        "checks": ["cat hello.txt"],
    },
    {
        "id": "farewell",
        "title": "Write the farewell",
        "instructions": "Create bye.txt.",
        "acceptance": ["bye.txt exists"],
        "depends_on": ["greeting"],
    },
]


def script() -> ScriptedProvider:
    def verdict(passed: bool, feedback: str) -> list[ModelResponse]:
        args = {"passed": passed, "feedback": feedback}
        return [tool_turn(("submit_verdict", args)), text_turn("done")]

    return ScriptedProvider(
        [
            tool_turn(("publish_tasks", {"tasks": PLAN})),
            text_turn("Published two tasks."),
            tool_turn(("write_file", {"path": "hello.txt", "content": "Hello!\n"})),
            text_turn("Wrote hello.txt."),
            *verdict(False, "The greeting does not name the reader. Address Ada."),
            tool_turn(("write_file", {"path": "hello.txt", "content": "Hello, Ada!\n"})),
            text_turn("hello.txt now greets Ada."),
            *verdict(True, "hello.txt greets Ada; check passed."),
            tool_turn(("write_file", {"path": "bye.txt", "content": "Goodbye, Ada.\n"})),
            text_turn("Wrote bye.txt."),
            *verdict(True, "bye.txt exists."),
        ]
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        events = EventBus()
        events.subscribe(MissionRenderer(sys.stdout))
        supervisor = Supervisor(
            agent_maker(root, script(), AgentOptions(audit=False), events),
            CheckRunner(root),
            MissionStore(root / ".guardrun" / "missions"),
            Inbox(root / ".guardrun" / "inbox.jsonl"),
            events=events,
        )
        mission = supervisor.run(supervisor.plan("Greet Ada and say goodbye"))
        print("\n" + mission_report(mission))
        print("[completed]" if mission.status == "done" else f"[{mission.status}]")
        return 0 if mission.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
