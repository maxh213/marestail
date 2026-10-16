import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from marestail.tui import web, web_render, web_sources
from marestail.tui.model import Fleet, Process, RepoState, Step, Worker
from marestail.tui.web_sources import Dandelion, Dash, Host

NOW = 1_800_000_000.0
CLOCK = datetime.fromtimestamp(NOW, UTC)


def make_dash(tmp_path: Path, runs_script: Path | None = None) -> Dash:
    return Dash(root=tmp_path, every=60, runs_script=runs_script or tmp_path / "missing.py", cache=tmp_path / "cache" / "dandelion.json")


def completed(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["x"], code, out, err)


def runs_script(tmp_path: Path, payload: Any, code: int = 0) -> Path:
    script = tmp_path / "runs.py"
    script.write_text(f"import sys\nprint({json.dumps(json.dumps(payload))})\nsys.exit({code})\n")
    return script


def step(label: str, status: str = "done", verdict: str | None = None) -> Step:
    return Step(role=label, label=label, attempt=1, status=status, summary=f"<{label}>", verdict=verdict, minutes=1.0)


def repo(name: str, root: Path, alive: bool = False, **extra: Any) -> RepoState:
    return RepoState(name=name, root=root, branch="main", head="abc", task=extra.pop("task", None), log_path=None, alive=alive, **extra)


class StopLoopError(Exception):
    pass


def stop_after(count: int) -> Any:
    calls = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) >= count:
            raise StopLoopError

    return sleep


def test_settle_keeps_the_last_json_line_and_clears_the_error() -> None:
    state = Dandelion(error="old")
    web_sources.settle(state, completed(0, 'noise\n{"route": "a"}\n'))
    assert (state.data, state.error) == ({"route": "a"}, None)


@pytest.mark.parametrize(
    ("out", "expected"), [(completed(1, "", "boom\n"), "boom"), (completed(2, "said\n"), "said"), (completed(0, "  "), "exit 0")]
)
def test_settle_records_why_a_probe_failed(out: subprocess.CompletedProcess[str], expected: str) -> None:
    state = Dandelion(data={"kept": True})
    web_sources.settle(state, out)
    assert (state.data, state.error) == ({"kept": True}, expected)


def test_probe_once_runs_dandelion_json_and_remembers_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(web_sources.subprocess, "run", lambda command, **_: seen.append(command) or completed(0, '{"route": "r"}'))
    dash = make_dash(tmp_path)
    web_sources.probe_once(dash)
    assert seen == [["dandelion", "--json"]]
    assert dash.dandelion.data == {"route": "r"}
    assert dash.dandelion.running is False
    assert dash.dandelion.at
    assert json.loads(dash.cache.read_text())["data"] == {"route": "r"}


def test_probe_once_keeps_going_when_dandelion_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def missing(*_: Any, **__: Any) -> Any:
        raise FileNotFoundError("dandelion")

    monkeypatch.setattr(web_sources.subprocess, "run", missing)
    dash = make_dash(tmp_path)
    dash.cache = tmp_path
    web_sources.probe_once(dash)
    assert dash.dandelion.error == "dandelion"
    assert dash.dandelion.running is False


def test_probe_forever_sleeps_the_interval_between_probes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    probes: list[Dash] = []
    monkeypatch.setattr(web_sources, "probe_once", probes.append)
    sleep = stop_after(2)
    dash = make_dash(tmp_path)
    with pytest.raises(StopLoopError):
        web_sources.probe_forever(dash, sleep)
    assert len(probes) == 2


@pytest.mark.parametrize("text", [None, "{not json", '{"data": [1]}', "[1]"])
def test_recall_ignores_a_missing_or_unusable_cache(tmp_path: Path, text: str | None) -> None:
    dash = make_dash(tmp_path)
    if text is not None:
        dash.cache.parent.mkdir()
        dash.cache.write_text(text)
    web_sources.recall(dash)
    assert dash.dandelion == Dandelion()


