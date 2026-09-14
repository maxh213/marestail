# Marestail: unconditional mutation gates, Kimi CLI backend, install-time gitignore flag

## Overview

Three independent enhancements to marestail (a deterministic quality-gate gauntlet + agent-pipeline runner for coding agents):

1. **Mutation testing always runs on `--tier full`.** Today `py.mutation`/`ts.mutation` run unconditionally, but `ex.mutation` and `cs.mutation` skip unless `[elixir] mutation = true` / `[dotnet] mutation = true` is set, and `rb.mutation` is a permanent skip stub. A full-tier run must exercise real mutation testing in every configured language.
2. **Kimi CLI as an agent backend.** `marestail run --agent kimi --model <alias>` must work like the existing five backends (claude, agy, grok, cursor, kilo).
3. **Opt-in install flag to gitignore marestail's generated files.** `marestail install . --gitignore-generated` adds everything marestail creates (except `marestail.toml`) to the target repo's `.gitignore`, so marestail can be used on shared repos without committing feature files, QA scripts, hook configs, etc. Default behavior is unchanged.

## Context & Constraints

- Repo: this worktree is a checkout of `marestail` on branch `feat/marestail-mutation-kimi-install-gitignore`. **All work happens in this worktree** (`/home/max/workspace/marestail-marestail-mutation-kimi-install-gitignore`). Do not touch the main checkout at `/home/max/workspace/marestail` or any sibling worktree.
- Stack: stdlib-only Python ≥ 3.11, no `pyproject.toml`, run in place via `bin/marestail`. **No new dependencies.** Match existing code idioms (the project bans comments/docstrings in gated code; write the same clean style even though marestail does not gate itself).
- Key files:
  - Gates: `marestail/gates/rb_mutation.py` (currently a skip stub), `marestail/gates/ex_mutation.py:13` (opt-in check), `marestail/gates/cs_mutation.py` (opt-in check), `marestail/gates/py_mutation.py`, `marestail/gates/ts_mutation.py` (working examples to mirror). Gate contract: `run_gate(ctx) -> marestail.report.Result`; findings are repo-root-relative `path:line message` strings; missing required contracts **fail closed** with a remediation message; registry in `marestail/gates/__init__.py`.
  - Backends: `marestail/runner.py` — `resolve_agent` (line ~409), `agent_command` (~441), `grok_command` (~500), `kilo_command` (~521, the best template: JSONL-event parsing with `raw_decode` per line, `MARESTAIL_<NAME>` binary env override, default-model constant), rate-limit handling (`LIMIT_PATTERN`, `MARESTAIL_LIMIT_WAITS`/`MARESTAIL_LIMIT_WAIT_SECONDS`), `agent_label`/`stamped` commit stamps. CLI choices at `marestail/cli.py:66`. Tests at `tools/test-agent-backends.py`.
  - Install: `marestail/install.py` (`extend_gitignore` at line ~120 appends under a `# marestail` header; all steps are idempotent via the marker string `"marestail gate"`), install subcommand wiring in `marestail/cli.py`, `templates/`.
  - Self-checks: `tools/dryrun.sh` (full pipeline rehearsal with stub agent; success = exits 0 with 0 remaining plan lines), `tools/test-agent-backends.py` (prints `agent backends ok`), sample repos `tools/samples/ruby/` and `tools/samples/dotnet/` (intentionally red; each planted violation must fail its gate for the right reason, green when fixed).
- Kimi CLI facts (verified against https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command.html):
  - Non-interactive: `kimi -p "<prompt>"` — in `-p` mode no approvals are requested (auto permission policy), and `--yolo`/`--auto`/`--plan` are *rejected* in combination with `-p`. Do not add them.
  - Machine-readable output: `--output-format stream-json` (one JSON object per line on stdout; tool progress/notices go to stderr). Thinking content is not in the JSONL.
  - Model: `-m <alias>` / `--model <alias>` (aliases are user-configured, e.g. `kimi-code/kimi-for-coding`).
  - There is **no** reasoning-effort flag.
