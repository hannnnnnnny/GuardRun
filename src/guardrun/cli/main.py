"""The ``guardrun`` command."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from guardrun import __version__
from guardrun.agent import Agent, RunResult
from guardrun.cli.mission import add_parsers as add_mission_parsers
from guardrun.cli.mission import cmd_inbox, cmd_mission, cmd_mission_status
from guardrun.cli.render import ConsoleRenderer, format_result, result_to_dict
from guardrun.cli.starter import STARTER_CONFIG
from guardrun.config import CONFIG_FILENAME, PROVIDER_DEFAULT_MODELS, Config, load_config
from guardrun.doctor import FAIL, run_checks
from guardrun.errors import ConfigError, GuardRunError
from guardrun.factory import STATE_DIR, build_agent, options_from_config
from guardrun.providers.anthropic import EFFORT_LEVELS, AnthropicProvider
from guardrun.providers.base import Provider
from guardrun.providers.deepseek import DeepSeekProvider
from guardrun.report import RunSummary, summarize_runs
from guardrun.safety.approval import ConsoleApprover, deny_all
from guardrun.safety.audit import read_audit
from guardrun.safety.command_policy import CommandPolicy
from guardrun.safety.modes import ApprovalMode
from guardrun.safety.workspace import Workspace
from guardrun.sessions import SessionStore, new_session_id
from guardrun.usage import Usage

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_USAGE = 2

ProviderFactory = Callable[[Config], Provider]


@dataclasses.dataclass
class IO:
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    interactive: bool


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-w", "--workspace", type=Path, default=Path.cwd(), help="workspace root")
    parser.add_argument("--mode", choices=[m.value for m in ApprovalMode], help="approval mode")
    parser.add_argument("--provider", choices=list(PROVIDER_DEFAULT_MODELS), help="model provider")
    parser.add_argument("--model", help="model id (default from config)")
    parser.add_argument("--effort", choices=EFFORT_LEVELS, help="reasoning effort")
    parser.add_argument("--max-turns", type=int, help="stop after this many model turns")
    parser.add_argument("--max-cost", type=float, help="stop after this many US dollars")
    parser.add_argument("--no-audit", action="store_true", help="do not write the audit log")
    parser.add_argument("-v", "--verbose", action="store_true", help="show more tool output")
    parser.add_argument("--resume", metavar="SESSION", help="continue a saved session")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="guardrun", description="A safety-first Claude agent.")
    parser.add_argument("--version", action="version", version=f"guardrun {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one task and exit")
    run.add_argument("task", help="what the agent should do")
    run.add_argument(
        "--json", action="store_true", help="print one JSON result object; never prompts"
    )
    _add_run_options(run)
    chat = sub.add_parser("chat", help="interactive multi-turn session")
    _add_run_options(chat)
    policy = sub.add_parser("policy", help="show how a shell command would be treated")
    policy.add_argument("shell_command")
    policy.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    audit = sub.add_parser("audit", help="print recent audit log entries")
    audit.add_argument("-n", "--tail", type=int, default=20)
    audit.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    usage = sub.add_parser("usage", help="summarize token use and cost of recent runs")
    usage.add_argument("-n", "--last", type=int, default=10)
    usage.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    sessions = sub.add_parser("sessions", help="list saved sessions")
    sessions.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    doctor = sub.add_parser("doctor", help="check the environment and configuration")
    doctor.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    init = sub.add_parser("init", help="write a starter guardrun.toml")
    init.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    add_mission_parsers(sub)
    return parser


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Command-line flags win over the config file."""
    changes: dict[str, Any] = {}
    for flag, field in (
        ("provider", "provider"),
        ("model", "model"),
        ("effort", "effort"),
        ("max_turns", "max_turns"),
    ):
        if getattr(args, flag, None) is not None:
            changes[field] = getattr(args, flag)
    if getattr(args, "mode", None):
        changes["mode"] = ApprovalMode(args.mode)
    if getattr(args, "max_cost", None) is not None:
        changes["max_cost_usd"] = args.max_cost
    if getattr(args, "no_audit", False):
        changes["audit"] = False
    for key in ("max_turns", "max_cost_usd"):
        value = changes.get(key)
        if isinstance(value, (int, float)) and value <= 0:
            raise ConfigError(f"--{key.replace('_usd', '').replace('_', '-')} must be positive")
    return dataclasses.replace(config, **changes)


