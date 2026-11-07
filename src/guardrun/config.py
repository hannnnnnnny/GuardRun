"""Project configuration loaded from ``guardrun.toml``.

Unknown keys are errors rather than being ignored: a typo in a safety setting
(``shel.allow``) must not silently fall back to a default.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guardrun.dispatch import DEFAULT_MAX_CALLS_PER_TURN, DEFAULT_MAX_IDENTICAL_CALLS
from guardrun.errors import ConfigError
from guardrun.providers.anthropic import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    EFFORT_LEVELS,
)
from guardrun.providers.deepseek import DEFAULT_DEEPSEEK_MODEL
from guardrun.safety.modes import ApprovalMode
from guardrun.tools.executor import DEFAULT_MAX_OUTPUT_CHARS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 CI only
    import tomli as tomllib

CONFIG_FILENAME = "guardrun.toml"
# Same as guardrun.mission.plan.MAX_TASKS; importing it here would create a cycle.
MAX_MISSION_TASKS = 50
PROVIDER_DEFAULT_MODELS = {"deepseek": DEFAULT_DEEPSEEK_MODEL, "anthropic": DEFAULT_MODEL}


@dataclass(frozen=True)
class Config:
    provider: str = "deepseek"
    # Empty means the provider's default model (see ``resolved_model``).
    model: str = ""
    effort: str = DEFAULT_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS
    mode: ApprovalMode = ApprovalMode.ASK
    max_turns: int | None = 50
    max_cost_usd: float | None = None
    max_total_tokens: int | None = None
    shell_allow: tuple[str, ...] = ()
    shell_block: tuple[str, ...] = ()
    protect: tuple[str, ...] = ()
    unprotect: tuple[str, ...] = ()
    instructions: str = ""
    instructions_file: str = ""
    audit: bool = True
    network_enabled: bool = False
    network_allow: tuple[str, ...] = ()
    max_calls_per_turn: int = DEFAULT_MAX_CALLS_PER_TURN
    max_identical_calls: int = DEFAULT_MAX_IDENTICAL_CALLS
    parallel_reads: bool = True
    max_tool_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    mission_max_attempts: int = 3
    mission_max_tasks: int = 20
    mission_max_cost_usd: float | None = None
    source: Path | None = field(default=None, compare=False)

    @property
    def resolved_model(self) -> str:
        return self.model or PROVIDER_DEFAULT_MODELS[self.provider]


_SCHEMA: dict[str, set[str]] = {
    "": {
        "instructions",
        "instructions_file",
        "model",
        "safety",
        "shell",
        "budget",
        "network",
        "limits",
        "mission",
    },
    "model": {"provider", "name", "effort", "max_tokens"},
    "safety": {"mode", "protect", "unprotect", "audit"},
    "shell": {"allow", "block"},
    "budget": {"max_turns", "max_cost_usd", "max_total_tokens"},
    "network": {"enabled", "allow_domains"},
    "limits": {
        "max_calls_per_turn",
        "max_identical_calls",
        "parallel_reads",
        "max_tool_output_chars",
    },
    "mission": {"max_attempts", "max_tasks", "max_cost_usd"},
}


def _expect(value: Any, kind: type | tuple[type, ...], key: str) -> Any:
    # bool is an int subclass; a boolean where a number belongs is a mistake.
    if isinstance(value, bool) and kind in (int, float, (int, float)):
        raise ConfigError(f"{key} must be a number, not a boolean")
    if not isinstance(value, kind):
        raise ConfigError(f"{key} has the wrong type ({type(value).__name__})")
    return value


def _positive(value: Any, key: str) -> Any:
    _expect(value, (int, float), key)
    if value <= 0:
        raise ConfigError(f"{key} must be positive")
    return value


def _strings(value: Any, key: str) -> tuple[str, ...]:
    _expect(value, list, key)
    return tuple(_expect(item, str, f"{key}[]") for item in value)


def _check_keys(data: dict[str, Any], section: str) -> None:
    unknown = sorted(set(data) - _SCHEMA[section])
    if unknown:
        where = f"[{section}]" if section else "top level"
        raise ConfigError(f"unknown config keys at {where}: {', '.join(unknown)}")


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = _expect(data.get(name, {}), dict, f"[{name}]")
    _check_keys(section, name)
    return dict(section)


def _model_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "provider" in section:
        provider = _expect(section["provider"], str, "model.provider")
        if provider not in PROVIDER_DEFAULT_MODELS:
            raise ConfigError(f"model.provider must be one of {', '.join(PROVIDER_DEFAULT_MODELS)}")
        fields["provider"] = provider
    if "name" in section:
        fields["model"] = _expect(section["name"], str, "model.name")
    if "effort" in section:
        effort = _expect(section["effort"], str, "model.effort")
        if effort not in EFFORT_LEVELS:
            raise ConfigError(f"model.effort must be one of {', '.join(EFFORT_LEVELS)}")
        fields["effort"] = effort
    if "max_tokens" in section:
        fields["max_tokens"] = int(_positive(section["max_tokens"], "model.max_tokens"))
    return fields


def _safety_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "mode" in section:
        mode = _expect(section["mode"], str, "safety.mode")
        try:
            fields["mode"] = ApprovalMode(mode)
        except ValueError as exc:
            choices = ", ".join(m.value for m in ApprovalMode)
            raise ConfigError(f"safety.mode must be one of {choices}") from exc
    for key in ("protect", "unprotect"):
        if key in section:
            fields[key] = _strings(section[key], f"safety.{key}")
    if "audit" in section:
        fields["audit"] = _expect(section["audit"], bool, "safety.audit")
    return fields


def _shell_fields(section: dict[str, Any]) -> dict[str, Any]:
    return {
        f"shell_{key}": _strings(section[key], f"shell.{key}")
        for key in ("allow", "block")
        if key in section
    }


def _budget_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("max_turns", "max_total_tokens"):
        if key in section:
            fields[key] = int(_positive(section[key], f"budget.{key}"))
    if "max_cost_usd" in section:
        fields["max_cost_usd"] = float(_positive(section["max_cost_usd"], "budget.max_cost_usd"))
    return fields


def _network_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "enabled" in section:
        fields["network_enabled"] = _expect(section["enabled"], bool, "network.enabled")
    if "allow_domains" in section:
        fields["network_allow"] = _strings(section["allow_domains"], "network.allow_domains")
    return fields


def _limits_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("max_calls_per_turn", "max_identical_calls", "max_tool_output_chars"):
        if key in section:
            fields[key] = int(_positive(_expect(section[key], int, f"limits.{key}"), key))
    if "parallel_reads" in section:
        fields["parallel_reads"] = _expect(section["parallel_reads"], bool, "limits.parallel_reads")
    return fields


def _mission_fields(section: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("max_attempts", "max_tasks"):
        if key in section:
            value = _positive(_expect(section[key], int, f"mission.{key}"), f"mission.{key}")
            if key == "max_tasks" and value > MAX_MISSION_TASKS:
                raise ConfigError(f"mission.max_tasks may be at most {MAX_MISSION_TASKS}")
            fields[f"mission_{key}"] = int(value)
    if "max_cost_usd" in section:
        cost = _positive(section["max_cost_usd"], "mission.max_cost_usd")
        fields["mission_max_cost_usd"] = float(cost)
    return fields


def parse_config(data: dict[str, Any], source: Path | None = None) -> Config:
    _check_keys(data, "")
    fields: dict[str, Any] = {}
    fields.update(_model_fields(_section(data, "model")))
    fields.update(_safety_fields(_section(data, "safety")))
    fields.update(_shell_fields(_section(data, "shell")))
    fields.update(_budget_fields(_section(data, "budget")))
    fields.update(_network_fields(_section(data, "network")))
    fields.update(_limits_fields(_section(data, "limits")))
    fields.update(_mission_fields(_section(data, "mission")))
    for key in ("instructions", "instructions_file"):
        if key in data:
            fields[key] = _expect(data[key], str, key)
    return Config(**fields, source=source)


def load_config(root: Path) -> Config:
    """Load ``guardrun.toml`` from the workspace root, or defaults if absent."""
    path = root / CONFIG_FILENAME
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc
    return parse_config(data, source=path)
