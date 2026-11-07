"""guardrun: a powerful, safety-first agent harness for DeepSeek and Claude models."""

from guardrun.agent import Agent, RunResult
from guardrun.budget import Budget
from guardrun.factory import AgentOptions, build_agent
from guardrun.safety.modes import ApprovalMode

__version__ = "0.2.0"

__all__ = [
    "Agent",
    "AgentOptions",
    "ApprovalMode",
    "Budget",
    "RunResult",
    "__version__",
    "build_agent",
]
