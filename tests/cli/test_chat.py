from pathlib import Path

from guardrun.cli.main import EXIT_OK, main
from guardrun.providers.scripted import ScriptedProvider, text_turn
from tests.cli.test_main import Term


def chat(tmp_path: Path, stdin: str, *replies: str, extra: tuple[str, ...] = ()) -> Term:
    provider = ScriptedProvider([text_turn(r) for r in replies])
    term = Term(stdin=stdin)
    code = main(
        ["chat", "-w", str(tmp_path), *extra],
        io=term.io,
        provider_factory=lambda config: provider,
    )
    assert code == EXIT_OK
    return term


def test_banner_names_model_and_mode(tmp_path: Path) -> None:
    term = chat(tmp_path, "/exit\n")
    assert "deepseek-v4-pro · mode ask" in term.out
    assert "type /help" in term.out


def test_help_and_unknown_commands(tmp_path: Path) -> None:
    term = chat(tmp_path, "/help\n/oops\n/quit\n")
    assert "/usage" in term.out
    assert "unknown command /oops" in term.out


def test_usage_adds_up_the_chat(tmp_path: Path) -> None:
    term = chat(tmp_path, "hi\nagain\n/usage\n/exit\n", "one", "two")
    assert "2 replies" in term.out


def test_session_and_new(tmp_path: Path) -> None:
    term = chat(tmp_path, "hi\n/session\n/new\n/session\n/exit\n", "one")
    resumes = [line for line in term.out.splitlines() if "resume with" in line]
    assert len(resumes) == 2
    assert resumes[0] != resumes[1]
    assert "new conversation" in term.out


def test_double_slash_sends_text_starting_with_a_slash(tmp_path: Path) -> None:
    provider = ScriptedProvider([text_turn("it is a path")])
    term = Term(stdin="//etc/hosts is what?\n/exit\n")
    main(["chat", "-w", str(tmp_path)], io=term.io, provider_factory=lambda config: provider)
    sent = provider.requests[0].messages[-1]["content"][0]["text"]
    assert sent == "/etc/hosts is what?"


def test_end_of_input_says_bye(tmp_path: Path) -> None:
    term = chat(tmp_path, "")
    assert term.out.rstrip().endswith("bye")


def test_resume_applies_only_to_the_first_conversation(tmp_path: Path) -> None:
    first = chat(tmp_path, "hi\n/session\n/exit\n", "one")
    session_id = first.out.split("resume with: guardrun chat --resume ")[1].split()[0]
    term = chat(tmp_path, "/session\n/new\n/session\n/exit\n", extra=("--resume", session_id))
    marker = "resume with: guardrun chat --resume "
    ids = [line.split(marker)[1] for line in term.out.splitlines() if marker in line]
    assert ids[0] == session_id
    assert ids[1] != session_id


def test_bare_guardrun_shows_an_overview() -> None:
    term = Term()
    assert main([], io=term.io) == EXIT_OK
    assert 'guardrun mission plan "GOAL" --run' in term.out
    assert "guardrun doctor" in term.out
