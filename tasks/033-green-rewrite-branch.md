+++
depends = ["032-green-worker-merges-and-runs"]
stack = true
+++
# 033 — `marestail green rewrite`: 1-to-1 commit parity onto a `<stack>-rewrite` branch

After this task, `marestail green` can maintain a full rewrite of a repository in another language on a `<stack>-rewrite` branch (for example `dotnet-rewrite` or `rust-rewrite` tracking `main`) with 1-to-1 commit parity:

1. **Base rewrite (`001-rewrite-<stack>-base-<short-sha>`)**: Setting up a rewrite on an existing project creates the `<stack>-rewrite` branch in an isolated worktree under `.marestail/green/`, configures `<stack>`'s gates and rulebooks in the worktree, and queues task `001-rewrite-<stack>-base-<short-sha>` to rewrite the existing codebase at `HEAD` into `<stack>` with 1-to-1 functional parity and pass `marestail gate --tier full`.
2. **Ongoing commit parity (`002-rewrite-<stack>-<short-sha>`, `003-…`)**: Every subsequent commit on the tracked source branch triggers `marestail green on-commit`, which appends a stacked task `<seq>-rewrite-<stack>-<short-sha>` carrying that commit's subject, changed file list, and `git show --stat --patch <sha>` diff, and runs the background worker on `<stack>-rewrite` so every commit on the source branch is ported 1-to-1 and greened in `<stack>`.

## Commands and configuration

Extend `add_green` in `marestail/cli.py` with a `rewrite` subcommand after `worker`:

```
marestail green rewrite <stack> [TARGET] [--branch BRANCH] [--no-spawn]
```

| argument | details |
|---|---|
| `stack` | positional, `choices=REWRITE_STACKS` where `REWRITE_STACKS = ("dotnet", "elixir", "erlang", "java", "python", "ruby", "rust", "ts")` |
| `target` | positional, `nargs="?"`, `default="."` |
| `--branch` | optional source branch to track (defaults to the current checked-out branch in `TARGET`) |
| `--no-spawn` | `action="store_true"`, help `prepare the rewrite branch and base task without starting the worker` |

Subcommand help text for `rewrite`: `open a <stack>-rewrite branch with a base rewrite task and track commits 1-to-1`. Update `tests/test_green_repo.py` (`test_subcommand_help_lists_its_flags_in_order`) if it inspects nested subcommands.

Extend `[green]` in `marestail.toml` with three optional keys:

| key | type | default | effect |
|---|---|---|---|
| `rewrites` | array of strings from `REWRITE_STACKS` | `[]` | stacks that `on-commit` automatically initializes (if not yet initialized) and tracks 1-to-1 on every commit |
| `green_branch` | bool | `true` | when `false`, `on-commit` skips the same-language `green-from-<sha>` branch and only updates `<stack>-rewrite` branches |
| `rewrite_scope` | string (`"all"` or `"changed"`) | `"all"` | `--scope` value the worker passes to `marestail run` for `<stack>-rewrite` tasks |

## Stack metadata

In `marestail/green.py`, map each stack in `REWRITE_STACKS` to its `marestail.toml` section name and its guidance file stem under `templates/guidance/`:

| `stack` | `marestail.toml` section | guidance stem (`templates/guidance/<stem>.md`) |
|---|---|---|
| `dotnet` | `dotnet` | `cs` |
| `elixir` | `elixir` | `ex` |
| `erlang` | `erlang` | `er` |
| `java` | `java` | ` None` (no `java.md` in `templates/guidance/`) |
| `python` | `python` | `py` |
| `ruby` | `ruby` | `rb` |
| `rust` | `rust` | `None` (no `rs.md` in `templates/guidance/`) |
| `ts` | `ts` | `ts` |

Also define `REWRITE_ALLOW`: the manifest and layer files that `coder`, `cleaner`, and `architect` must be allowed to create or edit inside a rewrite worktree via `[freeze.allow]` in the worktree's `marestail.toml`:

