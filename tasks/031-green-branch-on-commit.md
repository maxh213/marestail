+++
depends = []
+++
# 031 — `marestail green`: hook commits into a `green-from-<sha>` branch until merge

After this task, a repository with `marestail green install` turns every commit on a working branch into a stacked greening task on a companion `green-from-<short-sha>` branch inside `.marestail/green/`. The first commit since the last merge opens `green-from-<short-sha>` in its own worktree and writes task `001-green-<short-sha>`. Every later commit on the working branch before `green-from-<short-sha>` is merged back is queued onto that same `green-from-<short-sha>` chain as `002-green-<next-sha>`, `003-green-<next-sha>`, and so on, each stacked on the task before it. Merging `green-from-<short-sha>` back into the working branch closes the cycle and removes the worktree; the next commit after the merge starts a fresh `green-from-<new-sha>`.

This task builds the CLI (`marestail green install`, `uninstall`, `on-commit`, `status`, and the `worker` stub entry point), the `post-commit` git hook, the per-branch state and cycle rules in `.marestail/green/<branch_key>/state.json`, the generated task and feature files, and the detached worker spawn. Task 032 fills in `marestail green worker` so the background worker merges each queued commit into the worktree (handing any merge conflicts to the coder to resolve) and runs `marestail run --from coder --to hardener --auto`.

## Commands

```
marestail green install [TARGET]
marestail green uninstall [TARGET]
marestail green on-commit [--no-spawn]
marestail green status [TARGET] [--json]
marestail green worker [--branch BRANCH]
```

Add `add_green` to `build_parser` in `marestail/cli.py` directly after `add_install` (`cli.py:104`), with help text `track commits on a green-from-<sha> branch and green them in the background`. Update the command list in `tests/test_cli.py` (`COMMAND_CHOICES`), `tests/test_events.py`, and `tests/test_green_repo.py` (`test_help_lists_every_subcommand` and `test_subcommand_help_lists_its_flags_in_order`). Each subcommand handler in `cli.py` imports `marestail.green` lazily, the same way `install_command` imports `marestail.install`.

Subcommand help texts and arguments:

| subcommand | help | arguments |
|---|---|---|
| `install` | `install the git post-commit hook into a target repo` | `target` (`nargs="?"`, `default="."`) |
| `uninstall` | `remove the git post-commit hook from a target repo` | `target` (`nargs="?"`, `default="."`) |
| `on-commit` | `record HEAD on its green-from-<sha> branch and start the worker` | `--no-spawn` (`action="store_true"`, help `record the commit and write task files without starting the worker`) |
| `status` | `show the green-from-<sha> state for each tracked branch` | `target` (`nargs="?"`, `default="."`), `--json` (`action="store_true"`) |
| `worker` | `drain queued commits on a green-from-<sha> branch` | `--branch` (optional branch name; when omitted, processes every branch under `.marestail/green/` with pending commits) |

## Hook install and uninstall

- `marestail green install [TARGET]` resolves `TARGET` (`Path(target).resolve()`). It runs `git rev-parse --git-path hooks/post-commit` with `cwd=target`. When `git` exits non-zero, it prints `marestail green install needs a git repository: <target>` to stdout and exits 1 without writing anything.
- When `git` exits 0, the hook path is `(target / output.strip()).resolve()`. Ensure its parent directory exists.
- The hook block is delimited by `# marestail-green-hook` and `# end marestail-green-hook`:

```sh
# marestail-green-hook
marestail green on-commit >/dev/null 2>&1 || true
# end marestail-green-hook
```

- If the hook file does not exist or is empty, write `#!/usr/bin/env sh\n` followed by the block and a trailing newline.
- If the hook file exists and already contains `# marestail-green-hook` as a full line, leave the file byte-for-byte unchanged.
- If the hook file exists and does not contain `# marestail-green-hook`, prepend `#!/usr/bin/env sh\n` if the file does not start with `#!`, ensure the existing text ends with `\n`, and append the block.
- Set mode `0o755` on the hook file (`path.chmod(0o755)`), print `installed post-commit hook at <hook_path>`, and exit 0.
- `marestail green uninstall [TARGET]` resolves the hook path the same way. If `TARGET` is not a git repository, it prints `marestail green uninstall needs a git repository: <target>` and exits 1. If the hook file does not exist or has no `# marestail-green-hook` line, it prints `no marestail post-commit hook at <hook_path>` and exits 0. Otherwise it removes the lines from `# marestail-green-hook` through `# end marestail-green-hook` inclusive. If the remaining lines are only a `#!` shebang and/or blank lines, unlink the file; otherwise write the remaining lines back and keep `0o755`. Print `removed post-commit hook from <hook_path>` and exit 0.

