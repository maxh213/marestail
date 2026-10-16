+++
depends = ["031-green-branch-on-commit"]
stack = true
+++
# 032 — `marestail green worker`: merge incoming commits, hand conflicts to the coder, and run `--from coder --to hardener`

This task completes `marestail green` on top of 031. After this task, `marestail green worker` (started automatically in the background by `on-commit`, or run by hand) drains every pending commit recorded in `.marestail/green/<branch_key>/state.json` inside that branch's `green-from-<short-sha>` worktree. For the first commit in a cycle, the worktree is already at that commit; for every later commit that landed on the source branch before merge, the worker merges that commit into `green-from-<short-sha>` first. When the merge has conflicts against code an earlier green task refactored, the worker commits the conflict markers and embeds the conflicted file list and the incoming commit's patch into the task file so the coder resolves the conflict while preserving the incoming commit's behaviour. It then runs `marestail run tasks/<task_id>.md --from coder --to hardener --auto --scope <scope>`.

## Worker lifecycle

`marestail green worker [--branch BRANCH]` runs from the repository root:

1. **Select branches**:
   - With `--branch BRANCH`, inspect only `<repo_root>/.marestail/green/<branch_key>/state.json` (if missing, print `no green state for branch <BRANCH>` and exit 1).
   - Without `--branch`, inspect every `<repo_root>/.marestail/green/*/state.json` in `source_branch` order that has at least one commit with `status == "pending"`.
2. **Per-branch lock**:
   - Before touching a branch's worktree, acquire `<branch_dir>/lock`: if `<branch_dir>/lock` exists and `is_worker_alive(int(lock.read_text().strip()))` is `True`, skip that branch (print `worker already running for <source_branch> (pid <pid>)` and return 0). Otherwise write `str(os.getpid())` to `<branch_dir>/lock` and remove the lock file in a `finally` block when done.
   - Also write `worker_pid = os.getpid()` into `state.json` while running, and set `worker_pid = None` in `state.json` before releasing the lock.
