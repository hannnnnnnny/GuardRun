# GuardRun

[![CI](https://github.com/hannnnnnnny/GuardRun/actions/workflows/ci.yml/badge.svg)](https://github.com/hannnnnnnny/GuardRun/actions/workflows/ci.yml)
[![CodeQL](https://github.com/hannnnnnnny/GuardRun/actions/workflows/codeql.yml/badge.svg)](https://github.com/hannnnnnnny/GuardRun/actions/workflows/codeql.yml)

A powerful, safety-first agent harness for DeepSeek and Claude models, written in Python.

GuardRun gives Claude real tools (files, search, and a shell) inside a single
workspace directory, and puts a layered safety system between the model and
your machine: a path sandbox, a shell command policy, approval modes, secret
redaction, prompt-injection flagging, budgets, and an audit log.

```text
you ──task──▶ Agent loop ──request──▶ Claude
                 ▲   │
        results  │   ▼ tool calls
                 │  ToolExecutor ──▶ validate ─▶ SafetyGate ─▶ tool ─▶ redact / flag ─▶ result
                 │                                   │
                 └──────── events ──▶ audit log, console
```

## Install

```bash
pip install -e ".[dev]"
```

Python 3.10 or newer. GuardRun uses **DeepSeek** by default (`deepseek-v4-pro`
via DeepSeek's Anthropic-compatible API). Put your key in an environment
variable; GuardRun reads it from there and never stores it:

```bash
export DEEPSEEK_API_KEY=...        # PowerShell: $env:DEEPSEEK_API_KEY = "..."
guardrun doctor                    # confirms the key is visible (without printing it)
```

To use Claude instead, set `provider = "anthropic"` under `[model]` in
`guardrun.toml` (or pass `--provider anthropic`) and provide `ANTHROPIC_API_KEY`
or an `ant auth login` profile.

## Quick start

```bash
guardrun init                                  # optional: write guardrun.toml
guardrun run "add type hints to utils.py and run the tests"
guardrun chat                                  # multi-turn session
guardrun run "continue" --resume 3f2a9c1b7d4e  # pick a session back up
guardrun usage                                 # tokens and cost of recent runs
guardrun mission plan "GOAL" --run              # plan, do, and inspect a whole goal
guardrun policy "rm -rf build"                 # how would this command be treated?
```

When the agent wants to do something risky it asks first:

```text
> write_file {"path": "utils.py", "content": "..."}
[approval needed] write_file: utils.py
  reason: write actions require approval in ask mode
  allow? [y]es once / [a]lways this session / [N]o (optionally: n <why>):
```

## Missions: a week of work, unattended

Give GuardRun a big goal and let it run until there is a result. A planner
publishes tasks, workers do them one by one, and an inspector checks every
result against its acceptance criteria and sends weak work back with feedback.
You are told only what needs a human.

```bash
guardrun mission plan "migrate the config loader to pydantic and update the docs" --run
guardrun mission import examples/weekly_mission.toml   # or write the plan yourself
guardrun mission run 1ce02250ec55                      # resume after a stop
guardrun mission status                                # what got done
guardrun inbox                                         # what needs you
```

```text
== tests-for-utils: Cover utils with tests (attempt 1)
   QA sent back: parse_date has no test for an empty string.
== tests-for-utils: Cover utils with tests (attempt 2)
   QA passed: all public functions covered; pytest passed.
== deploy-docs: Publish the docs (attempt 1)
   -> escalated: the docs host needs credentials that are not configured
   [needs you] the docs host needs credentials that are not configured
```

Workers run in `unattended` mode, so your shell allowlist decides what they
can execute; planners and inspectors are read-only. See [Missions](docs/missions.md).

## Approval modes

| Mode | Reads | File edits | Shell commands | Network |
|---|---|---|---|---|
| `read-only` | yes | blocked | blocked | blocked |
| `ask` *(default)* | yes | ask | ask | ask |
| `auto-edit` | yes | yes | per command policy | ask |
| `unattended` | yes | yes | per command policy | allowlisted domains only |

In `unattended` mode nobody is there to answer, so anything that would need
approval is **denied**. Actions the policy blocks (for example `sudo`, `curl … | sh`,
or reading `.env`) can never be approved in any mode.

## Built-in tools

| Tool | Risk | What it does |
|---|---|---|
| `read_file` | read | Line-numbered file view with paging |
| `list_dir` | read | Directory listing (protected entries hidden) |
| `glob` | read | Find files by pattern, e.g. `**/*.py` |
| `grep` | read | Regex or literal search across files |
| `todo` | read | The agent's own task list for multi-step work |
| `write_file` | write | Create or replace a file (atomic, size-limited) |
| `edit_file` | write | Exact, unique string replacement |
| `multi_edit` | write | Several replacements in one file, all or nothing |
| `run_shell` | execute | Policy-checked shell command with a scrubbed environment |
| `web_fetch` | network | Opt-in: fetch a public URL as text (SSRF-protected) |

## Using GuardRun from Python

```python
from guardrun import AgentOptions, ApprovalMode, Budget, build_agent
from guardrun.providers.anthropic import AnthropicProvider
from guardrun.safety import ConsoleApprover
import sys

agent = build_agent(
    "path/to/project",
    AnthropicProvider(effort="high"),
    AgentOptions(
        mode=ApprovalMode.AUTO_EDIT,
        approver=ConsoleApprover(sys.stdin, sys.stdout),
        budget=Budget(max_turns=30, max_cost_usd=2.0),
    ),
)
result = agent.run("Fix the failing test in tests/test_parser.py")
print(result.stop, result.text)
```

`result.stop` is one of `completed`, `refused`, `max_tokens`, `budget`,
`stuck`, `cancelled`, or `error`. See [`examples/`](examples/) for custom tools,
project-specific policy gates (`AgentOptions(extra_gates=[...])`), an offline
demo, and an offline mission (`examples/mission_demo.py`).

## Documentation

- [Architecture](docs/architecture.md): how a run flows through the system
- [Missions](docs/missions.md): planner, workers, inspector, and the inbox
- [Security model](docs/security-model.md): what is protected, how, and the known limits
- [Configuration](docs/configuration.md): every `guardrun.toml` setting
- [CLI reference](docs/cli.md): every command and flag
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md) and [Security policy](SECURITY.md)

## License

MIT, see [LICENSE](LICENSE).