- Ruby mutation testing: the `mutant` gem (with `mutant-rspec` for rspec integration) is the standard. The sample repo `tools/samples/ruby/` uses rspec + SimpleCov with branch coverage.
- Assumptions made during planning (no interview was held; do not re-litigate, but flag in `PROGRESS-marestail-mutation-kimi-install-gitignore.md` if one proves wrong):
  - "Fix the mutation testing" means: remove every opt-in skip so all five languages' mutation gates execute on every full-tier run; wire Ruby up properly with mutant rather than leaving the skip stub.
  - muex / Stryker.NET / mutant become *required* for full tier in their language: if the tool is not installed in the target repo, the gate fails closed with a remediation message (consistent with how e.g. `cs_deps` treats a missing `.dotnet-layers.json`).
  - `--effort` with the kimi backend only labels commit stamps (like cursor), since kimi has no effort flag.
  - No Stop hook is installed for kimi (not requested; kimi hooks support can be a later change).
  - The install flag is named `--gitignore-generated`.

## Phases

### Phase 1: Mutation testing runs on every full-tier run

- Remove the opt-in check in `marestail/gates/ex_mutation.py` so the gate runs muex whenever `[elixir]` is configured; if muex is missing from the repo, fail with a remediation message (add `{:muex, "~> 0.9", only: [:dev, :test], runtime: false}` to mix.exs). Keep existing report parsing, PASSING statuses, and `--since`-style changed-scope behavior.
- Remove the equivalent opt-in check in `marestail/gates/cs_mutation.py` so dotnet-stryker runs whenever `[dotnet]` is configured; fail closed with a remediation message if the tool is absent. Keep the single-project-layout and Sentry source-generator guards.
- Implement `marestail/gates/rb_mutation.py` for real: run mutant via the repo's bundle (respect the existing `marestail/ruby.py` `bundle()` prefix and `[ruby]` config patterns used by the other rb gates), require machine-readable results, fail on any surviving/uncovered/errored mutant, and map `--scope changed` files to mutant subjects so only changed code is mutated. Fail closed with a remediation message when mutant is not in the bundle. Follow the conventions of the other mutation gates (timeouts ~7200s, capped findings, `Result.skipped` only when no ruby files are in scope).
- Update `templates/marestail.toml` (drop the now-dead mutation opt-in toggles), `README.md` (gate table: remove "(opt-in)" and the rb "skipped" note), and `.agents/skills/add-language/SKILL.md` / `.claude/skills/add-language/SKILL.md` if they mention the opt-ins.
- Wire `mutant` + `mutant-rspec` into `tools/samples/ruby/` (Gemfile, mutant config, any spec_helper hooks mutant-rspec needs) so its planted boundary mutant survives and `rb.mutation` reports it with a `path:line` finding; killing the mutant must flip the gate green.
- Verify: `tools/dryrun.sh` exits 0 with 0 remaining plan lines; `python3 tools/test-agent-backends.py` prints `agent backends ok`; `cd tools/samples/ruby && bundle install && ../../bin/marestail gate --tier full --only rb.mutation` fails naming the planted mutant in `lib/box.rb`; `cd tools/samples/dotnet && dotnet tool restore && ../../bin/marestail gate --tier full --only cs.mutation` runs Stryker (not "skipped") and fails on the planted survivors. If a toolchain (Elixir/mix) is unavailable on this machine, verify that gate by code review and record the gap in the PROGRESS file instead of skipping silently.

Deliverables: the three mutation gate files rewritten, template/README/skills updated, ruby sample wired for mutant.

### Phase 2: Kimi CLI backend with model passing

- Add `"kimi"` to the `--agent` choices in `marestail/cli.py:66` and to every backend-recognition point in `marestail/runner.py` (`resolve_agent` precedence stays: `--agent` flag > `MARESTAIL_AGENT` env > `[agent] backend` in toml > `claude`).
- Implement `kimi_command(state, prompt)` (mirroring `kilo_command`): binary from `MARESTAIL_KIMI` env defaulting to `kimi`; argv `kimi -p <prompt> --output-format stream-json` plus `-m <model>` when `--model`/`[agent] model` is set; no permission flags (see Kimi facts above). Deliver the prompt the same way the cursor/claude backends do (argv or stdin) — pick whichever the existing code structure makes cleanest and note the choice in PROGRESS.
- Parse the `stream-json` JSONL leniently (per-line `raw_decode`, like `kilo_events`): extract assistant text for the report/verdict fallback, extract turns/token/cost fields for the console summary when present, and detect rate-limit/quota errors so the existing `LIMIT_PATTERN` retry loop works for kimi.
- `--effort` with kimi only stamps commits (handled by the existing `agent_label` machinery; no argv change).
- Extend `tools/test-agent-backends.py` with assertions: exact `kimi_command` argv (with and without model), `MARESTAIL_KIMI` override, backend resolution for kimi, stream-json parsing including a malformed line, and rate-limit detection.
- Update `README.md` (`marestail run` backend list; note kimi has no effort flag; effort only labels commits).
- Verify: `python3 tools/test-agent-backends.py` prints `agent backends ok`; `marestail run --help` lists `kimi`; `tools/dryrun.sh` still exits 0; if a `kimi` binary is on PATH, run one real `marestail run tasks/<scratch>.md --agent kimi --from specifier --to specifier --auto` smoke test and record the result — if not installed, record the gap in PROGRESS.

