"""The agent loop: call the model, run the tools it asks for, repeat."""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from typing import Any

from guardrun.budget import Budget, BudgetTracker
from guardrun.dispatch import Dispatcher
from guardrun.errors import BudgetExceeded, ProviderError
from guardrun.events import EventBus
from guardrun.messages import Conversation, ModelResponse, ToolResult
from guardrun.prompts import DEFAULT_SYSTEM_PROMPT
from guardrun.providers.base import ModelRequest, Provider
from guardrun.tools.base import ToolContext
from guardrun.tools.executor import DEFAULT_MAX_OUTPUT_CHARS, Gate, OutputFilter, ToolExecutor
from guardrun.tools.registry import ToolRegistry
from guardrun.usage import Usage

MAX_CONSECUTIVE_FAILED_TURNS = 5
TRUNCATED_CALL_MESSAGE = (
    "Your response hit the output token limit, so this tool call's input may be "
    "incomplete and it was not run. Re-issue it, splitting large content if needed."
)


@dataclass(frozen=True)
class RunResult:
    """How a run ended. ``stop`` is one of: completed, refused, max_tokens,
    budget, stuck, cancelled, error."""

    text: str
    stop: str
    turns: int
    usage: Usage
    cost_usd: float | None = None
    detail: str = ""
    # An "error" stop caused by a provider outage; ``Agent.resume`` may finish the run.
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.stop == "completed"


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: ToolRegistry,
        ctx: ToolContext,
        *,
        gates: list[Gate] | None = None,
        output_filters: list[OutputFilter] | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        budget: Budget | None = None,
        events: EventBus | None = None,
        conversation: Conversation | None = None,
        dispatcher_options: dict[str, Any] | None = None,
        max_tool_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.system_prompt = system_prompt
        self.budget = budget or Budget()
        self.events = events or EventBus()
        self.conversation = conversation or Conversation()
        self.executor = ToolExecutor(
            tools,
            ctx,
            gates=gates,
            output_filters=output_filters,
            max_output_chars=max_tool_output_chars,
        )
        self.dispatcher = Dispatcher(self.executor, self.events, **(dispatcher_options or {}))
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """Stop after the current step; safe to call from another thread."""
        self._cancelled.set()

    def run(self, task: str) -> RunResult:
        """Add ``task`` as a user message and loop until the model is done.

        Calling ``run`` again continues the same conversation.
        """
        self.conversation.add_user_text(task)
        return self._run(task)

    def resume(self) -> RunResult:
        """Continue after a run ended waiting on the model (e.g. a provider outage),
        without adding a message; the model call that failed is simply made again."""
        if self.conversation.last_role != "user":
            raise ValueError("nothing to resume: the model already answered")
        return self._run("(resumed)")

    def _run(self, task: str) -> RunResult:
        self._cancelled.clear()
        tracker = BudgetTracker(self.budget, self.provider.model)
        self.events.emit("run_started", task=task)
        try:
            result = self._loop(tracker)
        except BudgetExceeded as exc:
            result = self._result("budget", tracker, detail=str(exc))
        except ProviderError as exc:
            result = dataclasses.replace(
                self._result("error", tracker, detail=str(exc)), retryable=exc.transient
            )
        self.events.emit(
            "run_finished",
            stop=result.stop,
            turns=result.turns,
            detail=result.detail,
            model=self.provider.model,
            usage=dataclasses.asdict(result.usage),
            cost_usd=result.cost_usd,
        )
        return result

    def _loop(self, tracker: BudgetTracker) -> RunResult:
        failed_turns = 0
        while True:
            if self._cancelled.is_set():
                return self._result("cancelled", tracker)
            tracker.check()
            response = self._call_model(tracker)
            if response.stop_reason == "refusal":
                return self._result("refused", tracker, text=response.text)
            calls = response.tool_calls
            if response.stop_reason == "pause_turn":
                continue
            if not calls:
                stop = "max_tokens" if response.stop_reason == "max_tokens" else "completed"
                return self._result(stop, tracker, text=response.text)
            if response.stop_reason == "max_tokens":
                results = [ToolResult(c.id, TRUNCATED_CALL_MESSAGE, is_error=True) for c in calls]
            else:
                results = self.dispatcher.run(calls)
            self.conversation.add_tool_results(results)
            failed_turns = failed_turns + 1 if all(r.is_error for r in results) else 0
            if failed_turns >= MAX_CONSECUTIVE_FAILED_TURNS:
                detail = f"{failed_turns} consecutive turns where every tool call failed"
                return self._result("stuck", tracker, detail=detail)

    def _call_model(self, tracker: BudgetTracker) -> ModelResponse:
        self.events.emit("turn_started", turn=tracker.turns + 1)
        request = ModelRequest(
            system=self.system_prompt,
            messages=self.conversation.messages,
            tools=self.tools.specs(),
        )
        response = self.provider.complete(request)
        tracker.record(response.usage)
        self.conversation.add_assistant(response)
        self.events.emit(
            "model_responded",
            stop_reason=response.stop_reason,
            text=response.text,
            tool_calls=[c.name for c in response.tool_calls],
            output_tokens=response.usage.output_tokens,
        )
        return response

    def _result(
        self, stop: str, tracker: BudgetTracker, *, text: str = "", detail: str = ""
    ) -> RunResult:
        return RunResult(
            text=text,
            stop=stop,
            turns=tracker.turns,
            usage=tracker.usage,
            cost_usd=tracker.cost_usd,
            detail=detail,
        )
