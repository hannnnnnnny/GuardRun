# Security model

GuardRun runs actions chosen by a language model on your machine. Model output
is treated as **untrusted input** everywhere, and several independent layers
limit what it can do. This document describes each layer and, just as
importantly, what it does not protect against.

## Threats considered

- The model misunderstands a task and does something destructive.
- Content the agent reads (a README, a web page pasted into a file, command
  output) contains prompt-injection text that tries to redirect it.
- Credentials on the machine end up in the model context, logs, or a network request.
- The agent tampers with its own oversight: git hooks, the audit log, or its config.

## Layer 1: input validation

Every tool call is validated against the tool's JSON schema before anything
runs. Tool definitions using schema keywords the validator does not enforce
are rejected at registration rather than being half-checked.

## Layer 2: the workspace sandbox (file tools)

- Paths are checked lexically first: NUL bytes, `~`, UNC/network paths,
  Windows device names (`CON`, `nul.txt`) and NTFS alternate data streams are
  rejected.
- Paths are then resolved **after following symlinks** and must stay inside
  the workspace root. `../`, absolute paths elsewhere, sibling-prefix tricks,
  and symlinks pointing out are all refused.
- **Protected paths** apply at any depth, case-insensitively:
  - never readable: `.env*`, private keys (`*.pem`, `*.key`, `id_rsa*`, ...),
    `.ssh/`, `.aws/`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`,
    `.git/config`, and GuardRun's own state in `.guardrun/`;
  - additionally never writable: everything under `.git/`.
  `glob`, `grep`, and `list_dir` skip protected files entirely, so they cannot
  be used to discover or read them. `.guardrun/` and `.git/` cannot be
  unprotected by configuration.
- Reads are size-capped and refuse binaries; writes are size-capped and atomic.

## Layer 3: the shell command policy

Each command line is parsed (quote-aware) into simple commands. Wrappers such
as `env`, `timeout`, `nice`, and `xargs` are unwrapped to find what really runs.
Every segment gets a verdict and the strictest one wins:

- **Block** (cannot be approved): privilege escalation (`sudo`, `su`, ...),
  system administration, `mkfs`/raw `dd`, recursive deletion or chmod of `/`,
  `~`, or the workspace; download-and-execute pipelines (`curl … | sh`);
  redirects that leave the workspace or hit protected files; arguments that
  name protected files (`cat .env`, `head ~/.ssh/id_rsa`); unparseable input.
- **Ask**: network tools, git operations that touch remotes or discard work,
  `git -c` overrides, package installs, `find -exec/-delete`, `rm`/`mv`,
  command substitution, paths outside the workspace, and any program that is
  not on the auto-approved list.
- **Allow**: read-only inspection commands, read-only git subcommands, and
  prefixes you configure (for example `pytest`).

Commands run with the workspace as the working directory, stdin closed, a
timeout that kills the whole process tree, capped output, and an
**allowlisted environment**: only variables such as `PATH`, `HOME`, and locale
settings are passed, and anything with a secret-looking name is dropped even if
allowlisted.

On Windows the shell is Git Bash. The `bash.exe` launchers in the Windows
directory and in WindowsApps start WSL instead (a different filesystem and
environment), so they are skipped.

`[mission] notify_command` runs outside the command policy because it comes
from your own config, like the policy itself. It receives notices only through
environment variables and gets the same scrubbed environment. A
`guardrun.toml` from a repository you do not trust deserves the same review as
any script in it.

## Layer 3b: network access

`web_fetch` is disabled unless `[network] enabled = true`. When enabled:

- only `http` and `https` URLs without embedded credentials are accepted, and an
  optional domain allowlist is enforced before any request is made;
- the host is resolved and **every** returned address must be public, which
  rules out loopback, private ranges, link-local (including `169.254.169.254`
  cloud metadata), and IPv4-mapped IPv6 tricks;
- the connection is pinned to the address that passed the check, so a DNS
  answer that changes afterwards (DNS rebinding) cannot redirect it, while TLS
  still verifies the certificate against the hostname;
- each redirect hop is re-validated, redirects are capped, only text content
  types are read, and bodies are size-limited;
- non-canonical numeric hosts (`127.1`, `0177.0.0.1`, `0x7f000001`) are refused,
  because resolvers on different platforms disagree about what they mean;
- without a domain allowlist every fetch needs human approval, because any URL
  can carry data out in its path or query string; unattended runs can only
  fetch allowlisted domains.

## Layer 4: approval modes and the safety gate

The `SafetyGate` combines the approval mode (by tool risk) with the tool's own
assessment. Blocks are final. Asks go to the approver; the default approver
denies everything, and the CLI only prompts when attached to a terminal. In
`unattended` mode every ask becomes a denial. "Approve for this session" is
keyed on the exact tool input, so approving `rm a.txt` never approves `rm -r src`.

## Layer 5: output filtering

Before any tool output reaches the model:

- **Secret redaction** replaces well-known credential formats (API keys,
  tokens, JWTs, private key blocks, `user:pass@` URLs, `password=...`
  assignments) and the literal values of secret-named environment variables.
- **Prompt-injection flagging** appends a notice to output that looks like
  instructions addressed to an AI. Content is never removed, so neither the
  model nor the user loses evidence.

The system prompt also tells the model that tool results are data, not instructions.

## Layer 6: limits and accountability

- Budgets stop runs after a number of turns, tokens, or dollars.
- A run that keeps failing is stopped as `stuck`.
- Every event is written, redacted, to `.guardrun/audit.jsonl` (mode `0600` on
  POSIX). The agent cannot read or write this directory.

## Adversarial test suite

`tests/adversarial/` is a catalogue of known attacks that must keep failing:
path escapes (traversal, absolute and drive-relative paths, device names,
symlinks, case tricks), shell policy bypasses (wrappers, chaining,
pipe-to-shell, globs and variables that expand to protected files, paths hidden
in `--flag=value` and `@file` references), SSRF address forms (decimal, octal,
hex, IPv4-mapped IPv6, metadata endpoints), end-to-end secret leakage, and a
model that obeys injected instructions. New bypasses should be added there
first, as failing tests.

## Known limitations

- **The shell is not a sandbox.** The command policy is defence in depth.
  Globs, variables, `eval`, interpreters (`python -c`), or scripts can reach
  things the parser cannot see. That is why unknown commands default to
  *ask*, and why you should only pre-approve commands you trust with arbitrary
  arguments. For untrusted workloads run GuardRun inside a container or VM.
- Redaction is pattern-based and can miss secrets in unusual formats that are
  not also present in the environment.
- Injection flagging is a heuristic. It raises the model's suspicion; it is not
  a guarantee.
- Approving an action approves what it does. Read the request before saying yes.
