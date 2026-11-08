from pathlib import Path

from guardrun.config import CONFIG_FILENAME
from guardrun.doctor import (
    FAIL,
    OK,
    WARN,
    check_config,
    check_credentials,
    check_python,
    check_shell,
    run_checks,
)


def test_python_check_passes_on_supported_versions() -> None:
    assert check_python().status == OK


def test_credentials_are_detected_without_revealing_them(tmp_path: Path) -> None:
    check = check_credentials({"ANTHROPIC_API_KEY": "sk-ant-secret-value"}, tmp_path)
    assert check.status == OK
    assert "sk-ant" not in check.detail


def test_profile_directory_counts_as_credentials(tmp_path: Path) -> None:
    (tmp_path / ".config" / "anthropic").mkdir(parents=True)
    assert check_credentials({}, tmp_path).status == OK


def test_missing_credentials_warn(tmp_path: Path) -> None:
    check = check_credentials({"ANTHROPIC_API_KEY": ""}, tmp_path)
    assert check.status == WARN
    assert "ant auth login" in check.detail


def test_shell_check() -> None:
    found = check_shell(lambda: "/bin/bash")
    assert found.status == OK
    assert found.detail == "bash at /bin/bash"
    assert check_shell(lambda: None).status == WARN


def test_config_check(tmp_path: Path) -> None:
    assert check_config(tmp_path).status == OK
    (tmp_path / CONFIG_FILENAME).write_text("[typo]\n")
    failed = check_config(tmp_path)
    assert failed.status == FAIL
    assert "unknown config keys" in failed.detail


def test_run_checks_covers_everything(tmp_path: Path) -> None:
    names = [c.name for c in run_checks(tmp_path, {})]
    assert names == ["python", "credentials", "shell", "config", "workspace"]


def test_deepseek_credentials(tmp_path: Path) -> None:
    ok = check_credentials({"DEEPSEEK_API_KEY": "sk-deepseek-secret"}, tmp_path, "deepseek")
    assert ok.status == OK
    assert "secret" not in ok.detail
    missing = check_credentials({"ANTHROPIC_API_KEY": "x"}, tmp_path, "deepseek")
    assert missing.status == WARN
    assert "DEEPSEEK_API_KEY" in missing.detail


def test_run_checks_follows_the_configured_provider(tmp_path: Path) -> None:
    checks = {c.name: c for c in run_checks(tmp_path, {"DEEPSEEK_API_KEY": "k"})}
    assert checks["credentials"].status == OK
    assert "provider=deepseek" in checks["config"].detail
