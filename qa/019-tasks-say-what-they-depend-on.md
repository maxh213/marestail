# QA: 019 — tasks say what they depend on

Run from the marestail-green checkout with the venv active. Save the script at the bottom as `/tmp/qa-019.py` and run `python3 /tmp/qa-019.py`. Expected: exit 0, and the lines below, in order. The script uses `/tmp/qa-019-empty` and `/tmp/qa-019-install`, and it points `HOME` and `GROK_HOME` at a scratch directory for the install step. `\n` and `\r` in the tables are LF and CR.

1. `help ok`. `bin/marestail --help` contains `{gate,run,tasks,install,sonar,watch,perf,visual,route,graph,depth}` and `check task files`. `tasks --help` contains `{check}` and `check the front matter and dependencies of task files`. `tasks check --help` contains `usage: marestail tasks check [-h] [PATH ...]` and `a folder of task files or a .md file (default: tasks/)`. `tasks` with no subcommand exits 2, stdout empty, stderr exactly:
   ```
   usage: marestail tasks [-h] {check} ...
   marestail tasks: error: the following arguments are required: tasks_command
   ```
   `run --help` exits 0, contains `--from` and `--model`, and does not contain `front matter`. `gate --help` exits 0 and contains `--tier`. `watch`, `perf`, `visual`, `install`, `sonar`, `graph` and `depth` `--help` each exit 0. `route --help` with `MARESTAIL_DANDELION` unset and `PATH=/usr/bin:/bin` exits 127 and stderr contains `dandelion is not installed`.

2. `clean ok`. `marestail tasks check empty` on an empty directory exits 0 with empty stdout and stderr. A directory with no `marestail.toml` and `tasks/018-a.md` (`depends = []`), `tasks/019-b.md` (`depends = ["018-a"]`, `stack = true`), `tasks/020-c.md` (`depends = ["018-a", "019-b"]`, `stack = false`) and a CRLF `tasks/021-crlf.md` (`+++\r\ndepends = []\r\n+++\r\n# ok\n`) is clean both as the default `tasks` folder and as a passed folder. Stdout and stderr are empty, exit 0.

3. `folder ok`. In `named/`, `README.md` has a valid `depends = []` block, `readme.md` and `Readme.MD` have no block, `notes.txt` is present, `018-bad.md` is `# no block\n`, and `nested/019-bad.md` is an unclosed block. `marestail tasks check named` exits 1 and stdout is exactly `named/018-bad.md: no front matter: line 1 must be +++\n`. `named/README.md` exits 0. `named/readme.md` exits 1 with `named/readme.md: no front matter: line 1 must be +++\n`. `named/notes.txt` exits 1 with `named/notes.txt: not a .md file\n`. `named/nested/019-bad.md` exits 1 with `named/nested/019-bad.md: front matter is not closed: no +++ line after line 1\n`. `missing_dir/` exits 1 with `missing_dir: no such file or folder\n`. With no arguments in an empty directory, stdout is exactly `tasks: no such file or folder\n`, exit 1. Stderr is empty for every check.

4. `dedup ok`. `once/019-bad.md` is `# title\n`. `marestail tasks check once/sub/../019-bad.md once` exits 1 and stdout is exactly one line: `once/sub/../019-bad.md: no front matter: line 1 must be +++\n`.

5. `rows ok`. For each single-problem row in the feature outline, a directory whose only file is `tasks/019-test.md` makes `marestail tasks check tasks/019-test.md` exit 1, stderr empty, stdout exactly `tasks/019-test.md: <problem>\n`. The rows are the outline in `features/019-tasks-say-what-they-depend-on.feature`, from `# 019 title` through the empty block `+++\n+++\n# x\n`. A wrong `depends` type, bad TOML, or an unclosed block is that one line and nothing else, including when the named dependency file does not exist.

6. `slash ok`. `tasks/019-slash.md` is five LF-terminated lines: `+++`, `depends = ["018`, two bytes of value 92, `a"]`, `stack = true`, `+++`, `# x`. Stdout is one line: `tasks/019-slash.md: depends entry '018`, one byte of value 92, `a' is not a task id; use the file name without .md`, then a newline. Exit 1, stderr empty.

7. `order ok`. `tasks/019-test.md` with `depends = ["", "sub/018", "018-a", "018-a"]` and `stack = true` prints these four lines and nothing else, exit 1:
   ```
   tasks/019-test.md: depends entry '' is not a task id; use the file name without .md
   tasks/019-test.md: depends entry 'sub/018' is not a task id; use the file name without .md
   tasks/019-test.md: depends lists 018-a twice
   tasks/019-test.md: stack = true needs exactly one dependency, got 4
   ```

8. `keys ok`. `zebra = 1` then `alpha = 2`, and no `depends`, prints `alpha`, then `zebra`, then `front matter has no depends; write depends = [] for a task with no dependencies`.

9. `extra ok`. `depends = ["018-a"]` and `extra = 1`, with `018-a.md` absent, prints the unknown key `extra` and then `stack is required when depends is not empty`. It does not print a missing-file line.

10. `published ok`. `tasks/020-run-events.md` depends on `018-runs-stay-nice` and `019-tasks` with `stack = true`. `tasks/021-status.md` has `depend = []`. `marestail tasks check tasks` exits 1, stderr empty, stdout exactly:
    ```
    tasks/020-run-events.md: stack = true needs exactly one dependency, got 2
    tasks/021-status.md: unknown front matter key 'depend'; allowed keys are depends and stack
    tasks/021-status.md: front matter has no depends; write depends = [] for a task with no dependencies
    ```

