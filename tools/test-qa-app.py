#!/usr/bin/env python3
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import config as cm
from marestail import gates, prompts, runner
from marestail.config import Config
from marestail.gates import qa
from marestail.pipeline import find
from marestail.shell import run as shell_run
from tests.conftest import make_context

ROOT = Path(__file__).resolve().parent.parent
APP = (
    "import os, http.server as h\n"
    'print(os.environ.get("CMS_URL", ""), os.environ.get("LOCALE", ""), flush=True)\n'
    'h.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), h.SimpleHTTPRequestHandler).serve_forever()\n'
)
NOTE = "The app is started for the qa gate; its address is in MARESTAIL_APP_URL."
QA_ENV_KEYS = ("MARESTAIL_TASK", "MARESTAIL_QA_CMD_TIMEOUT")


def expect(name: str, got: Any, wanted: Any) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_true(name: str, value: Any) -> None:
    if not value:
        raise SystemExit(f"{name}: {value!r}")


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def group_gone(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True


def bind_port(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen()
    return sock


def run_qa(root: Path, raw: dict[str, Any], env: dict[str, str] | None = None) -> Any:
    with qa_env_vars(env):
        return qa.run_gate(make_context(root, raw))


@contextmanager
def qa_env_vars(env: dict[str, str] | None) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in QA_ENV_KEYS}
    apply_qa_env(env or {})
    try:
        yield
    finally:
        restore_env(previous)


def apply_qa_env(wanted: dict[str, str]) -> None:
    for key in QA_ENV_KEYS:
        drop_unless_set(key, wanted)
    os.environ.update(wanted)


def drop_unless_set(key: str, wanted: dict[str, str]) -> None:
    if key not in wanted:
        os.environ.pop(key, None)


def restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        restore_one(key, value)


def restore_one(key: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(key, None)
        return
    os.environ[key] = value


def capture_spawn(root: Path, raw: dict[str, Any], env: dict[str, str] | None = None) -> tuple[Any, int | None]:
    seen: dict[str, int | None] = {"pid": None}
    real = qa.spawn

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        process = real(*args, **kwargs)
        seen["pid"] = process.pid
        return process

    with patch.object(qa, "spawn", wrapped):
        result = run_qa(root, raw, env)
    return result, seen["pid"]


def ready_before_cmd(folder: Path) -> None:
    root = folder / "ready"
    root.mkdir()
    write(root, "app.py", APP)
    order: list[str] = []
    real_answers = qa.answers

    def track(url: str) -> bool:
        ok = real_answers(url)
        if ok and "ready" not in order:
            order.append("ready")
        return ok

    real_run = shell_run

    def track_run(command: list[str], cwd: Path, **options: Any) -> tuple[int, str]:
        order.append("cmd")
        code, output = real_run(command, cwd, **options)
        return code, output + f"\nURL={options.get('env', {}).get('MARESTAIL_APP_URL')}\nPORT={options.get('env', {}).get('PORT')}\n"

    with patch.object(qa, "answers", track), patch.object(qa, "run", track_run):
        result, pid = capture_spawn(
            root,
            {"qa": {"cmd": "true", "start": "python3 app.py", "ready": "/", "port": 3400}},
        )
    expect_true("ready-ok", result.ok)
    expect_true("ready-summary", "qa passed" in result.summary)
    expect("ready-order", order[:2], ["ready", "cmd"])
    expect_true("ready-gone", pid is None or group_gone(pid))


def cleanup_on_fail(folder: Path) -> None:
    root = folder / "fail"
    root.mkdir()
    write(root, "app.py", APP)
    result, pid = capture_spawn(root, {"qa": {"cmd": "exit 1", "start": "python3 app.py", "ready": "/", "port": 3410}})
    expect("fail-ok", result.ok, False)
    expect_true("fail-gone", pid is None or group_gone(pid))


def never_listens(folder: Path) -> None:
    root = folder / "sleep"
    root.mkdir()
    marker = root / "ran-cmd"
    result, pid = capture_spawn(
        root,
        {
            "qa": {
                "cmd": f"touch {marker}",
                "start": "sleep 60",
                "ready": "/",
                "port": 3420,
                "ready_timeout": 2,
            }
        },
    )
    expect("sleep-ok", result.ok, False)
    expect("sleep-summary", result.summary, "qa: app did not answer on http://localhost:3420/ within 2s")
    expect_true("sleep-findings", isinstance(result.findings, list))
    expect("sleep-cmd", marker.exists(), False)
    expect_true("sleep-gone", pid is None or group_gone(pid))


def next_free_port(folder: Path) -> None:
    root = folder / "port"
    root.mkdir()
    write(root, "app.py", APP)
    binder = bind_port(3400)
    try:
        result, pid = capture_spawn(
            root,
            {
                "qa": {
                    "cmd": "printf '%s' \"$MARESTAIL_APP_URL\" > seen-url",
                    "start": "python3 app.py",
                    "ready": "/",
                    "port": 3400,
                }
            },
        )
        expect_true("port-ok", result.ok)
        seen = (root / "seen-url").read_text()
        expect_true("port-url", seen.startswith("http://localhost:"))
        port = int(seen.rsplit(":", 1)[1])
        expect_true("port-higher", port > 3400)
        expect_true("port-gone", pid is None or group_gone(pid))
        expect("port-free-again", listening(port), False)
    finally:
        binder.close()


def env_to_app_not_prompt(folder: Path) -> None:
    root = folder / "env"
    root.mkdir()
    write(root, "app.py", APP)
    write(root, "tasks/t.md", "task\n")
    write(root, "roles/qa.md", "You are QA.\n")
    result, pid = capture_spawn(
        root,
        {
            "qa": {
                "cmd": "true",
                "start": "python3 app.py",
                "ready": "/",
                "port": 3430,
                "env": {"CMS_URL": "https://example.test/g", "LOCALE": "en-gb"},
            }
        },
    )
    expect_true("env-ok", result.ok)
    log = (root / ".marestail" / "qa-app.log").read_text()
    expect_true("env-cms", "https://example.test/g" in log)
    expect_true("env-locale", "en-gb" in log)
    with patch.object(prompts, "ROLES_DIR", root / "roles"):
        text = prompts.worker_prompt(
            Config(root=root, raw={"qa": {"start": "python3 app.py", "env": {"CMS_URL": "https://example.test/g", "LOCALE": "en-gb"}}}),
            find("qa"),
            root / "tasks" / "t.md",
            "t",
            write(root, ".marestail/r.md", ""),
            "",
        )
    expect_true("env-note", NOTE in text)
    expect("env-no-cms", "example.test" in text, False)
    expect("env-no-locale", "en-gb" in text, False)
    expect_true("env-gone", pid is None or group_gone(pid))


def no_start_unchanged(folder: Path) -> None:
    root = folder / "nostart"
    root.mkdir()
    (root / ".marestail").mkdir()
    result, pid = capture_spawn(root, {"qa": {"cmd": "true", "cwd": "."}})
    expect_true("nostart-ok", result.ok)
    expect("nostart-pid", pid, None)
    expect("nostart-log", (root / ".marestail" / "qa-app.log").exists(), False)


def empty_cmd_skips_with_start(folder: Path) -> None:
    root = folder / "empty"
    root.mkdir()
    write(root, "app.py", APP)
    result, pid = capture_spawn(root, {"qa": {"cmd": "", "start": "python3 app.py"}})
    expect("empty-summary", result.summary, "skipped: no [qa] cmd configured")
    expect("empty-ok", result.ok, True)
    expect("empty-pid", pid, None)
    expect("empty-log", (root / ".marestail" / "qa-app.log").exists(), False)


def start_in_cwd(folder: Path) -> None:
    root = folder / "cwd"
    root.mkdir()
    write(root, "web/app.py", APP)
    result, pid = capture_spawn(
        root,
        {"qa": {"cmd": "true", "cwd": "web", "start": "python3 app.py", "ready": "/", "port": 3440}},
    )
    expect_true("cwd-ok", result.ok)
    expect_true("cwd-gone", pid is None or group_gone(pid))


def only_qa_tier() -> None:
    expect("tier-fast", "qa" in gates.tiers_for("fast"), False)
    expect("tier-sonar", "qa" in gates.tiers_for("sonar"), False)
    expect("tier-full", "qa" in gates.tiers_for("full"), False)
    expect("tier-qa", "qa" in gates.tiers_for("qa"), True)


def log_paths(folder: Path) -> None:
    root = folder / "logs"
    root.mkdir()
    write(root, "app.py", APP)
    result, _ = capture_spawn(root, {"qa": {"cmd": "true", "start": "python3 app.py", "ready": "/", "port": 3450}})
    expect_true("log-bare-ok", result.ok)
    expect_true("log-bare", (root / ".marestail" / "qa-app.log").exists())
    result, _ = capture_spawn(
        root,
        {"qa": {"cmd": "true", "start": "python3 app.py", "ready": "/", "port": 3451}},
        env={"MARESTAIL_TASK": "t"},
    )
    expect_true("log-task-ok", result.ok)
    expect_true("log-task", (root / ".marestail" / "runs" / "t" / "qa-app.log").exists())


def runner_sets_task() -> None:
    os.environ.pop("MARESTAIL_TASK", None)
    config = Config(root=ROOT, raw={})
    with (
        patch.object(cm, "load", return_value=config),
        patch.object(runner, "run_steps", return_value=0),
        patch.object(runner.perf_trees, "record_start"),
    ):
        runner.run_pipeline(Path("tasks/006-marestail-starts-the-app-for-qa.md"), "specifier", "specifier", True, None, 0)
    expect("runner-task", os.environ.get("MARESTAIL_TASK"), "006-marestail-starts-the-app-for-qa")


def cleanup_on_timeout(folder: Path) -> None:
    root = folder / "timeout"
    root.mkdir()
    write(root, "app.py", APP)
    result, pid = capture_spawn(
        root,
        {"qa": {"cmd": "sleep 30", "start": "python3 app.py", "ready": "/", "port": 3460}},
        env={"MARESTAIL_QA_CMD_TIMEOUT": "1"},
    )
    expect("timeout-ok", result.ok, False)
    expect_true("timeout-code", "124" in result.summary or "timed out" in result.summary.lower() or not result.ok)
    expect_true("timeout-gone", pid is None or group_gone(pid))


def cleanup_on_interrupt(folder: Path) -> None:
    root = folder / "int"
    root.mkdir()
    write(root, "app.py", APP)
    child = start_interrupt_child(root)
    await_port(child, 3470)
    stop_with_sigint(child)
    wait_while(lambda: listening(3470), 10)
    expect("interrupt-gone", listening(3470), False)


def start_interrupt_child(root: Path) -> subprocess.Popen[Any]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("MARESTAIL_TASK", None)
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from marestail.gates import qa; from tests.conftest import make_context; "
            "qa.run_gate(make_context(Path('.'), "
            "{'qa': {'cmd': 'sleep 60', 'start': 'python3 app.py', 'ready': '/', 'port': 3470}}))",
        ],
        cwd=root,
        env=env,
    )


