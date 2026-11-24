import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import offload, remote
from marestail.config import Config
from tests.conftest import make_context

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("pnpm", "npm", "npx", "yarn", "vitest", "playwright", "jest", "stryker", "mutmut", "mix", "mvn", "mvnw")


class FakeStreamed:
    def __init__(self, outcome: remote.Outcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, ctx: Any, gate: str, build: Callable[[int], list[str]], cwd: Path, pull: tuple[str, ...]) -> remote.Outcome:
        self.calls.append((ctx.root, ctx.config.raw, gate, build(0), build(150), cwd, pull))
        return self.outcome


@pytest.fixture
def streamed(monkeypatch: pytest.MonkeyPatch) -> Callable[[remote.Outcome], FakeStreamed]:
    def install(outcome: remote.Outcome) -> FakeStreamed:
        fake = FakeStreamed(outcome)
        monkeypatch.setattr(remote, "run_streamed", fake)
        return fake

    return install


def test_offload_needs_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert offload.offload_command([], []) == 2
    assert offload.offload_command(["--"], []) == 2
    assert capsys.readouterr().err == f"{offload.USAGE}\n{offload.USAGE}\n"


def test_offload_command_strips_the_separator_and_stops_cleanly_on_a_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, streamed: Any
) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(signal, "signal", lambda signum, handler: handlers.__setitem__(signum, handler))
    monkeypatch.chdir(tmp_path)
    fake = streamed(remote.Outcome(4, "", ""))
    assert offload.offload_command(["--", "pnpm", "test"], ["coverage"]) == 4
    assert fake.calls == [(tmp_path, {}, "offload", ["pnpm", "test"], ["pnpm", "test"], tmp_path, ("coverage",))]
    assert handlers == {signal.SIGTERM: offload.stopped, signal.SIGHUP: offload.stopped}
    assert offload.offload_command(["vitest"], []) == 4


def test_stopped_exits_with_the_signal_code() -> None:
    with pytest.raises(SystemExit) as stopped:
        offload.stopped(signal.SIGTERM, None)
    assert stopped.value.code == 143


def test_offloaded_runs_from_the_repo_root_with_its_config(git_repo: Path, streamed: Any, capsys: pytest.CaptureFixture[str]) -> None:
    (git_repo / "marestail.toml").write_text("[remote]\nenabled = false\n")
    web = git_repo / "web"
    web.mkdir()
    fake = streamed(remote.Outcome(0, "", " (on box, 3s)"))
    assert offload.offloaded(["npx", "stryker", "run"], ["out"], web) == 0
    ((root, raw, gate, local, sized, cwd, pull),) = fake.calls
    assert (root, raw, gate, cwd) == (git_repo, {"remote": {"enabled": False}}, "ts.mutation", web)
    assert (local, sized) == (["npx", "stryker", "run"], ["npx", "stryker", "run", "--concurrency", "150"])
    assert pull == ("web/reports/mutation", "web/reports/stryker-incremental.json", "web/out")
    assert capsys.readouterr().err == "marestail offload: (on box, 3s)\n"


def test_repo_root_outside_git_is_the_working_directory(tmp_path: Path) -> None:
    assert offload.repo_root(tmp_path) == tmp_path


def test_repo_config_without_a_toml_is_empty(tmp_path: Path) -> None:
    assert offload.repo_config(tmp_path) == Config(root=tmp_path, raw={})


@pytest.mark.parametrize(
    ("command", "gate"),
    [
        (["npx", "stryker", "run", "--mutate", "src/a.ts"], "ts.mutation"),
        (["npm", "exec", "stryker@9", "run"], "ts.mutation"),
        (["node_modules/.bin/stryker", "run"], "ts.mutation"),
        ([".venv/bin/mutmut", "run"], "py.mutation"),
        (["mix", "muex", "--files", "lib/a.ex"], "ex.mutation"),
        (["mvn", "-B", "org.pitest:pitest-maven:mutationCoverage"], "java.mutation"),
        (["npx", "vitest", "run"], None),
        (["dotnet", "stryker"], None),
        (["dotnet-stryker"], None),
        (["pnpm", "test"], None),
    ],
)
def test_mutation_gate_names_the_mutation_tool(command: list[str], gate: str | None) -> None:
    assert offload.mutation_gate(command) == gate


