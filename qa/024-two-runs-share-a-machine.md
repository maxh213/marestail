# QA: 024 — two runs of one repo share a machine

Run from the marestail-green root with the venv active. `GREEN` is that root. Save the script at the bottom as `/tmp/suffix-qa.py`. It stubs Docker and SonarQube. Do not start either.

A suffix is `[a-z0-9-]{1,32}`. Empty is invalid, not unset. With nothing set, every name, port and command line stays as it is today.

## What you see

A bad `MARESTAIL_RUN_SUFFIX` stops `marestail gate` before any gate, including outside a repo and including `--hook`. Exit 1. stderr is one line and stdout is empty:

```
MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'
```

`marestail run` inside a repo prints that same line only after nice has been accepted. `MARESTAIL_NICE=high` still wins, and stdout is empty:

```
nice must be an integer 0-19, got 'high'
```

`MARESTAIL_JOBS=0` with no suffix still prints its own line. With both bad, the suffix line wins and jobs is not read:

```
MARESTAIL_JOBS must be a whole number 1 or more, got '0'
```

`--run-suffix A1` on `run`, `gate` and `sonar` exits 2. stderr ends with:

```
argument --run-suffix: must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'
```

A valid suffix does not hide a focus clash. Exit 2, stderr exactly:

```
--focus cannot be combined with --scope all
```

`marestail gate` with the suffix set, credentials present and no `[sonar] project_key` does not scan. The seconds are 0.0. Ten spaces sit between `sonar` and `no`:

```
[FAIL] sonar          no project key  (0.0s)
       marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set

GATE FAILED: sonar
```

`marestail sonar prune` with neither flag nor variable exits 1. stderr:

```
sonar prune needs a run suffix: pass --run-suffix or set MARESTAIL_RUN_SUFFIX; it never deletes the unsuffixed project
```

Prune with a suffix prints one of these and exits 0. A missing project is not an error. A repeat is the second line:

```
deleted sonar project key_a1
```

```
no sonar project key_a1
```

No stored token:

```
sonar is not set up; run: marestail sonar setup
```

`sonar setup` is given the suffixed key, so the line it already prints for that key is:

```
sonar ready: http://localhost:9000/dashboard?id=marestail_a1
```

This repo's scanner arguments for `a1` are `-Dsonar.projectKey=marestail_a1` followed by `-Dsonar.projectName=marestail (a1)`. `perf db url --tree head` under `a1`, with the password file holding `pw24`, prints:

```
postgresql://postgres:pw24@127.0.0.1:57376/bench
```

`marestail perf db --help` describes down as:

```
remove this run's performance database containers (every one when MARESTAIL_RUN_SUFFIX is unset), keeping the volume
```

The README row after `MARESTAIL_TASK`, and the two Use lines after `marestail sonar setup`, are:

```
| `MARESTAIL_RUN_SUFFIX` | per-run suffix (`[a-z0-9-]{1,32}`) for the names a run shares with the machine: Sonar project `<key>_<suffix>`, perf containers `…_<suffix>` and their ports; `--run-suffix` on `run`, `gate` and `sonar` sets it |
```

```
marestail run tasks/001.md --run-suffix a1   # a second run of this repo on this machine, in its own checkout
marestail sonar prune --run-suffix a1   # delete the Sonar project that suffix created
```

## Procedure

1. `python3 /tmp/suffix-qa.py rules`
   Expected: exit 0, prints `rules ok`. Unset: `current()` is None, `named("k")` is `k`, `titled("K")` is `K`, and a later set and delete is seen on the next call. `a1`, `a`, `0`, `-`, `a-1-b` and 32 `a`s pass `current` and `argument` unchanged; `named` is `k_<suffix>` and `titled` is `K (<suffix>)`, including `k_-` and `K (-)`. The rejected values `""`, `A1`, `a_1`, `a.1`, `a/b`, `a 1`, `a1` plus a newline, `é` and 33 `a`s each raise SystemExit `MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got <repr>`, one line, so the newline value's message contains a backslash and `n`, not a line break. `argument` of each raises `argparse.ArgumentTypeError` with the same `got` and without the `MARESTAIL_RUN_SUFFIX must be` prefix. The module's only module-level imports are `os` and `re`. `argument` imports `argparse` inside the function.

2. `python3 /tmp/suffix-qa.py cli`
   Expected: exit 0, prints `cli ok`. `run t.md --from coder --to qa --auto --model m --retries 2 --agent grok --effort high --scope hard --focus a --focus b --run-suffix a1` calls `run_pipeline` with `(Path("t.md"), "coder", "qa", True, "m", 2, "grok", "high", "hard", ["a", "b"])` while the environment is `a1`, even if it was `zz`. The same run with no flag leaves `zz`. `--run-suffix A1` on `run`, `gate` and `sonar setup` exits 2 with the argparse ending above and does not call the handler. `run`, `gate` and `sonar` help mention `--run-suffix`. `perf`, `queue` and `status` help do not. `SONAR_ACTIONS` is `("up", "down", "setup", "prune")`. `sonar nope` exits 2 and stderr contains `invalid choice: 'nope' (choose from up, down, setup, prune)`.

3. `python3 /tmp/suffix-qa.py order`
   Expected: exit 0, prints `order ok`. With the suffix unset, a valid task records `nice`, `suffix`, `jobs`, `read`, `pick_model`, stdout does not contain `MARESTAIL_RUN_SUFFIX`, and the run-start line in `events.jsonl` has no `suffix` key. `MARESTAIL_NICE=high` with `A1` raises `nice must be an integer 0-19, got 'high'` and records only `nice`. `A1` alone, and `A1` together with `MARESTAIL_JOBS=0`, raise the suffix message, record `nice`, `suffix`, do not call jobs, do not read the task, and leave stdout empty. `MARESTAIL_JOBS=0` with the suffix unset raises `MARESTAIL_JOBS must be a whole number 1 or more, got '0'` and records `nice`, `suffix`, `jobs`. From a directory with no `marestail.toml`, `run t.md` with `A1` raises `no marestail.toml found above` and not the suffix message.

4. `python3 /tmp/suffix-qa.py gate`
   Expected: exit 0, prints `gate ok`. `gate --run-suffix a1 --tier fast` with the environment at `zz` calls `run_gates_with_context` with the environment already `a1`. `gate --tier fast` and `gate --hook` with `A1` raise the suffix message, run no gate, and the hook prints nothing. `gate --scope all --focus a.py` with `A1` raises the suffix message, not the clash. The same command with `--run-suffix a1` exits 2 and stderr is `--focus cannot be combined with --scope all\n`. `gate --tier nope` with `A1` exits 2, stderr contains `invalid choice: 'nope'`, and it does not contain the suffix message. Outside a repo, `gate` with `A1` is the suffix message, not `no marestail.toml found above`.

