# QA: 018 — runs stay nice

Run from the marestail-green root with the venv active. Save the script at the bottom as `/tmp/nice-qa.py` and run the commands from that root. Every check is a child process that first sets itself to nice 0, ionice `best-effort: prio 4` and oom value 0, so a parent pipeline that is already idle does not hide the result. The script drops `MARESTAIL_AGENT`, `MARESTAIL_NICE`, `MARESTAIL_CLAUDE`, `MARESTAIL_SCOPE`, `MARESTAIL_FOCUS` and `MARESTAIL_TASK` before each child.

1. `python3 /tmp/nice-qa.py levels`
   Expected: exit 0, prints `levels ok`. Parse: `True` is 19, `False` / `None` / `""` / `0` / `"0"` are off, `10` and `"10"` are 10, `99` and `"99"` both parse as 99. `SystemExit` text is `nice must be an integer 0-19, got 'high'`, `got -1`, `got -4`, `got '1.5'`, `got 1.5`, `got 'true'`, `got 'false'`. Level: no config, empty config and `[run]` with no `nice` key are 19, toml `true` is 19, `5` is 5, `10` and `"10"` are 10, `99` is 19, `false` / `0` / `"0"` / `""` / `None` are off. Env `0` beats toml 19, env `7` beats toml 19, env `""` is off, env `99` beats toml 5 and is 19, env `high` raises `got 'high'`.

2. `python3 /tmp/nice-qa.py apply`
   Expected: exit 0, prints `apply ok`. A child that calls `apply` on an empty config is nice 19, ionice `idle`, and the proc file reads back as `500` plus a newline. A second `apply` whose `PATH` starts with an `ionice` that exits 3 is still nice 19 with that same oom value. A child started at nice 5 with toml `nice = 0` stays nice 5, `best-effort: prio 4`, oom value 0, so off did not call `setpriority`.

3. `python3 /tmp/nice-qa.py runs`
   Expected: exit 0, and these lines, in order: `default ok`, `toml-5 ok`, `env-7 ok`, `env-0 ok`, `env-empty ok`, `env-high ok`, `env-neg ok`, `env-float ok`, `env-true ok`, `env-false ok`, `toml-neg ok`, `toml-float ok`, `toml-high ok`, `nice-before-model ok`, `bad-model ok`, `ionice-exit ok`, `runs ok`.
   - default: no `[run] nice`, `MARESTAIL_NICE` unset, exit 0, stdout contains `pipeline complete` and not `oom_score_adj`, `ionice` or `setpriority`, stderr empty, stub file `19` / `idle` / `500`.
   - toml `nice = 5`: stub `5` / `idle` / `500`, exit 0, stderr empty.
   - `MARESTAIL_NICE=7` with toml `nice = 19`: stub `7` / `idle` / `500`, exit 0, stderr empty.
   - `MARESTAIL_NICE=0` with toml `nice = 19`: stub `0` / `best-effort: prio 4` / `0`, exit 0, stderr empty.
   - `MARESTAIL_NICE=` empty with toml `nice = 19`: same off result.
   - `MARESTAIL_NICE=high`, `1.5`, `true` and `false`: exit 1, stdout empty, no stub file, stderr exactly `nice must be an integer 0-19, got 'high'\n`, `got '1.5'\n`, `got 'true'\n` and `got 'false'\n`.
   - `MARESTAIL_NICE=-4`: stderr exactly `nice must be an integer 0-19, got -4\n`, no stub file.
   - toml `nice = -1`, `1.5` and `"high"`: stderr exactly `got -1\n`, `got 1.5\n` and `got 'high'\n`, no stub file.
   - `MARESTAIL_NICE=high` plus `--model dandelion/route --agent grok`: stderr exactly the `got 'high'` line, not `drop --agent`.
   - nice unset and those model flags: exit 1, stdout empty, stderr exactly `--model dandelion/route picks the backend and effort before every session; drop --agent and --effort\n`.
   - `PATH` starting with an `ionice` that exits 3: exit 0, stderr empty, stdout contains `pipeline complete`, stub file `19` / `ionice-failed` / `500`.