3. **Prepare the worktree (`prepare_worktree`)**:
   - If `<worktree>` does not exist on disk, recreate it at `green_branch` (`git worktree add <worktree> <green_branch>`, or `-B <green_branch> <worktree> <anchor_sha>` if the branch ref is missing).
   - Ensure the repository's `.git/info/exclude` (resolved via `git rev-parse --git-path info/exclude` in `repo_root`) contains a `# marestail-green-exclude` … `# end marestail-green-exclude` block listing:
     ```
     # marestail-green-exclude
     .marestail/
     tasks/*-green-*.md
     features/*-green-*.feature
     # end marestail-green-exclude
     ```
     so generated green task and feature files in the worktree are ignored by git and never committed onto `green-from-<short-sha>`, even in repositories that do not gitignore `tasks/` or `features/`.
   - Copy untracked marestail working files from `repo_root` into `worktree` when they exist in `repo_root` and are missing in `worktree`: `marestail.toml`, `sonar-project.properties`, `PERFORMANCE.md`, and any `guidance/*.md` and `guidance/patterns/*.md`.
   - Set `[git] base` in the worktree: if `marestail.toml` in the worktree is untracked (`git ls-files --error-unmatch marestail.toml` in `worktree` exits non-zero), rewrite or add `[git]\nbase = "<base_sha>"` in `<worktree>/marestail.toml` so every gate (`--scope changed` and `--scope hyper`) measures the diff from `<base_sha>` (the parent of the cycle's first commit).
   - If `<repo_root>/.marestail/tooling` exists and `<worktree>/.marestail/tooling` does not, create a symlink `<worktree>/.marestail/tooling -> <repo_root>/.marestail/tooling` so out-of-tree TypeScript tooling from `marestail install --scope hyper` works inside the worktree without running `npm install` again.

## Draining pending commits in order

The worker loops, re-reading `state.json` at the top of each iteration so any commit appended by `on-commit` while a run was in progress is picked up in the same loop:

1. Find the first entry in `state["commits"]` with `status == "pending"`. When none is `"pending"`, break and return 0 (or 1 if any entry ended `"failed"`).
2. Set `entry["status"] = "running"` and save `state.json`.
3. **Merge step when `entry["seq"] > 1`**:
   - In `worktree`, check whether `entry["sha"]` is already an ancestor of `HEAD` (`git merge-base --is-ancestor <sha> HEAD`).
   - If it is not an ancestor, run `git merge --no-commit --no-ff <sha>` in `worktree`:
     - **Clean merge (`code == 0`)**:
       - If `git status --porcelain` in `worktree` has staged changes, commit with `git commit -q -m "Merge commit <short_sha> into <green_branch>"`.
     - **Conflicted merge (`code != 0`)**:
       - Collect the conflicted file paths with `git diff --name-only --diff-filter=U` in `worktree` (sorted, non-empty lines).
       - Read the incoming commit's patch from `git show --stat --patch <sha>` in `worktree`.
       - Stage the working tree with the conflict markers intact (`git add -A`) and commit it with `git commit -q -m "Merge commit <short_sha> into <green_branch> (conflicts for coder)"` so `HEAD` is a clean commit before `marestail run` starts and `verify_worker` can diff against `before`.
       - Append a `## Merge conflicts to resolve` section to `<branch_dir>/tasks/<task_id>.md`:
         ```markdown
         ## Merge conflicts to resolve

         Merging `<sha>` (`<subject>`) into `<green_branch>` left conflict markers in:

         - `<conflicted path 1>`
         - `<conflicted path 2>`

         Resolve every conflict marker (`<<<<<<<`, `=======`, `>>>>>>>`) while preserving all functionality introduced by `<sha>` and keeping the clean structure on `<green_branch>`.

         ### Incoming commit `<short_sha>`

         ```diff
         <git show --stat --patch output>
         ```
         ```
4. **Copy task and feature files into the worktree**:
   - Copy `<branch_dir>/tasks/<task_id>.md` to `<worktree>/tasks/<task_id>.md` and `<branch_dir>/tasks/<task_id>.feature` to `<worktree>/features/<task_id>.feature` (creating `tasks/` and `features/` in `worktree` if needed).
5. **Run the pipeline**:
   - Determine `<scope>`: `config.get("green", "scope")` when set in `marestail.toml`, else `"hyper"` when `.git/info/exclude` contains `# marestail (install --scope hyper)`, else `"changed"`.
   - Run `[sys.executable, "-u", str(Path(__file__).resolve().parent / "cli.py"), "run", f"tasks/{task_id}.md", "--from", "coder", "--to", "hardener", "--auto", "--scope", scope]` with `cwd=worktree` and `env={**os.environ, "MARESTAIL_GREEN_ACTIVE": "1"}` via a module-level `run_task(command: list[str], cwd: Path, env: dict[str, str]) -> int` helper (calling `subprocess.run(command, cwd=cwd, env=env, check=False).returncode`) so tests can stub `run_task`.
   - In a `finally` block after `run_task`, unlink `<worktree>/tasks/<task_id>.md` and `<worktree>/features/<task_id>.feature` (the copies in `<branch_dir>/tasks/` stay as the record).
6. **Record the outcome**:
   - Re-read `state.json` before updating the finished entry so any new `"pending"` commits appended by `on-commit` while `run_task` was running are preserved!
   - When `run_task` returns `0` (or `3`, the visual geometry-only pass code):
     - Set `entry["status"] = "done"` and `entry["green_head"] = <HEAD of worktree from git rev-parse HEAD>`.
     - Save `state.json` and continue the loop to the next `"pending"` commit.
   - When `run_task` returns any other code:
     - Set `entry["status"] = "failed"`, save `state.json`, and stop draining this branch (return 1).

## Must not break

- All existing behaviour from 031 (`install`, `uninstall`, `on-commit`, `status`, `status --json`).
- A commit arriving via `on-commit` while `worker` is mid-`run_task` on `001-green-<c1>` is never lost when `worker` saves `state.json` at the end of `001`, and `worker` immediately continues to `002-green-<c2>` in the same invocation.
- No comment or docstring under `marestail/`, CRAP <= 4 on every function, and `.importlinter` contracts intact.

## Tests

Extend `tests/test_green.py` with hermetic tests (using real `git` in `tmp_path` repos and monkeypatching `run_task` so no real agent CLI runs):

- Single commit `c1`: `on-commit --no-spawn` followed by `marestail green worker` prepares the worktree, writes `.git/info/exclude`, copies `<task_id>.md` and `<task_id>.feature` into the worktree during `run_task`, invokes `marestail run tasks/001-green-<c1[:7]>.md --from coder --to hardener --auto --scope changed` with `MARESTAIL_GREEN_ACTIVE=1`, cleans up the worktree's temporary task and feature files afterwards, and marks `status = "done"` and `green_head` in `state.json`.
- Two commits `c1` and `c2` with a clean merge: `c2` adds a new file or non-overlapping lines on `main`; `worker` runs `001`, then merges `c2` cleanly into `green-from-<c1[:7]>`, runs `002`, and leaves both `done` on `green-from-<c1[:7]>`.
- Two commits `c1` and `c2` with a merge conflict: while `001`'s fake `run_task` refactors the line `c1` touched on `green-from-<c1[:7]>`, `c2` edits that same line on `main`. When `worker` advances to `002`, the merge conflicts; `worker` commits the conflict markers so `worktree` has a clean `git status --porcelain`, appends `## Merge conflicts to resolve` with the conflicted path and `c2`'s diff to `002-green-<c2[:7]>.md`, and passes it to `run_task` so the coder resolves the conflict and preserves `c2`'s behaviour.
- Concurrent `on-commit` while `run_task` is running: inside the fake `run_task` for `001`, make a new commit `c2` on `main` and call `on-commit --no-spawn`. Verify that when `001` finishes, `state.json` retains `002` and the same `worker` call immediately drains `002` without dropping it.
- Failed run: when `run_task` returns `1` on `001`, `001` is marked `"failed"`, `002` stays `"pending"`, and `worker` exits 1.
- Lock contention: when `<branch_dir>/lock` holds a live worker PID, `worker` prints `worker already running for <branch> (pid <pid>)` and exits 0 without calling `run_task`.
- Hyper scope detection and `.marestail/tooling` symlink: in a repo whose `.git/info/exclude` has `# marestail (install --scope hyper)` and `.marestail/tooling`, `worker` symlinks `.marestail/tooling` into the worktree and passes `--scope hyper` to `marestail run`.

## Done when

`marestail gate --tier full` passes with 100% coverage, CRAP <= 4, zero comments/docstrings, `.importlinter` clean, `marestail tasks check tasks/031-green-branch-on-commit.md tasks/032-green-worker-merges-and-runs.md` exits 0, and every mutant is killed.
