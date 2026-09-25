#!/usr/bin/env python3
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import freeze, runner
from marestail.config import Config
from marestail.perf import db as perf_db
from marestail.perf import hygiene, results, samples, settings, table
from marestail.perf import image as perf_image
from marestail.perf import review as perf_review
from marestail.perf import trees as perf_trees
from marestail.pipeline import Judge, find, names
from marestail.runner import Run, run_judge, run_step

CLI = Path(__file__).resolve().parent.parent / "marestail" / "cli.py"
BENCH = (
    "#!/usr/bin/env python3\n"
    "import json, os\n"
    'value = {"pre-marestail": 8, "baseline": 10, "head": 20}[os.environ["MARESTAIL_PERF_TREE"]]\n'
    'print(json.dumps({"target": "add_one", "unit": "ms", "better": "lower", "values": [value] * 200}))\n'
)
POLICY = results.Policy()
TABLE_WITH_ROW = "# Performance\n\n| Task | Commit | Date | Rows |\n|---|---|---|---|\n| t | abc1234 | 2026-09-13 | — |\n"
ALL_TREES = ("baseline", "head", "pre-marestail")


def expect(name: str, got: Any, wanted: Any) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def new_repo(tmp: str, readme_first: bool = False) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    if readme_first:
        (root / "README.md").write_text("# repo\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "readme")
    (root / "marestail.toml").write_text('[git]\nbase = "main"\n')
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "src.py").write_text("original\n")
    (root / "tasks").mkdir()
    (root / "tasks" / "t.md").write_text("# Add one\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def state(root: Path, raw: dict[str, Any] | None = None, retries: int = 1) -> Run:
    return Run(
        config=Config(root=root, raw=raw or {"git": {"base": "main"}}),
        task=root / "tasks" / "t.md",
        model=None,
        retries=retries,
        agent="claude",
    )


def worktree_count(root: Path) -> int:
    return git(root, "worktree", "list", "--porcelain").count("worktree ")


def commit_change(root: Path, content: str) -> None:
    (root / "src.py").write_text(content)
    git(root, "commit", "-qam", "change")


def verdict_file(prompt: str) -> Path:
    return Path(cast(re.Match[str], re.search(r"Write your verdict to (\S+) and", prompt)).group(1))


def write_script(root: Path, name: str, body: str, executable: bool = True) -> None:
    path = root / "perf" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/usr/bin/env python3\nimport json, os, sys, time\n" + body + "\n")
    path.chmod(0o755 if executable else 0o644)


def head_only_trees(root: Path) -> Config:
    config = Config(root=root, raw={})
    perf_trees.write_trees(config, perf_trees.Session("t", [perf_trees.Tree("head", git(root, "rev-parse", "HEAD"), root)]))
    return config


def sampling_judge(verdict: str) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env python3",
            "import json, re, subprocess, sys",
            "from pathlib import Path",
            "prompt = sys.stdin.read()",
            'Path("perf").mkdir(exist_ok=True)',
            f'Path("perf/bench_x.py").write_text({BENCH!r})',
            'Path("perf/bench_x.py").chmod(0o755)',
            'for tree in json.loads(Path(".marestail/perf/trees.json").read_text())["trees"]:',
            f'    subprocess.run([sys.executable, {str(CLI)!r}, "perf", "run", "perf/bench_x.py", "--tree", tree["tree"], "--samples", "10"], check=True, capture_output=True)',
            'Path("src.py").write_text("tampered\\n")',
            'Path("stray.txt").write_text("stray\\n")',
            'Path("PERFORMANCE.md").write_text("agent edit\\n")',
            'Path("perf/_probe_timing.py").write_text("print(1)\\n")',
            'Path("perf/__pycache__").mkdir(exist_ok=True)',
            'Path("perf/__pycache__/benchlib.cpython-314.pyc").write_bytes(b"x")',
            'verdict = Path(re.search(r"Write your verdict to (\\S+) and", prompt).group(1))',
            f"verdict.write_text({verdict!r})",
            'print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0, "result": "ok"}))',
            "",
        ]
    )


@contextlib.contextmanager
def quiet() -> Iterator[None]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