@pytest.mark.parametrize(
    ("command", "gate", "workers", "expected"),
    [
        (["stryker", "run"], "ts.mutation", 8, ["stryker", "run", "--concurrency", "8"]),
        (["stryker", "run", "--concurrency", "2"], "ts.mutation", 8, ["stryker", "run", "--concurrency", "2"]),
        (["stryker", "run"], "ts.mutation", 0, ["stryker", "run"]),
        (["mutmut", "run"], "py.mutation", 8, ["mutmut", "run"]),
    ],
)
def test_with_workers_sizes_only_stryker(command: list[str], gate: str, workers: int, expected: list[str]) -> None:
    assert offload.with_workers(command, gate, workers) == expected


def test_pulled_is_relative_to_the_root_and_stays_inside_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    web = root / "web"
    web.mkdir(parents=True)
    pulls = ["coverage", "../api/out.json", str(root / "abs"), "../../elsewhere"]
    assert offload.pulled(root, web, "offload", pulls) == ("web/coverage", "api/out.json", "abs")
    assert offload.pulled(root, root, "py.mutation", []) == ("mutants",)


def test_shims_path_prints_the_folder(capsys: pytest.CaptureFixture[str]) -> None:
    assert offload.shims_path_command() == 0
    assert capsys.readouterr().out == f"{remote.SHIMS}\n"


def test_agent_env_without_a_box_adds_nothing(tmp_path: Path) -> None:
    assert offload.agent_env(make_context(tmp_path).config) == {}


def test_agent_env_with_a_box_puts_the_shims_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "remote.toml"
    config.write_text('[remote]\ninstance = "b"\nproject = "p"\nzone = "z"\n')
    monkeypatch.setattr(remote, "CONFIG", config)
    monkeypatch.delenv(remote.ENV)
    monkeypatch.setenv("PATH", f"/usr/bin:{remote.SHIMS}:/bin")
    assert offload.agent_env(make_context(tmp_path).config) == {
        "PATH": f"{remote.SHIMS}:/usr/bin:/bin",
        "BASH_ENV": str(ROOT / "marestail" / "offload.bash"),
        "MARESTAIL_NO_OFFLOAD": "",
    }
    assert offload.agent_env(make_context(tmp_path, {"remote": {"enabled": False}}).config) == {}


def test_every_shim_is_an_executable_script() -> None:
    assert sorted(path.name for path in remote.SHIMS.iterdir()) == sorted([*TOOLS, "_offload.sh"])
    assert [tool for tool in TOOLS if not os.access(remote.SHIMS / tool, os.X_OK)] == []


@pytest.fixture
def shimmed(tmp_path: Path) -> Path:
    home = tmp_path / "install"
    shutil.copytree(remote.SHIMS, home / "marestail" / "shims")
    shutil.copy(ROOT / "marestail" / "offload.bash", home / "marestail" / "offload.bash")
    (home / "bin").mkdir()
    fake(home / "bin", "marestail", 'echo "offloaded: $*"')
    real = tmp_path / "real"
    real.mkdir()
    for tool in TOOLS:
        fake(real, tool, f'echo "real {tool} $* guard=${{MARESTAIL_NO_OFFLOAD:-}} path=$PATH"')
    return home


