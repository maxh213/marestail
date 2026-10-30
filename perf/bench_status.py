#!/usr/bin/env python3
import importlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import harness

root = harness.use_tree()

from marestail import cli as cli_module

TARGETS = (
    "status.read_run events",
    "status.read_run events dead",
    "status.read_run legacy",
    "status.read_run missing",
    "status.scan repo",
    "status.scan fleet",
    "status.scan queued",
    "status.show table",
    "status.show all",
    "status.show json",
    "status.show queued",
    "status.abandon success",
    "status.abandon missing",
    "status._machine_processes",
    "status._discover",
    "status._parse_log",
    "status._table",
    "marestail status table",
    "marestail status json",
    "marestail status all",
    "marestail status queued",
    "marestail abandon success",
    "marestail abandon missing",
)


def status_module() -> Any | None:
    try:
        return importlib.import_module("marestail.status")
    except ImportError:
        return None


def git(folder: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "bench@example.com")
    git(path, "config", "user.name", "bench")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")


def add_events(repo: Path, task: str, rows: list[dict[str, Any]], log: str | None = None) -> Path:
    folder = repo / ".marestail" / "runs" / task
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if log is not None:
        (folder / "pipeline.log").write_text(log)
    return folder


def ev(event: str, at: str, **fields: Any) -> dict[str, Any]:
    return {"v": 1, "at": at, "event": event, **fields}


status = status_module()

if status is None:
    for target in TARGETS:
        harness.absent(target)
