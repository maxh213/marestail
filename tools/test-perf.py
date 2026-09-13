#!/usr/bin/env python3
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import freeze, runner
from marestail.config import Config
from marestail.perf import trees as perf_trees
from marestail.pipeline import find, names
from marestail.runner import Run, run_judge, run_step

BOUNCING_JUDGE = """#!/usr/bin/env python3
import json, re, sys
from pathlib import Path
prompt = sys.stdin.read()
verdict = Path(re.search(r"Write your verdict to (\\S+) and", prompt).group(1))
verdict.parent.mkdir(parents=True, exist_ok=True)
verdict.write_text("VERDICT: BOUNCE specifier\\n1. slow\\n")
Path("perf").mkdir(exist_ok=True)
Path("perf/bench_x.py").write_text("print(1)\\n")
Path("src.py").write_text("tampered\\n")
Path("stray.txt").write_text("stray\\n")
Path("PERFORMANCE.md").write_text("agent edit\\n")
print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0, "result": "ok"}))
"""
TABLE_WITH_ROW = "# Performance\n\n| Task | Commit | Date | Rows |\n|---|---|---|---|\n| t | abc1234 | 2026-09-13 | — |\n"


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root, *args):
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


def state(root: Path, raw=None) -> Run:
    return Run(config=Config(root=root, raw=raw or {"git": {"base": "main"}}), task=root / "tasks" / "t.md", model=None, retries=1, agent="claude")


def worktree_count(root: Path) -> int:
    return git(root, "worktree", "list", "--porcelain").count("worktree ")


def commit_change(root: Path, content: str) -> None:
    (root / "src.py").write_text(content)
    git(root, "commit", "-qam", "change")


@contextlib.contextmanager
def agent_stub(folder: Path, body: str):
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
                os.environ[key] = previous[key]


@contextlib.contextmanager
def replaced_invoke(replacement):
    original = runner.invoke
    runner.invoke = replacement
    try:
        yield
    finally:
        runner.invoke = original


def pipeline_order():
    expect("pipeline", names(), ["specifier", "critic", "coder", "cleaner", "architect", "perf", "hardener", "qa"])
    perf = find("perf")
    expect("perf-bounce-to", perf.bounce_to, "coder")
    expect("perf-writes", perf.writes, ("perf/**",))


def freeze_paths():
    config = Config(root=Path("/tmp"), raw={})
    touched = ["perf/bench_x.py", "PERFORMANCE.md", "src/app.py"]
    expect("coder-frozen", freeze.frozen_paths(config, "coder", touched), ["perf/bench_x.py", "PERFORMANCE.md"])
    expect("cleaner-frozen", freeze.frozen_paths(config, "cleaner", touched), ["perf/bench_x.py", "PERFORMANCE.md"])


def pinned_bounce_keeps_only_writes():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        with agent_stub(Path(tmp), BOUNCING_JUDGE), contextlib.redirect_stdout(io.StringIO()):
            verdict, target, _ = run_judge(state(root), find("perf"))
        expect("pinned-verdict", (verdict, target), ("BOUNCE", None))
        expect("pinned-subject", git(root, "log", "-1", "--format=%s").endswith("perf verdict: BOUNCE"), True)
        expect("verdict-commit-files", git(root, "show", "--name-only", "--format=", "HEAD"), "perf/bench_x.py")
        expect("src-restored", (root / "src.py").read_text(), "original\n")
        expect("stray-removed", (root / "stray.txt").exists(), False)
        expect("table-edit-removed", (root / "PERFORMANCE.md").exists(), False)
        expect("tree-clean", git(root, "status", "--porcelain"), "")


def disabled_skip():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        before = git(root, "rev-parse", "HEAD")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = run_step(state(root, {"perf": {"enabled": False}}), find("perf"))
        expect("disabled-passes", passed, True)
        expect("disabled-message", "perf disabled in marestail.toml; skipping" in output.getvalue(), True)
        expect("disabled-no-commit", git(root, "rev-parse", "HEAD"), before)


def start_commit_recorded_once():
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


def start_commit_archived():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        run_state = state(root)
        perf_trees.record_start(run_state.config, "t")
        run_state.handoffs.mkdir(parents=True)
        (run_state.handoffs / "01-coder.md").write_text("done\n")
        runner.archive_handoffs(run_state)
        expect("start-moved", perf_trees.start_file(run_state.config, "t").exists(), False)
        expect("start-archived", len(list(run_state.folder.glob("handoffs-*/start-commit"))), 1)


def pre_marestail_commit():
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


def trees_during_attempt():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, readme_first=True)
        run_state = state(root)
        perf_trees.record_start(run_state.config, "t")
        start = git(root, "rev-parse", "HEAD")
        readme = git(root, "rev-list", "--max-parents=0", "HEAD")
        commit_change(root, "changed\n")
        head = git(root, "rev-parse", "HEAD")
        seen = {}

        def inspecting(current, label, prompt):
            data = json.loads(perf_trees.trees_file(current.config).read_text())
            seen["trees"] = {tree["tree"]: tree["sha"] for tree in data["trees"]}
            seen["paths"] = [Path(tree["path"]) for tree in data["trees"] if tree["tree"] != "head"]
            seen["checked_out"] = [git(path, "rev-parse", "HEAD") for path in seen["paths"]]
            seen["prompt"] = prompt
            Path(re.search(r"Write your verdict to (\S+) and", prompt).group(1)).write_text("VERDICT: PASS\n")

        with replaced_invoke(inspecting), contextlib.redirect_stdout(io.StringIO()):
            run_judge(run_state, find("perf"))
        expect("trees-listed", seen["trees"], {"baseline": start, "head": head, "pre-marestail": readme})
        expect("trees-checked-out", seen["checked_out"], [start, readme])
        expect("trees-prompt", "# Trees\n- baseline: " + start in seen["prompt"], True)
        expect("trees-removed", [path.exists() for path in seen["paths"]], [False, False])
        expect("trees-pruned", worktree_count(root), 1)
        expect("trees-file-removed", perf_trees.trees_file(run_state.config).exists(), False)


def trees_removed_when_invoke_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, readme_first=True)
        run_state = state(root)
        seen = {}

        def raising(current, label, prompt):
            data = json.loads(perf_trees.trees_file(current.config).read_text())
            seen["paths"] = [Path(tree["path"]) for tree in data["trees"] if tree["tree"] != "head"]
            raise RuntimeError("agent crashed")

        with replaced_invoke(raising), contextlib.redirect_stdout(io.StringIO()):
            try:
                run_judge(run_state, find("perf"))
            except RuntimeError:
                pass
        expect("raise-trees-seen", len(seen["paths"]), 2)
        expect("raise-trees-removed", [path.exists() for path in seen["paths"]], [False, False])
        expect("raise-trees-pruned", worktree_count(root), 1)
        expect("raise-trees-file-removed", perf_trees.trees_file(run_state.config).exists(), False)


if __name__ == "__main__":
    pipeline_order()
    freeze_paths()
    pinned_bounce_keeps_only_writes()
    disabled_skip()
    start_commit_recorded_once()
    start_commit_archived()
    pre_marestail_commit()
    trees_during_attempt()
    trees_removed_when_invoke_raises()
    print("perf ok")
