import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from marestail import rust
from tests.conftest import make_context

INSTALLED = {"llvm-cov": "/usr/bin/llvm-cov", "llvm-profdata": "/usr/bin/llvm-profdata"}


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def which_from(found: dict[str, str]) -> Any:
    return lambda tool: found.get(tool)


@pytest.mark.parametrize(("value", "expected"), [(None, []), (["a", 1], ["a", "1"]), ("x", ["x"])])
def test_listify(value: Any, expected: list[str]) -> None:
    assert rust.listify(value) == expected


def test_rel(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"rust": {"root": "crate"}})
    assert rust.rel(ctx, "src/lib.rs") == "crate/src/lib.rs"
    assert rust.rel(ctx, tmp_path / "other" / "a.rs") == "other/a.rs"
    assert rust.rel(ctx, "/nowhere/a.rs") == "/nowhere/a.rs"
    assert rust.rel(ctx, Path("/nowhere/b.rs")) == "/nowhere/b.rs"


def test_env_prefers_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", which_from(INSTALLED))
    ctx = make_context(tmp_path, {"rust": {"llvm_cov": "/llvm/cov", "llvm_profdata": "/llvm/profdata"}})
    assert rust.env(ctx) == {"LLVM_COV": "/llvm/cov", "LLVM_PROFDATA": "/llvm/profdata"}


def test_env_uses_system_llvm_without_rustup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLVM_COV", raising=False)
    monkeypatch.setenv("LLVM_PROFDATA", "/set")
    monkeypatch.setattr(shutil, "which", which_from(INSTALLED))
    assert rust.env(make_context(tmp_path)) == {"LLVM_COV": "/usr/bin/llvm-cov"}


@pytest.mark.parametrize("found", [{**INSTALLED, "rustup": "/usr/bin/rustup"}, {}])
def test_env_leaves_rustup_or_missing_tools_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, found: dict[str, str]) -> None:
    monkeypatch.delenv("LLVM_COV", raising=False)
    monkeypatch.delenv("LLVM_PROFDATA", raising=False)
    monkeypatch.setattr(shutil, "which", which_from(found))
    assert rust.env(make_context(tmp_path)) == {}


def test_cargo_runs_in_rust_root(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", which_from({}))
    fake = fake_run(rust, [(0, "ok"), (1, "bad")])
    ctx = make_context(tmp_path, {"rust": {"root": "crate", "cargo": ["cross", "+nightly"], "llvm_cov": "/c"}})
    assert rust.cargo(ctx, ["test"], timeout=1800) == (0, "ok")
    assert rust.cargo(ctx, ["fmt"], timeout=5, cwd=tmp_path) == (1, "bad")
    assert fake.calls == [["cross", "+nightly", "test"], ["cross", "+nightly", "fmt"]]
    assert fake.options == [
        {"cwd": tmp_path / "crate", "env": {"LLVM_COV": "/c"}, "timeout": 1800},
        {"cwd": tmp_path, "env": {"LLVM_COV": "/c"}, "timeout": 5},
    ]


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (127, "", f"cargo is not installed: {rust.INSTALL['cargo']}"),
        (101, "error: No such command: `mutants`", f"cargo mutants is not installed: {rust.INSTALL['mutants']}"),
        (1, "error: 'cargo-clippy' is not installed for the toolchain", f"cargo clippy is not installed: {rust.INSTALL['clippy']}"),
        (1, "test failed", None),
        (0, "", None),
    ],
)
def test_missing(code: int, output: str, expected: str | None) -> None:
    tool = "mutants" if "mutants" in output else "clippy"
    assert rust.missing(code, output, tool) == expected


def test_missing_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^missing$"):
        rust.missing(None, "", "clippy")  # type: ignore[arg-type]


def test_require_missing_keeps_valid_args() -> None:
    rust.require_missing(0, "clippy")


def test_skipped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert rust.skipped(ctx, tmp_path / "target" / "a.rs")
    assert rust.skipped(ctx, tmp_path / "perf" / "bench.rs")
    assert not rust.skipped(ctx, tmp_path / "src" / "lib.rs")


def make_tree(root: Path) -> None:
    for name in ("src/lib.rs", "src/gen/out.rs", "src/skip.rs", "src/notes.txt", "src/target/x.rs", "extra/a.rs", "perf/b.rs"):
        touch(root / name)


def test_sources_default_and_exclusions(tmp_path: Path) -> None:
    make_tree(tmp_path)
    ctx = make_context(tmp_path, {"rust": {"source_exclude": ["src/gen/", "src/skip.rs"]}})
    assert rust.sources(ctx) == [tmp_path / "src" / "lib.rs"]


def test_sources_patterns(tmp_path: Path) -> None:
    make_tree(tmp_path)
    ctx = make_context(tmp_path, {"rust": {"sources": ["src", "ext*", "perf"]}})
    names = [path.relative_to(tmp_path).as_posix() for path in rust.sources(ctx)]
    assert names == ["extra/a.rs", "src/gen/out.rs", "src/lib.rs", "src/skip.rs"]


