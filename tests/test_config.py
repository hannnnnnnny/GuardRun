from pathlib import Path
from typing import Any

import pytest

from guardrun.config import (
    CONFIG_FILENAME,
    MAX_MISSION_TASKS,
    Config,
    load_config,
    parse_config,
)
from guardrun.errors import ConfigError
from guardrun.mission.plan import MAX_TASKS
from guardrun.safety.modes import ApprovalMode

FULL = """
instructions = "Run pytest before finishing."

[model]
provider = "anthropic"
name = "claude-sonnet-5-5"
effort = "medium"
max_tokens = 32000

[safety]
mode = "auto-edit"
protect = ["secrets/**"]
unprotect = [".env.example"]
audit = false

[shell]
allow = ["pytest", "npm test"]
block = ["docker"]

[budget]
max_turns = 20
max_cost_usd = 2.5
max_total_tokens = 1000000

[network]
enabled = true
allow_domains = ["python.org"]
"""


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path) == Config()


def test_full_config(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(FULL)
    config = load_config(tmp_path)
    assert config.provider == "anthropic"
    assert config.model == "claude-sonnet-5-5"
    assert config.effort == "medium"
    assert config.max_tokens == 32000
    assert config.mode is ApprovalMode.AUTO_EDIT
    assert config.protect == ("secrets/**",)
    assert config.unprotect == (".env.example",)
    assert config.audit is False
    assert config.shell_allow == ("pytest", "npm test")
    assert config.shell_block == ("docker",)
    assert config.max_turns == 20
    assert config.max_cost_usd == 2.5
    assert config.max_total_tokens == 1_000_000
    assert config.instructions == "Run pytest before finishing."
    assert config.network_enabled is True
    assert config.network_allow == ("python.org",)
    assert config.source == tmp_path / CONFIG_FILENAME


def test_invalid_toml(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("[model\nname=")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"shel": {}}, "unknown config keys at top level: shel"),
        ({"shell": {"alow": []}}, r"unknown config keys at \[shell\]: alow"),
        ({"model": {"effort": "extreme"}}, "model.effort must be one of"),
        ({"model": {"max_tokens": 0}}, "must be positive"),
        ({"model": {"max_tokens": True}}, "not a boolean"),
        ({"safety": {"mode": "yolo"}}, "safety.mode must be one of"),
        ({"safety": {"audit": "no"}}, "wrong type"),
        ({"shell": {"allow": "pytest"}}, "wrong type"),
        ({"shell": {"allow": [1]}}, "wrong type"),
        ({"budget": {"max_cost_usd": -1}}, "must be positive"),
        ({"model": "opus"}, "wrong type"),
        ({"instructions": 5}, "wrong type"),
        ({"network": {"enabled": "yes"}}, "wrong type"),
        ({"network": {"allow": []}}, "unknown config keys"),
    ],
)
def test_invalid_config_is_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_integer_cost_is_accepted() -> None:
    assert parse_config({"budget": {"max_cost_usd": 3}}).max_cost_usd == 3.0


def test_limits_section() -> None:
    config = parse_config(
        {"limits": {"max_calls_per_turn": 5, "max_identical_calls": 2, "parallel_reads": False}}
    )
    assert (config.max_calls_per_turn, config.max_identical_calls) == (5, 2)
    assert config.parallel_reads is False


@pytest.mark.parametrize(
    "limits",
    [{"max_calls_per_turn": 0}, {"max_identical_calls": 1.5}, {"parallel_reads": 1}],
)
def test_invalid_limits(limits: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        parse_config({"limits": limits})


def test_instructions_file_key() -> None:
    assert parse_config({"instructions_file": "AGENTS.md"}).instructions_file == "AGENTS.md"
    with pytest.raises(ConfigError, match="wrong type"):
        parse_config({"instructions_file": ["AGENTS.md"]})


def test_default_provider_is_deepseek() -> None:
    config = Config()
    assert config.provider == "deepseek"
    assert config.resolved_model == "deepseek-v4-pro"


def test_explicit_model_wins_over_provider_default() -> None:
    config = parse_config({"model": {"provider": "anthropic"}})
    assert config.resolved_model == "claude-opus-5-5"
    assert parse_config({"model": {"name": "deepseek-flash"}}).resolved_model == "deepseek-flash"


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ConfigError, match=r"model\.provider must be one of"):
        parse_config({"model": {"provider": "openai"}})


def test_mission_section() -> None:
    config = parse_config({"mission": {"max_attempts": 5, "max_tasks": 50, "max_cost_usd": 20}})
    assert config.mission_max_attempts == 5
    assert config.mission_max_tasks == 50
    assert config.mission_max_cost_usd == 20.0
    defaults = parse_config({})
    assert (defaults.mission_max_attempts, defaults.mission_max_tasks) == (3, 20)
    assert defaults.mission_max_cost_usd is None


@pytest.mark.parametrize(
    "mission",
    [
        {"max_attempts": 0},
        {"max_attempts": 1.5},
        {"max_tasks": 51},
        {"max_cost_usd": -1},
        {"retries": 2},
    ],
)
def test_invalid_mission_section(mission: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        parse_config({"mission": mission})


def test_mission_task_cap_matches_the_plan_limit() -> None:
    assert MAX_MISSION_TASKS == MAX_TASKS
