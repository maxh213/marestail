# QA: 020 — runs write events

Run from the marestail-green checkout with the venv active. Save the script at the bottom as `/tmp/qa-020.py` and run `python3 /tmp/qa-020.py`. Expected: exit 0, and the lines below, in order. The script uses `/tmp/qa-020` and inserts `Path.cwd()` at the front of `sys.path` before importing `marestail`, because a file run from `/tmp` would otherwise import the installed copy. It drops `MARESTAIL_AGENT`, `MARESTAIL_NICE`, `MARESTAIL_SCOPE`, `MARESTAIL_FOCUS`, `MARESTAIL_TASK` and `MARESTAIL_DANDELION` around every in-process run. Nothing here is a screen layout. The proof is the events file and the terminal text.

The script fails until the events writer, the runner wiring, the freeze entry, the import contract and the README paragraph exist. That is the expected result of this procedure after the coder lands.

1. `help ok`. `bin/marestail --help` exits 0 and contains `{gate,run,tasks,install,sonar,watch,perf,visual,route,graph,depth}` in that order. `run --help` lists `--from`, `--to`, `--auto`, `--scope`, `--focus`, `--model`, `--retries`, `--effort`, `--agent` in that order, and still says `attempts per role; 0 means unlimited (default)` and `skip the approval pause after the critic`. `tasks check` on `depends = []` exits 0 with empty stdout and stderr. `route --help` with `PATH=/usr/bin:/bin` exits 127 and stderr contains `dandelion is not installed`.

2. `defaults ok`. A `Config` whose root is not a git repository, `run_steps` replaced so it returns 7, and `perf_changes` set to `perf changes`: `run_pipeline(Path("t.md"), "coder", "cleaner", True, None, 2)` returns 7 and stdout is exactly `\nperf changes\n`. The call order is nice.apply, then task_file.read, then pick_model. The events file has one `run-start`, branch null, `start_commit` a string, and no `run-end`. The same fixture with `--model dandelion/route`, `--scope changed` and focus `src/a.py` prints exactly `\n`, and that `run-start` has model `dandelion/route`, agent null and scope `changed`.

3. `pass ok`. Branch `main`, base `main`, `tasks/t.md` is `# Add one\n`, `--from specifier --to critic --auto --retries 0 --model m --agent claude`. The specifier commits `features/x.feature` as `add feature`. The critic writes `VERDICT: PASS`. Events: `run-start`, `01-specifier` worker attempt 1 `done`, `02-critic` judge attempt 1 `pass`, `run-end` `complete`. `from` is `specifier`, `to` is `critic`, scope `all`, effort null, `start_commit` is HEAD before the call. The specifier head's subject is `[claude/m] add feature`. The critic head's subject is `[claude/m] critic verdict: PASS` and it differs. Handoffs are `.marestail/handoffs/t/01-specifier.md` and `02-critic.md`. `archive` matches `^\.marestail/runs/t/handoffs-\d{8}T\d{6}(-\d+)?$` and holds those files plus `start-commit`. Stdout ends with `pipeline complete\n\n`, contains the two `==` lines and `   verdict PASS`, and does not contain `events.jsonl`. Stderr is empty. Return 0. `pipeline complete` is already on stdout when `run-end` is written, and `proposals_summary` runs after that. Every line has `v` 1, this process's pid, the timestamp form, and the keys in the feature's order. `step-start.at` and `step-end.at` match `timeline.json`, and `seconds` is the rounded difference. Each step's `step-start` is already the last event when that invoke begins.

4. `append ok`. The file starts as `not json\n`. Two passing runs append. The first line is still `not json`. Two `run-start` lines share this pid, and the two `start_commit` values differ.

5. `bounce ok`. First critic: `VERDICT: BOUNCE` and `1. scenario missing`. Second: `VERDICT: PASS`. Events add `02-critic` bounce to `specifier`, `03-specifier` done, `04-critic` pass, `run-end` complete. Stdout contains `   verdict BOUNCE to specifier`.

6. `stop ok`. Both critics use that same bounce text. Events end `04-critic` bounce, `run-end` stopped. No `05-specifier`. Exit 1. `stopped_at` is `critic`. Reason is `critic repeated the same findings twice; the worker is not making progress, stopping for a human`. Stdout contains that line and `pipeline stopped at critic`.

7. `guard ok`. `--retries 0`, specifier writes nothing, three times. Stems stay `01-specifier`, attempts 1, 2, 3, outcomes `failed`, `failed`, `stopped`, handoffs null. Reason is `specifier got the same problems back 3 times in a row; the worker is not making progress, stopping for a human`. Stdout contains `missing handoff .marestail/handoffs/t/01-specifier.md` and does not contain `used all`. Archive is null and the handoffs directory remains.

8. `retries ok`. Coder, gate failing `py.tests`. `--retries 1`: one `01-coder` `stopped`, reason `coder used all 1 attempts`, stdout contains `GATE FAILED: py.tests` and `pipeline stopped at coder` and not `used all 1 attempts`. `--retries 2` with two missing handoffs: `failed` then `stopped`, reason `coder used all 2 attempts`. `--retries 3` with three: the third is `stopped` and the reason is the coder repeat-guard line, not `used all 3 attempts`. `--retries 2` with a failure, then a handoff, a passing gate, and `runner.audit.problems` returning []: `failed`, `done`, `run-end` complete, exit 0. Without that audit stub the second outcome is `stopped` and the reason is `coder used all 2 attempts`, because the handoff exists and the fresh test repo has no `features/*.feature`.

9. `proposal ok`. First specifier appends `\n\n[events]\nkeep = false\n` to `marestail.toml`, commits it, and writes `## Config change` / `Allow the events file.` Then `proposal` for `01-specifier`, path `.marestail/handoffs/t/02-proposal.md`, files `["marestail.toml"]`, step-end `failed` whose subject is `[claude/m] Revert change to frozen files by 01-specifier, recorded as a proposal`. Then `03-specifier` done, `04-critic` pass, `run-end` complete. Stdout contains the `frozen, reverted` sentence with that proposal path. `proposals_summary` runs after `run-end` is in the file and the text it returns is empty: stdout ends `pipeline complete\n\n` and does not contain `## Config changes the agents asked for and were refused`. The archive contains `02-proposal.md`. The event path is still the handoffs path. A second run, `--from specifier --to specifier` and `--retries 1`, makes only that justified edit and does not succeed again. One `01-specifier` attempt, outcome `stopped`, reason `specifier used all 1 attempts` (one attempt cannot trip the repeat guard). `archive` is null. `.marestail/handoffs/t/02-proposal.md` remains and is not archived. Stdout contains the frozen-reverted sentence and, after `pipeline stopped at specifier`, the refused-changes heading, and does not contain `used all`. The summary still runs after `run-end`.

10. `plain ok`. `--retries 1`. A committed toml change whose handoff has no `## Config change` writes no proposal. The head subject is `[claude/m] Revert change to frozen files by 01-specifier` and does not contain `recorded as a proposal`. Stdout contains `marestail.toml is frozen for specifier; reverted. Work within the current configuration.` A dirty uncommitted toml writes no proposal and no revert. Stdout contains `marestail.toml is frozen for specifier` and does not contain `reverted`.

11. `skip ok`. For each row, stdout contains the skip line and `pipeline complete`, and does not contain `== `. Events are only `run-start` and `run-end` complete, exit 0, archive null, `from` and `to` that judge. `start-commit` is gone. `events.jsonl` remains. The invoke stub is not called. Rows: practices disabled (`practices disabled in marestail.toml; skipping`); practices enabled and no guidance file (`practices: no guidance files; skipping`); design with no rulebooks (`design: no pattern rulebooks; skipping`); perf disabled (`perf disabled in marestail.toml; skipping`).

12. `perf ok`. Perf enabled, no bench file, `--retries 2`. Invokes: `perf-author-1`, `01-perf` with `VERDICT: AUTHOR`, `perf-author-1`, `02-perf` with `VERDICT: PASS`. When each `perf-author-1` invoke begins, the last event is already that attempt's `step-start`. Stdout contains `   perf-author-1: benches unchanged` twice and not `== perf (perf-author-1)`. Events: `01-perf` failed (head equals `start_commit`), `02-perf` pass, `run-end` complete. `from` and `to` are `perf`.

13. `rework ok`. Hardener bounces with `1. same problem`. Coder writes nothing. Gates and audit return nothing. Events: `01-hardener` bounce to `coder`, three `02-coder` outcomes `failed`, `failed`, `stopped`, `run-end` stopped at `hardener` with the coder repeat-guard line. Stdout contains `pipeline stopped at hardener`. One hardener attempt. Coder events say `coder`.

14. `gate ok`. `run_gates` fails only tier `full`, with `py.tests` / `nope` / 1.0 seconds. Hardener writes `VERDICT: PASS` and the coder writes a handoff. `01-hardener` and `03-hardener` bounce to `coder`, `02-coder` is done, no `04-coder`. Reason: `hardener repeated the same findings twice; the worker is not making progress, stopping for a human`. Step-end has no `gate` key. The timeline gate is `[{"name": "py.tests", "seconds": 1.0, "ok": false}]`. The full-tier gate runs before the first hardener `step-start`. Stdout contains `   verdict BOUNCE` and not `GATE FAILED`.

15. `nonworker ok`. Critic writes `VERDICT: BOUNCE hardener` and `1. not a worker`. Bounce to `hardener`. `run-end` stopped at `critic`, reason `critic bounced to hardener, which is not a worker`. Stdout contains `   verdict BOUNCE to hardener` and `pipeline stopped at critic`, and not `which is not a worker`.

16. `rounds ok`. `MAX_JUDGE_ROUNDS` is set to 2 for this check only. Findings `1. finding A` then `1. finding B`. The specifier writes a handoff each time. Events: `01-critic` bounce, `02-specifier` done, `03-critic` bounce, `04-specifier` done, `run-end` stopped. Reason `critic ran 2 rounds without a pass`. Stdout contains `pipeline stopped at critic` and not `ran 2 rounds` or `ran 1000 rounds`. After the check the constant is 1000 again.

