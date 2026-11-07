# Changelog

All notable changes to GuardRun are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Missions: `guardrun mission plan|import|run|status` turns a large goal into
  tasks and works through them unattended. A read-only planner publishes the
  tasks, workers do them, and a read-only inspector checks each result against
  its acceptance criteria and automated checks, sending weak work back with
  feedback. Dependent tasks wait; independent ones keep going.
- `guardrun inbox`: only what needs a human (escalations, tasks that failed
  inspection, spent budgets, suspected prompt injection), redacted.
- `[mission]` settings: `max_attempts`, `max_tasks`, `max_cost_usd`.
- `AgentOptions.tools` replaces the built-in tool set (e.g. read-only tools).
- `examples/mission_demo.py` and `examples/weekly_mission.toml`.

## [0.1.0] - 2026-10-20

First release: a safety-first agent harness for DeepSeek and Claude.

Earlier development builds were named pyagent. Rename an existing
`pyagent.toml` and `.pyagent/` to `guardrun.toml` and `.guardrun/` to keep their
settings and sessions.

### Agent
- DeepSeek (`deepseek-v4-pro`, `deepseek-flash`, the default) through DeepSeek's
  Anthropic-compatible API, or Claude. Select with `[model] provider` or
  `--provider`; the key comes from `DEEPSEEK_API_KEY` or `ANTHROPIC_API_KEY`.
- Manual tool-use loop over the Anthropic Messages API with adaptive thinking,
  configurable effort, streaming, prompt caching, and server-side refusal fallbacks.
- Append-only conversation history that preserves thinking blocks verbatim.
- Handling for every stop reason, including not running tool calls truncated by
  `max_tokens`.
- Turn, token, and cost budgets; a `stuck` guard; cancellation.
- Per-turn tool call limit, identical-call loop guard, and concurrent execution
  of read-only, approval-free calls.
- Sessions that can be saved and resumed.
- `instructions_file` loads project guidance (e.g. `AGENTS.md`) through the sandbox.
- `limits.max_tool_output_chars` to tune how much tool output reaches the model.

### Tools
- `read_file`, `list_dir`, `glob`, `grep`, `write_file`, `edit_file`,
  `multi_edit`, `todo`, `run_shell`, and the opt-in `web_fetch`.
- Every input validated against its JSON schema; every failure returned to the
  model as an error result rather than crashing the loop.
- Edits report unified diffs and respect CRLF line endings.

### Safety
- Workspace sandbox with symlink-aware containment and Windows path hardening.
- Protected paths for credentials, `.git/`, and GuardRun's own state.
- Shell command policy (allow / ask / block) with wrapper unwrapping, glob and
  variable handling, and paths hidden in flags and `@file` references.
- Approval modes (`read-only`, `ask`, `auto-edit`, `unattended`) with a safety
  gate that never lets a block be approved.
- Allowlisted subprocess environments, secret redaction, prompt-injection
  flagging, and a redacted JSONL audit log.
- SSRF-protected `web_fetch`: public addresses only, pinned connections,
  per-hop redirect checks, and rejection of ambiguous numeric hosts.
- `AgentOptions.extra_gates` for project-specific policy rules.
- `web_fetch` without `allow_domains` always needs approval, so unattended
  runs cannot send data to arbitrary URLs.
- An adversarial test suite of known escape and bypass attempts.

### Tooling
- `guardrun` CLI: `run`, `chat`, `policy`, `audit`, `usage`, `sessions`, `init`, and
  `doctor` (Python, credential presence, shell, config, workspace permissions).
- `guardrun run --json` prints a single machine-readable result and never prompts.
- Strict `guardrun.toml` configuration.
- CI on Linux, macOS, and Windows for Python 3.10 to 3.14, a 95% coverage floor,
  CodeQL, and Dependabot.

[Unreleased]: https://github.com/hannnnnnnny/GuardRun/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hannnnnnnny/GuardRun/releases/tag/v0.1.0
