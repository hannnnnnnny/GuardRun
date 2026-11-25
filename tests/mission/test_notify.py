import os
from pathlib import Path

import pytest

from guardrun.mission import CommandNotifier, Inbox, Notice
from guardrun.mission.notify import notice_env


def _env(**extra: str) -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "PATHEXT")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(extra)
    return env


def test_notice_reaches_the_command_through_the_environment(tmp_path: Path) -> None:
    errors: list[str] = []
    command = 'printf "%s|%s" "$GUARDRUN_NOTICE_LEVEL" "$GUARDRUN_NOTICE_REASON" > out.txt'
    notifier = CommandNotifier(command, tmp_path, errors.append, _env())
    inbox = Inbox(tmp_path / "inbox.jsonl")
    inbox.notifiers.append(notifier)
    inbox.post("m1", "t1", "blocker", "needs $(touch pwned) a key")
    assert errors == []
    assert (tmp_path / "out.txt").read_text() == "blocker|needs $(touch pwned) a key"
    assert not (tmp_path / "pwned").exists()


def test_secrets_are_not_passed_to_the_command(tmp_path: Path) -> None:
    command = 'printf "%s" "${DEEPSEEK_API_KEY:-none}" > out.txt'
    notifier = CommandNotifier(
        command, tmp_path, pytest.fail, _env(DEEPSEEK_API_KEY="sk-should-not-leak")
    )
    notifier(Notice("m", "t", "blocker", "x"))
    assert (tmp_path / "out.txt").read_text() == "none"


def test_failures_are_reported_not_raised(tmp_path: Path) -> None:
    errors: list[str] = []
    CommandNotifier("echo nope; exit 3", tmp_path, errors.append, _env())(
        Notice("m", "t", "warning", "x")
    )
    assert errors == ["notify_command exited with 3: nope"]


def test_notice_env() -> None:
    env = notice_env(Notice("m", "t", "warning", "why", ts="2026-01-01T00:00:00+00:00"))
    assert env["GUARDRUN_NOTICE_MISSION"] == "m"
    assert env["GUARDRUN_NOTICE_TIME"].startswith("2026")


def test_inbox_read_marker(tmp_path: Path) -> None:
    inbox = Inbox(tmp_path / "inbox.jsonl")
    inbox.post("m", "a", "blocker", "one")
    inbox.post("m", "b", "blocker", "two")
    assert [len(part) for part in inbox.unread()] == [0, 2]
    inbox.mark_seen(1)
    assert [len(part) for part in inbox.unread()] == [1, 1]
    inbox.mark_seen(5)  # the inbox was cleared by hand: everything is new again
    assert [len(part) for part in inbox.unread()] == [0, 2]
    (tmp_path / "inbox.jsonl.seen").write_text("garbage")
    assert inbox.seen_count() == 0