def default_provider(config: Config) -> Provider:
    """Build the provider named in the config."""
    cls = DeepSeekProvider if config.provider == "deepseek" else AnthropicProvider
    return cls(config.resolved_model, max_tokens=config.max_tokens, effort=config.effort)


@dataclasses.dataclass
class CliSession:
    """An agent plus where its conversation is saved after every run."""

    agent: Agent
    store: SessionStore
    id: str
    model: str
    title: str = ""
    json_output: bool = False

    def run(self, task: str, io: IO) -> RunResult:
        self.title = self.title or task
        try:
            result = self.agent.run(task)
        except KeyboardInterrupt:
            self.agent.cancel()
            result = RunResult("", "cancelled", 0, Usage(), detail="interrupted by user")
        self.store.save(self.id, self.agent.conversation, title=self.title, model=self.model)
        if self.json_output:
            io.stdout.write(json.dumps(result_to_dict(result, self.id), ensure_ascii=False) + "\n")
        else:
            io.stdout.write(format_result(result) + f"\nsession: {self.id}\n")
        return result


def _session_store(workspace: Path) -> SessionStore:
    return SessionStore(workspace / STATE_DIR / "sessions")


def _make_session(
    args: argparse.Namespace, config: Config, io: IO, provider_factory: ProviderFactory
) -> CliSession:
    store = _session_store(args.workspace)
    # JSON output must stay machine-readable, so it never shows prompts or progress.
    json_output = getattr(args, "json", False)
    interactive = io.interactive and not json_output
    approver = ConsoleApprover(io.stdin, io.stdout) if interactive else deny_all
    options = options_from_config(config, approver, root=args.workspace)
    options.conversation = store.load(args.resume) if args.resume else None
    if not json_output:
        options.events.subscribe(ConsoleRenderer(io.stdout, verbose=args.verbose))
    agent = build_agent(args.workspace, provider_factory(config), options)
    session_id = args.resume or new_session_id()
    return CliSession(agent, store, session_id, config.resolved_model, json_output=json_output)


def cmd_run(args: argparse.Namespace, config: Config, io: IO, factory: ProviderFactory) -> int:
    result = _make_session(args, config, io, factory).run(args.task, io)
    return EXIT_OK if result.ok else EXIT_INCOMPLETE


def cmd_chat(args: argparse.Namespace, config: Config, io: IO, factory: ProviderFactory) -> int:
    session = _make_session(args, config, io, factory)
    io.stdout.write("guardrun chat - type /exit to quit\n")
    while True:
        io.stdout.write("you> ")
        io.stdout.flush()
        line = io.stdin.readline()
        if not line or line.strip() in {"/exit", "/quit"}:
            return EXIT_OK
        if line.strip():
            session.run(line.strip(), io)


def cmd_sessions(args: argparse.Namespace, io: IO) -> int:
    infos = _session_store(args.workspace).list_sessions()
    if not infos:
        io.stdout.write("no saved sessions\n")
    for info in infos:
        io.stdout.write(f"{info.id}  {info.updated}  {info.messages:>4} msgs  {info.title[:60]}\n")
    return EXIT_OK


def cmd_policy(args: argparse.Namespace, config: Config, io: IO) -> int:
    policy = CommandPolicy(
        allow_prefixes=config.shell_allow, blocked_programs=frozenset(config.shell_block)
    )
    assessment = policy.assess(args.shell_command, Workspace(args.workspace))
    reason = f": {assessment.reason}" if assessment.reason else ""
    io.stdout.write(f"{assessment.verdict.name}{reason}\n")
    return EXIT_OK