17. `cap ok`. The critic in a critic-only window has `bounces` 1. Same two different findings. After `03-critic` bounce there is no `04-specifier`. Reason `critic still bouncing after 1 rounds; stopping for a human`, and stdout contains it.

18. `noverdict ok`. `--retries 1`. First critic writes nothing, the specifier writes a handoff, the next critic writes `VERDICT: PASS`. `01-critic` is `failed`, handoff null, head equal to `start_commit`. The next event is `02-specifier`. Stdout contains `critic wrote no verdict; retrying`. No event contains `produced no verdict`. The specifier prompt contains `critic produced no verdict after 1 attempts`. `run-end` is complete.

19. `approval ok`. Auto false, stdin a tty, answer `n`, critic writes `VERDICT: PASS`. Events: `01-critic` pass, `run-end` stopped at `critic`, reason `not approved`, exit 1. Stdout contains `continue to coder? [y/N] ` and not `not approved` or `pipeline stopped`. With stdin not a tty, stdout contains `non-interactive: continuing without approval (use --to critic to stop here)` and `pipeline complete`, and `run-end` is complete with exit 0 and reason null.

20. `qa ok`. `--from qa --to qa` and gates returning nothing. This window has no visual judge, so the run was seen. `ran-against: app` is complete, exit 0, reason null, and stdout has `pipeline complete` and not `NOT verified`. `ran-against: harness` is exit 3 and reason `pipeline complete, NOT verified against the running app (qa ran against a harness)`. `ran-against: nothing` is exit 3 and reason `pipeline complete, NOT verified against the running app (qa ran against nothing)`. `app` is not exit 0 once `state.unseen` is set. `[visual]` enabled, committed `qa/t.md` a visual block with `route: /page` and `selector: #box`, no `where:` or `symptom:`, `--agent kilo`, no `--model`, gates returning nothing, and `run_backend` returning `(1, "rate limit exceeded")`. The invoke writes `VERDICT: PASS`. `--from visual --to visual` is complete, exit 3, stopped_at null, reason `pipeline complete, NOT verified by eye`. Stdout contains that line, `visual: claude-fable-5-1 is out of usage; judging with kilo`, and `   verdict PASS (geometry only, pictures not seen)`. `--from visual --to qa` with QA writing `ran-against: app` has the same reason and exit. The same window with `ran-against: harness` keeps the harness line, exit 3, and stdout does not contain `NOT verified by eye`.

21. `wait ok`. `--retries 1`, `runner.LIMIT_WAIT_SECONDS` 90, `runner.LIMIT_WAITS` 12, and `runner.time.sleep` a no-op. One `(1, "rate limit exceeded")` then `(0, "ok")` writes `wait` / `rate-limit` between the step boundaries, and `until` is `at` plus 90 seconds in the same timestamp form, not a copy of `at`. Stdout contains `   rate limited; waiting 1 min before retrying 01-specifier`. Always limited: 12 waits, each with that same 90-second `until`, then `stopped` with `specifier used all 1 attempts`, and stdout contains `   01-specifier: still rate limited after 12 waits`. `--model dandelion/route`, the same 90 seconds, wait cap and patched sleep, require a no-op, choose empty once (`no subscription has quota left`) then `Choice("claude-opus-5 high claude", "claude", "claude-opus-5", "high", {})`: `run-start` has agent null, model `dandelion/route`, effort null, and one `dandelion-unrouted` wait whose `until` is also 90 seconds after `at`. Stdout contains `   dandelion/route: no subscription has quota left; waiting 1 min before retrying 01-specifier`.

22. `session ok`. Real `invoke`, `run_backend` writes the passing handoff or verdict and returns `(0, "ok")`, `elapsed` returns 0. Stdout contains `   01-specifier finished in 0.0 min: ok` and `pipeline complete`. Stderr is empty.

23. `dir ok`. Two passing runs with `elapsed` 0. The second `events.jsonl` is a directory. Stdout texts match and the return values match. The second stderr is one line starting `warning: cannot write run events to ` and containing `events.jsonl`. A third run on that directory warns once more.

24. `signals ok`. The specifier invoke raises the exception. There is `01-specifier` `step-start` and no `step-end`, then `run-end` with `stopped_at` `specifier`. KeyboardInterrupt stays a KeyboardInterrupt, exit_code 130, reason `SIGINT`. The installed SIGTERM handler raises a BaseException that is not an Exception, not SystemExit and not KeyboardInterrupt; `run_pipeline` then raises `SystemExit(143)`, reason `SIGTERM`. `RuntimeError("boom")` is exit 1, `RuntimeError: boom`, and re-raises. `RuntimeError("boom\nnext")` is `RuntimeError: boom`. `SystemExit(4)` is exit 4 and `SystemExit: 4`. `SystemExit("nope")` is exit 1 and `SystemExit: nope`. `SystemExit()` is exit 0 and `SystemExit: ` with one trailing space. No real signal is sent. SIGTERM and SIGINT handlers afterwards are what they were. A KeyboardInterrupt from the coder during a hardener rework leaves the hardener `bounce` in place, the coder `step-start` without `step-end`, and `stopped_at` `hardener`.

25. `repro ok`. The task has `where: /page` and `symptom: the box is square`, `[visual]` is enabled, and `visual.reproduce` returns None. Events are `run-start` then `run-end` stopped at `specifier`, exit 1, reason `pipeline stopped before specifier: the bug could not be reproduced`. No step. Stdout contains that line and not `pipeline stopped at` or `pipeline complete`.

26. `scope ok`. `--from specifier --to specifier`, handoff written. Unscoped on `main` is scope `all`, agent `claude`, model `m`, effort null, branch `main`. `--scope changed` is `changed`. `--focus src/a.py` with that file committed is `changed`. `--scope hard --focus src/a.py` is `hard`. `--scope hyper` is `hyper`. `--effort low` records `low`. No `--model` and no `[agent] model` records model null and agent `claude`. `[agent] model = "from-toml"` records `from-toml`. `[agent] backend = "grok"` records agent `grok`. `[agent] effort = "high"` records effort `high`. Detached HEAD records branch null. No `[git]` table records base `origin/master`. A task file outside the repo records an absolute `task_path`. `--scope hyper` across the whole window, with gates, audit and hunks empty, has `from` `specifier`, `to` `qa`, and step roles `specifier, critic, coder, architect, blast, hardener, qa` only. Without `[visual]`, `--from visual` exits 1 with the plain-pipeline unknown-role line and no events file. With `[visual]` and no `qa/t.md`, `--from visual --to visual` prints `visual: no qa/t.md; skipping` and `run-start` has from `visual` and to `visual`.

27. `early ok`. Each child exits 1, stdout empty, stderr exactly one line, and `events.jsonl` is absent. `--model dandelion/route --agent grok` prints the `drop --agent and --effort` line. `MARESTAIL_NICE=high` prints `nice must be an integer 0-19, got 'high'`, including when the route flags are also present, and it is the real nice check. `--scope hard` with no focus prints the hard-needs-focus line. `--scope all --focus src/a.py` and `--scope hyper --focus src/a.py` print their focus-clash lines. `--focus src/missing` prints `focus path not found under <repo>: src/missing`. Two dependencies and `stack = true` print `<task>: stack = true needs exactly one dependency, got 2`. A missing task file's stderr contains `FileNotFoundError` and the path. `--scope hyper --from cleaner` prints `unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa` and leaves `start-commit` with no `events.jsonl`. `--from nope` prints the full-pipeline unknown-role line.

28. `module ok`. `Log.append("wait", {"reason": "rate-limit", "until": "2026-09-28T14:03:07.412Z"}, "2026-09-28T14:03:07.412Z", 4)` writes exactly `{"v": 1, "at": "2026-09-28T14:03:07.412Z", "event": "wait", "pid": 4, "reason": "rate-limit", "until": "2026-09-28T14:03:07.412Z"}\n`. A second append adds a line. None is `null`. Patching `marestail.events.os` shows one open with `O_WRONLY | O_APPEND | O_CREAT` and mode `0o644`, one write of those bytes, one fsync and one close. A directory warns once per `Log`. The source does not call `datetime`, `time` or `utc_now`, and the only marestail import is `marestail.shell`.

29. `docs ok`. `timeline.record` returns the step, and a worker step has no `verdict` key. The coder's frozen list gains `.marestail/runs/t/events.jsonl` and still freezes the two timeline paths; `src/a.py` stays unfrozen. The old timeline-only input still returns only those two paths. `.importlinter` keeps `marestail.task_file` on the report layer, adds `marestail.events` there and under `foundations-import-nothing-above`, and `events-stays-below-runner` forbids the same modules as `timeline-stays-below-runner`. The Overnight sentence ending in the `nohup setsid` command is unchanged. The next paragraph is the events sentence from the feature, then the six-row field table, then the Kilo paragraph. `marestail/events.py` does not read the environment.

30. `pytest ok`. `.venv/bin/pytest tests/test_events.py tests/test_runner_flow.py tests/test_timeline.py -q --tb=line` exits 0.

