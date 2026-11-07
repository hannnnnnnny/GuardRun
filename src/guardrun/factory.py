"""Assemble a fully wired, safe-by-default agent."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guardrun.agent import Agent
from guardrun.budget import Budget
from guardrun.config import Config
from guardrun.errors import ConfigError
from guardrun.events import EventBus
from guardrun.instructions import combine_instructions, load_instructions_file
from guardrun.messages import Conversation
from guardrun.prompts import build_system_prompt
from guardrun.providers.base import Provider
from guardrun.safety.approval import Approver, deny_all
from guardrun.safety.audit import AuditLog
from guardrun.safety.command_policy import CommandPolicy
from guardrun.safety.gate import SafetyGate
from guardrun.safety.injection import make_injection_filter
from guardrun.safety.modes import ApprovalMode
from guardrun.safety.protected import DEFAULT_NO_READ, DEFAULT_NO_WRITE, ProtectedPaths
from guardrun.safety.redact import Redactor
from guardrun.safety.workspace import Workspace
from guardrun.tools.base import Tool, ToolContext
from guardrun.tools.builtin import default_tools
from guardrun.tools.builtin.web_fetch import WebFetch
from guardrun.tools.executor import DEFAULT_MAX_OUTPUT_CHARS, Gate
from guardrun.tools.registry import ToolRegistry
from guardrun.web import Fetcher

STATE_DIR = ".guardrun"


@dataclass
class AgentOptions:
    """Everything configurable about an agent, with conservative defaults."""

    mode: ApprovalMode = ApprovalMode.ASK
    approver: Approver = deny_all
    budget: Budget = field(default_factory=Budget)
    command_policy: CommandPolicy = field(default_factory=CommandPolicy)
    protected: ProtectedPaths = field(default_factory=ProtectedPaths)
    # Replaces the built-in tool set when given (e.g. read-only tools for a reviewer).
    tools: list[Tool] | None = None
    extra_tools: list[Tool] = field(default_factory=list)
    # Project-specific checks, run after the built-in SafetyGate has allowed a call.
    extra_gates: list[Gate] = field(default_factory=list)
    instructions: str = ""
    audit: bool = True
    events: EventBus = field(default_factory=EventBus)
    conversation: Conversation | None = None
    dispatcher_options: dict[str, Any] = field(default_factory=dict)
    max_tool_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS


def build_agent(root: Path | str, provider: Provider, options: AgentOptions | None = None) -> Agent:
    """Wire tools, the safety gate, redaction, injection flagging, and auditing."""
    opts = options or AgentOptions()
    workspace = Workspace(root, opts.protected)
    ctx = ToolContext(workspace=workspace)
    redactor = Redactor.from_environment(os.environ)
    if opts.audit:
        run_id = uuid.uuid4().hex[:12]
        audit_path = workspace.root / STATE_DIR / "audit.jsonl"
        opts.events.subscribe(AuditLog(audit_path, redactor, run_id))
    injection_filter = make_injection_filter(
        lambda phrase: opts.events.emit("injection_suspected", phrase=phrase)
    )
    base = default_tools(opts.command_policy) if opts.tools is None else opts.tools
    tools = ToolRegistry([*base, *opts.extra_tools])
    gate = SafetyGate(ctx, opts.mode, opts.approver, opts.events)
    return Agent(
        provider,
        tools,
        ctx,
        gates=[gate, *opts.extra_gates],
        output_filters=[redactor.redact, injection_filter],
        system_prompt=build_system_prompt(opts.instructions),
        budget=opts.budget,
        events=opts.events,
        conversation=opts.conversation,
        dispatcher_options=opts.dispatcher_options,
        max_tool_output_chars=opts.max_tool_output_chars,
    )


# Tampering with these would let the agent rewrite its own audit trail or git hooks.
_NEVER_UNPROTECT = (".guardrun", ".git")


def protected_paths_from(protect: tuple[str, ...], unprotect: tuple[str, ...]) -> ProtectedPaths:
    for pattern in unprotect:
        normalized = pattern.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.lower().startswith(_NEVER_UNPROTECT):
            raise ConfigError(
                f"refusing to unprotect {pattern!r}: "
                "guardrun state and git internals stay protected"
            )
    return ProtectedPaths(
        no_read=(*DEFAULT_NO_READ, *protect),
        no_write=(*DEFAULT_NO_WRITE, *protect),
        allow=unprotect,
    )


def options_from_config(
    config: Config, approver: Approver = deny_all, root: Path | None = None
) -> AgentOptions:
    """Translate a loaded :class:`Config` into :class:`AgentOptions`.

    ``root`` is needed only to load ``instructions_file`` from the workspace.
    """
    from_file = ""
    if config.instructions_file and root is not None:
        from_file = load_instructions_file(root, config.instructions_file)
    policy = CommandPolicy(
        allow_prefixes=config.shell_allow,
        blocked_programs=frozenset(config.shell_block),
    )
    return AgentOptions(
        mode=config.mode,
        approver=approver,
        budget=Budget(
            max_turns=config.max_turns,
            max_total_tokens=config.max_total_tokens,
            max_cost_usd=config.max_cost_usd,
        ),
        command_policy=policy,
        protected=protected_paths_from(config.protect, config.unprotect),
        instructions=combine_instructions(config.instructions, from_file),
        audit=config.audit,
        extra_tools=_network_tools(config),
        dispatcher_options={
            "max_calls_per_turn": config.max_calls_per_turn,
            "max_identical_calls": config.max_identical_calls,
            "parallel_reads": config.parallel_reads,
        },
        max_tool_output_chars=config.max_tool_output_chars,
    )


def _network_tools(config: Config) -> list[Tool]:
    if not config.network_enabled:
        return []
    return [WebFetch(Fetcher(allowed_domains=config.network_allow))]