def await_port(child: subprocess.Popen[Any], port: int) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        if listening(port):
            return
        if child.poll() is not None:
            raise SystemExit(f"interrupt-child-exited-early: {child.returncode}")
        time.sleep(0.2)
    raise SystemExit("interrupt-port-timeout")


def stop_with_sigint(child: subprocess.Popen[Any]) -> None:
    child.send_signal(signal.SIGINT)
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def wait_while(predicate: Any, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and predicate():
        time.sleep(0.2)


def install_template() -> None:
    text = (ROOT / "templates" / "marestail.toml").read_text()
    for key in ("start", "ready", "port", "ready_timeout", "env"):
        expect_true(f"template-{key}", f"# {key}" in text or f"#{key}" in text)
    expect_true("template-cmd", "cmd =" in text.split("[qa]", 1)[1])
    expect_true("template-cwd", "cwd =" in text.split("[qa]", 1)[1])


def readme_docs() -> None:
    text = (ROOT / "README.md").read_text()
    for key in ("start", "ready", "port", "ready_timeout", "env"):
        expect_true(f"readme-{key}", key in text.split("Acceptance", 1)[1].split("Tiers:", 1)[0])
    expect_true("readme-tier", "only for the `qa` tier" in text)
    section = text.split("## Environment variables", 1)[1]
    expect_true("readme-task", "`MARESTAIL_TASK`" in section)
    expect_true("readme-timeout", "`MARESTAIL_QA_CMD_TIMEOUT`" in section)


def freeze_unchanged() -> None:
    from marestail.freeze import GATE_CONFIG, SPEC, frozen_paths

    expect("freeze-qa-glob", "qa/**" in SPEC, True)
    expect("freeze-no-playwright-in-gate", any("playwright.config" in p for p in GATE_CONFIG), False)
    config = Config(root=ROOT, raw={"freeze": {"paths": [*GATE_CONFIG, "**/playwright.config.*"], "spec": SPEC}})
    paths = frozen_paths(config, "qa", ["qa/x.md", "client/playwright.config.ts", "src/a.py"])
    expect_true("freeze-qa-dir", "qa/x.md" in paths)
    expect_true("freeze-playwright", "client/playwright.config.ts" in paths)
    expect("freeze-src", "src/a.py" in paths, False)
    ruff = (ROOT / "ruff.toml").read_text()
    expect_true("ruff-excludes-qa", "qa" in ruff.split("extend-exclude", 1)[1])
    expect_true("ruff-excludes-features", "features" in ruff.split("extend-exclude", 1)[1])


def main() -> None:
    only_qa_tier()
    runner_sets_task()
    install_template()
    readme_docs()
    freeze_unchanged()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        ready_before_cmd(folder)
        cleanup_on_fail(folder)
        never_listens(folder)
        next_free_port(folder)
        env_to_app_not_prompt(folder)
        no_start_unchanged(folder)
        empty_cmd_skips_with_start(folder)
        start_in_cwd(folder)
        log_paths(folder)
        cleanup_on_timeout(folder)
        cleanup_on_interrupt(folder)
    print("ok")


if __name__ == "__main__":
    main()