## Configuration in `marestail.toml`

`[green]` is an optional table in `marestail.toml`:

| key | type | default | effect |
|---|---|---|---|
| `auto_run` | bool | `true` | when `true`, `on-commit` starts `marestail green worker --branch <branch>` in a detached session unless `--no-spawn` was passed or a worker for that branch is already alive |
| `scope` | string (`"changed"` or `"hyper"`) | `"hyper"` when `.git/info/exclude` contains `# marestail (install --scope hyper)`, else `"changed"` | `--scope` value the worker passes to `marestail run` |
| `branches` | array of strings | `[]` (every branch) | when non-empty, `on-commit` acts only on branches in this list and exits 0 quietly on any other branch |

A repository with no `marestail.toml` yet still works with the defaults above (`config.load` falls back to `{}` when `marestail.toml` is absent in `green.py`).

## State layout under `.marestail/green/`

For a source branch `<branch>`, `branch_key` replaces every `/` with `__` (`main` -> `main`, `feat/quick-fix` -> `feat__quick-fix`). Its folder is `<repo_root>/.marestail/green/<branch_key>/`:

- `state.json`: UTF-8 JSON written with `json.dumps(..., indent=2) + "\n"`.
- `worktree/`: the git worktree checked out on `green-from-<short_sha>`.
- `tasks/`: `<task_id>.md` and `<task_id>.feature`, written by `on-commit` and copied into the worktree by the worker.
- `worker.log`: stdout and stderr of the background worker.
- `lock`: PID lock file owned by the active worker (032).

`state.json` has these keys in this order:

```json
{
  "v": 1,
  "source_branch": "main",
  "green_branch": "green-from-a1b2c3d",
  "anchor_sha": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  "base_sha": "00112233445566778899aabbccddeeff00112233",
  "worktree": "/abs/path/to/repo/.marestail/green/main/worktree",
  "worker_pid": null,
  "commits": [
    {
      "seq": 1,
      "sha": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
      "short_sha": "a1b2c3d",
      "subject": "Fix null check on payload",
      "task_id": "001-green-a1b2c3d",
      "status": "pending",
      "green_head": null
    }
  ]
}
```

## Rules for `marestail green on-commit`

`on-commit` runs from the repository where the commit was made (`Path.cwd()`).

1. **Guards that exit 0 quietly with no output and no writes**:
   - `os.environ.get("MARESTAIL_GREEN_ACTIVE") == "1"` or `os.environ.get("MARESTAIL_GATE_ACTIVE") == "true"`.
   - `git rev-parse --show-toplevel` fails, or the resolved toplevel path has `.marestail` in its path parts (so commits made inside `.marestail/green/<branch_key>/worktree` or a perf/visual worktree are ignored).
   - `git symbolic-ref --quiet --short HEAD` exits non-zero (detached HEAD) or prints a branch starting with `green-from-` or `marestail/`.
   - `[green] branches` is non-empty and the branch is not in it.
2. **Inspect `HEAD`**:
   - Read `<head_sha>` (`git rev-parse HEAD`), `<short_sha>` (`head_sha[:7]`), `<subject>` (`git log -1 --format=%s HEAD`), `<parents>` (`git log -1 --format=%P HEAD`, split on whitespace), and `<changed_files>` (`git diff-tree --no-commit-id --name-only -r HEAD`, non-empty lines).
3. **Check whether an existing cycle in `state.json` just merged or already closed**:
   - When `state.json` exists for this branch:
     - Let `green_branch` be `state["green_branch"]`. Check whether `refs/heads/<green_branch>` exists (`git rev-parse --verify --quiet refs/heads/<green_branch>`).
     - **Merge-back commit (`HEAD` merged `green_branch`)**: If `refs/heads/<green_branch>` resolves to `<green_tip>` and `<green_tip>` is in `<parents>`, or if any entry's non-null `green_head` in `state["commits"]` is in `<parents>`:
       - The user just merged `green_branch` into `source_branch`.
       - Remove the worktree if it exists (`worktree.remove(repo_root, Path(state["worktree"]))` or `git worktree remove --force <worktree>`), delete `state.json`, and exit 0 without opening a new cycle.
     - **Cycle already merged or branch deleted before `HEAD`**: If `refs/heads/<green_branch>` does not exist, or `git merge-base --is-ancestor <green_branch> HEAD` exits 0:
       - Clean up the old worktree if it exists and treat this commit as the start of a new cycle (step 4).
     - **Open cycle**: Otherwise `green_branch` is still unmerged. If `<head_sha>` is already in `state["commits"]` (by `sha`), exit 0. Otherwise append to the open cycle (step 5).
