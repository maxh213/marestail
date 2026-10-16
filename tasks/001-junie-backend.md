# 001 — a junie backend, so `dandelion route` can send a session to Junie

After this task, `marestail run tasks/x.md --agent junie` runs every role on the JetBrains Junie CLI, and a `--model dandelion/route` run whose `dandelion route` prints `gemini-3.8-flash high junie` runs that session on junie, stamped `[gemini-3.8-flash high]`, instead of stopping with "dandelion route picked 'junie', which marestail has no backend for". Junie is a subscription with its own credit balance, so this adds one more pool of quota the fleet can drain.

## The Junie CLI (verified against Junie 26.9.14)

Junie is non-interactive when given a task, runs shell commands, edits files and commits unattended (verified: a task that ran `git add` and `git commit` in a scratch repo did so with no prompt), and needs no permission flag; `--brave` is interactive-only and must not be passed. The session command, run with the repo root as cwd:

```
junie --skip-update-check --input-format=json --output-format=json -p <repo root> --model=gemini-3.8-flash --effort=high
```

- The prompt goes on **stdin** as one JSON object, `{"task": "<the whole worker prompt>"}`, so the 128 KB argv limit that made kimi point at the prompt file does not apply; the prompt file under `.marestail/runs/<task>/` is still written as for every backend.
- `--model=<model>` only when the run has a model, exactly as kimi passes `-m`. Junie accepts, among others, `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.1-pro-preview`, `claude-opus-5`, `claude-fable-5-1`, `gpt-5.6-terra`, `grok-4.6`; an unknown model exits 1 with empty stdout and stderr `Junie failed with the message: Invalid model: no-such-model-xyz` followed by `Available models:` and one `- <id>` line per model.
- `--effort=<effort>` only when the effort is `low`, `medium` or `high`, the three values junie accepts. Any other effort (`xhigh`, `max`) is left off the command and still labels the commits, the way kimi's effort does.
- `MARESTAIL_JUNIE` names the binary, default `junie`, like `MARESTAIL_KIMI`. Name it in the README env-var list.

Verified stdout for a finished session, one JSON object (`errors`, a list of objects with `level` and `message`, is present only on failure; extra fields must be tolerated):

```json
{"sessionId":"session-260918-135149-1lx1","taskName":"Junie CLI Task Completion and Output Handling","result":"### Summary\n- pong\n\n### Changes\n- No files were created or modified as requested.\n\n### Verification\n- Verified that the repository remains empty and untouched.","changes":[],"llmUsage":[{"model":"gemini-3.8-flash","calls":17,"cost":0.06395658750000001,"inputTokens":91729,"cacheInputTokens":261869,"cacheCreateTokens":0,"outputTokens":10527},{"model":"gpt-5.4-nano","calls":13,"cost":0.01091355,"inputTokens":44499,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":1611},{"model":"gpt-4.1-mini-2025-04-14","calls":16,"cost":0.004483599999999999,"inputTokens":10037,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":293},{"model":"gpt-4.1-2025-04-14","calls":1,"cost":0.0015019999999999999,"inputTokens":743,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":2},{"model":"gemini-3.5-flash-lite","calls":1,"cost":4.9225E-4,"inputTokens":1969,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":0}]}
```

The helper models in `llmUsage` are junie's own; the first entry is the model the session asked for.

## Runner

- `--agent junie` and `[agent] backend = "junie"` select it; `route.py`'s `BACKENDS` maps the dandelion account `junie` to it with no account env, so `gemini-3.8-flash high junie` parses to backend `junie`, model `gemini-3.8-flash`, effort `high`.
- The session runs through the same four-hour cap as kimi and kilo. Exit code and stdout are kept; on a non-zero exit stderr is appended, as kimi does.
- Summary line for the run log, from the verified output above: `calls=48 tokens=423279 cost=$0.08 "### Summary - pong ..."`, where calls and cost are summed over `llmUsage`, tokens sums `inputTokens`, `cacheInputTokens` and `outputTokens`, cost is rounded to cents, and the quoted text is the first 100 characters of `result` with newlines collapsed to spaces. Output that is not JSON falls back to its last 200 characters, as kimi's summary does.
- Rate limit: a non-zero exit whose stdout, stderr or `errors[].message` matches the existing `LIMIT_PATTERN` or junie's own out-of-credit wording, `Your balance is exhausted`, `InsufficientAccountBalance` or `insufficient` followed by `balance`, counts as rate limited, so `invoke` waits and asks dandelion again. An exit 0 whose `result` merely mentions quota is not rate limited. `Invalid model` is a failure, not a limit.
- A judge's `VERDICT:` line inside `result` counts, as it does in kimi's and kilo's streams.
- Junie has no command Stop hook wired to marestail's gate; like kimi, workers run `marestail gate` themselves as `CLAUDE.md` and `AGENTS.md` tell them, and the cap is the timeout. Junie's own hook system is out of scope.
- The watch TUI's process list (`marestail/tui/collect.py` `BACKENDS`) recognises a running `junie` worker; `templates/marestail.toml` lists junie among the backends.

## README

Add junie to the backend list next to `marestail run`, to the `--effort` paragraph (junie takes `--effort` as `low`, `medium` or `high`), to the `dandelion/route` paragraph (junie runs its own CLI), a "Junie pipeline runs" paragraph beside kimi's with the command above and the stdin JSON, and `MARESTAIL_JUNIE`.

## Tests (pin every one)

Under `tests/`, faking the process at the seam the greened runner uses for kimi, plus the matching rows in `tools/test-agent-backends.py` and `tools/test-route.py`, which must keep passing with plain `python3`:

- `--agent junie`, model `gemini-3.8-flash`, effort `high` builds exactly `["junie", "--skip-update-check", "--input-format=json", "--output-format=json", "-p", "<root>", "--model=gemini-3.8-flash", "--effort=high"]`; with no model, no `--model`; with effort `xhigh`, no `--effort` and the stamp is still `[gemini-3.8-flash xhigh]`; `MARESTAIL_JUNIE=/opt/junie` replaces the binary.
- The stdin sent is `{"task": <prompt>}` and a prompt containing quotes, backslashes and newlines round-trips through `json.loads`.
- The verified output above summarises to `calls=48 tokens=423279 cost=$0.08 "### Summary - pong ..."` (the quoted text is its first 100 characters), and is not rate limited.
- Exit 1 with stderr `Your balance is exhausted.` is rate limited; exit 1 with `{"errors":[{"level":"ERROR","message":"InsufficientAccountBalance"}]}` is rate limited; exit 1 with `Junie failed with the message: Invalid model: no-such-model-xyz` is not; exit 0 with a result that says "the quota gate passed" is not.
- `dandelion route` output `gemini-3.8-flash high junie` parses to `("junie", "gemini-3.8-flash", "high", {})`; `resolve_agent` returns `junie` from `--agent`, from `MARESTAIL_AGENT=junie` and from `[agent] backend = "junie"`; the label for model `gemini-3.8-flash` effort `high` is `gemini-3.8-flash high`.
- A verdict file missing but `result` containing `VERDICT: BOUNCE to coder` parses to `("BOUNCE", "coder")`.

## Must not break

- Every existing backend's command, output readers and rate-limit rules; `tools/test-*.py` run directly with `python3`; the run-log wording for every other backend.
- 000's bar: this is Python under `marestail/` and stays inside its gate (100% coverage from `tests/`, CRAP at most 4, no comments, ruff, mypy strict, import contracts, vulture, docs) with no runtime dependency beyond the standard library.