@contextlib.contextmanager
def agent_stub(folder: Path, body: str) -> Iterator[None]:
    stub = folder / "stub-agent"
    stub.write_text(body)
    stub.chmod(0o755)
    keys = ["MARESTAIL_CLAUDE", "MARESTAIL_AGENT"]
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["MARESTAIL_CLAUDE"] = str(stub)
    os.environ.pop("MARESTAIL_AGENT", None)
    try:
        yield
    finally:
        for key in keys:
            if previous[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = cast(str, previous[key])


@contextlib.contextmanager
def env_var(key: str, value: str | None) -> Iterator[None]:
    previous = os.environ.get(key)
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


@contextlib.contextmanager
def replaced_invoke(replacement: Callable[[Run, str, str], None]) -> Iterator[None]:
    original = runner.invoke
    runner.invoke = cast(Any, replacement)
    try:
        yield
    finally:
        runner.invoke = original


def measurement(
    baseline: float | None,
    head: float | None,
    better: str = "lower",
    target: str = "add_one",
    metric: str = "p50",
    pre: float | None = None,
    unit: str = "ms",
    values: int = 1000,
    interval: tuple[float, float] | None = None,
    control: float | None = None,
) -> results.Measurement:
    return results.Measurement(target, metric, unit, better, pre, baseline, head, 10, "perf/bench_t", values, interval, control)


def pooled(tree: str, values: list[float], target: str = "t", script: str = "perf/bench_t") -> dict[str, Any]:
    return {
        "tree": tree,
        "sha": "x",
        "script": script,
        "sample": 1,
        "db": False,
        "reset_ms": None,
        "target": target,
        "unit": "ms",
        "better": "lower",
        "values": values,
    }


def record(
    tree: str, target: str = "t", value: float = 1.0, script: str = "perf/bench_t", db: bool = False, unit: str = "ms"
) -> dict[str, Any]:
    return {
        "tree": tree,
        "sha": "x",
        "script": script,
        "sample": 1,
        "db": db,
        "reset_ms": None,
        "target": target,
        "unit": unit,
        "better": "lower",
        "value": value,
    }


def absent(tree: str, target: str = "t", script: str = "perf/bench_t") -> dict[str, Any]:
    return {"tree": tree, "sha": "x", "script": script, "sample": 1, "db": False, "reset_ms": None, "target": target, "absent": True}


def pipeline_order() -> None:
    expect("pipeline", names(), ["specifier", "critic", "coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"])
    perf = cast(Judge, find("perf"))
    expect("perf-bounce-to", perf.bounce_to, "coder")
    expect("perf-writes", perf.writes, ("perf/**",))


def freeze_paths() -> None:
    config = Config(root=Path("/tmp"), raw={})
    touched = ["perf/bench_x.py", "PERFORMANCE.md", "src/app.py"]
    expect("coder-frozen", freeze.frozen_paths(config, "coder", touched), ["perf/bench_x.py", "PERFORMANCE.md"])
    expect("cleaner-frozen", freeze.frozen_paths(config, "cleaner", touched), ["perf/bench_x.py", "PERFORMANCE.md"])


def pinned_bounce_keeps_only_writes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        verdict = "VERDICT: BOUNCE specifier\n1. add_one is twice as slow; batch it in src.py:1\n"
        with agent_stub(Path(tmp), sampling_judge(verdict)), quiet():
            outcome, target, _ = run_judge(state(root), cast(Judge, find("perf")))
        expect("pinned-verdict", (outcome, target), ("BOUNCE", None))
        expect("pinned-subject", git(root, "log", "-1", "--format=%s").endswith("perf verdict: BOUNCE"), True)
        expect("verdict-commit-files", git(root, "show", "--name-only", "--format=", "HEAD"), "perf/bench_x.py")
        expect("bench-executable", git(root, "ls-files", "-s", "perf/bench_x.py").split()[0], "100755")
        expect(
            "scratch-not-committed",
            [(root / "perf" / "_probe_timing.py").exists(), (root / "perf" / "__pycache__").exists()],
            [False, False],
        )
        expect("src-restored", (root / "src.py").read_text(), "original\n")
        expect("stray-removed", (root / "stray.txt").exists(), False)
        expect("table-not-written-on-bounce", (root / "PERFORMANCE.md").exists(), False)
        expect("tree-clean", git(root, "status", "--porcelain"), "")


def disabled_skip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        before = git(root, "rev-parse", "HEAD")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = run_step(state(root, {"perf": {"enabled": False}}), find("perf"))
        expect("disabled-passes", passed, True)
        expect("disabled-message", "perf disabled in marestail.toml; skipping" in output.getvalue(), True)
        expect("disabled-no-commit", git(root, "rev-parse", "HEAD"), before)


def start_commit_recorded_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        config = state(root).config
        first = git(root, "rev-parse", "HEAD")
        perf_trees.record_start(config, "t")
        commit_change(root, "changed\n")
        perf_trees.record_start(config, "t")
        expect("start-kept", perf_trees.start_commit(config, "t"), (first, ""))
        perf_trees.start_file(config, "t").unlink()
        sha, note = perf_trees.start_commit(config, "t")
        expect("start-fallback-sha", sha, git(root, "merge-base", "main", "HEAD"))
        expect("start-fallback-note", "git merge-base main HEAD" in note, True)


def start_commit_archived() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        run_state = state(root)
        perf_trees.record_start(run_state.config, "t")
        run_state.handoffs.mkdir(parents=True)
        (run_state.handoffs / "01-coder.md").write_text("done\n")
        runner.archive_handoffs(run_state)
        expect("start-moved", perf_trees.start_file(run_state.config, "t").exists(), False)
        expect("start-archived", len(list(run_state.folder.glob("handoffs-*/start-commit"))), 1)


def pre_marestail_commit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, readme_first=True)
        config = state(root).config
        readme = git(root, "rev-list", "--max-parents=0", "HEAD")
        expect("pre-parent", perf_trees.pre_marestail_commit(config), (readme, ""))
        (root / "PERFORMANCE.md").write_text(TABLE_WITH_ROW)
        expect("pre-after-rows", perf_trees.pre_marestail_commit(config), (None, ""))
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        expect("pre-root-commit", perf_trees.pre_marestail_commit(state(root).config), (None, perf_trees.NO_PRE_MARESTAIL))


def trees_during_attempt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, readme_first=True)
        run_state = state(root)
        perf_trees.record_start(run_state.config, "t")
        start = git(root, "rev-parse", "HEAD")
        readme = git(root, "rev-list", "--max-parents=0", "HEAD")
        commit_change(root, "changed\n")
        head = git(root, "rev-parse", "HEAD")
        seen: dict[str, Any] = {}

        def inspecting(current: Run, label: str, prompt: str) -> None:
            data = json.loads(perf_trees.trees_file(current.config).read_text())
            seen["trees"] = {tree["tree"]: tree["sha"] for tree in data["trees"]}
            seen["paths"] = [Path(tree["path"]) for tree in data["trees"] if tree["tree"] != "head"]
            seen["checked_out"] = [git(path, "rev-parse", "HEAD") for path in seen["paths"]]
            seen["prompt"] = prompt
            verdict_file(prompt).write_text("VERDICT: PASS\n")

        with replaced_invoke(inspecting), quiet():
            run_judge(run_state, cast(Judge, find("perf")))
        expect("trees-listed", seen["trees"], {"baseline": start, "head": head, "pre-marestail": readme})
        expect("trees-checked-out", seen["checked_out"], [start, readme])
        expect("trees-prompt", "# Trees\n- baseline: " + start in seen["prompt"], True)
        expect("trees-removed", [path.exists() for path in seen["paths"]], [False, False])
        expect("trees-pruned", worktree_count(root), 1)
        expect("trees-file-removed", perf_trees.trees_file(run_state.config).exists(), False)
        expect("empty-pass-writes-no-table", (root / "PERFORMANCE.md").exists(), False)