```python
REWRITE_ALLOW = [
    "marestail.toml",
    "sonar-project.properties",
    "pyproject.toml",
    "**/setup.cfg",
    ".importlinter",
    "**/.coveragerc",
    "package.json",
    "**/.dependency-cruiser.cjs",
    "**/stryker.config.*",
    "**/vite.config.*",
    "**/vitest.config.*",
    "**/eslint.config.*",
    "**/tsconfig*.json",
    "**/knip.json*",
    "**/knip.config.*",
    "mix.exs",
    "mix.lock",
    ".formatter.exs",
    ".credo.exs",
    "rebar.config",
    "rebar.lock",
    "**/*.app.src",
    "Gemfile",
    "Gemfile.lock",
    ".rubocop.yml",
    "**/.rubocop.yml",
    ".rspec",
    ".ruby-version",
    ".ruby-layers.json",
    "**/mutant.yml",
    "**/mutant.yaml",
    "**/*.csproj",
    "**/*.sln",
    "**/*.props",
    "**/*.targets",
    "**/.editorconfig",
    "**/.globalconfig",
    "**/*.runsettings",
    "**/stryker-config.*",
    ".dotnet-layers.json",
    "**/Cargo.toml",
    "Cargo.lock",
    "**/clippy.toml",
    "**/.clippy.toml",
    "**/rustfmt.toml",
    "**/.rustfmt.toml",
    ".rust-layers.json",
    "**/pom.xml",
    "**/pmd-ruleset.xml",
    ".java-layers.json",
]
```

## `marestail green rewrite <stack> [TARGET] [--branch BRANCH] [--no-spawn]`

1. Resolve `target = Path(args.target).resolve()`. Verify `git rev-parse --show-toplevel` exits 0 in `target`; if not, print `marestail green rewrite needs a git repository: <target>` and exit 1. Let `repo_root` be the resolved toplevel path.
2. Ensure the `post-commit` hook is installed in `repo_root` (if `# marestail-green-hook` is not already present in the hook file, install it quietly using the hook installer from 031).
3. Resolve `source_branch`: `args.branch` when provided, else `git symbolic-ref --quiet --short HEAD` in `repo_root`. If `source_branch` is empty, starts with `green-from-` or `marestail/`, or ends with `-rewrite`, print `marestail green rewrite needs a source branch` and exit 1.
4. Resolve `<head_sha>` with `git rev-parse --verify --quiet refs/heads/<source_branch>` in `repo_root`. If that exits non-zero, print `unknown source branch: <source_branch>` and exit 1.
5. Compute:
   - `branch_key = source_branch.replace("/", "__")`
   - `state_key = f"{branch_key}__{stack}-rewrite"`
   - `branch_dir = repo_root / ".marestail" / "green" / state_key`
   - `green_branch`: `f"{stack}-rewrite"` unless another `state.json` under `<repo_root>/.marestail/green/` with `source_branch != <source_branch>` already has `green_branch == f"{stack}-rewrite"`, in which case `f"{branch_key}-{stack}-rewrite"`.
6. If `<branch_dir>/state.json` already exists:
   - Print `rewrite branch <green_branch> already initialized for <source_branch>`.
   - If `--no-spawn` is not set, `auto_run` is `True`, any commit in `state["commits"]` has `status == "pending"`, and `not is_worker_alive(state.get("worker_pid"))`, spawn `marestail green worker --branch <green_branch>` and record `worker_pid`.
   - Exit 0.
