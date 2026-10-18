import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.gates import comments, deadcode
from tests.conftest import make_context

INSTALL = golang.INSTALL["go"]
PIN = "github.com/go-gremlins/gremlins v0.6.0"
ENV_KEYS = ["GOVERSION", "GOROOT", "GOBIN", "GOPATH"]


def plant(root: Path, relative: str, text: str = "package p\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def env_json(version: str) -> str:
    payload = {"GOVERSION": version, "GOROOT": "/usr/lib/go", "GOBIN": "", "GOPATH": "/home/max/go"}
    return json.dumps(payload)


def test_listify_and_go_bin(tmp_path: Path) -> None:
    assert golang.listify(None) == []
    assert golang.listify(["a", 1]) == ["a", "1"]
    assert golang.listify("a") == ["a"]
    assert golang.go_bin(make_context(tmp_path)) == ["go"]
    assert golang.go_bin(make_context(tmp_path, {"go": {"go": "/usr/lib/go/bin/go"}})) == ["/usr/lib/go/bin/go"]


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (127, "missing", f"go is not installed: {INSTALL}"),
        (2, "  boom\n", "go env failed: boom"),
        (0, "[]", "go env failed: []"),
        (0, "note", "go env failed: note"),
        (1, env_json("go1.25.0"), "go env failed: " + env_json("go1.25.0")),
        (0, env_json("go1.24.9"), f"go 1.25 or newer is required, found go1.24.9: {INSTALL}"),
        (0, env_json("devel"), f"go 1.25 or newer is required, found devel: {INSTALL}"),
        (0, env_json(""), f"go 1.25 or newer is required, found : {INSTALL}"),
    ],
)
def test_toolchain_rejects(tmp_path: Path, fake_run: Any, code: int, output: str, expected: str) -> None:
    fake = fake_run(golang, [(code, output)])
    ctx = make_context(tmp_path, {"go": {"root": "svc"}})
    plant(tmp_path, "svc/go.mod")
    assert golang.toolchain(ctx) == expected
    assert golang.toolchain(ctx) == expected
    assert fake.calls == [["go", "env", "-json", *ENV_KEYS]]
    assert fake.options == [{"cwd": tmp_path / "svc", "timeout": 60}]


@pytest.mark.parametrize("version", ["go1.25.0", "go1.25", "go1.27.0-X:nodwarf5"])
def test_toolchain_accepts(tmp_path: Path, fake_run: Any, version: str) -> None:
    fake_run(golang, [(0, env_json(version))])
    loaded = golang.toolchain(make_context(tmp_path, {"go": {}}))
    assert isinstance(loaded, dict)
    assert loaded["GOVERSION"] == version


def test_module_missing_runs_nothing(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(golang, [])
    ctx = make_context(tmp_path, {"go": {}})
    message = "go.mod:1 missing; [go] root must name the folder that holds go.mod"
    assert golang.module(ctx) == message
    assert golang.preflight(ctx) == message
    assert fake.calls == []
    nested = make_context(tmp_path, {"go": {"root": "svc"}})
    assert golang.module(nested) == "svc/go.mod:1 missing; [go] root must name the folder that holds go.mod"


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (127, "missing", f"go is not installed: {INSTALL}"),
        (1, "y" * 400, "go mod edit -json failed: " + ("y" * 300)),
        (0, "[]", "go mod edit -json failed: []"),
        (0, '{"Module": {}}', "go mod edit -json failed: " + '{"Module": {}}'),
        (0, '{"Module": "x"}', 'go mod edit -json failed: {"Module": "x"}'),
        (0, '{"Module": {"Path": 1}}', 'go mod edit -json failed: {"Module": {"Path": 1}}'),
        (2, '{"Module": {"Path": "m"}}', 'go mod edit -json failed: {"Module": {"Path": "m"}}'),
    ],
)
def test_module_failures_stop_before_env(tmp_path: Path, fake_run: Any, code: int, output: str, expected: str) -> None:
    fake = fake_run(golang, [(code, output)])
    plant(tmp_path, "go.mod", "module m\n")
    ctx = make_context(tmp_path, {"go": {}})
    assert golang.preflight(ctx) == expected
    assert [call[1] for call in fake.calls] == ["mod"]
    assert fake.calls[0][:4] == ["go", "mod", "edit", "-json"]
    assert fake.options[0] == {"cwd": tmp_path, "timeout": 60}


