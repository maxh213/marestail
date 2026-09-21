import json
import os
import subprocess
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


def test_surely_keeps_missing_values() -> None:
    assert collect.surely("x") == "x"
    assert collect.surely(None) is None
    assert collect.present(0) is True
    assert collect.present(None) is False
    assert collect.is_str("a") is True
    assert collect.is_str(None) is False
    assert collect.is_path(Path("/tmp")) is True
    assert collect.is_path(None) is False


def test_first_text_skips_missing_and_keeps_a_string() -> None:
    assert collect.first_text(None, "45s") == "45s"
    assert collect.first_text("12s", "0m") == "12s"
    assert collect.first_text("", "later") == ""
    assert collect.first_text(None, None) == ""


def test_work_path(tmp_path: Path) -> None:
    assert collect.work_path(tmp_path) == tmp_path / collect.WORK
    assert collect.work_path(tmp_path, "runs", "x") == tmp_path / collect.WORK / "runs" / "x"


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


def test_git_line_passes_the_root(tmp_path: Path, monkeypatch: Any) -> None:
    seen: list[tuple[Path, list[str]]] = []

    def run_git(root: Path, args: list[str]) -> object:
        seen.append((root, args))
        return type("Out", (), {"returncode": 0, "stdout": "ok\n"})()

    monkeypatch.setattr(collect, "run_git", run_git)
    assert collect.git_line(tmp_path, ["status"]) == "ok"
    assert seen == [(tmp_path, ["status"])]


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