def test_use_files(tmp_path: Path) -> None:
    touch(tmp_path / "Cargo.toml")
    touch(tmp_path / "sub" / "Cargo.toml")
    touch(tmp_path / "target" / "Cargo.toml")
    for name in ("tests/a.rs", "examples/b.rs", "benches/c.rs", "src/d.rs", "sub/tests/e.rs", "target/tests/f.rs", "tests/g.txt"):
        touch(tmp_path / name)
    names = [path.relative_to(tmp_path).as_posix() for path in rust.use_files(make_context(tmp_path))]
    assert names == ["benches/c.rs", "examples/b.rs", "sub/tests/e.rs", "tests/a.rs"]


def test_in_scope(tmp_path: Path) -> None:
    paths = [tmp_path / "src" / "a.rs", tmp_path / "src" / "b.rs"]
    assert rust.in_scope(make_context(tmp_path, changed={"src/b.rs"}), paths) == paths
    scoped = make_context(tmp_path, scope_changed=True, changed={"src/b.rs"})
    assert rust.in_scope(scoped, paths) == [paths[1]]


@pytest.mark.parametrize(
    ("root", "relative", "expected"),
    [
        (".", "src/gen/a.rs", True),
        (".", "src/gen", True),
        (".", "src/generated.rs", False),
        (".", "src/x_test.rs", True),
        ("crate", "crate/src/gen/a.rs", True),
        ("crate", "src/gen/a.rs", False),
        ("crate", "crate/src/lib.rs", False),
    ],
)
def test_excluded(tmp_path: Path, root: str, relative: str, expected: bool) -> None:
    ctx = make_context(tmp_path, {"rust": {"root": root, "skip": ["/src/gen/", "src/*_test.rs"]}})
    assert rust.excluded(ctx, relative, "skip") is expected


def test_configured_list() -> None:
    assert rust.configured_list([]) == []
    assert rust.configured_list(["a", 2]) == ["a", "2"]
    assert rust.configured_list("ab") == ["ab"]
    with pytest.raises(TypeError, match=r"^list$"):
        rust.configured_list(None)


def test_require_timeout_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^timeout$"):
        rust.require_timeout(None)  # type: ignore[arg-type]
    assert rust.require_timeout(1800) == 1800


def test_cargo_requires_timeout(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rust, [(0, "")])
    ctx = make_context(tmp_path)
    with pytest.raises(TypeError):
        rust.cargo(ctx, ["test"])  # type: ignore[call-arg]


def test_crates_finds_manifests(tmp_path: Path) -> None:
    touch(tmp_path / "Cargo.toml")
    touch(tmp_path / "sub" / "Cargo.toml")
    touch(tmp_path / "target" / "Cargo.toml")
    assert rust.crates(make_context(tmp_path)) == {tmp_path, tmp_path / "sub"}


def test_staged_crate_rejects_a_missing_ctx() -> None:
    with pytest.raises(TypeError, match=r"^ctx$"):
        rust.staged_crate(None)  # type: ignore[arg-type]


def test_staged_crate_creates_nested_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan = tmp_path / "scan"
    touch(scan / "main.rs", "fn main() {}")
    touch(scan / "Cargo.lock", "")
    monkeypatch.setattr(rust, "SCAN_DIR", scan)
    monkeypatch.setattr(rust, "SCAN_MANIFEST", tmp_path / "frozen" / "Cargo.toml")
    touch(tmp_path / "frozen" / "Cargo.toml", "[package]\n")
    ctx = make_context(tmp_path)
    assert not ctx.work.exists()
    first = rust.staged_crate(ctx)
    again = rust.staged_crate(ctx)
    assert first == again
    assert (first / "main.rs").read_text() == "fn main() {}"


def binary(root: Path) -> Path:
    return root / ".marestail" / rust.SCAN_BIN


def stamp(root: Path) -> Path:
    return root / ".marestail" / "rs-scan" / "stamp"


def test_build_scanner_reuses_fresh_binary(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust)
    touch(binary(tmp_path))
    touch(stamp(tmp_path), rust.scanner_digest())
    assert rust.build_scanner(make_context(tmp_path)) is None
    assert fake.calls == []


def test_build_scanner_builds_and_stamps(tmp_path: Path, fake_run: Any) -> None:
    touch(binary(tmp_path))
    touch(stamp(tmp_path), "old")
    fake = fake_run(rust, [(0, "")])
    ctx = make_context(tmp_path, {"rust": {"cargo": "cargo1"}})
    assert rust.build_scanner(ctx) is None
    manifest = str(rust.SCAN_DIR / rust.CARGO_TOML)
    assert fake.calls == [["cargo1", "build", "--release", "--locked", "--quiet", "--manifest-path", manifest]]
    assert fake.options == [{"cwd": tmp_path, "env": {"CARGO_TARGET_DIR": str(tmp_path / ".marestail" / "rs-scan")}, "timeout": 900}]
    assert stamp(tmp_path).read_text() == rust.scanner_digest()


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((127, ""), f"cargo is not installed: {rust.INSTALL['cargo']}"),
        ((101, "x" * 400 + " boom  \n"), "rust scanner build failed: " + "x" * 295 + " boom"),
        ((0, "built"), "rust scanner build failed: built"),
    ],
)
def test_build_scanner_failures(tmp_path: Path, fake_run: Any, reply: tuple[int, str], expected: str) -> None:
    fake_run(rust, [reply])
    assert rust.build_scanner(make_context(tmp_path)) == expected
    assert not stamp(tmp_path).exists()