def fake(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def run_shim(home: Path, command: str, **env: str) -> str:
    shims = home / "marestail" / "shims"
    real = home.parent / "real"
    path = f"{shims}:{real}:{os.environ['PATH']}"
    clean = {key: value for key, value in os.environ.items() if key not in {remote.GUARD, remote.ENV, "BASH_ENV"}}
    completed = subprocess.run(
        ["bash", "-c", command], env={**clean, "PATH": path, **env}, capture_output=True, text=True, check=False, timeout=60
    )
    return completed.stdout.strip() + completed.stderr.strip()


OFFLOADED = [
    "pnpm test",
    "pnpm test:unit --run",
    "pnpm t",
    "pnpm check",
    "pnpm run test:e2e",
    "pnpm run-script check",
    "pnpm --filter web test",
    "pnpm -C apps/web run test",
    "pnpm vitest run src",
    "pnpm exec vitest",
    "pnpm exec playwright test --project chromium",
    "pnpm dlx jest",
    "pnpm exec stryker run",
    "pnpm playwright test",
    "npm test",
    "npm t",
    "npm run test:unit",
    "npm --workspace web run test",
    "npm exec -- vitest run",
    "npx vitest run",
    "npx -y playwright@1.64.1 test",
    "npx --package jest jest",
    "npx stryker run",
    "yarn test",
    "yarn test:ci",
    "yarn run check",
    "yarn --cwd web vitest",
    "yarn exec jest",
    "vitest",
    "vitest run --coverage",
    "jest --ci",
    "playwright test",
    "stryker run",
    "node_modules/.bin/vitest run",
    "mutmut run",
    "mix muex --files lib/a.ex",
    "mvn -B org.pitest:pitest-maven:mutationCoverage",
    "mvnw org.pitest:pitest-maven:mutationCoverage",
]
PASSED_THROUGH = [
    "pnpm install --frozen-lockfile",
    "pnpm run build",
    "pnpm lint",
    "pnpm exec tsc --noEmit",
    "pnpm playwright install chromium",
    "pnpm",
    "pnpm --filter",
    "npm ci",
    "npm run lint",
    "npm exec eslint",
    "npm check",
    "npx tsc",
    "npx playwright install",
    "npx @playwright/test",
    "yarn install",
    "yarn check",
    "yarn run build",
    "playwright install",
    "playwright show-report",
    "stryker init",
    "mutmut results",
    "mix test",
    "mvn test",
]


@pytest.mark.parametrize("command", OFFLOADED)
def test_a_test_run_goes_to_marestail_offload(shimmed: Path, command: str) -> None:
    tool, _, rest = command.partition(" ")
    shim = shimmed / "marestail" / "shims" / Path(tool).name
    called = command if "/" not in tool else f"{shim} {rest}"
    assert run_shim(shimmed, called) == f"offloaded: offload -- {Path(tool).name} {rest}".rstrip()


@pytest.mark.parametrize("command", PASSED_THROUGH)
def test_anything_else_runs_the_real_tool_without_the_shims_on_path(shimmed: Path, command: str) -> None:
    tool, _, rest = command.partition(" ")
    real = shimmed.parent / "real"
    assert run_shim(shimmed, command) == f"real {tool} {rest} guard= path={real}:{os.environ['PATH']}"


@pytest.mark.parametrize("env", [{remote.GUARD: "1"}, {remote.ENV: "0"}, {remote.ENV: "Off"}, {remote.ENV: "no"}, {remote.ENV: "FALSE"}])
def test_the_guard_or_a_switched_off_remote_passes_test_runs_through(shimmed: Path, env: dict[str, str]) -> None:
    assert run_shim(shimmed, "pnpm test", **env).startswith("real pnpm test guard=")


def test_bash_env_routes_a_tool_that_comes_before_the_shims(shimmed: Path) -> None:
    real = shimmed.parent / "real"
    bash_env = str(shimmed / "marestail" / "offload.bash")
    command = f'PATH="{real}:$PATH"; pnpm test; pnpm install'
    assert run_shim(shimmed, command, BASH_ENV=bash_env).splitlines() == [
        "offloaded: offload -- pnpm test",
        f"real pnpm install guard= path={real}:{real}:{os.environ['PATH']}",
    ]


def test_a_missing_real_tool_is_not_found(shimmed: Path) -> None:
    shim = shimmed / "marestail" / "shims" / "mvnw"
    clean = {key: value for key, value in os.environ.items() if key not in {remote.GUARD, remote.ENV}}
    env = {**clean, "PATH": f"{shim.parent}:/usr/bin:/bin"}
    completed = subprocess.run([str(shim), "verify"], env=env, capture_output=True, text=True, check=False, timeout=60)
    assert completed.returncode == 127
    assert "mvnw" in completed.stderr


def test_marestail_offload_runs_locally_when_the_remote_is_off(tmp_path: Path) -> None:
    env = {**os.environ, remote.ENV: "0", "PATH": f"{remote.SHIMS}:{os.environ['PATH']}"}
    command = 'echo "$MARESTAIL_NO_OFFLOAD $(pwd)"; case ":$PATH:" in *marestail/shims*) echo shimmed;; esac; exit 3'
    completed = subprocess.run(
        [sys.executable, str(ROOT / "marestail" / "cli.py"), "offload", "--", "sh", "-c", command],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (completed.returncode, completed.stdout, completed.stderr) == (3, f"1 {tmp_path}\n", "")
