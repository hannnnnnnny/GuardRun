import json
from pathlib import Path
from typing import Any

import pytest

from guardrun.cli.main import EXIT_INCOMPLETE, EXIT_OK, EXIT_USAGE, main
from guardrun.cli.mission import MAX_PLAN_FILE_BYTES, MissionRenderer, read_plan_file
from guardrun.events import Event
from guardrun.messages import ModelResponse
from guardrun.mission import Inbox, MissionStore, TaskStatus, mission_from_plan
from guardrun.providers.base import ModelRequest
from guardrun.providers.scripted import ScriptedProvider, text_turn, tool_turn
from tests.cli.test_main import Term
from tests.mission.conftest import task, verdict, work

PLAN_TOML = """
goal = "Write the weekly report"

[[tasks]]
id = "draft"
title = "Draft it"
instructions = "Write report.md"
acceptance = ["report.md exists"]

[[tasks]]
id = "polish"
title = "Polish it"
instructions = "Fix typos"
acceptance = ["no typos"]
depends_on = ["draft"]
"""


def run_cli(tmp_path: Path, *argv: str, turns: list[ModelResponse] | None = None) -> Any:
    term = Term()
    provider = ScriptedProvider(turns or [])
    code = main([*argv, "-w", str(tmp_path)], io=term.io, provider_factory=lambda config: provider)
    return code, term, provider


def store(tmp_path: Path) -> MissionStore:
    return MissionStore(tmp_path / ".guardrun" / "missions")


def test_plan_and_run_in_one_go(tmp_path: Path) -> None:
    turns = [
        tool_turn(("publish_tasks", {"tasks": [task("a", checks=["echo ok"])]})),
        text_turn("planned"),
        *work(),
        *verdict(True, "checked out.txt"),
    ]
    code, term, _ = run_cli(tmp_path, "mission", "plan", "Make out.txt", "--run", turns=turns)
    assert code == EXIT_OK, term.err
    assert "published 1 tasks" in term.out
    assert "$ echo ok" in term.out
    assert "== a: Task a (attempt 1)" in term.out
    assert "QA passed: checked out.txt" in term.out
    assert "status: done" in term.out
    assert (tmp_path / "out.txt").exists()


def test_plan_only_saves_the_mission(tmp_path: Path) -> None:
    turns = [tool_turn(("publish_tasks", {"tasks": [task("a")]})), text_turn("ok")]
    code, term, _ = run_cli(tmp_path, "mission", "plan", "goal", turns=turns)
    assert code == EXIT_OK
    (saved,) = store(tmp_path).list_missions()
    assert f"guardrun mission run {saved.id}" in term.out
    assert saved.tasks[0].status is TaskStatus.PENDING


def test_planner_failure_is_a_usage_error(tmp_path: Path) -> None:
    code, term, _ = run_cli(tmp_path, "mission", "plan", "goal", turns=[text_turn("no")])
    assert code == EXIT_USAGE
    assert "did not publish a plan" in term.err


def test_import_toml_then_run_with_retry(tmp_path: Path) -> None:
    (tmp_path / "week.toml").write_text(PLAN_TOML)
    code, term, _ = run_cli(tmp_path, "mission", "import", str(tmp_path / "week.toml"))
    assert code == EXIT_OK, term.err
    (saved,) = store(tmp_path).list_missions()
    assert [t.id for t in saved.tasks] == ["draft", "polish"]

    fail = [*work(), *verdict(False, "too short")]
    code, term, _ = run_cli(tmp_path, "mission", "run", saved.id, "--max-attempts", "1", turns=fail)
    assert code == EXIT_INCOMPLETE
    assert "[needs you]" in term.out
    assert "guardrun inbox" in term.out

    passing = [text_turn("longer now"), *verdict(True), *work("final.md"), *verdict(True)]
    code, term, _ = run_cli(tmp_path, "mission", "run", saved.id, "--retry", turns=passing)
    assert code == EXIT_OK, term.out
    assert "requeued 2 tasks" in term.out
    assert store(tmp_path).load(saved.id).status == "done"


def test_import_json(tmp_path: Path) -> None:
    plan = {"goal": "g", "tasks": [task("a")]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    code, _, _ = run_cli(tmp_path, "mission", "import", str(tmp_path / "plan.json"))
    assert code == EXIT_OK


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("bad.toml", "goal = ", "not valid toml"),
        ("bad.json", "{", "not valid json"),
        ("empty.toml", 'goal = "g"', "non-empty list"),
        ("big.toml", "#" * (MAX_PLAN_FILE_BYTES + 1), "larger than"),
    ],
)
def test_bad_plan_files(tmp_path: Path, name: str, content: str, message: str) -> None:
    (tmp_path / name).write_text(content)
    code, term, _ = run_cli(tmp_path, "mission", "import", str(tmp_path / name))
    assert code == EXIT_USAGE
    assert message in term.err