else:
    folder = Path(tempfile.mkdtemp())
    try:
        fleet = folder / "fleet"
        alpha = fleet / "alpha"
        beta = fleet / "beta"
        init_repo(alpha)
        init_repo(beta)

        add_events(
            alpha,
            "011-live",
            [
                ev("run-start", "2026-10-16T11:30:00.000Z", pid=4242, branch="main", start_commit="aaa111"),
                ev("step-start", "2026-10-16T11:35:00.000Z", pid=4242, step="01-specifier", role="specifier"),
                ev(
                    "step-end",
                    "2026-10-16T11:40:00.000Z",
                    pid=4242,
                    step="01-specifier",
                    role="specifier",
                    outcome="done",
                    head="bbb222",
                ),
                ev("step-start", "2026-10-16T11:58:00.000Z", pid=4242, step="03-coder", role="coder"),
            ],
            "== qa (04-qa) attempt 1\npipeline complete\n",
        )

        add_events(
            alpha,
            "010-killed",
            [
                ev("run-start", "2026-10-16T11:00:00.000Z", pid=999999, branch="feature", start_commit="abc"),
                ev("step-start", "2026-10-16T11:02:00.000Z", pid=999999, step="01-specifier", role="specifier"),
                ev(
                    "step-end",
                    "2026-10-16T11:05:00.000Z",
                    pid=999999,
                    step="01-specifier",
                    role="specifier",
                    outcome="done",
                    head="fff",
                ),
                ev("step-start", "2026-10-16T11:10:00.000Z", pid=999999, step="02-critic", role="critic"),
            ],
        )

        add_events(
            alpha,
            "009-paused",
            [
                ev("run-start", "2026-10-16T10:00:00.000Z", pid=5555, branch="main", start_commit="s"),
                ev(
                    "run-end",
                    "2026-10-16T10:20:00.000Z",
                    pid=5555,
                    result="stopped",
                    exit_code=1,
                    head="ccc",
                    stopped_at="critic",
                    reason="not approved",
                ),
            ],
        )

        add_events(
            alpha,
            "007-done",
            [
                ev("run-start", "2026-10-16T09:00:00.000Z", pid=6666, branch="main", start_commit="s"),
                ev("step-start", "2026-10-16T09:10:00.000Z", pid=6666, step="01-specifier", role="specifier"),
                ev(
                    "step-end",
                    "2026-10-16T09:20:00.000Z",
                    pid=6666,
                    step="01-specifier",
                    role="specifier",
                    outcome="done",
                    head="x",
                ),
                ev("run-end", "2026-10-16T10:00:00.000Z", pid=6666, result="complete", exit_code=0, head="ddd444"),
            ],
        )

        given_up_folder = add_events(
            alpha,
            "006-given-up",
            [
                ev("run-start", "2026-10-16T08:00:00.000Z", pid=7777, branch="main", start_commit="s"),
                ev(
                    "run-end",
                    "2026-10-16T08:30:00.000Z",
                    pid=7777,
                    result="stopped",
                    stopped_at="coder",
                    reason="failed",
                ),
            ],
        )
        (given_up_folder / "abandoned").write_text("abandoned 2026-10-16 09:00: superseded\n")

        (alpha / ".marestail" / "runs" / "008-fresh").mkdir(parents=True, exist_ok=True)
        (alpha / ".marestail" / "runs" / "098-abandon").mkdir(parents=True, exist_ok=True)

        beta_run = beta / ".marestail" / "runs" / "003-later"
        beta_run.mkdir(parents=True, exist_ok=True)
        (beta_run / "pipeline.log").write_text(
            "== specifier (01-specifier) attempt 1\n"
            "   01-specifier finished in 2.0 min: ok\n"
            "== qa (04-qa) attempt 1\n"
        )

        clones = folder / "clones"
        w1 = clones / "w1"
        w2 = clones / "w2"
        for w in (w1, w2):
            init_repo(w)
            git(w, "remote", "add", "origin", "https://example.com/same.git")
            (w / ".marestail").mkdir(exist_ok=True)

        tasks1 = w1 / "tasks"
        tasks1.mkdir()
        (tasks1 / "012-next.md").write_text("next\n")
        (tasks1 / "013-done.md").write_text("done\n")
        (tasks1 / "README.md").write_text("read\n")
        tasks2 = w2 / "tasks"
        tasks2.mkdir()
        (tasks2 / "012-next.md").write_text("next\n")
        (w2 / ".marestail" / "runs" / "013-done").mkdir(parents=True, exist_ok=True)

        procs = [(4242, ["python3", "-u", "/opt/marestail/marestail/cli.py", "run", "tasks/011-live.md"], alpha)]

        table_header = ["Repo", "Task", "State", "Where it stands", "Last activity", "Branch"]
        sample_rows = [
            ["alpha", "011-live", "running", "in 03-coder", "10-16 11:58 (2m ago)", "main"],
            ["beta", "003-later", "dead", "mid 04-qa", "10-16 11:30 (30m ago)", "main"],
            ["alpha", "010-killed", "dead", "mid 02-critic", "10-16 11:10 (50m ago)", "feature"],
            ["alpha", "009-paused", "stopped", "at critic: not approved", "10-16 10:20 (1h ago)", "main"],
            ["alpha", "008-fresh", "never-started", "no handoffs, no steps, no log", "10-16 10:50 (1h ago)", "main"],
        ]

        harness.emit("status.read_run events", harness.measure(lambda: status.read_run(alpha, "011-live", procs)))
        harness.emit(
            "status.read_run events dead",
            harness.measure(lambda: status.read_run(alpha, "010-killed", procs)),
        )
        harness.emit("status.read_run legacy", harness.measure(lambda: status.read_run(beta, "003-later", procs)))
        harness.emit("status.read_run missing", harness.measure(lambda: status.read_run(alpha, "099-missing", procs)))
        harness.emit("status.scan repo", harness.measure(lambda: status.scan([alpha])))
        harness.emit("status.scan fleet", harness.measure(lambda: status.scan([fleet])))
        harness.emit("status.scan queued", harness.measure(lambda: status.scan([clones])))
        harness.emit("status.show table", harness.measure(lambda: status.show([fleet], False, False, False)))
        harness.emit("status.show all", harness.measure(lambda: status.show([fleet], False, True, False)))
        harness.emit("status.show json", harness.measure(lambda: status.show([fleet], True, False, False)))
        harness.emit("status.show queued", harness.measure(lambda: status.show([clones], False, False, True)))
        harness.emit(
            "status.abandon success",
            harness.measure(lambda: status.abandon([str(alpha / "098-abandon")], "tested")),
        )
        harness.emit(
            "status.abandon missing",
            harness.measure(lambda: status.abandon([str(alpha / "099-missing")], "")),
        )
        harness.emit("status._machine_processes", harness.measure(status._machine_processes))
        harness.emit("status._discover", harness.measure(lambda: status._discover([fleet])))
        harness.emit(
            "status._parse_log",
            harness.measure(lambda: status._parse_log(beta / ".marestail" / "runs" / "003-later" / "pipeline.log")),
        )
        harness.emit("status._table", harness.measure(lambda: status._table(sample_rows, table_header)))

        if not hasattr(cli_module, "status_command"):
            for cli_target in (
                "marestail status table",
                "marestail status json",
                "marestail status all",
                "marestail status queued",
            ):
                harness.absent(cli_target)
        else:
            harness.emit("marestail status table", harness.measure(lambda: cli_module.main(["status", str(fleet)])))
            harness.emit(
                "marestail status json",
                harness.measure(lambda: cli_module.main(["status", "--json", str(fleet)])),
            )
            harness.emit(
                "marestail status all",
                harness.measure(lambda: cli_module.main(["status", "--all", str(fleet)])),
            )
            harness.emit(
                "marestail status queued",
                harness.measure(lambda: cli_module.main(["status", "--queued", str(clones)])),
            )

        if not hasattr(cli_module, "abandon_command"):
            for cli_target in (
                "marestail abandon success",
                "marestail abandon missing",
            ):
                harness.absent(cli_target)
        else:
            harness.emit(
                "marestail abandon success",
                harness.measure(
                    lambda: cli_module.main(["abandon", str(alpha / "098-abandon"), "--reason", "bench"])
                ),
            )
            harness.emit(
                "marestail abandon missing",
                harness.measure(lambda: cli_module.main(["abandon", str(alpha / "099-missing")])),
            )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
