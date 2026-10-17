# QA: 023 — one knob for mutation workers

Run from the marestail-green root with the venv active. `GREEN` is that root. Save the script at the bottom as `/tmp/jobs-qa.py`. It stubs every mutation tool. `os.cpu_count` is 8 inside the script except where a step says otherwise.

## What you see

`marestail run` stops before it reads the task. stderr is one line and stdout is empty:

```
MARESTAIL_JOBS must be a whole number 1 or more, got '0'
```

```
[run] jobs must be a whole number 1 or more, got 0
```

`marestail gate --tier full --only py.mutation` does not stop the process. `render_one` is unchanged. Its head is the width-14 gate name (three pad spaces) plus the separating space, so four spaces, then the summary, then two spaces and the seconds to one decimal. `0.0` below stands for that measured value. The first stdout line is:

```
[FAIL] py.mutation    py.mutation crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'  (0.0s)
```

At most six traceback findings follow, each starting with seven spaces. Paths in those lines are not fixed. The last of them is:

```
       SystemExit: MARESTAIL_JOBS must be a whole number 1 or more, got '0'
```

Then a blank line and `GATE FAILED: py.mutation`. The summary is not `mutmut failed`.

With `ts.mutation` in the same run, the next line after those findings (no blank line) is exactly:

```
[ok  ] ts.mutation    skipped: no changed typescript sources  (0.0s)
```

Then a blank line and `GATE FAILED: py.mutation`. `ts.mutation` is the next mutation gate, and a skip does not read the bad value. The cap and the precedence print nothing.

## Procedure

1. `python3 /tmp/jobs-qa.py parse`
   Expected: exit 0, prints `parse ok`. `jobs.ENV` is `MARESTAIL_JOBS`. `1`, `3`, `"3"` and `"03"` are `1`, `3`, `3` and `3` for both `MARESTAIL_JOBS` and `[run] jobs`. Each rejected value raises SystemExit whose text is `<name> must be a whole number 1 or more, got <repr>`: `0`, `"0"`, `-1`, `"-1"`, `True`, `False`, `2.5`, `"2.5"`, `"four"`, `" 3"`, `[3]`, for both names. Nothing is written to stdout or stderr by `parse`.

2. `python3 /tmp/jobs-qa.py resolve`
   Expected: exit 0, prints `resolve ok`. `run_jobs` is `None` when nothing is set, `3` for `[run] jobs = 3`, `5` when `MARESTAIL_JOBS=5` beats that 3, and `3` when `MARESTAIL_JOBS=` is empty. `MARESTAIL_JOBS=0` raises `got '0'`. `[run] jobs = 0` raises `got 0`. `MARESTAIL_JOBS=5` with `[run] jobs = 0` still raises `got 0`. cpu 2 caps `MARESTAIL_JOBS=16` to 2. cpu `None` caps it to 1. `pick` returns 3, 8, 3, 4 and raw `0` in the feature's order. cpu 2 with `MARESTAIL_JOBS=16` returns 2. cpu 2 with no variable, no language key and `[run] jobs = 16` returns 2. cpu 2 with `mutation_jobs = 16` returns 16. `pick` raises the same two SystemExit texts as `run_jobs`, including `MARESTAIL_JOBS=5` with `[run] jobs = 0` even when `mutation_workers` is 8. Success and the cap write nothing.

3. `python3 /tmp/jobs-qa.py gates`
   Expected: exit 0, prints `gates ok`. cpu is 8. Builders still honour the count they are given.
   - py, no knob: `["<repo>/.venv/bin/mutmut", "run", "--max-children", "4"]`. `MARESTAIL_JOBS=3` and `[run] jobs = 3` each use `"3"`. `mutation_workers = 8` with `[run] jobs = 3` uses `"8"`. `MARESTAIL_JOBS=3` over that 8 uses `"3"`. The builder called with 13 still ends `"13"`.
   - ts BASE is `["npx", "stryker", "run", "--reporters", "json,progress", "--tempDirName", ".stryker-tmp", "--cleanTempDir", "always", "--dryRunTimeoutMinutes", "120"]`. No knob and no `mutation_concurrency`: the stub receives BASE and the offered workers are 0. The builder called with 10 is `BASE + ["--concurrency", "10"]`. Either source of 3: `BASE + ["--concurrency", "3"]`. Targets `a.ts` and `b.ts` with either source of 3: `BASE + ["--concurrency", "3", "--mutate", "a.ts,b.ts"]`. Jobs unset, `mutation_concurrency = "3"`, incremental, target `a.ts`: `BASE + ["--concurrency", "3", "--incremental", "--incrementalFile", "reports/stryker-incremental.json", "--mutate", "a.ts"]`. The same with `mutation_concurrency = "9"` and jobs unset uses `"9"`. Either source of 3 beats that 9 and stays before `--incremental` and `--mutate`.
   - ex, no knob: `watched("--no-filter", "--concurrency", "4")`. Either source of 3 ends `"3"`. `build(16)` stays `"16"`. `muex_concurrency = 0` with `[run] jobs = 3` is `watched("--no-filter")` with no `--concurrency`. `MARESTAIL_JOBS=3` over that 0 ends `"3"`.
   - rb, no knob: `["bundle", "exec", "mutant", "run", "Admin::Boss*", "Named*", "User*"]` after `["bundle", "info", "mutant"]`. Either source of 3 inserts `"--jobs", "3"` before `Admin::Boss*`.
   - cs `command` as in `test_command`, no knob, is `["stryker", "--skip-version-check", "--break-on-initial-test-failure", "--test-project", "<tests>", "--project", "App.csproj", "-O", "<out>", "-r", "json", "-r", "progress", "-m", "!**/Gen", "-m", "!**/Migr", "-m", "**/A.cs", "-m", "**/other/B.cs"]`. Either source of 3 inserts `"--concurrency", "3"` after `"progress"` and before the first `"-m"`. The surviving-mutants full run, no knob, ends `["-r", "progress", "-m", "!**/App/Gen"]`. Either source of 3 ends `["-r", "progress", "--concurrency", "3", "-m", "!**/App/Gen"]`.
   - rs, no knob: `["cargo", "mutants", "--output", "<repo>/.marestail", "--no-shuffle", "--colors", "never", "--jobs", "2"]`. Either source of 3 ends `"3"`. `mutation_args = ["--in-place"]` with no knob, with `MARESTAIL_JOBS=3` and with `[run] jobs = 3` is that list without `--jobs` and with `--in-place` at the end.
   - java, no knob: the `pit(out, "2")` list, and `build(16)` has `-Dthreads=16`. Either source of 3 uses `-Dthreads=3` locally and `build(16)` stays 16. `mutation_threads = 8` with `[run] jobs = 3` stays `-Dthreads=8`.

