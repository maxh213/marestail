import json
import os
from pathlib import Path
from typing import Any

from marestail.tui import collect
from marestail.tui.model import Process, RepoState, Step, Worker


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def step(label: str = "01-coder", status: str = "running") -> Step:
    return Step(role="coder", label=label, attempt=1, status=status, summary="s", verdict=None, minutes=None)


def repo(root: Path, **fields: Any) -> RepoState:
    fields.setdefault("log_path", None)
    return RepoState(name=root.name, root=root, branch="main", head="abc", task="t", **fields)


def test_discover_roots_and_children(tmp_path: Path) -> None:
    nested = tmp_path / "child"
    write(tmp_path / ".marestail" / "x", "1")
    write(nested / ".marestail" / "x", "1")
    (tmp_path / "empty").mkdir()
    assert collect.discover([tmp_path]) == [tmp_path, nested]


def test_list_dirs_oserror(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(OSError("no")))
    assert collect.list_dirs(tmp_path) == []


def test_repo_shell_and_collect(tmp_path: Path, monkeypatch: Any) -> None:
    write(tmp_path / ".marestail" / "runs" / "overnight-1.log", "== coder (01-coder) attempt 1\nworking\n")
    write(tmp_path / ".marestail" / "handoffs" / "task-a" / "x", "1")
    monkeypatch.setattr(collect, "git_line", lambda root, args: "main" if "branch" in args else "abc msg")
    monkeypatch.setattr(collect, "proc_rows", lambda: [])
    state = collect.collect_repo(tmp_path)
    assert state.branch == "main"
    assert state.task == "task-a"
    assert state.steps[0].label == "01-coder"


def test_attach_live_binds_worker_and_activity(tmp_path: Path, monkeypatch: Any) -> None:
    state = repo(tmp_path, steps=[step()])
    rows = [(1, 0, 3, ["python", "cli.py", "run"]), (2, 1, 4, ["pytest"])]
    monkeypatch.setattr(collect, "cwd_of", lambda pid: tmp_path)
    monkeypatch.setattr(collect, "transcript_tail", lambda root: ["tail"])
    collect.attach_live(state, tmp_path, rows)
    assert state.alive is True
    assert state.worker is not None
    assert state.gate_activity == "pytest 4s"
    assert state.worker.tail_lines == ["tail"]


def test_attach_activity_skips_runner_when_gate_runs(tmp_path: Path, monkeypatch: Any) -> None:
    state = repo(tmp_path)
    monkeypatch.setattr(collect, "gate_activity", lambda rows, pipeline: "pytest")
    monkeypatch.setattr(collect, "transcript_tail", lambda root: ["t"])
    collect.attach_activity(state, [], [1], tmp_path)
    assert state.runner_activity is None
    assert state.gate_activity == "pytest"


