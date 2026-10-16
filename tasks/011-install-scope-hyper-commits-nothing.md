# 011 — `marestail install --scope hyper` leaves nothing of marestail in the target's history

After this task, a developer can install marestail into a shared repository, run a hyper-scoped fix, and open a pull request that contains the fix and its tests and nothing else. No `marestail.toml`, no Sonar properties, no guidance, no tool configs, no lockfile, no `.gitignore` block, no agent docs.

Under hyper the expectation is that nobody else on the team runs marestail. StripeDonationPortal#365 shows what install costs today: a root `package.json`, an 8,810-line lockfile and six tool configs in a repository that had no JavaScript tooling. `--gitignore-generated` is not enough, because it still commits a block to the tracked `.gitignore`. Task 004 made `install --scope hard` leave `CLAUDE.md` and `AGENTS.md` alone. This task takes the same idea to its end.

## What changes

`marestail install --scope hyper`:

- **Touches no tracked file.** Not `.gitignore`, not `CLAUDE.md`, not `AGENTS.md`, not a Stop-hook settings file that is already tracked. It prints which tracked files it left alone.
- **Hides what it writes through git's local exclude file,** `$(git rev-parse --git-path info/exclude)`, which is never committed and is shared by every worktree of the clone. It adds a marked block listing `marestail.toml`, `sonar-project.properties`, `guidance/`, `tasks/`, `features/`, `qa/`, `perf/`, `PERFORMANCE.md`, `.marestail/`, the Stop-hook configs and every tool config it writes. Running install again does not duplicate the block.
- **Keeps `marestail.toml` at the repository root,** because that is where config is found, and excludes it.
- **Puts tooling out of tree,** under `.marestail/tooling/`: its own `package.json` and `node_modules` installed with `npm --prefix`, and the tool configs (ESLint, tsconfig, dependency-cruiser, Stryker, knip, Jest or Vitest where the repo has none of its own). Nothing is added to the target's own `package.json` or lockfile.
- **Points every gate at those configs by path** through a new `[ts] tooling = ".marestail/tooling"` key: ESLint `-c`, `tsc -p`, depcruise `--config`, the Stryker config argument, knip's config, the test runner binary and config, the complexity scanner's module lookup, and Sonar's `-Dproject.settings=`. With the key unset, every gate looks where it looks today.
- **Uses local hook files where a backend has one** (`.claude/settings.local.json` for Claude) so no tracked settings file changes. Where a backend has no local file, install says the Stop hook was not installed for it.
- `--scope hyper` implies everything `--scope hard` does in task 004.

The runner already untracks anything a worker force-adds when git would ignore it, and that check honours `info/exclude`, so tooling cannot slip into a commit during a run.

## What must not change

- `install` with no scope, with `--gitignore-generated`, and with `--scope hard`.
- A target that already has committed marestail files keeps them. This task removes nothing.
- Gates in a target with `[ts] tooling` unset.
- Python targets: the venv location and the Python tool lookups are out of scope here. Say so in the README; a later task can do the same for them.

## Tests

`tools/test-install-hyper.py`, in a temp git repo with a tracked `.gitignore` and a tracked `CLAUDE.md`:

- after `install --scope hyper`, `git status --porcelain` is empty and both tracked files are byte-for-byte unchanged
- `info/exclude` holds one marked block; a second install leaves one block
- `marestail.toml` exists at the root and `git check-ignore` reports it ignored
- `.marestail/tooling/package.json` exists and the target has no new root `package.json`
- with `[ts] tooling` set, the ESLint, tsc, depcruise and Stryker commands carry the config paths; with it unset they are the commands of today
- `git add -f marestail.toml` followed by the runner's untrack step removes it from the index
- a second worktree of the same clone sees the same exclusions

## Done when

The new test passes, the other `tools/test-*.py` still pass, and README has an "Installing into a repository that does not use marestail" section that walks through `install --scope hyper` and says what a reviewer will and will not see in the pull request.
