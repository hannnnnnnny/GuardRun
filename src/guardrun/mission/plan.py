"""Missions: a goal broken into tasks that workers carry out and an inspector checks.

Plans come from a model (the planner) or a hand-written file, so everything is
validated here before it is trusted: ids, field types and sizes, references
between tasks, and the absence of dependency cycles.
"""

from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from guardrun.errors import GuardRunError

TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_TASKS = 50
MAX_TITLE_CHARS = 200
MAX_TEXT_CHARS = 8000
MAX_LIST_ITEMS = 20
MAX_ITEM_CHARS = 1000


class PlanError(GuardRunError):
    """Raised when a mission plan is malformed."""


class TaskStatus(enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    """The inspector rejected every attempt."""
    ESCALATED = "escalated"
    """Stopped for a human: a blocker, a failed run, or a serious QA finding."""
    SKIPPED = "skipped"
    """Not started because a task it depends on did not pass."""


@dataclass
class Attempt:
    """One round of work on a task and the inspector's judgment of it."""

    number: int
    stop: str
    summary: str
    passed: bool
    feedback: str
    cost_usd: float = 0.0


@dataclass
class Task:
    id: str
    title: str
    instructions: str
    acceptance: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    attempts: list[Attempt] = field(default_factory=list)
    note: str = ""
    # Attempts before this index belong to earlier rounds (before a ``--retry``).
    round_start: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Mission:
    goal: str
    tasks: list[Task]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    cost_usd: float = 0.0

    def task(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in TaskStatus}
        for task in self.tasks:
            counts[task.status.value] += 1
        return counts

    @property
    def status(self) -> str:
        """``done``, ``in-progress`` (work left), or ``needs-attention``."""
        counts = self.counts()
        if counts["pending"]:
            return "in-progress"
        if counts["passed"] == len(self.tasks):
            return "done"
        return "needs-attention"

    def touch(self) -> None:
        self.updated = _now()


def _text(data: dict[str, Any], key: str, where: str, limit: int, required: bool = True) -> str:
    if key not in data:
        if required:
            raise PlanError(f"{where}: missing {key!r}")
        return ""
    value = data[key]
    if not isinstance(value, str) or (required and not value.strip()):
        raise PlanError(f"{where}: {key!r} must be a non-empty string")
    if len(value) > limit:
        raise PlanError(f"{where}: {key!r} is longer than {limit} characters")
    return value.strip()


def _strings(data: dict[str, Any], key: str, where: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise PlanError(f"{where}: {key!r} must be a list of non-empty strings")
    if len(value) > MAX_LIST_ITEMS:
        raise PlanError(f"{where}: {key!r} has more than {MAX_LIST_ITEMS} items")
    if any(len(v) > MAX_ITEM_CHARS for v in value):
        raise PlanError(f"{where}: an item in {key!r} is longer than {MAX_ITEM_CHARS} characters")
    return [v.strip() for v in value]


_TASK_KEYS = {"id", "title", "instructions", "acceptance", "checks", "depends_on"}


def task_from_dict(data: Any, index: int) -> Task:
    """Validate one task as written by a planner or a person."""
    where = f"task {index + 1}"
    if not isinstance(data, dict):
        raise PlanError(f"{where}: must be an object")
    unknown = sorted(set(data) - _TASK_KEYS)
    if unknown:
        raise PlanError(f"{where}: unknown keys {', '.join(unknown)}")
    task_id = _text(data, "id", where, 40)
    if not TASK_ID.match(task_id):
        raise PlanError(f"{where}: id {task_id!r} must be lowercase letters, digits, and dashes")
    return Task(
        id=task_id,
        title=_text(data, "title", where, MAX_TITLE_CHARS),
        instructions=_text(data, "instructions", where, MAX_TEXT_CHARS),
        acceptance=_strings(data, "acceptance", where),
        checks=_strings(data, "checks", where),
        depends_on=_strings(data, "depends_on", where),
    )


def check_graph(tasks: list[Task]) -> None:
    """Ids are unique, dependencies exist, and there are no cycles."""
    ids = [task.id for task in tasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PlanError(f"duplicate task ids: {', '.join(duplicates)}")
    known = set(ids)
    for task in tasks:
        missing = [d for d in task.depends_on if d not in known]
        if missing:
            raise PlanError(f"task {task.id} depends on unknown tasks: {', '.join(missing)}")
    if topological_order(tasks) is None:
        raise PlanError("task dependencies form a cycle")


def topological_order(tasks: list[Task]) -> list[Task] | None:
    """Tasks with dependencies first, keeping the written order otherwise; None on a cycle."""
    remaining = list(tasks)
    done: set[str] = set()
    ordered: list[Task] = []
    while remaining:
        ready = next((t for t in remaining if set(t.depends_on) <= done), None)
        if ready is None:
            return None
        ordered.append(ready)
        done.add(ready.id)
        remaining.remove(ready)
    return ordered


def tasks_from_list(raw: Any, max_tasks: int = MAX_TASKS) -> list[Task]:
    """Validate a whole plan and return its tasks in a runnable order."""
    if not isinstance(raw, list) or not raw:
        raise PlanError("a plan needs a non-empty list of tasks")
    if len(raw) > max_tasks:
        raise PlanError(f"a plan may have at most {max_tasks} tasks, got {len(raw)}")
    tasks = [task_from_dict(item, index) for index, item in enumerate(raw)]
    check_graph(tasks)
    return topological_order(tasks) or tasks


def mission_from_plan(data: Any, max_tasks: int = MAX_TASKS) -> Mission:
    """Build a new mission from ``{"goal": ..., "tasks": [...]}``."""
    if not isinstance(data, dict):
        raise PlanError("a mission plan must be an object with 'goal' and 'tasks'")
    unknown = sorted(set(data) - {"goal", "tasks"})
    if unknown:
        raise PlanError(f"unknown mission keys: {', '.join(unknown)}")
    goal = _text(data, "goal", "mission", MAX_TEXT_CHARS)
    return Mission(goal=goal, tasks=tasks_from_list(data.get("tasks"), max_tasks))