4. From a temporary git repo whose `marestail.toml` has `[git] base = "main"` and `[python] root = "."`, and whose `tasks/t.md` is `+++\ndepend = []\n+++\n# t\n`:
   `MARESTAIL_JOBS=0 "$GREEN/bin/marestail" run tasks/t.md --from critic --to critic --auto --retries 1`
   Expected: exit 1, stdout empty, stderr exactly `MARESTAIL_JOBS must be a whole number 1 or more, got '0'\n`. No `.marestail/runs` directory. The same command with `--model dandelion/route --agent grok` still has that stderr and does not contain `drop --agent`.
   With the variable unset and `[run] jobs = 0` in that toml, stderr is exactly `[run] jobs must be a whole number 1 or more, got 0\n`.

5. In that repo add `mutation_scope = "all"` under `[python]`, add `[ts] root = "."`, commit an empty tree on `main` so the base exists, and do not add a TypeScript file.
   `MARESTAIL_JOBS=0 "$GREEN/bin/marestail" gate --tier full --only py.mutation`
   Expected: exit 1, stderr empty. The first stdout line is the `[FAIL] py.mutation` line in the fence, with the measured seconds. Then traceback lines of seven spaces, a blank line, and `GATE FAILED: py.mutation`. The summary is not `mutmut failed`.
   `MARESTAIL_JOBS=0 "$GREEN/bin/marestail" gate --tier full --only py.mutation,ts.mutation`
   Expected: exit 1, stderr empty. The same `py.mutation` block, then exactly `[ok  ] ts.mutation    skipped: no changed typescript sources  (0.0s)`, then a blank line and `GATE FAILED: py.mutation`. `ts.mutation` ran. A skip does not read the bad value.
   `MARESTAIL_JOBS=0 "$GREEN/bin/marestail" gate --only docs` (no `--tier`, so the fast tier, and this repo has no `[docs]` section) exits 2. stderr is exactly `no gate ran: nothing in the fast tier matches --only and marestail.toml\n`. stdout is empty. Neither stream contains `MARESTAIL_JOBS`.

6. `python3 /tmp/jobs-qa.py erlang`
   Expected: exit 0, prints `erlang ok`. The pool class is `er_mutation.ThreadPoolExecutor`. It is constructed with `max_workers` 1 when jobs are unset and 3 for `MARESTAIL_JOBS=3` and for `[run] jobs = 3`, and `map` is given the five mutants. The reply is chosen from the command, not popped from a shared list. Five mutants of `src/a.erl`, comparison, `<` to `>=`: id 1 line 1 `erlc` exit 1 is `invalid`; id 2 line 2 `escript` exit 0 is `survived`; id 3 line 3 `escript` exit 1 is `killed`; id 4 line 4 `escript` exit 124 is `timeout`; id 5 line 5 `escript` exit 3 is `error`. For one worker and for three, `ok` is false, the summary is `1 of 4 mutants not killed (1 failed to compile)`, the findings are `src/a.erl:2 comparison mutant survived: < -> >=`, and `er-mutation.json` lists those statuses in that order. `Result.seconds` may differ.

7. `python3 /tmp/jobs-qa.py findings`
   Expected: exit 0, prints `findings ok`. 70 Rust survivors, `src/a.rs` lines 1–40 then `src/b.rs` lines 1–30, text `<path>:<line> add: swap survived`, once in that order and once after `random.Random(0).shuffle`. Both return the same 60 findings. The first is `src/a.rs:1 add: swap survived`, line 9 comes before line 10, the 60th is `src/b.rs:20 add: swap survived`, line 21 is absent, and the summary is `70 of 70 mutants not killed`. The same cut for a mutant `subject_results` report: `lib/a.rb` lines 1–40 `A#m` and `lib/b.rb` lines 1–30 `B#m`, first `lib/a.rb:1 A#m: 1 mutant survived`, line 9 before line 10, 60th `lib/b.rb:20 B#m: 1 mutant survived`, line 21 absent, summary `70 of 70 mutants not killed`.

