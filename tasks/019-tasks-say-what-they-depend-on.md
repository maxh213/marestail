# 019 — task files say what they depend on: `+++` front matter and `marestail tasks check`

After this task, a task file can open with a short TOML block that names the tasks it depends on and whether it runs on its dependency's branch, and `marestail tasks check` tells you, one line per problem, whether a folder of task files is well-formed before anything runs:

```
+++
depends = ["018-runs-stay-nice"]
stack = true
+++
# 019 — ...
```

Today a task's order and dependencies live only in its number and in the head of whoever wrote the queue script (`.marestail/phases/run-queue-all-opus.sh` loops over `0*.md` in name order). A queue, or a scheduler running marestail on several machines, needs them written down in the file. `marestail run` keeps accepting files with no block, exactly as today.

Green has no module for task files: nothing in `marestail/` parses one. The task is a bare `Path` everywhere (`Run.task`, `marestail/runner.py:124`), its id is `Path.stem` (`runner.py:143-144`, `runner.py:206`), and its text is read raw in three places, all in `marestail/prompts.py`.

## Format

- A block is present only when line 1 of the file is exactly `+++` (a trailing `\r` is ignored). The block ends at the first later line that is exactly `+++`. Everything between is TOML, parsed with `tomllib.loads`. Everything after the closing line is the body.
- A file whose line 1 is anything else has no block, and its body is the whole file. A `+++` further down is plain text.
- The task id is the file name without `.md`: `tasks/018-runs-stay-nice.md` has id `018-runs-stay-nice`.
- Keys, and nothing else:

| key | type | rule |
|---|---|---|
| `depends` | array of strings | required whenever a block is present; `[]` means no dependencies; each entry is a task id, resolved as `<the task's own folder>/<id>.md` |
| `stack` | bool | required when `depends` is not empty, forbidden when it is empty; `true` means the task runs on its dependency's branch, after it, and needs exactly one dependency; `false` means the task starts its own branch from `[git] base` once every dependency is merged into it |

`marestail tasks check` and `marestail run` do not act on `stack`; they only check it. `marestail queue` (022) is its reader.

## `marestail/task_file.py`

New module. It imports only the standard library (`tomllib`, `pathlib`, `dataclasses`). It goes into the `.importlinter` layer that holds `marestail.report : marestail.config : marestail.changes : marestail._location`, and into the `foundations-import-nothing-above` contract's `source_modules`.

- `TaskFile`, a frozen dataclass: `id: str`, `path: Path`, `depends: tuple[str, ...]`, `stack: bool | None`, `body: str`, `has_front_matter: bool`. With no block: `depends == ()`, `stack is None`, `has_front_matter is False`, `body` is the whole text. With a block and `depends = []`: `stack is None`.
- `TaskFileError(ValueError)`, carrying `problems: list[str]`, every per-file problem found, without the path.
- `read(path: Path) -> TaskFile`. Reads the file (a missing file raises `FileNotFoundError`, as `Path.read_text` does today). Raises `TaskFileError` when the file has any per-file problem below. A file with no block is not a problem for `read`.
- `check(paths: Sequence[Path]) -> list[str]`: every problem across the given files and folders, one string per problem, each `<path>: <problem>`. Empty means clean.

### Per-file problems

Found by `read` and by `check`, in this order, all reported, not just the first. The text is exact.

