from pathlib import Path

from guardrun.mission.checks import CheckResult, CheckRunner, format_checks
from guardrun.safety.redact import Redactor


def test_passing_failing_and_blocked_checks(tmp_path: Path) -> None:
    runner = CheckRunner(tmp_path)
    ok, failed, blocked = runner.run(["echo ok", "ls no-such-dir", "sudo ls"])
    assert ok.passed
    assert not ok.blocked
    assert "ok" in ok.output
    assert not failed.passed
    assert not failed.blocked
    assert blocked.blocked
    assert not blocked.passed


def test_check_output_is_redacted(tmp_path: Path) -> None:
    secret = "tok-0123456789abcdefghijkl"
    runner = CheckRunner(tmp_path, redactor=Redactor(known_secrets={secret}))
    (result,) = runner.run([f"echo {secret}"])
    assert secret not in result.output


def test_format_checks() -> None:
    assert "no automated checks" in format_checks([])
    text = format_checks(
        [
            CheckResult("a", True, "[exit code 0]"),
            CheckResult("b", False, "[exit code 1]"),
            CheckResult("c", False, "blocked", blocked=True),
        ]
    )
    assert "$ a  -> PASS" in text
    assert "$ b  -> FAIL" in text
    assert "$ c  -> BLOCKED" in text
