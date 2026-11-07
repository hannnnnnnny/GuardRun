# GuardRun

[![CI](https://github.com/hannnnnnnny/GuardRun/actions/workflows/ci.yml/badge.svg)](https://github.com/hannnnnnnny/GuardRun/actions/workflows/ci.yml)
[![CodeQL](https://github.com/hannnnnnnny/GuardRun/actions/workflows/codeql.yml/badge.svg)](https://github.com/hannnnnnnny/GuardRun/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/hannnnnnnny/GuardRun)](https://github.com/hannnnnnnny/GuardRun/releases/latest)

A powerful, safety-first agent harness for DeepSeek and Claude models, written in Python.

GuardRun gives the model real tools (files, search, and a shell) inside a single
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

On top of that sits **missions**: hand GuardRun a big goal (a week of work),
and AI publishes tasks to AI, does them, inspects them, and reworks them until
there is a result. You only hear about what truly needs a human.

> **中文简介：** GuardRun 是一个安全优先的 AI Agent 运行框架（默认 DeepSeek，也支持 Claude）。
> 它的“任务（mission）”功能可以把一个大目标交给 AI：规划员拆分任务、执行者逐个完成、
> 质检员按验收标准检查并打回重做，直到出结果——一次就能安排一周的活儿。
> 只有遇到重大问题（缺少凭据、反复质检不过、预算用完、疑似提示词注入）才会写进
> `guardrun inbox` 提醒你。Windows 用户可直接从
> [Releases](https://github.com/hannnnnnnny/GuardRun/releases/latest) 下载 `.exe`，无需安装 Python。

## Install

**Windows, no Python needed:** download `guardrun-<version>-windows-x64.exe`
from the [latest release](https://github.com/hannnnnnnny/GuardRun/releases/latest),
rename it to `guardrun.exe`, and put it on your `PATH` (or run it from the
project folder). Each release also ships a `.sha256` file to verify the download:

```powershell
(Get-FileHash .\guardrun-0.2.0-windows-x64.exe -Algorithm SHA256).Hash
```

**With Python 3.10+ (any OS):** install the wheel attached to the release, or
from source:

```bash
pip install guardrun-0.2.0-py3-none-any.whl   # from the release page
pip install -e ".[dev]"                       # from a checkout, for development
```

GuardRun uses **DeepSeek** by default (`deepseek-v4-pro`
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