def test_collect_helpers(tmp_path: Path, monkeypatch: Any) -> None:
    assert collect.skip() is None
    assert collect.none_of() is None
    assert collect.present(0) is True
    assert collect.empty_list() == []
    assert collect.blank() == ""
    assert collect.false_of() is False
    assert collect.ident("x") == "x"
    assert collect.or_blank(None) == ""
    assert collect.or_blank("hi") == "hi"
    assert collect.entries(tmp_path / "gone") == []
    (tmp_path / "child").mkdir()
    assert tmp_path / "child" in collect.entries(tmp_path)
    assert collect.has_marestail(tmp_path) is False
    write(tmp_path / ".marestail" / "x", "1")
    assert collect.self_repo(tmp_path) == [tmp_path]
    assert collect.row_pid((9, 1, 2, ["a"])) == 9
    assert collect.last_item(["a", "b"]) == "b"
    assert collect.last_or_none([]) is None
    assert collect.newest_name([tmp_path]) == tmp_path.name
    assert collect.log_name(tmp_path / "overnight-a.log") == "overnight-a.log"
    assert collect.secs_fmt(10) == "10s"
    assert collect.secs_fmt(60) is None
    assert collect.mins_fmt(120) == "2m"
    assert collect.mins_fmt(4000) is None
    assert collect.hours_fmt(3661) == "1h01m"
    assert collect.contained(["pytest"], "pytest") is True
    assert collect.not_flag("-rm") is False
    assert collect.not_flag("pytest") is True
    assert collect.in_backends("claude") is True
    assert collect.has_sonar("-Dsonar") is True
    assert collect.has_sonarqube("/opt/sonarqube") is True
    assert collect.has_pitest("pitest:mutants") is True
    assert collect.has_pmd("PmdCli") is True
    assert collect.has_eunit("eunit") is True
    assert collect.erlc_eunit(["erlc"]) == "eunit"
    assert collect.token_eunit(["x"]) is None
    assert collect.kids({1: [2]}, 1) == [2]
    assert collect.kids({}, 1) == []
    assert collect.first_of((4, "pytest")) == 4
    assert collect.elapsed_of(Process(1, 9, "m", "claude")) == 9
    assert collect.min_elapsed([]) is None
    assert collect.existing(tmp_path / "gone") is None
    assert collect.named_one(["muex"], "muex") == "muex"
    assert collect.mix_kind("test") == "mix test"
    assert collect.mix_kind("compile") == "mix"
    assert collect.mix_from(None) is None
    assert collect.scanner_sonar(["sonar-scanner"]) == "sonar"
    assert collect.inner_name(["svc"]) is None
    assert collect.second_basename(["svc", "/bin/pytest"]) == "pytest"
    assert collect.nonempty("") is None
    assert collect.nonempty("x") == "x"
    assert collect.prefix_text("💭 ", "") is None
    assert collect.prefix_text("💭 ", "hi") == "💭 hi"
    assert collect.is_dict({}) is True
    assert collect.list_or_empty("x") == []
    assert collect.list_or_empty([1]) == [1]
    assert collect.result_field(None) is None
    assert collect.str_or_raw(1, "raw") == "raw"
    assert collect.decoded_if(None) is None
    assert collect.ok_git(None) is False
    assert collect.git_stdout(None) == ""
    assert collect.path_under(tmp_path, tmp_path) is True
    assert collect.token_after(["a", "b"], 0) == "b"
    assert collect.basename_is(["mix", "test"], "mix", 0) is True
    assert collect.is_model_flag(["--model", "opus"], 0) is True
    assert collect.pid_entry((1, 0, 2, ["pytest"])) == (1, (2, ["pytest"]))
    assert collect.row_agent(1, 0, 2, ["other"]) is None
    assert collect.cli_run_at(["python", "cli.py", "run"], 1) is True
    assert collect.docker_at(["docker", "compose", "run", "svc", "pytest"], 0) is True
    assert collect.is_bundle_exec(["bundle", "exec", "rspec"], 0) is True
    assert collect.bundle_at(["bundle", "exec", "rspec"], 0) == "rspec"
    assert collect.has_field({"file_path": "a.py"}, "file_path") is True
    assert collect.field_text({"file_path": "a.py"}, "file_path") == "a.py"
    assert collect.dict_fields({"command": "ls"}) == ["ls"]
    assert collect.empty_at(None, 1, 1) == []
    assert collect.empty_lines(0, None, 1) == []
    assert collect.work_home().name in {".claude-work", "work"}
    monkeypatch.delenv("DANDELION_CLAUDE_WORK_CONFIG_DIR", raising=False)
    assert collect.work_home().name == ".claude-work"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert collect.expanded_env("CLAUDE_CONFIG_DIR") is None
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/c")
    assert collect.expanded_env("CLAUDE_CONFIG_DIR") == Path("/tmp/c")
    assert collect.format_gate([]) is None
    assert collect.format_gate([(3, "pytest")]) == "pytest 3s"
    assert collect.classified(["python", "cli.py", "run"], ["python", "cli.py", "run"]) is None
    assert collect.sonar_if_named(["-Dsonar"]) == "sonar"
    assert collect.pmd_if_java(["PmdCli"]) == "pmd"
    assert collect.gate_hit((1, ["echo"])) == []
    assert collect.false_of(1) is False
    state = repo(tmp_path)
    collect.set_runner(state)
    collect.maybe_runner(state)
    state.worker = collect.worker_without_task(state, step(), None)
    collect.copy_tails(state)
    assert state.worker.tail_lines == state.tail_lines
    collect.add_child({}, (1, 0, 1, ["x"]))
    collect.add_live([], ["a"])
    collect.mark_running([step()], ["hello"])
    finished = collect.FINISH_RE.match("  x finished in 1.0 min: z")
    started = collect.STEP_RE.match("== coder (01-coder) attempt 1")
    verdict = collect.VERDICT_RE.match("  verdict PASS")
    quoted = collect.QUOTED_RE.search(" 'ok'")
    assert finished is not None
    assert started is not None
    assert verdict is not None
    assert quoted is not None
    collect.complete_if_found(None, finished)
    collect.append_step([], started)
    collect.set_verdict([step()], verdict)
    assert collect.last_if_running([step()]) is not None
    assert collect.last_if_running([step("x", "done")]) is None
    assert collect.collapse_rest("a  b", None) == "a b"
    assert collect.eval_quote("", quoted) == "ok"
    assert collect.worker_without_task(state, step(), None).result_path is None
    assert collect.min_process([Process(1, 9, "m", "claude")]).elapsed_s == 9
    assert collect.make_process(1, 2, ["claude"], "claude").backend == "claude"
    assert collect.process_of(1, 2, ["claude"], None) is None
    assert collect.agent_under(tmp_path, Process(0, 1, "", "claude")) is False
    assert collect.read_ignore(tmp_path / "gone") == []
    assert collect.repo_steps(None) == []
    assert collect.nonempty_lines(None) == []
    assert collect.project_jsonl(tmp_path, tmp_path / "home") == []
    assert collect.split_tail(10, b"a\nb\n", 3) == ["b"]
    assert collect.decode_tail((0, None), 1) == []
    assert collect.assistant_dict("x") is False
    assert collect.dict_content({"content": ["a"]}) == ["a"]
    assert collect.first_from({"message": None}) is None
    assert collect.tool_named("", {"name": ""}) is None
    assert collect.tool_text("read", {"input": {}}) == "⚒ read"
    assert collect.stripped_str(" x ") is True
    assert collect.parsed_parts(["1"]) is None
    assert collect.ints_row(["a", "b", "c", "d"]) is None
    assert collect.load_text(tmp_path / "gone") is None
    assert collect.dict_result({"result": "ok"}) == "ok"
    assert collect.second_basename(["a", "b/c"]) == "c"
    assert collect.inner_name(["a", "b"]) == "b"
    assert collect.compose_run_target(["docker", "compose", "run", "svc"], 0) is None
    assert collect.start_stack({1: [2]}, [1]) == [2]
    assert collect.unwind({2: []}, [2]) == [2]
    assert collect.gate_hit_pid({}, 1) == []
    assert collect.push_level({1: []}, [], []) is None
    assert isinstance(collect.run_ps(), str)
    assert collect.blank("x") == ""
    assert collect.running_step([]) is None
    running = collect.running_step([step()])
    assert running is not None
    assert running.label == "01-coder"
    assert collect.surely("x") == "x"
    assert collect.missing_block({}) is None
    assert collect.latest_runner_line(None) is None
    assert collect.has_text(("prompt", "x")) is True
    assert collect.has_text(("prompt", None)) is False
    assert collect.named_texts((("prompt", "x"), ("handoff", None))) == [("prompt", "x")]
    assert collect.worker_sections(None) == []
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None)
    assert collect.worker_texts(worker) == []
    assert collect.list_task_dirs(tmp_path / "gone") == []
    assert collect.overnight_logs(tmp_path) == []
    assert collect.is_overnight_log(tmp_path) is False
    assert collect.read_lines(tmp_path / "gone") == []
    assert collect.accept_start([], "not a start") is False
    assert collect.accept_finish([], "not a finish") is False
    assert collect.last_running([]) is False
    assert collect.is_jsonl(tmp_path / "a.jsonl") is False
    assert collect.jsonl_logs(tmp_path) == []
    assert collect.expand_var("~/x").name == "x"
    assert collect.formatted_if(None, 1, 1) == []
    assert collect.parsed_json("{") is None
    assert collect.assistant_block("x") is None
    assert collect.message_content(None) == []
    assert collect.first_block([]) is None
    assert collect.rendered_blocks([]) == []
    assert collect.tool_fields("x") == []
    assert isinstance(collect.ps_lines(), list)
    assert collect.child_map([]) == {}
    assert collect.descendant_gates({}, {}, []) == []
    assert collect.skipped_gate(["python", "cli.py", "run"], ["python"]) is True
    assert collect.mix_gate(["mix", "test"]) == "mix test"
    assert collect.named_command(["dotnet", "test"], "dotnet", "test", "dotnet test") == "dotnet test"
    assert collect.sonarqube_token(["/opt/sonarqube"]) == "sonar"
    assert collect.java_sonar(["java"], ["-Dsonar"]) == "sonar"
    assert collect.java_sonar(["python"], ["-Dsonar"]) is None
    assert collect.maven_cmd(["mvn"]) is True
    assert collect.maven_gate(["mvn"], ["mvn"]) == "mvn"
    assert collect.maven_kind(["pitest"]) == "pitest"
    assert collect.pmd_gate(["java"], ["PmdCli"]) == "pmd"
    assert collect.pmd_gate(["python"], ["PmdCli"]) is None
    assert collect.named_tool(["pytest"]) == "pytest"
    assert collect.eunit_gate(["erlc"], []) == "eunit"
    assert collect.docker_compose_run("docker", ["docker", "compose", "run"], 0) is True
    assert collect.backend_of(["claude"]) == "claude"
    assert collect.model_of(["--model", "opus"]) == "opus"
    assert collect.run_git(tmp_path, ["status"]) is not None or collect.run_git(tmp_path, ["status"]) is None
    assert collect.decoded_result("{") == "{"
    started = collect.STEP_RE.match("== coder (01-coder) attempt 1")
    finished = collect.FINISH_RE.match("  x finished in 1.0 min: z")
    assert started is not None
    assert finished is not None
    assert collect.new_step(started).label == "01-coder"
    assert collect.quoted_or_plain("plain", None) == "plain"
    assert collect.pipeline_pids([], tmp_path) == []
    collect.bind_running(state, [], tmp_path)
    collect.bind_tails_or_runner(state)
    collect.set_worker(state, step(), [], tmp_path)
    collect.stamp_running([step()], ["hello"])
    collect.try_finish([], "  x finished in 1.0 min: z")
    collect.apply_log_line([], "== coder (01-coder) attempt 1")
    assert collect.stored_start([], None) is False
    assert collect.stored_finish([], None) is False
    collect.apply_verdict([], None)
    assert collect.chosen_step([], finished) is None
    collect.complete_step(step(), finished)
    assert collect.running_label("01-coder", step()) is True
    assert collect.running_named([], "01-coder") is None
    assert collect.agents_of([]) == []
    assert collect.matching_agent([], tmp_path) is None
    assert collect.worker_with_task(state, step(), None).result_path is None
    log = write(tmp_path / "overnight-z.log", "x")
    assert collect.newest_log([log]) == log
    assert collect.newest_fresh([log]) is not None or collect.newest_fresh([log]) is None
    assert collect.load_tail(log, 10)[0] >= 0
    assert collect.read_tail(tmp_path / "gone", 10) == (0, None)
    monkeypatch.setattr(collect, "cwd_of", lambda pid: tmp_path)
    assert collect.is_pipeline_row(tmp_path, (1, 0, 1, ["python", "cli.py", "run"])) is True
    assert collect.gate_candidates(["pytest"], ["pytest"])[-2] == "pytest"
    assert collect.stripped_out(type("Out", (), {"stdout": " ok \n"})()) == "ok"


