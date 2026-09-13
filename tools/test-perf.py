#!/usr/bin/env python3
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import freeze
from marestail.config import Config
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


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(root: Path) -> Path:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    (root / "marestail.toml").write_text('[git]\nbase = "main"\n')
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "src.py").write_text("original\n")
    (root / "tasks").mkdir()
    (root / "tasks" / "t.md").write_text("# Add one\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def state(root: Path, raw=None) -> Run:
    return Run(config=Config(root=root, raw=raw or {}), task=root / "tasks" / "t.md", model=None, retries=1, agent="claude")


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
        root = Path(tmp) / "repo"
        root.mkdir()
        make_repo(root)
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
        root = Path(tmp) / "repo"
        root.mkdir()
        make_repo(root)
        before = git(root, "rev-parse", "HEAD")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = run_step(state(root, {"perf": {"enabled": False}}), find("perf"))
        expect("disabled-passes", passed, True)
        expect("disabled-message", "perf disabled in marestail.toml; skipping" in output.getvalue(), True)
        expect("disabled-no-commit", git(root, "rev-parse", "HEAD"), before)


if __name__ == "__main__":
    pipeline_order()
    freeze_paths()
    pinned_bounce_keeps_only_writes()
    disabled_skip()
    print("perf ok")
