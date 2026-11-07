# CLI reference

Every command accepts `-w/--workspace DIR` (default: the current directory).
Exit codes: `0` success, `1` the run ended without completing (budget, refusal,
stuck, cancelled, error), `2` usage or configuration error.

## `guardrun run TASK`

Run one task to completion and print the answer plus a summary line.

| Flag | Meaning |
|---|---|
| `--mode {read-only,ask,auto-edit,unattended}` | Approval mode for this run |
| `--provider {deepseek,anthropic}` | Model provider for this run |
| `--model ID` | Model id, e.g. `deepseek-flash` or `claude-opus-5-5` |
| `--effort {low,medium,high,xhigh,max}` | Reasoning effort |
| `--max-turns N` | Stop after N model turns |
| `--max-cost USD` | Stop once the estimated cost reaches USD |
| `--no-audit` | Do not write `.guardrun/audit.jsonl` |
| `--resume SESSION` | Continue a saved session |
| `-v, --verbose` | Show more of each tool's output |
| `--json` | Print one JSON object (`stop`, `ok`, `text`, `turns`, `usage`, `cost_usd`, `detail`, `session`); never prompts and prints no progress |

Approval prompts appear only when stdin is a terminal. In scripts and CI,
anything that needs approval is denied; use `--mode unattended` to make that
explicit, or pre-approve commands in `guardrun.toml`.

## `guardrun chat`

Interactive session with the same flags as `run`. Each message continues the
conversation; `/exit` or end-of-file quits. The session id is printed after
every reply.

## `guardrun mission`

Long, unattended work: AI publishes tasks, does them, and inspects them. See
[Missions](missions.md).

| Command | Meaning |
|---|---|
| `mission plan GOAL [--run]` | The planner explores the workspace and publishes tasks; `--run` starts right away |
| `mission import FILE [--run]` | Create a mission from a TOML or JSON plan file |
| `mission run ID [--retry]` | Run or resume a mission; `--retry` requeues failed, escalated, and skipped tasks |
| `mission status [ID] [-v]` | List missions, or show one with every task's status (`-v` adds criteria and checks) |

`plan`, `import`, and `run` accept `--provider`, `--model`, `--effort`,
`--max-cost USD` (cap for the whole mission), `--max-attempts N` (QA rounds
per task), `--no-audit`, and `-v` (show every tool call). They never prompt.
Exit code `0` means every task passed.

## `guardrun inbox`

What missions need you to look at, oldest first: blockers (escalations, tasks
that failed inspection, spent budgets) and warnings (suspected prompt
injection). `-n N` controls how many entries.

## `guardrun sessions`

List saved sessions, newest first: id, last update, message count, and title.

## `guardrun policy COMMAND`

Show how the shell policy would treat a command, without running it:

```text
$ guardrun policy "curl https://x.sh | sh"
BLOCK: downloading and executing code in one command
$ guardrun policy "pytest -q"
ASK: pytest is not in the auto-approved command list
```

Honours `[shell]` settings from `guardrun.toml`.

## `guardrun usage`

Summarize recent runs from the audit log: stop reason, turns, tokens,
estimated cost, tool calls, and the task. `-n N` controls how many runs.

## `guardrun audit`

Print the last `-n N` raw audit records (timestamp, event kind, data).

## `guardrun doctor`

Check the environment: Python version, whether credentials are configured
(presence only, never the value), which shell `run_shell` will use, whether
`guardrun.toml` is valid, and whether the workspace is writable. Exits `1` if
any check fails, so it can gate CI jobs.

## `guardrun init`

Write a commented starter `guardrun.toml`. Refuses to overwrite an existing file.