def test_recall_restores_the_last_probe(tmp_path: Path) -> None:
    dash = make_dash(tmp_path)
    dash.cache.parent.mkdir()
    dash.cache.write_text(json.dumps({"data": {"route": "r"}, "error": "e", "at": 5}))
    web_sources.recall(dash)
    assert dash.dandelion == Dandelion(data={"route": "r"}, error="e", at=5)


STAT_BEFORE = "cpu 10 0 10 80 0 0 0 0\ncpu0 5 0 5 40 0 0 0 0\ncpu1 5 0 5 40 0 0 0 0\nintr 1 2\n"
STAT_AFTER = "cpu 20 0 20 160 0 0 0 0\ncpu0 15 0 15 50 0 0 0 0\ncpu1 5 0 5 40 0 0 0 0\nintr 1 2\n"


def test_cpu_usage_compares_two_samples_per_core() -> None:
    before, after = web_sources.cpu_sample(STAT_BEFORE), web_sources.cpu_sample(STAT_AFTER)
    assert before[0] == ("cpu", 100, 80)
    assert web_sources.cpu_usage(before, after) == {"all": 20.0, "cores": [("0", pytest.approx(66.666, abs=0.01)), ("1", 0.0)]}


def test_cpu_usage_needs_a_matching_earlier_sample() -> None:
    after = web_sources.cpu_sample(STAT_AFTER)
    assert web_sources.cpu_usage([], after) is None
    assert web_sources.cpu_usage(after[:1], after) is None


def test_cpu_forever_publishes_usage_and_survives_a_bad_read(tmp_path: Path) -> None:
    reads = iter([STAT_BEFORE, "cpu x", STAT_AFTER])
    dash = make_dash(tmp_path)
    halt = stop_after(3)

    def next_read() -> str:
        return next(reads)

    with pytest.raises(StopLoopError):
        web_sources.cpu_forever(dash, halt, next_read)
    assert dash.cpu is not None
    assert dash.cpu["all"] == 20.0


def test_memory_used_leaves_cache_out() -> None:
    info = web_sources.meminfo("MemTotal: 100 kB\nMemFree: 10 kB\nBuffers: 5 kB\nCached: 30 kB\nSReclaimable: 5 kB\nShmem: 10 kB\n")
    assert info["MemTotal"] == 102400
    assert web_sources.memory_used(info) == (100 - 10 - 5 - 25) * 1024
    assert web_sources.memory_used({}) == 0