Deliverables: kimi backend in runner/cli, backend tests, README update.

### Phase 3: `marestail install --gitignore-generated`

- Add `--gitignore-generated` (store_true) to the install subcommand in `marestail/cli.py` and thread it into `install.install()`.
- In `marestail/install.py`, when the flag is set, extend the target repo's `.gitignore` (under the existing `# marestail` header, idempotently — no duplicate lines on re-run) with the marestail-managed paths beyond the current list: `features/`, `qa/`, `tasks/`, `sonar-project.properties`, `.claude/settings.json`, `.agents/hooks.json`, `.grok/`, `.cursor/hooks.json`. Add `CLAUDE.md`/`AGENTS.md` only when this install created them fresh (if the file pre-existed, leave it tracked and do not gitignore it). `marestail.toml` is never gitignored.
- Without the flag, behavior is byte-for-byte identical to today (backwards compatible). Re-running install with the flag on an already-installed repo only appends the missing entries.
- Update `README.md` (document the flag and what it ignores, with the shared-repo use case).
- Verify (script these exact checks in a throwaway temp dir, e.g. `/tmp/marestail-install-test`, using `git init` first):
  1. `marestail install .` → `.gitignore` exists and does NOT contain `features/`.
  2. Fresh repo + `marestail install . --gitignore-generated` → `.gitignore` contains `features/`, `qa/`, `tasks/`, `sonar-project.properties`, `.claude/settings.json`, `.agents/hooks.json`, `.grok/`, `.cursor/hooks.json`, and does NOT contain `marestail.toml`.
  3. Run step 2's command twice → the `.gitignore` lines appear exactly once.
  4. Repo with a pre-existing `CLAUDE.md` → install with the flag → `CLAUDE.md` is NOT added to `.gitignore`; repo without one → it IS added.

Deliverables: flag wired through cli/install, README documentation.

## Success Criteria (all must be true)

- [ ] `marestail gate --tier full` in `tools/samples/ruby` runs `rb.mutation` (output contains no `rb.mutation ... skipped`) and fails with a `lib/box.rb`-pointing finding.
- [ ] Killing the planted mutant in `tools/samples/ruby` flips `rb.mutation` to ok (red → green acceptance loop holds).
- [ ] `ex.mutation` and `cs.mutation` contain no `mutation` opt-in config check; `grep -rn "mutation = true" marestail/gates/ templates/ README.md` returns nothing.
- [ ] `cd tools/samples/dotnet && marestail gate --tier full --only cs.mutation` executes Stryker.NET rather than skipping.
- [ ] `python3 tools/test-agent-backends.py` prints `agent backends ok` and includes kimi assertions.
- [ ] `marestail run --help` shows `kimi` in the `--agent` choices; `kimi_command` builds `kimi -p … --output-format stream-json` and appends `-m <model>` only when a model is given.
- [ ] `tools/dryrun.sh` exits 0 with 0 remaining plan lines after all changes.
- [ ] All four Phase-3 install verification scenarios pass exactly as specified.
- [ ] `README.md` documents the kimi backend and the `--gitignore-generated` flag; no README text still claims ex/cs mutation is opt-in or rb mutation is unwired.

## Out of Scope

- A kimi Stop hook (`gate --hook` integration) — not requested; do not build unless asked.
- Gleam or any new language gates; changes to non-mutation gates.
- Packaging/publishing (no pyproject, no releases).
- Reformatting or "improving" unrelated files. Touch only what the phases name.
- Merging the branch or pushing; the worktree stays on disk for human review.

## Rules for the Implementing Agent

- Never delete, skip, or weaken a test or a planted sample violation to make it pass; flag suspect ones in `PROGRESS-marestail-mutation-kimi-install-gitignore.md` instead.
- Record failed approaches and key decisions in `PROGRESS-marestail-mutation-kimi-install-gitignore.md` as you go.
- Commit after each completed phase.
- Stay inside this worktree. Do not touch sibling worktrees or the main checkout.
- Gates fail closed: a missing required tool/config is a FAIL with a remediation message, never a silent skip, never a crash.
- No new dependencies; stdlib Python only on the marestail side.