11. `missing ok`. `ord/019-a.md` with `depends = ["099-z", "098-a"]` and `stack = false` prints `099-z` first and `098-a` second, each `depends on <id>, but ord/<id>.md does not exist`. `left/019-a.md` depending on `020-b` does not see `right/020-b.md`: stdout is `left/019-a.md: depends on 020-b, but left/020-b.md does not exist\n`. The same file addressed as `left/sub/../019-a.md` and depending on `020-gone` prints `left/sub/../019-a.md: depends on 020-gone, but left/sub/../020-gone.md does not exist\n`.

12. `outside ok`. `box/018-prev.md` is `+++\n`. `box/019-next.md` depends on `018-prev` with `stack = true`. Checking only `box/019-next.md` exits 0 with empty stdout. Checking `box` prints only `box/018-prev.md: front matter is not closed: no +++ line after line 1\n`.

13. `dupes ok`. `alpha/019-dup.md`, `beta/019-dup.md` and `mid/019-dup.md`, each `depends = []`, print:
    ```
    beta/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md
    mid/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md
    ```

14. `cycles ok`. `cycle/020-b.md` depends on `021-c`, `cycle/021-c.md` depends on `019-a`, `cycle/019-a.md` depends on `020-b`, and `cycle/022-d.md` depends on `021-c`, all `stack = true` except none of them uses `stack = false`. Stdout is exactly `cycle/019-a.md: dependency cycle: 019-a -> 020-b -> 021-c -> 019-a\n`. `twin/019-a.md` depends on `020-b` and `021-c` with `stack = false`, and each of those depends on `019-a` with `stack = true`. Stdout is the `020-b` cycle line and then the `021-c` cycle line, both on `twin/019-a.md`.

15. `mixed ok`. `alpha/018-bad.md` is unclosed, `alpha/019-a.md` depends on `099-missing` and `020-b` with `stack = false`, `alpha/020-b.md` depends on `019-a`, `alpha/022-d.md` depends on `019-a`, and `beta/019-a.md` has `depends = []`. Stdout is exactly:
    ```
    alpha/018-bad.md: front matter is not closed: no +++ line after line 1
    alpha/019-a.md: depends on 099-missing, but alpha/099-missing.md does not exist
    alpha/019-a.md: dependency cycle: 019-a -> 020-b -> 019-a
    beta/019-a.md: duplicate task id 019-a: also alpha/019-a.md
    ```

16. `run-bad ok`. In a git repo whose `marestail.toml` is `[git] base = "main"`, `[perf] enabled = false`, `[practices] enabled = false`, with a stub claude that records that it started: the 020 file exits 1, stdout empty, stderr exactly the one `got 2` line, the stub does not start, and `.marestail/runs/020-run-events` does not exist. The 021 file's stderr is the unknown-key line and then the missing-depends line. `MARESTAIL_NICE=high` on the 020 command exits 1 with stderr exactly `nice must be an integer 0-19, got 'high'\n` and no `stack = true`. The 020 command plus `--model dandelion/route --agent grok` keeps the one `got 2` line and does not contain `drop --agent`. `tasks/missing.md` exits 1, stdout empty, stderr contains `FileNotFoundError` and `tasks/missing.md`, and `.marestail/runs/missing` does not exist. In `/tmp/qa-019-empty`, the 020 command exits 1 and stderr is exactly `no marestail.toml found above /tmp/qa-019-empty\n`.

17. `run-ok ok`. `tasks/t.md` bytes `# Add one\n`, critic to critic, `--auto --retries 1`: exit 0, stdout contains `pipeline complete`, stderr empty, the stub started, `.marestail/runs/t/timeline.json` has task `t`, `timeline.md` exists, `.marestail/handoffs/t/` exists, `pipeline.log` does not, and the critic prompt contains `# Task\n# Add one`. `tasks/018-prev.md` is `+++\n` and `tasks/019-demo.md` stacks on `018-prev` with body `# Something else`, a blank line, and `Carry on.`: the same critic run exits 0, the stub starts, the timeline task is `019-demo`, the handoffs folder is `.marestail/handoffs/019-demo/`, and the text under `# Task` is the one-dependency sentence for `018-prev`, a blank line, then that body. The critic prompt contains neither `+++`, nor `depends =`, nor `stack`.

18. `prompts ok`. For a file whose bytes are a space, `Do the thing.`, a space and LF, all three of `worker_prompt`, `judge_prompt` and `perf_author_prompt` have the task text `Do the thing.`. For `# Add one\n` the text is `# Add one`. For a first line `+++ ` (a trailing space) the task text is that whole file with the final LF stripped, fences included. `depends = []` with body `# Title`, a blank line, and `Body.` is that body with the final LF stripped. One dependency uses the singular sentence, and `stack = true` and `stack = false` produce the same text. Two ids use `` `017-a` and `018-b` ``. Three use `` `017-a`, `018-b` and `019-c` ``. Four use `` `017-a`, `018-b`, `019-c` and `020-d` ``. The plural sentence says `which run` and `what they delivered`. The three prompts match. Except for the spaced fence, none of those prompts contains `+++`, `depends =` or `stack`. The function signatures are the ones in `marestail/prompts.py` today.

