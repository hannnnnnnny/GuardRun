"""The supervisor: hand tasks to workers, have each result inspected, repeat.

A mission runs without a human. Work that fails inspection goes back to the
worker with the inspector's feedback; only what the agents cannot settle
themselves (a blocker, a serious finding, exhausted retries or budget) is
posted to the user's inbox. State is saved after every attempt, so an
interrupted mission picks up where it stopped.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from guardrun.agent import Agent, RunResult
from guardrun.events import Event, EventBus
from guardrun.factory import AgentOptions, build_agent
from guardrun.mission.checks import CheckResult, CheckRunner, format_checks
from guardrun.mission.inbox import BLOCKER, WARNING, Inbox
from guardrun.mission.plan import Attempt, Mission, PlanError, Task, TaskStatus
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
from guardrun.mission.store import MissionStore
from guardrun.providers.base import Provider
from guardrun.safety.modes import ApprovalMode
from guardrun.tools.base import Tool
from guardrun.tools.builtin import read_only_tools

# Run endings that retrying with feedback cannot fix.
FATAL_STOPS = frozenset({"error", "refused", "cancelled"})
MAX_SUMMARY_CHARS = 4000
_UNFINISHED = (TaskStatus.FAILED, TaskStatus.ESCALATED, TaskStatus.SKIPPED)


class Role(enum.Enum):
    PLANNER = "planner"
    WORKER = "worker"
    INSPECTOR = "inspector"


AgentMaker = Callable[[Role, list[Tool]], Agent]


def worker_mode(configured: ApprovalMode) -> ApprovalMode:
    """Nobody answers prompts during a mission, so workers run unattended,
    unless the user restricted the workspace to read-only."""
    return configured if configured is ApprovalMode.READ_ONLY else ApprovalMode.UNATTENDED


def agent_maker(root: Path, provider: Provider, base: AgentOptions, events: EventBus) -> AgentMaker:
    """Build role agents from the project's options.

    Planners and inspectors only ever get read-only tools. Each agent has its own
    event bus (so audit logging is not duplicated) that forwards to ``events``.
    """

    def make(role: Role, tools: list[Tool]) -> Agent:
        opts = dataclasses.replace(base, events=EventBus(), conversation=None)
        if role is Role.WORKER:
            opts.mode = worker_mode(base.mode)
            opts.extra_tools = [*base.extra_tools, *tools]
        else:
            opts.mode = ApprovalMode.READ_ONLY
            opts.tools = read_only_tools()
            opts.extra_tools = list(tools)

        def forward(event: Event) -> None:
            events.emit(event.kind, **{**event.data, "role": role.value})

        opts.events.subscribe(forward)
        return build_agent(root, provider, opts)

    return make


@dataclass
class MissionSettings:
    max_attempts: int = 3
    max_tasks: int = 20
    # The whole mission, across planner, workers, and inspector.
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_tasks < 1:
            raise ValueError("max_attempts and max_tasks must be positive")
        if self.max_cost_usd is not None and self.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")


@dataclass
class _Work:
    """What one worker run produced, before inspection."""

    result: RunResult
    summary: str
    escalation: str | None


class Supervisor:
    def __init__(
        self,
        make_agent: AgentMaker,
        checks: CheckRunner,
        store: MissionStore,
        inbox: Inbox,
        *,
        settings: MissionSettings | None = None,
        events: EventBus | None = None,
    ) -> None:
        self.make_agent = make_agent
        self.checks = checks
        self.store = store
        self.inbox = inbox
        self.settings = settings or MissionSettings()
        self.events = events or EventBus()
        self.events.subscribe(self._watch)
        self._suspicions: list[str] = []

    # -- publishing -------------------------------------------------------

    def plan(self, goal: str) -> Mission:
        """Have the planner break ``goal`` into tasks, and save the mission."""
        publish = PublishTasks(self.settings.max_tasks)
        planner = self.make_agent(Role.PLANNER, [publish])
        result = planner.run(planner_prompt(goal, self.settings.max_tasks))
        if publish.tasks is None:
            # No mission exists to file a warning under; don't pin these on a later task.
            self._suspicions.clear()
            why = result.detail or result.text.strip()[:300] or "no plan was published"
            raise PlanError(f"the planner did not publish a plan ({result.stop}: {why})")
        mission = Mission(goal=goal, tasks=publish.tasks, cost_usd=result.cost_usd or 0.0)
        self.store.save(mission)
        # The planner reads the project too, so its findings must reach the user.
        self._report_suspicions(mission, "")
        self.events.emit("mission_planned", mission=mission.id, tasks=len(mission.tasks))
        return mission

    # -- running ----------------------------------------------------------

    def run(self, mission: Mission) -> Mission:
        """Work through every runnable task until the mission is settled."""
        self.events.emit("mission_started", mission=mission.id, goal=mission.goal)
        while (task := self._next_task(mission)) is not None:
            if self._budget_spent(mission):
                left = mission.counts()["pending"]
                limit = self.settings.max_cost_usd
                self._notify(
                    mission,
                    "",
                    BLOCKER,
                    f"mission budget of ${limit:.2f} reached "
                    f"with {left} tasks left; raise it and run the mission again",
                )
                break
            self._run_task(mission, task)
            self.store.save(mission)
        self.store.save(mission)
        self.events.emit(
            "mission_finished",
            mission=mission.id,
            status=mission.status,
            counts=mission.counts(),
            cost_usd=mission.cost_usd,
        )
        return mission

    def _budget_spent(self, mission: Mission) -> bool:
        limit = self.settings.max_cost_usd
        return limit is not None and mission.cost_usd >= limit

    def _next_task(self, mission: Mission) -> Task | None:
        """The first pending task whose dependencies passed; skips blocked ones."""
        for task in mission.tasks:
            if task.status is not TaskStatus.PENDING:
                continue
            deps = [mission.task(dep) for dep in task.depends_on]
            failed = [dep for dep in deps if dep.status in _UNFINISHED]
            if failed:
                names = ", ".join(f"{d.id} ({d.status.value})" for d in failed)
                self._settle(task, TaskStatus.SKIPPED, f"depends on {names}")
            elif all(dep.status is TaskStatus.PASSED for dep in deps):
                return task
        return None

    def _run_task(self, mission: Mission, task: Task) -> None:
        try:
            self._work_on(mission, task)
        finally:
            # Also when the budget stops the task early: warnings must not be lost.
            self._report_suspicions(mission, task.id)

    def _work_on(self, mission: Mission, task: Task) -> None:
        escalate = Escalate()
        worker = self.make_agent(Role.WORKER, [escalate])
        prompt = worker_prompt(mission, task)
        if task.attempts and not task.attempts[-1].passed:
            prompt += "\n\n" + rework_prompt(task.attempts[-1].feedback)
        # Attempts made before an interruption count, so resuming doesn't reset the limit.
        used = len(task.attempts) - task.round_start
        for _ in range(max(self.settings.max_attempts - used, 0)):
            self.events.emit(
                "task_started", task=task.id, title=task.title, attempt=len(task.attempts) + 1
            )
            if self._attempt(mission, task, worker, escalate, prompt):
                break
            self.store.save(mission)
            if self._budget_spent(mission):
                return
            prompt = rework_prompt(task.attempts[-1].feedback)
        else:
            last = task.attempts[-1].feedback
            reason = f"failed inspection {self.settings.max_attempts} times. Last feedback: {last}"
            self._settle(task, TaskStatus.FAILED, reason)
            self._notify(mission, task.id, BLOCKER, reason)

    def _attempt(
        self, mission: Mission, task: Task, worker: Agent, escalate: Escalate, prompt: str
    ) -> bool:
        """One round of work and inspection; True once the task is settled."""
        work = self._work(mission, worker, escalate, prompt)
        problem = work.escalation
        if problem is None and work.result.stop in FATAL_STOPS:
            problem = f"the worker run ended with {work.result.stop}: {work.result.detail}"
        if problem is not None:
            return self._escalate(mission, task, work, problem)
        results = self.checks.run(task.checks)
        blocked = [check.command for check in results if check.blocked]
        if blocked:
            return self._escalate(mission, task, work, _blocked_message(blocked))
        verdict = self._inspect(mission, task, work.summary, results)
        if verdict is None:
            return self._escalate(mission, task, work, "the inspector could not finish a review")
        # A failing check overrules the inspector: QA can be stricter than the
        # checks, never more lenient.
        checks_ok = all(check.passed for check in results)
        passed = verdict.passed and checks_ok
        feedback = (
            verdict.feedback if checks_ok else f"{verdict.feedback}\n\n{_checks_failed(results)}"
        )
        self._record(task, work, passed, feedback)
        if verdict.needs_human:
            self._settle(task, TaskStatus.ESCALATED, f"inspector: {feedback}")
            self._notify(mission, task.id, BLOCKER, f"the inspector flagged: {feedback}")
            return True
        if passed:
            self._settle(task, TaskStatus.PASSED, feedback)
        return passed

    def _work(self, mission: Mission, worker: Agent, escalate: Escalate, prompt: str) -> _Work:
        escalate.reason = None
        result = self._spend(mission, worker.run(prompt))
        summary = result.text.strip()[:MAX_SUMMARY_CHARS] or f"(no summary; ended {result.stop})"
        return _Work(result, summary, escalate.reason)

    def _inspect(
        self, mission: Mission, task: Task, summary: str, results: list[CheckResult]
    ) -> Verdict | None:
        submit = SubmitVerdict()
        inspector = self.make_agent(Role.INSPECTOR, [submit])
        result = self._spend(
            mission, inspector.run(inspector_prompt(task, summary, format_checks(results)))
        )
        if submit.verdict is None and result.stop not in FATAL_STOPS:
            text = result.text.strip()[:1000] or "no reason given"
            return Verdict(passed=False, feedback=f"No verdict was recorded. Inspector: {text}")
        return submit.verdict

    # -- bookkeeping ------------------------------------------------------

    def _spend(self, mission: Mission, result: RunResult) -> RunResult:
        mission.cost_usd += result.cost_usd or 0.0
        return result

    def _record(self, task: Task, work: _Work, passed: bool, feedback: str) -> None:
        task.attempts.append(
            Attempt(
                number=len(task.attempts) + 1,
                stop=work.result.stop,
                summary=work.summary,
                passed=passed,
                feedback=feedback,
                cost_usd=work.result.cost_usd or 0.0,
            )
        )
        self.events.emit("task_reviewed", task=task.id, passed=passed, feedback=feedback)

    def _escalate(self, mission: Mission, task: Task, work: _Work, reason: str) -> bool:
        self._record(task, work, False, reason)
        self._settle(task, TaskStatus.ESCALATED, reason)
        self._notify(mission, task.id, BLOCKER, reason)
        return True

    def _settle(self, task: Task, status: TaskStatus, note: str) -> None:
        task.status = status
        task.note = note
        self.events.emit("task_finished", task=task.id, status=status.value, note=note)

    def _notify(self, mission: Mission, task: str, level: str, reason: str) -> None:
        notice = self.inbox.post(mission.id, task, level, reason)
        self.events.emit("inbox_posted", task=task, level=level, reason=notice.reason)

    def _watch(self, event: Event) -> None:
        if event.kind == "injection_suspected":
            self._suspicions.append(str(event.data.get("phrase", "")))

    def _report_suspicions(self, mission: Mission, task_id: str) -> None:
        if self._suspicions:
            phrases = ", ".join(repr(p) for p in sorted(set(self._suspicions))[:5])
            self._notify(
                mission, task_id, WARNING, f"possible prompt injection in tool output: {phrases}"
            )
            self._suspicions.clear()


def _blocked_message(commands: list[str]) -> str:
    listed = ", ".join(f"`{c}`" for c in commands)
    return (
        f"check {listed} needs approval, which an unattended mission cannot give. "
        "Add it to [shell] allow in guardrun.toml or remove it from the task"
    )


def _checks_failed(results: list[CheckResult]) -> str:
    failed = [r for r in results if not r.passed]
    return "Automated checks failed:\n" + format_checks(failed)


def reopen(mission: Mission) -> int:
    """Put failed, escalated, and skipped tasks back in the queue, each with a fresh
    set of attempts; returns how many."""
    reopened = 0
    for task in mission.tasks:
        if task.status in _UNFINISHED:
            task.status = TaskStatus.PENDING
            task.note = ""
            task.round_start = len(task.attempts)
            reopened += 1
    return reopened