def test_host_or_error_reads_memory_swap_disk_and_the_cpu_sample(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 100 kB\nMemFree: 50 kB\nSwapTotal: 10 kB\nSwapFree: 4 kB\n")
    monkeypatch.setattr(web_sources, "PROC_MEMINFO", meminfo)
    monkeypatch.setattr(web_sources.shutil, "disk_usage", lambda _: type("Disk", (), {"used": 3, "total": 9})())
    dash = make_dash(tmp_path)
    dash.cpu = {"all": 1.0, "cores": []}
    assert web_sources.host_or_error(dash) == Host(dash.cpu, 50 * 1024, 100 * 1024, 6 * 1024, 10 * 1024, 3, 9)


def test_host_or_error_says_why_it_cannot_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(web_sources, "PROC_MEMINFO", tmp_path / "absent")
    assert str(web_sources.host_or_error(make_dash(tmp_path))).startswith("unavailable: ")


def test_runs_table_lists_the_script_records(tmp_path: Path) -> None:
    dash = make_dash(tmp_path, runs_script(tmp_path, [{"repo": "r", "task": "t"}, "junk"]))
    assert web_sources.runs_table(dash) == [{"repo": "r", "task": "t"}]


@pytest.mark.parametrize(("payload", "code"), [([{"repo": "r"}], 1), ({"repo": "r"}, 0)])
def test_runs_table_is_empty_when_the_script_fails_or_answers_oddly(tmp_path: Path, payload: Any, code: int) -> None:
    assert web_sources.runs_table(make_dash(tmp_path, runs_script(tmp_path, payload, code))) == []


def test_runs_table_is_empty_without_the_script_or_with_garbage(tmp_path: Path) -> None:
    assert web_sources.runs_table(make_dash(tmp_path)) == []
    script = tmp_path / "garbage.py"
    script.write_text("print('not json')\n")
    assert web_sources.runs_table(make_dash(tmp_path, script)) == []


def test_beds_drop_samples_and_put_running_repos_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repos = [
        repo("b", tmp_path / "b"),
        repo("a", tmp_path / "a"),
        repo("z", tmp_path / "z", alive=True),
        repo("s", tmp_path / "tools" / "samples" / "s"),
    ]
    monkeypatch.setattr(web_sources, "collect_fleet", lambda roots: Fleet(repos=repos, scanned_at=0))
    found = web_sources.beds(make_dash(tmp_path))
    assert [bed.name for bed in found if isinstance(bed, RepoState)] == ["z", "a", "b"]


def test_beds_say_when_collecting_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def broken(roots: list[Path]) -> Fleet:
        raise RuntimeError("no proc")

    monkeypatch.setattr(web_sources, "collect_fleet", broken)
    assert web_sources.beds(make_dash(tmp_path)) == "collect failed: no proc"


def test_capped_keeps_the_head_or_the_tail() -> None:
    assert web_sources.capped(" short ", 10) == "short"
    assert web_sources.capped("abcdef", 3) == "abc\n…"
    assert web_sources.capped("abcdef", 3, tail=True) == "…\ndef"


def build_run(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "tasks").mkdir(parents=True)
    (root / "tasks" / "001-x.md").write_text("# do x")
    runs = root / ".marestail" / "runs" / "001-x"
    runs.mkdir(parents=True)
    (runs / "pipeline.log").write_text("old line\n== coder (03-coder) attempt 1\n   coding\n")
    archive = runs / "handoffs-20261016"
    archive.mkdir()
    (archive / "03-coder.md").write_text("handed off")
    return root


def test_task_view_reads_the_task_log_and_newest_archived_handoff(tmp_path: Path) -> None:
    root = build_run(tmp_path)
    record = {
        "repo": "proj",
        "task": "001-x",
        "root": str(root),
        "state": "running",
        "current_step": "coder",
        "verdict": "PASS",
        "detail": "busy",
    }
    dash = make_dash(tmp_path, runs_script(tmp_path, [record]))
    view = web_sources.task_view(dash, "proj", "001-x")
    assert view is not None
    assert (view["state"], view["step"], view["task_text"]) == ("running", "coder", "# do x")
    assert view["working"].split("\n\n")[:2] == ["running · coder · PASS", "busy"]
    assert "== coder (03-coder) attempt 1\n   coding" in view["working"]
    assert "old line" not in view["working"]
    assert view["working"].endswith("── 03-coder.md\nhanded off")


def test_task_view_prefers_live_handoffs_and_reads_a_named_log(tmp_path: Path) -> None:
    root = build_run(tmp_path)
    live = root / ".marestail" / "handoffs" / "001-x"
    live.mkdir(parents=True)
    (live / "04-cleaner.md").write_text("live one")
    log = root / "elsewhere.log"
    log.write_text("plain log")
    record = {"repo": "proj", "task": "001-x", "root": str(root), "log": str(log)}
    view = web_sources.task_view(make_dash(tmp_path, runs_script(tmp_path, [record])), "proj", "001-x")
    assert view is not None
    assert view["state"] == ""
    assert view["step"] == ""
    assert view["working"] == "unknown\n\nplain log\n\n── 04-cleaner.md\nlive one"


@pytest.mark.parametrize(("repo_name", "task"), [("", "t"), ("a/b", "t"), ("a..b", "t"), ("proj", "bad/name"), ("proj", "nope")])
def test_task_view_refuses_odd_names_and_unknown_runs(tmp_path: Path, repo_name: str, task: str) -> None:
    root = build_run(tmp_path)
    dash = make_dash(tmp_path, runs_script(tmp_path, [{"repo": "proj", "task": "001-x", "root": str(root)}]))
    assert web_sources.task_view(dash, repo_name, task) is None


def test_task_view_refuses_runs_outside_the_root(tmp_path: Path) -> None:
    dash = make_dash(tmp_path / "ws", runs_script(tmp_path, [{"repo": "proj", "task": "001-x", "root": str(tmp_path)}]))
    (tmp_path / "ws").mkdir()
    assert web_sources.task_view(dash, "proj", "001-x") is None


def test_file_helpers_cope_with_missing_paths(tmp_path: Path) -> None:
    assert web_sources.read_capped(tmp_path / "absent", 5) == ""
    assert web_sources.log_excerpt(tmp_path / "absent") == ""
    assert web_sources.newest_md(tmp_path / "absent") is None
    assert web_sources.newest_handoff(tmp_path, "t") is None
    assert web_sources.inside(tmp_path / "a", tmp_path / "b") is False


def test_text_helpers() -> None:
    assert web_render.esc(None) == ""
    assert web_render.esc("<a>") == "&lt;a&gt;"
    assert [web_render.ago(ts, NOW) for ts in (None, NOW - 30, NOW - 600, NOW - 7200)] == ["", "30s ago", "10m ago", "2h ago"]
    assert web_render.until(None, CLOCK) == ""
    assert web_render.until("soon", CLOCK) == ""
    assert web_render.until("2020-01-01T00:00:00Z", CLOCK) == "reset due"
    assert web_render.until(datetime.fromtimestamp(NOW + 90061, UTC).isoformat(), CLOCK) == "↻ 1d1h"
    assert web_render.until(datetime.fromtimestamp(NOW + 3720, UTC).isoformat(), CLOCK) == "↻ 1h02m"
    assert [web_render.tone(pct) for pct in (10, 60, 90, 99)] == ["calm", "warm", "hot", "crit"]
    assert [web_render.fmt_elapsed(s) for s in (65, 3725)] == ["1m05s", "1h02m"]
    assert [web_render.human_bytes(n) for n in (-1, 2048, 5 * 1024**4)] == ["0B", "2.0K", "5.0T"]


def test_bar_and_meter_clamp_and_colour() -> None:
    assert web_render.bar(150) == f'<span class="bar crit">{"█" * 24}</span> <b>100%</b>'
    assert web_render.bar(None).endswith("<b>0%</b>")
    assert (
        web_render.meter(50, "x<", 4)
        == '<span class="dim">[</span><span class="bar warm" title="50%">██░░</span><span class="dim">]</span> x&lt;'
    )


def test_step_glyphs() -> None:
    assert [
        web_render.step_glyph(step("s", status, verdict))
        for status, verdict in (("running", None), ("done", "PASS"), ("done", "BOUNCE"), ("done", "FAIL"), ("done", None))
    ] == [
        "▶",
        "✓",
        "↩",
        "✗",
        "·",
    ]


def test_render_dandelion_before_the_first_probe() -> None:
    assert "first probe running" in web_render.render_dandelion(Dandelion(running=True), NOW)
    assert "<small>probing…</small>" in web_render.render_dandelion(Dandelion(running=True), NOW)
    assert "nope" in web_render.render_dandelion(Dandelion(error="nope", at=NOW - 30), NOW)


def test_render_dandelion_shows_routes_providers_and_windows() -> None:
    reset = datetime.fromtimestamp(NOW + 7200, UTC).isoformat()
    providers = [
        {"id": "kimi", "status": "unavailable", "reason": "no token"},
        {"id": "codex", "displayName": "codex", "planLabel": "plus", "status": "ok", "windows": []},
        {
            "id": "claude",
            "planLabel": "max",
            "status": "ok",
            "eligible": False,
            "windows": [{"label": "5h", "usedPct": 40, "resetsAt": reset, "projectedFullAt": reset}, "junk"],
        },
        "junk",
    ]
    out = web_render.render_dandelion(Dandelion(data={"providers": providers, "route": "r1", "routeHigh": "r2"}, error="late", at=NOW), NOW)
    assert "route → <b>r1</b>" in out
    assert "route --high → <b>r2</b>" in out
    assert '<div class="prov off"><span class="name">kimi</span> <span class="dim"></span> <span class="dim">no token</span></div>' in out
    assert '<div class="win dim">no usage windows</div>' in out
    assert "routing off" in out
    assert "↻ 2h00m → 100% 2h00m" in out
    assert "last error: late" in out


def test_render_fleet_shows_live_and_idle_beds(tmp_path: Path) -> None:
    worker = Worker(
        step=step("coder", "running"),
        process=Process(pid=1, elapsed_s=3725, model="m", backend="claude"),
        result_path=None,
        prompt_path=None,
        handoff_path=None,
        tail_lines=["w1", "w2"],
    )
    beds = [
        repo("busy", tmp_path, alive=True, task="001", worker=worker, steps=[step("critic", verdict="PASS"), step("coder", "running")]),
        repo("gating", tmp_path, alive=True, gate_activity="gate ts.tests", tail_lines=["t1"]),
        repo("idle", tmp_path, tail_lines=["x" * 300]),
    ]
    out = web_render.render_fleet(beds)
    assert "2 running · 3 beds" in out
    assert '▶ coder <span class="dim">1h02m m claude</span>' in out
    assert '<div class="tail">w2</div>' in out
    assert '▶ <span class="dim">gate ts.tests</span>' in out
    assert '<span class="task">001</span>' in out
    assert ('<div class="tail">' + "x" * 220 + "</div>") in out
    assert web_render.render_fleet("collect failed: x") == '<h2>marestail watch</h2><p class="dim">collect failed: x</p>'


def test_render_fleet_live_worker_without_a_process(tmp_path: Path) -> None:
    worker = Worker(step=step("qa", "running"), process=None, result_path=None, prompt_path=None, handoff_path=None)
    assert '▶ qa <span class="dim"></span>' in web_render.render_fleet([repo("r", tmp_path, alive=True, worker=worker)])


def test_render_runs_sorts_and_drops_leftovers() -> None:
    table = [
        {"repo": "a", "task": "1", "state": "complete", "last_activity": NOW - 10},
        {"repo": "b", "task": "2", "state": "running", "current_step": "coder", "last_activity": NOW - 30},
        {"repo": "c", "task": "3", "state": "leftover"},
        {"repo": "d", "task": "4", "state": "dead", "detail": "killed", "last_activity": NOW - 5},
    ]
    out = web_render.render_runs(table, NOW)
    assert "3 known" in out
    assert ">c<" not in out
    assert out.index(">d<") < out.index(">b<") < out.index(">a<")
    assert 'data-repo="b" data-task="2"' in out
    assert "<td>killed</td>" in out
    assert "30s ago" in out


def test_render_host_with_and_without_cpu_and_swap() -> None:
    assert web_render.render_host("unavailable: x") == '<h2>host</h2><p class="dim">unavailable: x</p>'
    sampling = web_render.render_host(Host(None, 1, 0, 0, 0, 1, 0))
    assert "sampling…" in sampling
    assert "swp" not in sampling
    assert "1B/0B" in sampling
    busy = web_render.render_host(Host({"all": 50.0, "cores": [("0", 10.0), ("1", 99.0)]}, 512, 1024, 1, 4, 2, 4))
    assert "swp" in busy
    assert '<span class="crit">99.0%</span>' in busy
    assert "512B/1.0K" in busy
    single = web_render.render_host(Host({"all": 5.0, "cores": [("0", 5.0)]}, 1, 2, 0, 0, 1, 2))
    assert 'class="cores"' not in single


def test_document_carries_the_assets() -> None:
    out = web_render.document("box", "now", ["<p>s</p>"])
    assert out.startswith("<!doctype html>")
    assert "<title>marestail dash</title>" in out
    assert web_render.CSS in out
    assert web_render.HEAD_JS in out
    assert web_render.TAIL_JS in out
    assert 'id="task-pop"' in out
    assert "marestail · box" in out
    assert "<p>s</p>" in out


class Started:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    def __call__(self, status: str, headers: list[tuple[str, str]]) -> None:
        self.calls.append((status, headers))


def call(app: web.App, path: str, query: str = "", method: str = "GET") -> tuple[str, dict[str, str], bytes]:
    started = Started()
    body = b"".join(app({"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query}, started))
    status, headers = started.calls[0]
    return status, dict(headers), body


def stub_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_sources, "host_or_error", lambda dash: "unavailable: test")
    monkeypatch.setattr(web_sources, "beds", lambda dash: [])
    monkeypatch.setattr(web_sources, "runs_table", lambda dash: [{"repo": "r", "task": "t", "state": "running"}])


def test_app_serves_the_page_json_and_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stub_sources(monkeypatch)
    dash = make_dash(tmp_path)
    dash.dandelion = Dandelion(data={"providers": []}, at=5)
    app = web.application(dash)
    status, headers, body = call(app, "/")
    assert status == "200 OK"
    assert headers["Content-Type"] == web.HTML
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Length"] == str(len(body))
    assert b"marestail runs" in body
    assert json.loads(call(app, "/json")[2]) == {
        "dandelion": {"providers": []},
        "dandelion_at": 5,
        "runs": [{"repo": "r", "task": "t", "state": "running"}],
    }
    assert call(app, "/", method="HEAD")[2] == b""
    assert call(app, "")[0] == "200 OK"


def test_app_task_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        web_sources, "task_view", lambda dash, repo_name, task: seen.append((repo_name, task)) or ({"task": task} if task == "t" else None)
    )
    app = web.application(make_dash(tmp_path))
    assert call(app, "/task", "repo=r&task=t")[0:3:2] == ("200 OK", b'{"task": "t"}')
    status, _, body = call(app, "/task")
    assert (status, json.loads(body)) == ("404 Not Found", {"error": "no such run"})
    assert seen == [("r", "t"), ("", "")]


def test_quiet_handler_drops_request_logs() -> None:
    assert web.quiet("x", 1) is None
    assert web.QuietHandler.log_message is web.quiet


def test_start_probes_recalls_then_starts_both_threads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    started: list[Any] = []
    recalled: list[Dash] = []

    class FakeThread:
        def __init__(self, target: Any, args: tuple[Any, ...], daemon: bool) -> None:
            self.target, self.daemon = target, daemon

        def start(self) -> None:
            started.append((self.target, self.daemon))

    monkeypatch.setattr(web.threading, "Thread", FakeThread)
    monkeypatch.setattr(web_sources, "recall", recalled.append)
    dash = make_dash(tmp_path)
    web.start_probes(dash)
    assert recalled == [dash]
    assert started == [(web_sources.probe_forever, True), (web_sources.cpu_forever, True)]


class FakeServer:
    def __init__(self, log: list[Any]) -> None:
        self.log = log

    def __enter__(self) -> "FakeServer":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def serve_forever(self) -> None:
        self.log.append("served")


def test_run_serves_until_stopped_with_the_default_runs_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    made: list[Any] = []
    dashes: list[Dash] = []
    monkeypatch.setattr(
        web,
        "make_server",
        lambda host, port, app, server_class, handler_class: made.append((host, port, server_class, handler_class)) or FakeServer(made),
    )
    monkeypatch.setattr(web, "start_probes", dashes.append)
    assert web.run(tmp_path, "0.0.0.0", 1234, 30.0, None) == 0
    assert made == [("0.0.0.0", 1234, web.ThreadingServer, web.QuietHandler), "served"]
    assert dashes[0].runs_script == web.RUNS_SCRIPT
    assert dashes[0].every == 30.0
    assert dashes[0].root == tmp_path
    assert web.run(tmp_path, "h", 1, 1.0, tmp_path / "r.py") == 0
    assert dashes[1].runs_script == tmp_path / "r.py"


def test_the_runs_script_runs_with_this_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []
    script = runs_script(tmp_path, [])
    monkeypatch.setattr(web_sources.subprocess, "run", lambda command, **_: seen.append(command) or completed(0, "[]"))
    web_sources.runs_table(make_dash(tmp_path, script))
    assert seen == [[sys.executable, str(script), "--json", "--all", "--root", str(tmp_path)]]
