from pathlib import Path

import pytest

from guardrun.errors import ToolError
from guardrun.mission.plan import Attempt
from guardrun.mission.roles import (
    Escalate,
    PublishTasks,
    SubmitVerdict,
    Verdict,
    inspector_prompt,
    planner_prompt,
    rework_prompt,
    worker_prompt,
)
from guardrun.tools.base import ToolContext
from guardrun.tools.schema import check_schema
from tests.mission.conftest import mission, task


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext.for_root(tmp_path)


def test_schemas_use_supported_keywords() -> None:
    for tool in (PublishTasks(), Escalate(), SubmitVerdict()):
        check_schema(tool.input_schema)


def test_publish_tasks_validates_the_plan(ctx: ToolContext) -> None:
    tool = PublishTasks(max_tasks=2)
    with pytest.raises(ToolError, match="plan rejected: duplicate"):
        tool.run({"tasks": [task("a"), task("a")]}, ctx)
    assert tool.tasks is None
    assert "Published 2 tasks" in tool.run({"tasks": [task("a"), task("b")]}, ctx)
    assert [t.id for t in tool.tasks or []] == ["a", "b"]


def test_escalate_and_verdict_record_their_input(ctx: ToolContext) -> None:
    escalate = Escalate()
    escalate.run({"reason": "  need the prod password  "}, ctx)
    assert escalate.reason == "need the prod password"
    submit = SubmitVerdict()
    submit.run({"passed": True, "feedback": " fine "}, ctx)
    assert submit.verdict == Verdict(True, "fine", needs_human=False)


def test_prompts_carry_the_task() -> None:
    m = mission(task("a", checks=["pytest -q"]), task("b", depends_on=["a"]))
    a, b = m.tasks
    a.attempts.append(Attempt(1, "completed", "built the parser", True, "ok"))
    assert "at most 7 tasks" in planner_prompt("make it fast", 7)
    assert "make it fast" in planner_prompt("make it fast", 7)
    first = worker_prompt(m, a)
    assert "Task a: Task a" in first
    assert "- pytest -q" in first
    assert "Ship it" in first
    assert "built the parser" in worker_prompt(m, b)
    assert "fix the import" in rework_prompt("fix the import")
    review = inspector_prompt(a, "I did it", "$ pytest -q -> PASS")
    assert "<worker_report>\nI did it\n</worker_report>" in review
    assert "PASS" in review