8. `python3 /tmp/jobs-qa.py docs`
   Expected: exit 0, prints `docs ok`. Under `# [run]` and the existing `# nice = 19  # 0 leaves the process at the shell's priority; MARESTAIL_NICE overrides; children inherit` line, the next line is `# jobs = 4  # parallel workers for every mutation gate; MARESTAIL_JOBS wins over it`. The three keys are `# mutation_workers = 4  # wins over [run] jobs for mutmut`, `# mutation_jobs = 2  # wins over [run] jobs for cargo-mutants` and `# mutation_threads = 2  # wins over [run] jobs for PIT`. The paragraph after `Mutation testing always runs on the diff` names the variable, then the four keys and their flags (`--max-children`, muex `--concurrency`, cargo-mutants `--jobs`, `-Dthreads`), then `[run] jobs`, then the tool default, plus StrykerJS `--concurrency`, mutant `--jobs`, Stryker.NET `--concurrency`, the Erlang pool, the CPU cap, that 0 is an error, that the mutant set and the verdict do not change, the `--in-place` exception, and that a pom `<threads>` beats `-Dthreads` while `templates/java-pitest.xml` sets none. The `MARESTAIL_JOBS` cell is `parallel workers for every mutation gate (1 or more, capped at the CPU count; empty means unset); wins over [run] jobs and the per-language worker keys`. Both skill copies are identical and, under `The gate contract`, contain `jobs.pick(ctx.config, "<section>", "<key>", <today's default>)` and `jobs.run_jobs(ctx.config)`. `.importlinter` has `marestail.jobs` on the layer line that contains `marestail.context` and `marestail.ran_against`. `marestail/jobs.py` imports only `os`, `re` and `marestail.config`. `tests/conftest.py` hermetic contains `monkeypatch.delenv("MARESTAIL_JOBS", raising=False)`.

9. `python -m pytest tests/test_jobs.py tests/test_gates_py_mutation.py tests/test_gates_ts_mutation.py tests/test_gates_ex_mutation.py tests/test_gates_rb_mutation.py tests/test_gates_cs_mutation.py tests/test_gates_er_mutation.py tests/test_gates_rs_mutation.py tests/test_gates_java_mutation.py tests/test_runner_flow.py tests/test_gates_init.py -q --tb=no`
   Expected: exit 0. Those tests passed. This command does not print coverage or CRAP. Coverage and CRAP stay on `marestail gate`. `test_time_budget` and `test_per_mutant_timeout` pass with jobs unset.