| problem | text |
|---|---|
| no block (`check` only) | `no front matter: line 1 must be +++` |
| no closing line | `front matter is not closed: no +++ line after line 1` |
| TOML error | `front matter is not valid TOML: <tomllib's message, unchanged>` (its line numbers count from the line after the opening `+++`) |
| unknown key, one line per key, sorted | `unknown front matter key '<key>'; allowed keys are depends and stack` |
| no `depends` | `front matter has no depends; write depends = [] for a task with no dependencies` |
| `depends` not an array of strings | `depends must be an array of strings` |
| entry that is empty, contains `/` or `\`, or ends in `.md` | `depends entry '<entry>' is not a task id; use the file name without .md` |
| entry listed twice | `depends lists <id> twice` |
| entry equal to the file's own id | `depends on itself` |
| `stack` not a bool | `stack must be true or false, got <value!r>` (so `stack = "yes"` reads `got 'yes'`) |
| `depends` not empty, no `stack` | `stack is required when depends is not empty` |
| `depends` empty, `stack` present | `stack is not allowed when depends is empty` |
| `stack = true`, not exactly one dependency | `stack = true needs exactly one dependency, got <n>` |

The rows after `depends must be an array of strings` that read `depends` are skipped when `depends` is missing or of the wrong type. An unclosed block or bad TOML stops the file there: nothing else is reported for it.

### Set problems (`check` only)

- Path expansion. A folder contributes every `*.md` directly inside it (not recursive), sorted, except `README.md` in any case, which is the installed guide. Other files in a folder are ignored. A `.md` file passed by name is checked even when it is a README. The same file reached twice (`tasks/` and `tasks/019-x.md`) is checked once, deduplicated by resolved path.
  - a path that does not exist: `<path>: no such file or folder`
  - a file passed by name without the `.md` suffix: `<path>: not a .md file`
- Duplicate id: two checked files in different folders with the same id. The second in path order gets `duplicate task id <id>: also <first path>`.
- Missing dependency: `depends on <id>, but <folder>/<id>.md does not exist`, where `<folder>` is the task's own folder as given (`depends on 018-x, but tasks/018-x.md does not exist`).
- A dependency outside the checked set: when `<folder>/<id>.md` exists but was not passed or reached through a folder argument, the dependency is satisfied and the file is not read. Its own problems, and any cycle that runs through it, are not reported; check the whole folder to see those. This is what lets `marestail tasks check tasks/020-x.md` pass when 020 depends on a 019 that has already run. Whether 019 is done is the queue's question, not this check's.
- Cycle: among checked files with no per-file problems, following resolved dependency edges. Each distinct cycle is reported once, against the file with the smallest id in it, listed from that id round to itself: `dependency cycle: 019-a -> 020-b -> 019-a`. A file that depends on a cycle without being in it is not reported. A self-dependency is reported as `depends on itself` only, never also as a cycle.
- Set checks cover only files that read with no per-file problems. A file that depends on a broken file is not reported for it; the broken file carries its own lines.

Output order: by path string, and within one file in the order of the two tables above (per-file, then duplicate, missing dependencies in `depends` order, cycle).

## `marestail tasks check`

- `marestail tasks check [PATH ...]`. `tasks` is a new command with one subcommand, `check`, added to `build_parser` in `marestail/cli.py` straight after `run` (`cli.py:89`), so the help lists `{gate,run,tasks,install,sonar,watch,perf,route,graph,depth}`. Help text: `tasks`: `check task files`; `check`: `check the front matter and dependencies of task files`; `PATH`: `a folder of task files or a .md file (default: tasks/)`.
- With no PATH it checks `tasks` relative to the current directory. It needs no `marestail.toml` and does not load config.
- Clean: prints nothing, exit 0. An empty folder is clean.
- Problems: one line per problem on stdout, `<path>: <problem>`, exit 1. Paths print as `Path` renders them, so the default folder missing reads `tasks: no such file or folder`.

Example, in a folder where 020 stacks on 019 but also names 017, and 021's block has a typo:

```
$ marestail tasks check
tasks/020-run-events.md: stack = true needs exactly one dependency, got 2
tasks/021-status.md: unknown front matter key 'depend'; allowed keys are depends and stack
tasks/021-status.md: front matter has no depends; write depends = [] for a task with no dependencies
$ echo $?
1
```

## `marestail run`

- `run_pipeline` calls `task_file.read(task)` straight after `nice.apply(config)` (018) and before `pick_model`. On `TaskFileError` it raises `SystemExit` with one `<path>: <problem>` line per problem, joined by newlines, so a broken block stops the run before any agent starts.
- It checks only the file's own block. It does not look for dependency files, cycles or duplicates, and it does not check that dependencies have run: a one-off run is the user's call, and those are set rules for `tasks check` and the queue.
- A file with no block runs exactly as today.
- A missing task file still raises `FileNotFoundError`, now from this call instead of from the first prompt.

## What the roles see

Roles see the body, never the block. When `depends` is not empty, the Task section opens with one plain sentence, then a blank line, then the body:

- one dependency: ``This task depends on `018-runs-stay-nice`, which runs before it, so its work is already in the tree. Treat what it delivered as existing behaviour: build on it and keep it working.``
- several: ``This task depends on `017-a` and `018-b`, which run before it, so their work is already in the tree. Treat what they delivered as existing behaviour: build on it and keep it working.`` (three or more: `` `a`, `b` and `c` ``)

No block, or `depends = []`: the Task section is the body alone, byte-identical to today for a file with no block. `stack` never reaches a prompt.

Why: raw TOML in a prompt is noise, and a role that sees `stack = true` may try to act on branch mechanics it does not control. The one fact a role can use is that another task's work is already there. The specifier then writes no scenarios re-specifying the dependency's behaviour, apart from pinning what must keep working; the critic does not bounce for a missing scenario that belongs to the dependency; the coder does not rebuild what exists. The same sentence goes to every role, because the Task section is shared (`worker_prompt`, `judge_prompt`, `perf_author_prompt`), and no role file changes.

This goes in one helper in `marestail/prompts.py` that reads the file with `task_file.read` and returns the Task section's text. It replaces `task.read_text()` at `prompts.py:72`, `prompts.py:103` and `prompts.py:245`. The prompt functions' signatures stay as they are.

## Every place green reads a task file, and the rule for each

| place | what it reads | rule after this task |
|---|---|---|
| `marestail/prompts.py:72` (`worker_prompt`), `:103` (`judge_prompt`), `:245` (`perf_author_prompt`) | the whole text, raw, into `# Task` | the helper above: body plus the dependency sentence |
| `marestail/runner.py:143-144` (`Run.task_name`), `:165` (`Run.folder`), `:169` (`Run.handoffs`) | `task.stem` | unchanged; the id is the file name, never the content |
| `marestail/runner.py:206` | `MARESTAIL_TASK = Path(task).stem` | unchanged |
| `marestail/runner.py:522-523` → `marestail/timeline.py:26`, `:157` | `task_name` into `timeline.json` / `timeline.md` | unchanged |
| `marestail/audit.py:29-30` (`is_related`), used by `feature_files` and `prompts.qa_files` | `features/*.feature` and `qa/*.md` stems matched against the task name | unchanged |
| `marestail/tui/collect.py:303-309` (`task_name`) | folder names under `.marestail/handoffs` and `.marestail/runs` | unchanged |
| commit labels (`agent_label`, `stamped`) | backend, model and effort only | unchanged; no label uses the task |
| `marestail/cli.py:165`, `:411` | the path argument | unchanged |