```python
#!/usr/bin/env python3
import io
import json
import os
import re
import signal
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
PY = sys.executable
CLI = str(ROOT / "marestail" / "cli.py")
WORK = Path("/tmp/qa-020")
ENV_KEYS = (
    "MARESTAIL_AGENT",
    "MARESTAIL_NICE",
    "MARESTAIL_SCOPE",
    "MARESTAIL_FOCUS",
    "MARESTAIL_TASK",
    "MARESTAIL_DANDELION",
)
BASE = '[git]\nbase = "main"\n[perf]\nenabled = false\n[practices]\nenabled = false\n'
AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
EYE = "pipeline complete, NOT verified by eye"
VISUAL_BLOCK = "```visual\nroute: /page\nselector: #box\n```\n"
WAIT_SECONDS = 90
ARCHIVE = re.compile(r"^\.marestail/runs/t/handoffs-\d{8}T\d{6}(-\d+)?$")
KEYS = {
    "run-start": ["v", "at", "event", "pid", "task", "task_path", "branch", "base", "start_commit", "from", "to", "scope", "agent", "model", "effort"],
    "step-start": ["v", "at", "event", "pid", "step", "role", "kind", "attempt"],
    "step-end": ["v", "at", "event", "pid", "step", "role", "attempt", "outcome", "bounce_to", "head", "handoff", "seconds"],
    "wait": ["v", "at", "event", "pid", "reason", "until"],
    "proposal": ["v", "at", "event", "pid", "step", "path", "files"],
    "run-end": ["v", "at", "event", "pid", "result", "exit_code", "head", "stopped_at", "reason", "archive"],
}
HANDOFF = "# Handoff\n\n## What I did\n\ndone\n"
GUARD = {
    "specifier": "specifier got the same problems back 3 times in a row; the worker is not making progress, stopping for a human",
    "coder": "coder got the same problems back 3 times in a row; the worker is not making progress, stopping for a human",
}


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args!r}\n{result.stderr}")
    return result.stdout.strip()


def fresh(path):
    if path.exists():
        subprocess.run(["rm", "-rf", str(path)], check=True)
    path.mkdir(parents=True)
    return path


def make_repo(name, toml=BASE, task="# Add one\n", task_name="t.md"):
    root = fresh(WORK / name)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "qa@example.com")
    git(root, "config", "user.name", "QA")
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "marestail.toml").write_text(toml)
    (root / "tasks").mkdir()
    (root / "tasks" / task_name).write_text(task)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def commit_feature(root):
    path = root / "features" / "x.feature"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Feature: x\n")
    git(root, "add", "features/x.feature")
    git(root, "commit", "-q", "-m", "add feature")


def commit_toml(root):
    path = root / "marestail.toml"
    path.write_text(path.read_text() + "\n\n[events]\nkeep = false\n")
    git(root, "add", "marestail.toml")
    git(root, "commit", "-q", "-m", "edit toml")


def plus(stamp, seconds):
    parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def events_path(root, task="t"):
    return root / ".marestail" / "runs" / task / "events.jsonl"


def objects(path):
    rows = []
    for line in path.read_text().splitlines():
        if line == "not json":
            continue
        rows.append(json.loads(line))
    return rows


def assert_shape(path):
    text = path.read_text()
    assert text.endswith("\n") and "\n\n" not in text
    for line in text.splitlines():
        if line == "not json":
            continue
        obj = json.loads(line)
        assert json.dumps(obj) == line
        assert list(obj) == KEYS[obj["event"]]
        assert obj["v"] == 1 and obj["pid"] == os.getpid() and AT.fullmatch(obj["at"])


def marker(obj):
    if obj["event"] == "step-end":
        return obj["outcome"]
    if obj["event"] == "run-end":
        return obj["result"]
    return None


def sequence(rows):
    steps = {"step-start", "step-end", "proposal"}
    return [(row["event"], row["step"] if row["event"] in steps else None, marker(row)) for row in rows]


def write_report(state, label, text):
    path = state.handoffs / f"{label}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def child(args, cwd, env=None):
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(ROOT)
    for key in ENV_KEYS:
        merged.pop(key, None)
    if env:
        merged.update(env)
    return subprocess.run([PY, CLI, *args], cwd=cwd, env=merged, capture_output=True, text=True)


def run_check(name, fn):
    fn()
    print(f"{name} ok")


def drive(root, plan, **kw):
    import marestail.runner as runner
    import marestail.reported as reported

    task = kw.get("task") or (root / "tasks" / "t.md")
    raw = kw.get("raw")
    if raw is None:
        import tomllib
        raw = tomllib.loads((root / "marestail.toml").read_text())
    config = __import__("marestail.config", fromlist=["Config"]).Config(root=root, raw=raw)
    saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    saved = {
        "load": runner.config_module.load,
        "apply": runner.nice.apply,
        "invoke": runner.invoke,
        "gates": runner.run_gates,
        "audit": runner.audit.problems,
        "hunks": runner.hunks.problems,
        "backend": runner.run_backend,
        "elapsed": runner.elapsed,
        "require": runner.dandelion.require,
        "choose": runner.dandelion.choose,
        "wait": runner.LIMIT_WAIT_SECONDS,
        "waits": runner.LIMIT_WAITS,
        "sleep": runner.time.sleep,
        "rounds": runner.MAX_JUDGE_ROUNDS,
        "summary": runner.proposals_summary,
        "window": runner.window,
        "read": runner.task_file.read,
        "focus": runner.resolve_focus,
        "hook": runner.hook_focus,
        "reproduce": reported.visual.reproduce,
        "sigterm": signal.getsignal(signal.SIGTERM),
        "sigint": signal.getsignal(signal.SIGINT),
        "stdin": sys.stdin,
    }
    heads, prompts, order = [], [], []
    out, err = io.StringIO(), io.StringIO()

    def load(_start):
        return config

    def invoke(state, label, prompt, rebuild=None):
        prompts.append(prompt)
        if kw.get("boundary", True) and re.fullmatch(r"\d+-.+", label):
            last = objects(events_path(root, task.stem))[-1]
            assert (last["event"], last["step"]) == ("step-start", label)
        heads.append(git(root, "rev-parse", "HEAD"))
        plan(state, label, prompt, rebuild)
        state.attempt_agent = {
            "backend": "claude", "model": state.model or "claude", "effort": state.effort,
            "account": None, "minutes": 0.0, "summary": "ok",
        }

    def wrapped_summary(state):
        rows = objects(events_path(root, task.stem))
        assert rows[-1]["event"] == "run-end"
        if rows[-1]["result"] == "complete":
            assert "pipeline complete" in out.getvalue()
        order.append("summary")
        return saved["summary"](state)

    try:
        runner.config_module.load = load
        runner.nice.apply = lambda config=None: None
        if kw.get("invoke", True):
            runner.invoke = invoke
        if "gates" in kw:
            runner.run_gates = kw["gates"]
        if "audit" in kw:
            runner.audit.problems = kw["audit"]
        if "hunks" in kw:
            runner.hunks.problems = kw["hunks"]
        if "backend" in kw:
            runner.run_backend = kw["backend"]
        if "elapsed" in kw:
            runner.elapsed = kw["elapsed"]
        if "require" in kw:
            runner.dandelion.require = kw["require"]
        if "choose" in kw:
            runner.dandelion.choose = kw["choose"]
        if "wait" in kw:
            runner.LIMIT_WAIT_SECONDS = kw["wait"]
        if "waits" in kw:
            runner.LIMIT_WAITS = kw["waits"]
        if "sleep" in kw:
            runner.time.sleep = kw["sleep"]
        if "rounds" in kw:
            runner.MAX_JUDGE_ROUNDS = kw["rounds"]
        if "window" in kw:
            runner.window = kw["window"]
        if "read" in kw:
            runner.task_file.read = kw["read"]
        if "hook" in kw:
            runner.hook_focus = kw["hook"]
        if "resolve" in kw:
            runner.resolve_focus = kw["resolve"]
        if "reproduce" in kw:
            reported.visual.reproduce = kw["reproduce"]
        if "stdin" in kw:
            sys.stdin = kw["stdin"]
        if kw.get("watch_summary", True) and kw.get("invoke", True):
            runner.proposals_summary = wrapped_summary
        before = git(root, "rev-parse", "HEAD") if kw.get("git", True) else None
        value = exc = None
        with redirect_stdout(out), redirect_stderr(err):
            try:
                value = runner.run_pipeline(
                    task, kw.get("start", "specifier"), kw.get("stop", "critic"), kw.get("auto", True),
                    kw.get("model", "m"), kw.get("retries", 0), kw.get("agent", "claude"), kw.get("effort"),
                    kw.get("scope"), kw.get("focus"),
                )
            except BaseException as caught:
                exc = caught
        assert signal.getsignal(signal.SIGTERM) == saved["sigterm"]
        assert signal.getsignal(signal.SIGINT) == saved["sigint"]
        path = events_path(root, task.stem)
        rows = objects(path) if path.is_file() else None
        if rows is not None and kw.get("shape", True):
            assert_shape(path)
        return {
            "value": value, "out": out.getvalue(), "err": err.getvalue(), "exc": exc,
            "rows": rows, "heads": heads, "prompts": prompts, "before": before, "order": order,
        }
    finally:
        runner.config_module.load = saved["load"]
        runner.nice.apply = saved["apply"]
        runner.invoke = saved["invoke"]
        runner.run_gates = saved["gates"]
        runner.audit.problems = saved["audit"]
        runner.hunks.problems = saved["hunks"]
        runner.run_backend = saved["backend"]
        runner.elapsed = saved["elapsed"]
        runner.dandelion.require = saved["require"]
        runner.dandelion.choose = saved["choose"]
        runner.LIMIT_WAIT_SECONDS = saved["wait"]
        runner.LIMIT_WAITS = saved["waits"]
        runner.time.sleep = saved["sleep"]
        runner.MAX_JUDGE_ROUNDS = saved["rounds"]
        runner.proposals_summary = saved["summary"]
        runner.window = saved["window"]
        runner.task_file.read = saved["read"]
        runner.resolve_focus = saved["focus"]
        runner.hook_focus = saved["hook"]
        reported.visual.reproduce = saved["reproduce"]
        signal.signal(signal.SIGTERM, saved["sigterm"])
        signal.signal(signal.SIGINT, saved["sigint"])
        sys.stdin = saved["stdin"]
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def passing(root):
    def plan(state, label, prompt, rebuild=None):
        role = label.split("-", 1)[1]
        if role == "specifier":
            commit_feature(root)
            write_report(state, label, "# Specifier handoff\n\n## What I did\n\nwrote it\n")
        elif role == "critic":
            write_report(state, label, "VERDICT: PASS\n")
        else:
            raise AssertionError(label)
    return plan


