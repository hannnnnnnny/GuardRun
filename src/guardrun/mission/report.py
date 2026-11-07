"""Plain-text views of missions for the terminal."""

from __future__ import annotations

from guardrun.mission.plan import Mission, Task

_MARKS = {
    "pending": "[ ]",
    "passed": "[x]",
    "failed": "[!]",
    "escalated": "[?]",
    "skipped": "[-]",
}


def _oneline(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def mission_line(mission: Mission) -> str:
    counts = mission.counts()
    done = f"{counts['passed']}/{len(mission.tasks)} passed"
    return (
        f"{mission.id}  {mission.updated[:19]}  {mission.status:15} {done:12} "
        f"${mission.cost_usd:.2f}  {_oneline(mission.goal, 50)}"
    )


def _task_lines(task: Task, verbose: bool) -> list[str]:
    count = len(task.attempts)
    tries = f" ({count} attempt{'s' if count != 1 else ''})" if count else ""
    lines = [f"{_MARKS[task.status.value]} {task.id}: {task.title}{tries}"]
    if task.depends_on:
        lines.append(f"      after: {', '.join(task.depends_on)}")
    if task.note:
        lines.append(f"      {task.status.value}: {_oneline(task.note, 300)}")
    if verbose:
        lines.extend(f"      - {item}" for item in task.acceptance)
        lines.extend(f"      $ {check}" for check in task.checks)
    return lines


def mission_report(mission: Mission, verbose: bool = False) -> str:
    """The goal, every task with its status, and the totals."""
    lines = [f"mission {mission.id}: {_oneline(mission.goal, 200)}", ""]
    for task in mission.tasks:
        lines.extend(_task_lines(task, verbose))
    counts = ", ".join(f"{n} {status}" for status, n in mission.counts().items() if n)
    lines += ["", f"status: {mission.status} ({counts}), ~${mission.cost_usd:.4f}"]
    return "\n".join(lines)
