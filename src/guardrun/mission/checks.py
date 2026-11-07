"""Automated checks: shell commands whose exit code decides part of QA.

Checks run through the same command policy and safety gate as the agent's own
``run_shell`` calls, in unattended mode, so a plan cannot smuggle in a command
the policy would not run without a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from guardrun.messages import ToolCall
from guardrun.safety.command_policy import CommandPolicy
from guardrun.safety.gate import SafetyGate
from guardrun.safety.modes import ApprovalMode
from guardrun.safety.protected import ProtectedPaths
from guardrun.safety.redact import Redactor
from guardrun.safety.workspace import Workspace
from guardrun.tools.base import ToolContext
from guardrun.tools.builtin.run_shell import RunShell
from guardrun.tools.executor import ToolExecutor
from guardrun.tools.registry import ToolRegistry

MAX_CHECK_OUTPUT_CHARS = 4000
_PASSED_PREFIX = "[exit code 0]"
_BLOCKED_PREFIX = "blocked by safety policy"


@dataclass(frozen=True)
class CheckResult:
    command: str
    passed: bool
    output: str
    blocked: bool = False


class CheckRunner:
    def __init__(
        self,
        root: Path,
        policy: CommandPolicy | None = None,
        protected: ProtectedPaths | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        ctx = ToolContext(workspace=Workspace(root, protected))
        self._executor = ToolExecutor(
            ToolRegistry([RunShell(policy)]),
            ctx,
            gates=[SafetyGate(ctx, ApprovalMode.UNATTENDED)],
            max_output_chars=MAX_CHECK_OUTPUT_CHARS,
            output_filters=[(redactor or Redactor()).redact],
        )

    def run(self, commands: list[str]) -> list[CheckResult]:
        return [self._run_one(index, command) for index, command in enumerate(commands)]

    def _run_one(self, index: int, command: str) -> CheckResult:
        call = ToolCall(f"check_{index}", "run_shell", {"command": command})
        result = self._executor.execute(call)
        blocked = result.is_error and result.content.startswith(_BLOCKED_PREFIX)
        passed = not result.is_error and result.content.startswith(_PASSED_PREFIX)
        return CheckResult(command, passed, result.content, blocked)


def format_checks(results: list[CheckResult]) -> str:
    if not results:
        return "(no automated checks for this task)"
    parts = []
    for result in results:
        status = "BLOCKED" if result.blocked else ("PASS" if result.passed else "FAIL")
        parts.append(f"$ {result.command}  -> {status}\n{result.output}")
    return "\n\n".join(parts)
