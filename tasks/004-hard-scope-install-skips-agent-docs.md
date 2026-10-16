# 004 — a hard-scoped install leaves CLAUDE.md and AGENTS.md alone

After this task, a developer who hard-scopes marestail to one file in a shared repo can run `marestail install --scope hard .` and open a pull request that adds no marestail text to `CLAUDE.md` and creates no `AGENTS.md`. Hard scope means only the focus paths are gated, and the expectation that goes with it is that the rest of the team is not running marestail. A "Gate" section telling every agent in the repo to run `marestail gate` until it passes is wrong for them: they have no marestail, and the gate does not cover their files.

Today `install` always calls `append_instructions` for both files. On otwarteklatki/next-boilerplate#537 (a hard-scoped run focused on `components/blocks/DonorboxWidget/DonorboxWidget.tsx`) the install commit `5f13970` appended the six-line Gate section to the team's `CLAUDE.md` and created a new `AGENTS.md` holding nothing but that section. Both went into the pull request and had to be taken out by hand.

## What changes

- `marestail install` takes `--scope hard`, using the same `add_scope` choices as `gate` and `run`. Any other value, or no `--scope`, installs exactly as now.
- With `--scope hard`, `install` does not call `append_instructions` at all. An existing `CLAUDE.md` or `AGENTS.md` is left byte-for-byte as it was. A missing one is not created.
- With `--scope hard`, `install` behaves as if `--gitignore-generated` was also passed. The two flags answer the same question (not everyone here runs marestail), so nobody should have to remember both.
- The closing `installed into …` line says, under `--scope hard`, that `CLAUDE.md` and `AGENTS.md` were left alone.
- Workers lose nothing. Under `run` they get the gate rules from the role prompt and the `# Scope` section, and the Stop hooks that `install` still merges keep enforcing `marestail gate --hook`.

## What must not change

- `install` with no `--scope`: every file it writes today, including the Gate section in `CLAUDE.md` and `AGENTS.md`, and the `GATE_MARKER` check that stops a second append.
- `--gitignore-generated` without `--scope hard`.
- Everything else `--scope hard` installs: `marestail.toml`, `sonar-project.properties`, `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md`, `guidance/cs.md`, the four Stop-hook configs, the Grok folder trust.
- `CLAUDE.md`, `AGENTS.md` and `GEMINI.md` stay in the freeze list. A worker still may not edit them.
- `gate --scope hard` and `run --scope hard` behaviour, `hook_scope`, `MARESTAIL_SCOPE` / `MARESTAIL_FOCUS`.
- A repo that already has the Gate section from an earlier install keeps it. This task removes nothing from a target.

## Tests

Extend the install cases in `tools/test-practices.py`, or add `tools/test-install-hard.py`, each in a temp directory:

- `install(target)` on an empty target still creates `CLAUDE.md` and `AGENTS.md`, both containing `marestail gate`
- `install(target, hard=True)` on an empty target creates neither file
- `install(target, hard=True)` on a target whose `CLAUDE.md` holds `team rules\n` leaves it as `team rules\n` and creates no `AGENTS.md`
- `install(target, hard=True)` writes the `GITIGNORE_GENERATED_LINES` into `.gitignore` without `gitignore_generated=True`
- `install(target, hard=True)` still writes `marestail.toml`, `guidance/ts.md` and `.claude/settings.json` with the gate Stop hook
- `marestail install --scope hard <dir>` through the CLI gives the same result as `install(target, hard=True)`, and `marestail install --scope all <dir>` the same as no flag
- the printed line mentions `CLAUDE.md` only under `--scope hard`

## Done when

The new tests pass, `python3 tools/test-practices.py`, `python3 tools/test-perf.py` and `python3 tools/test-scope-hard.py` still pass, and README says, in the install line of the quick start and in the `--gitignore-generated` paragraph, that `install --scope hard` leaves `CLAUDE.md` and `AGENTS.md` alone and implies `--gitignore-generated`. The sentence there that says the two files are shared documentation now applies to a full install only.