def test_missing_plan_file(tmp_path: Path) -> None:
    code, term, _ = run_cli(tmp_path, "mission", "import", str(tmp_path / "nope.toml"))
    assert code == EXIT_USAGE
    assert "cannot read" in term.err


def test_invalid_settings_are_reported(tmp_path: Path) -> None:
    code, term, _ = run_cli(tmp_path, "mission", "plan", "g", "--max-attempts", "0")
    assert code == EXIT_USAGE
    assert "invalid mission settings" in term.err


def test_status_lists_and_shows_missions(tmp_path: Path) -> None:
    code, term, _ = run_cli(tmp_path, "mission", "status")
    assert "no missions yet" in term.out
    (tmp_path / "week.toml").write_text(PLAN_TOML)
    run_cli(tmp_path, "mission", "import", str(tmp_path / "week.toml"))
    (saved,) = store(tmp_path).list_missions()
    code, term, _ = run_cli(tmp_path, "mission", "status")
    assert code == EXIT_OK
    assert saved.id in term.out
    assert "0/2 passed" in term.out
    code, term, _ = run_cli(tmp_path, "mission", "status", saved.id, "-v")
    assert "[ ] polish: Polish it" in term.out
    assert "after: draft" in term.out
    assert "- no typos" in term.out
    code, term, _ = run_cli(tmp_path, "mission", "status", "0123456789ab")
    assert code == EXIT_USAGE


def test_inbox(tmp_path: Path) -> None:
    code, term, _ = run_cli(tmp_path, "inbox")
    assert "nothing needs you" in term.out
    inbox = Inbox(tmp_path / ".guardrun" / "inbox.jsonl")
    inbox.post("abc", "t1", "blocker", "need a decision")
    inbox.post("abc", "", "warning", "budget")
    code, term, _ = run_cli(tmp_path, "inbox", "-n", "5")
    assert code == EXIT_OK
    assert "abc/t1" in term.out
    assert "need a decision" in term.out
    code, term, _ = run_cli(tmp_path, "inbox", "-n", "0")
    assert "nothing needs you" in term.out


class Interrupting(ScriptedProvider):
    def complete(self, request: ModelRequest) -> ModelResponse:
        raise KeyboardInterrupt


def test_interrupt_saves_and_explains_how_to_resume(tmp_path: Path) -> None:
    (tmp_path / "week.toml").write_text(PLAN_TOML)
    run_cli(tmp_path, "mission", "import", str(tmp_path / "week.toml"))
    (saved,) = store(tmp_path).list_missions()
    term = Term()
    provider = Interrupting([])
    code = main(
        ["mission", "run", saved.id, "-w", str(tmp_path)],
        io=term.io,
        provider_factory=lambda config: provider,
    )
    assert code == EXIT_INCOMPLETE
    assert f"resume with: guardrun mission run {saved.id}" in term.out


def test_verbose_renderer_shows_agent_tool_calls() -> None:
    term = Term()
    renderer = MissionRenderer(term.io.stdout, verbose=True)
    renderer(Event("tool_started", {"tool": "read_file", "input": {}, "role": "worker"}))
    renderer(Event("tool_started", {"tool": "ignored", "input": {}}))
    renderer(Event("inbox_posted", {"task": "t", "level": "warning", "reason": "hmm"}))
    assert "> read_file" in term.out
    assert "ignored" not in term.out
    assert "[warning] hmm" in term.out


def test_example_week_plan_is_valid() -> None:
    path = Path(__file__).resolve().parents[2] / "examples" / "weekly_mission.toml"
    planned = mission_from_plan(read_plan_file(path))
    assert planned.tasks[-1].id == "release-notes"


def test_import_needs_no_provider(tmp_path: Path) -> None:
    (tmp_path / "week.toml").write_text(PLAN_TOML)
    term = Term()

    def no_provider(config: object) -> ScriptedProvider:
        raise AssertionError("import without --run must not build a provider")

    code = main(
        ["mission", "import", str(tmp_path / "week.toml"), "-w", str(tmp_path)],
        io=term.io,
        provider_factory=no_provider,
    )
    assert code == EXIT_OK


def test_import_and_run(tmp_path: Path) -> None:
    plan = {"goal": "g", "tasks": [task("a")]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    turns = [*work(), *verdict(True)]
    code, term, _ = run_cli(
        tmp_path, "mission", "import", str(tmp_path / "plan.json"), "--run", turns=turns
    )
    assert code == EXIT_OK, term.out
    assert "status: done" in term.out