def handoff_plan(text):
    def plan(state, label, prompt, rebuild=None):
        write_report(state, label, text)
    return plan


def join_timeline(root, rows, task="t"):
    doc = json.loads((root / ".marestail" / "runs" / task / "timeline.json").read_text())
    found = {(step["id"], step["attempt"]): step for step in doc["steps"]}
    for row in rows:
        if row["event"] not in {"step-start", "step-end"}:
            continue
        step = found[(row["step"], row["attempt"])]
        if row["event"] == "step-start":
            assert row["at"] == step["started_at"]
        else:
            assert row["at"] == step["ended_at"]
            started = datetime.strptime(step["started_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
            ended = datetime.strptime(step["ended_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
            assert row["seconds"] == round((ended - started).total_seconds(), 3)


def ends(rows):
    return [row for row in rows if row["event"] == "step-end"]


def help_ok():
    seen = child(["--help"])
    assert seen.returncode == 0
    assert "{gate,run,tasks,install,sonar,watch,perf,visual,route,graph,depth}" in seen.stdout
    run = child(["run", "--help"]).stdout
    flags = ["--from", "--to", "--auto", "--scope", "--focus", "--model", "--retries", "--effort", "--agent"]
    indexes = [run.index(flag) for flag in flags]
    assert indexes == sorted(indexes)
    assert "attempts per role; 0 means unlimited (default)" in run
    assert "skip the approval pause after the critic" in run
    root = fresh(WORK / "help-tasks")
    (root / "tasks").mkdir()
    (root / "tasks" / "t.md").write_text("+++\ndepends = []\n+++\n# t\n")
    checked = child(["tasks", "check", "tasks/t.md"], root)
    assert (checked.returncode, checked.stdout, checked.stderr) == (0, "", "")
    route = child(["route", "--help"], ROOT, {"PATH": "/usr/bin:/bin"})
    assert route.returncode == 127 and "dandelion is not installed" in route.stderr


def defaults_ok():
    import marestail.runner as runner
    root = fresh(WORK / "defaults")
    (root / "marestail.toml").write_text("[agent]\nmodel = \"cfg-model\"\neffort = \"cfg-effort\"\n")
    order = []
    real = {
        "steps": runner.run_steps,
        "pick": runner.pick_model,
        "apply": runner.nice.apply,
        "read": runner.task_file.read,
        "load": runner.config_module.load,
        "require": runner.dandelion.require,
        "resolve": runner.resolve_focus,
        "hook": runner.hook_focus,
    }

    def pick(*args, **kwargs):
        order.append("pick")
        return real["pick"](*args, **kwargs)

    def read(path):
        order.append("read")
        return runner.task_file.TaskFile(Path(path).stem, Path(path), (), None, "", False)

    def apply(_config=None):
        order.append("nice")

    def steps(state, window, auto):
        state.perf_changes = "perf changes"
        return 7

    saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    try:
        import tomllib
        from marestail.config import Config
        config = Config(root=root, raw=tomllib.loads((root / "marestail.toml").read_text()))
        runner.config_module.load = lambda _start: config
        runner.nice.apply = apply
        runner.task_file.read = read
        runner.pick_model = pick
        runner.run_steps = steps
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            value = runner.run_pipeline(Path("t.md"), "coder", "cleaner", True, None, 2)
        assert (value, out.getvalue(), err.getvalue()) == (7, "\nperf changes\n", "")
        assert order == ["nice", "read", "pick"]
        rows = objects(events_path(root, "t"))
        assert [row["event"] for row in rows] == ["run-start"]
        assert rows[0]["branch"] is None and isinstance(rows[0]["start_commit"], str)
        runner.run_steps = lambda state, window, auto: 7
        runner.dandelion.require = lambda: None
        runner.resolve_focus = lambda config, paths: set(paths)
        runner.hook_focus = lambda config: {"src/hook.py"}
        out2 = io.StringIO()
        with redirect_stdout(out2), redirect_stderr(io.StringIO()):
            runner.run_pipeline(Path("t.md"), None, None, False, "dandelion/route", 0, scope="changed", focus=[" ", "src/a.py"])
        assert out2.getvalue() == "\n"
        routed = [row for row in objects(events_path(root, "t")) if row["event"] == "run-start"][-1]
        assert (routed["model"], routed["agent"], routed["scope"]) == ("dandelion/route", None, "changed")
    finally:
        runner.run_steps = real["steps"]
        runner.pick_model = real["pick"]
        runner.nice.apply = real["apply"]
        runner.task_file.read = real["read"]
        runner.config_module.load = real["load"]
        runner.dandelion.require = real["require"]
        runner.resolve_focus = real["resolve"]
        runner.hook_focus = real["hook"]
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def pass_ok():
    root = make_repo("pass")
    before = git(root, "rev-parse", "HEAD")
    got = drive(root, passing(root))
    assert got["exc"] is None and got["value"] == 0 and got["err"] == ""
    rows = got["rows"]
    assert sequence(rows) == [
        ("run-start", None, None),
        ("step-start", "01-specifier", None),
        ("step-end", "01-specifier", "done"),
        ("step-start", "02-critic", None),
        ("step-end", "02-critic", "pass"),
        ("run-end", None, "complete"),
    ]
    start = rows[0]
    assert (start["task"], start["task_path"], start["branch"], start["base"], start["start_commit"]) == ("t", "tasks/t.md", "main", "main", before)
    assert (start["from"], start["to"], start["scope"], start["agent"], start["model"], start["effort"]) == ("specifier", "critic", "all", "claude", "m", None)
    spec, critic = ends(rows)
    started = [row for row in rows if row["event"] == "step-start"]
    assert (started[0]["kind"], started[0]["attempt"]) == ("worker", 1)
    assert (started[1]["kind"], started[1]["attempt"]) == ("judge", 1)
    assert (spec["handoff"], spec["bounce_to"]) == (".marestail/handoffs/t/01-specifier.md", None)
    assert (critic["handoff"], critic["bounce_to"]) == (".marestail/handoffs/t/02-critic.md", None)
    assert spec["head"] == got["heads"][1] == git(root, "log", "-1", "--format=%H", spec["head"])
    assert git(root, "log", "-1", "--format=%s", spec["head"]) == "[claude/m] add feature"
    assert git(root, "log", "-1", "--format=%s", critic["head"]) == "[claude/m] critic verdict: PASS"
    assert spec["head"] != critic["head"] == git(root, "rev-parse", "HEAD")
    finish = rows[-1]
    assert (finish["exit_code"], finish["stopped_at"], finish["reason"]) == (0, None, None)
    assert ARCHIVE.fullmatch(finish["archive"])
    archive = root / finish["archive"]
    assert (archive / "01-specifier.md").is_file() and (archive / "02-critic.md").is_file() and (archive / "start-commit").is_file()
    assert not (root / ".marestail" / "handoffs" / "t").exists()
    assert not (root / ".marestail" / "runs" / "t" / "start-commit").exists()
    assert events_path(root).is_file() and (root / ".marestail" / "runs" / "t" / "timeline.json").is_file()
    assert got["out"].endswith("pipeline complete\n\n")
    assert "== specifier (01-specifier) attempt 1" in got["out"] and "== critic (02-critic) attempt 1" in got["out"]
    assert "   verdict PASS" in got["out"] and "events.jsonl" not in got["out"] and "NOT verified" not in got["out"]
    assert got["order"] == ["summary"]
    join_timeline(root, rows)


def append_ok():
    root = make_repo("append")
    folder = root / ".marestail" / "runs" / "t"
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text("not json\n")
    first = drive(root, passing(root))
    second = drive(root, passing(root))
    assert first["exc"] is None and second["exc"] is None
    text = events_path(root).read_text()
    assert text.startswith("not json\n")
    starts = [row for row in objects(events_path(root)) if row["event"] == "run-start"]
    assert len(starts) == 2 and starts[0]["pid"] == starts[1]["pid"] == os.getpid()
    assert starts[0]["start_commit"] != starts[1]["start_commit"]
    assert_shape(events_path(root))


def bounce_ok():
    root = make_repo("bounce")
    seen = {"n": 0}

    def plan(state, label, prompt, rebuild=None):
        seen["n"] += 1
        role = label.split("-", 1)[1]
        if role == "critic" and seen["n"] == 2:
            write_report(state, label, "VERDICT: BOUNCE\n1. scenario missing\n")
        elif role == "critic":
            write_report(state, label, "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF)
    got = drive(root, plan)
    assert got["exc"] is None and got["value"] == 0
    assert sequence(got["rows"])[4] == ("step-end", "02-critic", "bounce")
    assert got["rows"][4]["bounce_to"] == "specifier"
    assert sequence(got["rows"])[-2:] == [("step-end", "04-critic", "pass"), ("run-end", None, "complete")]
    assert "   verdict BOUNCE to specifier" in got["out"]


def stop_ok():
    root = make_repo("stop")

    def plan(state, label, prompt, rebuild=None):
        role = label.split("-", 1)[1]
        body = "VERDICT: BOUNCE\n1. scenario missing\n" if role == "critic" else HANDOFF
        write_report(state, label, body)
    got = drive(root, plan)
    reason = "critic repeated the same findings twice; the worker is not making progress, stopping for a human"
    assert got["exc"] is None and got["value"] == 1
    assert sequence(got["rows"])[-3:] == [
        ("step-start", "04-critic", None), ("step-end", "04-critic", "bounce"), ("run-end", None, "stopped"),
    ]
    assert "05-specifier" not in events_path(root).read_text()
    end = got["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"]) == (1, "critic", reason)
    assert reason in got["out"] and "pipeline stopped at critic" in got["out"]


def guard_ok():
    root = make_repo("guard")
    got = drive(root, lambda *args: None, stop="specifier")
    assert got["value"] == 1 and got["exc"] is None
    pairs = [row for row in got["rows"] if row["event"] in {"step-start", "step-end"}]
    assert [row["attempt"] for row in pairs if row["event"] == "step-start"] == [1, 2, 3]
    assert [row["outcome"] for row in pairs if row["event"] == "step-end"] == ["failed", "failed", "stopped"]
    assert all(row["step"] == "01-specifier" and row["handoff"] is None for row in ends(got["rows"]))
    end = got["rows"][-1]
    assert (end["result"], end["stopped_at"], end["reason"], end["archive"]) == ("stopped", "specifier", GUARD["specifier"], None)
    assert "missing handoff .marestail/handoffs/t/01-specifier.md" in got["out"] and "used all" not in got["out"]
    assert (root / ".marestail" / "handoffs" / "t").is_dir()


def retries_ok():
    from marestail.report import Result
    fail = [Result("py.tests", False, "nope", [], 1.0)]

    def once(retries, plan, gates, outcomes, reason, complete=False, audit=None):
        root = make_repo(f"retries-{retries}-{complete}")
        extra = {"audit": audit} if audit is not None else {}
        got = drive(root, plan, start="coder", stop="coder", retries=retries, gates=gates, **extra)
        assert got["exc"] is None
        assert [row["outcome"] for row in ends(got["rows"])] == outcomes
        end = got["rows"][-1]
        if complete:
            assert end["result"] == "complete" and got["value"] == 0
        else:
            assert (end["result"], end["reason"], got["value"]) == ("stopped", reason, 1)
        return got

    silent = lambda *args: None
    once(1, silent, lambda *args: fail, ["stopped"], "coder used all 1 attempts")
    root = make_repo("retries-stdout")
    got = drive(root, silent, start="coder", stop="coder", retries=1, gates=lambda *args: fail)
    assert "GATE FAILED: py.tests" in got["out"] and "pipeline stopped at coder" in got["out"]
    assert "used all 1 attempts" not in got["out"]
    once(2, silent, lambda *args: fail, ["failed", "stopped"], "coder used all 2 attempts")
    once(3, silent, lambda *args: fail, ["failed", "failed", "stopped"], GUARD["coder"])
    calls = {"n": 0}

    def later(state, label, prompt, rebuild=None):
        calls["n"] += 1
        if calls["n"] == 2:
            write_report(state, label, HANDOFF)

    def gates(*args):
        return fail if calls["n"] < 2 else []

    once(2, later, gates, ["failed", "done"], None, complete=True, audit=lambda *args: [])


def proposal_ok():
    root = make_repo("proposal")
    seen = {"n": 0}

    def plan(state, label, prompt, rebuild=None):
        seen["n"] += 1
        role = label.split("-", 1)[1]
        if seen["n"] == 1:
            commit_toml(root)
            write_report(state, label, "# Specifier handoff\n\n## Config change\nAllow the events file.\n\n## What I did\n\nwrote it\n")
        elif role == "specifier":
            commit_feature(root)
            write_report(state, label, HANDOFF)
        else:
            write_report(state, label, "VERDICT: PASS\n")
    got = drive(root, plan)
    assert got["exc"] is None and got["value"] == 0
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-specifier", None),
        ("proposal", "01-specifier", None),
        ("step-end", "01-specifier", "failed"),
        ("step-start", "03-specifier", None),
        ("step-end", "03-specifier", "done"),
        ("step-start", "04-critic", None),
        ("step-end", "04-critic", "pass"),
        ("run-end", None, "complete"),
    ]
    proposal = got["rows"][2]
    assert (proposal["path"], proposal["files"]) == (".marestail/handoffs/t/02-proposal.md", ["marestail.toml"])
    failed = ends(got["rows"])[0]
    assert git(root, "log", "-1", "--format=%s", failed["head"]) == "[claude/m] Revert change to frozen files by 01-specifier, recorded as a proposal"
    sentence = "marestail.toml: frozen, reverted. Your reason was recorded as .marestail/handoffs/t/02-proposal.md"
    assert sentence in got["out"]
    assert got["order"] == ["summary"]
    assert got["out"].endswith("pipeline complete\n\n")
    assert "## Config changes the agents asked for and were refused" not in got["out"]
    assert (root / got["rows"][-1]["archive"] / "02-proposal.md").is_file()
    stopped = make_repo("proposal-stopped")

    def once(state, label, prompt, rebuild=None):
        commit_toml(stopped)
        write_report(state, label, "# Specifier handoff\n\n## Config change\nAllow the events file.\n\n## What I did\n\nwrote it\n")

    held = drive(stopped, once, stop="specifier", retries=1)
    assert held["exc"] is None and held["value"] == 1
    assert sequence(held["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-specifier", None),
        ("proposal", "01-specifier", None),
        ("step-end", "01-specifier", "stopped"),
        ("run-end", None, "stopped"),
    ]
    assert held["rows"][1]["attempt"] == 1
    assert (held["rows"][2]["path"], held["rows"][2]["files"]) == (".marestail/handoffs/t/02-proposal.md", ["marestail.toml"])
    subject = git(stopped, "log", "-1", "--format=%s", ends(held["rows"])[0]["head"])
    assert subject == "[claude/m] Revert change to frozen files by 01-specifier, recorded as a proposal"
    end = held["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"], end["archive"]) == (1, "specifier", "specifier used all 1 attempts", None)
    assert (stopped / ".marestail" / "handoffs" / "t" / "02-proposal.md").is_file()
    assert list((stopped / ".marestail" / "runs" / "t").glob("handoffs-*")) == []
    assert sentence in held["out"] and "used all" not in held["out"]
    assert held["out"].index("## Config changes the agents asked for and were refused") > held["out"].index("pipeline stopped at specifier")
    assert held["order"] == ["summary"]


def plain_ok():
    root = make_repo("plain")

    def committed(state, label, prompt, rebuild=None):
        commit_toml(root)
        write_report(state, label, HANDOFF)
    got = drive(root, committed, stop="specifier", retries=1)
    assert not any(row["event"] == "proposal" for row in got["rows"])
    subject = git(root, "log", "-1", "--format=%s", ends(got["rows"])[0]["head"])
    assert subject == "[claude/m] Revert change to frozen files by 01-specifier" and "recorded as a proposal" not in subject
    assert "marestail.toml is frozen for specifier; reverted. Work within the current configuration." in got["out"]
    dirty_root = make_repo("dirty")
    before = git(dirty_root, "rev-parse", "HEAD")

    def dirty(state, label, prompt, rebuild=None):
        path = dirty_root / "marestail.toml"
        path.write_text(path.read_text() + "\n\n[events]\nkeep = false\n")
        write_report(state, label, HANDOFF)
    got = drive(dirty_root, dirty, stop="specifier", retries=1)
    assert not any(row["event"] == "proposal" for row in got["rows"])
    assert git(dirty_root, "rev-parse", "HEAD") == before
    assert "marestail.toml is frozen for specifier" in got["out"] and "reverted" not in got["out"]


def skip_ok():
    rows = [
        ("practices", BASE, "practices disabled in marestail.toml; skipping"),
        ("practices", '[git]\nbase = "main"\n[perf]\nenabled = false\n', "practices: no guidance files; skipping"),
        ("design", BASE, "design: no pattern rulebooks; skipping"),
        ("perf", BASE, "perf disabled in marestail.toml; skipping"),
    ]
    for judge, toml, line in rows:
        root = make_repo(f"skip-{judge}-{len(toml)}", toml)
        got = drive(root, lambda *args: (_ for _ in ()).throw(AssertionError("invoked")), start=judge, stop=judge)
        assert got["exc"] is None and got["value"] == 0 and got["heads"] == []
        assert line in got["out"] and "pipeline complete" in got["out"] and "== " not in got["out"]
        assert sequence(got["rows"]) == [("run-start", None, None), ("run-end", None, "complete")]
        assert (got["rows"][0]["from"], got["rows"][0]["to"], got["rows"][-1]["archive"], got["rows"][-1]["exit_code"]) == (judge, judge, None, 0)
        assert not (root / ".marestail" / "runs" / "t" / "start-commit").exists()
        assert events_path(root).is_file()


def perf_ok():
    import marestail.runner as runner
    root = make_repo("perf", '[git]\nbase = "main"\n[practices]\nenabled = false\n')
    saved = (runner.perf_db.prepare, runner.perf_db.release)
    runner.perf_db.prepare = lambda *args: None
    runner.perf_db.release = lambda *args: None

    def plan(state, label, prompt, rebuild=None):
        if label == "perf-author-1":
            last = objects(events_path(root))[-1]
            assert last["event"] == "step-start" and last["step"].endswith("-perf")
            return
        if label == "01-perf":
            write_report(state, label, "VERDICT: AUTHOR\n")
        elif label == "02-perf":
            write_report(state, label, "VERDICT: PASS\n")
        else:
            raise AssertionError(label)
    try:
        got = drive(root, plan, start="perf", stop="perf", retries=2)
    finally:
        runner.perf_db.prepare, runner.perf_db.release = saved
    assert got["exc"] is None and got["value"] == 0
    assert got["out"].count("   perf-author-1: benches unchanged") == 2
    assert "== perf (perf-author-1)" not in got["out"]
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-perf", None),
        ("step-end", "01-perf", "failed"),
        ("step-start", "02-perf", None),
        ("step-end", "02-perf", "pass"),
        ("run-end", None, "complete"),
    ]
    failed, passed = ends(got["rows"])
    assert failed["head"] == got["rows"][0]["start_commit"]
    assert passed["head"] != failed["head"]
    assert (got["rows"][0]["from"], got["rows"][0]["to"]) == ("perf", "perf")


def rework_ok():
    root = make_repo("rework")

    def plan(state, label, prompt, rebuild=None):
        if "hardener" in label:
            write_report(state, label, "VERDICT: BOUNCE\n1. same problem\n")
    got = drive(root, plan, start="hardener", stop="hardener", gates=lambda *args: [], audit=lambda *args: [])
    assert got["exc"] is None and got["value"] == 1
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-hardener", None),
        ("step-end", "01-hardener", "bounce"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "failed"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "failed"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "stopped"),
        ("run-end", None, "stopped"),
    ]
    assert got["rows"][2]["bounce_to"] == "coder"
    assert all(row["role"] == "coder" for row in got["rows"] if row.get("step") == "02-coder")
    end = got["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"]) == (1, "hardener", GUARD["coder"])
    assert "pipeline stopped at hardener" in got["out"] and GUARD["coder"] in got["out"]


