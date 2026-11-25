"""``guardrun chat``: a multi-turn session with a few slash commands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from guardrun import __version__
from guardrun.config import Config

if TYPE_CHECKING:
    from guardrun.cli.main import IO, CliSession

HELP = """commands:
  /help          show this list
  /usage         tokens and cost of this chat so far
  /session       this session's id and how to resume it later
  /new           start a fresh conversation (a new session)
  /exit, /quit   leave (Ctrl+D or Ctrl+C at the prompt also work)
Anything else goes to the agent. Ctrl+C during a reply stops only that reply.
Start a message with // to send text that begins with /."""

_EXIT = {"/exit", "/quit", "/q"}


def banner(config: Config, workspace: Path, io: IO) -> str:
    s = io.style
    title = s.bold(f"GuardRun {__version__}")
    facts = f"{config.resolved_model} · mode {config.mode.value} · {workspace}"
    return f"{title} {s.dim(facts)}\n{s.dim('type /help for commands, /exit to leave')}\n"


def _read(io: IO) -> str | None:
    """One line from the user; None when they leave with Ctrl+D or Ctrl+C."""
    io.stdout.write(io.style.accent("you> "))
    io.stdout.flush()
    try:
        line = io.stdin.readline()
    except KeyboardInterrupt:
        return None
    return line if line else None


def usage_line(session: CliSession) -> str:
    return (
        f"{session.runs} replies, {session.usage.total_tokens} tokens "
        f"(in {session.usage.input_tokens}, out {session.usage.output_tokens}), "
        f"~${session.cost_usd:.4f}"
    )


def _command(
    text: str, session: CliSession, new_session: Callable[[], CliSession], io: IO
) -> CliSession | None:
    """Handle a slash command; returns the session to continue with, None to leave."""
    name = text.split(maxsplit=1)[0].lower()
    if name in _EXIT:
        return None
    if name == "/help":
        io.stdout.write(HELP + "\n")
    elif name == "/usage":
        io.stdout.write(usage_line(session) + "\n")
    elif name == "/session":
        io.stdout.write(f"session {session.id}; resume with: guardrun chat --resume {session.id}\n")
    elif name == "/new":
        session = new_session()
        io.stdout.write(io.style.dim(f"new conversation, session {session.id}") + "\n")
    else:
        io.stdout.write(io.style.warn(f"unknown command {name}; type /help") + "\n")
    return session


def run_chat(new_session: Callable[[], CliSession], config: Config, workspace: Path, io: IO) -> int:
    session = new_session()
    io.stdout.write(banner(config, workspace, io))
    while True:
        line = _read(io)
        if line is None:
            io.stdout.write("\nbye\n")
            return 0
        text = line.strip()
        if not text:
            continue
        if text.startswith("//"):
            text = text[1:]
        elif text.startswith("/"):
            next_session = _command(text, session, new_session, io)
            if next_session is None:
                return 0
            session = next_session
            continue
        session.run(text, io)