```python
#!/usr/bin/env python3
import ast, json, os, random, re, sys
from pathlib import Path
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
os.environ.pop("MARESTAIL_JOBS", None)

def die(msg):
    sys.stderr.write(msg + "\n")
    raise SystemExit(1)

def check(cond, msg):
    if not cond:
        die(msg)

def silence(fn):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        value = fn()
    check(out.getvalue() == "" and err.getvalue() == "", "wrote output")
    return value

def raised(fn):
    try:
        silence(fn)
    except SystemExit as exc:
        return str(exc)
    die("no SystemExit")

if sys.argv[1:] == ["parse"]:
    import marestail.jobs as jobs
    check(jobs.ENV == "MARESTAIL_JOBS", "ENV")
    for name in ("MARESTAIL_JOBS", "[run] jobs"):
        check([silence(lambda raw=raw, name=name: jobs.parse(raw, name)) for raw in (1, 3, "3", "03")] == [1, 3, 3, 3], name)
        for raw in (0, "0", -1, "-1", True, False, 2.5, "2.5", "four", " 3", [3]):
            check(raised(lambda raw=raw, name=name: jobs.parse(raw, name)) == f"{name} must be a whole number 1 or more, got {raw!r}", repr(raw))
    print("parse ok")
elif sys.argv[1:] == ["resolve"]:
    import marestail.jobs as jobs
    from marestail.config import Config
    def cfg(**run):
        raw = {"run": run} if run else {}
        return Config(ROOT, raw)
    def env(value):
        if value is None:
            os.environ.pop("MARESTAIL_JOBS", None)
        else:
            os.environ["MARESTAIL_JOBS"] = value
    def cpus(n):
        jobs.os.cpu_count = lambda: n
    cpus(8)
    env(None)
    check(silence(lambda: jobs.run_jobs(cfg())) is None, "none")
    check(silence(lambda: jobs.run_jobs(cfg(jobs=3))) == 3, "toml")
    env("5")
    check(silence(lambda: jobs.run_jobs(cfg(jobs=3))) == 5, "env")
    env("")
    check(silence(lambda: jobs.run_jobs(cfg(jobs=3))) == 3, "empty")
    cpus(2)
    env("16")
    check(silence(lambda: jobs.run_jobs(cfg())) == 2, "cap")
    cpus(None)
    check(silence(lambda: jobs.run_jobs(cfg())) == 1, "none-cpu")
    cpus(8)
    env("0")
    check(raised(lambda: jobs.run_jobs(cfg())) == "MARESTAIL_JOBS must be a whole number 1 or more, got '0'", "zero env")
    env(None)
    check(raised(lambda: jobs.run_jobs(cfg(jobs=0))) == "[run] jobs must be a whole number 1 or more, got 0", "zero key")
    env("5")
    check(raised(lambda: jobs.run_jobs(cfg(jobs=0))) == "[run] jobs must be a whole number 1 or more, got 0", "both")
    env("3")
    both = Config(ROOT, {"run": {"jobs": 3}, "python": {"mutation_workers": 8}})
    check(silence(lambda: jobs.pick(both, "python", "mutation_workers", 4)) == 3, "pick env")
    env(None)
    check(silence(lambda: jobs.pick(both, "python", "mutation_workers", 4)) == 8, "pick key")
    bare = Config(ROOT, {"run": {"jobs": 3}, "python": {}})
    check(silence(lambda: jobs.pick(bare, "python", "mutation_workers", 4)) == 3, "pick run")
    check(silence(lambda: jobs.pick(Config(ROOT, {"python": {}}), "python", "mutation_workers", 4)) == 4, "pick default")
    elixir = Config(ROOT, {"run": {"jobs": 3}, "elixir": {"muex_concurrency": 0}})
    check(silence(lambda: jobs.pick(elixir, "elixir", "muex_concurrency", 4)) == 0, "raw zero")
    cpus(2)
    rust = Config(ROOT, {"run": {"jobs": 3}, "rust": {"mutation_jobs": 16}})
    check(silence(lambda: jobs.pick(rust, "rust", "mutation_jobs", 2)) == 16, "raw cap")
    env("16")
    check(silence(lambda: jobs.pick(Config(ROOT, {"python": {}}), "python", "mutation_workers", 4)) == 2, "pick env cap")
    env(None)
    check(silence(lambda: jobs.pick(Config(ROOT, {"run": {"jobs": 16}, "python": {}}), "python", "mutation_workers", 4)) == 2, "pick run cap")
    cpus(8)
    env("0")
    check(raised(lambda: jobs.pick(both, "python", "mutation_workers", 4)) == "MARESTAIL_JOBS must be a whole number 1 or more, got '0'", "pick bad env")
    env(None)
    check(raised(lambda: jobs.pick(Config(ROOT, {"run": {"jobs": 0}, "python": {}}), "python", "mutation_workers", 4)) == "[run] jobs must be a whole number 1 or more, got 0", "pick bad key")
    env("5")
    check(raised(lambda: jobs.pick(Config(ROOT, {"run": {"jobs": 0}, "python": {"mutation_workers": 8}}), "python", "mutation_workers", 4)) == "[run] jobs must be a whole number 1 or more, got 0", "pick both")
    print("resolve ok")
elif sys.argv[1:] == ["gates"]:
    import tempfile
    from unittest.mock import patch
    import marestail.jobs as jobs
    from marestail import dotnet, remote, rust
    from marestail.gates import cs_mutation, ex_mutation, java_mutation, py_mutation, rb_mutation, rs_mutation, ts_mutation
    from tests.conftest import make_context
    jobs.os.cpu_count = lambda: 8
    BASE = ["npx", "stryker", "run", "--reporters", "json,progress", "--tempDirName", ".stryker-tmp", "--cleanTempDir", "always", "--dryRunTimeoutMinutes", "120"]
    USER = "class User\n  module Named\n  end\nend\nclass Admin::Boss < User\n"
    POM = "<project><build><plugins><plugin><artifactId>pitest-maven</artifactId></plugin></plugins></build></project>"

    def scratch():
        return Path(tempfile.mkdtemp())

    def knob(env, jobs_value):
        os.environ.pop("MARESTAIL_JOBS", None)
        if env is not None:
            os.environ["MARESTAIL_JOBS"] = env
        return {"run": {"jobs": jobs_value}} if jobs_value is not None else {}

    class Cap:
        def __init__(self):
            self.builds = []
            self.workers = []
            self.commands = []

        def run_mutation(self, ctx, gate, build, cwd, workers, env=None, timeout=0, pull=()):
            self.builds.append(build)
            self.workers.append(workers)
            command = build(workers)
            self.commands.append(command)
            return remote.Outcome(1, "stub", "")

    def run_py(env, jobs_value, workers_key=None):
        root = scratch()
        section = {"mutation_scope": "all"}
        if workers_key is not None:
            section["mutation_workers"] = workers_key
        raw = knob(env, jobs_value)
        raw["python"] = section
        cap = Cap()
        with patch("marestail.remote.run_mutation", cap.run_mutation):
            py_mutation.run_gate(make_context(root, raw))
        return root, cap

    def expect_py(env, jobs_value, n, workers_key=None, probe=None):
        root, cap = run_py(env, jobs_value, workers_key)
        want = [f"{root}/.venv/bin/mutmut", "run", "--max-children", str(n)]
        check(cap.commands == [want], str(cap.commands))
        if probe is not None:
            check(cap.builds[0](probe)[-1] == str(probe), "py probe")

    expect_py(None, None, 4, probe=13)
    expect_py("3", None, 3, probe=13)
    expect_py(None, 3, 3, probe=13)
    expect_py(None, 3, 8, workers_key=8)
    expect_py("3", 3, 3, workers_key=8, probe=13)

    def run_ts(env, jobs_value, section, changed=None):
        root = scratch()
        (root / "web").mkdir()
        raw = knob(env, jobs_value)
        raw["ts"] = {"root": "web", **section}
        fields = {}
        if changed is not None:
            for name in changed:
                (root / name).write_text("")
            fields = {"scope_changed": True, "changed": set(changed)}
        cap = Cap()
        with patch("marestail.remote.run_mutation", cap.run_mutation):
            ts_mutation.run_gate(make_context(root, raw, **fields))
        return cap

    unset = run_ts(None, None, {"mutation_scope": "all"})
    check(unset.commands == [BASE], str(unset.commands))
    check(unset.workers == [0], str(unset.workers))
    check(unset.builds[0](10) == [*BASE, "--concurrency", "10"], "ts probe")
    for env, jobs_value in (("3", None), (None, 3)):
        cap = run_ts(env, jobs_value, {"mutation_scope": "all"})
        check(cap.commands == [[*BASE, "--concurrency", "3"]], str(cap.commands))
        cap = run_ts(env, jobs_value, {}, ["web/a.ts", "web/b.ts"])
        check(cap.commands == [[*BASE, "--concurrency", "3", "--mutate", "a.ts,b.ts"]], str(cap.commands))
    kept = run_ts(None, None, {"mutation_concurrency": "3", "mutation_incremental": True}, ["web/a.ts"])
    check(kept.commands == [[*BASE, "--concurrency", "3", "--incremental", "--incrementalFile", "reports/stryker-incremental.json", "--mutate", "a.ts"]], str(kept.commands))
    nine = run_ts(None, None, {"mutation_concurrency": "9", "mutation_incremental": True}, ["web/a.ts"])
    check(nine.commands[0][nine.commands[0].index("--concurrency") + 1] == "9", str(nine.commands))
    for env, jobs_value in (("3", None), (None, 3)):
        command = run_ts(env, jobs_value, {"mutation_concurrency": "9", "mutation_incremental": True}, ["web/a.ts"]).commands[0]
        at = command.index("--concurrency")
        check(command[at + 1] == "3", str(command))
        check(command.index("--concurrency") < command.index("--incremental") < command.index("--mutate"), str(command))

    def watched(*parts, limit="300"):
        return ["bash", "-c", ex_mutation.WATCHDOG, "muex-watchdog", limit, "15", "mix", "muex", "--format", "json", "--fail-at", "0", *parts]

    def run_ex(env, jobs_value, settings=None):
        root = scratch()
        raw = knob(env, jobs_value)
        raw["elixir"] = {"mutation_scope": "all", **(settings or {})}
        cap = Cap()
        with patch.object(ex_mutation, "run", lambda *args, **kwargs: (0, "")), patch("marestail.remote.run_mutation", cap.run_mutation):
            ex_mutation.run_gate(make_context(root, raw))
        return cap

    plain = run_ex(None, None)
    check(plain.commands == [watched("--no-filter", "--concurrency", "4")], str(plain.commands))
    check(plain.builds[0](16) == watched("--no-filter", "--concurrency", "16"), "ex probe")
    for env, jobs_value in (("3", None), (None, 3)):
        cap = run_ex(env, jobs_value)
        check(cap.commands == [watched("--no-filter", "--concurrency", "3")], str(cap.commands))
        check(cap.builds[0](16) == watched("--no-filter", "--concurrency", "16"), "ex stays")
    dropped = run_ex(None, 3, {"muex_concurrency": 0})
    check(dropped.commands == [watched("--no-filter")] and "--concurrency" not in dropped.commands[0], str(dropped.commands))
    over = run_ex("3", None, {"muex_concurrency": 0})
    check(over.commands == [watched("--no-filter", "--concurrency", "3")], str(over.commands))

    def run_rb(env, jobs_value):
        root = scratch()
        path = root / "app" / "models" / "user.rb"
        path.parent.mkdir(parents=True)
        path.write_text(USER)
        raw = knob(env, jobs_value)
        raw["ruby"] = {"exec": ["bundle", "exec"]}
        calls = []

        def reply(command, cwd, **options):
            calls.append(list(command))
            return (0, "mutant") if list(command)[-2:] == ["info", "mutant"] else (1, "")

        with patch.object(rb_mutation, "run", reply):
            rb_mutation.run_gate(make_context(root, raw, scope_changed=True, changed={"app/models/user.rb"}))
        return calls

    bare = run_rb(None, None)
    check(bare == [["bundle", "info", "mutant"], ["bundle", "exec", "mutant", "run", "Admin::Boss*", "Named*", "User*"]], str(bare))
    wanted_rb = ["bundle", "exec", "mutant", "run", "--jobs", "3", "Admin::Boss*", "Named*", "User*"]
    for env, jobs_value in (("3", None), (None, 3)):
        calls = run_rb(env, jobs_value)
        check(calls == [["bundle", "info", "mutant"], wanted_rb], str(calls))

    def cs_argv(env, jobs_value):
        root = scratch()
        raw = knob(env, jobs_value)
        raw["dotnet"] = {"root": "cs", "mutation_exclude": ["/cs/Gen/", "Migr"]}
        ctx = make_context(root, raw)
        product, tests, out = root / "cs" / "App.csproj", root / "cs" / "T" / "T.csproj", root / "out"
        return cs_mutation.command(ctx, product, tests, out, ["cs/A.cs", "other/B.cs"]), tests, out

    base_argv, tests, out = cs_argv(None, None)
    check(base_argv == ["stryker", "--skip-version-check", "--break-on-initial-test-failure", "--test-project", str(tests), "--project", "App.csproj", "-O", str(out), "-r", "json", "-r", "progress", "-m", "!**/Gen", "-m", "!**/Migr", "-m", "**/A.cs", "-m", "**/other/B.cs"], str(base_argv))
    for env, jobs_value in (("3", None), (None, 3)):
        argv, _, _ = cs_argv(env, jobs_value)
        at = argv.index("progress")
        check(argv[at + 1:at + 4] == ["--concurrency", "3", "-m"], str(argv))

    def run_cs(env, jobs_value):
        root = scratch()
        for name, text in {"App/App.csproj": "<Project />", "AppTests/AppTests.csproj": "", "App/A.cs": "", "App/B.cs": "", "AppTests/T.cs": ""}.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        raw = knob(env, jobs_value)
        raw["dotnet"] = {"mutation_scope": "all", "coverage_exclude": ["App/Gen"]}
        ctx = make_context(root, raw)
        ctx.work.mkdir()
        calls = []

        def dotnet_fn(ctx, args, **options):
            calls.append(list(args))
            if args[0] == "tool":
                return 0, ""
            path = ctx.work / cs_mutation.OUTPUT_DIR / cs_mutation.REPORT
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"files": {}}')
            return 0, ""

        with patch.object(dotnet, "dotnet", dotnet_fn), patch.dict(dotnet.PROJECTS, {}, clear=True):
            cs_mutation.run_gate(ctx)
        return root, calls

    root, calls = run_cs(None, None)
    check(calls[1][calls[1].index("-O") + 1] == str(root / ".marestail" / "stryker"), str(calls[1]))
    check(calls[1][-4:] == ["-r", "progress", "-m", "!**/App/Gen"], str(calls[1]))
    for env, jobs_value in (("3", None), (None, 3)):
        _, calls = run_cs(env, jobs_value)
        check(calls[1][-6:] == ["-r", "progress", "--concurrency", "3", "-m", "!**/App/Gen"], str(calls[1]))

    def crate(root):
        for name in ("src/lib.rs", "src/main.rs"):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("fn main() {}\n")
        (root / ".marestail").mkdir()

    def run_rs(env, jobs_value, section, changed=None):
        root = scratch()
        crate(root)
        raw = knob(env, jobs_value)
        raw["rust"] = section
        fields = {"scope_changed": True, "changed": set(changed)} if changed is not None else {}
        calls = []

        def reply(command, cwd, **options):
            calls.append(list(command))
            if list(command)[-1] == "--version":
                return 0, "cargo-mutants 27"
            folder = root / ".marestail" / "mutants.out"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "outcomes.json").write_text('{"outcomes": []}')
            return 2, ""

        with patch.object(rust, "env", lambda ctx: {}), patch.object(rust, "run", reply):
            rs_mutation.run_gate(make_context(root, raw, **fields))
        return root, calls

    def rs_list(root, jobs, extra):
        jobs_args = [] if jobs is None else ["--jobs", jobs]
        return ["cargo", "mutants", "--output", str(root / ".marestail"), "--no-shuffle", "--colors", "never", *jobs_args, *extra]

    root, calls = run_rs(None, None, {})
    check(calls[1] == rs_list(root, "2", []), str(calls[1]))
    for env, jobs_value in (("3", None), (None, 3)):
        root, calls = run_rs(env, jobs_value, {})
        check(calls[1] == rs_list(root, "3", []), str(calls[1]))
    _, calls = run_rs(None, 3, {"mutation_jobs": 4}, ["src/lib.rs"])
    check(calls[1][-3:] == ["4", "--file", "src/lib.rs"], str(calls[1]))
    _, calls = run_rs("3", None, {"mutation_jobs": 4}, ["src/lib.rs"])
    check(calls[1][-3:] == ["3", "--file", "src/lib.rs"], str(calls[1]))
    for env, jobs_value in ((None, None), ("3", None), (None, 3)):
        root, calls = run_rs(env, jobs_value, {"mutation_args": ["--in-place"]})
        check(calls[1] == rs_list(root, None, ["--in-place"]), str(calls[1]))

    def pit(out, threads):
        return ["mvn", "-B", "-ntp", "test-compile", "org.pitest:pitest-maven:mutationCoverage", "-DtargetClasses=app.App,app.App$*,app.core.Core,app.core.Core$*", "-DoutputFormats=XML", "-DtimestampedReports=false", f"-DreportsDirectory={out}", f"-Dthreads={threads}", "-DtargetTests=app.*,app.core.*"]

    def run_java(env, jobs_value, threads=None):
        root = scratch()
        (root / "pom.xml").write_text(POM)
        for name in ("src/main/java/app/App.java", "src/main/java/app/core/Core.java", "src/test/java/app/AppTest.java", "src/test/java/app/core/CoreTest.java"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("class X {}\n")
        (root / ".marestail").mkdir()
        section = {"mutation_scope": "all"}
        if threads is not None:
            section["mutation_threads"] = threads
        raw = knob(env, jobs_value)
        raw["java"] = section
        cap = Cap()
        with patch("marestail.remote.run_mutation", cap.run_mutation):
            java_mutation.run_gate(make_context(root, raw))
        return root, cap

    root, cap = run_java(None, None)
    out = root / ".marestail" / "pit"
    check(cap.commands == [pit(out, "2")], str(cap.commands))
    check(cap.builds[0](16) == pit(out, "16"), "java probe")
    for env, jobs_value in (("3", None), (None, 3)):
        root, cap = run_java(env, jobs_value)
        out = root / ".marestail" / "pit"
        check(cap.commands == [pit(out, "3")], str(cap.commands))
        check(cap.builds[0](16) == pit(out, "16"), "java stays")
    root, cap = run_java(None, 3, 8)
    check(cap.commands == [pit(root / ".marestail" / "pit", "8")], str(cap.commands))
    root, cap = run_java("3", None, 8)
    check(cap.commands == [pit(root / ".marestail" / "pit", "3")], str(cap.commands))
    print("gates ok")
elif sys.argv[1:] == ["erlang"]:
    import tempfile
    from unittest.mock import patch
    import concurrent.futures
    import marestail.jobs as jobs
    from marestail import erlang
    from marestail.gates import er_mutation
    from tests.conftest import make_context
    jobs.os.cpu_count = lambda: 8
    real = concurrent.futures.ThreadPoolExecutor

    class Recording:
        workers = []
        sizes = []

        def __init__(self, max_workers=None, *args, **kwargs):
            Recording.workers.append(kwargs.get("max_workers", max_workers))
            self._pool = real(max_workers=max_workers, *args, **kwargs)

        def map(self, fn, items, *args, **kwargs):
            items = list(items)
            Recording.sizes.append(len(items))
            return self._pool.map(fn, items, *args, **kwargs)

        def __enter__(self):
            self._pool.__enter__()
            return self

        def __exit__(self, *exc):
            return self._pool.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._pool, name)

    def once(env, jobs_value):
        os.environ.pop("MARESTAIL_JOBS", None)
        if env is not None:
            os.environ["MARESTAIL_JOBS"] = env
        root = Path(tempfile.mkdtemp())
        for name in ("src/a.erl", "test/a_tests.erl"):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("")
        (root / ".marestail").mkdir()
        generated = [
            {"id": ident, "file": str(root / "src" / "a.erl"), "line": ident, "operator": "comparison", "original": "<", "replacement": ">=", "mutant": f"/scratch/m{ident}/a.erl"}
            for ident in range(1, 6)
        ]
        raw = {"erlang": {"erlc": "erlc", "escript": "escript", "mutation_scope": "all"}}
        if jobs_value is not None:
            raw["run"] = {"jobs": jobs_value}

        def reply(command, cwd, **options):
            if len(command) > 3 and command[2] == "mutants":
                (Path(command[3]) / "mutants.json").write_text(json.dumps({"mutants": generated}))
                return 0, ""
            text = " ".join(command)
            if command[0] == "erlc" and "/ebin-1" in text:
                return 1, "bad"
            if command[0] == "escript" and "/ebin-2" in text:
                return 0, ""
            if command[0] == "escript" and "/ebin-3" in text:
                return 1, ""
            if command[0] == "escript" and "/ebin-4" in text:
                return 124, ""
            if command[0] == "escript" and "/ebin-5" in text:
                return 3, ""
            return 0, ""

        Recording.workers, Recording.sizes = [], []
        check(hasattr(er_mutation, "ThreadPoolExecutor"), "bind ThreadPoolExecutor on er_mutation")
        with patch.object(erlang, "run", reply), patch.object(erlang, "host_erlang", lambda ctx: True), patch.object(er_mutation, "ThreadPoolExecutor", Recording):
            result = er_mutation.run_gate(make_context(root, raw))
        report = json.loads((root / ".marestail" / "er-mutation.json").read_text())
        statuses = [entry["status"] for entry in report["mutants"]]
        return Recording.workers, Recording.sizes, result.ok, result.summary, result.findings, statuses

    finding = ["src/a.erl:2 comparison mutant survived: < -> >="]
    summary = "1 of 4 mutants not killed (1 failed to compile)"
    statuses = ["invalid", "survived", "killed", "timeout", "error"]
    for env, jobs_value, workers in ((None, None, 1), ("3", None, 3), (None, 3, 3)):
        got_workers, sizes, ok, got_summary, findings, got_statuses = once(env, jobs_value)
        check(got_workers == [workers] and sizes == [5], f"workers {got_workers} sizes {sizes}")
        check(ok is False and got_summary == summary and findings == finding and got_statuses == statuses, f"{ok} {got_summary} {findings} {got_statuses}")
    print("erlang ok")
elif sys.argv[1:] == ["findings"]:
    import tempfile
    from marestail.gates import rb_mutation, rs_mutation
    from tests.conftest import make_context
    root = Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    ctx = make_context(root, {"rust": {}})

    def rust_mutant(path, line):
        body = {"package": "demo", "file": path, "name": "swap", "span": {"start": {"line": line, "column": 1}}, "function": {"function_name": "add"}}
        return {"scenario": {"Mutant": body}, "summary": "MissedMutant"}

    items = [rust_mutant("src/a.rs", n) for n in range(1, 41)] + [rust_mutant("src/b.rs", n) for n in range(1, 31)]
    baseline = {"scenario": "Baseline", "summary": "Success"}

    def judge(order):
        return rs_mutation.verdict(ctx, {"outcomes": [baseline, *order]}, "", 0.0)

    shuffled = items.copy()
    random.Random(0).shuffle(shuffled)
    first, second = judge(items), judge(shuffled)
    check(len(first.findings) == 60 and first.findings == second.findings, f"rust {first.findings[:2]} vs {second.findings[:2]}")
    check(first.findings[0] == "src/a.rs:1 add: swap survived", first.findings[0])
    check(first.findings.index("src/a.rs:9 add: swap survived") < first.findings.index("src/a.rs:10 add: swap survived"), "rust lines")
    check(first.findings[59] == "src/b.rs:20 add: swap survived", first.findings[59])
    check("src/b.rs:21 add: swap survived" not in first.findings, "rust 21")
    check(first.summary == "70 of 70 mutants not killed", first.summary)

    def subject(path, line, syntax):
        return {"identification": f"{syntax}:{path}:{line}", "source_path": str(root / path), "coverage_results": [{"mutation_result": {"mutation_type": "evil"}, "criteria_result": {"test_result": False}}]}

    subs = [subject("lib/a.rb", n, "A#m") for n in range(1, 41)] + [subject("lib/b.rb", n, "B#m") for n in range(1, 31)]
    ruby = make_context(root, {"ruby": {}})

    def judge_rb(order):
        total, failures = rb_mutation.session_failures(order, ruby)
        return rb_mutation.verdict(total, failures, "", 0.0)

    shuffled = subs.copy()
    random.Random(0).shuffle(shuffled)
    first, second = judge_rb(subs), judge_rb(shuffled)
    check(len(first.findings) == 60 and first.findings == second.findings, f"rb {first.findings[:2]} vs {second.findings[:2]}")
    check(first.findings[0] == "lib/a.rb:1 A#m: 1 mutant survived", first.findings[0])
    check(first.findings.index("lib/a.rb:9 A#m: 1 mutant survived") < first.findings.index("lib/a.rb:10 A#m: 1 mutant survived"), "rb lines")
    check(first.findings[59] == "lib/b.rb:20 B#m: 1 mutant survived", first.findings[59])
    check("lib/b.rb:21 B#m: 1 mutant survived" not in first.findings, "rb 21")
    check(first.summary == "70 of 70 mutants not killed", first.summary)
    print("findings ok")
elif sys.argv[1:] == ["docs"]:
    template = (ROOT / "templates/marestail.toml").read_text().splitlines()
    nice = "# nice = 19  # 0 leaves the process at the shell's priority; MARESTAIL_NICE overrides; children inherit"
    check(template[template.index("# [run]") + 1] == nice, "nice")
    check(template[template.index(nice) + 1] == "# jobs = 4  # parallel workers for every mutation gate; MARESTAIL_JOBS wins over it", "jobs line")
    text = "\n".join(template)
    for line in ("# mutation_workers = 4  # wins over [run] jobs for mutmut", "# mutation_jobs = 2  # wins over [run] jobs for cargo-mutants", "# mutation_threads = 2  # wins over [run] jobs for PIT"):
        check(line in text, line)
    readme = (ROOT / "README.md").read_text().split("\n\n")
    scope = next(i for i, part in enumerate(readme) if part.startswith("Mutation testing always runs on the diff"))
    para = readme[scope + 1]
    for phrase in ("MARESTAIL_JOBS", "[python] mutation_workers", "--max-children", "[elixir] muex_concurrency", "--concurrency", "[rust] mutation_jobs", "--jobs", "[java] mutation_threads", "-Dthreads", "[run] jobs", "StrykerJS", "Stryker.NET", "Erlang", "CPU", "0 is an error", "mutant set", "verdict", "default", "--in-place", "<threads>", "templates/java-pitest.xml"):
        check(phrase in para, phrase)
    check(re.search(r"(?<![A-Za-z])mutant(?![A-Za-z])", para), "mutant")
    cell = "parallel workers for every mutation gate (1 or more, capped at the CPU count; empty means unset); wins over [run] jobs and the per-language worker keys"
    check(any(line.startswith("| `MARESTAIL_JOBS` |") and cell in line for line in (ROOT / "README.md").read_text().splitlines()), "env")
    skills = [(ROOT / ".claude/skills/add-language/SKILL.md").read_text(), (ROOT / ".agents/skills/add-language/SKILL.md").read_text()]
    check(skills[0] == skills[1], "skills differ")
    contract = skills[0].split("## The gate contract")[1].split("## ")[0]
    bullet = 'jobs.pick(ctx.config, "<section>", "<key>", <today\'s default>)'
    lines = [line for line in contract.splitlines() if line.startswith("- ")]
    check(any(bullet in line and "jobs.run_jobs(ctx.config)" in line for line in lines), "one bullet")
    layer = next(line for line in (ROOT / ".importlinter").read_text().splitlines() if "marestail.context" in line and "marestail.ran_against" in line)
    check("marestail.jobs" in layer, "layer")
    tree = ast.parse((ROOT / "marestail/jobs.py").read_text())
    plain, modules = set(), set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                check(alias.asname is None, alias.name)
                plain.add(alias.name)
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
        else:
            check(not isinstance(node, ast.Expr) or not isinstance(getattr(node, "value", None), ast.Constant), "docstring")
    check(plain >= {"os", "re"} and modules == {"os", "re", "marestail.config"}, str((plain, modules)))
    check('monkeypatch.delenv("MARESTAIL_JOBS", raising=False)' in (ROOT / "tests/conftest.py").read_text(), "hermetic")
    print("docs ok")
else:
    die("usage")
```