4. `python3 /tmp/nice-qa.py gate`
   Expected: prints `gate ok`. Exit of the gate is 0. Apart from the script's own `PROBE` line, stdout is exactly one line matching `^\[ok  \] docs           docs match the code  \(\d+\.\d+s\)$`, then a blank line and `GATE PASSED`, and no `scope:` line. Nice, ionice and oom stay at the launcher baseline.

5. `python3 /tmp/nice-qa.py watch`
   Expected: prints `watch ok`. `marestail watch /tmp` with stdin closed and stdout a pipe exits 1, and stderr contains `cbreak() returned ERR`. Curses may write screen escapes to stdout first. The same process, catching that error, is still at the launcher baseline.

6. `python3 /tmp/nice-qa.py perf`
   Expected: prints `perf ok`. This checkout has no `.marestail/trees.json`. Exit 2, the command's own stdout is empty, stderr is exactly `no perf run in progress\n`, and the process stays at the launcher baseline.

7. `python3 /tmp/nice-qa.py missing`
   Expected: prints `missing ok`. In an empty directory, exit 1, stdout empty, stderr exactly `no marestail.toml found above /tmp/nice-qa-empty\n`. The in-process call stays at the launcher baseline.

8. `python3 /tmp/nice-qa.py hook`
   Expected: prints `hook ok`. This runs the fast tier, so it takes as long as `marestail gate --tier fast`. Stdin is `{}`. Exit 0, the hook's own stdout and stderr are empty, and the process stays at the launcher baseline.

9. `python3 /tmp/nice-qa.py readme`
   Expected: prints `readme ok`. The paragraph is the first text after the `## Use` example block (the fence under that heading, not an earlier fence). The environment table has the new row. Both `1–19` dashes are U+2013. Every name already in that table is still there.

10. `python3 /tmp/nice-qa.py layers` and `python3 /tmp/nice-qa.py callers`
    Expected: `layers` prints the colon-separated layer line containing `marestail.ran_against` and `marestail.nice`. `callers` prints `callers ok`: `marestail/nice.py` imports no `marestail` module except `marestail.config`, `nice.apply` appears once under `marestail/` outside that file, that call sits in `runner.py` with `config_module.load` above it and `pick_model` below it, and `tools/test-nice.py` does not exist.

11. `python -m pytest tests/test_nice.py tests/test_cli.py tests/test_runner_flow.py -q --tb=no`
    Expected: all pass, and this process's nice does not change. `tests/test_nice.py` stubs `os.setpriority`, `subprocess.run` and the proc path. It covers every row above, including `parse(99) == 99` and `parse("99") == 99`, a non-zero `ionice` exit that still writes `500`, and the four swallowed errors (`setpriority` `OSError`, `setpriority` `AttributeError`, `ionice` `OSError`, the oom write `OSError`) with later steps still running. `pipeline_env` stubs `nice.apply` and records one call with the loaded config before `pick_model`. A bad nice value raises before `run_steps`. A test in `tests/test_cli.py` shows `marestail gate` never calls `nice.apply`.