7. Otherwise initialize the rewrite cycle at `<head_sha>`:
   - `short_sha = head_sha[:7]`
   - `subject` = output of `git log -1 --format=%s <head_sha>` in `repo_root`.
   - Create the worktree at `<branch_dir>/worktree` on branch `<green_branch>` at `<head_sha>`: if `<worktree>` exists, remove it first with `git worktree remove --force <worktree>`, then run `git worktree add -B <green_branch> <worktree> <head_sha>`.
   - Collect the existing repository files at `<head_sha>` via `git ls-tree -r --name-only <head_sha>` in `repo_root`, filtering out paths that equal or start with `.git`, `.marestail/`, `tasks/`, `features/`, `qa/`, `guidance/`, `perf/`, `CLAUDE.md`, `AGENTS.md`, `PERFORMANCE.md`, `marestail.toml`, or `sonar-project.properties`.
   - Create the base rewrite entry:
     ```json
     {
       "seq": 1,
       "sha": "<head_sha>",
       "short_sha": "<short_sha>",
       "subject": "Base rewrite to <stack> (<subject>)",
       "task_id": "001-rewrite-<stack>-base-<short_sha>",
       "kind": "base",
       "status": "pending",
       "green_head": null
     }
     ```
   - Write `<branch_dir>/tasks/001-rewrite-<stack>-base-<short_sha>.md` and `.feature` (format below).
   - Write `<branch_dir>/state.json`:
     ```json
     {
       "v": 1,
       "mode": "rewrite",
       "stack": "<stack>",
       "source_branch": "<source_branch>",
       "green_branch": "<green_branch>",
       "anchor_sha": "<head_sha>",
       "base_sha": "<head_sha>",
       "worktree": "<abs_path_to_worktree>",
       "worker_pid": null,
       "commits": [ ... ]
     }
     ```
   - Print `initialized <green_branch> from <source_branch> at <short_sha>`.
   - If `--no-spawn` is not set and `auto_run` is `True`, spawn `marestail green worker --branch <green_branch>` and save `worker_pid` in `state.json`.
   - Exit 0.

## `marestail green on-commit` with rewrite branches

Update `on-commit` in `marestail/green.py`:

1. **Branch guard**: in addition to ignoring branches starting with `green-from-` or `marestail/`, exit 0 quietly when the current branch ends with `-rewrite`.
2. **Same-language `green-from-<sha>` cycle**: run the 031 cycle logic for `<source_branch>` when `config.get("green", "green_branch", True)` is `True`. When `green_branch = false`, skip creating or updating `<repo_root>/.marestail/green/<branch_key>/state.json`.
3. **Auto-initializing `[green] rewrites`**: for each `<stack>` in `config.get("green", "rewrites", [])` (validated against `REWRITE_STACKS`):
   - If `<repo_root>/.marestail/green/<branch_key>__<stack>-rewrite/state.json` does not exist yet, initialize it at `<head_sha>` with the base rewrite entry `001-rewrite-<stack>-base-<short_sha>` (exact same state and task files as `marestail green rewrite <stack> --no-spawn`).
4. **Appending to existing rewrite branches**: for every `<repo_root>/.marestail/green/<branch_key>__*-rewrite/state.json` whose `state.get("mode") == "rewrite"` and `state.get("source_branch") == source_branch`:
   - If `<head_sha>` is already in `state["commits"]` (by `sha`) — including when step 3 just initialized the base rewrite at `<head_sha>` — do not append a duplicate entry; just ensure the worker is spawned if `--no-spawn` is not set and `auto_run` is `True`.
   - Otherwise append a commit-parity entry:
     - `stack = state["stack"]`
     - `seq = len(state["commits"]) + 1`
     - `task_id = f"{seq:03d}-rewrite-{stack}-{short_sha}"`
     - `prev_task_id = state["commits"][-1]["task_id"]`
     - `entry = {"seq": seq, "sha": head_sha, "short_sha": short_sha, "subject": subject, "task_id": task_id, "kind": "commit", "status": "pending", "green_head": None}`
     - Read `patch` from `git show --stat --patch <head_sha>` in `repo_root`.
     - Write `<branch_dir>/tasks/<task_id>.md` and `<branch_dir>/tasks/<task_id>.feature` (format below), save `state.json`, and spawn `marestail green worker --branch <green_branch>` if `--no-spawn` is not set, `auto_run` is `True`, and `not is_worker_alive(state.get("worker_pid"))`.

## Generated task and feature files for rewrites