def gate_ok():
    from marestail.report import Result
    root = make_repo("gate")
    order = []

    def gates(tier, *args):
        written = events_path(root).read_text() if events_path(root).is_file() else ""
        order.append((tier, "step-start" in written))
        if tier == "full":
            return [Result("py.tests", False, "nope", [], 1.0)]
        return []

    def plan(state, label, prompt, rebuild=None):
        if "hardener" in label:
            write_report(state, label, "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF)
    got = drive(root, plan, start="hardener", stop="hardener", gates=gates, audit=lambda *args: [])
    reason = "hardener repeated the same findings twice; the worker is not making progress, stopping for a human"
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-hardener", None),
        ("step-end", "01-hardener", "bounce"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "done"),
        ("step-start", "03-hardener", None),
        ("step-end", "03-hardener", "bounce"),
        ("run-end", None, "stopped"),
    ]
    assert got["rows"][2]["bounce_to"] == "coder" and "gate" not in got["rows"][2]
    assert got["rows"][-1]["reason"] == reason and got["value"] == 1
    doc = json.loads((root / ".marestail" / "runs" / "t" / "timeline.json").read_text())
    hardener = [step for step in doc["steps"] if step["role"] == "hardener"]
    assert hardener[0]["gate"] == [{"name": "py.tests", "seconds": 1.0, "ok": False}]
    assert order[0] == ("full", False)
    assert "   verdict BOUNCE" in got["out"] and "GATE FAILED" not in got["out"]


