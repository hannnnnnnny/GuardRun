from pathlib import Path

import pytest

from guardrun.agent import MAX_CONSECUTIVE_FAILED_TURNS, TRUNCATED_CALL_MESSAGE, Agent, RunResult
from guardrun.budget import Budget
from guardrun.errors import ProviderError
from guardrun.events import Event, EventBus
from guardrun.messages import ModelResponse
from guardrun.providers.base import ModelRequest
from guardrun.providers.scripted import ScriptedProvider, text_turn, tool_turn
from guardrun.tools.base import ToolContext
from guardrun.tools.builtin import file_tools
from guardrun.tools.registry import ToolRegistry
from guardrun.usage import Usage


def make_agent(root: Path, turns: list[ModelResponse], **kwargs: object) -> Agent:
    return Agent(
        ScriptedProvider(turns),
        ToolRegistry(file_tools()),
        ToolContext.for_root(root),
        **kwargs,  # type: ignore[arg-type]
    )


def test_single_turn_completion(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, [text_turn("All done.", Usage(10, 5))])
    result = agent.run("say hi")
    assert result.ok
    assert result.text == "All done."
    assert result.turns == 1
    assert result.usage == Usage(10, 5)


def test_tool_loop_executes_calls_and_feeds_results_back(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("remember the milk")
    provider = ScriptedProvider(
        [tool_turn(("read_file", {"path": "notes.txt"})), text_turn("It says milk.")]
    )
    agent = Agent(provider, ToolRegistry(file_tools()), ToolContext.for_root(tmp_path))
    result = agent.run("what is in notes.txt?")
    assert result.text == "It says milk."
    second: ModelRequest = provider.requests[1]
    tool_result = second.messages[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "remember the milk" in tool_result["content"]


def test_parallel_calls_return_in_one_message(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")
    provider = ScriptedProvider(
        [
            tool_turn(("read_file", {"path": "a.txt"}), ("read_file", {"path": "b.txt"})),
            text_turn("ok"),
        ]
    )
    Agent(provider, ToolRegistry(file_tools()), ToolContext.for_root(tmp_path)).run("read both")
    last = provider.requests[1].messages[-1]
    assert last["role"] == "user"
    assert len(last["content"]) == 2


def test_requests_carry_system_prompt_and_sorted_tools(tmp_path: Path) -> None:
    provider = ScriptedProvider([text_turn("x")])
    Agent(
        provider, ToolRegistry(file_tools()), ToolContext.for_root(tmp_path), system_prompt="SYS"
    ).run("t")
    request = provider.requests[0]
    assert request.system == "SYS"
    names = [t["name"] for t in request.tools]
    assert names == sorted(names)


def test_refusal_stops_the_run(tmp_path: Path) -> None:
    refusal = ModelResponse(content=[], stop_reason="refusal")
    result = make_agent(tmp_path, [refusal]).run("do something bad")
    assert result.stop == "refused"
    assert not result.ok


def test_truncated_tool_calls_are_not_executed(tmp_path: Path) -> None:
    truncated = tool_turn(("write_file", {"path": "big.txt", "content": "partial"}))
    truncated = ModelResponse(truncated.content, "max_tokens")
    agent = make_agent(tmp_path, [truncated, text_turn("retrying later")])
    result = agent.run("write a big file")
    assert not (tmp_path / "big.txt").exists()
    assert result.ok
    tool_result = agent.conversation.messages[2]["content"][0]
    assert tool_result["content"] == TRUNCATED_CALL_MESSAGE


def test_max_tokens_without_tools_stops(tmp_path: Path) -> None:
    response = ModelResponse([{"type": "text", "text": "partial answ"}], "max_tokens")
    assert make_agent(tmp_path, [response]).run("essay").stop == "max_tokens"


def test_pause_turn_continues(tmp_path: Path) -> None:
    paused = ModelResponse([{"type": "text", "text": "searching"}], "pause_turn")
    result = make_agent(tmp_path, [paused, text_turn("found it")]).run("search")
    assert result.text == "found it"
    assert result.turns == 2


def test_budget_stops_the_run(tmp_path: Path) -> None:
    turns = [tool_turn(("list_dir", {})) for _ in range(5)]
    result = make_agent(tmp_path, turns, budget=Budget(max_turns=2)).run("loop forever")
    assert result.stop == "budget"
    assert result.turns == 2
    assert "2 turns" in result.detail


def test_repeated_failures_stop_the_run(tmp_path: Path) -> None:
    turns = [tool_turn(("read_file", {"path": "missing.txt"})) for _ in range(10)]
    result = make_agent(tmp_path, turns).run("read it")
    assert result.stop == "stuck"
    assert result.turns == MAX_CONSECUTIVE_FAILED_TURNS


def test_provider_errors_end_the_run_cleanly(tmp_path: Path) -> None:
    result = make_agent(tmp_path, []).run("anything")
    assert result.stop == "error"
    assert "ran out" in result.detail


def test_cancel_before_next_turn(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, [tool_turn(("list_dir", {})), text_turn("never")])
    agent.events.subscribe(lambda e: agent.cancel() if e.kind == "tool_finished" else None)
    result = agent.run("list")
    assert result.stop == "cancelled"
    assert result.turns == 1


def test_run_continues_the_same_conversation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, [text_turn("first"), text_turn("second")])
    agent.run("one")
    agent.run("two")
    roles = [m["role"] for m in agent.conversation.messages]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_events_describe_the_run(tmp_path: Path) -> None:
    seen: list[Event] = []
    bus = EventBus()
    bus.subscribe(seen.append)
    agent = make_agent(tmp_path, [tool_turn(("list_dir", {})), text_turn("ok")], events=bus)
    agent.run("go")
    assert [e.kind for e in seen] == [
        "run_started",
        "turn_started",
        "model_responded",
        "tool_started",
        "tool_finished",
        "turn_started",
        "model_responded",
        "run_finished",
    ]


def test_provider_errors_other_than_exhaustion_are_reported(tmp_path: Path) -> None:
    class Broken:
        model = "m"

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise ProviderError("authentication failed")

    agent = Agent(Broken(), ToolRegistry(file_tools()), ToolContext.for_root(tmp_path))
    result = agent.run("x")
    assert result.detail == "authentication failed"
    assert not result.retryable


class Flaky:
    """Fails with an outage first, then answers."""

    model = "m"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("overloaded", transient=True)
        return text_turn("done")


def test_outage_can_be_resumed_without_repeating_the_task(tmp_path: Path) -> None:
    agent = Agent(Flaky(), ToolRegistry(file_tools()), ToolContext.for_root(tmp_path))
    first = agent.run("do it")
    assert first.stop == "error"
    assert first.retryable
    second = agent.resume()
    assert second.ok
    assert second.text == "done"
    roles = [m["role"] for m in agent.conversation.messages]
    assert roles == ["user", "assistant"]


def test_resume_needs_a_pending_turn(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, [text_turn("hi")])
    agent.run("hello")
    with pytest.raises(ValueError, match="nothing to resume"):
        agent.resume()


@pytest.mark.parametrize("stop", ["completed", "refused", "budget", "stuck", "cancelled"])
def test_only_completed_is_ok(stop: str) -> None:
    assert RunResult("", stop, 0, Usage()).ok is (stop == "completed")


def test_dispatcher_options_are_applied(tmp_path: Path) -> None:
    turn = tool_turn(("list_dir", {}), ("list_dir", {"path": "."}), ("glob", {"pattern": "*"}))
    agent = make_agent(
        tmp_path, [turn, text_turn("ok")], dispatcher_options={"max_calls_per_turn": 1}
    )
    agent.run("look")
    results = agent.conversation.messages[2]["content"]
    assert [r.get("is_error", False) for r in results] == [False, True, True]


def test_run_finished_reports_usage(tmp_path: Path) -> None:
    seen: list[Event] = []
    bus = EventBus()
    bus.subscribe(seen.append)
    make_agent(tmp_path, [text_turn("x", Usage(3, 4))], events=bus).run("go")
    finished = seen[-1].data
    assert finished["model"] == "scripted"
    assert finished["usage"]["input_tokens"] == 3
    assert finished["usage"]["output_tokens"] == 4
    assert finished["cost_usd"] is None


def test_tool_output_limit_is_applied(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 5000)
    agent = make_agent(
        tmp_path,
        [tool_turn(("read_file", {"path": "big.txt"})), text_turn("ok")],
        max_tool_output_chars=300,
    )
    agent.run("read it")
    result = agent.conversation.messages[2]["content"][0]["content"]
    assert len(result) <= 300
    assert "characters truncated" in result
