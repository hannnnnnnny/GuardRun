"""Running shell commands with timeouts, output caps, and process-tree cleanup."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from guardrun.text import truncate_middle

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_CHARS = 30_000


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    output: str
    timed_out: bool = False


def _is_wsl_launcher(directory: Path, environ: Mapping[str, str]) -> bool:
    """``bash.exe`` in the Windows directory or WindowsApps starts WSL, not a
    local shell: it runs in another filesystem and environment, and fails outright
    when no Linux distribution is installed."""
    parts = [part.lower() for part in directory.parts]
    if "windowsapps" in parts:
        return True
    system_root = environ.get("SYSTEMROOT") or environ.get("SystemRoot") or r"C:\Windows"
    root_parts = [part.lower() for part in Path(system_root).parts]
    return parts[: len(root_parts)] == root_parts


def _git_bash_candidates(path_dirs: list[Path], environ: Mapping[str, str]) -> list[Path]:
    """Where Git for Windows keeps bash, when its ``bin`` is not itself on PATH."""
    candidates = []
    for directory in path_dirs:
        if (directory / "git.exe").is_file():
            # Git\cmd\git.exe and Git\mingw64\bin\git.exe both sit below Git\.
            candidates += [directory.parent / "bin", directory.parent.parent / "bin"]
    for var, sub in (("ProgramFiles", "Git"), ("ProgramFiles(x86)", "Git")):
        if environ.get(var):
            candidates.append(Path(environ[var]) / sub / "bin")
    if environ.get("LOCALAPPDATA"):
        candidates.append(Path(environ["LOCALAPPDATA"]) / "Programs" / "Git" / "bin")
    return [directory / "bash.exe" for directory in candidates]


def _find_windows_shell(environ: Mapping[str, str]) -> str | None:
    path_dirs = [Path(d) for d in environ.get("PATH", "").split(os.pathsep) if d]
    for name in ("bash.exe", "sh.exe"):
        for directory in path_dirs:
            if (directory / name).is_file() and not _is_wsl_launcher(directory, environ):
                return str(directory / name)
    for candidate in _git_bash_candidates(path_dirs, environ):
        if candidate.is_file():
            return str(candidate)
    return None


def find_posix_shell(
    environ: Mapping[str, str] | None = None, *, windows: bool = os.name == "nt"
) -> str | None:
    """The bash or sh that runs commands, or None.

    On Windows the WSL launchers are skipped in favour of Git Bash.
    """
    env = os.environ if environ is None else environ
    if windows:
        return _find_windows_shell(env)
    for name in ("bash", "sh"):
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return found
    return None


def default_shell() -> list[str]:
    """The argv prefix used to run a command string.

    A POSIX shell is preferred everywhere because the command policy parses
    POSIX syntax; cmd.exe is only a last resort on Windows.
    """
    found = find_posix_shell()
    if found:
        return [found, "-c"]
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c"]
    raise RuntimeError("no shell found on PATH")


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        # taskkill /T also terminates grandchildren that Popen.kill would orphan.
        subprocess.run(  # noqa: S603 - fixed argv, pid is an int we own
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    proc.kill()


def run_command(
    command: str,
    cwd: Path,
    env: dict[str, str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    shell: list[str] | None = None,
) -> ProcessResult:
    """Run ``command`` and return its combined stdout/stderr.

    The command has already been approved by the policy layer; this function
    only bounds its resources. stdin is closed so interactive prompts fail fast.
    """
    argv = [*(shell or default_shell()), command]
    popen_kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(  # noqa: S603 - command vetted by CommandPolicy
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **popen_kwargs,  # type: ignore[call-overload]
    )
    try:
        raw, _ = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raw, _ = proc.communicate()
        timed_out = True
    output = truncate_middle(raw.decode("utf-8", errors="replace"), max_output_chars)
    return ProcessResult(None if timed_out else proc.returncode, output, timed_out)