4. **Starting a new cycle**:
   - `green_branch` is `f"green-from-{short_sha}"`.
   - `anchor_sha` is `head_sha`.
   - `base_sha` is `parents[0]` when `parents` is non-empty, else `head_sha`.
   - Create the worktree at `<repo_root>/.marestail/green/<branch_key>/worktree` on branch `green_branch` at `head_sha`: if `<worktree>` already exists, remove it first with `git worktree remove --force <worktree>`, then run `git worktree add -B <green_branch> <worktree> <head_sha>`.
   - Build the first commit entry with `seq = 1`, `task_id = f"001-green-{short_sha}"`, `status = "pending"`, `green_head = None`.
   - Write `<branch_dir>/tasks/001-green-<short_sha>.md` and `<branch_dir>/tasks/001-green-<short_sha>.feature` (format below) and save `state.json`.
5. **Appending to an open cycle**:
   - Do not run `git worktree` or `git merge` in `on-commit` (the worker may be mid-run in the worktree).
   - `seq` is `len(state["commits"]) + 1`.
   - `task_id` is `f"{seq:03d}-green-{short_sha}"`.
   - `prev_task_id` is `state["commits"][-1]["task_id"]`.
   - Append the new entry to `state["commits"]`, write `<branch_dir>/tasks/<task_id>.md` (with `depends = [prev_task_id]` and `stack = true`) and `<branch_dir>/tasks/<task_id>.feature`, and save `state.json`.
6. **Spawning the worker**:
   - If `--no-spawn` is not set and `auto_run` is `True` and `worker_pid` is not a live worker for this repo (checked with the process check below), start `[sys.executable, "-u", str(Path(__file__).resolve().parent / "cli.py"), "green", "worker", "--branch", source_branch]` via `subprocess.Popen` with `cwd=repo_root`, `start_new_session=True`, `stdin=subprocess.DEVNULL`, `stdout` and `stderr` opened in `"a"` mode on `<branch_dir>/worker.log`, and `env={**os.environ, "MARESTAIL_GREEN_ACTIVE": "1"}`.
   - Record `proc.pid` as `worker_pid` in `state.json`.

A `worker_pid` counts as alive when `/proc/<pid>/cmdline` exists, reads without `OSError`, and its NUL-separated tokens contain both `"green"` and `"worker"`. Wrap the `/proc` read in a small module-level helper `is_worker_alive(pid: int | None) -> bool` so tests can monkeypatch it or `Popen`.

## Generated task and feature files

`write_task_files(branch_dir: Path, entry: dict[str, Any], prev_task_id: str | None, changed_files: list[str])` writes two files under `<branch_dir>/tasks/`:

1. `<task_id>.md`:
   - Front matter:
     - When `prev_task_id is None`:
       ```
       +++
       depends = []
       +++
       ```
     - When `prev_task_id` is a string:
       ```
       +++
       depends = ["<prev_task_id>"]
       stack = true
       +++
       ```
   - Body:
     ```markdown
     # Green commit <short_sha>: <subject>

     This is a refactor of commit `<sha>` (`<subject>`): nothing about the behaviour introduced by `<sha>` may change. Bring the code and tests touched by this commit through `marestail gate --tier full`.

     ## Changed files

     - `<path 1>`
     - `<path 2>`
     ```
     (When `changed_files` is empty, write `- (none)` under `## Changed files`.)

2. `<task_id>.feature`:
   ```gherkin
   Feature: Green commit <short_sha>

     Scenario: Preserve the behaviour of commit <short_sha> while passing the gate
       Given commit <short_sha> (<subject>) is applied to the branch
       When the code and tests are brought through the gate
       Then the behaviour of commit <short_sha> is preserved and the gate passes
   ```

Writing this matching `.feature` file means `marestail.audit.feature_files` finds a related feature file for `<task_id>` when `--from coder --to hardener` runs in 032, instead of falling back to unrelated `.feature` files in `features/` or failing with `audit: no feature file found for this task`.

## `marestail green status [TARGET] [--json]`