def trees_removed_when_invoke_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, readme_first=True)
        run_state = state(root)
        seen: dict[str, Any] = {}

        def raising(current: Run, label: str, prompt: str) -> None:
            data = json.loads(perf_trees.trees_file(current.config).read_text())
            seen["paths"] = [Path(tree["path"]) for tree in data["trees"] if tree["tree"] != "head"]
            raise RuntimeError("agent crashed")

        with replaced_invoke(raising), quiet(), contextlib.suppress(RuntimeError):
            run_judge(run_state, cast(Judge, find("perf")))
        expect("raise-trees-seen", len(seen["paths"]), 2)
        expect("raise-trees-removed", [path.exists() for path in seen["paths"]], [False, False])
        expect("raise-trees-pruned", worktree_count(root), 1)
        expect("raise-trees-file-removed", perf_trees.trees_file(run_state.config).exists(), False)


def perf_run_exit_codes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        (root / "marestail.toml").write_text('[git]\nbase = "main"\n\n[perf]\nsample_timeout = 1\n')
        write_script(root, "bench_ok.py", 'print(json.dumps({"target": "a", "unit": "ms", "better": "lower", "value": 1}))')
        with contextlib.chdir(root), quiet():
            expect("run-no-trees", samples.run_command("perf/bench_ok.py", "head", 1, False), 2)
            head_only_trees(root)
            expect("run-ok", samples.run_command("perf/bench_ok.py", "head", 3, False), 0)
            expect("run-unknown-tree", samples.run_command("perf/bench_ok.py", "baseline", 1, False), 2)
            write_script(root, "helper.py", "print(1)")
            expect("run-not-bench", samples.run_command("perf/helper.py", "head", 1, False), 2)
            write_script(root, "bench_plain.py", "print(1)", executable=False)
            expect("run-not-executable", samples.run_command("perf/bench_plain.py", "head", 1, False), 2)
            write_script(root, "bench_fail.py", "sys.exit(3)")
            expect("run-failing", samples.run_command("perf/bench_fail.py", "head", 1, False), 1)
            write_script(root, "bench_silent.py", 'print("warming up")')
            expect("run-silent", samples.run_command("perf/bench_silent.py", "head", 1, False), 1)
            write_script(root, "bench_slow.py", "time.sleep(3)")
            expect("run-timeout", samples.run_command("perf/bench_slow.py", "head", 1, False), 1)
            expect("run-db-unconfigured", samples.run_command("perf/bench_ok.py", "head", 1, True), 2)
        expect("run-ok-recorded", len(perf_trees.samples_file(Config(root=root, raw={})).read_text().splitlines()), 3)


def perf_run_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        write_script(
            root,
            "bench_mixed.py",
            'print("warming up")\nprint(json.dumps({"target": "a", "unit": "ms", "better": "lower", "value": 2.5}))\nprint(json.dumps({"target": "b", "absent": True}))',
        )
        config = head_only_trees(root)
        sha = git(root, "rev-parse", "HEAD")
        with contextlib.chdir(root), quiet():
            expect("records-exit", samples.run_command("perf/bench_mixed.py", "head", 1, False), 0)
        lines = [json.loads(line) for line in perf_trees.samples_file(config).read_text().splitlines()]
        stamp = hygiene.fingerprint(root, "perf/bench_mixed.py")
        base = {
            "tree": "head",
            "sha": sha,
            "script": "perf/bench_mixed.py",
            "fingerprint": stamp,
            "sample": 1,
            "db": False,
            "reset_ms": None,
        }
        expect(
            "records",
            lines,
            [base | {"target": "a", "unit": "ms", "better": "lower", "value": 2.5}, base | {"target": "b", "absent": True}],
        )


def percentiles() -> None:
    expect("percentiles", results.percentiles([float(value) for value in range(20, 0, -1)]), (10.5, 19.0))
    expect("percentiles-single", results.percentiles([4.0]), (4.0, 4.0))


def validation_errors() -> None:
    trees = ["baseline", "head"]

    def problems(records: list[dict[str, Any]], benches: tuple[str, ...] = ("perf/bench_t",)) -> list[str]:
        return results.compile_records(records, trees, list(benches), results.Policy(min_runs=2))[1]

    def has(name: str, found: list[str], fragment: str) -> None:
        expect(name, any(fragment in problem for problem in found), True)

    valid = [record("baseline"), record("baseline"), record("head"), record("head")]
    expect("valid", problems(valid), [])
    has("unit", problems([*valid, record("head", unit="s")]), "`t` reports more than one unit: ms, s")
    has("both", problems([*valid, absent("head")]), "`t` has both values and absent on the head tree")
    other = [record("baseline", target="o"), record("baseline", target="o"), record("head", target="o"), record("head", target="o")]
    has("unmeasured", problems(valid[:2] + other), "`t` was not measured on the head tree")
    has("min-runs", problems(valid[1:]), "`t` has 1 samples on the baseline tree; min_runs is 2")
    has(
        "db",
        problems([record("baseline", db=True), record("baseline", db=True), record("head"), record("head")]),
        "`perf/bench_t` was run with --db on some trees and without it on others",
    )
    has("bench", problems(valid, ("perf/bench_t", "perf/bench_u")), "`perf/bench_u` has no samples on the baseline tree")
    has("both-null", problems([absent("baseline"), absent("head")]), "`t` is absent on both the baseline and head trees")
    measured, _ = results.compile_records(
        [*valid, absent("pre-marestail")], ["baseline", "head", "pre-marestail"], ["perf/bench_t"], results.Policy(min_runs=2)
    )
    expect("pre-null", [item.pre_marestail for item in measured], [None, None])


