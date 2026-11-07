from typing import Any

import pytest

from guardrun.mission.plan import (
    MAX_LIST_ITEMS,
    Mission,
    PlanError,
    Task,
    TaskStatus,
    mission_from_plan,
    tasks_from_list,
    topological_order,
)
from tests.mission.conftest import task


def test_valid_plan_is_parsed_and_trimmed() -> None:
    raw = task("a", title="  A  ", checks=["pytest -q"], depends_on=[])
    (parsed,) = tasks_from_list([raw])
    assert parsed.title == "A"
    assert parsed.checks == ["pytest -q"]
    assert parsed.status is TaskStatus.PENDING


def test_dependencies_are_ordered_first() -> None:
    tasks = tasks_from_list([task("b", depends_on=["a"]), task("a"), task("c")])
    assert [t.id for t in tasks] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "non-empty list"),
        ("nope", "non-empty list"),
        (["x"], "must be an object"),
        ([{**task(), "extra": 1}], "unknown keys extra"),
        ([{k: v for k, v in task().items() if k != "title"}], "missing 'title'"),
        ([task(title="  ")], "non-empty string"),
        ([task(title=5)], "non-empty string"),
        ([task(title="x" * 201)], "longer than 200"),
        ([task("Bad_ID")], "lowercase"),
        ([task(acceptance="one")], "list of non-empty strings"),
        ([task(acceptance=[""])], "list of non-empty strings"),
        ([task(checks=["x"] * (MAX_LIST_ITEMS + 1))], "more than"),
        ([task(checks=["x" * 1001])], "longer than 1000"),
        ([task("a"), task("a")], "duplicate task ids: a"),
        ([task("a", depends_on=["zz"])], "unknown tasks: zz"),
        ([task("a", depends_on=["b"]), task("b", depends_on=["a"])], "cycle"),
        ([task("a", depends_on=["a"])], "cycle"),
    ],
)
def test_invalid_plans_are_rejected(raw: Any, message: str) -> None:
    with pytest.raises(PlanError, match=message):
        tasks_from_list(raw)


def test_task_limit() -> None:
    with pytest.raises(PlanError, match="at most 1 tasks"):
        tasks_from_list([task("a"), task("b")], max_tasks=1)


def test_topological_order_reports_cycles() -> None:
    a = Task("a", "A", "x", depends_on=["b"])
    b = Task("b", "B", "x", depends_on=["a"])
    assert topological_order([a, b]) is None


def test_mission_from_plan() -> None:
    mission = mission_from_plan({"goal": "Build it", "tasks": [task()]})
    assert mission.goal == "Build it"
    assert len(mission.id) == 12
    assert mission.task("t1").title == "Task t1"
    with pytest.raises(KeyError):
        mission.task("missing")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be an object"),
        ({"goal": "g", "tasks": [task()], "when": 1}, "unknown mission keys: when"),
        ({"tasks": [task()]}, "missing 'goal'"),
    ],
)
def test_invalid_missions(data: Any, message: str) -> None:
    with pytest.raises(PlanError, match=message):
        mission_from_plan(data)


def _with(*statuses: TaskStatus) -> Mission:
    tasks = [Task(f"t{i}", "T", "x", status=s) for i, s in enumerate(statuses)]
    return Mission("goal", tasks)


def test_mission_status() -> None:
    assert _with(TaskStatus.PASSED, TaskStatus.PENDING).status == "in-progress"
    assert _with(TaskStatus.PASSED, TaskStatus.PASSED).status == "done"
    assert _with(TaskStatus.PASSED, TaskStatus.SKIPPED).status == "needs-attention"
    counts = _with(TaskStatus.FAILED, TaskStatus.FAILED).counts()
    assert counts["failed"] == 2
    assert counts["pending"] == 0
