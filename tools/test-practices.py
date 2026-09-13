#!/usr/bin/env python3
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import freeze, practices, runner
from marestail.config import Config
from marestail.pipeline import find, names
from marestail.runner import Run, run_judge, run_step


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def new_repo(tmp: str, guidance: bool = False) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    (root / "marestail.toml").write_text('[git]\nbase = "main"\n')
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "src.py").write_text("original\n")
    if guidance:
        (root / "guidance").mkdir()
        (root / "guidance" / "ts.md").write_text("# TypeScript practices\n\n- TS-1: prefer union types over enums\n")
    (root / "tasks").mkdir()
    (root / "tasks" / "t.md").write_text("# Add one\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def state(root: Path, raw=None, retries: int = 1) -> Run:
    return Run(config=Config(root=root, raw=raw or {"git": {"base": "main"}}), task=root / "tasks" / "t.md", model=None, retries=retries, agent="claude")


def passing_judge(verdict: str) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env python3",
            "import json, re, sys",
            "from pathlib import Path",
            "prompt = sys.stdin.read()",
            'verdict = Path(re.search(r"Write your verdict to (\\S+) and", prompt).group(1))',
            f"verdict.write_text({verdict!r})",
            'print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0, "result": "ok"}))',
            "",
        ]
    )


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


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
    expect("pipeline", names(), ["specifier", "critic", "coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"])
    judge = find("practices")
    expect("practices-bounce-to", judge.bounce_to, "coder")
    expect("practices-tier", judge.tier, None)
    expect("practices-writes", judge.writes, ())
    expect("practices-pinned", judge.pinned_bounce, True)
    expect("practices-optional", judge.optional, True)


def guidance_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expect("missing-folder", practices.files(root), [])
        folder = root / "guidance"
        folder.mkdir()
        (folder / "b.md").write_text("b\n")
        (folder / "a.md").write_text("a\n")
        (folder / "c.txt").write_text("c\n")
        (folder / "sub").mkdir()
        (folder / "sub" / "x.md").write_text("x\n")
        expect("found-sorted", [path.name for path in practices.files(root)], ["a.md", "b.md"])


def no_guidance_skip():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        before = git(root, "rev-parse", "HEAD")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = run_step(state(root), find("practices"))
        expect("noguidance-passes", passed, True)
        expect("noguidance-message", "practices: no guidance files; skipping" in output.getvalue(), True)
        expect("noguidance-no-commit", git(root, "rev-parse", "HEAD"), before)


def disabled_skip():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, guidance=True)
        before = git(root, "rev-parse", "HEAD")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = run_step(state(root, {"git": {"base": "main"}, "practices": {"enabled": False}}), find("practices"))
        expect("disabled-passes", passed, True)
        expect("disabled-message", "practices disabled in marestail.toml; skipping" in output.getvalue(), True)
        expect("disabled-no-commit", git(root, "rev-parse", "HEAD"), before)


def freeze_guidance():
    config = Config(root=Path("/tmp"), raw={})
    expect("spec-has-guidance", "guidance/**" in freeze.SPEC, True)
    expect("coder-frozen", freeze.frozen_paths(config, "coder", ["guidance/ts.md", "src/app.py"]), ["guidance/ts.md"])
    expect("practices-frozen", freeze.frozen_paths(config, "practices", ["guidance/ts.md", "src/app.py"]), ["guidance/ts.md"])


def pass_commit():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, guidance=True)
        with agent_stub(Path(tmp), passing_judge("VERDICT: PASS\n")), quiet():
            outcome, target, _ = run_judge(state(root), find("practices"))
        expect("pass-verdict", (outcome, target), ("PASS", None))
        expect("pass-subject", "practices verdict: PASS" in git(root, "log", "-1", "--format=%s"), True)
        expect("pass-commit-empty", git(root, "show", "--name-only", "--format=", "HEAD"), "")
        expect("guidance-kept", (root / "guidance" / "ts.md").read_text(), "# TypeScript practices\n\n- TS-1: prefer union types over enums\n")
        expect("tree-clean", git(root, "status", "--porcelain"), "")


def role_file():
    role = Path(__file__).resolve().parent.parent / "roles" / "practices.md"
    expect("role-exists", role.is_file(), True)
    expect("role-mentions-guidance", "guidance" in role.read_text(), True)


if __name__ == "__main__":
    pipeline_order()
    guidance_files()
    no_guidance_skip()
    disabled_skip()
    freeze_guidance()
    pass_commit()
    role_file()
    print("practices ok")
