"""The user's inbox: the few things a running mission cannot decide on its own.

Everything else (routine retries, QA feedback, passed tasks) stays in the
mission record, so the inbox is short enough to actually be read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from guardrun.safety.redact import Redactor

BLOCKER = "blocker"
"""Work stopped until a person acts."""
WARNING = "warning"
"""Work continued, but a person should look (e.g. suspected prompt injection)."""

MAX_REASON_CHARS = 2000


@dataclass(frozen=True)
class Notice:
    mission: str
    task: str
    level: str
    reason: str
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


Notifier = Callable[[Notice], None]


class Inbox:
    """An append-only JSONL file plus optional live notifiers (e.g. the console)."""

    def __init__(self, path: Path, redactor: Redactor | None = None) -> None:
        self.path = path
        self.redactor = redactor or Redactor()
        self.notifiers: list[Notifier] = []

    def post(self, mission: str, task: str, level: str, reason: str) -> Notice:
        # Reasons can quote tool output, so secrets are scrubbed before they are stored.
        text = self.redactor.redact(reason.strip())[:MAX_REASON_CHARS]
        notice = Notice(mission, task, level, text)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(notice), ensure_ascii=False) + "\n")
        for notify in self.notifiers:
            notify(notice)
        return notice

    def read(self) -> list[Notice]:
        """All notices, oldest first; malformed lines are skipped."""
        if not self.path.exists():
            return []
        notices = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            notice = _parse(line)
            if notice is not None:
                notices.append(notice)
        return notices


def _parse(line: str) -> Notice | None:
    try:
        data: Any = json.loads(line)
        return Notice(**data) if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None