def test_preflight_accepts_module_then_toolchain(tmp_path: Path, fake_run: Any) -> None:
    payload = '{"Module": {"Path": "example.com/mod"}}\n'
    fake = fake_run(golang, [(0, payload), (0, env_json("go1.27.0-X:nodwarf5"))])
    ctx = make_context(tmp_path, {"go": {"go": "/usr/lib/go/bin/go"}})
    plant(tmp_path, "go.mod")
    assert golang.preflight(ctx) is None
    assert golang.preflight(ctx) is None
    broken = make_context(tmp_path, {"go": {}})
    failed = fake_run(golang, [(0, payload), (127, "missing")])
    assert golang.preflight(broken) == f"go is not installed: {INSTALL}"
    assert [call[1] for call in failed.calls] == ["mod", "env"]
    assert fake.calls == [
        ["/usr/lib/go/bin/go", "mod", "edit", "-json"],
        ["/usr/lib/go/bin/go", "env", "-json", *ENV_KEYS],
    ]


def test_tool_configured_pin(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang.shutil, "which", lambda name: "/unused")
    text = "\tmod\tgithub.com/go-gremlins/gremlins\tv0.6.0\th1:abc\n"
    fake = fake_run(golang, [(0, text)])
    ctx = make_context(tmp_path, {"go": {"gremlins": "/opt/gremlins"}})
    assert golang.tool(ctx, "gremlins") == "/opt/gremlins"
    assert fake.calls == [["go", "version", "-m", "/opt/gremlins"]]


def test_tool_on_path(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang.shutil, "which", lambda name: "/usr/bin/golangci-lint")
    text = "\tmod\tgithub.com/golangci/golangci-lint/v2\tv2.13.2\n"
    fake_run(golang, [(0, text)])
    assert golang.tool(make_context(tmp_path, {"go": {}}), "golangci-lint") == "/usr/bin/golangci-lint"


def test_tool_gobin_and_gopath(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang.shutil, "which", lambda name: None)
    gobin = tmp_path / "gobin"
    gobin.mkdir()
    gopath = tmp_path / "gopath"
    binary = gopath / "bin" / "gremlins"
    binary.parent.mkdir(parents=True)
    binary.write_text("x")
    other = tmp_path / "other" / "bin" / "gremlins"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    ctx = make_context(tmp_path, {"go": {}})
    ctx.memo[golang.TOOLCHAIN] = {"GOBIN": str(gobin), "GOPATH": f"{gopath}:{tmp_path / 'other'}", "GOVERSION": "go1.25.0"}
    fake_run(golang, [(0, f"\tmod\t{PIN.replace(' ', chr(9))}\n")])
    assert golang.tool(ctx, "gremlins") == str(binary)


def test_tool_missing_and_bad_pins(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang.shutil, "which", lambda name: None)
    ctx = make_context(tmp_path, {"go": {}})
    ctx.memo[golang.TOOLCHAIN] = {"GOBIN": "", "GOPATH": "", "GOVERSION": "go1.25.0"}
    assert golang.tool(ctx, "gremlins") == f"gremlins is not installed: {golang.INSTALL['gremlins']}"
    ctx.memo.clear()
    ctx.memo[golang.TOOLCHAIN] = "go is not installed"
    assert golang.tool(ctx, "gremlins").startswith("gremlins is not installed")


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("\tmod\tgithub.com/go-gremlins/gremlins\tv0.5.0\n", "github.com/go-gremlins/gremlins v0.5.0"),
        ("\tmod\tgithub.com/go-gremlins/gremlins\t(devel)\n", "github.com/go-gremlins/gremlins (devel)"),
        ("\tmod\tother\tv0.6.0\n", "other v0.6.0"),
        ("noise\n\tpath\tonly\n", "no module version"),
        ("mod\tonly\n", "no module version"),
    ],
)
def test_tool_pin_mismatch(tmp_path: Path, fake_run: Any, text: str, found: str) -> None:
    fake_run(golang, [(0, text)])
    ctx = make_context(tmp_path, {"go": {"gremlins": "/opt/gremlins"}})
    message = f"gremlins at /opt/gremlins is {found}; the gate is pinned to {PIN}: {golang.INSTALL['gremlins']}"
    assert golang.tool(ctx, "gremlins") == message


def test_file_selection(tmp_path: Path) -> None:
    generated = "// Code generated by tool DO NOT EDIT.\npackage p\n"
    names = {
        "ok.go": "package ok\n",
        "ok_test.go": "package ok\n",
        "vendor/v.go": "package v\n",
        "testdata/t.go": "package t\n",
        ".marestail/m.go": "package m\n",
        ".git/g.go": "package g\n",
        "node_modules/n.go": "package n\n",
        ".hidden/h.go": "package h\n",
        "_blank/b.go": "package b\n",
        "perf/p.go": "package p\n",
        "sub/gen.go": generated,
        "sub/late.go": "package sub\n// Code generated by tool DO NOT EDIT.\n",
        "nested/go.mod": "module other\n",
        "nested/n.go": "package n\n",
    }
    for name, text in names.items():
        plant(tmp_path, name, text)
    ctx = make_context(tmp_path, {"go": {}})
    assert [path.relative_to(tmp_path).as_posix() for path in golang.sources(ctx)] == ["ok.go", "sub/late.go"]
    assert [path.relative_to(tmp_path).as_posix() for path in golang.tests(ctx)] == ["ok_test.go"]
    assert golang.files(make_context(tmp_path, {"go": {"root": "missing"}})) == []
    assert golang.generated(tmp_path) is False
    assert golang.kept(ctx, Path("/etc/passwd")) is False
    assert golang.generated_text(generated) is True
    assert golang.generated_text("package p\n") is False
    assert golang.generated_text("// note\n") is False