def test_stripped_lines_oserror(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "log"
    monkeypatch.setattr(Path, "read_text", lambda self, errors="ignore": (_ for _ in ()).throw(OSError("no")))
    assert collect.stripped_lines(path) == []


def test_transcript_tail_calls_conversation(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(collect, "transcript_conversation", lambda root, max_lines=200, window=1: ["x"])
    assert collect.transcript_tail(tmp_path) == ["x"]


def test_summary_of_invalid_quote() -> None:
    assert collect.summary_of(r"msg '\xzz'") == "\\xzz"


def test_attach_activity_uses_runner_line(tmp_path: Path, monkeypatch: Any) -> None:
    log = write(tmp_path / "run.log", "hello\n")
    state = repo(tmp_path, log_path=log)
    monkeypatch.setattr(collect, "gate_activity", lambda rows, pipeline: None)
    monkeypatch.setattr(collect, "transcript_tail", lambda root: [])
    collect.attach_activity(state, [], [1], tmp_path)
    assert state.runner_activity == "hello"


def test_parse_log_start_finish_verdict_and_running(tmp_path: Path) -> None:
    path = write(
        tmp_path / "log",
        "\n".join(
            [
                "== coder (01-coder) attempt 1",
                "  01-coder finished in 1.5 min: 'done'",
                "  verdict PASS",
                "== critic (02-critic) attempt 1",
                "still going",
            ]
        ),
    )
    steps = collect.parse_log(path)
    assert (steps[0].status, steps[0].verdict, steps[0].minutes) == ("done", "PASS", 1.5)
    assert steps[1].status == "running"
    assert "still going" in steps[1].summary


def test_parse_log_missing_and_unquoted(tmp_path: Path) -> None:
    assert collect.parse_log(tmp_path / "gone") == []
    path = write(tmp_path / "log", "== coder (01-coder) attempt 1\n  01-coder finished in 0.2 min: plain rest\n")
    assert collect.parse_log(path)[0].summary == "plain rest"


def test_finish_step_falls_back_and_ignores_empty() -> None:
    running = step("01-coder", "running")
    steps = [step("other", "done"), running]
    matched = collect.FINISH_RE.match("  missing finished in 1.0 min: x")
    assert matched is not None
    collect.finish_step(steps, matched)
    assert running.status == "done"
    empty: list[Step] = []
    leftover = collect.FINISH_RE.match("  x finished in 1.0 min: z")
    assert leftover is not None
    collect.finish_step(empty, leftover)
    assert empty == []


def test_summary_of_bad_literal() -> None:
    assert collect.summary_of("no quotes here") == "no quotes here"
    assert collect.collapse("a  b") == "a b"


def test_conversation_and_task_name(tmp_path: Path, monkeypatch: Any) -> None:
    worker = Worker(
        step=step(),
        process=None,
        result_path=write(tmp_path / "r.json", '{"result": "ok"}'),
        prompt_path=write(tmp_path / "p.md", "prompt"),
        handoff_path=write(tmp_path / "h.md", "handoff"),
    )
    monkeypatch.setattr(collect, "transcript_conversation", lambda root: ["live"])
    sections = collect.conversation_for(repo(tmp_path, worker=worker))
    assert [name for name, _ in sections] == ["prompt", "handoff", "result", "live"]
    monkeypatch.setattr(collect, "transcript_conversation", lambda root: [])
    assert collect.conversation_for(repo(tmp_path)) == []
    assert collect.task_name(tmp_path) is None
    write(tmp_path / ".marestail" / "runs" / "older" / "x", "1")
    newer = tmp_path / ".marestail" / "handoffs" / "newer"
    newer.mkdir(parents=True)
    os.utime(newer, (9999999999, 9999999999))
    assert collect.task_name(tmp_path) == "newer"


def test_latest_log_and_transcript(tmp_path: Path, monkeypatch: Any) -> None:
    assert collect.latest_log(tmp_path) is None
    write(tmp_path / ".marestail" / "runs" / "overnight-a.log", "a")
    write(tmp_path / ".marestail" / "runs" / "overnight-b.log", "b")
    write(tmp_path / ".marestail" / "runs" / "other.txt", "c")
    found = collect.latest_log(tmp_path)
    assert found is not None
    assert found.name == "overnight-b.log"
    monkeypatch.setattr(collect, "claude_homes", lambda: [tmp_path / "home"])
    folder = tmp_path / "home" / "projects" / str(tmp_path).replace("/", "-")
    write(folder / "a.jsonl", "{}\n")
    monkeypatch.setattr(collect, "dir_mtime", lambda path: 1)
    monkeypatch.setattr("marestail.tui.collect.time.time", lambda: 1)
    assert collect.live_transcript(tmp_path) == folder / "a.jsonl"
    monkeypatch.setattr("marestail.tui.collect.time.time", lambda: 1 + collect.TAIL_STALE_S + 1)
    assert collect.live_transcript(tmp_path) is None
    assert collect.live_transcript(tmp_path / "missing") is None


def test_format_entry_and_blocks() -> None:
    assert collect.format_entry("{") is None
    assert collect.format_entry("{}") is None
    assert collect.format_entry('{"type":"assistant","message":1}') is None
    assert collect.format_entry('{"type":"assistant","message":{"content":"x"}}') is None
    thinking = json.dumps({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}})
    assert collect.format_entry(thinking) == "💭 hmm"
    text = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
    assert collect.format_entry(text) == "hi"
    tool = json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "read", "input": {"file_path": "a.py"}}]}})
    assert collect.format_entry(tool) == "⚒ read a.py"
    assert collect.render_block({"type": "other"}) is None
    assert collect.thinking_line({"thinking": ""}) is None
    assert collect.text_line({"text": ""}) is None
    assert collect.tool_line({"name": ""}) is None
    assert collect.tool_detail("x") == ""
    assert collect.tool_detail({"command": " ls "}) == "ls"


