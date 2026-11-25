import io
from pathlib import Path

from guardrun.agent import RunResult
from guardrun.cli.main import main
from guardrun.cli.render import format_result
from guardrun.cli.style import PLAIN, Style, style_for, wants_colour
from guardrun.usage import Usage
from tests.cli.test_main import Term


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_plain_style_changes_nothing() -> None:
    assert PLAIN.error("x") == "x"
    assert Style(True).paint("", "red") == ""
    assert Style(True).paint("x") == "x"


def test_colour_wraps_and_resets() -> None:
    assert Style(True).ok("done") == "\033[32mdone\033[0m"
    assert Style(True).error("no") == "\033[31;1mno\033[0m"


def test_colour_only_on_a_terminal() -> None:
    assert not wants_colour(io.StringIO(), {})
    assert not wants_colour(Tty(), {"NO_COLOR": "1"})
    assert not wants_colour(Tty(), {"TERM": "dumb"})
    assert wants_colour(io.StringIO(), {"FORCE_COLOR": "1"})
    assert not style_for(io.StringIO(), {}).enabled


def test_result_summary_is_coloured_by_outcome() -> None:
    ok = format_result(RunResult("hi", "completed", 1, Usage()), Style(True))
    failed = format_result(RunResult("", "error", 1, Usage(), detail="boom"), Style(True))
    assert "\033[32m[completed]" in ok
    assert "\033[31;1m[error]" in failed


def test_doctor_explains_warnings(tmp_path: Path) -> None:
    term = Term()
    main(["doctor", "-w", str(tmp_path)], io=term.io)
    assert "[ok  ] python" in term.out
    if "[warn]" in term.out:
        assert "warnings don't stop GuardRun" in term.out
