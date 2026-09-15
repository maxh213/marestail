#!/usr/bin/env python3
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.config import Config
from marestail.pipeline import find
from marestail.prompts import finishing
from marestail.runner import Run, drop_ignored_since, restore_files, run_worker


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def new_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    (root / "marestail.toml").write_text('[git]\nbase = "main"\n')
    (root / ".gitignore").write_text(".marestail/\nfeatures/\nqa/\n")
    (root / "src.py").write_text("ok\n")
    (root / "tasks").mkdir()
    (root / "tasks" / "t.md").write_text("# Add one\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def state(root: Path) -> Run:
    return Run(config=Config(root=root, raw={"git": {"base": "main"}}), task=root / "tasks" / "t.md", model=None, retries=1, agent="claude")


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


def specifier_force_add() -> str:
    return "\n".join(
        [
            "#!/usr/bin/env python3",
            "import json, re, subprocess, sys",
            "from pathlib import Path",
            "prompt = sys.stdin.read()",
            "root = Path.cwd()",
            "def sh(*cmd):",
            "    subprocess.run(cmd, cwd=root, check=True, capture_output=True)",
            "(root / 'features').mkdir(exist_ok=True)",
            "(root / 'qa').mkdir(exist_ok=True)",
            "(root / 'features' / 't.feature').write_text('Feature: t\\n')",
            "(root / 'qa' / 't.md').write_text('1. open\\n')",
            "sh('git', 'add', '-f', 'features', 'qa')",
            "handoff = root / re.search(r'Write (\\.marestail/\\S+?):', prompt).group(1)",
            "handoff.parent.mkdir(parents=True, exist_ok=True)",
            "handoff.write_text('spec written\\n')",
            "sh('git', 'add', '-f', str(handoff.relative_to(root)))",
            "sh('git', 'commit', '-q', '-m', 'Specify t\\n\\nBy specifier.')",
            'print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0, "result": "ok"}))',
            "",
        ]
    )


def prompt_forbids_force():
    text = finishing(Config(root=Path("/tmp"), raw={}), find("specifier"), "t", Path("/tmp/.marestail/handoffs/t/01-specifier.md"), "", "")
    expect("no-commit-everything", "Commit everything" in text, False)
    expect("mentions-force", "git add -f" in text, True)
    expect("mentions-drop", "drops any gitignored path" in text, True)


def drop_keeps_files_off_head():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        before = git(root, "rev-parse", "HEAD")
        (root / "features").mkdir()
        (root / "features" / "t.feature").write_text("Feature: t\n")
        (root / "src.py").write_text("changed\n")
        git(root, "add", "-f", "features/t.feature")
        git(root, "add", "src.py")
        git(root, "commit", "-qm", "force-add")
        saved = drop_ignored_since(Config(root=root, raw={"git": {"base": "main"}}), before)
        restore_files(Config(root=root, raw={}), saved)
        tracked = git(root, "ls-tree", "-r", "--name-only", "HEAD")
        expect("feature-untracked", "features/t.feature" in tracked, False)
        expect("code-kept", "src.py" in tracked, True)
        expect("feature-on-disk", (root / "features" / "t.feature").read_text(), "Feature: t\n")
        expect("saved-feature", saved.get("features/t.feature"), b"Feature: t\n")


def specifier_force_add_stripped():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        with agent_stub(Path(tmp), specifier_force_add()), contextlib.redirect_stdout(io.StringIO()):
            passed = run_worker(state(root), find("specifier"), "")
        expect("specifier-passes", passed, True)
        tracked = git(root, "ls-tree", "-r", "--name-only", "HEAD")
        expect("no-feature", "features/t.feature" in tracked, False)
        expect("no-qa", "qa/t.md" in tracked, False)
        expect("no-handoff", any(line.startswith(".marestail/") for line in tracked.splitlines()), False)
        expect("feature-on-disk", (root / "features" / "t.feature").is_file(), True)
        expect("qa-on-disk", (root / "qa" / "t.md").is_file(), True)
        expect("handoff-on-disk", any((root / ".marestail" / "handoffs" / "t").glob("*-specifier.md")), True)
        expect("commit-mentions-specifier", "By specifier." in git(root, "log", "-1", "--format=%B"), True)


if __name__ == "__main__":
    prompt_forbids_force()
    drop_keeps_files_off_head()
    specifier_force_add_stripped()
    print("drop-ignored ok")