SCANNER_SOURCES = ("main.rs", "Cargo.toml", "Cargo.lock")


def scanner_folder() -> Path:
    return Path(rust.__file__).resolve().parent / "rs" / "scan"


def test_scanner_digest_is_sha256_of_the_three_frozen_sources_in_order() -> None:
    payload = b"".join((scanner_folder() / name).read_bytes() for name in SCANNER_SOURCES)
    assert rust.scanner_digest() == hashlib.sha256(payload).hexdigest()


def test_scan_input_uses_the_frozen_manifest() -> None:
    assert rust.scan_input("main.rs") == scanner_folder() / "main.rs"
    assert rust.scan_input("Cargo.lock") == scanner_folder() / "Cargo.lock"
    assert rust.scan_input("Cargo.toml") == rust.SCAN_MANIFEST
    assert (scanner_folder() / "main.rs").is_file()


def test_scan_input_prefers_a_bundled_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / rust.CARGO_TOML
    bundled.write_text("[package]\nname = 'x'\n")
    monkeypatch.setattr(rust, "SCAN_DIR", tmp_path)
    assert rust.scan_input(rust.CARGO_TOML) == bundled


def test_scan_input_keeps_missing_non_manifest_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust, "SCAN_DIR", tmp_path)
    assert rust.scan_input("main.rs") == tmp_path / "main.rs"


def test_staged_crate_reuses_a_bundled_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust, "SCAN_DIR", tmp_path)
    (tmp_path / rust.CARGO_TOML).write_text("[package]\n")
    assert rust.staged_crate(make_context(tmp_path)) == tmp_path


def test_staged_crate_copies_when_manifest_is_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "main.rs").write_text("fn main() {}")
    (scan / "Cargo.lock").write_text("")
    monkeypatch.setattr(rust, "SCAN_DIR", scan)
    ctx = make_context(tmp_path)
    ctx.work.mkdir(exist_ok=True)
    stage = rust.staged_crate(ctx)
    assert stage == ctx.work / rust.STAGE
    assert (stage / rust.CARGO_TOML).is_file()
    assert (stage / "main.rs").read_text() == "fn main() {}"


def fresh_scanner(root: Path) -> None:
    touch(binary(root))
    touch(stamp(root), rust.scanner_digest())


def test_scan_without_paths(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust)
    assert rust.scan(make_context(tmp_path), "deps", []) == ([], None)
    assert fake.calls == []


def test_scan_rejects_missing_context() -> None:
    with pytest.raises(TypeError, match=r"^ctx$"):
        rust.scan(None, "deps", [])  # type: ignore[arg-type]


def test_scan_reports_build_error(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rust, [(127, "")])
    assert rust.scan(make_context(tmp_path), "deps", [tmp_path / "a.rs"]) == (None, f"cargo is not installed: {rust.INSTALL['cargo']}")


def test_scan_runs_scanner(tmp_path: Path, fake_run: Any) -> None:
    fresh_scanner(tmp_path)
    fake = fake_run(rust, [(0, json.dumps([{"name": "f"}])), (0, "")])
    ctx = make_context(tmp_path)
    paths = [tmp_path / "a.rs"]
    uses = [tmp_path / "tests" / "t.rs"]
    assert rust.scan(ctx, "deadcode", paths, extra=["--root", "r"], uses=uses) == ([{"name": "f"}], None)
    assert rust.scan(ctx, "complexity", paths) == ([], None)
    scanner = str(binary(tmp_path))
    assert fake.calls == [
        [scanner, "deadcode", "--root", "r", str(paths[0]), "--uses", str(uses[0])],
        [scanner, "complexity", str(paths[0])],
    ]
    assert fake.options[0] == {"cwd": tmp_path, "timeout": 600}


def test_scan_reports_scanner_failure(tmp_path: Path, fake_run: Any) -> None:
    fresh_scanner(tmp_path)
    fake_run(rust, [(2, "  panic: bad input \n")])
    assert rust.scan(make_context(tmp_path), "deps", [tmp_path / "a.rs"]) == (None, "rust scanner failed (deps): panic: bad input")


def test_load_coverage(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert rust.load_coverage(ctx) is None
    touch(tmp_path / ".marestail" / "rs-coverage.json", '{"files": {}}')
    assert rust.load_coverage(ctx) == {"files": {}}