def test_rel_excluded_tags_and_scope(tmp_path: Path) -> None:
    plant(tmp_path, "svc/a.go")
    ctx = make_context(tmp_path, {"go": {"root": "svc", "coverage_exclude": ["sub", "gen/*.go", "/skip.go"]}})
    assert golang.rel(ctx, "a.go") == "svc/a.go"
    assert golang.rel(ctx, tmp_path / "svc" / "a.go") == "svc/a.go"
    assert golang.rel(ctx, "/etc/passwd") == "/etc/passwd"
    assert golang.excluded(ctx, "svc/sub/a.go", "coverage_exclude") is True
    assert golang.excluded(ctx, "svc/sub", "coverage_exclude") is True
    assert golang.excluded(ctx, "svc/gen/a.go", "coverage_exclude") is True
    assert golang.excluded(ctx, "svc/skip.go", "coverage_exclude") is True
    assert golang.excluded(ctx, "gen/a.go", "coverage_exclude") is False
    assert golang.excluded(ctx, "svc/a.go", "coverage_exclude") is False
    bare = make_context(tmp_path, {"go": {"coverage_exclude": ["a.go"]}})
    assert golang.mutation_excluded(bare, "a.go") is True
    explicit = make_context(tmp_path, {"go": {"coverage_exclude": ["a.go"], "mutation_exclude": []}})
    assert golang.mutation_excluded(explicit, "a.go") is False
    chosen = make_context(tmp_path, {"go": {"mutation_exclude": ["b.go"]}})
    assert golang.mutation_excluded(chosen, "b.go") is True
    assert golang.tags(make_context(tmp_path, {"go": {}}), "-tags") == []
    tagged = make_context(tmp_path, {"go": {"build_tags": ["a", "b"]}})
    assert golang.tags(tagged, "--tags") == ["--tags", "a,b"]
    paths = [tmp_path / "svc" / "a.go", tmp_path / "svc" / "b.go"]
    assert golang.in_scope(ctx, paths) == paths
    scoped = make_context(tmp_path, {"go": {"root": "svc"}}, scope_changed=True, changed={"svc/a.go"})
    assert golang.in_scope(scoped, paths) == [paths[0]]


def test_import_folder(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    assert golang.import_folder(ctx, "example.com/a", "fmt") is None
    assert golang.import_folder(ctx, "example.com/a", "example.com/a") == "."
    assert golang.import_folder(ctx, "example.com/a", "example.com/a/internal/store") == "internal/store"
    plant(tmp_path, "svc/go.mod")
    nested = make_context(tmp_path, {"go": {"root": "svc"}})
    assert golang.import_folder(nested, "example.com/a", "example.com/a") == "svc"
    assert golang.import_folder(nested, "example.com/a", "example.com/a/x") == "svc/x"


def test_scanner_contract() -> None:
    folder = golang.SCAN_DIR
    text = (folder / "main.go").read_text()
    assert (folder / "go.mod").read_text() == "module marestail/goscan\n\ngo 1.25\n"
    assert not (folder / "go.sum").exists()
    assert list(folder.glob("*_test.go")) == []
    assert "/*" not in text
    assert "go/types" not in text
    assert all(not line.lstrip().startswith("//") for line in text.splitlines())
    digest = hashlib.sha256(b"".join((folder / name).read_bytes() for name in golang.SCAN_INPUTS)).hexdigest()
    assert golang.scanner_digest() == digest
    comment_at = comments.SCANNERS.index(comments.java_findings) + 1
    dead_at = deadcode.SCANNERS.index(deadcode.java_findings) + 1
    assert comments.SCANNERS[comment_at] is comments.go_findings
    assert comments.SCANNERS[comment_at + 1] is comments.markup_findings
    assert deadcode.SCANNERS[dead_at] is deadcode.go_findings


def test_build_scanner_is_fresh_then_rebuilt(tmp_path: Path, fake_run: Any) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    binary, stamp = golang.scanner_paths(ctx)
    binary.parent.mkdir(parents=True)
    binary.write_text("old")
    stamp.write_text(golang.scanner_digest())
    fake = fake_run(golang, [])
    assert golang.build_scanner(ctx) is None
    assert fake.calls == []
    stamp.write_text("stale")

    def reply(command: list[str]) -> tuple[int, str]:
        Path(command[4]).write_text("new")
        return 0, ""

    fake = fake_run(golang, reply)
    assert golang.build_scanner(ctx) is None
    assert fake.calls == [["go", "build", "-trimpath", "-o", str(binary), "."]]
    assert fake.options == [{"cwd": golang.SCAN_DIR, "env": golang.SCAN_ENV, "timeout": 300}]
    assert stamp.read_text() == golang.scanner_digest()
    assert golang.build_scanner(ctx) is None
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("code", "output", "create", "expected"),
    [
        (127, "missing", False, f"go is not installed: {INSTALL}"),
        (1, "z" * 400, False, "go scanner build failed: " + ("z" * 300)),
        (0, "quiet", False, "go scanner build failed: quiet"),
    ],
)
def test_build_scanner_failures(tmp_path: Path, fake_run: Any, code: int, output: str, create: bool, expected: str) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    fake_run(golang, [(code, output)])
    assert golang.build_scanner(ctx) == expected
    binary, stamp = golang.scanner_paths(ctx)
    assert stamp.exists() is create
    assert binary.exists() is create