def test_run_git_invokes_git(tmp_path: Path, monkeypatch: Any) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        seen.append((list(command), dict(options)))
        return subprocess.CompletedProcess(command, 0, "main\n", "")

    monkeypatch.setattr("marestail.tui.collect.subprocess.run", run)
    out = collect.run_git(tmp_path, ["branch", "--show-current"])
    assert out is not None
    assert out.stdout == "main\n"
    assert seen == [(["git", "-C", str(tmp_path), "branch", "--show-current"], {"capture_output": True, "text": True, "timeout": 10})]


def test_run_git_swallows_oserror(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("marestail.tui.collect.subprocess.run", lambda *args, **options: (_ for _ in ()).throw(OSError("no")))
    assert collect.run_git(tmp_path, ["status"]) is None


def test_run_ps_invokes_ps(monkeypatch: Any) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        seen.append((list(command), dict(options)))
        return subprocess.CompletedProcess(command, 0, "PID\n1 0 1 bash\n", "")

    monkeypatch.setattr("marestail.tui.collect.subprocess.run", run)
    assert collect.run_ps() == "PID\n1 0 1 bash\n"
    assert seen == [(["ps", "-eo", "pid,ppid,etimes,args"], {"capture_output": True, "text": True, "timeout": 10})]


def test_run_ps_swallows_oserror(monkeypatch: Any) -> None:
    monkeypatch.setattr("marestail.tui.collect.subprocess.run", lambda *args, **options: (_ for _ in ()).throw(OSError("no")))
    assert collect.run_ps() == ""


def test_repo_shell_uses_git_line_args(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(collect, "latest_log", lambda root: tmp_path / "log")
    monkeypatch.setattr(collect, "git_line", lambda root, args: " ".join(args))
    monkeypatch.setattr(collect, "task_name", lambda root: "task")
    monkeypatch.setattr(collect, "repo_steps", lambda path: [])
    state = collect.repo_shell(tmp_path)
    assert (state.name, state.root, state.task, state.log_path) == (tmp_path.name, tmp_path, "task", tmp_path / "log")
    assert state.branch == "branch --show-current"
    assert state.head == "log -1 --format=%h%x20%s"


class Tracker:
    def __init__(self, fn: Any = lambda *args, **kwargs: None) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.fn = fn

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.fn(*args, **kwargs)


def tracker(fn: Any = lambda *args, **kwargs: None) -> Tracker:
    return Tracker(fn)


def test_collect_constants() -> None:
    assert collect.RUNS == "runs"
    assert collect.HANDOFFS == "handoffs"
    assert collect.LIVE == "live"
    assert collect.STATUS_RUNNING == "running"
    assert collect.STATUS_DONE == "done"
    assert collect.ERRORS_IGNORE == "ignore"
    assert collect.ERRORS_REPLACE == "replace"
    assert collect.WORK_CONFIG_ENV == "DANDELION_CLAUDE_WORK_CONFIG_DIR"
    assert collect.WORK_HOME_DEFAULT == "~/.claude-work"
    assert collect.CLAUDE_CONFIG_ENV == "CLAUDE_CONFIG_DIR"
    assert collect.CLAUDE_HOME == ".claude"
    assert collect.PROMPT_SUFFIX == ".prompt.md"
    assert collect.RESULT_SUFFIX == ".json"
    assert collect.HANDOFF_SUFFIX == ".md"
    assert collect.CONV_MAX_LINES == 200
    assert collect.CONV_BYTES == 262144


def test_transcript_conversation_uses_default_window(tmp_path: Path, monkeypatch: Any) -> None:
    seen: list[tuple[Path | None, int, int]] = []

    def formatted_if(path: Path | None, max_lines: int, window: int) -> list[str]:
        seen.append((path, max_lines, window))
        return []

    monkeypatch.setattr(collect, "live_transcript", lambda root: None)
    monkeypatch.setattr(collect, "formatted_if", formatted_if)
    assert collect.transcript_conversation(tmp_path) == []
    assert seen == [(None, collect.CONV_MAX_LINES, collect.CONV_BYTES)]


def test_self_repo_empty_and_list_dirs_files(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "child").mkdir()
    assert collect.self_repo(tmp_path) == []
    assert collect.list_dirs(tmp_path) == [tmp_path / "child"]
    (tmp_path / collect.HANDOFFS).mkdir()
    (tmp_path / collect.HANDOFFS / "note.txt").write_text("x")
    (tmp_path / collect.HANDOFFS / "task").mkdir()
    assert collect.list_task_dirs(tmp_path / collect.HANDOFFS) == [tmp_path / collect.HANDOFFS / "task"]


def test_collect_repo_passes_real_path(tmp_path: Path, monkeypatch: Any) -> None:
    live = tracker(lambda state, real, rows: None)
    monkeypatch.setattr(collect, "attach_live", live)
    monkeypatch.setattr(collect, "repo_shell", lambda root: repo(root))
    monkeypatch.setattr(collect, "proc_rows", lambda: [])
    monkeypatch.setattr(collect, "real_path", lambda root: root / "real")
    collect.collect_repo(tmp_path)
    assert live.calls[0][0][1] == tmp_path / "real"


def test_repo_shell_passes_root(tmp_path: Path, monkeypatch: Any) -> None:
    seen: list[tuple[Path, list[str]]] = []

    def git_line(root: Path, args: list[str]) -> str:
        seen.append((root, args))
        return "x"

    monkeypatch.setattr(collect, "git_line", git_line)
    monkeypatch.setattr(collect, "latest_log", lambda root: None)
    monkeypatch.setattr(collect, "task_name", lambda root: None)
    collect.repo_shell(tmp_path)
    assert seen[0][0] is tmp_path
    assert seen[1][0] is tmp_path


def test_attach_live_passes_real(tmp_path: Path, monkeypatch: Any) -> None:
    state = repo(tmp_path, steps=[step()])
    bind = tracker()
    activity = tracker()
    monkeypatch.setattr(collect, "bind_running", bind)
    monkeypatch.setattr(collect, "attach_activity", activity)
    monkeypatch.setattr(collect, "pipeline_pids", lambda rows, real: [1])
    collect.attach_live(state, tmp_path, [])
    assert bind.calls[0][0][2] is tmp_path
    assert activity.calls[0][0][3] is tmp_path


def test_pipeline_row_uses_pid(tmp_path: Path, monkeypatch: Any) -> None:
    cwd = tracker(lambda pid: tmp_path)
    monkeypatch.setattr(collect, "cwd_of", cwd)
    row = (7, 0, 1, ["python", "cli.py", "run"])
    assert collect.is_pipeline_row(tmp_path, row) is True
    assert cwd.calls[0][0] == (7,)
    rows = [row, (8, 0, 1, ["echo"])]
    monkeypatch.setattr(collect, "cwd_of", lambda pid: tmp_path)
    assert collect.pipeline_pids(rows, tmp_path) == [7]


def test_set_worker_and_bind_running_pass_real(tmp_path: Path, monkeypatch: Any) -> None:
    state = repo(tmp_path, steps=[step()])
    built = tracker(lambda state, running, rows, real: collect.worker_without_task(state, running, None))
    monkeypatch.setattr(collect, "build_worker", built)
    collect.set_worker(state, step(), [], tmp_path)
    assert built.calls[0][0][3] is tmp_path
    bind_built = tracker(lambda state, running, rows, real: collect.worker_without_task(state, running, None))
    monkeypatch.setattr(collect, "build_worker", bind_built)
    collect.bind_running(state, [], tmp_path)
    assert bind_built.calls[0][0][3] is tmp_path


def test_attach_activity_passes_root(tmp_path: Path, monkeypatch: Any) -> None:
    state = repo(tmp_path)
    tails = tracker(lambda root: ["t"])
    monkeypatch.setattr(collect, "transcript_tail", tails)
    monkeypatch.setattr(collect, "gate_activity", lambda rows, pipeline: None)
    collect.attach_activity(state, [], [1], tmp_path)
    assert tails.calls[0][0] == (tmp_path,)
    assert state.tail_lines == ["t"]


def test_read_ignore_and_read_lines_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "log"
    path.write_bytes(b"ok\xff\n")
    assert collect.read_ignore(path) == ["ok"]
    assert collect.read_lines(path) == ["ok\ufffd"]
    loaded = collect.load_text(path)
    assert loaded == "ok\ufffd\n"
    raw = b"a\xffb"
    assert collect.split_tail(3, raw, 10) == [raw.decode(errors=collect.ERRORS_REPLACE)]


def test_collect_fleet_scanned_at(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(collect, "discover", lambda roots: [])
    monkeypatch.setattr("marestail.tui.collect.time.time", lambda: 12.5)
    fleet = collect.collect_fleet([tmp_path])
    assert fleet.scanned_at == 12.5


def test_add_live_and_conversation_root(tmp_path: Path, monkeypatch: Any) -> None:
    sections: list[tuple[str, str]] = []
    collect.add_live(sections, ["a", "b"])
    assert sections == [(collect.LIVE, "a\nb")]
    trans = tracker(lambda root: ["live"])
    monkeypatch.setattr(collect, "transcript_conversation", trans)
    monkeypatch.setattr(collect, "real_path", lambda root: root / "r")
    collect.conversation_for(repo(tmp_path))
    assert trans.calls[0][0] == (tmp_path / "r",)


def test_task_name_uses_runs_dir(tmp_path: Path) -> None:
    write(tmp_path / ".marestail" / collect.RUNS / "from-runs" / "x", "1")
    assert collect.task_name(tmp_path) == "from-runs"


def test_newest_log_uses_name_key(tmp_path: Path) -> None:
    older = tmp_path / "overnight-a.log"
    newer = tmp_path / "overnight-b.log"
    older.write_text("a")
    newer.write_text("b")
    assert collect.newest_log([older, newer]) == newer
    assert collect.newest_log([newer, older]) == newer
    first = tmp_path / "z" / "a.log"
    second = tmp_path / "a" / "z.log"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("a")
    second.write_text("z")
    assert collect.newest_log([first, second]) == second


def test_new_step_fields() -> None:
    matched = collect.STEP_RE.match("== coder (01-coder) attempt 3")
    assert matched is not None
    created = collect.new_step(matched)
    assert created.role == "coder"
    assert created.label == "01-coder"
    assert created.attempt == 3
    assert created.status == collect.STATUS_RUNNING
    assert created.summary == ""
    assert created.verdict is None
    assert created.minutes is None


def test_last_text_skips_whitespace() -> None:
    assert collect.last_text(["keep", "   "]) == "keep"
    assert collect.last_text(["keep", "\t"]) == "keep"


def test_chosen_step_uses_finish_label() -> None:
    first = step("01-coder", "running")
    second = step("02-critic", "running")
    steps = [first, second]
    matched = collect.FINISH_RE.match("  01-coder finished in 1.0 min: x")
    assert matched is not None
    assert collect.chosen_step(steps, matched) is first
    named = collect.running_named(steps, "01-coder")
    assert named is first


def test_eval_quote_value_error() -> None:
    matched = collect.QUOTED_RE.search(r" '\xzz'")
    assert matched is not None
    assert collect.eval_quote("", matched) == "\\xzz"
    quoted = collect.QUOTED_RE.search(" 'ok'")
    assert quoted is not None
    assert collect.eval_quote("", quoted) == "ok"


def test_transcript_tail_args(tmp_path: Path, monkeypatch: Any) -> None:
    conv = tracker(lambda root, max_lines=0, window=0: ["x"])
    monkeypatch.setattr(collect, "transcript_conversation", conv)
    assert collect.transcript_tail(tmp_path) == ["x"]
    assert conv.calls[0][0] == (tmp_path, collect.TAIL_LIMIT, collect.TAIL_BYTES)


def test_formatted_if_passes_args(tmp_path: Path, monkeypatch: Any) -> None:
    tail = tracker(lambda path, max_lines, window: ["a"])
    monkeypatch.setattr(collect, "formatted_tail", tail)
    path = tmp_path / "t.jsonl"
    assert collect.formatted_if(path, 4, 9) == ["a"]
    assert tail.calls[0][0] == (path, 4, 9)
    live = tracker(lambda root: path)
    formatted = tracker(lambda found, max_lines, window: ["z"])
    monkeypatch.setattr(collect, "live_transcript", live)
    monkeypatch.setattr(collect, "formatted_if", formatted)
    assert collect.transcript_conversation(tmp_path, 3, 8) == ["z"]
    assert live.calls[0][0] == (tmp_path,)
    assert formatted.calls[0][0] == (path, 3, 8)


def test_formatted_tail_passes_window(tmp_path: Path, monkeypatch: Any) -> None:
    lines = tracker(lambda path, window: ['{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}'])
    monkeypatch.setattr(collect, "transcript_lines", lines)
    path = tmp_path / "a.jsonl"
    assert collect.formatted_tail(path, 2, 77) == ["hi"]
    assert lines.calls[0][0] == (path, 77)


def test_expanded_env_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    expanded = tracker(lambda value: Path(value))
    monkeypatch.setattr(collect, "expand_var", expanded)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    collect.expanded_env("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/c")
    assert collect.expanded_env("CLAUDE_CONFIG_DIR") == Path("/tmp/c")


def test_work_home_env(monkeypatch: Any) -> None:
    monkeypatch.setenv(collect.WORK_CONFIG_ENV, "/tmp/work-config")
    assert collect.work_home() == Path("/tmp/work-config")
    monkeypatch.delenv(collect.WORK_CONFIG_ENV, raising=False)
    assert collect.work_home() == Path(collect.WORK_HOME_DEFAULT).expanduser()


def test_newest_fresh_mtime_key_and_stale(tmp_path: Path, monkeypatch: Any) -> None:
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text("1")
    b.write_text("2")
    times = {a: 5.0, b: 1.0}
    monkeypatch.setattr(collect, "dir_mtime", lambda path: times[path])
    monkeypatch.setattr("marestail.tui.collect.time.time", lambda: 6.0)
    assert collect.newest_fresh([a, b]) is a
    assert collect.newest_fresh([b, a]) is a
    monkeypatch.setattr("marestail.tui.collect.time.time", lambda: 5.0 + collect.TAIL_STALE_S)
    assert collect.newest_fresh([a]) is a
    monkeypatch.setattr("marestail.tui.collect.time.time", lambda: 5.0 + collect.TAIL_STALE_S + 0.1)
    assert collect.newest_fresh([a]) is None


def test_split_tail_boundary() -> None:
    assert collect.split_tail(10, b"a\nb\n", 10) == ["a", "b"]
    assert collect.split_tail(11, b"a\nb\n", 10) == ["b"]


def test_rendered_blocks_skips_non_dict() -> None:
    assert collect.rendered_blocks(["x", {"type": "text", "text": "hi"}]) == ["hi"]


def test_worker_without_and_with_task_paths(tmp_path: Path) -> None:
    process = Process(3, 4, "m", "claude")
    running = step()
    state = repo(tmp_path)
    state.task = None
    worker = collect.worker_without_task(state, running, process)
    assert worker.step is running
    assert worker.process is process
    state.task = "t"
    write(tmp_path / ".marestail" / collect.RUNS / "t" / f"01-coder{collect.RESULT_SUFFIX}", "{}")
    write(tmp_path / ".marestail" / collect.RUNS / "t" / f"01-coder{collect.PROMPT_SUFFIX}", "p")
    write(tmp_path / ".marestail" / collect.HANDOFFS / "t" / f"01-coder{collect.HANDOFF_SUFFIX}", "h")
    with_task = collect.worker_with_task(state, running, process)
    assert with_task.step is running
    assert with_task.process is process
    assert with_task.prompt_path is not None
    assert with_task.handoff_path is not None
    assert with_task.result_path is not None


def test_build_worker_matching_agent(tmp_path: Path, monkeypatch: Any) -> None:
    process = Process(9, 1, "m", "claude")
    matched = tracker(lambda rows, real: process)
    monkeypatch.setattr(collect, "matching_agent", matched)
    state = repo(tmp_path)
    state.task = None
    worker = collect.build_worker(state, step(), [(1, 0, 1, ["claude"])], tmp_path)
    assert matched.calls[0][0] == ([(1, 0, 1, ["claude"])], tmp_path)
    assert worker.process is process


def test_agent_under_uses_pid(tmp_path: Path, monkeypatch: Any) -> None:
    cwd = tracker(lambda pid: tmp_path)
    monkeypatch.setattr(collect, "cwd_of", cwd)
    process = Process(11, 1, "m", "claude")
    assert collect.agent_under(tmp_path, process) is True
    assert cwd.calls[0][0] == (11,)


def test_min_process_by_elapsed() -> None:
    slow = Process(1, 9, "m", "claude")
    fast = Process(2, 1, "m", "claude")
    assert collect.min_process([slow, fast]) is fast
    assert collect.min_process([fast, slow]) is fast


def test_matching_agent_filters_under(tmp_path: Path, monkeypatch: Any) -> None:
    inside = Process(1, 5, "m", "claude")
    outside = Process(2, 1, "m", "claude")
    monkeypatch.setattr(collect, "agents_of", lambda rows: [inside, outside])
    monkeypatch.setattr(collect, "agent_under", lambda real, process: process is inside)
    found = collect.matching_agent([], tmp_path)
    assert found is inside


def test_row_agent_passes_pid_elapsed() -> None:
    process = collect.row_agent(4, 0, 8, ["claude"])
    assert process == Process(pid=4, elapsed_s=8, model="", backend="claude")


def test_run_ps_swallows_subprocess_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "marestail.tui.collect.subprocess.run",
        lambda *args, **options: (_ for _ in ()).throw(subprocess.SubprocessError("no")),
    )
    assert collect.run_ps() == ""


def test_ints_row_and_parse_ps_split() -> None:
    row = collect.ints_row(["1", "2", "3", "python cli.py run extra"])
    assert row == (1, 2, 3, ["python", "cli.py", "run", "extra"])
    parsed = collect.parse_ps_line("10 20 30 python cli.py run")
    assert parsed == (10, 20, 30, ["python", "cli.py", "run"])
    long = collect.parse_ps_line("1 2 3 a b c d")
    assert long == (1, 2, 3, ["a", "b", "c", "d"])


def test_gate_text_uses_max_elapsed() -> None:
    assert collect.gate_text([(1, "echo"), (5, "pytest")]) == "pytest 5s"
    assert collect.gate_text([(5, "pytest"), (1, "echo")]) == "pytest 5s"
    assert collect.gate_text([(5, "echo"), (5, "pytest")]) == "echo 5s"


def test_first_present_keeps_empty_text() -> None:
    assert collect.first_present(("",)) == ""
    assert collect.first_present((None, "x")) == "x"
    assert collect.first_present((None,)) is None


def test_push_level_walks_children() -> None:
    children = {1: [2], 2: [3]}
    found: list[int] = []
    collect.push_level(children, [1], found)
    assert found == [1, 2, 3]


def test_gate_hit_pid_default() -> None:
    assert collect.gate_hit_pid({}, 1) == []
    by_pid = {1: (4, ["pytest"])}
    assert collect.gate_hit_pid(by_pid, 1) == [(4, "pytest")]


def test_skipped_gate_backends() -> None:
    assert collect.skipped_gate(["claude"], ["claude"]) is True
    assert collect.skipped_gate(["echo"], ["echo"]) is False


def test_java_pmd_eunit_pass_names() -> None:
    assert collect.java_sonar(["java"], ["-Dsonar"]) == "sonar"
    assert collect.pmd_gate(["java"], ["PmdCli"]) == "pmd"
    assert collect.eunit_gate(["erlc"], []) == "eunit"
    assert collect.eunit_gate([], ["beam.eunit"]) == "eunit"


def test_docker_inner_index_and_flags() -> None:
    tokens = ["docker", "compose", "run", "--rm", "svc", "pytest"]
    assert collect.docker_inner(tokens) == "pytest"
    assert collect.compose_run_target(tokens, 0) == "pytest"
    short = ["docker", "compose", "run", "svc"]
    assert collect.compose_run_target(short, 0) is None
    assert collect.docker_inner(["echo", "compose", "run", "svc", "pytest"]) is None


def test_time_fmt_boundaries() -> None:
    assert collect.mins_fmt(3599) == "59m"
    assert collect.mins_fmt(3600) is None
    assert collect.hours_fmt(3600) == "1h00m"
    assert collect.hours_fmt(3660) == "1h01m"
    assert collect.hours_fmt(3661) == "1h01m"
    assert collect.fmt_seconds(10) == "10s"
    assert collect.fmt_seconds(120) == "2m"
    assert collect.fmt_seconds(3600) == "1h00m"


def test_model_of_empty_and_flag() -> None:
    assert collect.model_of([]) == ""
    assert collect.model_of(["--model", "opus"]) == "opus"
    assert collect.model_of(["-m", "haiku"]) == "haiku"
    assert collect.model_of(["--model"]) == ""


def test_path_under_false_when_unrelated(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    nested = tmp_path / "a"
    nested.mkdir()
    assert collect.path_under(nested, tmp_path) is True
    assert collect.path_under(tmp_path, tmp_path) is True
    assert collect.path_under(other, nested) is False


def test_run_git_swallows_subprocess_error(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "marestail.tui.collect.subprocess.run",
        lambda *args, **options: (_ for _ in ()).throw(subprocess.SubprocessError("no")),
    )
    assert collect.run_git(tmp_path, ["status"]) is None


def test_ok_git_missing_returncode() -> None:
    class Bare:
        pass

    assert collect.ok_git(Bare()) is False

    class Zero:
        returncode = 0

    assert collect.ok_git(Zero()) is True


def test_str_or_raw_and_result_field() -> None:
    assert collect.str_or_raw("yes", "raw") == "yes"
    assert collect.str_or_raw(1, "raw") == "raw"
    assert collect.result_field({"result": "ok"}) == "ok"
    assert collect.result_field(["no"]) is None