5. `python3 /tmp/suffix-qa.py sonar`
   Expected: exit 0, prints `sonar ok`. In a directory named `repo` with `project_key = "key"`, `sonar setup` and `sonar --run-suffix a1 setup` call `setup("key_a1", "repo (a1)")`. Adding `project_name = "N"` calls `("key_a1", "N (a1)")`. A suffix and no key raises `marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set` and does not call `setup`. With the suffix unset, `("key", "repo")` and `(None, "N")` stay. In this repo, `a1` calls `("marestail_a1", "marestail (a1)")`, which is the ready line in the fence. `sonar up` and `sonar down` with `a1` or with `A1` call `up()` and `down()` with no arguments. `prune` with neither flag nor variable raises the needs-a-suffix message and does not call `prune`. `A1` and no flag raises the suffix message. `prune --run-suffix a1` with the environment at `zz` calls `prune("key_a1")`. The environment `b2` and no flag calls `prune("key_b2")`. No key raises the same missing-key message. `prune("key_a1")` with `A1` still in the environment does not read it: no credentials raises `sonar is not set up; run: marestail sonar setup` and makes no request; a found project GETs `api/projects/search` with `projects=key_a1` and POSTs `api/projects/delete` with `project=key_a1` and prints `deleted sonar project key_a1\n`; an empty search does not POST and prints `no sonar project key_a1\n`; HTTP 401 raises `sonar GET api/projects/search?projects=key_a1: 401 no`. `prune` does not call `up` or `down`. `DEFAULT_URL` is `http://localhost:9000`. `sonar/docker-compose.yml`, the file `setup.COMPOSE` reads at `marestail/sonar/docker-compose.yml`, still contains `container_name: marestail-sonarqube` and `"9000:9000"`.

6. `python3 /tmp/suffix-qa.py scan`
   Expected: exit 0, prints `scan ok`. `scanner_command(ctx, creds, "proj")` with `a1` is today's command with `-Dsonar.projectKey=proj_a1` followed immediately by `-Dsonar.projectName=proj (a1)`. `sonar.projectName=Props` makes the name `Props (a1)`. `[sonar] project_name = "Nice"` makes it `Nice (a1)` and beats the properties file. With the suffix unset, `Props` adds no `sonar.projectName` argument. A clean `run_gate` with `project_key = "proj"` and `a1` puts `proj_a1` on the docker command and does not put `proj_a1_a1` anywhere; every get param named `projectKey`, `componentKeys`, `project` or `component` is `proj_a1`; `api/ce/task` is `{"id": "T1"}`; posts stay empty. Credentials present, `a1`, and no key returns `Result("sonar", False, "no project key", ["marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set"])` with `seconds` `0.0`, no docker call and no client. The rendered text is the FAIL fence above. Missing credentials with `a1` and no key stays `("sonar", False, "not set up", ["run: marestail sonar setup"])`. `dotnet_scan` of key `proj` with `a1` contains `/k:"proj_a1"` and `/n:"proj (a1)"`, and not `proj_a1_a1` or `proj_a1 (a1)`. `project_name = "Nice"` contains `/n:"Nice (a1)"`. With the suffix unset the script contains `/k:"proj" /n:"Nice"` and, with no project name, `/n:"proj"`. This repo's command contains `-Dsonar.projectKey=marestail_a1` followed by `-Dsonar.projectName=marestail (a1)`.

7. `python3 /tmp/suffix-qa.py perf`
   Expected: exit 0, prints `perf ok`. `suffix=None` keeps `mp-files`, `mp-db-<key>-head`, `mp-golden-g`, `/perf/work/<key>-head`, ports `55432`, `55433`, `55434`, and `build_database` with no port setting equals today's `Database(...)` including `suffix is None`. `suffix="a1"` makes `mp-files_a1`, `mp-db-<key>-head_a1`, `mp-golden-g_a1`, `/perf/work/<key>-head_a1`, and `volume` stays `vol`. `build_database` with `a1` has port `57376`; `b2` has `58003`; `slot-1` has `56743`; `[perf.db] port = 6000` and `a1` has `7944`; `MARESTAIL_PERF_DB_PORT=7000` with that file port and `a1` has `7000`; base `65535` and `a1` returns `67479` and does not raise. `tree_port` of the `a1` database is `57376`, `57377`, `57378`. `tree_port("control")` still raises `KeyError`. `golden_name` is the same with and without `a1`. `start_postgres` and `ensure_helper` with `a1` put `--label marestail.run-suffix=a1` immediately after `--name <name>`. With `None`, those commands contain no `--label`. Docker exit 125 and `port is already allocated` raises `starting <container> failed (exit 125): port is already allocated`. `down` with `a1` lists `docker ps -a --filter label=marestail.run-suffix=a1 --format {{.Names}}` and removes only the names it printed. `down` with `None` lists `docker ps -a --format {{.Names}}` and removes every name that starts with the prefix, including one that ends in `_a1`. The old `mp-files` / `other` / `mp-db-1` reply still removes only `mp-files` and `mp-db-1`. `url --tree head` under `a1` prints the URL in the fence. `release` with `a1` and the helper running removes `mp-db-<key>-head_a1` and `mp-db-<key>-baseline_a1` and execs `mp-files_a1` to remove the two suffixed work dirs. `perf db` with no `migrate` and `A1` prints `configure [perf.db] migrate in marestail.toml\n` and does not raise. `perf run` without `--db` and `A1` prints `no perf run in progress\n` and does not raise. `perf run --db` and `perf db url` with `migrate` set and `A1` raise the suffix message. `start_build`'s argv ends with `perf db golden --tree head --wait` and has no `--run-suffix`. `TEMP_PREFIX` stays `marestail-perf-`. The QA app's preferred port stays `3400`. `perf db` help contains the down sentence in the fence.

8. `python3 /tmp/suffix-qa.py quiet`
   Expected: exit 0, prints `quiet ok`. With `A1` set, `tasks check tasks/024-two-runs-share-a-machine.md`, `status` of an empty directory, `abandon --help`, `watch --help`, `dash --help`, `visual --help`, `remote --help`, `graph`, `depth`, `route --help`, `install --help` and `queue --help` do not print the suffix message. `graph` and `depth` exit 0. The queue child argv contains `run` and does not contain `--run-suffix`. With `a1` set, the child environment contains it. With it unset, the child environment does not gain it. A run-start field list and the status JSON keys do not contain `suffix`.

9. `python3 /tmp/suffix-qa.py docs`
   Expected: exit 0, prints `docs ok`. The row in the fence is the next line after the `MARESTAIL_TASK` row, and `MARESTAIL_QA_CMD_TIMEOUT` is the line after that. The two Use lines in the fence are the next two lines after `marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail`, and `marestail gate` is the line after those. The first paragraph after the `## Use` code fence is the suffix paragraph: two runs, each in its own checkout, without editing a tracked file; the key `<key>_<suffix>` and the name `<name> (<suffix>)`; the perf containers and their ports; not the SonarQube server, the perf volume, or anything under `.marestail/`; the gate creates the project on its first analysis; `marestail sonar prune` deletes it. The next paragraph still begins `` `marestail run` starts at nice 19 ``. The `--db` paragraph ends with the sentence that containers end in `_<suffix>`, ports move unless `MARESTAIL_PERF_DB_PORT` is set, `down` removes only that run's containers, goldens in the shared volume are still reused, a shared hash slot fails because the port is already allocated, and `MARESTAIL_PERF_DB_PORT` is the way out. The `.importlinter` layer line that contains `marestail.report` and `marestail._location` also contains `marestail.run_suffix`. `foundations-import-nothing-above` lists it. The layer that contains `marestail.jobs` does not, and the file still has 38 contracts. `tests/conftest.py` `hermetic` deletes `MARESTAIL_RUN_SUFFIX` as well as `MARESTAIL_JOBS`. `marestail/sonar/setup.py` does not import `run_suffix`.

