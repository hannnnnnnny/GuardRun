import os
import sys
from pathlib import Path

import pytest

from guardrun.safety.process import default_shell, find_posix_shell, run_command

PY = sys.executable.replace("\\", "/")


def _env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP")
    return {k: v for k, v in os.environ.items() if k.upper() in keep}


def test_default_shell_is_found() -> None:
    assert default_shell()


def test_captures_stdout_and_stderr(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr)"
    result = run_command(f'"{PY}" -c "{code}"', tmp_path, _env())
    assert result.exit_code == 0
    assert "out" in result.output
    assert "err" in result.output


def test_nonzero_exit_code(tmp_path: Path) -> None:
    result = run_command(f'"{PY}" -c "raise SystemExit(3)"', tmp_path, _env())
    assert result.exit_code == 3
    assert not result.timed_out


def test_runs_in_working_directory(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("")
    result = run_command(f'"{PY}" -c "import os; print(os.listdir())"', tmp_path, _env())
    assert "marker.txt" in result.output


def test_timeout_kills_the_process(tmp_path: Path) -> None:
    result = run_command(f'"{PY}" -c "import time; time.sleep(30)"', tmp_path, _env(), timeout=1)
    assert result.timed_out
    assert result.exit_code is None


def test_output_is_truncated(tmp_path: Path) -> None:
    result = run_command(f'"{PY}" -c "print(\'x\' * 5000)"', tmp_path, _env(), max_output_chars=100)
    assert len(result.output) <= 100


def test_environment_is_exactly_what_was_passed(tmp_path: Path) -> None:
    env = {**_env(), "ONLY_THIS": "yes"}
    code = "import os; print(sorted(k for k in os.environ if k.startswith('ANTHROPIC')))"
    result = run_command(f'"{PY}" -c "{code}"', tmp_path, env)
    assert "[]" in result.output


@pytest.mark.skipif(os.name == "nt", reason="stdin semantics differ under Windows shells")
def test_stdin_is_closed(tmp_path: Path) -> None:
    result = run_command(f'"{PY}" -c "import sys; print(repr(sys.stdin.read()))"', tmp_path, _env())
    assert "''" in result.output


def _exe(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("")
    return directory / name


def test_windows_skips_wsl_launchers_for_git_bash(tmp_path: Path) -> None:
    windows = tmp_path / "Windows"
    _exe(windows / "System32", "bash.exe")
    _exe(tmp_path / "AppData" / "Local" / "Microsoft" / "WindowsApps", "bash.exe")
    git_bash = _exe(tmp_path / "Git" / "usr" / "bin", "bash.exe")
    path = os.pathsep.join(
        [
            str(windows / "System32"),
            str(tmp_path / "AppData" / "Local" / "Microsoft" / "WindowsApps"),
            str(git_bash.parent),
        ]
    )
    env = {"PATH": path, "SYSTEMROOT": str(windows)}
    assert find_posix_shell(env, windows=True) == str(git_bash)


def test_windows_finds_git_bash_next_to_git_on_path(tmp_path: Path) -> None:
    _exe(tmp_path / "Git" / "cmd", "git.exe")
    bash = _exe(tmp_path / "Git" / "bin", "bash.exe")
    env = {"PATH": str(tmp_path / "Git" / "cmd"), "SYSTEMROOT": str(tmp_path / "Windows")}
    assert find_posix_shell(env, windows=True) == str(bash)


def test_windows_finds_git_bash_in_program_files(tmp_path: Path) -> None:
    bash = _exe(tmp_path / "Git" / "bin", "bash.exe")
    env = {"PATH": "", "ProgramFiles": str(tmp_path), "SYSTEMROOT": str(tmp_path / "Windows")}
    assert find_posix_shell(env, windows=True) == str(bash)


def test_windows_with_only_wsl_finds_nothing(tmp_path: Path) -> None:
    windows = tmp_path / "Windows"
    _exe(windows / "System32", "bash.exe")
    env = {"PATH": str(windows / "System32"), "SYSTEMROOT": str(windows)}
    assert find_posix_shell(env, windows=True) is None


def test_posix_lookup_uses_path(tmp_path: Path) -> None:
    assert find_posix_shell({"PATH": str(tmp_path)}, windows=False) is None