def test_proc_rows_and_parse(monkeypatch: Any) -> None:
    class Out:
        stdout = "header\n1 0 2 python cli.py run\nbad\n1 x 2 a b\n"

    monkeypatch.setattr("marestail.tui.collect.subprocess.run", lambda *args, **opts: Out())
    assert collect.proc_rows()[0][0] == 1
    monkeypatch.setattr("marestail.tui.collect.subprocess.run", lambda *args, **opts: (_ for _ in ()).throw(OSError("no")))
    assert collect.proc_rows() == []
    assert collect.parse_ps_line("1 2") is None


def test_gate_labels() -> None:
    assert collect.gate_label(["muex"], ["muex"]) == "muex"
    assert collect.gate_label(["mix", "test"], ["mix"]) == "mix test"
    assert collect.gate_label(["mix", "compile"], ["mix"]) == "mix"
    assert collect.gate_label(["dotnet", "test"], ["dotnet"]) == "dotnet test"
    assert collect.gate_label(["sonar-scanner"], ["sonar-scanner"]) == "sonar"
    assert collect.gate_label(["/opt/sonarqube"], ["sonarqube"]) == "sonar"
    assert collect.gate_label(["java", "-Dsonar"], ["java"]) == "sonar"
    assert collect.gate_label(["mvn"], ["mvn"]) == "mvn"
    assert collect.gate_label(["mvnw", "pitest"], ["mvnw"]) == "pitest"
    assert collect.gate_label(["java", "PmdCli"], ["java"]) == "pmd"
    assert collect.gate_label(["bundle", "exec", "rspec"], ["bundle"]) == "rspec"
    assert collect.gate_label(["docker", "compose", "run", "--rm", "image", "pytest"], ["docker"]) == "pytest"
    assert collect.gate_label(["pytest"], ["pytest"]) == "pytest"
    assert collect.gate_label(["erlc"], ["erlc"]) == "eunit"
    assert collect.gate_label(["beam", "eunit"], ["beam"]) == "eunit"
    assert collect.gate_label(["unknown"], ["unknown"]) is None
    assert collect.classify_gate(["python", "cli.py", "run"]) is None
    assert collect.classify_gate(["claude"]) is None


def test_docker_inner_short() -> None:
    assert collect.docker_inner(["docker", "compose", "run", "only"]) is None
    assert collect.bundle_inner(["bundle", "exec"]) is None
    assert collect.after(["mix"], "mix") is None


def test_agent_process_and_fmt() -> None:
    assert collect.agent_process(1, 1, ["python", "cli.py", "run"]) is None
    assert collect.agent_process(1, 1, ["other"]) is None
    process = collect.agent_process(1, 9, ["claude", "--model", "opus"])
    assert process == Process(pid=1, elapsed_s=9, model="opus", backend="claude")
    assert collect.fmt_seconds(10) == "10s"
    assert collect.fmt_seconds(120) == "2m"
    assert collect.fmt_seconds(3661) == "1h01m"


def test_git_line_and_paths(tmp_path: Path, monkeypatch: Any) -> None:
    assert collect.git_line(tmp_path, ["status"]) == ""
    monkeypatch.setattr("marestail.tui.collect.subprocess.run", lambda *args, **opts: (_ for _ in ()).throw(OSError("no")))
    assert collect.git_line(tmp_path, ["status"]) == ""

    class Ok:
        returncode = 0
        stdout = "ok\n"

    monkeypatch.setattr("marestail.tui.collect.subprocess.run", lambda *args, **opts: Ok())
    assert collect.git_line(tmp_path, ["status"]) == "ok"
    assert collect.cwd_of(0) is None
    assert collect.under_root(None, tmp_path) is False
    assert collect.under_root(tmp_path / "a", tmp_path) is True
    broken = tmp_path / "missing"
    monkeypatch.setattr(Path, "resolve", lambda self: (_ for _ in ()).throw(OSError("no")))
    assert collect.real_path(broken) == broken