def classification() -> None:
    def status(item: results.Measurement, threshold: float = 10) -> tuple[str, float | None]:
        found = results.classify(item, results.Policy(threshold_percent=threshold))
        return found.status, found.change

    expect("degraded-at-threshold", status(measurement(10, 11)), ("degraded", 10.0))
    expect("improved-at-threshold", status(measurement(10, 9)), ("improved", -10.0))
    expect("higher-is-better", status(measurement(10, 11, better="higher")), ("improved", 10.0))
    expect("higher-worse", status(measurement(10, 9, better="higher")), ("degraded", -10.0))
    expect("below-threshold", status(measurement(10, 10.99))[0], "unchanged")
    expect("new", status(measurement(None, 5)), ("new", None))
    expect("removed", status(measurement(5, None)), ("removed", None))
    expect("zero-both", status(measurement(0, 0)), ("unchanged", 0.0))
    expect("zero-baseline", status(measurement(0, 5)), ("degraded", 100.0))


def audits() -> None:
    degraded = results.classify(measurement(10, 20), POLICY)
    benches = ["perf/bench_t"]
    expect(
        "audit-unflagged",
        results.audit([degraded], [], "VERDICT: PASS\n", "PASS", benches),
        ["`add_one` is degraded (+100.0% p50) but the verdict does not name it"],
    )
    expect("audit-flagged", results.audit([degraded], [], "VERDICT: PASS\n- add_one: +100% p50, accepted\n", "PASS", benches), [])
    expect(
        "audit-missing-column",
        results.audit([degraded], ["add_one p50", "gone p95"], "VERDICT: PASS\n- add_one\n", "PASS", benches),
        ["column `gone p95` in PERFORMANCE.md was not re-measured; every existing bench runs on every tree every time"],
    )
    expect("audit-empty-pass", results.audit([], [], "VERDICT: PASS\n", "PASS", []), [])
    expect("audit-empty-bounce", len(results.audit([], [], "VERDICT: BOUNCE\n", "BOUNCE", [])), 1)
    expect("audit-empty-with-benches", len(results.audit([], [], "VERDICT: PASS\n", "PASS", benches)), 1)


def snapshot(
    task: str, classified: list[results.Classified], pre_commit: str | None = None, rows: str = "—", commit: str = "abc1234"
) -> table.Snapshot:
    return table.Snapshot(task, commit, "2026-09-13", rows, pre_commit, classified)


def table_writes() -> None:
    template = table.TEMPLATE.read_text()
    prefix = template[: template.index("| Task |")]
    p50 = results.classify(measurement(10, 20, pre=8), POLICY)
    p95 = results.classify(measurement(10, 20, pre=8, metric="p95"), POLICY)
    first = table.render(table.upsert(table.parse(template), snapshot("t", [p50, p95], pre_commit="0000000")))
    expect(
        "table-first",
        first,
        prefix
        + "| Task | Commit | Date | Rows | add_one p50 | add_one p95 |\n|---|---|---|---|---|---|\n"
        + "| pre-marestail | 0000000 | 2026-09-13 | — | 8ms | 8ms |\n"
        + "| t | abc1234 | 2026-09-13 | — | 20ms (+100.0%) ⚠ | 20ms (+100.0%) ⚠ |\n",
    )
    steady = results.classify(measurement(12.4, 12.1), POLICY)
    fresh = results.classify(measurement(None, 0.02, target="sub_one"), POLICY)
    second = table.render(table.upsert(table.parse(first), snapshot("u", [steady, fresh], commit="def5678")))
    lines = second[len(prefix) :].splitlines()
    expect("table-second-header", lines[0], "| Task | Commit | Date | Rows | add_one p50 | add_one p95 | sub_one p50 |")
    expect("table-second-pre", lines[2], "| pre-marestail | 0000000 | 2026-09-13 | — | 8ms | 8ms | — |")
    expect("table-second-t", lines[3], "| t | abc1234 | 2026-09-13 | — | 20ms (+100.0%) ⚠ | 20ms (+100.0%) ⚠ | — |")
    expect("table-second-u", lines[4], "| u | def5678 | 2026-09-13 | — | 12.1ms (-2.4%) | — | 0.02ms (new) |")
    faster = results.classify(measurement(12.4, 9), POLICY)
    gone = results.classify(measurement(5, None, metric="p95"), POLICY)
    rerun = table.render(table.upsert(table.parse(second), snapshot("t", [faster, gone], commit="9999999", rows="50000000")))
    rerun_lines = rerun[len(prefix) :].splitlines()
    expect("table-rerun-order", [line.split(" | ")[0] for line in rerun_lines[2:]], ["| pre-marestail", "| t", "| u"])
    expect("table-rerun-t", rerun_lines[3], "| t | 9999999 | 2026-09-13 | 50000000 | 9ms (-27.4%) ✓ | removed | — |")
    without_pre = table.render(table.upsert(table.parse(template), snapshot("t", [p50])))
    expect("table-no-pre", "pre-marestail" in without_pre, False)
    piped = results.classify(measurement(None, 3, target="GET /a|b"), POLICY)
    piped_text = table.render(table.upsert(table.parse(template), snapshot("t", [piped])))
    expect("table-pipe-escaped", "| GET /a\\|b p50 |" in piped_text, True)
    expect("table-pipe-parsed", table.parse(piped_text).columns, ["GET /a|b p50"])
    custom = "# Mine\n\nNotes | with a pipe, and *emphasis*.\n\n| Task | Commit | Date | Rows |\n|---|---|---|---|\n\nTrailing prose.\n"
    kept = table.render(table.upsert(table.parse(custom), snapshot("t", [p50])))
    expect("table-prose-before", kept.startswith("# Mine\n\nNotes | with a pipe, and *emphasis*.\n\n| Task |"), True)
    expect("table-prose-after", kept.endswith("\n\nTrailing prose.\n"), True)