1. **Base rewrite (`kind == "base"`, `task_id = f"001-rewrite-{stack}-base-{short_sha}"`)**:
   - `<task_id>.md`:
     ```markdown
     +++
     depends = []
     +++
     # Base rewrite to <stack> at <short_sha>: <raw_subject>

     This is a full rewrite of the repository at commit `<sha>` (`<raw_subject>`) on `<source_branch>` into `<stack>` on branch `<green_branch>`. Deliver 1-to-1 functional parity with the existing system in idiomatic `<stack>`, remove the old language implementation files from `<green_branch>`, and bring the entire `<stack>` codebase and test suite through `marestail gate --tier full`.

     You can inspect any original file from `<source_branch>` at `<short_sha>` with `git show <sha>:<path>`.

     ## Existing source files at <short_sha>

     - `<path 1>`
     - `<path 2>`
     ```
     (where `<raw_subject>` is the commit subject of `<sha>`, and `- (none)` is written when the file list is empty).
   - `<task_id>.feature`:
     ```gherkin
     Feature: Base rewrite to <stack> at <short_sha>

       Scenario: Rewrite the system at <short_sha> into <stack> with 1-to-1 parity
         Given the existing system at commit <short_sha> (<raw_subject>) on <source_branch>
         When the system is rewritten into <stack> on <green_branch>
         Then all behaviour of commit <short_sha> is preserved in <stack> and the gate passes
     ```

2. **Commit parity (`kind == "commit"`, `task_id = f"{seq:03d}-rewrite-{stack}-{short_sha}"`)**:
   - `<task_id>.md`:
     ```markdown
     +++
     depends = ["<prev_task_id>"]
     stack = true
     +++
     # Rewrite commit <short_sha> into <stack>: <subject>

     Port the exact behaviour changes of commit `<sha>` (`<subject>`) from `<source_branch>` into the `<stack>` codebase on `<green_branch>` with 1-to-1 parity, and bring the `<stack>` code and tests through `marestail gate --tier full`.

     ## Changed files on <source_branch>

     - `<path 1>`
     - `<path 2>`

     ## Source commit `<short_sha>`

     ```diff
     <git show --stat --patch <sha> output>
     ```
     ```
   - `<task_id>.feature`:
     ```gherkin
     Feature: Rewrite commit <short_sha> into <stack>

       Scenario: Port commit <short_sha> into <stack> with 1-to-1 parity
         Given commit <short_sha> (<subject>) on <source_branch>
         When its behaviour changes are ported into <stack> on <green_branch>
         Then 1-to-1 parity with commit <short_sha> is preserved in <stack> and the gate passes
     ```

## Worker behaviour on `<stack>-rewrite` branches

Update `marestail green worker` in `marestail/green.py`:

1. **Branch selection (`--branch BRANCH`)**:
   - Match every `state.json` under `<repo_root>/.marestail/green/*/` where `state.get("source_branch") == BRANCH` or `state.get("green_branch") == BRANCH` (sorted by `(state["source_branch"], state["green_branch"])`). If no state file matches `BRANCH`, print `no green state for branch <BRANCH>` and exit 1.
2. **Preparing a rewrite worktree (`state.get("mode") == "rewrite"`)**:
   - Ensure `.git/info/exclude` in `repo_root`'s git dir contains the `# marestail-green-exclude` block updated to cover rewrite task and feature files as well:
     ```
     # marestail-green-exclude
     .marestail/
     tasks/*-green-*.md
     features/*-green-*.feature
     tasks/*-rewrite-*.md
     features/*-rewrite-*.feature
     # end marestail-green-exclude
     ```
   - Write `<worktree>/marestail.toml` configured for `stack = state["stack"]`:
     - Extract the `[<section>]` table for `stack` (and `[sonar]`, `[deadcode]`, `[comments]`) from `templates/marestail.toml`.
     - Set `[git]\nbase = "<base_sha>"`.
     - Include `[freeze.allow]` granting `coder`, `cleaner`, and `architect` `REWRITE_ALLOW` so workers in the rewrite worktree can create and update `<stack>` project files, layer files, and `marestail.toml` without `freeze.frozen_paths` reverting them.
   - If the stack has a guidance stem `<stem>` in `templates/guidance/<stem>.md`, copy `templates/guidance/<stem>.md` to `<worktree>/guidance/<stem>.md` (and `templates/guidance/patterns/<stem>.md` to `<worktree>/guidance/patterns/<stem>.md` if it exists).
