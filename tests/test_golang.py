import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.gates import comments, deadcode, docs, go_deps
from tests.conftest import make_context

INSTALL = golang.INSTALL["go"]
PIN = "github.com/go-gremlins/gremlins v0.6.0"
ENV_KEYS = ["GOVERSION", "GOROOT", "GOBIN", "GOPATH"]
SAMPLE = Path("tools/samples/go").resolve()
SAMPLE_MODULE = "example.com/marestail/sample"
IMPORT_LINE = re.compile(r'^\s*(?:import\s+)?"([^"]+)"$')
FIXED_DOMAIN = """package domain

type bound struct {
\tleast int
\tname  string
}

var bounds = []bound{{41, "huge"}, {31, "big"}, {21, "mid"}, {10, "small"}}

func Classify(n int) string {
\tfor _, b := range bounds {
\t\tif n >= b.least {
\t\t\treturn b.name
\t\t}
\t}
\treturn "tiny"
}

func Tag(n int) string {
\tswitch n {
\tcase 0:
\t\treturn "zero"
\tdefault:
\t\treturn "other"
\t}
}
"""
FIXED_STORE = """package store

import (
\t"fmt"
\t"os"
)

type Store struct {
\titems []int
}

func New(items []int) Store {
\treturn Store{items: items}
}

func (s Store) Size() int {
\treturn len(s.items)
}

func (s Store) Label() string {
\treturn fmt.Sprintf("box %d", s.Size())
}

func Greeting() string {
\treturn os.Getenv("SAMPLE_GREETING")
}
"""
SURFACE = [
    "INSTALL",
    "MINIMUM",
    "MODULE_KEY",
    "PACKAGE",
    "PINS",
    "SCAN_DIR",
    "SCAN_ENV",
    "SCAN_INPUTS",
    "TOOLCHAIN",
    "build_scanner",
    "excluded",
    "files",
    "generated",
    "go_bin",
    "go_missing",
    "import_folder",
    "in_scope",
    "kept",
    "listify",
    "load_coverage",
    "module",
    "mutation_excluded",
    "preflight",
    "rel",
    "scan",
    "scanner_digest",
    "scanner_paths",
    "sources",
    "tags",
    "tests",
    "tool",
    "toolchain",
]


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
        (0, '{"GOROOT": "/usr/lib/go"}', f"go 1.25 or newer is required, found : {INSTALL}"),
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
    monkeypatch.setattr(shutil, "which", lambda name: "/unused")
    text = "\tmod\tgithub.com/go-gremlins/gremlins\tv0.6.0\th1:abc\n"
    fake = fake_run(golang, [(0, text)])
    ctx = make_context(tmp_path, {"go": {"gremlins": "/opt/gremlins"}})
    assert golang.tool(ctx, "gremlins") == "/opt/gremlins"
    assert fake.calls == [["go", "version", "-m", "/opt/gremlins"]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 60}]
    lint = "\tmod\tgithub.com/golangci/golangci-lint/v2\tv2.13.2\n"
    fake = fake_run(golang, [(0, lint)])
    configured = make_context(tmp_path, {"go": {"golangci_lint": "/opt/lint", "golangci-lint": "/wrong"}})
    assert golang.tool(configured, "golangci-lint") == "/opt/lint"
    assert fake.calls == [["go", "version", "-m", "/opt/lint"]]