def rows_cell() -> None:
    config = Config(root=Path("/tmp"), raw={"perf": {"db": {"rows": 1000}}})
    previous = os.environ.pop("MARESTAIL_PERF_DB_ROWS", None)
    try:
        expect("rows-without-db", perf_review.rows_cell(config, False), "—")
        expect("rows-with-db", perf_review.rows_cell(config, True), "1000")
    finally:
        if previous is not None:
            os.environ["MARESTAIL_PERF_DB_ROWS"] = previous


def rejected_verdict_retried_with_feedback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, readme_first=True)
        run_state = state(root, retries=5)
        prompts_seen: list[str] = []
        plan = [
            (2, "VERDICT: PASS\n"),
            (10, "VERDICT: PASS\n"),
            (10, "VERDICT: PASS\n## Degradations\n- add_one: +100% p50, accepted: Adds one\n"),
        ]

        def judging(current: Run, label: str, prompt: str) -> None:
            count, verdict = plan[len(prompts_seen)]
            prompts_seen.append(prompt)
            write_script(root, "bench_x.py", BENCH.split("\n", 2)[2])
            with contextlib.chdir(root):
                for tree in ALL_TREES:
                    samples.run_command("perf/bench_x.py", tree, count, False)
            verdict_file(prompt).write_text(verdict)

        with replaced_invoke(judging), quiet():
            outcome = run_judge(run_state, cast(Judge, find("perf")))
        expect("retry-outcome", outcome[0], "PASS")
        expect("retry-attempts", len(prompts_seen), 3)
        expect("retry-first-clean", "# Why your verdict was rejected" in prompts_seen[0], False)
        expect("retry-min-runs", "`add_one` has 2 samples on the baseline tree; min_runs is 10" in prompts_seen[1], True)
        expect("retry-unflagged", "`add_one` is degraded (+100.0% p50) but the verdict does not name it" in prompts_seen[2], True)
        expect(
            "retry-commit-files",
            sorted(git(root, "show", "--name-only", "--format=", "HEAD").splitlines()),
            ["PERFORMANCE.md", "perf/bench_x.py"],
        )
        rows = table.load(root).rows
        expect("retry-table-rows", [row["Task"] for row in rows], ["pre-marestail", "t"])
        expect("retry-table-cell", rows[1]["add_one p50"], "20ms (+100.0%) ⚠")
        expect(
            "retry-summary",
            run_state.perf_changes.splitlines()[:3],
            ["## Performance changes", "- degraded +100.0% `add_one p50`", "- degraded +100.0% `add_one p95`"],
        )


def image_detection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expect("image-default", perf_image.resolve(root, None), ("postgres:18", "default"))
        (root / ".tool-versions").write_text("nodejs 20.1.0\npostgres 14.9\n")
        expect("image-tool-versions", perf_image.resolve(root, None), ("postgres:14", ".tool-versions"))
        workflows = root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("jobs:\n  test:\n    services:\n      db:\n        image: postgres:15-alpine\n")
        expect("image-workflow", perf_image.resolve(root, None), ("postgres:15-alpine", ".github/workflows/ci.yml"))
        (root / "docker-compose.yml").write_text("services:\n  db:\n    image: postgres:16\n")
        expect("image-compose", perf_image.resolve(root, None), ("postgres:16", "docker-compose.yml"))
        expect("image-explicit", perf_image.resolve(root, "postgres:17"), ("postgres:17", "marestail.toml"))
    expect("helper-image-alpine", perf_image.helper_image("postgres:16-alpine"), "postgres:16")
    expect("helper-image-plain", perf_image.helper_image("postgres:16"), "postgres:16")


def row_count() -> None:
    plain = Config(root=Path("/tmp"), raw={})
    configured = Config(root=Path("/tmp"), raw={"perf": {"db": {"rows": 10000000}}})
    with env_var(settings.ROWS_ENV, None):
        expect("rows-default", settings.effective_rows(plain), (50000000, "default"))
        expect("rows-config", settings.effective_rows(configured), (10000000, "marestail.toml"))
        for bad in (-1, 1.5, "ten"):
            try:
                settings.effective_rows(Config(root=Path("/tmp"), raw={"perf": {"db": {"rows": bad}}}))
                raise SystemExit(f"rows-invalid {bad!r}: no error")
            except ValueError as error:
                expect(f"rows-invalid-{bad}", str(error), f"[perf.db] rows must be a whole number ≥ 0, got {bad}")
    with env_var(settings.ROWS_ENV, "0"):
        expect("rows-env-wins", settings.effective_rows(configured), (0, settings.ROWS_ENV))
    with env_var(settings.ROWS_ENV, "-1"):
        try:
            settings.effective_rows(plain)
            raise SystemExit("rows-env-invalid: no error")
        except ValueError as error:
            expect("rows-env-invalid", str(error), "[perf.db] rows must be a whole number ≥ 0, got -1")


def golden_names() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "perf").mkdir()
        tree = perf_trees.Tree("head", "abc123", root)

        def name(rows: int) -> str:
            return perf_db.golden_name(
                perf_db.build_database(Config(root=root, raw={}), {"migrate": "true"}, rows, "test", "postgres:16", "test"), tree
            )

        (root / "perf" / "seed.sql").write_text("insert into a select 1;\n")
        expect("golden-rows-differ", name(10000000) != name(50000000), True)
        seeded, empty = name(1000), name(0)
        (root / "perf" / "seed.sql").write_text("insert into a select 2;\n")
        expect("golden-seed-changes-name", name(1000) != seeded, True)
        expect("golden-seed-ignored-at-zero", name(0), empty)
        expect("golden-name-shape", re.fullmatch(r"golden_[0-9a-f]{16}", empty) is not None, True)


