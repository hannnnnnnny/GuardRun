"""Exception hierarchy for guardrun.

Every error raised by the harness derives from :class:`GuardRunError` so callers
can catch harness failures without swallowing unrelated exceptions.
"""

from __future__ import annotations


class GuardRunError(Exception):
    """Base class for all guardrun errors."""


class ConfigError(GuardRunError):
    """Raised when configuration is missing or invalid."""


class ToolError(GuardRunError):
    """Raised by a tool when it cannot complete; reported back to the model."""


class ToolInputError(ToolError):
    """Raised when tool input fails schema validation."""


class SafetyError(GuardRunError):
    """Base class for refusals made by the safety layer."""


class SandboxViolation(SafetyError):
    """Raised when an action would touch something outside the workspace."""


class PolicyViolation(SafetyError):
    """Raised when a command or action is blocked by policy."""


class ApprovalDenied(SafetyError):
    """Raised when a human (or approval policy) declines an action."""


class BudgetExceeded(GuardRunError):
    """Raised when a run exceeds its turn, token, or cost budget."""


class ProviderError(GuardRunError):
    """Raised when the model provider fails in a way the loop cannot recover from.

    ``transient`` marks outages (rate limits, overload, server errors, network
    trouble) that the SDK's own quick retries did not ride out but that waiting
    longer may fix, as opposed to problems like a bad key or a rejected request.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient
