import os
import signal
import sys
import tomllib
from functools import partial
from pathlib import Path
from types import FrameType

from marestail import remote
from marestail.config import FILENAME, Config
from marestail.context import Context
from marestail.shell import run

OFFLOAD_BASH = Path(__file__).resolve().parent / "offload.bash"
USAGE = "usage: marestail offload [--pull PATH]... -- COMMAND [ARGS...]"
STRYKER = "ts.mutation"
NAMED_TOOLS = (("muex", "ex.mutation"), ("mutmut", "py.mutation"), ("stryker", STRYKER))
DOTNET = {"dotnet", "dotnet-stryker"}
PULLED = {STRYKER: ("reports/mutation", "reports/stryker-incremental.json"), "py.mutation": ("mutants",)}
STOPPING = (signal.SIGTERM, signal.SIGHUP)


def offload_command(argv: list[str], pulls: list[str]) -> int:
    command = argv[1:] if argv[:1] == ["--"] else argv
    if not command:
        print(USAGE, file=sys.stderr)
        return 2
    for stopping in STOPPING:
        signal.signal(stopping, stopped)
    return offloaded(command, pulls, Path.cwd())


def offloaded(command: list[str], pulls: list[str], cwd: Path) -> int:
    ctx = Context(config=repo_config(repo_root(cwd)))
    gate = mutation_gate(command) or remote.OFFLOAD
    outcome = remote.run_streamed(ctx, gate, partial(with_workers, command, gate), cwd, pulled(ctx.root, cwd, gate, pulls))
    if outcome.where:
        print(f"marestail offload:{outcome.where}", file=sys.stderr)
    return outcome.code


def stopped(signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def repo_root(cwd: Path) -> Path:
    code, output = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, timeout=30)
    top = output.strip()
    return Path(top) if code == 0 and top else cwd


def repo_config(root: Path) -> Config:
    path = root / FILENAME
    return Config(root=root, raw=tomllib.loads(path.read_text()) if path.is_file() else {})


def mutation_gate(command: list[str]) -> str | None:
    if any("pitest" in word for word in command):
        return "java.mutation"
    return named_gate({Path(word).name.split("@")[0] for word in command})


def named_gate(names: set[str]) -> str | None:
    if names & DOTNET:
        return None
    return next((gate for tool, gate in NAMED_TOOLS if tool in names), None)


def with_workers(command: list[str], gate: str, workers: int) -> list[str]:
    if gate != STRYKER or not workers or "--concurrency" in command:
        return command
    return [*command, "--concurrency", str(workers)]


def pulled(root: Path, cwd: Path, gate: str, pulls: list[str]) -> tuple[str, ...]:
    wanted = [(cwd / path).resolve() for path in [*PULLED.get(gate, ()), *pulls]]
    return tuple(str(path.relative_to(root.resolve())) for path in wanted if path.is_relative_to(root.resolve()))


def shims_path_command() -> int:
    print(remote.SHIMS)
    return 0


def box_configured(config: Config) -> bool:
    return remote.settings_for(config) is not None


def agent_env(config: Config) -> dict[str, str]:
    if not box_configured(config):
        return {}
    path = remote.without_shims(os.environ.get("PATH", ""))
    return {"PATH": f"{remote.SHIMS}{os.pathsep}{path}", "BASH_ENV": str(OFFLOAD_BASH), remote.GUARD: ""}
