from pathlib import Path

from guardrun.mission.inbox import BLOCKER, MAX_REASON_CHARS, WARNING, Inbox, Notice
from guardrun.safety.redact import Redactor


def test_post_and_read(tmp_path: Path) -> None:
    inbox = Inbox(tmp_path / "state" / "inbox.jsonl")
    assert inbox.read() == []
    heard: list[Notice] = []
    inbox.notifiers.append(heard.append)
    inbox.post("m1", "t1", BLOCKER, "  need a key  ")
    inbox.post("m1", "", WARNING, "x" * (MAX_REASON_CHARS + 50))
    notices = inbox.read()
    assert [n.level for n in notices] == [BLOCKER, WARNING]
    assert notices[0].reason == "need a key"
    assert len(notices[1].reason) == MAX_REASON_CHARS
    assert heard == notices


def test_secrets_are_redacted(tmp_path: Path) -> None:
    secret = "sk-live-0123456789abcdefghij"
    inbox = Inbox(tmp_path / "inbox.jsonl", Redactor(known_secrets={secret}))
    inbox.post("m", "t", BLOCKER, f"the key {secret} failed")
    assert secret not in (tmp_path / "inbox.jsonl").read_text()


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "inbox.jsonl"
    inbox = Inbox(path)
    inbox.post("m", "t", BLOCKER, "real")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('not json\n[1, 2]\n{"unexpected": true}\n')
    assert [n.reason for n in inbox.read()] == ["real"]
