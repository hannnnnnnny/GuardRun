# Architecture

GuardRun is a manual tool-use loop around the Anthropic Messages API, with every
tool call routed through one choke point where safety decisions are made.

## Package layout

| Module | Responsibility |
|---|---|
| `guardrun.agent` | The loop: call the model, run requested tools, repeat |
| `guardrun.factory` | `build_agent`: wires tools, gate, filters, audit into an `Agent` |
| `guardrun.providers` | `Provider` protocol; `AnthropicProvider`; `ScriptedProvider` for tests |
| `guardrun.messages` | Wire-shaped, append-only `Conversation`; `ModelResponse`, `ToolCall`, `ToolResult` |
| `guardrun.tools` | `Tool` base class, schema validation, registry, executor, built-in tools |
| `guardrun.safety` | Workspace sandbox, protected paths, command policy, approvals, redaction, audit |
| `guardrun.budget` | Turn / token / cost limits |
| `guardrun.config` | Strict `guardrun.toml` loader |
| `guardrun.sessions` | Save and resume conversations |
| `guardrun.mission` | Planner, worker, and inspector roles; the supervisor loop; mission store and inbox ([Missions](missions.md)) |
| `guardrun.cli` | The `guardrun` command |

The safety package never imports the agent loop, and only `safety.gate`
depends on the tool framework. A test imports every module in isolation so
import cycles cannot creep back in.

## One run, step by step

1. `Agent.run(task)` appends the task to the `Conversation` and emits `run_started`.
2. The `BudgetTracker` checks turn, token, and cost limits *before* each call.
3. The provider receives the system prompt, the full history, and the tool
   specs (sorted by name so the prompt prefix is stable and cacheable).
4. The assistant turn is appended **verbatim**, including thinking blocks.
   History is never edited, which keeps preserved thinking valid.
5. Depending on `stop_reason`:
   - `refusal`: the run ends with `refused`.
   - `pause_turn`: the loop continues from the appended turn.
   - `max_tokens` with tool calls: the calls may be truncated, so they are
     **not run**; each gets an error result asking the model to re-issue it.
   - no tool calls: the run ends (`completed` or `max_tokens`).
6. The `Dispatcher` runs the turn's tool calls: at most `max_calls_per_turn`
   are executed, an identical call repeated too many times in a row is refused,
   and when every call is read-only and needs no approval they run concurrently.
   Each call goes through the `ToolExecutor` (below), and all results for a
   turn are returned in a single user message, in the original order.
7. Five consecutive turns in which every call failed end the run as `stuck`.

## The tool execution pipeline

```text
ToolCall
  │
  ├─ unknown tool? ───────────────────────────────▶ error result
  ├─ validate input against the tool's JSON schema ▶ error result on failure
  ├─ gates (SafetyGate)
  │    strictest(mode × risk, tool.assess(args))
  │      BLOCK ─────────────────────────────────▶ error result (never approvable)
  │      ASK   ─▶ approver ─ deny ─────────────▶ error result (+ user's note)
  │      ALLOW
  ├─ tool.run(args, ctx)
  │    ToolError ────────────────────────────────▶ error result (message shown)
  │    unexpected exception ─────────────────────▶ generic error (details logged locally)
  ├─ output filters: secret redaction, injection flagging
  └─ middle truncation ────────────────────────────▶ ToolResult
```

Additional gates passed as `AgentOptions.extra_gates` run after the
`SafetyGate`, so a project rule can only make the policy stricter.

Nothing a tool does can crash the loop: every failure becomes an `is_error`
result that the model can read and react to.

## Events

The loop and the gate emit structured events on an `EventBus`:
`run_started`, `turn_started`, `model_responded`, `tool_started`,
`tool_finished`, `approval_requested`, `approval_decided`, `action_blocked`,
`injection_suspected`, `run_finished`. The console renderer and the audit log
are both plain subscribers; a failing subscriber cannot break a run.

## Providers

`AnthropicProvider` streams every request (so large `max_tokens` values don't
hit HTTP timeouts), uses adaptive thinking with a configurable effort level,
enables prompt caching, turns on server-side refusal fallbacks, and maps SDK
errors to `ProviderError` with actionable messages. `DeepSeekProvider` (the
default) reuses the same code against DeepSeek's Anthropic-compatible endpoint
(`https://api.deepseek.com/anthropic`) with `DEEPSEEK_API_KEY`, sending only the
fields DeepSeek supports (no betas, fallbacks, or cache control). Because the
wire format is the same, history, tool calls, and thinking blocks need no
translation. `ScriptedProvider` replays
canned turns and records requests, which is how the whole system is tested
without network access.