19. `docs ok`. `templates/tasks-README.md` still has the `Name files` line immediately above a blank line and the `## Dependencies` section from the feature, including the four-space fence and `018-runs-stay-nice`. The `## Use` fence gains, on the line after `dandelion/route-best`, `marestail tasks check` plus ten spaces plus `# front matter and dependencies of every task in tasks/; or pass folders and .md files`. `## Writing tasks` contains the paragraph from the feature and that paragraph has no backticked token containing `/`. `marestail install` into an existing empty directory, with `GROK_HOME` and `HOME` in a scratch directory, writes `tasks/README.md` byte-identical to the template. Replacing that file with `keep\n` and installing again leaves `keep\n`. This checkout's `tasks/README.md` has no `## Dependencies` heading. `tasks/018-runs-stay-nice.md` still begins `# 018 — runs stay nice`. `tasks/019-tasks-say-what-they-depend-on.md` still begins `# 019 — task files say what they depend on`. No file in `tasks/` has a first line of `+++`. One `.importlinter` line contains `marestail.report : marestail.config : marestail.changes : marestail._location` and `marestail.task_file`. `foundations-import-nothing-above` lists `marestail.task_file`. `marestail/task_file.py` imports only `tomllib`, `pathlib` and `dataclasses`.

20. `.venv/bin/pytest tests/test_task_file.py tests/test_cli.py tests/test_prompts.py tests/test_runner_flow.py -q --tb=no`
    Expected: all pass. `tests/test_runner_flow.py` still calls `run_pipeline` with a missing `t.md`; its fixture stubs `task_file.read` and records one call after `nice.apply` and before `pick_model`. A `TaskFileError` of two problems is a `SystemExit` of both `<path>: <problem>` lines and `run_steps` is not called.