10. `python -m pytest tests/test_run_suffix.py tests/test_cli.py tests/test_runner_flow.py tests/test_sonar_setup.py tests/test_gates_sonar.py tests/test_perf_db.py tests/test_perf_trees.py tests/test_gates_qa.py tests/test_nice.py tests/test_green_repo.py tests/test_events.py tests/test_status.py tests/test_jobs.py tests/test_queue.py -q --tb=no`
    Expected: exit 0. Those tests passed. This command does not print coverage or CRAP. Coverage and CRAP stay on `marestail gate`. The orders in `tests/test_runner_flow.py` gain `suffix` after every `nice` except the two bad-nice tests, which stay `nice`. `test_readme_documents_run_priority` finds the nice paragraph after the new one. `test_readme_documents_junie` and `test_readme_documents_hermes` split on `marestail run tasks/001.md   # Claude (default)`, not on the new `--run-suffix` line. `test_sonar_setup`, `test_scanner_command`, `test_dotnet_scan_script`, `test_dotnet_scan_builds_project_when_solutions_are_ambiguous`, `test_database_names`, `test_build_database_defaults`, `test_start_postgres`, `test_ensure_helper_starts` and `test_down` stay as they are for an unset suffix, apart from `test_perf_parser_defaults_and_help` expecting the new down sentence.

