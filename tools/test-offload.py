#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import socket

from marestail import remote, runner
from marestail.config import Config
from marestail.perf import samples, trees
from marestail.remote import SHIMS, Outcome, mutation_gate, without_shims
from marestail.runner import Run, agent_env, agent_process

TOOLS = ("npx", "npm", "pnpm", "yarn", "stryker", "mutmut", "mix", "mvn")


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def fake_tool(folder: Path, name: str) -> None:
    path = folder / name
    path.write_text(f'#!/usr/bin/env bash\necho "fake-{name} local=${{MARESTAIL_MUTATION_LOCAL:-}} $*"\n')
    path.chmod(0o755)


def shim(tool: str, args: list[str], fake: Path, cwd: Path, **extra: str) -> str:
    env = {**os.environ, "PATH": f"{SHIMS}:{fake}:{os.environ['PATH']}", "MARESTAIL_REMOTE": "0", **extra}
    completed = subprocess.run([str(SHIMS / tool), *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)
    return (completed.stdout + completed.stderr).strip()


expect("npx stryker", mutation_gate(["npx", "stryker", "run", "--mutate", "src/a.ts"]), "ts.mutation")
expect("npm exec", mutation_gate(["npm", "exec", "stryker", "run"]), "ts.mutation")
expect("bin path", mutation_gate(["node_modules/.bin/stryker", "run"]), "ts.mutation")
expect("mutmut", mutation_gate([".venv/bin/mutmut", "run"]), "py.mutation")
expect("muex", mutation_gate(["mix", "muex", "--files", "lib/a.ex"]), "ex.mutation")
expect("pitest", mutation_gate(["mvn", "-B", "org.pitest:pitest-maven:mutationCoverage"]), "java.mutation")
expect("vitest", mutation_gate(["npx", "vitest", "run"]), None)
expect("dotnet stryker stays put", mutation_gate(["dotnet", "stryker"]), None)
expect("plain npm", mutation_gate(["npm", "test"]), None)

state = Run(config=Config(root=Path("/tmp"), raw={}), task=Path("t.md"), model=None, retries=0)
env = agent_env(state)
if not env["BASH_ENV"].endswith("/marestail/offload.bash"):
    raise SystemExit(f"BASH_ENV: {env['BASH_ENV']}")
if not Path(env["BASH_ENV"]).is_file():
    raise SystemExit("offload.bash missing")
expect("shims first on PATH", env["PATH"].split(":")[0], str(SHIMS))
expect("rest of PATH kept", env["PATH"].split(":", 1)[1], os.environ.get("PATH", ""))
for tool in TOOLS:
    if not os.access(SHIMS / tool, os.X_OK):
        raise SystemExit(f"shim missing or not executable: {tool}")
expect("without_shims", without_shims(f"{SHIMS}:/usr/bin:{SHIMS}:/bin"), "/usr/bin:/bin")

with tempfile.TemporaryDirectory() as temp:
    fake = Path(temp) / "fake"
    fake.mkdir()
    for tool in ("npx", "npm", "mutmut"):
        fake_tool(fake, tool)
    repo = Path(temp) / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "marestail.toml").write_text('[git]\nbase = "main"\n')
    # Not a mutation run: the shim steps aside and the real tool sees a PATH without the shims.
    expect("npm passthrough", shim("npm", ["--version"], fake, repo), "fake-npm local= --version")
    # A mutation run goes through `marestail offload`; with the box off it is run locally, marked as such, by the real tool.
    expect("npx stryker routed", shim("npx", ["stryker", "run", "--mutate", "a.ts"], fake, repo), "fake-npx local=1 stryker run --mutate a.ts")
    expect("mutmut routed", shim("mutmut", ["run", "x*"], fake, repo), "fake-mutmut local=1 run x*")
    # A run marestail started itself must not be sent round again.
    expect("local run passes", shim("npx", ["stryker", "run"], fake, repo, MARESTAIL_MUTATION_LOCAL="1"), "fake-npx local=1 stryker run")

    marker = Path(temp) / "child"
    started = time.time()
    code, out, err = agent_process(["bash", "-c", f"sleep 300 & echo $! > {marker}; wait"], Path(temp), dict(os.environ), "", timeout=1)
    expect("timeout code", code, 124)
    expect("timeout text", err, "bash: timed out after 1s")
    if time.time() - started > 20:
        raise SystemExit("timeout took too long to return")
    child = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        if Path(f"/proc/{child}/status").exists() and "zombie" in Path(f"/proc/{child}/status").read_text():
            break
        time.sleep(0.1)
    else:
        os.kill(child, 9)
        raise SystemExit("the agent's child outlived the timeout")
    code, out, err = agent_process(["/nonexistent/agent"], Path(temp), dict(os.environ), "")
    expect("missing code", code, 127)
    expect("missing text", err.startswith("/nonexistent/agent: not found"), True)
    code, out, err = agent_process(["bash", "-c", "cat; echo err >&2; exit 3"], Path(temp), dict(os.environ), "prompt")
    expect("stdin and streams", (code, out, err), (3, "prompt", "err\n"))
    expect("default timeout", runner.AGENT_TIMEOUT, 4 * 3600)

    # Perf samples: with no box they are taken here and remembered as such.
    os.environ["MARESTAIL_REMOTE"] = "0"
    host = socket.gethostname()
    root = Path(temp) / "perf-repo"
    (root / ".marestail").mkdir(parents=True)
    config = Config(root=root, raw={})
    expect("perf target local", remote.perf_target(config), host)
    expect("no remote sample", remote.run_sample(config, "perf/bench_x.py", root, {}, 10), None)
    same = json.dumps({"script": "b", "fingerprint": "f", "where": host})
    expect("fresh here", samples.stale(same, "b", "f", host), False)
    expect("old record is local", samples.stale(json.dumps({"script": "b", "fingerprint": "f"}), "b", "f", host), False)
    expect("taken elsewhere", samples.stale(same, "b", "f", "marestail-mutation-usw4"), True)
    expect("db benches stay put", samples.stale(json.dumps({"script": "b", "fingerprint": "f", "db": True, "where": host}), "b", "f", "box"), False)
    expect("bench changed", samples.stale(same, "b", "g", host), True)
    expect("other bench", samples.stale(same, "c", "g", "box"), False)
    expect("no where asked", samples.stale(same, "b", "f"), False)
    # A sample the box took is parsed like a local one and records where it ran.
    line = json.dumps({"target": "t", "unit": "ms", "better": "lower", "value": 3})
    remote.run_sample = lambda *args, **kwargs: Outcome(0, f"npm notice\n{line}\n", " (on box, 3s)")
    remote.perf_target = lambda config: "box"
    tree = trees.Tree("head", "abc", root)
    expect("remote sample ok", samples.take_sample(config, "perf/bench_x.py", tree, 1, (None, "f")), "")
    taken = json.loads(trees.samples_file(config).read_text().splitlines()[-1])
    expect("remote sample record", (taken["where"], taken["tree"], taken["value"], taken["sample"]), ("box", "head", 3, 1))
    remote.run_sample = lambda *args, **kwargs: Outcome(1, "boom", " (on box, 1s)")
    expect("remote sample failed", samples.take_sample(config, "perf/bench_x.py", tree, 2, (None, "f")), "perf/bench_x.py sample 2 on head (on box, 1s) exited 1")

print("ok")