```python
#!/usr/bin/env python3
import ast, json, os, shutil, subprocess, sys, textwrap
from pathlib import Path

ROOT = Path.cwd()
PY = sys.executable
CLI = str(ROOT / "marestail" / "cli.py")
WORK = Path("/tmp/qa-019")
EMPTY = Path("/tmp/qa-019-empty")
INSTALL = Path("/tmp/qa-019-install")
HOME = Path("/tmp/qa-019-home")

def decode(text):
    return text.replace("\\r", "\r").replace("\\n", "\n")

def fresh(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path

def place(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode() if isinstance(text, str) else text)
    return path

def run(args, cwd, env=None, drop=()):
    merged = os.environ.copy()
    for name in ("MARESTAIL_NICE", "MARESTAIL_TASK", "MARESTAIL_DANDELION", *drop):
        merged.pop(name, None)
    if env:
        merged.update(env)
    return subprocess.run([PY, CLI, *args], cwd=cwd, env=merged, text=True, capture_output=True)

def expect(label, completed, code, stdout, stderr=None):
    problems = []
    if completed.returncode != code:
        problems.append(f"exit {completed.returncode}")
    if stdout is not None and completed.stdout != stdout:
        problems.append(f"stdout {completed.stdout!r}")
    if stderr is not None and completed.stderr != stderr:
        problems.append(f"stderr {completed.stderr!r}")
    if problems:
        raise SystemExit(f"{label}: {problems}\nstdout={completed.stdout!r}\nstderr={completed.stderr!r}")

def lines(text):
    return text if text.endswith("\n") else text + "\n"

BLOCK = '+++\ndepends = []\n+++\n# x\n'

def one(root, rel, body):
    fresh(root)
    place(root, rel, decode(body) if "\\n" in body or "\\r" in body else body)

def check(label, cwd, args, code, stdout):
    done = run(args, cwd)
    expect(label, done, code, stdout, "")

def main():
    help_checks()
    clean_checks()
    folder_checks()
    dedup_check()
    row_checks()
    slash_check()
    order_check()
    keys_check()
    extra_check()
    published_check()
    missing_checks()
    outside_check()
    dupe_check()
    cycle_checks()
    mixed_check()
    run_checks()
    prompt_checks()
    doc_checks()
    print("all ok")

def help_checks():
    top = run(["--help"], ROOT)
    expect("help", top, 0, None, "")
    if "{gate,run,tasks,install,sonar,watch,perf,visual,route,graph,depth}" not in top.stdout or "check task files" not in top.stdout:
        raise SystemExit(top.stdout)
    tasks = run(["tasks", "--help"], ROOT)
    if "{check}" not in tasks.stdout or "check the front matter and dependencies of task files" not in tasks.stdout:
        raise SystemExit(tasks.stdout)
    check_help = run(["tasks", "check", "--help"], ROOT)
    if "usage: marestail tasks check [-h] [PATH ...]" not in check_help.stdout:
        raise SystemExit(check_help.stdout)
    if "a folder of task files or a .md file (default: tasks/)" not in check_help.stdout:
        raise SystemExit(check_help.stdout)
    bare = run(["tasks"], ROOT)
    expect("tasks", bare, 2, "", "usage: marestail tasks [-h] {check} ...\nmarestail tasks: error: the following arguments are required: tasks_command\n")
    run_help = run(["run", "--help"], ROOT)
    if run_help.returncode != 0 or "--from" not in run_help.stdout or "--model" not in run_help.stdout or "front matter" in run_help.stdout:
        raise SystemExit(run_help.stdout)
    gate = run(["gate", "--help"], ROOT)
    if gate.returncode != 0 or "--tier" not in gate.stdout:
        raise SystemExit("gate help")
    for name in ("watch", "perf", "visual", "install", "sonar", "graph", "depth"):
        done = run([name, "--help"], ROOT)
        if done.returncode != 0:
            raise SystemExit(f"{name} --help {done.returncode}")
    route = run(["route", "--help"], ROOT, {"PATH": "/usr/bin:/bin"})
    if route.returncode != 127 or "dandelion is not installed" not in route.stderr:
        raise SystemExit(f"route {route.returncode} {route.stderr!r}")
    print("help ok")

def clean_checks():
    empty = fresh(WORK / "empty")
    check("empty", WORK, ["tasks", "check", "empty"], 0, "")
    root = fresh(WORK / "notoml")
    place(root, "tasks/018-a.md", decode('+++\ndepends = []\n+++\n# 018\n'))
    place(root, "tasks/019-b.md", decode('+++\ndepends = ["018-a"]\nstack = true\n+++\n# 019\n'))
    place(root, "tasks/020-c.md", decode('+++\ndepends = ["018-a", "019-b"]\nstack = false\n+++\n# 020\n'))
    place(root, "tasks/021-crlf.md", decode('+++\r\ndepends = []\r\n+++\r\n# ok\n'))
    check("default", root, ["tasks", "check"], 0, "")
    check("passed", ROOT, ["tasks", "check", str(root / "tasks")], 0, "")
    print("clean ok")

def folder_checks():
    root = fresh(WORK / "named")
    place(root, "named/README.md", decode(BLOCK))
    place(root, "named/readme.md", "# lower\n")
    place(root, "named/Readme.MD", "# mixed\n")
    place(root, "named/notes.txt", "not a task\n")
    place(root, "named/018-bad.md", "# no block\n")
    place(root, "named/nested/019-bad.md", "+++\ndepends = []\n")
    check("named", root, ["tasks", "check", "named"], 1, "named/018-bad.md: no front matter: line 1 must be +++\n")
    check("readme-name", root, ["tasks", "check", "named/README.md"], 0, "")
    check("lower", root, ["tasks", "check", "named/readme.md"], 1, "named/readme.md: no front matter: line 1 must be +++\n")
    check("txt", root, ["tasks", "check", "named/notes.txt"], 1, "named/notes.txt: not a .md file\n")
    check("nested", root, ["tasks", "check", "named/nested/019-bad.md"], 1, "named/nested/019-bad.md: front matter is not closed: no +++ line after line 1\n")
    bare = fresh(WORK / "bare")
    check("missing", bare, ["tasks", "check", "missing_dir/"], 1, "missing_dir: no such file or folder\n")
    check("no-tasks", bare, ["tasks", "check"], 1, "tasks: no such file or folder\n")
    print("folder ok")

def dedup_check():
    root = fresh(WORK / "once")
    place(root, "once/019-bad.md", "# title\n")
    check("dedup", root, ["tasks", "check", "once/sub/../019-bad.md", "once"], 1, "once/sub/../019-bad.md: no front matter: line 1 must be +++\n")
    print("dedup ok")

ROWS = [
    ("# 019 title\\n", "no front matter: line 1 must be +++"),
    ("\\n+++\\ndepends = []\\n+++\\n# x\\n", "no front matter: line 1 must be +++"),
    ("+++ \\ndepends = []\\n+++\\n# x\\n", "no front matter: line 1 must be +++"),
    ("+++ \\r\\ndepends = []\\r\\n+++\\r\\n# x\\n", "no front matter: line 1 must be +++"),
    ("+++\\ndepends = [\"018-a\"]\\nstack = true\\n# no close\\n", "front matter is not closed: no +++ line after line 1"),
    ("+++\\ndepends = []\\n+++ \\n# x\\n", "front matter is not closed: no +++ line after line 1"),
    ("+++\\ndepends = [broken\\n+++\\n# x\\n", "front matter is not valid TOML: Invalid value (at line 1, column 12)"),
    ("+++\\ndepends = []\\n=\\n+++\\n# x\\n", "front matter is not valid TOML: Invalid statement (at line 2, column 1)"),
    ("+++\\ndepends = \"018-a\"\\n+++\\n# x\\n", "depends must be an array of strings"),
    ("+++\\ndepends = [1]\\nstack = true\\n+++\\n# x\\n", "depends must be an array of strings"),
    ("+++\\ndepends = [1, \"018-a\"]\\nstack = true\\n+++\\n# x\\n", "depends must be an array of strings"),
    ("+++\\ndepends = [\"\"]\\nstack = true\\n+++\\n# x\\n", "depends entry '' is not a task id; use the file name without .md"),
    ("+++\\ndepends = [\"sub/018-a\"]\\nstack = true\\n+++\\n# x\\n", "depends entry 'sub/018-a' is not a task id; use the file name without .md"),
    ("+++\\ndepends = [\"018-a.md\"]\\nstack = true\\n+++\\n# x\\n", "depends entry '018-a.md' is not a task id; use the file name without .md"),
    ("+++\\ndepends = [\"018-a\", \"018-a\"]\\nstack = false\\n+++\\n# x\\n", "depends lists 018-a twice"),
    ("+++\\ndepends = [\"019-test\"]\\nstack = true\\n+++\\n# x\\n", "depends on itself"),
    ("+++\\ndepends = [\"018-a\"]\\nstack = \"yes\"\\n+++\\n# x\\n", "stack must be true or false, got 'yes'"),
    ("+++\\ndepends = [\"018-a\"]\\nstack = 1\\n+++\\n# x\\n", "stack must be true or false, got 1"),
    ("+++\\ndepends = [\"018-a\"]\\n+++\\n# x\\n", "stack is required when depends is not empty"),
    ("+++\\ndepends = []\\nstack = false\\n+++\\n# x\\n", "stack is not allowed when depends is empty"),
    ("+++\\ndepends = [\"018-a\", \"018-b\"]\\nstack = true\\n+++\\n# x\\n", "stack = true needs exactly one dependency, got 2"),
    ("+++\\n+++\\n# x\\n", "front matter has no depends; write depends = [] for a task with no dependencies"),
]

def row_checks():
    root = WORK / "row"
    for body, problem in ROWS:
        one(root, "tasks/019-test.md", body)
        check(problem, root, ["tasks", "check", "tasks/019-test.md"], 1, f"tasks/019-test.md: {problem}\n")
    print("rows ok")

def slash_check():
    root = fresh(WORK / "slash")
    line = 'depends = ["018' + chr(92) + chr(92) + 'a"]'
    place(root, "tasks/019-slash.md", "+++\n" + line + "\nstack = true\n+++\n# x\n")
    shown = "depends entry '018" + chr(92) + "a' is not a task id; use the file name without .md"
    check("slash", root, ["tasks", "check", "tasks/019-slash.md"], 1, f"tasks/019-slash.md: {shown}\n")
    print("slash ok")

def order_check():
    root = fresh(WORK / "order")
    place(root, "tasks/019-test.md", '+++\ndepends = ["", "sub/018", "018-a", "018-a"]\nstack = true\n+++\n# x\n')
    check("order", root, ["tasks", "check", "tasks/019-test.md"], 1, lines(textwrap.dedent("""\
        tasks/019-test.md: depends entry '' is not a task id; use the file name without .md
        tasks/019-test.md: depends entry 'sub/018' is not a task id; use the file name without .md
        tasks/019-test.md: depends lists 018-a twice
        tasks/019-test.md: stack = true needs exactly one dependency, got 4""")))
    print("order ok")

def keys_check():
    root = fresh(WORK / "keys")
    place(root, "tasks/021-keys.md", "+++\nzebra = 1\nalpha = 2\n+++\n# x\n")
    check("keys", root, ["tasks", "check", "tasks/021-keys.md"], 1, lines(textwrap.dedent("""\
        tasks/021-keys.md: unknown front matter key 'alpha'; allowed keys are depends and stack
        tasks/021-keys.md: unknown front matter key 'zebra'; allowed keys are depends and stack
        tasks/021-keys.md: front matter has no depends; write depends = [] for a task with no dependencies""")))
    print("keys ok")

def extra_check():
    root = fresh(WORK / "extra")
    place(root, "tasks/021-extra.md", '+++\ndepends = ["018-a"]\nextra = 1\n+++\n# x\n')
    check("extra", root, ["tasks", "check", "tasks/021-extra.md"], 1, lines(textwrap.dedent("""\
        tasks/021-extra.md: unknown front matter key 'extra'; allowed keys are depends and stack
        tasks/021-extra.md: stack is required when depends is not empty""")))
    print("extra ok")

def published_check():
    root = fresh(WORK / "pub")
    place(root, "tasks/020-run-events.md", '+++\ndepends = ["018-runs-stay-nice", "019-tasks"]\nstack = true\n+++\n# 020\n')
    place(root, "tasks/021-status.md", "+++\ndepend = []\n+++\n# 021\n")
    check("published", root, ["tasks", "check", "tasks"], 1, lines(textwrap.dedent("""\
        tasks/020-run-events.md: stack = true needs exactly one dependency, got 2
        tasks/021-status.md: unknown front matter key 'depend'; allowed keys are depends and stack
        tasks/021-status.md: front matter has no depends; write depends = [] for a task with no dependencies""")))
    print("published ok")

def task(depends, stack):
    stack_line = "" if stack is None else f"\nstack = {stack}"
    return f"+++\ndepends = {depends}{stack_line}\n+++\n# x\n"

def missing_checks():
    root = fresh(WORK / "ord")
    place(root, "ord/019-a.md", task('["099-z", "098-a"]', "false"))
    check("ord", root, ["tasks", "check", "ord/019-a.md"], 1, lines(textwrap.dedent("""\
        ord/019-a.md: depends on 099-z, but ord/099-z.md does not exist
        ord/019-a.md: depends on 098-a, but ord/098-a.md does not exist""")))
    root = fresh(WORK / "sides")
    place(root, "left/019-a.md", task('["020-b"]', "true"))
    place(root, "right/020-b.md", task("[]", None))
    check("sides", root, ["tasks", "check", "left", "right"], 1, "left/019-a.md: depends on 020-b, but left/020-b.md does not exist\n")
    place(root, "left/019-a.md", task('["020-gone"]', "true"))
    check("dotdot", root, ["tasks", "check", "left/sub/../019-a.md"], 1, "left/sub/../019-a.md: depends on 020-gone, but left/sub/../020-gone.md does not exist\n")
    print("missing ok")

def outside_check():
    root = fresh(WORK / "box")
    place(root, "box/018-prev.md", "+++\n")
    place(root, "box/019-next.md", task('["018-prev"]', "true"))
    check("one", root, ["tasks", "check", "box/019-next.md"], 0, "")
    check("box", root, ["tasks", "check", "box"], 1, "box/018-prev.md: front matter is not closed: no +++ line after line 1\n")
    print("outside ok")

def dupe_check():
    root = fresh(WORK / "dup")
    for folder in ("alpha", "beta", "mid"):
        place(root, f"{folder}/019-dup.md", task("[]", None))
    check("dup", root, ["tasks", "check", "alpha", "beta", "mid"], 1, lines(textwrap.dedent("""\
        beta/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md
        mid/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md""")))
    print("dupes ok")

def cycle_checks():
    root = fresh(WORK / "cycle")
    place(root, "cycle/020-b.md", task('["021-c"]', "true"))
    place(root, "cycle/021-c.md", task('["019-a"]', "true"))
    place(root, "cycle/019-a.md", task('["020-b"]', "true"))
    place(root, "cycle/022-d.md", task('["021-c"]', "true"))
    check("cycle", root, ["tasks", "check", "cycle"], 1, "cycle/019-a.md: dependency cycle: 019-a -> 020-b -> 021-c -> 019-a\n")
    root = fresh(WORK / "twin")
    place(root, "twin/019-a.md", task('["020-b", "021-c"]', "false"))
    place(root, "twin/020-b.md", task('["019-a"]', "true"))
    place(root, "twin/021-c.md", task('["019-a"]', "true"))
    check("twin", root, ["tasks", "check", "twin"], 1, lines(textwrap.dedent("""\
        twin/019-a.md: dependency cycle: 019-a -> 020-b -> 019-a
        twin/019-a.md: dependency cycle: 019-a -> 021-c -> 019-a""")))
    print("cycles ok")

def mixed_check():
    root = fresh(WORK / "mix")
    place(root, "alpha/018-bad.md", "+++\ndepends = []\n")
    place(root, "alpha/019-a.md", task('["099-missing", "020-b"]', "false"))
    place(root, "alpha/020-b.md", task('["019-a"]', "true"))
    place(root, "alpha/022-d.md", task('["019-a"]', "true"))
    place(root, "beta/019-a.md", task("[]", None))
    check("mix", root, ["tasks", "check", "alpha", "beta"], 1, lines(textwrap.dedent("""\
        alpha/018-bad.md: front matter is not closed: no +++ line after line 1
        alpha/019-a.md: depends on 099-missing, but alpha/099-missing.md does not exist
        alpha/019-a.md: dependency cycle: 019-a -> 020-b -> 019-a
        beta/019-a.md: duplicate task id 019-a: also alpha/019-a.md""")))
    print("mixed ok")

def git_repo(path):
    fresh(path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "qa@marestail"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "qa"], cwd=path, check=True)
    place(path, ".gitignore", ".marestail/\n")
    place(path, "marestail.toml", '[git]\nbase = "main"\n[perf]\nenabled = false\n[practices]\nenabled = false\n')
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)

def write_stub(directory):
    path = directory / "stub.py"
    path.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os, re, sys
        from pathlib import Path
        prompt = sys.stdin.read()
        Path(os.environ["QA_PROMPT"]).write_text(prompt)
        found = re.search(r"Write your verdict to (\\S+) and", prompt)
        verdict = Path(found.group(1))
        verdict.parent.mkdir(parents=True, exist_ok=True)
        verdict.write_text("VERDICT: PASS\\n")
        print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0, "result": "ok"}))
        """))
    path.chmod(0o755)
    return path

def critic(repo, args, env, prompt_path):
    prompt_path.unlink(missing_ok=True)
    extra = {"MARESTAIL_AGENT": "claude", "MARESTAIL_CLAUDE": str(prompt_path.parent / "stub.py"), "QA_PROMPT": str(prompt_path), **env}
    return run(args, repo, extra)

def run_checks():
    repo = WORK / "repo"
    git_repo(repo)
    stub = write_stub(WORK)
    prompt = WORK / "prompt.md"
    base = ["run", "tasks/020-run-events.md", "--from", "critic", "--to", "critic", "--auto", "--retries", "1"]
    place(repo, "tasks/020-run-events.md", '+++\ndepends = ["018-runs-stay-nice", "019-tasks"]\nstack = true\n+++\n# 020\n')
    bad = critic(repo, base, {}, prompt)
    expect("run-020", bad, 1, "", "tasks/020-run-events.md: stack = true needs exactly one dependency, got 2\n")
    if prompt.exists() or (repo / ".marestail" / "runs" / "020-run-events").exists():
        raise SystemExit("stub or runs dir appeared for a broken block")
    place(repo, "tasks/021-status.md", "+++\ndepend = []\n+++\n# 021\n")
    two = critic(repo, ["run", "tasks/021-status.md", "--from", "critic", "--to", "critic", "--auto", "--retries", "1"], {}, prompt)
    expect("run-021", two, 1, "", lines(textwrap.dedent("""\
        tasks/021-status.md: unknown front matter key 'depend'; allowed keys are depends and stack
        tasks/021-status.md: front matter has no depends; write depends = [] for a task with no dependencies""")))
    nice = critic(repo, base, {"MARESTAIL_NICE": "high"}, prompt)
    expect("nice", nice, 1, "", "nice must be an integer 0-19, got 'high'\n")
    if "stack = true" in nice.stderr:
        raise SystemExit(nice.stderr)
    model = critic(repo, base + ["--model", "dandelion/route", "--agent", "grok"], {}, prompt)
    expect("model", model, 1, "", "tasks/020-run-events.md: stack = true needs exactly one dependency, got 2\n")
    if "drop --agent" in model.stderr:
        raise SystemExit(model.stderr)
    missing = critic(repo, ["run", "tasks/missing.md", "--from", "critic", "--to", "critic", "--auto", "--retries", "1"], {}, prompt)
    if missing.returncode != 1 or missing.stdout or "FileNotFoundError" not in missing.stderr or "tasks/missing.md" not in missing.stderr:
        raise SystemExit(f"missing {missing.returncode} {missing.stderr!r}")
    if (repo / ".marestail" / "runs" / "missing").exists():
        raise SystemExit("missing run dir")
    fresh(EMPTY)
    nowhere = run(base, EMPTY)
    expect("notoml", nowhere, 1, "", "no marestail.toml found above /tmp/qa-019-empty\n")
    print("run-bad ok")
    place(repo, "tasks/t.md", "# Add one\n")
    plain = critic(repo, ["run", "tasks/t.md", "--from", "critic", "--to", "critic", "--auto", "--retries", "1"], {}, prompt)
    if plain.returncode != 0 or "pipeline complete" not in plain.stdout or plain.stderr:
        raise SystemExit(f"plain {plain.returncode}\n{plain.stdout}\n{plain.stderr}")
    if not prompt.exists():
        raise SystemExit("stub did not start")
    timeline = json.loads((repo / ".marestail" / "runs" / "t" / "timeline.json").read_text())
    if timeline["task"] != "t" or not (repo / ".marestail" / "runs" / "t" / "timeline.md").exists():
        raise SystemExit(timeline)
    if not (repo / ".marestail" / "handoffs" / "t").is_dir() or (repo / ".marestail" / "runs" / "t" / "pipeline.log").exists():
        raise SystemExit("layout")
    if "# Task\n# Add one" not in prompt.read_text():
        raise SystemExit(prompt.read_text())
    place(repo, "tasks/018-prev.md", "+++\n")
    place(repo, "tasks/019-demo.md", '+++\ndepends = ["018-prev"]\nstack = true\n+++\n# Something else\n\nCarry on.\n')
    demo = critic(repo, ["run", "tasks/019-demo.md", "--from", "critic", "--to", "critic", "--auto", "--retries", "1"], {}, prompt)
    if demo.returncode != 0 or "pipeline complete" not in demo.stdout or demo.stderr:
        raise SystemExit(f"demo {demo.returncode}\n{demo.stdout}\n{demo.stderr}")
    body = prompt.read_text()
    sentence = "This task depends on `018-prev`, which runs before it, so its work is already in the tree. Treat what it delivered as existing behaviour: build on it and keep it working.\n\n# Something else\n\nCarry on."
    if "# Task\n" + sentence not in body or "+++" in body or "depends =" in body or "stack" in body:
        raise SystemExit(body)
    demo_timeline = json.loads((repo / ".marestail" / "runs" / "019-demo" / "timeline.json").read_text())
    if demo_timeline["task"] != "019-demo" or not (repo / ".marestail" / "handoffs" / "019-demo").is_dir():
        raise SystemExit("demo layout")
    print("run-ok ok")

def task_text(prompt):
    rest = prompt.split("# Task\n", 1)[1]
    end = rest.find("\n\n#")
    return rest if end < 0 else rest[:end]

def prompt_checks():
    from marestail.config import Config
    from marestail.pipeline import find
    from marestail import prompts
    root = fresh(WORK / "prompts")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    config = Config(root=root, raw={"git": {"base": "main"}})
    report = root / "report.md"
    coder, critic_role = find("coder"), find("critic")
    sentence = {
        1: "This task depends on `018-runs-stay-nice`, which runs before it, so its work is already in the tree. Treat what it delivered as existing behaviour: build on it and keep it working.",
        2: "This task depends on `017-a` and `018-b`, which run before it, so their work is already in the tree. Treat what they delivered as existing behaviour: build on it and keep it working.",
        3: "This task depends on `017-a`, `018-b` and `019-c`, which run before it, so their work is already in the tree. Treat what they delivered as existing behaviour: build on it and keep it working.",
        4: "This task depends on `017-a`, `018-b`, `019-c` and `020-d`, which run before it, so their work is already in the tree. Treat what they delivered as existing behaviour: build on it and keep it working.",
    }
    files = [
        ("spaced", " Do the thing. \n", "Do the thing.", True),
        ("add", "# Add one\n", "# Add one", True),
        ("fence", "+++ \ndepends = []\n+++\n# Later\n", "+++ \ndepends = []\n+++\n# Later", False),
        ("empty", "+++\ndepends = []\n+++\n# Title\n\nBody.\n", "# Title\n\nBody.", True),
        ("one-true", '+++\ndepends = ["018-runs-stay-nice"]\nstack = true\n+++\n# Title\n\nBody.\n', sentence[1] + "\n\n# Title\n\nBody.", True),
        ("one-false", '+++\ndepends = ["018-runs-stay-nice"]\nstack = false\n+++\n# Title\n\nBody.\n', sentence[1] + "\n\n# Title\n\nBody.", True),
        ("two", '+++\ndepends = ["017-a", "018-b"]\nstack = false\n+++\n# Title\n\nBody.\n', sentence[2] + "\n\n# Title\n\nBody.", True),
        ("three", '+++\ndepends = ["017-a", "018-b", "019-c"]\nstack = false\n+++\n# Title\n\nBody.\n', sentence[3] + "\n\n# Title\n\nBody.", True),
        ("four", '+++\ndepends = ["017-a", "018-b", "019-c", "020-d"]\nstack = false\n+++\n# Title\n\nBody.\n', sentence[4] + "\n\n# Title\n\nBody.", True),
    ]
    for name, body, expected, hidden in files:
        path = place(root, f"tasks/{name}.md", body)
        built = [
            prompts.worker_prompt(config, coder, path, name, report, ""),
            prompts.judge_prompt(config, critic_role, path, name, report, ""),
            prompts.perf_author_prompt(config, path, name, "", report),
        ]
        texts = [task_text(item) for item in built]
        if texts != [expected, expected, expected]:
            raise SystemExit(f"{name}: {texts!r}")
        if hidden and any("+++" in item or "depends =" in item or "stack" in item for item in built):
            raise SystemExit(name)
    print("prompts ok")

DEPENDENCIES = """## Dependencies

A task that needs another task first says so in a block at the very top of the file, before the title:

    +++
    depends = ["018-runs-stay-nice"]
    stack = true
    +++

`depends` lists task ids: file names in this folder without `.md`. Write `depends = []` for a task that needs nothing. `stack = true` runs the task on its one dependency's branch, after it; `stack = false` starts it on its own branch once every dependency is merged. Leave `stack` out when `depends` is empty. `marestail tasks check` reports every problem in this folder, one line each. `marestail run` works with or without the block.
"""
PARAGRAPH = "A task file may open with a `+++` TOML block holding `depends` and `stack`. `depends` lists task ids, the file names in that folder without the `.md` suffix, and `depends = []` means the task needs nothing. `stack = true` runs the task on its one dependency's branch, after it; `stack = false` starts the task on its own branch once every dependency is merged. Leave `stack` out when `depends` is empty. `marestail tasks check` checks a folder of task files. `marestail run` never requires the block. Roles see one sentence naming the dependencies, not the block.\n"
USE = "marestail tasks check          # front matter and dependencies of every task in tasks/; or pass folders and .md files"

def doc_checks():
    template = (ROOT / "templates" / "tasks-README.md").read_text()
    marker = "Name files `NNN-short-name.md`. The specifier writes `features/short-name.feature` and `qa/short-name.md` to match.\n\n"
    if marker + DEPENDENCIES not in template:
        raise SystemExit("template ending")
    readme = (ROOT / "README.md").read_text()
    needle = "marestail run tasks/001.md --model dandelion/route-best   # ask dandelion route --high before every session\n" + USE + "\n"
    if needle not in readme:
        raise SystemExit("use line")
    if PARAGRAPH not in readme:
        raise SystemExit("writing tasks")
    import re
    writing = readme.split("## Writing tasks", 1)[1].split("## ", 1)[0]
    if re.search(r"`[^`\n]*/[^`\n]*`", writing):
        raise SystemExit("backticked path in Writing tasks")
    fresh(INSTALL)
    fresh(HOME)
    done = run(["install", str(INSTALL)], ROOT, {"HOME": str(HOME), "GROK_HOME": str(HOME / "grok")})
    if done.returncode != 0:
        raise SystemExit(done.stderr)
    installed = INSTALL / "tasks" / "README.md"
    if installed.read_text() != template:
        raise SystemExit("install copy")
    installed.write_text("keep\n")
    again = run(["install", str(INSTALL)], ROOT, {"HOME": str(HOME), "GROK_HOME": str(HOME / "grok")})
    if again.returncode != 0 or installed.read_text() != "keep\n":
        raise SystemExit("second install")
    frozen = (ROOT / "tasks" / "README.md").read_text()
    if "## Dependencies" in frozen:
        raise SystemExit("frozen readme")
    if not (ROOT / "tasks" / "018-runs-stay-nice.md").read_text().startswith("# 018 — runs stay nice"):
        raise SystemExit("018")
    if not (ROOT / "tasks" / "019-tasks-say-what-they-depend-on.md").read_text().startswith("# 019 — task files say what they depend on"):
        raise SystemExit("019")
    for path in (ROOT / "tasks").glob("*.md"):
        if path.read_text().splitlines()[0].rstrip("\r") == "+++":
            raise SystemExit(path)
    layer = (ROOT / ".importlinter").read_text().splitlines()
    if not any("marestail.report : marestail.config : marestail.changes : marestail._location" in line and "marestail.task_file" in line for line in layer):
        raise SystemExit("layer")
    text = (ROOT / ".importlinter").read_text()
    foundations = text.split("[importlinter:contract:foundations-import-nothing-above]", 1)[1].split("[importlinter:", 1)[0]
    if "marestail.task_file" not in foundations:
        raise SystemExit("foundations")
    modules = set()
    tree = ast.parse((ROOT / "marestail" / "task_file.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    if modules != {"tomllib", "pathlib", "dataclasses"}:
        raise SystemExit(modules)
    print("docs ok")

if __name__ == "__main__":
    main()
```
