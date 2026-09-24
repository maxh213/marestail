# 002 — a hermes backend, so `dandelion route` can send a session to Hermes Agent

After this task, `marestail run tasks/x.md --agent hermes` runs every role on Nous Research's Hermes Agent CLI, and a `--model dandelion/route` run whose `dandelion route` prints `x-ai/grok-4.6 xhigh hermes` runs that session on hermes, stamped `[x-ai/grok-4.6 xhigh]`, instead of stopping with "dandelion route picked 'hermes', which marestail has no backend for". Hermes runs on a Nous Portal subscription, so this adds one more pool of credits the fleet can drain.

## The Hermes CLI (verified against Hermes Agent v0.21.3)

The session command, run with the repo root as cwd:

```
hermes chat --query-file <prompt file> --oneshot -Q --format stream-json --yolo --accept-hooks --max-turns 1000 -m x-ai/grok-4.6 --reasoning xhigh
```

- `--query-file` reads the whole prompt from the file marestail already writes under `.marestail/runs/<task>/`, verbatim and with no shell interpretation, so nothing is passed in argv and the 128 KB limit does not apply. `--oneshot` with `-Q` answers and exits; `--yolo` bypasses dangerous-command approvals and `--accept-hooks` approves shell hooks without a TTY (verified: a task that ran `git add` and `git commit` in a scratch repo did so with no prompt).
- `-m <model>` only when the run has a model, as kimi passes `-m`. Hermes takes Nous model ids such as `x-ai/grok-4.6`, `anthropic/claude-fable-5.1`, `google/gemini-3.8-flash`, `openai/gpt-6-astra`.
- `--reasoning <effort>` whenever the run has an effort: hermes accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` and `ultra`, so every effort marestail knows passes through unchanged.
- `--max-turns 1000` lifts hermes's default cap of 150 agent iterations per turn (`agent.max_turns` in `~/.hermes/config.yaml`) so a long worker session is not ended early.
- `MARESTAIL_HERMES` names the binary, default `hermes`, like `MARESTAIL_KIMI`. Name it in the README env-var list.

Verified stdout, one JSON object per line: a `system`/`init` record, `text` deltas that concatenate to the answer, `tool_use` and `tool_result` records, and one final `result` record. Stderr carries deprecation warnings and a `session_id:` line and is noise.

```
{"type": "system", "subtype": "init", "model": "x-ai/grok-4.6", "session_id": "20260918_151301_be7c1c", "timestamp": 1789740781416}
{"type": "tool_use", "name": "terminal", "input": {"command": "git status --short"}, "timestamp": 1789740714068}
{"type": "tool_result", "name": "terminal", "output": "{\"output\": \"exit1=0\", \"exit_code\": 0, \"error\": null}", "duration_ms": 218, "is_error": false, "timestamp": 1789740714290}
{"type": "text", "text": "pong", "timestamp": 1789740800465}
{"type": "result", "session_id": "20260918_151301_be7c1c", "exit_code": 0, "text": "pong", "tokens": {"input": 14851, "output": 1, "total": 14980, "cache_read": 128, "cache_write": 0}, "duration_ms": 19100, "timestamp": 1789740800516}
```

The `result` record carries `"error": "<message>"` when the provider reported one, and its `exit_code` is the process exit code: `0` completed, `1` completed with no text, `2` failed or stopped partway, `130` interrupted. No cost is reported in this format.

## Runner

- `--agent hermes` and `[agent] backend = "hermes"` select it; `route.py`'s `BACKENDS` maps the dandelion account `hermes` to it with no account env, so `x-ai/grok-4.6 xhigh hermes` parses to backend `hermes`, model `x-ai/grok-4.6`, effort `xhigh`.
- The session runs through the same four-hour cap as kimi and kilo. Exit code and stdout are kept; on a non-zero exit stderr is appended, as kimi does.
- Summary line for the run log, from the verified result above: `tokens=14980 "pong"`, where tokens is `result.tokens.total` and the quoted text is the first 100 characters of `result.text` with newlines collapsed to spaces. Output that is not JSON falls back to its last 200 characters, as kimi's summary does.
- Rate limit: a non-zero exit whose `result.error`, stdout or stderr matches the existing `LIMIT_PATTERN` or hermes's own entitlement wording counts as rate limited, so `invoke` waits and asks dandelion again. Hermes's wording, from its `hermes_cli/auth.py`: `insufficient_credits`, `Subscription credits are exhausted`, `no_usable_credits`, `subscription_expired`, `subscription_required`, `member_spend_cap_exceeded`. An exit 0 whose text merely mentions quota is not rate limited; `Unknown --reasoning` and a bad model are failures, not limits.
- A judge's `VERDICT:` line inside `result.text` counts, as it does in kimi's and kilo's streams.
- Hermes has no command Stop hook wired to marestail's gate; like kimi, workers run `marestail gate` themselves as `CLAUDE.md` and `AGENTS.md` tell them, and the cap is the timeout. Hermes's own hooks are out of scope.
- The watch TUI's process list (`marestail/tui/collect.py` `BACKENDS`) recognises a running hermes worker: its process is `python -m hermes_cli.main chat ...` under `~/.hermes/hermes-agent/venv`, not a process named `hermes`, so match on `hermes_cli`. `templates/marestail.toml` lists hermes among the backends.

## README

Add hermes to the backend list next to `marestail run`, to the `--effort` paragraph (hermes takes `--reasoning` with every marestail effort), to the `dandelion/route` paragraph (hermes runs its own CLI), a "Hermes pipeline runs" paragraph beside kimi's with the command above, and `MARESTAIL_HERMES`.

## Tests (pin every one)

Under `tests/`, faking the process at the seam the greened runner uses for kimi, plus the matching rows in `tools/test-agent-backends.py` and `tools/test-route.py`, which must keep passing with plain `python3`:

- `--agent hermes`, model `x-ai/grok-4.6`, effort `xhigh` builds exactly `["hermes", "chat", "--query-file", "<prompt file>", "--oneshot", "-Q", "--format", "stream-json", "--yolo", "--accept-hooks", "--max-turns", "1000", "-m", "x-ai/grok-4.6", "--reasoning", "xhigh"]`; with no model, no `-m`; with no effort, no `--reasoning`; `MARESTAIL_HERMES=/opt/hermes` replaces the binary; the stamp is `[x-ai/grok-4.6 xhigh]`.
- The verified output above summarises to `tokens=14980 "pong"` and is not rate limited.
- Exit 2 with a result whose `error` is `Subscription credits are exhausted. Top up/renew credits, then retry.` is rate limited; exit 2 with stderr `insufficient_credits` is rate limited; exit 2 with `Unknown --reasoning 'ultrahigh'` is not; exit 0 with a text that says "the quota gate passed" is not.
- `dandelion route` output `x-ai/grok-4.6 xhigh hermes` parses to `("hermes", "x-ai/grok-4.6", "xhigh", {})`; `resolve_agent` returns `hermes` from `--agent`, from `MARESTAIL_AGENT=hermes` and from `[agent] backend = "hermes"`; the label for model `x-ai/grok-4.6` effort `xhigh` is `x-ai/grok-4.6 xhigh`.
- A verdict file missing but `result.text` containing `VERDICT: BOUNCE to coder` parses to `("BOUNCE", "coder")`.

## Must not break

- Every existing backend's command, output readers and rate-limit rules, including 001's junie; `tools/test-*.py` run directly with `python3`; the run-log wording for every other backend.
- 000's bar: this is Python under `marestail/` and stays inside its gate (100% coverage from `tests/`, CRAP at most 4, no comments, ruff, mypy strict, import contracts, vulture, docs) with no runtime dependency beyond the standard library.