def disk_estimate() -> None:
    prior = [{"rows": 50000000, "bytes": 12500000000}]
    expect("disk-estimate", perf_db.estimate_bytes(prior, 10000000, 50), 3000000000)
    expect("disk-no-prior", perf_db.estimate_bytes([], 10000000, 50), 50 * 1024**3)
    expect("disk-empty-prior-ignored", perf_db.estimate_bytes([{"rows": 0, "bytes": 90000000}], 10000000, 50), 50 * 1024**3)
    expect("disk-refuses", perf_db.refuses(2999999999, prior, 10000000, 50), True)
    expect("disk-allows", perf_db.refuses(3000000000, prior, 10000000, 50), False)
    expect("disk-rows-zero", perf_db.refuses(0, prior, 0, 50), False)
    expect(
        "disk-message",
        perf_db.disk_message("golden_0123456789abcdef", 3 * 1024**3, 1024**3),
        "not enough disk for golden_0123456789abcdef: need ~3.0 GB, have 1.0 GB free on the Docker data root; run marestail perf db prune or lower [perf.db] rows",
    )


def install_template_and_gitignore() -> None:
    from marestail.install import install

    with tempfile.TemporaryDirectory() as tmp, env_var("GROK_HOME", str(Path(tmp) / "grok")), quiet():
        plain = Path(tmp) / "plain"
        plain.mkdir()
        install(plain)
        expect("install-table", (plain / "PERFORMANCE.md").read_text(), table.TEMPLATE.read_text())
        plain_ignores = (plain / ".gitignore").read_text().splitlines()
        expect("install-plain-ignores", ["PERFORMANCE.md" in plain_ignores, "perf/" in plain_ignores], [False, False])
        (plain / "PERFORMANCE.md").write_text("mine\n")
        install(plain)
        expect("install-keeps-table", (plain / "PERFORMANCE.md").read_text(), "mine\n")
        generated = Path(tmp) / "generated"
        generated.mkdir()
        install(generated, gitignore_generated=True)
        generated_ignores = (generated / ".gitignore").read_text().splitlines()
        expect("install-generated-ignores", ["PERFORMANCE.md" in generated_ignores, "perf/" in generated_ignores], [True, True])


def gates_skip_benchmarks() -> None:
    from marestail import depth, dotnet, java, rust
    from marestail.context import Context
    from marestail.gates import comments, deadcode, docs, py_crap, py_lint, py_mutation, py_runtime, sonar, ts_lint, ts_mutation

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        for relative in ("perf/bench_x.py", "perf/Bench.cs", "perf/bench.rs", "src/app.py", "src/App.cs", "src/lib.rs"):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text("x = 1\n")
        at_root = {"docs": {"sources": ["."]}, "python": {"root": "."}, "ts": {"root": "."}, "dotnet": {"root": "."}, "rust": {"root": "."}}
        ctx = Context(config=Config(root=root, raw=at_root))
        nested = Context(config=Config(root=root, raw={"python": {"root": "src"}, "ts": {"root": "src"}}))
        bench, app = root / "perf" / "bench_x.py", root / "src" / "app.py"
        expect("comments-skip", [comments.skipped(bench, ctx), comments.skipped(app, ctx)], [True, False])
        expect("docs-skip", [path.relative_to(root).as_posix() for path in docs.source_files(ctx)], ["src/app.py", "src/lib.rs"])
        expect("py-runtime-skip", [path.relative_to(root).as_posix() for path in py_runtime.sources(ctx, root)], ["src/app.py"])
        expect("rust-skip", [rust.skipped(ctx, root / "perf" / "bench.rs"), rust.skipped(ctx, root / "src" / "lib.rs")], [True, False])
        expect("java-skip", [java.skipped(ctx, root / "perf" / "Bench.java"), java.skipped(ctx, root / "src" / "App.java")], [True, False])
        expect("depth-skip", [depth.skipped(bench, root), depth.skipped(app, root)], [True, False])
        expect(
            "dotnet-skip",
            [dotnet.generated(ctx, root / "perf" / "Bench.cs"), dotnet.generated(ctx, root / "src" / "App.cs")],
            [True, False],
        )
        expect("vulture-exclude", "perf/*" in deadcode.PYTHON_EXCLUDES, True)
        expect("radon-exclude", "perf/*" in py_crap.radon_command(ctx)[4].split(","), True)
        expect(
            "ruff-exclude-at-root",
            py_lint.path_exclusions(ctx),
            ["--extend-exclude", "perf/**", "--extend-exclude", "qa/**", "--extend-exclude", "features/**"],
        )
        expect("ruff-no-exclude-nested", py_lint.path_exclusions(nested), [])
        expect("eslint-exclude-at-root", ts_lint.eslint_command(ctx)[3:5], ["--ignore-pattern", "perf/"])
        expect("eslint-no-exclude-nested", "--ignore-pattern" in ts_lint.eslint_command(nested), False)
        expect("sonar-dotnet-exclude", "perf/**" in sonar.DOTNET_EXCLUSIONS, True)
        expect("sonar-template-exclude", "perf/**" in (table.TEMPLATE.parent / "sonar-project.properties").read_text(), True)
        changed = Context(
            config=Config(root=root, raw={"python": {"root": "."}, "ts": {"root": "."}}),
            scope_changed=True,
            changed={"perf/bench_x.py", "src/app.py", "perf/bench.ts", "src/app.ts"},
        )
        expect("py-lint-changed", py_lint.changed_python(changed), ["src/app.py"])
        expect("py-mutation-changed", py_mutation.mutant_patterns(changed, ["perf/bench_x.py", "src/app.py"]), ["src.app.*"])
        expect("ts-mutation-changed", ts_mutation.changed_sources(changed, ["perf/bench.ts", "src/app.ts"]), ["src/app.ts"])