def cmd_audit(args: argparse.Namespace, io: IO) -> int:
    records = read_audit(args.workspace / STATE_DIR / "audit.jsonl")
    if not records:
        io.stdout.write("no audit records yet\n")
    for record in records[-args.tail :] if args.tail > 0 else []:
        data = json.dumps(record.get("data", {}), ensure_ascii=False)
        io.stdout.write(f"{record.get('ts', '')} {record.get('kind', '')} {data[:200]}\n")
    return EXIT_OK


def _format_run(run: RunSummary) -> str:
    cost = f"${run.cost_usd:.4f}" if run.cost_usd is not None else "-"
    tools = sum(run.tools.values())
    return (
        f"{run.started[:19]:19}  {run.stop:10} {run.turns:>3} turns "
        f"{run.usage.total_tokens:>9} tok {cost:>9}  {tools:>3} tools  {run.task[:40]}"
    )


def cmd_usage(args: argparse.Namespace, io: IO) -> int:
    runs = summarize_runs(read_audit(args.workspace / STATE_DIR / "audit.jsonl"))
    shown = runs[-args.last :] if args.last > 0 else []
    if not shown:
        io.stdout.write("no runs recorded yet\n")
        return EXIT_OK
    for run in shown:
        io.stdout.write(_format_run(run) + "\n")
    total_tokens = sum(r.usage.total_tokens for r in shown)
    total_cost = sum(r.cost_usd or 0.0 for r in shown)
    io.stdout.write(f"total: {len(shown)} runs, {total_tokens} tokens, ${total_cost:.4f}\n")
    return EXIT_OK


def cmd_init(args: argparse.Namespace, io: IO) -> int:
    path = args.workspace / CONFIG_FILENAME
    if path.exists():
        io.stderr.write(f"{path} already exists; not overwriting\n")
        return EXIT_USAGE
    path.write_text(STARTER_CONFIG, encoding="utf-8")
    io.stdout.write(f"wrote {path}\n")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, io: IO) -> int:
    checks = run_checks(args.workspace)
    for check in checks:
        io.stdout.write(f"[{check.status:4}] {check.name:12} {check.detail}\n")
    return EXIT_INCOMPLETE if any(c.status == FAIL for c in checks) else EXIT_OK


# Commands that only inspect local state and never need the config file.
_STATE_COMMANDS: dict[str, Callable[[argparse.Namespace, IO], int]] = {
    "doctor": cmd_doctor,
    "init": cmd_init,
    "audit": cmd_audit,
    "sessions": cmd_sessions,
    "usage": cmd_usage,
    "inbox": cmd_inbox,
}


def _dispatch(args: argparse.Namespace, io: IO, factory: ProviderFactory) -> int:
    if args.command in _STATE_COMMANDS:
        return _STATE_COMMANDS[args.command](args, io)
    if args.command == "mission" and args.mission_command == "status":
        return cmd_mission_status(args, io)
    config = apply_overrides(load_config(args.workspace), args)
    if args.command == "policy":
        return cmd_policy(args, config, io)
    if args.command == "chat":
        return cmd_chat(args, config, io, factory)
    if args.command == "mission":
        return cmd_mission(args, config, io, factory)
    return cmd_run(args, config, io, factory)


def main(
    argv: Sequence[str] | None = None,
    *,
    io: IO | None = None,
    provider_factory: ProviderFactory = default_provider,
) -> int:
    io = io or IO(sys.stdin, sys.stdout, sys.stderr, interactive=sys.stdin.isatty())
    args = build_parser().parse_args(argv)
    args.workspace = args.workspace.resolve()
    if not args.workspace.is_dir():
        io.stderr.write(f"guardrun: workspace {args.workspace} is not a directory\n")
        return EXIT_USAGE
    try:
        return _dispatch(args, io, provider_factory)
    except (ConfigError, GuardRunError) as exc:
        io.stderr.write(f"guardrun: {exc}\n")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