- Resolves the repository root from `TARGET` (`git rev-parse --show-toplevel` from `TARGET`; if not a git repo, falls back to `Path(target).resolve()`).
- Reads every `<repo_root>/.marestail/green/*/state.json` sorted by `source_branch`.
- For each state, computes:
  - `merged`: `True` when `refs/heads/<green_branch>` is an ancestor of `refs/heads/<source_branch>` (`git merge-base --is-ancestor <green_branch> <source_branch>` exits 0), else `False`.
  - `worker_running`: `is_worker_alive(state.get("worker_pid"))`.
- With `--json`: prints `json.dumps({"v": 1, "branches": [...]}, indent=2)` and exits 0. Each item in `branches` has the keys of `state.json` plus `"merged": bool` and `"worker_running": bool`.
- Without `--json`:
  - When there are no state files, prints `no green branches tracked` and exits 0.
  - Otherwise prints one block per branch:
    `<source_branch> -> <green_branch> (base <base_sha[:7]>, <running|idle|merged>, <done>/<total> done)`
    followed by one indented line per commit:
    `  <task_id>  <short_sha>  <status>  <subject>`

## Architecture and `.importlinter`

- Create `marestail/green.py`.
- In `.importlinter`:
  - Add `marestail.green` to the pipeline commands layer in `[importlinter:contract:layers]` alongside `marestail.runner : marestail.install : marestail.route : marestail.perf : marestail.sonar.setup : marestail.backends : marestail.green`.
  - Add `marestail.green` to `forbidden_modules` in the lower-layer contracts (`gates-do-not-reach-up`, `foundations-import-nothing-above`, `language-adapters-stay-below-gates`, `backends-do-not-reach-up`, `timeline-stays-below-runner`, `events-stays-below-runner`, `ran-against-stays-below-runner`, `hunk-check-reads-only-git-and-freeze`, `remote-box-knows-no-language`).
- Document `MARESTAIL_GREEN_ACTIVE` in the `## Environment variables` table in `README.md` (`| MARESTAIL_GREEN_ACTIVE | set to 1 while a green worker runs so commits inside the green worktree do not re-trigger on-commit |`) and add `"MARESTAIL_GREEN_ACTIVE"` to `DOCUMENTED` in `tests/test_green_repo.py`.
- Add a concise `## Green` section to `README.md` describing `marestail green install`, `uninstall`, `on-commit`, `worker`, and `status`, and the `[green]` keys in `marestail.toml`.

## Tests

In `tests/test_green.py` and `tests/test_cli.py` (all hermetic, using `tmp_path` git repositories or monkeypatched `run`/`Popen`, never touching the network or real agent CLIs):

- `install` and `uninstall` on a non-git folder (exit 1), on a fresh git repo (creates executable `post-commit`), idempotency on a second `install`, preserving pre-existing lines in `post-commit` on `install` and `uninstall`, and unlinking `post-commit` when only the shebang remains after `uninstall`.
- `on-commit` on a first commit `c1` on `main`: creates branch `green-from-<c1[:7]>` and `.marestail/green/main/worktree`, writes `state.json` with `base_sha` equal to `c1`'s parent, writes `001-green-<c1[:7]>.md` (`depends = []`, passes `task_file.check`) and `.feature`, and spawns the worker unless `--no-spawn` or `auto_run = false`.
- `on-commit` on a second commit `c2` and third commit `c3` on `main` before merge: keeps `green-from-<c1[:7]>`, appends `002-green-<c2[:7]>` (`depends = ["001-green-<c1[:7]>"]`, `stack = true`) and `003-green-<c3[:7]>` (`depends = ["002-green-<c2[:7]>"]`, `stack = true`), and `marestail tasks check` passes on the `.marestail/green/main/tasks/` folder.
- `on-commit` when `green-from-<c1[:7]>` is merged into `main` (`git merge --no-ff green-from-<c1[:7]>`): detects the merge parent, removes the worktree and `state.json`, spawns nothing, and the next commit `c4` on `main` opens a fresh `green-from-<c4[:7]>`.
- `on-commit` guards: exits 0 quietly when `MARESTAIL_GREEN_ACTIVE=1`, when `MARESTAIL_GATE_ACTIVE=true`, on a detached HEAD, on a `green-from-*` or `marestail/*` branch, inside `.marestail/`, or on a branch not listed in `[green] branches`.
- `status` and `status --json` with no tracked branches, with an active branch, and with a merged branch.

## Done when

`marestail gate --tier full` passes with 100% line and branch coverage, CRAP <= 4 on every function, zero comments or docstrings, `.importlinter` clean, `marestail tasks check tasks/031-green-branch-on-commit.md` clean, and all mutants killed.