def test_result_text(tmp_path: Path) -> None:
    assert collect.result_text(None) is None
    assert collect.read_text(tmp_path / "gone") is None
    path = write(tmp_path / "a.json", "{")
    assert collect.result_text(path) == "{"
    write(tmp_path / "b.json", '{"result": 1}')
    assert collect.result_text(tmp_path / "b.json") == '{"result": 1}'
    write(tmp_path / "c.json", '{"result": "yes"}')
    assert collect.result_text(tmp_path / "c.json") == "yes"


def test_transcript_lines(tmp_path: Path) -> None:
    path = write(tmp_path / "t.jsonl", "one\ntwo\n")
    assert collect.transcript_lines(path, 1000)[-1] == "two"
    big = write(tmp_path / "big.jsonl", "head\n" + "x" * 50)
    lines = collect.transcript_lines(big, 10)
    assert lines != ["head", "x" * 50]
    assert collect.transcript_lines(tmp_path / "gone.jsonl") == []


def test_claude_homes(monkeypatch: Any) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DANDELION_CLAUDE_WORK_CONFIG_DIR", raising=False)
    homes = collect.claude_homes()
    assert homes[0] == Path.home() / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/claude")
    assert Path("/tmp/claude") in collect.claude_homes()


def test_gate_activity_empty_and_descendants(tmp_path: Path, monkeypatch: Any) -> None:
    assert collect.gate_activity([], []) is None
    rows = [(1, 0, 1, ["python", "cli.py", "run"]), (2, 1, 5, ["pytest"]), (3, 2, 2, ["echo"])]
    assert collect.gate_activity(rows, [1]) == "pytest 5s"


def test_dir_mtime_oserror(tmp_path: Path) -> None:
    assert collect.dir_mtime(tmp_path / "gone") == 0.0


def test_existing_and_is_pipeline(tmp_path: Path) -> None:
    path = write(tmp_path / "f", "1")
    assert collect.existing(path) == path
    assert collect.existing(tmp_path / "gone") is None
    assert collect.is_pipeline(["python", "cli.py", "run"]) is True
    assert collect.is_pipeline(["cli.py"]) is False


def test_formatted_tail_and_fresh_log(tmp_path: Path, monkeypatch: Any) -> None:
    path = write(tmp_path / "a.jsonl", json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n")
    assert collect.formatted_tail(path, 1, 1000) == ["hi"]
    assert collect.fresh_log([]) is None
    monkeypatch.setattr(collect, "dir_mtime", lambda path: 1)
    monkeypatch.setattr("marestail.tui.collect.time.time", lambda: 1)
    assert collect.fresh_log([path]) == path


def test_build_worker_without_task(tmp_path: Path, monkeypatch: Any) -> None:
    state = repo(tmp_path)
    state.task = None
    monkeypatch.setattr(collect, "cwd_of", lambda pid: tmp_path)
    worker = collect.build_worker(state, step(), [(1, 0, 1, ["claude"])], tmp_path)
    assert worker.result_path is None
    state.task = "t"
    write(tmp_path / ".marestail" / "runs" / "t" / "01-coder.json", "{}")
    worker = collect.build_worker(state, step(), [], tmp_path)
    assert worker.result_path is not None


def test_collect_fleet(tmp_path: Path, monkeypatch: Any) -> None:
    write(tmp_path / ".marestail" / "x", "1")
    monkeypatch.setattr(collect, "git_line", lambda root, args: "")
    monkeypatch.setattr(collect, "proc_rows", lambda: [])
    fleet = collect.collect_fleet([tmp_path])
    assert fleet.repos[0].root == tmp_path


def test_summary_literal() -> None:
    assert collect.summary_of("msg 'quoted'") == "quoted"
    assert collect.summary_of('msg "quoted"') == "quoted"


def test_accept_verdict_without_steps() -> None:
    steps: list[Step] = []
    collect.accept_verdict(steps, "  verdict PASS")
    assert steps == []


def test_last_text() -> None:
    assert collect.last_text(["", "  hi  ", ""]) == "  hi  "
    assert collect.last_text(["", ""]) == ""