```python
#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path.cwd()
PY = sys.executable
CLI = str(ROOT / "marestail" / "cli.py")
DROP = (
    "MARESTAIL_NICE",
    "MARESTAIL_AGENT",
    "MARESTAIL_CLAUDE",
    "MARESTAIL_SCOPE",
    "MARESTAIL_FOCUS",
    "MARESTAIL_TASK",
    "MARESTAIL_GATE_ACTIVE",
)

RESET = """
import os, subprocess
from pathlib import Path
os.setpriority(os.PRIO_PROCESS, 0, 0)
Path("/proc/%d/oom_score_adj" % os.getpid()).write_text("0")
subprocess.run(["ionice", "-c", "2", "-n", "4", "-p", str(os.getpid())], check=True)

def snapshot():
    oom = Path("/proc/%d/oom_score_adj" % os.getpid()).read_text()
    ion = subprocess.run(["ionice", "-p", str(os.getpid())], capture_output=True, text=True).stdout.strip()
    return (os.getpriority(os.PRIO_PROCESS, 0), oom, ion)
"""


def child(code: str, stdin: str = "", cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in DROP:
        env.pop(key, None)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [PY, "-c", RESET + "\n" + code],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or ROOT,
    )


def require(name: str, completed: subprocess.CompletedProcess[str], checks: list[tuple[bool, str]]) -> None:
    failed = [message for ok, message in checks if not ok]
    if completed.returncode != 0 and not any(ok for ok, _ in checks):
        failed.append(f"exit {completed.returncode}")
    if failed:
        raise SystemExit(
            f"{name} failed: {failed}\nstdout={completed.stdout!r}\nstderr={completed.stderr!r}"
        )


def levels() -> None:
    code = textwrap.dedent(
        """
        from pathlib import Path
        import os
        from marestail.config import Config
        from marestail.nice import ENV, level, parse
        root = Path(".")

        def dies(raw):
            try:
                parse(raw)
            except SystemExit as error:
                return str(error)
            raise SystemExit("no exit for %r" % (raw,))

        pairs = [
            (parse(True), 19), (parse(False), None), (parse(None), None), (parse(""), None),
            (parse(0), None), (parse("0"), None), (parse(10), 10), (parse("10"), 10), (parse(99), 99), (parse("99"), 99),
            (dies("high"), "nice must be an integer 0-19, got 'high'"),
            (dies(-1), "nice must be an integer 0-19, got -1"),
            (dies("-4"), "nice must be an integer 0-19, got -4"),
            (dies("1.5"), "nice must be an integer 0-19, got '1.5'"),
            (dies(1.5), "nice must be an integer 0-19, got 1.5"),
            (dies("true"), "nice must be an integer 0-19, got 'true'"),
            (dies("false"), "nice must be an integer 0-19, got 'false'"),
            (level(None), 19),
            (level(Config(root=root, raw={})), 19),
            (level(Config(root=root, raw={"run": {}})), 19),
            (level(Config(root=root, raw={"run": {"nice": True}})), 19),
            (level(Config(root=root, raw={"run": {"nice": 0}})), None),
            (level(Config(root=root, raw={"run": {"nice": 5}})), 5),
            (level(Config(root=root, raw={"run": {"nice": 10}})), 10),
            (level(Config(root=root, raw={"run": {"nice": "10"}})), 10),
            (level(Config(root=root, raw={"run": {"nice": 99}})), 19),
            (level(Config(root=root, raw={"run": {"nice": False}})), None),
            (level(Config(root=root, raw={"run": {"nice": "0"}})), None),
            (level(Config(root=root, raw={"run": {"nice": ""}})), None),
            (level(Config(root=root, raw={"run": {"nice": None}})), None),
        ]
        os.environ[ENV] = "0"
        pairs.append((level(Config(root=root, raw={"run": {"nice": 19}})), None))
        os.environ[ENV] = "7"
        pairs.append((level(Config(root=root, raw={"run": {"nice": 19}})), 7))
        os.environ[ENV] = ""
        pairs.append((level(Config(root=root, raw={"run": {"nice": 19}})), None))
        os.environ[ENV] = "99"
        pairs.append((level(Config(root=root, raw={"run": {"nice": 5}})), 19))
        bad = [item for item in pairs if item[0] != item[1]]
        if bad:
            raise SystemExit("WRONG %r" % (bad,))
        os.environ[ENV] = "high"
        try:
            level(Config(root=root, raw={}))
        except SystemExit as error:
            if str(error) != "nice must be an integer 0-19, got 'high'":
                raise
        else:
            raise SystemExit("env high did not raise")
        print("levels ok")
        """
    )
    done = child(code)
    require("levels", done, [(done.returncode == 0, f"exit {done.returncode}"), (done.stdout.strip() == "levels ok", done.stdout)])
    print(done.stdout.strip())


def apply_checks() -> None:
    code = textwrap.dedent(
        """
        import os, stat, subprocess, tempfile
        from pathlib import Path
        from marestail.config import Config
        from marestail.nice import apply

        def show(label):
            oom = Path("/proc/%d/oom_score_adj" % os.getpid()).read_text()
            ion = subprocess.run(["ionice", "-p", str(os.getpid())], capture_output=True, text=True)
            print(label, os.getpriority(os.PRIO_PROCESS, 0), ion.stdout.strip() if ion.returncode == 0 else "ionice-failed", repr(oom))

        apply(Config(root=Path("."), raw={}))
        show("on")
        folder = Path(tempfile.mkdtemp())
        fake = folder / "ionice"
        fake.write_text("#!/bin/sh\\nexit 3\\n")
        fake.chmod(stat.S_IRWXU)
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ["PATH"]
        apply(Config(root=Path("."), raw={}))
        show("fake-ionice")
        """
    )
    on = child(code)
    off = child(
        textwrap.dedent(
            """
            import os, subprocess
            from pathlib import Path
            from marestail.config import Config
            from marestail.nice import apply
            os.setpriority(os.PRIO_PROCESS, 0, 5)
            apply(Config(root=Path("."), raw={"run": {"nice": 0}}))
            oom = Path("/proc/%d/oom_score_adj" % os.getpid()).read_text()
            ion = subprocess.run(["ionice", "-p", str(os.getpid())], capture_output=True, text=True).stdout.strip()
            print(os.getpriority(os.PRIO_PROCESS, 0), ion, repr(oom))
            """
        )
    )
    require(
        "apply-on",
        on,
        [
            ("on 19 idle '500\\n'" in on.stdout, on.stdout),
            ("fake-ionice 19 ionice-failed '500\\n'" in on.stdout, "fake ionice"),
        ],
    )
    require("apply-off", off, [(off.stdout.strip() == "5 best-effort: prio 4 '0\\n'", off.stdout)])
    print("apply ok")


def prepare_repo(root: Path, nice: str | None) -> None:
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@marestail"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
        (root / ".gitignore").write_text(".marestail/\n")
        (root / "tasks").mkdir()
        (root / "tasks" / "t.md").write_text("# t\n")
    body = '[git]\nbase = "main"\n[perf]\nenabled = false\n[practices]\nenabled = false\n'
    if nice is not None:
        body += "[run]\nnice = " + nice + "\n"
    (root / "marestail.toml").write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=root, check=True)


def stub_path(directory: Path) -> Path:
    path = directory / "stub.py"
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, os, re, subprocess, sys",
                "from pathlib import Path",
                "prompt = sys.stdin.read()",
                "ion = subprocess.run(['ionice', '-p', str(os.getpid())], capture_output=True, text=True)",
                "oom = Path('/proc/%d/oom_score_adj' % os.getpid()).read_text().strip()",
                "ion_text = ion.stdout.strip() if ion.returncode == 0 else 'ionice-failed'",
                "Path(os.environ['NICE_OUT']).write_text('%s\\n%s\\n%s\\n' % (os.getpriority(os.PRIO_PROCESS, 0), ion_text, oom))",
                "found = re.search(r'Write your verdict to (\\S+) and', prompt)",
                "verdict = Path(found.group(1))",
                "verdict.parent.mkdir(parents=True, exist_ok=True)",
                "verdict.write_text('VERDICT: PASS\\n')",
                "print(json.dumps({'is_error': False, 'num_turns': 1, 'total_cost_usd': 0, 'result': 'ok'}))",
                "",
            ]
        )
    )
    path.chmod(0o755)
    return path


def invoke(args: list[str], cwd: Path, extra: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    code = "\n".join(
        [
            "import os, subprocess, sys",
            "os.environ.update(%r)" % extra,
            "completed = subprocess.run([sys.executable, %r, *%r], cwd=%r, env=os.environ.copy(), input=%r, text=True, capture_output=True)"
            % (CLI, args, str(cwd), stdin),
            "sys.stdout.write(completed.stdout)",
            "sys.stderr.write(completed.stderr)",
            "raise SystemExit(completed.returncode)",
        ]
    )
    return child(code)


def runs() -> None:
    work = Path("/tmp/nice-qa-work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    repo = work / "repo"
    repo.mkdir()
    stub = stub_path(work)
    out = work / "nice.txt"
    base = ["run", "tasks/t.md", "--from", "critic", "--to", "critic", "--auto", "--retries", "1"]

    def one(label: str, toml_nice: str | None, env: dict[str, str], args: list[str], want_exit: int, stdout_has: str, stderr: str | None, stub_text: str | None) -> None:
        prepare_repo(repo, toml_nice)
        out.unlink(missing_ok=True)
        extra = {"MARESTAIL_AGENT": "claude", "MARESTAIL_CLAUDE": str(stub), "NICE_OUT": str(out), **env}
        done = invoke(args, repo, extra)
        problems = []
        if done.returncode != want_exit:
            problems.append(f"exit {done.returncode} != {want_exit}")
        if stdout_has and stdout_has not in done.stdout:
            problems.append(f"stdout missing {stdout_has!r}")
        if stdout_has == "" and done.stdout != "":
            problems.append(f"stdout not empty {done.stdout!r}")
        for banned in ("oom_score_adj", "ionice", "setpriority"):
            if banned in done.stdout or banned in done.stderr:
                problems.append(f"port word {banned}")
        if stderr is not None and done.stderr != stderr:
            problems.append(f"stderr {done.stderr!r} != {stderr!r}")
        if stub_text is None and out.exists():
            problems.append(f"stub ran: {out.read_text()!r}")
        if stub_text is not None and (not out.exists() or out.read_text() != stub_text):
            got = out.read_text() if out.exists() else "absent"
            problems.append(f"stub {got!r} != {stub_text!r}")
        if problems:
            raise SystemExit(f"{label}: {problems}\nstdout={done.stdout!r}\nstderr={done.stderr!r}")
        print(label, "ok")

    one("default", None, {}, base, 0, "pipeline complete", "", "19\nidle\n500\n")
    one("toml-5", "5", {}, base, 0, "pipeline complete", "", "5\nidle\n500\n")
    one("env-7", "19", {"MARESTAIL_NICE": "7"}, base, 0, "pipeline complete", "", "7\nidle\n500\n")
    one("env-0", "19", {"MARESTAIL_NICE": "0"}, base, 0, "pipeline complete", "", "0\nbest-effort: prio 4\n0\n")
    one("env-empty", "19", {"MARESTAIL_NICE": ""}, base, 0, "pipeline complete", "", "0\nbest-effort: prio 4\n0\n")
    one("env-high", None, {"MARESTAIL_NICE": "high"}, base, 1, "", "nice must be an integer 0-19, got 'high'\n", None)
    one("env-neg", None, {"MARESTAIL_NICE": "-4"}, base, 1, "", "nice must be an integer 0-19, got -4\n", None)
    one("env-float", None, {"MARESTAIL_NICE": "1.5"}, base, 1, "", "nice must be an integer 0-19, got '1.5'\n", None)
    one("env-true", None, {"MARESTAIL_NICE": "true"}, base, 1, "", "nice must be an integer 0-19, got 'true'\n", None)
    one("env-false", None, {"MARESTAIL_NICE": "false"}, base, 1, "", "nice must be an integer 0-19, got 'false'\n", None)
    one("toml-neg", "-1", {}, base, 1, "", "nice must be an integer 0-19, got -1\n", None)
    one("toml-float", "1.5", {}, base, 1, "", "nice must be an integer 0-19, got 1.5\n", None)
    one("toml-high", '"high"', {}, base, 1, "", "nice must be an integer 0-19, got 'high'\n", None)
    bad = base + ["--model", "dandelion/route", "--agent", "grok"]
    one("nice-before-model", None, {"MARESTAIL_NICE": "high"}, bad, 1, "", "nice must be an integer 0-19, got 'high'\n", None)
    one(
        "bad-model",
        None,
        {},
        bad,
        1,
        "",
        "--model dandelion/route picks the backend and effort before every session; drop --agent and --effort\n",
        None,
    )
    fake_dir = work / "bin"
    fake_dir.mkdir()
    fake = fake_dir / "ionice"
    fake.write_text("#!/bin/sh\nexit 3\n")
    fake.chmod(0o755)
    one(
        "ionice-exit",
        None,
        {"PATH": str(fake_dir) + os.pathsep + os.environ["PATH"]},
        base,
        0,
        "pipeline complete",
        "",
        "19\nionice-failed\n500\n",
    )
    print("runs ok")


def probe(args: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    code = "\n".join(
        [
            "from marestail.cli import main",
            "before = snapshot()",
            "try:",
            "    code = main(%r)" % args,
            "except SystemExit as error:",
            "    code = error.code",
            "    print('MSG', str(error))",
            "except Exception as error:",
            "    code = 1",
            "    print('ERR', type(error).__name__, error)",
            "after = snapshot()",
            "print('PROBE', before, after, code)",
        ]
    )
    return child(code, stdin=stdin)


def command_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith("PROBE ")]


def gate() -> None:
    done = probe(["gate", "--tier", "fast", "--only", "docs"])
    lines = command_lines(done.stdout)
    ok = bool(lines) and re.fullmatch(r"\[ok  \] docs           docs match the code  \(\d+\.\d+s\)", lines[0]) is not None
    require(
        "gate",
        done,
        [
            (ok is True and lines[1:] == ["", "GATE PASSED"], lines),
            ("scope:" not in done.stdout, "scope"),
            ("PROBE (0, '0\\n', 'best-effort: prio 4') (0, '0\\n', 'best-effort: prio 4') 0" in done.stdout, done.stdout),
        ],
    )
    print("gate ok")


def watch() -> None:
    raw = subprocess.run([PY, CLI, "watch", "/tmp"], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    done = probe(["watch", "/tmp"])
    require(
        "watch",
        done,
        [
            (raw.returncode == 1, f"raw exit {raw.returncode}"),
            ("cbreak() returned ERR" in raw.stderr, raw.stderr[-200:]),
            ("cbreak() returned ERR" in done.stdout, done.stdout),
            ("PROBE (0, '0\\n', 'best-effort: prio 4') (0, '0\\n', 'best-effort: prio 4') 1" in done.stdout, done.stdout),
        ],
    )
    print("watch ok")


def perf() -> None:
    done = probe(["perf", "run", "nope", "--tree", "HEAD"])
    require(
        "perf",
        done,
        [
            ("PROBE (0, '0\\n', 'best-effort: prio 4') (0, '0\\n', 'best-effort: prio 4') 2" in done.stdout, done.stdout),
            (command_lines(done.stdout) == [], command_lines(done.stdout)),
            (done.stderr == "no perf run in progress\n", done.stderr),
        ],
    )
    print("perf ok")


def hook() -> None:
    done = probe(["gate", "--hook"], stdin="{}\n")
    require(
        "hook",
        done,
        [
            ("PROBE (0, '0\\n', 'best-effort: prio 4') (0, '0\\n', 'best-effort: prio 4') 0" in done.stdout, done.stdout),
            (command_lines(done.stdout) == [], command_lines(done.stdout)),
            (done.stderr == "", done.stderr),
        ],
    )
    print("hook ok")


def missing() -> None:
    folder = Path("/tmp/nice-qa-empty")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    code = "\n".join(
        [
            "from marestail.cli import main",
            "before = snapshot()",
            "try:",
            "    main(['run', 'tasks/t.md', '--from', 'critic', '--to', 'critic', '--auto'])",
            "except SystemExit as error:",
            "    print('EXIT', error.code)",
            "    print('MSG', str(error))",
            "after = snapshot()",
            "print('PROBE', before, after)",
        ]
    )
    done = child(code, cwd=folder)
    message = f"no marestail.toml found above {folder.resolve()}"
    require(
        "missing",
        done,
        [
            (f"MSG {message}" in done.stdout, done.stdout),
            ("PROBE (0, '0\\n', 'best-effort: prio 4') (0, '0\\n', 'best-effort: prio 4')" in done.stdout, repr(done.stdout)),
        ],
    )
    raw = subprocess.run(
        [PY, CLI, "run", "tasks/t.md", "--from", "critic", "--to", "critic", "--auto"],
        cwd=folder,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key not in DROP},
    )
    if raw.returncode != 1 or raw.stdout != "" or raw.stderr != message + "\n":
        raise SystemExit(f"raw missing: {raw.returncode} {raw.stdout!r} {raw.stderr!r}")
    print("missing ok")


def readme() -> None:
    text = (ROOT / "README.md").read_text()
    paragraph = (
        "`marestail run` starts at nice 19 with idle I/O and a raised OOM score, so a fleet of pipelines "
        "yields the CPU and disk to you and is first in line if the kernel needs memory. Children inherit. "
        "`[run] nice = 0` or `MARESTAIL_NICE=0` turns it off; any other integer 1\u201319 is the level "
        "(`MARESTAIL_NICE` wins over the file). `marestail watch` and `marestail gate` stay at the shell's priority."
    )
    row = (
        "| `MARESTAIL_NICE` | priority for `marestail run`: 0 turns it off, 1\u201319 is the nice level "
        "(default 19); wins over `[run] nice` |"
    )
    use = text.split("## Use", 1)[1]
    after = use.split("```", 2)[2]
    if not after.lstrip("\n").startswith(paragraph):
        raise SystemExit("paragraph is not the first text after the Use example block")
    table = text.split("## Environment variables", 1)[1].split("## ", 1)[0]
    if row not in table:
        raise SystemExit("environment row missing")
    kept = (
        "`MARESTAIL_AGENT`",
        "`MARESTAIL_CLAUDE`",
        "`MARESTAIL_AGY`",
        "`MARESTAIL_CURSOR`",
        "`MARESTAIL_GROK`",
        "`MARESTAIL_GROK_EFFORT`",
        "`MARESTAIL_KILO`",
        "`MARESTAIL_KILO_VARIANT`",
        "`MARESTAIL_KIMI`",
        "`MARESTAIL_JUNIE`",
        "`MARESTAIL_HERMES`",
        "`MARESTAIL_DANDELION`",
        "`DANDELION_CLAUDE_WORK_CONFIG_DIR`",
        "`CLAUDE_CONFIG_DIR`",
        "`MARESTAIL_LIMIT_WAIT_SECONDS`",
        "`MARESTAIL_LIMIT_WAITS`",
        "`MARESTAIL_SCOPE`, `MARESTAIL_FOCUS`",
        "`MARESTAIL_TASK`",
        "`MARESTAIL_QA_CMD_TIMEOUT`",
        "`MARESTAIL_GATE_ACTIVE`",
        "`MARESTAIL_SONAR_PASSWORD`",
        "`MARESTAIL_PERF_DB_ROWS`",
        "`MARESTAIL_PERF_DB_PORT`",
        "`MARESTAIL_PERF_DB_PREFIX`",
        "`MARESTAIL_PERF_DB_VOLUME`",
        "`MARESTAIL_PERF_DB_HOME`",
        "`GROK_HOME`",
        "`JAVA_HOME`",
    )
    missing = [name for name in kept if name not in table]
    if missing:
        raise SystemExit(f"dropped rows {missing}")
    print("readme ok")


