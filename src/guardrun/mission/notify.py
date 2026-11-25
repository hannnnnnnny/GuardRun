"""Push inbox notices somewhere a person will see them (phone, chat, desktop).

``[mission] notify_command`` is a shell command from the user's own config. The
notice reaches it only through environment variables, never by pasting text
into the command line, so model-written reasons cannot inject shell syntax.
The command gets the same scrubbed environment as the agent's shell, so API
keys are not visible to it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from guardrun.mission.inbox import Notice
from guardrun.safety.env import scrub_env
from guardrun.safety.process import run_command

NOTIFY_TIMEOUT_SECONDS = 30
MAX_NOTIFY_OUTPUT_CHARS = 2000


def notice_env(notice: Notice) -> dict[str, str]:
    return {
        "GUARDRUN_NOTICE_LEVEL": notice.level,
        "GUARDRUN_NOTICE_MISSION": notice.mission,
        "GUARDRUN_NOTICE_TASK": notice.task,
        "GUARDRUN_NOTICE_REASON": notice.reason,
        "GUARDRUN_NOTICE_TIME": notice.ts,
    }


class CommandNotifier:
    """Runs ``command`` once per notice; a failure is reported, never raised,
    because a broken notifier must not stop a mission."""

    def __init__(
        self,
        command: str,
        root: Path,
        on_error: Callable[[str], None],
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.command = command
        self.root = root
        self.on_error = on_error
        self.environ = os.environ if environ is None else environ

    def __call__(self, notice: Notice) -> None:
        env = scrub_env(self.environ, extra=notice_env(notice))
        try:
            result = run_command(
                self.command,
                self.root,
                env,
                timeout=NOTIFY_TIMEOUT_SECONDS,
                max_output_chars=MAX_NOTIFY_OUTPUT_CHARS,
            )
        except (OSError, RuntimeError) as exc:
            self.on_error(f"notify_command could not start: {exc}")
            return
        if result.timed_out:
            self.on_error(f"notify_command timed out after {NOTIFY_TIMEOUT_SECONDS}s")
        elif result.exit_code != 0:
            output = " ".join(result.output.split())[:200]
            self.on_error(f"notify_command exited with {result.exit_code}: {output}")