3. **Draining rewrite commits**:
   - Do **not** run `git merge` in a rewrite worktree when `entry["seq"] > 1`: `<green_branch>` contains the rewritten `<stack>` files, and the source commit's diff is already embedded in `<branch_dir>/tasks/<task_id>.md`.
   - Copy `<branch_dir>/tasks/<task_id>.md` to `<worktree>/tasks/<task_id>.md` and `<branch_dir>/tasks/<task_id>.feature` to `<worktree>/features/<task_id>.feature`.
   - Determine `<scope>`: `config.get("green", "rewrite_scope", "all")`.
   - Run `[sys.executable, "-u", str(Path(__file__).resolve().parent / "cli.py"), "run", f"tasks/{task_id}.md", "--from", "coder", "--to", "hardener", "--auto", "--scope", scope]` with `cwd=worktree` and `env={**os.environ, "MARESTAIL_GREEN_ACTIVE": "1"}` via `run_task`.
   - Unlink the worktree copies of `<task_id>.md` and `<task_id>.feature` in a `finally` block, re-read `state.json` so commits queued during `run_task` are kept, update `entry["status"]` and `entry["green_head"]`, and save `state.json`.

## Tests

Extend `tests/test_green.py` and `tests/test_cli.py` with hermetic tests:

- `marestail green rewrite dotnet` on a repo with commits on `main`: installs the `post-commit` hook if missing, creates branch `dotnet-rewrite` and worktree `.marestail/green/main__dotnet-rewrite/worktree`, writes `state.json` with `mode = "rewrite"`, `stack = "dotnet"`, `green_branch = "dotnet-rewrite"`, writes `001-rewrite-dotnet-base-<c1[:7]>.md` (listing existing source files, `depends = []`) and `.feature`, and spawns the worker unless `--no-spawn`.
- Subsequent commits `c2` and `c3` on `main` followed by `marestail green on-commit --no-spawn`: appends `002-rewrite-dotnet-<c2[:7]>` (`depends = ["001-rewrite-dotnet-base-<c1[:7]>"]`, `stack = true`) and `003-rewrite-dotnet-<c3[:7]>` (`depends = ["002-rewrite-dotnet-<c2[:7]>"]`, `stack = true`) containing `c2` and `c3`'s diffs under `## Source commit`, and `marestail tasks check` passes on `.marestail/green/main__dotnet-rewrite/tasks/`.
- `[green] rewrites = ["rust"]` and `green_branch = false` in `marestail.toml`: `on-commit --no-spawn` on `c1` auto-initializes `rust-rewrite` with `001-rewrite-rust-base-<c1[:7]>` without creating a `green-from-<c1[:7]>` branch, and `on-commit --no-spawn` on `c2` appends `002-rewrite-rust-<c2[:7]>`.
- `marestail green worker --branch dotnet-rewrite`: prepares `<worktree>/marestail.toml` with `[dotnet]` and `[freeze.allow]`, copies `guidance/cs.md` and `guidance/patterns/cs.md`, runs `001-rewrite-dotnet-base-<c1[:7]>` and `002-rewrite-dotnet-<c2[:7]>` in order with `--scope all` without running `git merge`, and records `status = "done"` and `green_head` for each entry.
- Guard and error cases: `rewrite` on a non-git directory (exit 1), on a detached HEAD or `*-rewrite` branch (exit 1), with an unknown `--branch` (exit 1), idempotent second `marestail green rewrite dotnet` call (exit 0), and `on-commit` exiting 0 quietly when `HEAD` is on `dotnet-rewrite`.

## Done when

`marestail gate --tier full` passes with 100% line and branch coverage, CRAP <= 4 on every function, zero comments or docstrings, `.importlinter` clean, `marestail tasks check` exits 0 on `tasks/`, and all mutants are killed.