def sonar_scanner_excludes_benchmarks() -> None:
    from marestail.context import Context
    from marestail.gates import sonar

    creds = {"url": "http://localhost:9000", "token": "t"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = Context(config=Config(root=root, raw={}))
        expect("sonar-no-properties", sonar.scanner_exclusions(ctx), "perf/**")
        (root / "sonar-project.properties").write_text(
            "sonar.projectKey=x\n# sonar.exclusions=ignored/**\nsonar.exclusions=**/node_modules/**, \\\n  **/dist/**\n"
        )
        expect("sonar-merged", sonar.scanner_exclusions(ctx), "**/node_modules/**,**/dist/**,perf/**")
        expect("sonar-flag", "-Dsonar.exclusions=**/node_modules/**,**/dist/**,perf/**" in sonar.scanner_command(ctx, creds, "x"), True)
        (root / "sonar-project.properties").write_text("sonar.exclusions : perf/**,**/tmp/**\n")
        expect("sonar-no-duplicate", sonar.scanner_exclusions(ctx), "perf/**,**/tmp/**")


def csharp_bench_guard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config(root=root, raw={})
        (root / "perf").mkdir()
        (root / "perf" / "Bench.cs").write_text("class Bench {}\n")
        expect("csharp-no-root-project", perf_review.csharp_problems(config), [])
        (root / "Api").mkdir()
        (root / "Api" / "Api.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk" />\n')
        expect("csharp-nested-project", perf_review.csharp_problems(config), [])
        (root / "App.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk" />\n')
        expect(
            "csharp-root-project",
            perf_review.csharp_problems(config),
            [
                "`perf/Bench.cs` would compile into App.csproj, because an SDK project at the repo root includes every .cs file below it; write this bench in another language"
            ],
        )


def fingerprints_follow_the_harness() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "perf").mkdir()
        write_script(root, "bench_a.py", "print(1)")
        write_script(root, "bench_b.py", "print(2)")
        (root / "perf" / "benchlib.py").write_text("SETUP = 1\n")
        first = hygiene.fingerprint(root, "perf/bench_a.py")
        expect("fingerprint-shape", re.fullmatch(r"[0-9a-f]{16}", first) is not None, True)
        write_script(root, "bench_b.py", "print(3)")
        expect("fingerprint-ignores-other-bench", hygiene.fingerprint(root, "perf/bench_a.py"), first)
        (root / "perf" / "_probe_timing.py").write_text("print(4)\n")
        (root / "perf" / "__pycache__").mkdir()
        (root / "perf" / "__pycache__" / "benchlib.cpython-314.pyc").write_bytes(b"\0")
        expect("fingerprint-ignores-scratch", hygiene.fingerprint(root, "perf/bench_a.py"), first)
        (root / "perf" / "benchlib.py").write_text("SETUP = 2\n")
        shared_changed = hygiene.fingerprint(root, "perf/bench_a.py")
        expect("fingerprint-follows-shared-file", shared_changed != first, True)
        write_script(root, "bench_a.py", "print(5)")
        expect("fingerprint-follows-bench", hygiene.fingerprint(root, "perf/bench_a.py") != shared_changed, True)
        (root / "perf" / "_helper.py").write_text("VALUE = 1\n")
        write_script(root, "bench_a.py", "import _helper\nprint(_helper.VALUE)")
        with_helper = hygiene.fingerprint(root, "perf/bench_a.py")
        (root / "perf" / "_helper.py").write_text("VALUE = 2\n")
        expect("fingerprint-follows-referenced-underscore-file", hygiene.fingerprint(root, "perf/bench_a.py") != with_helper, True)


def stale_samples_dropped_and_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        (root / "perf").mkdir()
        (root / "perf" / "benchlib.py").write_text("VALUE = 1\n")
        write_script(root, "bench_lib.py", 'print(json.dumps({"target": "lib", "unit": "ms", "better": "lower", "value": 1}))')
        config = head_only_trees(root)
        with contextlib.chdir(root), quiet():
            expect("stale-first-run", samples.run_command("perf/bench_lib.py", "head", 3, False), 0)
        old = [json.loads(line) for line in perf_trees.samples_file(config).read_text().splitlines()]
        (root / "perf" / "benchlib.py").write_text("VALUE = 2\n")
        current = {"perf/bench_lib.py": hygiene.fingerprint(root, "perf/bench_lib.py")}
        expect(
            "stale-problem",
            results.stale_problems(old, current),
            [
                "`perf/bench_lib.py` has samples taken before it or a shared file under perf/ last changed (3 on head); take its samples again on every tree"
            ],
        )
        errors = io.StringIO()
        with contextlib.chdir(root), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            expect("stale-second-run", samples.run_command("perf/bench_lib.py", "head", 2, False), 0)
        fresh = [json.loads(line) for line in perf_trees.samples_file(config).read_text().splitlines()]
        expect("stale-dropped", [record["fingerprint"] for record in fresh], [current["perf/bench_lib.py"]] * 2)
        expect("stale-message", "dropped 3 earlier measurements of perf/bench_lib.py" in errors.getvalue(), True)
        expect("stale-fresh-accepted", results.stale_problems(fresh, current), [])


def scratch_discarded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "perf").mkdir()
        write_script(root, "bench_a.py", "import _helper\nprint(_helper.VALUE)")
        (root / "perf" / "_helper.py").write_text("VALUE = 1\n")
        (root / "perf" / "_probe_timing.py").write_text("print(1)\n")
        (root / "perf" / "__init__.py").write_text("")
        (root / "perf" / "_scratch").mkdir()
        (root / "perf" / "_scratch" / "notes.txt").write_text("timings\n")
        (root / "perf" / "__pycache__").mkdir()
        (root / "perf" / "__pycache__" / "_helper.cpython-314.pyc").write_bytes(b"\0")
        expect(
            "scratch-removed",
            hygiene.discard_scratch(root),
            ["perf/__pycache__/_helper.cpython-314.pyc", "perf/_probe_timing.py", "perf/_scratch/notes.txt"],
        )
        expect(
            "scratch-kept",
            sorted(path.relative_to(root).as_posix() for path in (root / "perf").rglob("*")),
            ["perf/__init__.py", "perf/_helper.py", "perf/bench_a.py"],
        )


def pooled_values() -> None:
    expect(
        "parse-values",
        samples.measurement('{"target": "a", "unit": "ms", "better": "lower", "values": [1, 2.5]}'),
        {"target": "a", "unit": "ms", "better": "lower", "values": [1, 2.5]},
    )
    for bad in ("[]", '[1, "x"]', "[true]", '"many"'):
        expect(
            f"parse-values-invalid-{bad}",
            samples.measurement('{"target": "a", "unit": "ms", "better": "lower", "values": ' + bad + "}"),
            None,
        )
    policy = results.Policy(min_runs=2, values_per_sample=3, p95_min_values=6)
    trees = ["baseline", "head"]
    records = [pooled("baseline", [1, 2, 3]), pooled("baseline", [4, 5, 6]), pooled("head", [2, 3, 4]), pooled("head", [5, 6, 7])]
    measured, problems = results.compile_records(records, trees, ["perf/bench_t"], policy)
    expect("pooled-problems", problems, [])
    expect(
        "pooled-stats",
        [(item.metric, item.baseline, item.head, item.runs, item.values) for item in measured],
        [("p50", 3.5, 4.5, 2, 6), ("p95", 6, 7, 2, 6)],
    )
    short = [*records[:3], pooled("head", [5, 6])]
    expect(
        "pooled-short",
        results.compile_records(short, trees, ["perf/bench_t"], policy)[1],
        ["`t` has 1 samples on the head tree with fewer than 3 values each; time at least 3 requests per sample"],
    )
    single = [record("baseline"), record("baseline"), record("head"), record("head")]
    p95 = results.compile_records(single, trees, ["perf/bench_t"], results.Policy(min_runs=2))[0][1]
    thin = results.classify(p95, POLICY)
    expect("thin-p95", (thin.status, table.task_cell(thin)), ("thin", "1ms (n=2)"))


def noise_aware_classification() -> None:
    import random

    rng = random.Random(7)
    baseline = [10 + rng.random() for _ in range(400)]
    same = [10 + rng.random() for _ in range(400)]
    slower = [12 + rng.random() for _ in range(400)]
    policy = results.Policy(min_runs=2, min_change=())

    def compiled(head: list[float]) -> tuple[list[results.Measurement], list[str]]:
        records = [
            pooled("baseline", baseline[:200]),
            pooled("baseline", baseline[200:]),
            pooled("head", head[:200]),
            pooled("head", head[200:]),
        ]
        measured, problems = results.compile_records(records, ["baseline", "head"], ["perf/bench_t"], policy)
        expect("noise-problems", problems, [])
        return measured, [results.classify(item, policy).status for item in measured]

    first, statuses = compiled(slower)
    expect("noise-real-change", statuses, ["degraded", "degraded"])
    expect("bootstrap-deterministic", [item.interval for item in compiled(slower)[0]], [item.interval for item in first])
    expect("noise-identical-code", compiled(same)[1], ["unchanged", "unchanged"])
    expect("floor-sub-ms", results.classify(measurement(0.53, 0.90), POLICY).status, "unchanged")
    expect("floor-other-unit", results.classify(measurement(0.53, 0.90, unit="rows"), POLICY).status, "degraded")
    expect("interval-straddles", results.classify(measurement(100, 115, interval=(-5.0, 30.0)), POLICY).status, "unchanged")
    expect("interval-beyond", results.classify(measurement(100, 115, interval=(12.0, 20.0)), POLICY).status, "degraded")
    expect("interval-improved", results.classify(measurement(100, 85, interval=(-20.0, -12.0)), POLICY).status, "improved")
    expect("control-noise", results.classify(measurement(100, 118, interval=(15.0, 20.0), control=125), POLICY).status, "unchanged")
    expect("control-quiet", results.classify(measurement(100, 118, interval=(15.0, 20.0), control=101), POLICY).status, "degraded")


def policy_settings() -> None:
    expect("policy-defaults", settings.policy(Config(root=Path("/tmp"), raw={})), results.Policy())
    raw = {
        "perf": {
            "threshold_percent": 5,
            "min_runs": 3,
            "values_per_sample": 250,
            "p95_min_values": 500,
            "min_change": {"ms": 2, "rows": 10},
            "bootstrap": 100,
            "control": True,
        }
    }
    configured = Config(root=Path("/tmp"), raw=raw)
    expect("policy-configured", settings.policy(configured), results.Policy(3, 250, 500, 5.0, (("ms", 2.0), ("rows", 10.0)), 100))
    expect("control-on", settings.control(configured), True)
    expect("control-off", settings.control(Config(root=Path("/tmp"), raw={})), False)


def control_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        config = Config(root=root, raw={"git": {"base": "main"}, "perf": {"control": True}})
        perf_trees.record_start(config, "t")
        with quiet(), perf_trees.measuring(config, "t") as session:
            seen = [tree.name for tree in session.trees]
            shas = {tree.name: tree.sha for tree in session.trees}
            section = perf_trees.prompt_section(config, session)
        expect("control-trees", seen, ["baseline", "head", "control"])
        expect("control-same-commit", shas["control"], shas["baseline"])
        expect("control-prompt", "- control: a second copy of the baseline commit" in section, True)
        expect("control-removed", worktree_count(root), 1)


if __name__ == "__main__":
    pooled_values()
    noise_aware_classification()
    policy_settings()
    control_tree()
    fingerprints_follow_the_harness()
    stale_samples_dropped_and_rejected()
    scratch_discarded()
    install_template_and_gitignore()
    gates_skip_benchmarks()
    sonar_scanner_excludes_benchmarks()
    csharp_bench_guard()
    image_detection()
    row_count()
    golden_names()
    disk_estimate()
    pipeline_order()
    freeze_paths()
    pinned_bounce_keeps_only_writes()
    disabled_skip()
    start_commit_recorded_once()
    start_commit_archived()
    pre_marestail_commit()
    trees_during_attempt()
    trees_removed_when_invoke_raises()
    perf_run_exit_codes()
    perf_run_records()
    percentiles()
    validation_errors()
    classification()
    audits()
    table_writes()
    rows_cell()
    rejected_verdict_retried_with_feedback()
    print("perf ok")