def nonworker_ok():
    root = make_repo("nonworker")
    got = drive(root, handoff_plan("VERDICT: BOUNCE hardener\n1. not a worker\n"), start="critic", stop="critic")
    assert ends(got["rows"])[0]["bounce_to"] == "hardener"
    end = got["rows"][-1]
    assert (end["result"], end["exit_code"], end["stopped_at"], end["reason"]) == (
        "stopped", 1, "critic", "critic bounced to hardener, which is not a worker",
    )
    assert "   verdict BOUNCE to hardener" in got["out"] and "pipeline stopped at critic" in got["out"]
    assert "which is not a worker" not in got["out"]


def rounds_ok():
    import marestail.runner as runner
    root = make_repo("rounds")
    seen = {"n": 0}

    def plan(state, label, prompt, rebuild=None):
        if "critic" in label:
            seen["n"] += 1
            letter = "A" if seen["n"] == 1 else "B"
            write_report(state, label, f"VERDICT: BOUNCE\n1. finding {letter}\n")
        else:
            write_report(state, label, HANDOFF)
    got = drive(root, plan, start="critic", stop="critic", rounds=2)
    assert runner.MAX_JUDGE_ROUNDS == 1000
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-critic", None), ("step-end", "01-critic", "bounce"),
        ("step-start", "02-specifier", None), ("step-end", "02-specifier", "done"),
        ("step-start", "03-critic", None), ("step-end", "03-critic", "bounce"),
        ("step-start", "04-specifier", None), ("step-end", "04-specifier", "done"),
        ("run-end", None, "stopped"),
    ]
    assert got["rows"][-1]["reason"] == "critic ran 2 rounds without a pass"
    assert "pipeline stopped at critic" in got["out"] and "ran 2 rounds" not in got["out"] and "ran 1000 rounds" not in got["out"]


def cap_ok():
    import marestail.runner as runner
    root = make_repo("cap")
    real = runner.window

    def window(start, stop, mode=None, visual=False):
        return [replace(step, bounces=1) if step.name == "critic" else step for step in real(start, stop, mode, visual)]

    seen = {"n": 0}

    def plan(state, label, prompt, rebuild=None):
        if "critic" in label:
            seen["n"] += 1
            write_report(state, label, f"VERDICT: BOUNCE\n1. finding {'A' if seen['n'] == 1 else 'B'}\n")
        else:
            write_report(state, label, HANDOFF)
    got = drive(root, plan, start="critic", stop="critic", window=window)
    assert sequence(got["rows"])[-2:] == [("step-end", "03-critic", "bounce"), ("run-end", None, "stopped")]
    assert "04-specifier" not in events_path(root).read_text()
    reason = "critic still bouncing after 1 rounds; stopping for a human"
    assert got["rows"][-1]["reason"] == reason and reason in got["out"]


def noverdict_ok():
    root = make_repo("noverdict")
    seen = {"c": 0}

    def plan(state, label, prompt, rebuild=None):
        if "critic" in label:
            seen["c"] += 1
            if seen["c"] > 1:
                write_report(state, label, "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF)
    got = drive(root, plan, start="critic", stop="critic", retries=1)
    failed = ends(got["rows"])[0]
    assert (failed["step"], failed["outcome"], failed["handoff"], failed["head"]) == ("01-critic", "failed", None, got["rows"][0]["start_commit"])
    assert sequence(got["rows"])[3] == ("step-start", "02-specifier", None)
    assert "critic wrote no verdict; retrying" in got["out"]
    assert all("produced no verdict" not in json.dumps(row) for row in got["rows"])
    assert "critic produced no verdict after 1 attempts" in got["prompts"][1]
    assert got["rows"][-1]["result"] == "complete" and got["value"] == 0


class Tty(io.StringIO):
    def __init__(self, text, tty):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


def approval_ok():
    root = make_repo("approval-no")
    got = drive(root, handoff_plan("VERDICT: PASS\n"), start="critic", stop="critic", auto=False, stdin=Tty("n\n", True))
    assert got["value"] == 1 and got["exc"] is None
    assert sequence(got["rows"]) == [
        ("run-start", None, None), ("step-start", "01-critic", None), ("step-end", "01-critic", "pass"), ("run-end", None, "stopped"),
    ]
    end = got["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"]) == (1, "critic", "not approved")
    assert "continue to coder? [y/N] " in got["out"] and "not approved" not in got["out"] and "pipeline stopped" not in got["out"]
    root = make_repo("approval-yes")
    got = drive(root, handoff_plan("VERDICT: PASS\n"), start="critic", stop="critic", auto=False, stdin=Tty("", False))
    assert "non-interactive: continuing without approval (use --to critic to stop here)" in got["out"]
    assert "pipeline complete" in got["out"] and got["rows"][-1]["result"] == "complete" and got["value"] == 0
    assert got["rows"][-1]["reason"] is None


def qa_ok():
    cases = [
        ("ran-against: app\n", 0, None),
        ("ran-against: harness\n", 3, "pipeline complete, NOT verified against the running app (qa ran against a harness)"),
        ("ran-against: nothing\n", 3, "pipeline complete, NOT verified against the running app (qa ran against nothing)"),
    ]
    for text, code, reason in cases:
        root = make_repo(f"qa-{code}-{reason is None}")
        got = drive(root, handoff_plan(text), start="qa", stop="qa", gates=lambda *args: [])
        end = got["rows"][-1]
        assert got["exc"] is None and got["value"] == code
        assert (end["result"], end["exit_code"], end["reason"]) == ("complete", code, reason)
        if reason is None:
            assert "pipeline complete" in got["out"] and "NOT verified" not in got["out"]
        else:
            assert reason in got["out"]
    limited = lambda *args: (1, "rate limit exceeded")

    def visual_plan(qa=None):
        def plan(state, label, prompt, rebuild=None):
            role = label.split("-", 1)[1]
            if role == "visual":
                write_report(state, label, "VERDICT: PASS\n")
            elif role == "qa":
                write_report(state, label, qa)
            else:
                raise AssertionError(label)
        return plan

    def visual_repo(name):
        root = make_repo(name, BASE + "[visual]\nenabled = true\n")
        (root / "qa").mkdir()
        (root / "qa" / "t.md").write_text(VISUAL_BLOCK)
        git(root, "add", "qa/t.md")
        git(root, "commit", "-q", "-m", "qa")
        return root

    def eye(name, plan, stop, reason, eye_on_stdout):
        got = drive(
            visual_repo(name), plan, start="visual", stop=stop, agent="kilo", model=None,
            gates=lambda *args: [], backend=limited,
        )
        end = got["rows"][-1]
        assert got["exc"] is None and got["value"] == 3
        assert (end["result"], end["exit_code"], end["stopped_at"], end["reason"]) == ("complete", 3, None, reason)
        if eye_on_stdout:
            assert EYE in got["out"]
        else:
            assert "NOT verified by eye" not in got["out"] and reason in got["out"]
        return got

    seen = eye("eye-visual", visual_plan(), "visual", EYE, True)
    assert "visual: claude-fable-5-1 is out of usage; judging with kilo" in seen["out"]
    assert "   verdict PASS (geometry only, pictures not seen)" in seen["out"]
    eye("eye-app", visual_plan("ran-against: app\n"), "qa", EYE, True)
    harness = "pipeline complete, NOT verified against the running app (qa ran against a harness)"
    eye("eye-harness", visual_plan("ran-against: harness\n"), "qa", harness, False)


