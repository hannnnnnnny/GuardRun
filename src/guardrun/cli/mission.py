"""``guardrun mission`` and ``guardrun inbox``: long unattended work, reported briefly."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from guardrun.cli.render import ConsoleRenderer
from guardrun.config import PROVIDER_DEFAULT_MODELS, Config
from guardrun.events import Event, EventBus
from guardrun.factory import STATE_DIR, options_from_config
from guardrun.mission.checks import CheckRunner
from guardrun.mission.inbox import BLOCKER, Inbox, Notice
from guardrun.mission.plan import Mission, PlanError, mission_from_plan
from guardrun.mission.report import mission_line, mission_report
from guardrun.mission.store import MissionStore
from guardrun.mission.supervisor import MissionSettings, Supervisor, agent_maker, reopen
from guardrun.providers.anthropic import EFFORT_LEVELS
from guardrun.providers.base import Provider
from guardrun.safety.approval import deny_all
from guardrun.safety.redact import Redactor

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 CI only
    import tomli as tomllib

if TYPE_CHECKING:
    from guardrun.cli.main import IO

EXIT_OK = 0
EXIT_INCOMPLETE = 1
MAX_PLAN_FILE_BYTES = 1024 * 1024
ProviderFactory = Callable[[Config], Provider]


def _add_mission_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-w", "--workspace", type=Path, default=Path.cwd(), help="workspace root")
    parser.add_argument("--provider", choices=list(PROVIDER_DEFAULT_MODELS), help="model provider")
    parser.add_argument("--model", help="model id (default from config)")
    parser.add_argument("--effort", choices=EFFORT_LEVELS, help="reasoning effort")
    parser.add_argument(
        "--max-cost", type=float, dest="mission_cost", help="cost cap in USD for the whole mission"
    )
    parser.add_argument(
        "--max-attempts", type=int, dest="mission_attempts", help="QA rounds per task"
    )
    parser.add_argument("--no-audit", action="store_true", help="do not write the audit log")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every tool call")


def add_parsers(sub: Any) -> None:
    """Register ``mission`` (with its subcommands) and ``inbox``."""
    mission = sub.add_parser("mission", help="plan a goal into tasks, run them, inspect results")
    actions = mission.add_subparsers(dest="mission_command", required=True)
    plan = actions.add_parser("plan", help="have the planner break a goal into tasks")
    plan.add_argument("goal", help="what the mission should achieve")
    plan.add_argument("--run", action="store_true", help="start the mission right away")
    _add_mission_options(plan)
    load = actions.add_parser("import", help="create a mission from a TOML or JSON plan file")
    load.add_argument("file", type=Path, help="plan file with 'goal' and [[tasks]]")
    load.add_argument("--run", action="store_true", help="start the mission right away")
    _add_mission_options(load)
    run = actions.add_parser("run", help="run (or resume) a mission")
    run.add_argument("mission_id")
    run.add_argument("--retry", action="store_true", help="requeue failed and escalated tasks")
    _add_mission_options(run)
    status = actions.add_parser("status", help="show one mission, or list them all")
    status.add_argument("mission_id", nargs="?")
    status.add_argument("-v", "--verbose", action="store_true", help="show criteria and checks")
    status.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    inbox = sub.add_parser("inbox", help="what missions need you to look at")
    inbox.add_argument("-n", "--tail", type=int, default=20)
    inbox.add_argument("-w", "--workspace", type=Path, default=Path.cwd())


def _store(workspace: Path) -> MissionStore:
    return MissionStore(workspace / STATE_DIR / "missions")


def _inbox(workspace: Path) -> Inbox:
    return Inbox(workspace / STATE_DIR / "inbox.jsonl", Redactor.from_environment(os.environ))


class MissionRenderer:
    """Mission-level progress lines; agent tool calls only when verbose."""

    def __init__(self, out: TextIO, verbose: bool = False) -> None:
        self.out = out
        self.tools = ConsoleRenderer(out) if verbose else None

    def __call__(self, event: Event) -> None:
        line = _describe(event)
        if line is not None:
            self.out.write(line + "\n")
            self.out.flush()
        elif self.tools is not None and "role" in event.data:
            self.tools(event)


def _short(text: str, limit: int = 200) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _describe(event: Event) -> str | None:
    d = event.data
    if event.kind == "mission_planned":
        return f"published {d['tasks']} tasks as mission {d['mission']}"
    if event.kind == "task_started":
        return f"\n== {d['task']}: {d['title']} (attempt {d['attempt']})"
    if event.kind == "task_reviewed":
        verdict = "passed" if d["passed"] else "sent back"
        return f"   QA {verdict}: {_short(d['feedback'])}"
    if event.kind == "task_finished":
        # A pass was just shown by the QA line; other endings need their reason.
        note = "" if d["status"] == "passed" else f": {_short(d['note'], 120)}"
        return f"   -> {d['status']}{note}"
    if event.kind == "inbox_posted":
        label = "needs you" if d["level"] == BLOCKER else d["level"]
        return f"   [{label}] {_short(d['reason'], 300)}"
    return None


def _settings(config: Config, args: argparse.Namespace) -> MissionSettings:
    attempts = getattr(args, "mission_attempts", None)
    cost = getattr(args, "mission_cost", None)
    try:
        return MissionSettings(
            max_attempts=attempts if attempts is not None else config.mission_max_attempts,
            max_tasks=config.mission_max_tasks,
            max_cost_usd=cost if cost is not None else config.mission_max_cost_usd,
        )
    except ValueError as exc:
        raise PlanError(f"invalid mission settings: {exc}") from exc


def build_supervisor(
    args: argparse.Namespace, config: Config, io: IO, factory: ProviderFactory
) -> Supervisor:
    events = EventBus()
    events.subscribe(MissionRenderer(io.stdout, verbose=args.verbose))
    options = options_from_config(config, deny_all, root=args.workspace)
    inbox = _inbox(args.workspace)
    checks = CheckRunner(args.workspace, options.command_policy, options.protected, inbox.redactor)
    return Supervisor(
        agent_maker(args.workspace, factory(config), options, events),
        checks,
        _store(args.workspace),
        inbox,
        settings=_settings(config, args),
        events=events,
    )


def _execute(supervisor: Supervisor, mission: Mission, io: IO) -> int:
    io.stdout.write(f"running mission {mission.id} ({len(mission.tasks)} tasks)\n")
    try:
        supervisor.run(mission)
    except KeyboardInterrupt:
        supervisor.store.save(mission)
        io.stdout.write(f"\ninterrupted; resume with: guardrun mission run {mission.id}\n")
        return EXIT_INCOMPLETE
    io.stdout.write("\n" + mission_report(mission) + "\n")
    blockers = mission.counts()["failed"] + mission.counts()["escalated"]
    if blockers:
        io.stdout.write("see `guardrun inbox` for what needs you\n")
    return EXIT_OK if mission.status == "done" else EXIT_INCOMPLETE


def _created(mission: Mission, run: Callable[[], int], args: argparse.Namespace, io: IO) -> int:
    io.stdout.write(mission_report(mission, verbose=True) + "\n")
    if args.run:
        return run()
    io.stdout.write(f"start it with: guardrun mission run {mission.id}\n")
    return EXIT_OK


def read_plan_file(path: Path) -> Any:
    """Parse a TOML or JSON plan file, refusing anything unreasonably large."""
    try:
        if path.stat().st_size > MAX_PLAN_FILE_BYTES:
            raise PlanError(f"{path.name} is larger than {MAX_PLAN_FILE_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return tomllib.loads(text)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise PlanError(
            f"{path.name} is not valid {path.suffix.lstrip('.') or 'TOML'}: {exc}"
        ) from exc


def cmd_mission(args: argparse.Namespace, config: Config, io: IO, factory: ProviderFactory) -> int:
    if args.mission_command == "import":
        # Importing needs no model, so the provider (and its key) is only built to run.
        plan = read_plan_file(args.file)
        mission = mission_from_plan(plan, _settings(config, args).max_tasks)
        _store(args.workspace).save(mission)

        def run() -> int:
            return _execute(build_supervisor(args, config, io, factory), mission, io)

        return _created(mission, run, args, io)
    supervisor = build_supervisor(args, config, io, factory)
    if args.mission_command == "plan":
        io.stdout.write("planning...\n")
        planned = supervisor.plan(args.goal)
        return _created(planned, lambda: _execute(supervisor, planned, io), args, io)
    mission = supervisor.store.load(args.mission_id)
    if args.retry:
        io.stdout.write(f"requeued {reopen(mission)} tasks\n")
    return _execute(supervisor, mission, io)


def cmd_mission_status(args: argparse.Namespace, io: IO) -> int:
    store = _store(args.workspace)
    if args.mission_id:
        io.stdout.write(mission_report(store.load(args.mission_id), args.verbose) + "\n")
        return EXIT_OK
    missions = store.list_missions()
    if not missions:
        io.stdout.write("no missions yet; start one with: guardrun mission plan GOAL\n")
    for mission in missions:
        io.stdout.write(mission_line(mission) + "\n")
    return EXIT_OK


def _format_notice(notice: Notice) -> str:
    where = f"{notice.mission}/{notice.task}" if notice.task else notice.mission
    return f"{notice.ts[:19]}  {notice.level:8} {where}\n    {notice.reason}"


def cmd_inbox(args: argparse.Namespace, io: IO) -> int:
    notices = _inbox(args.workspace).read()
    shown = notices[-args.tail :] if args.tail > 0 else []
    if not shown:
        io.stdout.write("inbox is empty: nothing needs you\n")
    for notice in shown:
        io.stdout.write(_format_notice(notice) + "\n")
    return EXIT_OK
