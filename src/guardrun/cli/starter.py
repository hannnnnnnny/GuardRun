"""The template written by ``guardrun init``."""

STARTER_CONFIG = """\
# guardrun configuration. Every key is optional; unknown keys are errors.

# Project-specific guidance appended to the system prompt.
instructions = ""
# Or keep it in a file inside the workspace, e.g. "AGENTS.md".
instructions_file = ""

[model]
# deepseek (key in DEEPSEEK_API_KEY) or anthropic (ANTHROPIC_API_KEY / `ant auth login`)
provider = "deepseek"
# Defaults to deepseek-v4-pro or claude-opus-5-5; also try "deepseek-flash".
# name = "deepseek-v4-pro"
effort = "high"          # low | medium | high | xhigh | max

[safety]
# read-only | ask | auto-edit | unattended
mode = "ask"
# Extra glob patterns the agent may never read or write.
protect = []
# Patterns to exempt from the built-in secret rules (e.g. ".env.example").
unprotect = []
audit = true

[shell]
# Command prefixes that run without asking, e.g. ["pytest", "npm test"].
allow = []
# Programs that are always blocked.
block = []

[budget]
max_turns = 50
# max_cost_usd = 5.0

[limits]
max_calls_per_turn = 25
max_identical_calls = 3
parallel_reads = true

[mission]
# Used by `guardrun mission`: how often the inspector may send a task back,
# how many tasks the planner may publish, and a cost cap for a whole mission.
max_attempts = 3
max_tasks = 20
# max_cost_usd = 20.0

[network]
# Enables the web_fetch tool. Private, local, and metadata addresses are never reachable.
enabled = false
# Optional: only these domains (and their subdomains) may be fetched.
allow_domains = []
"""