def layers() -> None:
    line = next(item for item in (ROOT / ".importlinter").read_text().splitlines() if "marestail.ran_against" in item)
    if "marestail.nice" not in [part.strip() for part in line.split(":")]:
        raise SystemExit(line)
    print(line)


def callers() -> None:
    import ast

    tree = ast.parse((ROOT / "marestail" / "nice.py").read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    marestail_modules = sorted(name for name in modules if name == "marestail" or name.startswith("marestail."))
    if marestail_modules != ["marestail.config"]:
        raise SystemExit(f"imports {marestail_modules}")
    text = "\n".join(path.read_text() for path in (ROOT / "marestail").rglob("*.py") if "nice.py" not in str(path))
    if text.count("nice.apply") != 1 or "nice.apply(config)" not in text:
        raise SystemExit("expected one nice.apply(config) call")
    runner = (ROOT / "marestail" / "runner.py").read_text().splitlines()
    apply_at = next(index for index, line in enumerate(runner) if "nice.apply(config)" in line)
    previous = next(line.strip() for line in reversed(runner[:apply_at]) if line.strip())
    following = next(line.strip() for line in runner[apply_at + 1 :] if line.strip())
    if "config_module.load" not in previous or "pick_model" not in following:
        raise SystemExit(f"{previous}\n{runner[apply_at]}\n{following}")
    if not (ROOT / "tools" / "test-nice.py").exists():
        print("callers ok")
        return
    raise SystemExit("tools/test-nice.py exists")


COMMANDS = {
    "levels": levels,
    "apply": apply_checks,
    "runs": runs,
    "gate": gate,
    "watch": watch,
    "perf": perf,
    "hook": hook,
    "missing": missing,
    "readme": readme,
    "layers": layers,
    "callers": callers,
}

if __name__ == "__main__":
    COMMANDS[sys.argv[1]]()
```