def wait_ok():
    from marestail.route import Choice
    pause = {"wait": WAIT_SECONDS, "waits": 12, "sleep": lambda _seconds: None}
    root = make_repo("wait-once")
    calls = {"n": 0}

    def backend(state, name, prompt, prompt_file):
        calls["n"] += 1
        if calls["n"] == 1:
            return 1, "rate limit exceeded"
        return 0, "ok"
    got = drive(root, None, invoke=False, stop="specifier", retries=1, backend=backend, boundary=False, **pause)
    waits = [row for row in got["rows"] if row["event"] == "wait"]
    assert len(waits) == 1 and waits[0]["reason"] == "rate-limit"
    assert waits[0]["until"] == plus(waits[0]["at"], WAIT_SECONDS) and waits[0]["until"] != waits[0]["at"]
    indexes = [row["event"] for row in got["rows"]]
    assert indexes.index("wait") > indexes.index("step-start") and indexes.index("wait") < indexes.index("step-end")
    assert "   rate limited; waiting 1 min before retrying 01-specifier" in got["out"]
    root = make_repo("wait-twelve")
    got = drive(root, None, invoke=False, stop="specifier", retries=1, backend=lambda *args: (1, "rate limit exceeded"), boundary=False, **pause)
    waits = [row for row in got["rows"] if row["event"] == "wait"]
    assert len(waits) == 12 and all(row["until"] == plus(row["at"], WAIT_SECONDS) for row in waits)
    assert got["rows"][-1]["reason"] == "specifier used all 1 attempts"
    assert "   01-specifier: still rate limited after 12 waits" in got["out"]
    root = make_repo("wait-route")
    chosen = {"n": 0}

    def choose(mode, folder):
        chosen["n"] += 1
        if chosen["n"] == 1:
            return None, "no subscription has quota left"
        return Choice("claude-opus-5 high claude", "claude", "claude-opus-5", "high", {}), ""
    got = drive(
        root, None, invoke=False, stop="specifier", retries=1, model="dandelion/route", agent=None,
        require=lambda: None, choose=choose, backend=lambda *args: (0, "ok"), boundary=False, **pause,
    )
    start = got["rows"][0]
    assert (start["agent"], start["model"], start["effort"]) == (None, "dandelion/route", None)
    waits = [row for row in got["rows"] if row["event"] == "wait"]
    assert len(waits) == 1 and waits[0]["reason"] == "dandelion-unrouted"
    assert waits[0]["until"] == plus(waits[0]["at"], WAIT_SECONDS)
    assert "   dandelion/route: no subscription has quota left; waiting 1 min before retrying 01-specifier" in got["out"]


def session_ok():
    root = make_repo("session")

    def backend(state, name, prompt, prompt_file):
        label = prompt_file.name.removesuffix(".prompt.md")
        role = label.split("-", 1)[1]
        if role == "specifier":
            commit_feature(root)
            write_report(state, label, HANDOFF)
        else:
            write_report(state, label, "VERDICT: PASS\n")
        return 0, "ok"
    got = drive(root, None, invoke=False, backend=backend, elapsed=lambda started: 0, boundary=False, watch_summary=False)
    assert got["exc"] is None and got["err"] == "" and got["value"] == 0
    assert "   01-specifier finished in 0.0 min: ok" in got["out"] and "pipeline complete" in got["out"]


def dir_ok():
    root = make_repo("dir-a")
    first = drive(root, passing(root), elapsed=lambda started: 0)
    other = make_repo("dir-b")
    path = events_path(other)
    path.parent.mkdir(parents=True)
    path.mkdir()
    second = drive(other, passing(root and other), elapsed=lambda started: 0, boundary=False, shape=False, watch_summary=False)
    assert first["out"] == second["out"] and first["value"] == second["value"] == 0
    assert second["err"].count("\n") == 1
    assert second["err"].startswith("warning: cannot write run events to ") and "events.jsonl" in second["err"]
    third = drive(other, passing(other), elapsed=lambda started: 0, boundary=False, shape=False, watch_summary=False)
    assert third["err"].count("\n") == 1 and third["err"].startswith("warning: cannot write run events to ")


def signals_ok():
    cases = [
        (KeyboardInterrupt(), KeyboardInterrupt, "interrupted", 130, "SIGINT"),
        ("term", SystemExit, "interrupted", 143, "SIGTERM"),
        (RuntimeError("boom"), RuntimeError, "failed", 1, "RuntimeError: boom"),
        (RuntimeError("boom\nnext"), RuntimeError, "failed", 1, "RuntimeError: boom"),
        (SystemExit(4), SystemExit, "failed", 4, "SystemExit: 4"),
        (SystemExit("nope"), SystemExit, "failed", 1, "SystemExit: nope"),
        (SystemExit(), SystemExit, "failed", 0, "SystemExit: "),
    ]
    for raised, kind, result, code, reason in cases:
        root = make_repo(f"sig-{code}-{reason.split()[0]}")

        def plan(state, label, prompt, rebuild=None, raised=raised):
            if raised == "term":
                try:
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                except BaseException as caught:
                    assert not isinstance(caught, Exception)
                    assert not isinstance(caught, (SystemExit, KeyboardInterrupt))
                    raise
            raise raised
        got = drive(root, plan, stop="specifier")
        assert isinstance(got["exc"], kind)
        if raised == "term":
            assert got["exc"].code == 143
        events = [row["event"] for row in got["rows"]]
        assert "step-end" not in events and events[1] == "step-start" and events[-1] == "run-end"
        end = got["rows"][-1]
        assert (end["result"], end["exit_code"], end["stopped_at"], end["reason"]) == (result, code, "specifier", reason)
    root = make_repo("sig-rework")

    def plan(state, label, prompt, rebuild=None):
        if "hardener" in label:
            write_report(state, label, "VERDICT: BOUNCE\n1. same problem\n")
        else:
            raise KeyboardInterrupt
    got = drive(root, plan, start="hardener", stop="hardener", gates=lambda *args: [], audit=lambda *args: [])
    assert isinstance(got["exc"], KeyboardInterrupt)
    assert ("step-end", "01-hardener", "bounce") in sequence(got["rows"])
    assert ("step-start", "02-coder", None) in sequence(got["rows"])
    assert not any(row["event"] == "step-end" and row["step"] == "02-coder" for row in got["rows"])
    assert got["rows"][-1]["stopped_at"] == "hardener" and got["rows"][-1]["reason"] == "SIGINT"


def repro_ok():
    root = make_repo("repro", '[git]\nbase = "main"\n[visual]\nenabled = true\n', "where: /page\nsymptom: the box is square\n")
    called = {"n": 0}

    def reproduce(config, report):
        called["n"] += 1
        return None
    got = drive(root, lambda *args: (_ for _ in ()).throw(AssertionError("invoked")), stop="specifier", reproduce=reproduce)
    reason = "pipeline stopped before specifier: the bug could not be reproduced"
    assert called["n"] == 1 and got["exc"] is None and got["value"] == 1
    assert sequence(got["rows"]) == [("run-start", None, None), ("run-end", None, "stopped")]
    assert (got["rows"][-1]["exit_code"], got["rows"][-1]["stopped_at"], got["rows"][-1]["reason"]) == (1, "specifier", reason)
    assert reason in got["out"] and "pipeline stopped at" not in got["out"] and "pipeline complete" not in got["out"]


