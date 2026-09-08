---
name: add-language
description: Add a new language or framework to marestail's gates. Use when asked to support Erlang, Ruby, Go, Rust, a Next.js or Rails app, or any stack the gates do not cover yet.
---

# Adding a language or framework to marestail

marestail is a set of deterministic gates plus a role pipeline. Adding a language means giving each gate a way to measure that language. Adding a framework usually means configuration and templates, not new gates. Decide which you are doing first.

## The gate contract

A gate is one module in `marestail/gates/` exposing `run_gate(ctx: Context) -> Result`.

- `Context` (`marestail/context.py`) gives you `ctx.root` (repo root), `ctx.work` (`.marestail/`), `ctx.config.get(section, key, default)`, `ctx.scope_changed` and `ctx.changed` (repo-relative paths), and `ctx.changed_under(folder, suffixes)`.
- `Result` (`marestail/report.py`) is `Result(gate, ok, summary, findings, seconds)`. Findings are strings starting with a repo-relative `path:line`, one problem per line, so an agent can act on each without reading anything else. Use `Result.skipped(gate, why)` when the gate does not apply.
- Register it in `registry()` in `marestail/gates/__init__.py` with a tier: `FAST` for anything under a minute, `SONAR` for the Sonar gate, `FULL` for mutation testing, `QA` for end-to-end. The `section` argument names the `marestail.toml` section that must exist for the gate to run, or `None` to run whenever any language applies.
- Run tools with `marestail.shell.run(command, cwd, env, timeout, stdin)`; it returns `(exit_code, cleaned_output)` and never raises on a missing binary (exit 127).

## Rules that keep gates trustworthy

1. **Read machine-readable reports, never prose.** Coverage JSON, mutation meta files, JSON reporters. Text output changes between tool versions and cannot be filtered per file.
2. **Fail closed.** No coverage file, zero mutants generated, an empty report, a tool that is not installed: all of these are failures with a message saying what to install or run, never a pass. The mutation gate once passed on empty `mutmut results` output because that command lists only problem mutants; do not repeat that.
3. **Respect `ctx.scope_changed`.** Filter findings to `ctx.changed` so the Stop hook and `--scope changed` stay fast and relevant. Whole-repo is the default for the runner's own verification.
4. **Never parse the agent's words.** The gate looks at files, reports and git.
5. **No thresholds in prompts.** Thresholds live in `marestail.toml` defaults inside the gate module and in the config template. The role prompts only say "pass the gate".
6. **Exclude generated and test code** from analysis where the tool cannot tell, and say so in the gate's summary.

## The mapping table to fill in

| Gate | What it needs from the ecosystem | Python answer | TypeScript answer |
|---|---|---|---|
| tests | a runner and a per-line, per-branch coverage report, ideally per-function | pytest + coverage.py JSON (`functions` key) | vitest + v8 istanbul JSON |
| crap | cyclomatic complexity per function with line ranges, joined to coverage by line | radon `cc -j` | `marestail/js/ts_complexity.mjs` on the TypeScript AST |
| mutation | a mutation tester whose results are readable per mutant, runnable on a subset of files | mutmut 3, `.meta` files, module-name patterns | Stryker with the JSON reporter, `--mutate` |
| deps | a dependency-direction contract checker | import-linter contracts in `pyproject.toml` | dependency-cruiser rules |
| lint | type checker in strict mode plus a linter with a complexity rule | mypy `--strict`, ruff with `C90` | tsc, typescript-eslint strict with `complexity` |
| comments | a tokenizer or AST that exposes comment ranges | `tokenize` + `ast` docstrings | `marestail/js/ts_comments.mjs` |
| depth | public symbols and statements per module, pass-through detection | `ast` | `marestail/js/ts_depth.mjs` |
| deadcode | definitions unreachable from entry points | vulture | knip |
| sonar | a Sonar analyzer for the language and a coverage import format | `sonar.python.coverage.reportPaths` | `sonar.javascript.lcov.reportPaths` |
| qa | any command | `[qa] cmd` | `[qa] cmd` |

Start with tests, crap, lint, comments and deps. Add mutation when the ecosystem has a real tool. Add depth and deadcode when you can get at the AST.

## Where the pieces go

- Gates: `marestail/gates/<lang>_<gate>.py`, or extend the language-neutral ones (`comments`, `depth`, `deadcode`) with a new branch keyed on a config section.
- Helper scripts for a language's own AST tooling: `marestail/js/` style, invoked through the target's own installed toolchain (see how `ts_complexity.mjs` uses `createRequire` on the target's `typescript`).
- Config: a new section in `marestail.toml` (`[erlang] root = ..., crap_max = 4`), documented in `templates/marestail.toml`.
- Frozen files: add the language's gate config filenames to `GATE_CONFIG` in `marestail/freeze.py`.
- Templates: dependency-contract and tool config examples under `templates/`.
- `marestail graph` and `marestail depth`: add a branch in `marestail/graph.py` and `marestail/depth.py`.
- README: a row in the gates table and a paragraph if the language needs care.

## Frameworks

A framework changes configuration, exclusions and the QA command, rarely the gates. A Next.js adapter is a Jest variant of the tests gate if the project will not move to vitest, dependency-cruiser layers for `app`, `components` and `lib`, coverage exclusions for server components covered by Playwright instead, and knip's Next plugin. Ship it as a template folder and a section in the README, not as new gate files.

## Verifying a new language

1. **Scratch dry run of the pipeline** with the scripted stand-in: `tools/dryrun.sh`. This proves the runner, freeze, audit, judge and bounce mechanics; it does not exercise your gates.
2. **A tiny real project** in the new language, committed under `tools/samples/<lang>/` if small. It must have: one function with complexity above the limit, one uncovered branch, one comment, one surviving mutant, one dependency-rule violation, one pass-through function. Run `marestail gate --tier full` and confirm every gate goes red for the right reason with a `path:line` finding. Fix each and confirm green.
3. **Scope check**: change one file, run `--scope changed`, confirm only that file's findings appear.
4. **Fail-closed check**: uninstall the tool or delete the report and confirm the gate fails with an install message.
5. **Timing**: note each gate's seconds; anything over a minute moves to the `FULL` tier.

## When the ecosystem lacks a tool

Say so in the gate summary (`Result.skipped("erlang.mutation", "no mutation tester for Erlang; see README")`) and in the README. Do not approximate a mutation tester with coverage, or a complexity metric with line counts. A missing gate is visible; a fake one is trusted.

## Checklist before committing

- [ ] every new gate fails closed
- [ ] findings are `path:line` strings relative to the repo root
- [ ] `--scope changed` filters correctly
- [ ] config defaults live in the gate module and `templates/marestail.toml`
- [ ] gate config files are in `freeze.GATE_CONFIG`
- [ ] README table and, if needed, a paragraph
- [ ] the sample project goes red then green