def test_scan_paths_uses_and_failures(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    monkeypatch.setattr(golang, "build_scanner", lambda _ctx: (_ for _ in ()).throw(AssertionError("built")))
    assert golang.scan(ctx, "comments", []) == ([], None)
    monkeypatch.setattr(golang, "build_scanner", lambda _ctx: "no toolchain")
    assert golang.scan(ctx, "comments", [tmp_path / "a.go"]) == (None, "no toolchain")
    monkeypatch.setattr(golang, "build_scanner", lambda _ctx: None)
    source = tmp_path / "a.go"
    uses = tmp_path / "a_test.go"
    fake = fake_run(golang, [(0, '[{"file": "a.go"}]'), (0, ""), (1, "e" * 400)])
    assert golang.scan(ctx, "dead", [source], [uses]) == ([{"file": "a.go"}], None)
    binary = str(ctx.work / "go-scan" / "marestail-go-scan")
    assert fake.calls[0] == [binary, "dead", str(source), "--uses", str(uses)]
    assert fake.options[0] == {"cwd": tmp_path, "timeout": 600}
    assert golang.scan(ctx, "imports", [source]) == ([], None)
    failed = golang.scan(ctx, "depth", [source])
    assert failed[0] is None
    assert failed[1] == "go scanner failed (depth): " + ("e" * 300)


def test_load_coverage(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    assert golang.load_coverage(ctx) is None
    ctx.work.mkdir()
    ctx.work.joinpath("go-coverage.json").write_text('{"files": {}}\n')
    assert golang.load_coverage(ctx) == {"files": {}}


def test_readme_template_freeze_and_absent_section(tmp_path: Path) -> None:
    readme = Path("README.md").read_text()
    assert readme.index("The Java gates drive") < readme.index("The Go gates need")
    assert "| Gate | Python | TypeScript | Elixir | Ruby / Rails | C# / .NET | Erlang | Rust | Java | Go |" in readme
    assert readme.splitlines()[11].count("---") == 10
    cells = [line.split("|")[-2].strip() for line in readme.splitlines()[12:23]]
    assert cells == [
        "go test -coverprofile, statement blocks",
        "go/ast scanner + coverprofile",
        "gremlins",
        "imports vs `.go-layers.json` (the go tool rejects cycles and internal/)",
        "golangci-lint (pinned config), gofmt",
        "go/ast comment groups, directives exempt",
        "go/ast scanner (pass-throughs); internal/ enforced by the go tool",
        "golangci-lint unused, unreferenced exported names",
        "regex over sources",
        "—",
        "local SonarQube, built-in Go analyzer + coverprofile",
    ]
    template = Path("templates/marestail.toml").read_text()
    assert template.index("[java]") < template.index("[go]") < template.index("[sonar]")
    assert "marestail/go/golangci.yml" in template
    layers = json.loads(Path("templates/go-layers.json").read_text())
    assert layers["layers"][0]["forbid_external"] == ["net/http", "database/sql", "github.com/jackc/pgx/*"]
    exclusions = Path("sonar-project.properties").read_text().splitlines()
    assert exclusions[5].endswith(",marestail/go/**")
    assert "marestail/go/**" not in "\n".join(exclusions[:5] + exclusions[6:])
    from marestail.gates import docs, sonar

    assert sonar.go_properties(make_context(tmp_path)) == []
    assert docs.go_extra(make_context(tmp_path), ["x"]) == []
    assert comments.go_findings(make_context(tmp_path)) == []
    assert deadcode.go_findings(make_context(tmp_path)) == []
