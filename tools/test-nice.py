#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.config import Config
from marestail.nice import DEFAULT, ENV, OOM_SCORE, apply, level, parse

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "marestail" / "cli.py"


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_exit(name, raw):
    try:
        parse(raw)
    except SystemExit as error:
        expect(name, "nice must be an integer 0-19" in str(error), True)
        return
    raise SystemExit(f"{name}: expected SystemExit for {raw!r}")


def parses():
    expect("true", parse(True), DEFAULT)
    expect("false", parse(False), None)
    expect("empty", parse(""), None)
    expect("none", parse(None), None)
    expect("zero", parse(0), None)
    expect("zero-str", parse("0"), None)
    expect("ten", parse(10), 10)
    expect("ten-str", parse("10"), 10)
    expect("cap", parse(99), 99)
    expect_exit("word", "high")
    expect_exit("negative", -1)
    expect_exit("negative-str", "-4")
    expect_exit("floatish", "1.5")


def levels():
    empty = Config(root=ROOT, raw={})
    expect("default-no-config", level(None), DEFAULT)
    expect("default-empty-toml", level(empty), DEFAULT)
    expect("toml-off", level(Config(root=ROOT, raw={"run": {"nice": 0}})), None)
    expect("toml-five", level(Config(root=ROOT, raw={"run": {"nice": 5}})), 5)
    expect("toml-cap", level(Config(root=ROOT, raw={"run": {"nice": 99}})), 19)
    previous = os.environ.get(ENV)
    try:
        os.environ[ENV] = "0"
        expect("env-off-wins", level(Config(root=ROOT, raw={"run": {"nice": 19}})), None)
        os.environ[ENV] = "7"
        expect("env-wins", level(Config(root=ROOT, raw={"run": {"nice": 19}})), 7)
        os.environ[ENV] = ""
        expect("env-empty-off", level(empty), None)
    finally:
        if previous is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = previous


def probe(body: str, extra_env: dict[str, str] | None = None) -> str:
    env = {**os.environ, "PYTHONPATH": str(ROOT), **(extra_env or {})}
    completed = subprocess.run([sys.executable, "-c", body], capture_output=True, text=True, env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"probe failed: {completed.stderr or completed.stdout}")
    return completed.stdout


def applies():
    body = "\n".join(
        [
            "import os, subprocess, sys",
            "from pathlib import Path",
            "from marestail.config import Config",
            "from marestail.nice import OOM_SCORE, apply",
            "apply(Config(root=Path('.'), raw={}))",
            "print('nice', os.getpriority(os.PRIO_PROCESS, 0))",
            "print('oom', Path(f'/proc/{os.getpid()}/oom_score_adj').read_text().strip())",
            "child = subprocess.run([sys.executable, '-c', 'import os; print(os.getpriority(os.PRIO_PROCESS, 0))'], capture_output=True, text=True, check=True)",
            "print('child', child.stdout.strip())",
            "io = subprocess.run(['ionice', '-p', str(os.getpid())], capture_output=True, text=True)",
            "print('io', io.stdout.strip() if io.returncode == 0 else 'missing')",
        ]
    )
    lines = dict(line.split(" ", 1) for line in probe(body).splitlines())
    expect("apply-nice", lines["nice"], "19")
    expect("apply-oom", lines["oom"], str(OOM_SCORE))
    expect("apply-child", lines["child"], "19")
    if lines["io"] != "missing":
        expect("apply-io", "idle" in lines["io"], True)


def skips_when_off():
    before = os.getpriority(os.PRIO_PROCESS, 0)
    body = "\n".join(
        [
            "import os",
            "from pathlib import Path",
            "from marestail.config import Config",
            "from marestail.nice import apply",
            f"before = {before}",
            "apply(Config(root=Path('.'), raw={'run': {'nice': 0}}))",
            "print(os.getpriority(os.PRIO_PROCESS, 0))",
        ]
    )
    expect("skip-toml", probe(body).strip(), str(before))
    expect("skip-env", probe(body.replace("apply(Config(root=Path('.'), raw={'run': {'nice': 0}}))", "apply(Config(root=Path('.'), raw={}))"), {ENV: "0"}).strip(), str(before))


def pipeline_applies():
    with tempfile.TemporaryDirectory(prefix="marestail-nice-") as temp:
        repo = Path(temp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@marestail"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        (repo / "marestail.toml").write_text('[git]\nbase = "main"\n[perf]\nenabled = false\n[practices]\nenabled = false\n')
        (repo / "tasks").mkdir()
        (repo / "tasks" / "t.md").write_text("# t\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        stub = Path(temp) / "stub"
        stub.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json, os, re, sys",
                    "from pathlib import Path",
                    "prompt = sys.stdin.read()",
                    "Path(os.environ['NICE_OUT']).write_text(str(os.getpriority(os.PRIO_PROCESS, 0)))",
                    "verdict = Path(re.search(r'Write your verdict to (\\S+) and', prompt).group(1))",
                    "verdict.parent.mkdir(parents=True, exist_ok=True)",
                    "verdict.write_text('VERDICT: PASS\\n')",
                    "print(json.dumps({'is_error': False, 'num_turns': 1, 'total_cost_usd': 0, 'result': 'ok'}))",
                    "",
                ]
            )
        )
        stub.chmod(0o755)
        out = Path(temp) / "nice.txt"
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "MARESTAIL_CLAUDE": str(stub),
            "MARESTAIL_AGENT": "claude",
            "NICE_OUT": str(out),
        }
        env.pop(ENV, None)
        completed = subprocess.run(
            [sys.executable, str(CLI), "run", "tasks/t.md", "--from", "critic", "--to", "critic", "--auto", "--retries", "1"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(f"pipeline: {completed.stderr or completed.stdout}")
        expect("pipeline-nice", out.read_text().strip(), "19")


if __name__ == "__main__":
    parses()
    levels()
    applies()
    skips_when_off()
    pipeline_applies()
    print("nice ok")
