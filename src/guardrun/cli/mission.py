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
from guardrun.cli.style import PLAIN, Style
from guardrun.config import PROVIDER_DEFAULT_MODELS, Config
from guardrun.events import Event, EventBus
from guardrun.factory import STATE_DIR, options_from_config
from guardrun.mission.checks import CheckRunner
from guardrun.mission.inbox import BLOCKER, Inbox, Notice
from guardrun.mission.notify import CommandNotifier
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


def _add_inbox_parser(sub: Any) -> None:
    inbox = sub.add_parser("inbox", help="what missions need you to look at")
    inbox.add_argument("-n", "--tail", type=int, default=20, help="show at most this many")
    inbox.add_argument("-a", "--all", action="store_true", help="include notices already read")
    inbox.add_argument("--ack", action="store_true", help="mark every notice as read")
    inbox.add_argument("-w", "--workspace", type=Path, default=Path.cwd())


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
    _add_inbox_parser(sub)


def _store(workspace: Path) -> MissionStore:
    return MissionStore(workspace / STATE_DIR / "missions")


def _inbox(workspace: Path) -> Inbox:
    return Inbox(workspace / STATE_DIR / "inbox.jsonl", Redactor.from_environment(os.environ))


def _short(text: str, limit: int = 200) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _duration(seconds: float) -> str:
    return f"{seconds:.0f}s" if seconds < 60 else f"{seconds / 60:.0f} min"


def _task_started(d: dict[str, Any], s: Style) -> str:
    where = f"[{d['position']}/{d['total']}] " if "position" in d else ""
    head = s.bold(f"{where}{d['task']}: {d['title']}")
    spent = f" · ${d['spent_usd']:.2f} spent" if "spent_usd" in d else ""
    detail = s.dim(f"attempt {d['attempt']}{spent}")
    return f"\n{head} {detail}"


def _task_reviewed(d: dict[str, Any], s: Style) -> str:
    verdict = s.ok("QA passed") if d["passed"] else s.warn("QA sent back")
    return f"   {verdict}: {_short(d['feedback'])}"


def _task_finished(d: dict[str, Any], s: Style) -> str:
    status = d["status"]
    paint = {"passed": s.ok, "skipped": s.warn}.get(status, s.error)
    # A pass was just shown by the QA line; other endings need their reason.
    note = "" if status == "passed" else f": {_short(d['note'], 120)}"
    return f"   -> {paint(status)}{note}"


def _inbox_posted(d: dict[str, Any], s: Style) -> str:
    label = s.error("[needs you]") if d["level"] == BLOCKER else s.warn(f"[{d['level']}]")
    return f"   {label} {_short(d['reason'], 300)}"


def _service_waiting(d: dict[str, Any], s: Style) -> str:
    retry = f"retry {d['retry']}/{d['retries']} in {_duration(d['seconds'])}"
    return f"   {s.warn('model service unavailable')}: {_short(d['detail'], 120)} {s.dim(retry)}"


_LINES: dict[str, Callable[[dict[str, Any], Style], str]] = {
    "mission_planned": lambda d, s: f"published {d['tasks']} tasks as mission {d['mission']}",
    "task_started": _task_started,
    "task_reviewed": _task_reviewed,
    "task_finished": _task_finished,
    "inbox_posted": _inbox_posted,
    "service_waiting": _service_waiting,
}


def describe(event: Event, style: Style = PLAIN) -> str | None:
    line = _LINES.get(event.kind)
    return line(event.data, style) if line is not None else None


class MissionRenderer:
    """Mission-level progress lines; agent tool calls only when verbose."""

    def __init__(self, out: TextIO, verbose: bool = False, style: Style = PLAIN) -> None:
        self.out = out
        self.style = style
        self.tools = ConsoleRenderer(out, style=style) if verbose else None

    def __call__(self, event: Event) -> None:
        line = describe(event, self.style)
        if line is not None:
            # On a terminal, a blocker also rings the bell for whoever is nearby.
            bell = self.style.enabled and event.kind == "inbox_posted"
            bell = bell and event.data.get("level") == BLOCKER
            self.out.write(line + ("\a" if bell else "") + "\n")
            self.out.flush()
        elif self.tools is not None and "role" in event.data:
            self.tools(event)


def _settings(config: Config, args: argparse.Namespace) -> MissionSettings:
    attempts = getattr(args, "mission_attempts", None)
    cost = getattr(args, "mission_cost", None)
    try:
        return MissionSettings(
            max_attempts=attempts if attempts is not None else config.mission_max_attempts,
            max_tasks=config.mission_max_tasks,
            max_cost_usd=cost if cost is not None else config.mission_max_cost_usd,
            outage_retries=config.mission_outage_retries,
            outage_wait_seconds=config.mission_outage_wait_seconds,
        )
    except ValueError as exc:
        raise PlanError(f"invalid mission settings: {exc}") from exc


def _mission_inbox(args: argparse.Namespace, config: Config, io: IO) -> Inbox:
    inbox = _inbox(args.workspace)
    if config.mission_notify_command:

        def report(message: str) -> None:
            io.stderr.write(io.style.warn(f"guardrun: {message}") + "\n")

        notifier = CommandNotifier(config.mission_notify_command, args.workspace, report)
        inbox.notifiers.append(notifier)
    return inbox


def build_supervisor(
    args: argparse.Namespace, config: Config, io: IO, factory: ProviderFactory
) -> Supervisor:
    events = EventBus()
    events.subscribe(MissionRenderer(io.stdout, args.verbose, io.style))
    options = options_from_config(config, deny_all, root=args.workspace)
    inbox = _mission_inbox(args, config, io)
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
    _, unread = supervisor.inbox.unread()
    if unread:
        io.stdout.write(io.style.warn(f"{len(unread)} new in your inbox: guardrun inbox") + "\n")
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
        try:
            planned = supervisor.plan(args.goal)
        except KeyboardInterrupt:
            io.stdout.write("\ninterrupted; nothing was saved\n")
            return EXIT_INCOMPLETE
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


def _format_notice(notice: Notice, new: bool, style: Style) -> str:
    where = f"{notice.mission}/{notice.task}" if notice.task else notice.mission
    paint = style.error if notice.level == BLOCKER else style.warn
    level = paint(f"{notice.level:8}")
    mark = style.accent("new ") if new else "    "
    return f"{mark}{style.dim(notice.ts[:19])}  {level} {where}\n        {notice.reason}"


def cmd_inbox(args: argparse.Namespace, io: IO) -> int:
    inbox = _inbox(args.workspace)
    read, unread = inbox.unread()
    pool = [(n, False) for n in read] + [(n, True) for n in unread] if args.all else []
    pool = pool or [(n, True) for n in unread]
    shown = pool[-args.tail :] if args.tail > 0 else []
    if not shown:
        earlier = f" ({len(read)} read; see them with: guardrun inbox --all)" if read else ""
        io.stdout.write(f"nothing new needs you{earlier}\n")
    for notice, new in shown:
        io.stdout.write(_format_notice(notice, new, io.style) + "\n")
    if args.ack and unread:
        inbox.mark_seen(len(read) + len(unread))
        io.stdout.write(io.style.dim(f"marked {len(unread)} as read") + "\n")
    elif unread and shown:
        io.stdout.write(io.style.dim("when handled: guardrun inbox --ack") + "\n")
    return EXIT_OK
