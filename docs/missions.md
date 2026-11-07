# Missions

A mission is a large goal that GuardRun works through on its own: AI publishes
the tasks, AI does them, and AI inspects the results. You hear about it only
when something needs a human. Hand over a week of work, come back to finished
tasks and a short inbox.

```text
             goal (or a plan file)
                      │
                      ▼
   ┌────────── planner (read-only) ──────────┐
   │ explores the workspace, publish_tasks   │
   └──────────────────┬──────────────────────┘
                      ▼  tasks, in dependency order
   ┌──────────── supervisor loop ────────────┐
   │  worker (unattended) ─▶ checks ─▶ inspector (read-only)
   │      ▲                                  │ submit_verdict
   │      └──── feedback, up to N attempts ──┘
   └──────────────────┬──────────────────────┘
                      ▼  only what agents cannot settle
                 your inbox
```

## Roles

| Role | Tools | Decides by calling |
|---|---|---|
| Planner | read-only (`read_file`, `list_dir`, `glob`, `grep`, `todo`) | `publish_tasks` |
| Worker | everything your config allows, in `unattended` mode | `escalate` (only when stuck on something human) |
| Inspector | read-only | `submit_verdict` |

Every decision is a schema-validated tool call, not free text, so the
supervisor never guesses what a model meant. A plan is rejected (and the
planner asked to fix it) if ids repeat, a dependency is unknown, or the
dependencies form a cycle.

## Quality control

For each attempt the supervisor:

1. runs the worker with the task, its acceptance criteria, and summaries of
   the tasks it builds on;
2. runs the task's `checks` (shell commands) through the normal command policy,
   unattended: a check that would need approval is never run;
3. has the inspector review the workspace against the acceptance criteria. The
   worker's report is passed as data to verify, not as instructions.

A task passes only if the inspector passes it **and** every check exits 0: a
failing check overrules a lenient inspector. A rejected task goes back to the
same worker with the inspector's feedback, up to `max_attempts` times.

## When you are told

The inbox (`.guardrun/inbox.jsonl`, shown by `guardrun inbox`) receives only:

| Level | When |
|---|---|
| `blocker` | the worker called `escalate` (missing access, conflicting requirements, risky action) |
| `blocker` | the inspector set `needs_human` (security risk, data loss, impossible requirement) |
| `blocker` | a task failed inspection `max_attempts` times |
| `blocker` | a worker or inspector run failed (provider error, refusal) |
| `blocker` | a check needs approval, which an unattended mission cannot give |
| `blocker` | the mission's cost cap was reached |
| `warning` | tool output looked like a prompt-injection attempt |

Routine feedback and retries stay in the mission record. When a task is not
settled, tasks that depend on it are skipped and every independent task keeps
going, so one blocker never stalls the rest of the week.

Inbox entries are redacted like the audit log.

## Plan files

Write a plan yourself in TOML (or the same shape in JSON) and import it with
`guardrun mission import week.toml`:

```text
goal = "Release 0.2"

[[tasks]]
id = "fix-flaky-test"            # lowercase letters, digits, dashes
title = "Fix the flaky upload test"
instructions = "tests/test_upload.py fails about 1 in 10 runs. Find out why and fix it."
acceptance = ["the root cause is explained in the summary", "no sleep() added"]
checks = ["pytest -q tests/test_upload.py"]   # optional; exit 0 = pass

[[tasks]]
id = "changelog"
title = "Update the changelog"
instructions = "Add the 0.2 entries."
acceptance = ["every merged change is listed"]
depends_on = ["fix-flaky-test"]
```

See [`examples/weekly_mission.toml`](../examples/weekly_mission.toml) for a
longer example.

## Resuming

Missions are saved under `.guardrun/missions/` after every attempt. Running
`guardrun mission run ID` again continues with the pending tasks, including one
that was interrupted (it restarts with the last inspector feedback).
`--retry` requeues failed, escalated, and skipped tasks once you have dealt
with the inbox.

## Safety

Missions add no new powers. Workers run in `unattended` mode, which turns
every "ask" into a denial, so `[shell] allow` decides which commands run.
With `mode = "read-only"` in `guardrun.toml`, workers stay read-only too.
Planners and inspectors are always read-only. Sandbox, protected paths,
redaction, audit logging, and budgets apply to every agent, and
`[mission] max_cost_usd` caps the whole mission.