```python
#!/usr/bin/env python3
import ast, contextlib, hashlib, io, json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
MSG = "MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got {0!r}"
FLAG = "argument --run-suffix: must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'\n"
NEED = "sonar prune needs a run suffix: pass --run-suffix or set MARESTAIL_RUN_SUFFIX; it never deletes the unsuffixed project"
MISSING = "marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set"
CLASH = "--focus cannot be combined with --scope all\n"
ROW = "| `MARESTAIL_RUN_SUFFIX` | per-run suffix (`[a-z0-9-]{1,32}`) for the names a run shares with the machine: Sonar project `<key>_<suffix>`, perf containers `…_<suffix>` and their ports; `--run-suffix` on `run`, `gate` and `sonar` sets it |"
USE_RUN = "marestail run tasks/001.md --run-suffix a1   # a second run of this repo on this machine, in its own checkout"
USE_PRUNE = "marestail sonar prune --run-suffix a1   # delete the Sonar project that suffix created"
PARAGRAPH = (
    "`MARESTAIL_RUN_SUFFIX`, or `--run-suffix` on `run`, `gate` and `sonar`, lets two runs of one repo share a machine, "
    "each in its own checkout, without editing a tracked file. The suffix is added to the Sonar project key (`<key>_<suffix>`) "
    "and name (`<name> (<suffix>)`), and to the perf containers and their ports. It is not added to the SonarQube server, the perf "
    "volume, or anything under `.marestail/`. The gate creates the suffixed project on its first analysis, and `marestail sonar prune` deletes that project."
)
DB_SENTENCE = (
    "With `MARESTAIL_RUN_SUFFIX` set, the containers end in `_<suffix>`, the ports move by a per-suffix offset unless "
    "`MARESTAIL_PERF_DB_PORT` is set, `down` removes only that run's containers, and goldens in the shared volume are still reused; "
    "two suffixes that hash to the same port fail at `docker run` because the port is already allocated, and `MARESTAIL_PERF_DB_PORT` is the way out."
)
DOWN_HELP = "remove this run's performance database containers (every one when MARESTAIL_RUN_SUFFIX is unset), keeping the volume"
FAIL = (
    "[FAIL] sonar          no project key  (0.0s)\n"
    "       marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set\n"
    "\n"
    "GATE FAILED: sonar"
)

def die(msg):
    sys.stderr.write(msg + "\n")
    raise SystemExit(1)

def check(cond, msg):
    if not cond:
        die(msg)

def env_set(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value

def clear_suffix():
    for name in ("MARESTAIL_RUN_SUFFIX", "MARESTAIL_NICE", "MARESTAIL_JOBS", "MARESTAIL_PERF_DB_PORT", "MARESTAIL_PERF_DB_PREFIX", "MARESTAIL_PERF_DB_VOLUME", "MARESTAIL_PERF_DB_HOME", "MARESTAIL_PERF_DB_ROWS"):
        os.environ.pop(name, None)

def raised(fn):
    try:
        fn()
    except SystemExit as exc:
        return str(exc)
    die("no SystemExit")

def run_cli(args, extra=None, cwd=None, stdin=""):
    env = os.environ.copy()
    for name in ("MARESTAIL_RUN_SUFFIX", "MARESTAIL_NICE", "MARESTAIL_JOBS"):
        env.pop(name, None)
    if extra:
        env.update(extra)
    return subprocess.run([sys.executable, "-u", str(ROOT / "marestail" / "cli.py"), *args], cwd=cwd or ROOT, env=env, input=stdin, capture_output=True, text=True, timeout=120)

def capture(fn):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            value = fn()
        except SystemExit as exc:
            value = exc
    return value, out.getvalue(), err.getvalue()

def repo(name="repo", toml='[git]\nbase = "main"\n'):
    root = Path(tempfile.mkdtemp()) / name
    root.mkdir()
    (root / "marestail.toml").write_text(toml)
    return root

def task(root):
    folder = root / "tasks"
    folder.mkdir(exist_ok=True)
    path = folder / "t.md"
    path.write_text("+++\ndepends = []\n+++\n# t\n")
    return path

if sys.argv[1:] == ["rules"]:
    import marestail.run_suffix as run_suffix
    clear_suffix()
    check(run_suffix.ENV == "MARESTAIL_RUN_SUFFIX", "ENV")
    check(run_suffix.current() is None and run_suffix.named("k") == "k" and run_suffix.titled("K") == "K", "unset")
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    check(run_suffix.current() == "a1", "reread")
    clear_suffix()
    check(run_suffix.current() is None, "deleted")
    valid = [("a1", "k_a1", "K (a1)"), ("a", "k_a", "K (a)"), ("0", "k_0", "K (0)"), ("-", "k_-", "K (-)"), ("a-1-b", "k_a-1-b", "K (a-1-b)"), ("a" * 32, "k_" + "a" * 32, "K (" + "a" * 32 + ")")]
    for suffix, named, titled in valid:
        env_set("MARESTAIL_RUN_SUFFIX", suffix)
        check(run_suffix.current() == suffix and run_suffix.argument(suffix) == suffix, suffix)
        check(run_suffix.named("k") == named and run_suffix.titled("K") == titled, named)
    for raw in ("", "A1", "a_1", "a.1", "a/b", "a 1", "a1\n", "é", "a" * 33):
        env_set("MARESTAIL_RUN_SUFFIX", raw)
        message = raised(run_suffix.current)
        check(message == MSG.format(raw) and "\n" not in message, repr(message))
        die_arg = False
        try:
            run_suffix.argument(raw)
            die_arg = True
        except Exception as exc:
            error = exc
        check(not die_arg and type(error).__name__ == "ArgumentTypeError", type(error).__name__)
        check(str(error) == f"must be 1 to 32 characters from a-z, 0-9 and -, got {raw!r}", str(error))
        check("MARESTAIL_RUN_SUFFIX must be" not in str(error), "prefix")
    check("\\n" in MSG.format("a1\n"), "escaped newline")
    tree = ast.parse((ROOT / "marestail" / "run_suffix.py").read_text())
    mods = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module)
    check(set(mods) == {"os", "re"}, str(mods))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "argument")
    inner = [node for node in ast.walk(fn) if isinstance(node, (ast.Import, ast.ImportFrom))]
    check(any(getattr(node, "module", None) == "argparse" or any(alias.name == "argparse" for alias in getattr(node, "names", [])) for node in inner), "argparse")
    print("rules ok")
elif sys.argv[1:] == ["cli"]:
    from marestail import cli, runner
    calls = []
    def pipeline(*args):
        calls.append((args, os.environ.get("MARESTAIL_RUN_SUFFIX")))
        return 0
    runner.run_pipeline = pipeline
    env_set("MARESTAIL_RUN_SUFFIX", "zz")
    argv = ["run", "t.md", "--from", "coder", "--to", "qa", "--auto", "--model", "m", "--retries", "2", "--agent", "grok", "--effort", "high", "--scope", "hard", "--focus", "a", "--focus", "b", "--run-suffix", "a1"]
    check(cli.main(argv) == 0, "run flag")
    check(calls[-1][0] == (Path("t.md"), "coder", "qa", True, "m", 2, "grok", "high", "hard", ["a", "b"]) and calls[-1][1] == "a1", "argv")
    env_set("MARESTAIL_RUN_SUFFIX", "zz")
    check(cli.main(["run", "t.md"]) == 0 and calls[-1][1] == "zz", "kept")
    for args in (["run", "t.md", "--run-suffix", "A1"], ["gate", "--run-suffix", "A1"], ["sonar", "setup", "--run-suffix", "A1"]):
        proc = run_cli(args)
        check(proc.returncode == 2 and proc.stderr.endswith(FLAG) and proc.stdout == "", args[0])
    for command, present in (("run", True), ("gate", True), ("sonar", True), ("perf", False), ("queue", False), ("status", False)):
        proc = run_cli([command, "--help"])
        check(proc.returncode == 0 and (("--run-suffix" in proc.stdout) is present), command)
    check(cli.SONAR_ACTIONS == ("up", "down", "setup", "prune"), str(cli.SONAR_ACTIONS))
    proc = run_cli(["sonar", "nope"])
    check(proc.returncode == 2 and "invalid choice: 'nope' (choose from up, down, setup, prune)" in proc.stderr, proc.stderr)
    print("cli ok")
elif sys.argv[1:] == ["order"]:
    from marestail import jobs, nice, runner, task_file
    import marestail.run_suffix as run_suffix
    root = repo()
    path = task(root)
    os.chdir(root)
    order = []
    real_current = run_suffix.current
    def current():
        order.append("suffix")
        return real_current()
    run_suffix.current = current
    if getattr(runner, "current", None) is real_current:
        runner.current = current
    if getattr(getattr(runner, "run_suffix", None), "current", None) is real_current:
        runner.run_suffix.current = current
    def apply(loaded):
        order.append("nice")
        nice.level(loaded)
    nice.apply = apply
    real_jobs = jobs.run_jobs
    def run_jobs(loaded):
        order.append("jobs")
        return real_jobs(loaded)
    jobs.run_jobs = run_jobs
    real_read = task_file.read
    def read(item):
        order.append("read")
        return real_read(item)
    task_file.read = read
    real_pick = runner.pick_model
    def pick_model(*args):
        order.append("pick_model")
        return real_pick(*args)
    runner.pick_model = pick_model
    runner.run_steps = lambda state, steps, auto: 0
    clear_suffix()
    code, out, err = capture(lambda: runner.run_pipeline(path, None, None, True, "m", 0))
    check(code == 0 and order == ["nice", "suffix", "jobs", "read", "pick_model"], str(order))
    check("MARESTAIL_RUN_SUFFIX" not in out and err == "", out + err)
    events = root / ".marestail" / "runs" / "t" / "events.jsonl"
    line = next(json.loads(item) for item in events.read_text().splitlines() if json.loads(item)["event"] == "run-start")
    check("suffix" not in line, str(line))
    order.clear()
    clear_suffix()
    env_set("MARESTAIL_NICE", "high")
    env_set("MARESTAIL_RUN_SUFFIX", "A1")
    check(raised(lambda: runner.run_pipeline(path, None, None, True, "m", 0)) == "nice must be an integer 0-19, got 'high'", "nice")
    check(order == ["nice"], str(order))
    order.clear()
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "A1")
    exc, out, err = capture(lambda: runner.run_pipeline(path, None, None, True, "m", 0))
    check(isinstance(exc, SystemExit) and str(exc) == MSG.format("A1"), str(exc))
    check(order == ["nice", "suffix"] and "jobs" not in order and "read" not in order, str(order))
    check(out == "" and err == "", repr((out, err)))
    order.clear()
    env_set("MARESTAIL_JOBS", "0")
    exc, out, err = capture(lambda: runner.run_pipeline(path, None, None, True, "m", 0))
    check(isinstance(exc, SystemExit) and str(exc) == MSG.format("A1") and order == ["nice", "suffix"], str(order) + str(exc))
    order.clear()
    clear_suffix()
    env_set("MARESTAIL_JOBS", "0")
    check(raised(lambda: runner.run_pipeline(path, None, None, True, "m", 0)) == "MARESTAIL_JOBS must be a whole number 1 or more, got '0'", "jobs")
    check(order == ["nice", "suffix", "jobs"], str(order))
    os.chdir("/tmp")
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "A1")
    outside = raised(lambda: runner.run_pipeline(Path("t.md"), None, None, True, "m", 0))
    check(outside.startswith("no marestail.toml found above") and "must be 1 to 32" not in outside, outside)
    print("order ok")
elif sys.argv[1:] == ["gate"]:
    from marestail import cli
    from marestail.report import Result
    seen = {}
    class Ctx:
        scoped = False
        scope_name = "all"
        focus = set()
    def gates(*args, **kwargs):
        seen["env"] = os.environ.get("MARESTAIL_RUN_SUFFIX")
        seen["calls"] = seen.get("calls", 0) + 1
        return [Result("docs", True, "docs ok")], Ctx()
    cli.run_gates_with_context = gates
    root = repo()
    env_set("MARESTAIL_RUN_SUFFIX", "zz")
    os.chdir(root)
    check(cli.main(["gate", "--run-suffix", "a1", "--tier", "fast"]) == 0 and seen["env"] == "a1", seen.get("env"))
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "A1")
    calls = seen["calls"]
    check(raised(lambda: cli.main(["gate", "--tier", "fast"])) == MSG.format("A1") and seen["calls"] == calls, "fast")
    check(raised(lambda: cli.main(["gate", "--hook"])) == MSG.format("A1") and seen["calls"] == calls, "hook")
    proc = run_cli(["gate", "--hook"], {"MARESTAIL_RUN_SUFFIX": "A1"}, cwd=root, stdin="{}\n")
    check(proc.returncode == 1 and proc.stderr == MSG.format("A1") + "\n" and proc.stdout == "", proc.stderr)
    proc = run_cli(["gate", "--scope", "all", "--focus", "a.py"], {"MARESTAIL_RUN_SUFFIX": "A1"}, cwd=root)
    check(proc.returncode == 1 and proc.stderr == MSG.format("A1") + "\n" and "cannot be combined" not in proc.stderr, proc.stderr)
    proc = run_cli(["gate", "--scope", "all", "--focus", "a.py", "--run-suffix", "a1"], cwd=root)
    check(proc.returncode == 2 and proc.stderr == CLASH, proc.stderr)
    proc = run_cli(["gate", "--tier", "nope"], {"MARESTAIL_RUN_SUFFIX": "A1"}, cwd=root)
    check(proc.returncode == 2 and "invalid choice: 'nope'" in proc.stderr and "must be 1 to 32" not in proc.stderr, proc.stderr)
    proc = run_cli(["gate"], {"MARESTAIL_RUN_SUFFIX": "A1"}, cwd=Path("/tmp"))
    check(proc.returncode == 1 and proc.stderr == MSG.format("A1") + "\n" and "marestail.toml" not in proc.stderr, proc.stderr)
    print("gate ok")
elif sys.argv[1:] == ["sonar"]:
    from marestail import cli
    from marestail.sonar import client, setup
    calls = []
    real_prune = setup.prune
    def fake_setup(*args):
        calls.append(("setup", args))
    def fake_prune(key):
        calls.append(("prune", key))
    def fake_up():
        calls.append(("up", ()))
    def fake_down():
        calls.append(("down", ()))
    setup.setup = fake_setup
    setup.prune = fake_prune
    setup.up = fake_up
    setup.down = fake_down
    root = repo("repo", '[sonar]\nproject_key = "key"\n')
    os.chdir(root)
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    check(cli.main(["sonar", "setup"]) == 0 and calls[-1] == ("setup", ("key_a1", "repo (a1)")), str(calls[-1]))
    (root / "marestail.toml").write_text('[sonar]\nproject_key = "key"\nproject_name = "N"\n')
    check(cli.main(["sonar", "setup"]) == 0 and calls[-1] == ("setup", ("key_a1", "N (a1)")), str(calls[-1]))
    env_set("MARESTAIL_RUN_SUFFIX", "zz")
    (root / "marestail.toml").write_text('[sonar]\nproject_key = "key"\n')
    check(cli.main(["sonar", "--run-suffix", "a1", "setup"]) == 0 and calls[-1] == ("setup", ("key_a1", "repo (a1)")), "flag")
    (root / "marestail.toml").write_text("[sonar]\n")
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    before = len(calls)
    check(raised(lambda: cli.main(["sonar", "setup"])) == MISSING and len(calls) == before, "missing")
    clear_suffix()
    (root / "marestail.toml").write_text('[sonar]\nproject_key = "key"\n')
    check(cli.main(["sonar", "setup"]) == 0 and calls[-1] == ("setup", ("key", "repo")), "plain key")
    (root / "marestail.toml").write_text("[sonar]\nproject_name = 'N'\n")
    check(cli.main(["sonar", "setup"]) == 0 and calls[-1] == ("setup", (None, "N")), "plain name")
    os.chdir(ROOT)
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    check(cli.main(["sonar", "setup"]) == 0 and calls[-1] == ("setup", ("marestail_a1", "marestail (a1)")), str(calls[-1]))
    check(f"sonar ready: {setup.DEFAULT_URL}/dashboard?id=marestail_a1" == "sonar ready: http://localhost:9000/dashboard?id=marestail_a1", setup.DEFAULT_URL)
    os.chdir(root)
    for action, bad in (("up", False), ("down", False), ("up", True), ("down", True)):
        env_set("MARESTAIL_RUN_SUFFIX", "A1" if bad else "a1")
        check(cli.main(["sonar", action]) == 0 and calls[-1] == (action, ()), action)
        check(cli.main(["sonar", "--run-suffix", "a1", action]) == 0 and calls[-1] == (action, ()), action + " flag")
    clear_suffix()
    (root / "marestail.toml").write_text('[sonar]\nproject_key = "key"\n')
    before = len(calls)
    check(raised(lambda: cli.main(["sonar", "prune"])) == NEED and len(calls) == before, "needs")
    env_set("MARESTAIL_RUN_SUFFIX", "A1")
    check(raised(lambda: cli.main(["sonar", "prune"])) == MSG.format("A1"), "bad prune")
    env_set("MARESTAIL_RUN_SUFFIX", "zz")
    check(cli.main(["sonar", "prune", "--run-suffix", "a1"]) == 0 and calls[-1] == ("prune", "key_a1"), "flag prune")
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "b2")
    check(cli.main(["sonar", "prune"]) == 0 and calls[-1] == ("prune", "key_b2"), "env prune")
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    (root / "marestail.toml").write_text("[sonar]\n")
    before = len(calls)
    check(raised(lambda: cli.main(["sonar", "prune"])) == MISSING and len(calls) == before, "prune key")
    setup.prune = real_prune
    hits = {"up": 0, "down": 0}
    setup.up = lambda: hits.__setitem__("up", hits["up"] + 1)
    setup.down = lambda: hits.__setitem__("down", hits["down"] + 1)
    def exercise(search):
        gets, posts = [], []
        class Client:
            def __init__(self, url, token, password=None):
                pass
            def get(self, path, **params):
                gets.append((path, params))
                if search == 401:
                    raise SystemExit("sonar GET api/projects/search?projects=key_a1: 401 no")
                return search
            def post(self, path, **params):
                posts.append((path, params))
                return {}
        setup.Client = Client
        client.Client = Client
        return gets, posts
    env_set("MARESTAIL_RUN_SUFFIX", "A1")
    setup.credentials = lambda: None
    client.credentials = lambda: None
    gets, posts = exercise({"components": []})
    check(raised(lambda: setup.prune("key_a1")) == "sonar is not set up; run: marestail sonar setup" and gets == [] and posts == [], "creds")
    setup.credentials = lambda: {"url": "http://localhost:9000", "token": "tok"}
    client.credentials = setup.credentials
    gets, posts = exercise({"components": [{"key": "key_a1"}]})
    code, out, err = capture(lambda: setup.prune("key_a1"))
    check(code is None and gets == [("api/projects/search", {"projects": "key_a1"})] and posts == [("api/projects/delete", {"project": "key_a1"})], str((gets, posts)))
    check(out == "deleted sonar project key_a1\n" and err == "", repr(out))
    gets, posts = exercise({"components": []})
    code, out, err = capture(lambda: setup.prune("key_a1"))
    check(code is None and posts == [] and out == "no sonar project key_a1\n", repr(out))
    gets, posts = exercise(401)
    check(raised(lambda: setup.prune("key_a1")) == "sonar GET api/projects/search?projects=key_a1: 401 no", "401")
    check(gets == [("api/projects/search", {"projects": "key_a1"})] and posts == [], str(gets))
    check(hits == {"up": 0, "down": 0}, str(hits))
    check(setup.DEFAULT_URL == "http://localhost:9000", setup.DEFAULT_URL)
    compose = setup.COMPOSE.read_text()
    check("container_name: marestail-sonarqube" in compose and '"9000:9000"' in compose, compose)
    print("sonar ok")
elif sys.argv[1:] == ["scan"]:
    from marestail.gates import sonar
    from marestail import dotnet
    from marestail.report import Result, render
    from tests.conftest import make_context
    CREDS = {"url": "http://sonar:9000", "token": "tok"}
    def names(args):
        return [item for item in args if item.startswith("-Dsonar.projectName=")]
    def scrub(args):
        out = []
        for item in args:
            if item.startswith("-Dsonar.projectName="):
                continue
            out.append("KEY" if item.startswith("-Dsonar.projectKey=") else item)
        return out
    tmp = Path(tempfile.mkdtemp())
    clear_suffix()
    plain = sonar.scanner_command(make_context(tmp), CREDS, "proj")
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    suffixed = sonar.scanner_command(make_context(tmp), CREDS, "proj")
    key_at = suffixed.index("-Dsonar.projectKey=proj_a1")
    check(suffixed[key_at + 1] == "-Dsonar.projectName=proj (a1)" and scrub(plain) == scrub(suffixed), suffixed[key_at:key_at + 2])
    check(names(plain) == [], str(names(plain)))
    (tmp / "sonar-project.properties").write_text("sonar.projectName=Props\n")
    clear_suffix()
    check(names(sonar.scanner_command(make_context(tmp), CREDS, "proj")) == [], "unset props")
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    props = sonar.scanner_command(make_context(tmp), CREDS, "proj")
    check(props[props.index("-Dsonar.projectKey=proj_a1") + 1] == "-Dsonar.projectName=Props (a1)", "props")
    nice = sonar.scanner_command(make_context(tmp, {"sonar": {"project_name": "Nice"}}), CREDS, "proj")
    check(nice[nice.index("-Dsonar.projectKey=proj_a1") + 1] == "-Dsonar.projectName=Nice (a1)", "nice")
    made, calls = [], []
    def factory(url, token, password=None):
        client = Fake(healthy)
        made.append(client)
        check((url, token) == ("http://sonar:9000", "tok"), url)
        return client
    class Fake:
        def __init__(self, respond):
            self.respond, self.gets, self.posts = respond, [], []
        def get(self, path, **params):
            self.gets.append((path, params))
            return self.respond(path, params)
        def post(self, path, **params):
            self.posts.append((path, params))
            return {}
    def healthy(path, params):
        replies = {"api/ce/task": {"task": {"status": "SUCCESS"}}, "api/qualitygates/project_status": {"projectStatus": {"status": "OK"}}, "api/measures/component": {"component": {"measures": [{"metric": "coverage", "value": "100"}]}}}
        return replies.get(path, {})
    def fake_run(command, cwd=None, **opts):
        calls.append(list(command))
        if command[0] != "git":
            work = Path(cwd) / ".marestail" / "scannerwork"
            work.mkdir(parents=True, exist_ok=True)
            (work / "report-task.txt").write_text("ceTaskId=T1\n")
        return (1, "") if command[0] == "git" else (0, "")
    sonar.credentials = lambda: dict(CREDS)
    sonar.Client = factory
    sonar.run = fake_run
    gate_root = Path(tempfile.mkdtemp())
    result = sonar.run_gate(make_context(gate_root, {"sonar": {"project_key": "proj"}}))
    docker = next(command for command in calls if command[0] == "docker")
    check(result.ok and result.summary == "sonar clean" and "proj_a1_a1" not in " ".join(docker), "clean")
    check("-Dsonar.projectKey=proj_a1" in docker, docker)
    named_params = [value for client in made for path, params in client.gets for name, value in params.items() if name in {"projectKey", "componentKeys", "project", "component"}]
    check(named_params and set(named_params) == {"proj_a1"}, str(named_params))
    check(("api/ce/task", {"id": "T1"}) in made[0].gets and made[0].posts == [], str(made[0].gets))
    calls.clear()
    made.clear()
    bare = Path(tempfile.mkdtemp())
    missing = sonar.run_gate(make_context(bare, {}))
    check((missing.gate, missing.ok, missing.summary, missing.findings, missing.seconds) == ("sonar", False, "no project key", [MISSING], 0.0), str(missing))
    check(calls == [] and made == [], "scanned anyway")
    check(render([missing]) == FAIL, render([missing]))
    sonar.credentials = lambda: None
    unset = sonar.run_gate(make_context(bare, {}))
    check((unset.gate, unset.ok, unset.summary, unset.findings) == ("sonar", False, "not set up", ["run: marestail sonar setup"]), str(unset))
    def dotnet_text(root, raw):
        (root / ".marestail" / "dotnet-tools" / "dotnet-sonarscanner").mkdir(parents=True)
        dotnet.projects = lambda ctx: (root / "App.csproj", root / "App.csproj", None)
        dotnet.dotnet = lambda *args, **kwargs: (0, "scanned")
        sonar.dotnet_scan(make_context(root, raw), CREDS, "proj")
        return (root / ".marestail" / "sonar-dotnet.sh").read_text()
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    text = dotnet_text(Path(tempfile.mkdtemp()), {})
    check('/k:"proj_a1"' in text and '/n:"proj (a1)"' in text and "proj_a1_a1" not in text and "proj_a1 (a1)" not in text, text)
    text = dotnet_text(Path(tempfile.mkdtemp()), {"sonar": {"project_name": "Nice"}})
    check('/k:"proj_a1"' in text and '/n:"Nice (a1)"' in text, text)
    clear_suffix()
    text = dotnet_text(Path(tempfile.mkdtemp()), {"sonar": {"project_name": "Nice"}})
    check('/k:"proj"' in text and '/n:"Nice"' in text and "(a1)" not in text, text)
    text = dotnet_text(Path(tempfile.mkdtemp()), {})
    check('/n:"proj"' in text and "(a1)" not in text, text)
    import tomllib
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    with (ROOT / "marestail.toml").open("rb") as handle:
        raw = tomllib.load(handle)
    green = sonar.scanner_command(make_context(ROOT, raw), CREDS, raw["sonar"]["project_key"])
    at = green.index("-Dsonar.projectKey=marestail_a1")
    check(green[at + 1] == "-Dsonar.projectName=marestail (a1)", green[at:at + 2])
    print("scan ok")
elif sys.argv[1:] == ["perf"]:
    from marestail.config import Config
    from marestail.perf import db, trees
    from marestail.gates import qa
    from marestail.gates import _serve
    from tests.test_perf_db import make_db
    tmp = Path(tempfile.mkdtemp())
    plain = make_db(tmp)
    key = plain.repo_key
    check(plain.suffix is None and plain.helper == "mp-files" and plain.tree_container("head") == f"mp-db-{key}-head", "plain names")
    check(plain.golden_container("g") == "mp-golden-g" and plain.work_dir("head") == f"/perf/work/{key}-head", "plain work")
    check([plain.tree_port(name) for name in ("head", "baseline", "pre-marestail")] == [55432, 55433, 55434], "plain ports")
    suffixed = make_db(tmp, suffix="a1")
    check(suffixed.helper == "mp-files_a1" and suffixed.tree_container("head") == f"mp-db-{key}-head_a1", "names")
    check(suffixed.golden_container("g") == "mp-golden-g_a1" and suffixed.work_dir("head") == f"/perf/work/{key}-head_a1" and suffixed.volume == "vol", "work")
    clear_suffix()
    home = tmp / "home-default"
    real_home = Path.home
    Path.home = lambda: home
    try:
        database = db.build_database(Config(root=tmp, raw={}), {"migrate": "m"}, 5, "rs", "img", "is")
    finally:
        Path.home = real_home
    check(database == db.Database(tmp, "m", "DATABASE_URL", 5, "rs", [], db.DEFAULT_SKIP_TABLES, 50.0, "img", "is", 55432, "marestail-perf-", "marestail-perf-pgdata", home / ".config" / "marestail"), "defaults")
    check(database.suffix is None, "default suffix")
    def built(suffix, section=None, port=None):
        clear_suffix()
        env_set("MARESTAIL_RUN_SUFFIX", suffix)
        env_set("MARESTAIL_PERF_DB_PORT", port)
        return db.build_database(Config(root=tmp, raw={}), section or {"migrate": "m"}, 5, "rs", "img", "is")
    check(built("a1").port == 57376 and built("a1").suffix == "a1", "a1")
    check(built("b2").port == 58003 and built("slot-1").port == 56743, "slots")
    check(built("a1", {"migrate": "m", "port": "6000"}).port == 7944, "6000")
    check(built("a1", {"migrate": "m", "port": "6000"}, "7000").port == 7000, "7000")
    check(built("a1", {"migrate": "m", "port": "65535"}).port == 67479, "high")
    ports = [built("a1").tree_port(name) for name in ("head", "baseline", "pre-marestail")]
    check(ports == [57376, 57377, 57378], str(ports))
    try:
        plain.tree_port("control")
        die("control")
    except KeyError:
        pass
    tree = trees.Tree("head", "abc", tmp / "head")
    check(db.golden_name(plain, tree) == db.golden_name(suffixed, tree), "golden")
    def install(reply):
        recorder = []
        def run(command, cwd=None, **opts):
            recorder.append(list(command))
            return reply(list(command))
        db.run = run
        return recorder
    def reply_ok(command):
        if "State.Running" in " ".join(command):
            return 1, ""
        return 0, ""
    calls = install(reply_ok)
    db.start_postgres(suffixed, suffixed.tree_container("head"), "/perf/x", 57376, 9)
    started = next(command for command in calls if command[1:3] == ["run", "-d"])
    at = started.index("--name")
    check(started[at + 1] == suffixed.tree_container("head") and started[at + 2:at + 4] == ["--label", "marestail.run-suffix=a1"], started)
    calls = install(reply_ok)
    db.ensure_helper(suffixed)
    started = next(command for command in calls if command[1:3] == ["run", "-d"])
    at = started.index("--name")
    check(started[at + 1] == "mp-files_a1" and started[at + 2:at + 4] == ["--label", "marestail.run-suffix=a1"], started)
    calls = install(reply_ok)
    db.start_postgres(plain, "box", "/perf/x", None, 9)
    check(not any("--label" in command for command in calls), calls)
    calls = install(lambda command: (1, "") if "State.Running" in " ".join(command) else (0, ""))
    db.ensure_helper(plain)
    check(not any("--label" in command for command in calls), "helper label")
    def collide(command):
        if command[1:3] == ["run", "-d"]:
            return 125, "port is already allocated\n"
        return 0, ""
    install(collide)
    try:
        db.start_postgres(suffixed, suffixed.tree_container("head"), "/perf/x", 57376, 9)
        die("no collision")
    except db.DatabaseError as exc:
        check(str(exc) == f"starting {suffixed.tree_container('head')} failed (exit 125): port is already allocated", str(exc))
    def listing(text):
        def reply(command):
            if command[1] == "ps":
                return 0, text
            return 0, ""
        return reply
    calls = install(listing("mp-files_a1\nmp-db-abc-head_a1\n"))
    check(db.down(suffixed) == ["mp-files_a1", "mp-db-abc-head_a1"], "down a1")
    check(calls[0] == ["docker", "ps", "-a", "--filter", "label=marestail.run-suffix=a1", "--format", "{{.Names}}"], calls[0])
    check(calls[1:] == [["docker", "rm", "-f", "-v", "mp-files_a1"], ["docker", "rm", "-f", "-v", "mp-db-abc-head_a1"]], calls[1:])
    calls = install(listing("mp-files\nmp-files_a1\nother\n"))
    check(db.down(plain) == ["mp-files", "mp-files_a1"], "down none")
    check(calls[0] == ["docker", "ps", "-a", "--format", "{{.Names}}"], calls[0])
    calls = install(listing("mp-files\nother\nmp-db-1\n"))
    check(db.down(plain) == ["mp-files", "mp-db-1"] and calls[1:] == [["docker", "rm", "-f", "-v", "mp-files"], ["docker", "rm", "-f", "-v", "mp-db-1"]], "old down")
    home = tmp / "pw"
    home.mkdir()
    (home / "perf-db.json").write_text(json.dumps({"password": "pw24"}))
    project = repo("perf", '[perf.db]\nmigrate = "m"\nimage = "postgres:16"\nrows = 0\n')
    os.chdir(project)
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    env_set("MARESTAIL_PERF_DB_HOME", str(home))
    code, out, err = capture(lambda: db.command("url", "head", False))
    check(code == 0 and out == "postgresql://postgres:pw24@127.0.0.1:57376/bench\n" and err == "", repr(out))
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    env_set("MARESTAIL_PERF_DB_PREFIX", "mp-")
    env_set("MARESTAIL_PERF_DB_HOME", str(home))
    def running(command):
        if "State.Running" in " ".join(command):
            return 0, "true\n"
        return 0, ""
    calls = install(running)
    session = trees.Session("t", [trees.Tree("head", "abc", project / "head"), trees.Tree("baseline", "abc", project / "baseline")], image="postgres:16")
    db.release(Config(root=project, raw={"perf": {"db": {"migrate": "m", "image": "postgres:16", "rows": 0}}}), session)
    release_key = hashlib.sha256(str(project).encode()).hexdigest()[:12]
    check(calls[0] == ["docker", "rm", "-f", "-v", f"mp-db-{release_key}-head_a1"], calls[0])
    check(calls[1] == ["docker", "rm", "-f", "-v", f"mp-db-{release_key}-baseline_a1"], calls[1])
    check(calls[3] == ["docker", "exec", "mp-files_a1", "rm", "-rf", f"/perf/work/{release_key}-head_a1", f"/perf/work/{release_key}-baseline_a1"], calls[3])
    bare = repo("bare", "[perf]\n")
    os.chdir(bare)
    env_set("MARESTAIL_RUN_SUFFIX", "A1")
    code, out, err = capture(lambda: db.command("status", None, False))
    check(code == 2 and err == "configure [perf.db] migrate in marestail.toml\n", repr(err))
    from marestail.perf import samples
    code, out, err = capture(lambda: samples.run_command("perf/bench_x", "head", 1, False))
    check(code == 2 and err == "no perf run in progress\n", repr(err))
    configured = repo("db", '[perf.db]\nmigrate = "m"\nimage = "postgres:16"\nrows = 0\n')
    os.chdir(configured)
    check(raised(lambda: db.command("url", "head", False)) == MSG.format("A1"), "db reads")
    bench = configured / "perf" / "bench_x"
    bench.parent.mkdir()
    bench.write_text("#!/bin/sh\n")
    bench.chmod(0o755)
    (configured / ".marestail" / "perf").mkdir(parents=True)
    (configured / ".marestail" / "perf" / "trees.json").write_text(json.dumps({"trees": [{"tree": "head", "sha": "abc", "path": str(configured)}]}))
    check(raised(lambda: samples.run_command("perf/bench_x", "head", 1, True)) == MSG.format("A1"), "run --db")
    seen = []
    def popen(argv, **kwargs):
        seen.append(argv)
        class Proc:
            pid = 1
        return Proc()
    db.golden_state = lambda database, name: "missing"
    db.subprocess.Popen = popen
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    db.start_build(suffixed, tree)
    check(seen[0][-6:] == ["perf", "db", "golden", "--tree", "head", "--wait"] and "--run-suffix" not in seen[0], seen[0])
    import tempfile as tempfile_module
    prefixes = []
    real_mkdtemp = tempfile_module.mkdtemp
    def mkdtemp(prefix):
        prefixes.append(prefix)
        return real_mkdtemp()
    tempfile_module.mkdtemp = mkdtemp
    trees.add_worktree = lambda *args, **kwargs: (0, "")
    trees.add_tree(Config(root=configured, raw={}), trees.Session("t"), "baseline", "abc")
    check(prefixes == ["marestail-perf-"], prefixes)
    calls = []
    @contextlib.contextmanager
    def ready(*args):
        calls.append(args)
        yield None, 3400
    _serve.ready_app = ready
    qa.run = lambda *args, **kwargs: (0, "")
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    from tests.conftest import make_context
    qa.run_gate(make_context(Path(tempfile.mkdtemp()), {"qa": {"cmd": "true", "start": "yarn dev"}}))
    check(calls[0][2] == 3400, calls[0][2])
    proc = run_cli(["perf", "db", "--help"])
    check(DOWN_HELP in proc.stdout, proc.stdout)
    print("perf ok")
elif sys.argv[1:] == ["quiet"]:
    from marestail.events import _FIELDS
    from marestail.queue import _ChildOptions, _build_child_argv, _build_child_env
    from marestail.status import _KEYS
    from marestail.task_file import TaskFile
    empty = Path(tempfile.mkdtemp())
    commands = [
        ["tasks", "check", "tasks/024-two-runs-share-a-machine.md"],
        ["status", str(empty)],
        ["abandon", "--help"],
        ["watch", "--help"],
        ["dash", "--help"],
        ["visual", "--help"],
        ["remote", "--help"],
        ["graph"],
        ["depth"],
        ["route", "--help"],
        ["install", "--help"],
        ["queue", "--help"],
    ]
    for args in commands:
        proc = run_cli(args, {"MARESTAIL_RUN_SUFFIX": "A1"})
        check(MSG.format("A1") not in proc.stderr and MSG.format("A1") not in proc.stdout, args[0] + proc.stderr)
        if args[0] in {"graph", "depth"}:
            check(proc.returncode == 0, args[0])
    root = repo()
    item = TaskFile("t", root / "tasks" / "t.md", (), None, "", True)
    (root / "tasks").mkdir()
    argv = _build_child_argv(item, root, None, _ChildOptions(None, None, None, None, [], None, None))
    check("run" in argv and "--run-suffix" not in argv, argv)
    clear_suffix()
    env_set("MARESTAIL_RUN_SUFFIX", "a1")
    check(_build_child_env().get("MARESTAIL_RUN_SUFFIX") == "a1", "child env")
    clear_suffix()
    check("MARESTAIL_RUN_SUFFIX" not in _build_child_env(), "child unset")
    check("suffix" not in _FIELDS["run-start"] and "suffix" not in _KEYS, "keys")
    print("quiet ok")
elif sys.argv[1:] == ["docs"]:
    import ast as ast_mod
    text = (ROOT / "README.md").read_text()
    lines = text.splitlines()
    task_at = next(index for index, line in enumerate(lines) if line.startswith("| `MARESTAIL_TASK`"))
    check(lines[task_at + 1] == ROW and lines[task_at + 2].startswith("| `MARESTAIL_QA_CMD_TIMEOUT`"), lines[task_at + 1])
    setup_at = lines.index("marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail")
    check(lines[setup_at + 1] == USE_RUN and lines[setup_at + 2] == USE_PRUNE, lines[setup_at + 1])
    check(lines[setup_at + 3].startswith("marestail gate"), lines[setup_at + 3])
    after = text.split("## Use", 1)[1].split("```", 2)[2].lstrip("\n")
    check(after.startswith(PARAGRAPH), after.split("\n\n", 1)[0])
    rest = after[len(PARAGRAPH):].lstrip("\n")
    check(rest.startswith("`marestail run` starts at nice 19"), rest.split("\n\n", 1)[0][:80])
    check("The password is generated into `~/.config/marestail/perf-db.json`. " + DB_SENTENCE in text, "db sentence")
    imported = (ROOT / ".importlinter").read_text()
    layer = next(line for line in imported.splitlines() if "marestail.report" in line and "marestail._location" in line)
    check("marestail.run_suffix" in layer, layer)
    jobs_layer = next(line for line in imported.splitlines() if "marestail.jobs" in line)
    check("marestail.run_suffix" not in jobs_layer, jobs_layer)
    foundations = imported.split("foundations-import-nothing-above", 1)[1].split("[importlinter:contract:", 1)[0]
    check("marestail.run_suffix" in foundations, "foundations")
    check(imported.count("[importlinter:contract:") == 38, str(imported.count("[importlinter:contract:")))
    hermetic = (ROOT / "tests" / "conftest.py").read_text().split("def hermetic", 1)[1].split("\ndef ", 1)[0]
    check('delenv("MARESTAIL_JOBS", raising=False)' in hermetic and 'delenv("MARESTAIL_RUN_SUFFIX", raising=False)' in hermetic, "conftest")
    setup_tree = ast_mod.parse((ROOT / "marestail" / "sonar" / "setup.py").read_text())
    imported_names = []
    for node in ast_mod.walk(setup_tree):
        if isinstance(node, ast_mod.ImportFrom):
            imported_names.append(node.module or "")
        elif isinstance(node, ast_mod.Import):
            imported_names.extend(alias.name for alias in node.names)
    check(not any("run_suffix" in name for name in imported_names), str(imported_names))
    print("docs ok")
else:
    die("usage: rules|cli|order|gate|sonar|scan|perf|quiet|docs")
```