def test_tool_on_path(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", {"golangci-lint": "/usr/bin/golangci-lint"}.get)
    text = "\tmod\tgithub.com/golangci/golangci-lint/v2\tv2.13.2\n"
    fake_run(golang, [(0, text)])
    assert golang.tool(make_context(tmp_path, {"go": {}}), "golangci-lint") == "/usr/bin/golangci-lint"


def test_tool_gobin_and_gopath(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
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
    first = gobin / "gremlins"
    first.write_text("x")
    fake_run(golang, [(0, f"\tmod\t{PIN.replace(' ', chr(9))}\n")])
    assert golang.tool(ctx, "gremlins") == str(first)


def test_bin_dirs_skip_unset_folders(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    ctx.memo[golang.TOOLCHAIN] = {"GOVERSION": "go1.25.0"}
    assert golang._bin_dirs(ctx) == []
    ctx.memo[golang.TOOLCHAIN] = {"GOVERSION": "go1.25.0", "GOBIN": "", "GOPATH": ""}
    assert golang._bin_dirs(ctx) == []
    ctx.memo[golang.TOOLCHAIN] = {"GOVERSION": "go1.25.0", "GOBIN": "/g/bin", "GOPATH": "/p1:/p2:/p3"}
    assert golang._bin_dirs(ctx) == [Path("/g/bin"), Path("/p1/bin")]
    ctx.memo[golang.TOOLCHAIN] = "go is not installed"
    assert golang._bin_dirs(ctx) == []


def test_tool_missing_and_bad_pins(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
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
        ("mod github.com/go-gremlins/gremlins v0.6.0\n", "no module version"),
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
        "a/vendor/x.go": "package x\n",
        "a/y.go": "package a\n",
        "deep/inner/go.mod": "module inner\n",
        "deep/inner/pkg/x.go": "package pkg\n",
        "go.mod": "module root\n",
    }
    for name, text in names.items():
        plant(tmp_path, name, text)
    ctx = make_context(tmp_path, {"go": {}})
    assert [path.relative_to(tmp_path).as_posix() for path in golang.sources(ctx)] == ["a/y.go", "ok.go", "sub/late.go"]
    assert [path.relative_to(tmp_path).as_posix() for path in golang.tests(ctx)] == ["ok_test.go"]
    assert golang.files(make_context(tmp_path, {"go": {"root": "missing"}})) == []
    assert golang.generated(tmp_path) is False
    assert golang.kept(ctx, Path("/etc/passwd")) is False
    assert golang._generated_text(generated) is True
    assert golang._generated_text("package p\n") is False
    assert golang._generated_text("// note\n") is False
    broken = tmp_path / "broken.go"
    broken.write_bytes(b"// Code generated by tool DO NOT EDIT.\npackage p\n\xff\n")
    assert golang.generated(broken) is True


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
    folder = tmp_path / ".marestail" / "go-scan"
    assert (binary, stamp) == (folder / "marestail-go-scan", folder / "stamp")
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
    monkeypatch.setattr(golang, "build_scanner", lambda built: None if built is ctx else "another context")
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
    from marestail.gates import docs, sonar

    assert sonar.go_properties(make_context(tmp_path)) == []
    assert docs.go_extra(make_context(tmp_path), ["x"]) == []
    assert comments.go_findings(make_context(tmp_path)) == []
    assert deadcode.go_findings(make_context(tmp_path)) == []


def test_the_adapter_surface_and_where_go_sits() -> None:
    assert golang.__all__ == SURFACE
    text = Path(".importlinter").read_text()
    assert text.count("[importlinter:contract:") == 38
    assert "marestail.rust : marestail.golang :" in text
    assert text.count("marestail.rust\n    marestail.golang\n") == 3
    independent = text.split("[importlinter:contract:gates-are-independent]", 1)[1].split("\n[", 1)[0]
    assert all(f"marestail.gates.{name}\n" in independent for name in ("go_crap", "go_deps", "go_lint", "go_mutation", "go_tests"))
    allowed = text.split("[importlinter:contract:crap-belongs-to-crap-gates]", 1)[1].split("\n[", 1)[0]
    assert "marestail.gates.go_crap\n" in allowed
    source = Path("marestail/gates/go_crap.py").read_text()
    assert "rs_crap" not in source
    assert "marestail.gates._crap" in source
    assert "marestail.gates._hyper_crap" in source


def sample_imports(root: Path) -> list[dict[str, Any]]:
    return [
        {"file": str(path), "line": number, "path": found.group(1)}
        for path in sorted((root / "internal").rglob("*.go"))
        if not path.name.endswith("_test.go")
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if (found := IMPORT_LINE.match(line))
    ]


def sample_gates(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    ctx = make_context(root, tomllib.loads((root / "marestail.toml").read_text()))
    ctx.memo[golang.MODULE_KEY] = SAMPLE_MODULE
    monkeypatch.setattr(golang, "preflight", lambda _ctx: None)
    monkeypatch.setattr(golang, "scan", lambda _ctx, mode, files: (sample_imports(root), None))
    return docs.run_gate(ctx), go_deps.run_gate(ctx)


def test_the_red_sample_plants_each_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    domain = (SAMPLE / "internal" / "domain" / "domain.go").read_text().splitlines()
    store = (SAMPLE / "internal" / "store" / "store.go").read_text().splitlines()
    assert (SAMPLE / "go.mod").read_text() == f"module {SAMPLE_MODULE}\n\ngo 1.25\n"
    assert domain[8:10] == ["// Classify names the size of n.", "func Classify(n int) string {"]
    assert [line.index(">") + 1 for line in domain[10:14]] == [7, 8, 9, 10]
    assert (domain[34], domain[35]) == ("func Label(s store.Store) string {", "\treturn describe(s)")
    assert (store[9], store[13], store[14]) == ("\treturn Store{items:items}", "\tsize := 0", "\tsize = len(s.items)")
    assert (store[19], store[22]) == ('\treturn os.Getenv("SAMPLE_GREETING")', "func UnusedHelper() int {")
    assert "SAMPLE_GREETING" not in (SAMPLE / "README.md").read_text()
    config = tomllib.loads((SAMPLE / "marestail.toml").read_text())
    assert (config["git"], config["go"], config["comments"]) == ({"base": "HEAD"}, {"root": ".", "crap_max": 4}, {"paths": ["internal"]})
    assert config["sonar"]["project_key"] == "marestail-go-sample"
    drift, layers = sample_gates(SAMPLE, monkeypatch)
    assert drift.findings == ["internal/store/store.go:20 environment variable SAMPLE_GREETING is not documented"]
    assert layers.findings == [
        f"internal/domain/domain.go:6 internal/domain must not depend on internal/store ({SAMPLE_MODULE}/internal/store)"
    ]
    assert (drift.ok, layers.ok, layers.summary) == (False, False, "1 layer breaks")


def test_the_fixed_sample_clears_the_planted_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "go"
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".marestail"))
    (root / "internal" / "domain" / "domain.go").write_text(FIXED_DOMAIN)
    (root / "internal" / "store" / "store.go").write_text(FIXED_STORE)
    readme = root / "README.md"
    readme.write_text(readme.read_text() + "\nThe store greets with `SAMPLE_GREETING`.\n")
    drift, layers = sample_gates(root, monkeypatch)
    assert (drift.ok, drift.summary, drift.findings) == (True, "docs match the code", [])
    assert (layers.ok, layers.summary, layers.findings) == (True, go_deps.KEPT, [])
    assert [record["path"] for record in sample_imports(root)] == ["fmt", "os"]