def scope_ok():
    def one(name, **kw):
        root = kw.pop("root", None) or make_repo(name, kw.pop("toml", BASE))
        got = drive(root, handoff_plan(HANDOFF), stop="specifier", **kw)
        assert got["exc"] is None, got["exc"]
        return got["rows"][0]

    assert (one("scope-all")["scope"], one("scope-all2")["agent"], one("scope-all3")["model"], one("scope-all4")["branch"]) == ("all", "claude", "m", "main")
    assert one("scope-changed", scope="changed")["scope"] == "changed"
    assert one("scope-focus", focus=["src/a.py"], model="m")["scope"] == "changed"
    assert one("scope-hard", scope="hard", focus=["src/a.py"])["scope"] == "hard"
    assert one("scope-hyper", scope="hyper")["scope"] == "hyper"
    assert one("scope-effort", effort="low")["effort"] == "low"
    assert (one("scope-nomodel", model=None)["model"], one("scope-nomodel2", model=None)["agent"]) == (None, "claude")
    toml = BASE + "[agent]\nmodel = \"from-toml\"\n"
    assert one("scope-toml-model", toml=toml, model=None)["model"] == "from-toml"
    toml = BASE + "[agent]\nbackend = \"grok\"\n"
    assert one("scope-backend", toml=toml, agent=None)["agent"] == "grok"
    toml = BASE + "[agent]\neffort = \"high\"\n"
    assert one("scope-toml-effort", toml=toml)["effort"] == "high"
    root = make_repo("scope-detach")
    git(root, "checkout", "--detach")
    assert one("scope-detach", root=root)["branch"] is None
    assert one("scope-base", toml="[perf]\nenabled = false\n[practices]\nenabled = false\n")["base"] == "origin/master"
    root = make_repo("scope-out")
    outside = Path("/tmp/qa-020-outside.md")
    outside.write_text("# Outside\n")
    got = drive(root, handoff_plan(HANDOFF), stop="specifier", task=outside)
    assert Path(got["rows"][0]["task_path"]) == outside.resolve() and Path(got["rows"][0]["task_path"]).is_absolute()
    root = make_repo("scope-hyper-all")

    def plan(state, label, prompt, rebuild=None):
        role = label.split("-", 1)[1]
        if role == "qa":
            write_report(state, label, "ran-against: app\n")
        elif role in {"critic", "blast", "hardener"}:
            write_report(state, label, "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF)
    got = drive(root, plan, start=None, stop=None, scope="hyper", gates=lambda *args: [], audit=lambda *args: [], hunks=lambda *args: [])
    assert (got["rows"][0]["from"], got["rows"][0]["to"], got["rows"][0]["scope"]) == ("specifier", "qa", "hyper")
    roles = [row["role"] for row in got["rows"] if row["event"] == "step-end"]
    assert roles == ["specifier", "critic", "coder", "architect", "blast", "hardener", "qa"]
    missing = make_repo("scope-visual-missing")
    seen = child(["run", "tasks/t.md", "--from", "visual", "--to", "visual", "--auto", "--model", "m", "--agent", "claude"], missing)
    assert seen.returncode == 1 and seen.stdout == ""
    assert seen.stderr == "unknown role visual; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n"
    assert not events_path(missing).exists()
    present = make_repo("scope-visual", BASE + "[visual]\nenabled = true\n")
    got = drive(present, lambda *args: (_ for _ in ()).throw(AssertionError("invoked")), start="visual", stop="visual")
    assert "visual: no qa/t.md; skipping" in got["out"]
    assert (got["rows"][0]["from"], got["rows"][0]["to"]) == ("visual", "visual")
    assert [row["event"] for row in got["rows"]] == ["run-start", "run-end"]


def early_ok():
    root = make_repo("early")
    cases = [
        (["run", "tasks/t.md", "--model", "dandelion/route", "--agent", "grok"], None,
         "--model dandelion/route picks the backend and effort before every session; drop --agent and --effort\n"),
        (["run", "tasks/t.md", "--model", "m", "--agent", "claude"], {"MARESTAIL_NICE": "high"},
         "nice must be an integer 0-19, got 'high'\n"),
        (["run", "tasks/t.md", "--model", "dandelion/route", "--agent", "grok"], {"MARESTAIL_NICE": "high"},
         "nice must be an integer 0-19, got 'high'\n"),
        (["run", "tasks/t.md", "--scope", "hard", "--model", "m", "--agent", "claude"], None,
         "--scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml\n"),
        (["run", "tasks/t.md", "--scope", "all", "--focus", "src/a.py", "--model", "m", "--agent", "claude"], None,
         "--focus cannot be combined with --scope all\n"),
        (["run", "tasks/t.md", "--scope", "hyper", "--focus", "src/a.py", "--model", "m", "--agent", "claude"], None,
         "--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else\n"),
        (["run", "tasks/t.md", "--focus", "src/missing", "--model", "m", "--agent", "claude"], None,
         f"focus path not found under {root.resolve()}: src/missing\n"),
    ]
    for args, env, stderr in cases:
        seen = child(args, root, env)
        assert (seen.returncode, seen.stdout, seen.stderr) == (1, "", stderr)
        assert not events_path(root).exists()
    bad = root / "tasks" / "bad.md"
    bad.write_text('+++\ndepends = ["018-a", "019-b"]\nstack = true\n+++\n# x\n')
    seen = child(["run", "tasks/bad.md", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1 and seen.stdout == ""
    assert seen.stderr == "tasks/bad.md: stack = true needs exactly one dependency, got 2\n"
    seen = child(["run", "tasks/missing.md", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1 and "FileNotFoundError" in seen.stderr and "tasks/missing.md" in seen.stderr
    assert not events_path(root, "missing").exists()
    seen = child(["run", "tasks/t.md", "--scope", "hyper", "--from", "cleaner", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1 and seen.stdout == ""
    assert seen.stderr == "unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa\n"
    assert (root / ".marestail" / "runs" / "t" / "start-commit").is_file()
    assert not events_path(root).exists()
    seen = child(["run", "tasks/t.md", "--from", "nope", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1 and seen.stdout == ""
    assert seen.stderr == "unknown role nope; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n"


def module_ok():
    import ast
    import marestail.events as events
    folder = fresh(WORK / "module")
    path = folder / "events.jsonl"
    log = events.Log(path)
    at = "2026-09-28T14:03:07.412Z"
    log.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    line = '{"v": 1, "at": "2026-09-28T14:03:07.412Z", "event": "wait", "pid": 4, "reason": "rate-limit", "until": "2026-09-28T14:03:07.412Z"}\n'
    assert path.read_text() == line
    log.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    assert path.read_text() == line + line
    other = folder / "null.jsonl"
    events.Log(other).append(
        "run-end",
        {"result": "failed", "exit_code": 1, "head": "abc", "stopped_at": None, "reason": None, "archive": None},
        at, 4,
    )
    assert '"stopped_at": null' in other.read_text()
    calls = []
    real = events.os

    def open_file(file, flags, mode=0o777):
        calls.append(("open", flags, mode))
        return 7

    def write(fd, data):
        calls.append(("write", fd, data))
        return len(data)

    def fsync(fd):
        calls.append(("fsync", fd))

    def close(fd):
        calls.append(("close", fd))

    events.os.open, events.os.write, events.os.fsync, events.os.close = open_file, write, fsync, close
    try:
        events.Log(folder / "flags.jsonl").append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    finally:
        events.os.open, events.os.write, events.os.fsync, events.os.close = real.open, real.write, real.fsync, real.close
    flags, mode = calls[0][1], calls[0][2]
    assert flags & real.O_WRONLY and flags & real.O_APPEND and flags & real.O_CREAT and mode == 0o644
    assert calls[1][0] == "write" and calls[1][2] == line.encode()
    assert ("fsync", 7) in [(item[0], item[1]) for item in calls[1:]] and ("close", 7) in [(item[0], item[1]) for item in calls]
    directory = folder / "blocked"
    directory.mkdir()
    warned = io.StringIO()
    with redirect_stderr(warned):
        blocked = events.Log(directory)
        blocked.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
        blocked.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
        events.Log(directory).append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    assert warned.getvalue().count("warning: cannot write run events to ") == 2
    source = (ROOT / "marestail" / "events.py").read_text()
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert [name for name in imported if name.startswith("marestail")] == ["marestail.shell"]
    assert "datetime" not in source and "utc_now" not in source and "time." not in source and "os.environ" not in source


def docs_ok():
    import marestail.freeze as freeze
    import marestail.timeline as timeline
    from marestail.config import Config
    folder = fresh(WORK / "timeline")
    agent = {"backend": "claude", "model": "m", "effort": None, "account": None, "minutes": 0.1, "summary": "ok"}
    returned = timeline.record(folder, "t", "07-coder", "coder", 1, "2026-01-01T00:00:00.000Z", [], [], agent, None, [], [], folder / "missing")
    data = json.loads((folder / "timeline.json").read_text())
    assert returned == data["steps"][0] and "verdict" not in returned and returned["ended_at"] == data["steps"][0]["ended_at"]
    config = Config(root=ROOT, raw={})
    old = [".marestail/runs/t/timeline.md", ".marestail/runs/t/timeline.json", "src/a.py", ".marestail/runs/t/19-coder.md"]
    assert freeze.frozen_paths(config, "coder", old) == old[:2]
    paths = old[:2] + [".marestail/runs/t/events.jsonl", "src/a.py"]
    assert freeze.frozen_paths(config, "coder", paths) == old[:2] + [".marestail/runs/t/events.jsonl"]
    text = (ROOT / ".importlinter").read_text()
    layer = next(line for line in text.splitlines() if "marestail.report : marestail.config : marestail.changes : marestail._location" in line)
    assert "marestail.events" in layer and "marestail.task_file" in layer

    def contract(name, which):
        body = text.split(f"[importlinter:contract:{name}]", 1)[1].split("\n[", 1)[0]
        modules = []
        taking = False
        for line in body.splitlines():
            if line.split("=")[0].strip() in {"source_modules", "forbidden_modules"}:
                taking = line.split("=")[0].strip() == which
                continue
            if not line.startswith("    "):
                taking = False
            elif taking:
                modules.append(line.strip())
        return modules

    assert "marestail.events" in contract("foundations-import-nothing-above", "source_modules")
    assert set(contract("events-stays-below-runner", "forbidden_modules")) == set(contract("timeline-stays-below-runner", "forbidden_modules"))
    readme = (ROOT / "README.md").read_text()
    anchor = "Start it detached: `nohup setsid tools/overnight.sh ... > /dev/null 2>&1 &`."
    assert anchor in readme
    rest = readme.split(anchor, 1)[1].lstrip("\n")
    assert rest.startswith("`.marestail/runs/<task>/events.jsonl` gets one JSON line per run start, attempt start, attempt end, wait, proposal and run end")
    block = rest.split("Kilo Code pipeline runs", 1)[0]
    for event in ("run-start", "step-start", "step-end", "wait", "proposal", "run-end"):
        assert f"`{event}`" in block
    for field in ("start_commit", "kind", "bounce_to", "until", "files", "stopped_at"):
        assert field in block
    assert "MARESTAIL_" not in (ROOT / "marestail" / "events.py").read_text()


def pytest_ok():
    result = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "pytest"), "tests/test_events.py", "tests/test_runner_flow.py", "tests/test_timeline.py", "-q", "--tb=line"],
        cwd=ROOT,
    )
    assert result.returncode == 0


if __name__ == "__main__":
    run_check("help", help_ok)
    run_check("defaults", defaults_ok)
    run_check("pass", pass_ok)
    run_check("append", append_ok)
    run_check("bounce", bounce_ok)
    run_check("stop", stop_ok)
    run_check("guard", guard_ok)
    run_check("retries", retries_ok)
    run_check("proposal", proposal_ok)
    run_check("plain", plain_ok)
    run_check("skip", skip_ok)
    run_check("perf", perf_ok)
    run_check("rework", rework_ok)
    run_check("gate", gate_ok)
    run_check("nonworker", nonworker_ok)
    run_check("rounds", rounds_ok)
    run_check("cap", cap_ok)
    run_check("noverdict", noverdict_ok)
    run_check("approval", approval_ok)
    run_check("qa", qa_ok)
    run_check("wait", wait_ok)
    run_check("session", session_ok)
    run_check("dir", dir_ok)
    run_check("signals", signals_ok)
    run_check("repro", repro_ok)
    run_check("scope", scope_ok)
    run_check("early", early_ok)
    run_check("module", module_ok)
    run_check("docs", docs_ok)
    run_check("pytest", pytest_ok)
```