Nothing in green takes a title from line 1 or from the first `# ` heading: `grep` for `splitlines()[0]`, `readline` and `startswith("# ")` under `marestail/` finds only gate output parsing (`gates/ts_lint.py:133`, `gates/ts_tests.py:139`, `gates/py_runtime.py:180`, `erlang.py:81`, `perf/db.py:346`). Outside `marestail/`: `tools/overnight.sh:42` (main's copy, which the drivers call, at its line 41) and the queue drivers in `.marestail/phases/` use only the path and file name, and `.marestail/phases/sources/marestail-runs.py:367-371` lists `tasks/*.md` by stem. A file starting with `+++` breaks none of them.

The live drivers run main's marestail (`run-queue-all-opus.sh` puts `/home/max/workspace/marestail/bin` first on `PATH`). Main's prompts also read the task raw (main's `marestail/prompts.py:23`, `:37`, `:169`), so a task with a block run by main shows the TOML to the roles and nothing else changes. This task does not touch main.

## Docs

- `templates/tasks-README.md` is what `marestail install` copies to `tasks/README.md` (`marestail/_install.py:77-78`, `copy_if_missing`, so a repo that already has one keeps it). Append this section:

  ```
  ## Dependencies

  A task that needs another task first says so in a block at the very top of the file, before the title:

      +++
      depends = ["018-runs-stay-nice"]
      stack = true
      +++

  `depends` lists task ids: file names in this folder without `.md`. Write `depends = []` for a task that needs nothing. `stack = true` runs the task on its one dependency's branch, after it; `stack = false` starts it on its own branch once every dependency is merged. Leave `stack` out when `depends` is empty. `marestail tasks check` reports every problem in this folder, one line each. `marestail run` works with or without the block.
  ```

  `tests/test_install.py:53` and `:74` compare the installed file with the template, so they keep passing unchanged.
- `README.md`, `## Use` code block: after the last `marestail run` line (`README.md:74`), add `marestail tasks check          # front matter and dependencies of every task in tasks/; or pass folders and .md files`.
- `README.md`, `## Writing tasks` (`README.md:145-147`): add a paragraph saying a task file may open with a `+++` TOML block holding `depends` and `stack`, what each means, that `marestail tasks check` checks a folder, that `marestail run` never requires the block, and that roles see one sentence naming the dependencies, not the block. The docs gate checks backticked paths starting `tasks/` against the tree (`marestail.toml:27`, `gates/docs.py:143-144`), so name no example task file there in backticks.
- Green's own `tasks/README.md` is a copy of the template, but `tasks/**` is frozen spec (`marestail/freeze.py:84`); leave it alone. Green's existing `tasks/*.md` have no block and are not edited.

## Must not break

- `marestail run` on a task file with no front matter: same prompts byte for byte, same stdout, same exit codes. `tools/dryrun.sh:12` writes such a file (`# Add one`).
- The `.marestail/runs/<task-id>/` layout (prompts, results, `timeline.json`, `timeline.md`) and `.marestail/handoffs/<task-id>/`, all keyed by the file stem. Green writes no `pipeline.log`; main does (main's `runner.py:134`), and nothing here touches it.
- The queue driver scripts in `.marestail/phases/`: never edit, move or delete anything under `.marestail/phases/`, because a live driver runs from there.
- `tests/test_runner_flow.py` calls `run_pipeline(Path("t.md"), ...)` with no such file. Extend the `pipeline_env` fixture to stub `task_file.read` and record its calls, as it stubs `perf_trees.record_start`; do not make those tests write files.
- `marestail gate`, `watch`, `install`, `perf`, `route`, `graph` and `depth`: unchanged. `tasks check` loads no config.

## Tests

In-process pytest, `tmp_path` task folders, no agent sessions, no real `marestail run`.

`tests/test_task_file.py`:

- `read`: no block (whole text is the body, `depends == ()`, `stack is None`, `has_front_matter is False`); a block with `depends = []` (`stack is None`, body is the text after the closing line, verbatim); `depends = ["a"]` with `stack = true` and with `stack = false`; `depends = ["a", "b"]` with `stack = false`; `+++\r` on line 1 counts; `+++ ` with a trailing space and a leading blank line do not; a `+++` on line 5 of a blockless file stays in the body; a missing file raises `FileNotFoundError`.
- one test per row of the per-file table, asserting the exact text and that `read` raises `TaskFileError` with it in `problems`; plus two unknown keys reported sorted; unknown key and missing `stack` reported together; bad TOML and unclosed block reporting nothing else; `depends = "a"` and `depends = [1]` skipping the later rows; `stack = true` with `depends = ["a", "b"]` reading `got 2`; an empty block reading `front matter has no depends; ...`.
- `check`: a clean folder returns `[]`; `README.md` and `readme.md` in a folder are skipped and a non-`.md` file there is ignored; a README passed by name reports `no front matter`; a missing path; a `.txt` passed by name; a folder and a file in it passed together checked once; duplicate ids in two folders reported on the second path only; a missing dependency with the exact `<folder>/<id>.md` text; a dependency present in the folder but not passed is satisfied and not read (give it a broken block and assert nothing is reported for it); two-file and three-file cycles each reported once against the smallest id; a file depending on a cycle not reported; a self-dependency reported as `depends on itself` and not as a cycle; a file depending on a broken checked file gets no line; output order across several files and several problems in one file.

`tests/test_cli.py`:

- `tasks check` with no PATH in a `tmp_path` cwd with a clean `tasks/`: exit 0, empty stdout; with problems: exit 1 and the exact lines; with no `tasks/`: `tasks: no such file or folder`, exit 1; several PATH arguments passed through; `tasks` with no subcommand is an argparse error.
- `test_help_lists_commands_in_order` updated to `{gate,run,tasks,install,sonar,watch,perf,route,graph,depth}`.

`tests/test_prompts.py`:

- a task with no block: Task section unchanged (the existing tests already pin it).
- one dependency, two, three: the exact sentence, a blank line, then the body; the TOML and the `+++` lines appear nowhere in the prompt; `depends = []` gives the body alone; the same Task section in `worker_prompt`, `judge_prompt` and `perf_author_prompt`.

`tests/test_runner_flow.py`:

- `run_pipeline` calls `task_file.read` once with the task path, after `nice.apply` and before `pick_model`; a `TaskFileError` with two problems becomes `SystemExit` with both `<path>: <problem>` lines and `run_steps` is never called.

## Done when

`marestail/task_file.py` exists with every branch covered, `marestail tasks check` behaves as above, prompts carry the body and the dependency sentence instead of the raw file, `marestail run` rejects a broken block before any agent starts and runs blockless files as before, `.importlinter` lists `marestail.task_file`, `templates/tasks-README.md` and `README.md` document the block, and every existing test still passes.
