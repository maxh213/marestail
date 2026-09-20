import json
from pathlib import Path
from typing import Any

import pytest

from marestail import java
from marestail.context import Context
from tests.conftest import make_context

JDK = "install a JDK 21 or newer (java and javac on PATH, JAVA_HOME, or [java] java_home)"
MAVEN = "install Maven 3.9+ (mvn on PATH), commit the Maven wrapper (./mvnw), or set [java] mvn"
NS = "http://maven.apache.org/POM/4.0.0"


def ctx_at(base: Path, **settings: Any) -> Context:
    (base / ".marestail").mkdir(exist_ok=True)
    return make_context(base, {"java": settings})


def write(path: Path, text: str = "class X {}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def flag(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def option(command: list[str], prefix: str) -> str:
    return next(part for part in command if part.startswith(prefix)).split("=", 1)[1]


@pytest.mark.parametrize(("value", "expected"), [(None, []), ([1, "a"], ["1", "a"]), (3, ["3"]), ("mvn", ["mvn"])])
def test_listify(value: Any, expected: list[str]) -> None:
    assert java.listify(value) == expected


def test_rel(tmp_path: Path) -> None:
    ctx = ctx_at(tmp_path)
    assert java.rel(ctx, "a/b.java") == "a/b.java"
    assert java.rel(ctx, tmp_path / "c" / "d.java") == "c/d.java"
    assert java.rel(ctx, Path("/elsewhere/x.java")) == "/elsewhere/x.java"


@pytest.mark.parametrize(("root", "prefix"), [(".", ""), ("svc", "svc/"), ("svc/api", "svc/api/")])
def test_root_prefix(tmp_path: Path, root: str, prefix: str) -> None:
    assert java.root_prefix(ctx_at(tmp_path, root=root)) == prefix


@pytest.mark.parametrize(("owner", "folder"), [("a.b.C", "a/b"), ("C", ""), ("", "")])
def test_package_dir(owner: str, folder: str) -> None:
    assert java.package_dir(owner) == folder


def test_output_tail() -> None:
    assert java.output_tail("  short \n") == "short"
    assert java.output_tail("x" * 10 + "y" * 300 + "\n") == "y" * 300


def test_tool_prefers_configured_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAVA_HOME", "/env/jdk")
    assert java.tool(ctx_at(tmp_path, java_home="/opt/jdk"), "javac") == "/opt/jdk/bin/javac"
    assert java.tool(ctx_at(tmp_path), "java") == "/env/jdk/bin/java"
    monkeypatch.delenv("JAVA_HOME")
    assert java.tool(ctx_at(tmp_path), "java") == "java"


def test_mvn_command(tmp_path: Path) -> None:
    assert java.mvn_command(ctx_at(tmp_path, mvn=["mvn", "-q"])) == ["mvn", "-q"]
    assert java.mvn_command(ctx_at(tmp_path, mvn="/opt/mvn")) == ["/opt/mvn"]
    assert java.mvn_command(ctx_at(tmp_path)) == ["mvn"]
    write(tmp_path / "mvnw", "#!/bin/sh\n")
    assert java.mvn_command(ctx_at(tmp_path)) == [str(tmp_path / "mvnw")]


def test_mvn_runs_in_java_root(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(java, [(0, "built"), (1, "broke")])
    ctx = ctx_at(tmp_path, root="svc")
    assert java.mvn(ctx, ["verify"]) == (0, "built")
    assert java.mvn(ctx, ["test"], timeout=60, pom=Path("/tools/pom.xml")) == (1, "broke")
    assert fake.calls == [["mvn", "-B", "-ntp", "verify"], ["mvn", "-B", "-ntp", "-f", "/tools/pom.xml", "test"]]
    assert fake.options == [{"cwd": tmp_path / "svc", "timeout": 1800}, {"cwd": tmp_path / "svc", "timeout": 60}]


@pytest.mark.parametrize(
    ("code", "output", "hint"),
    [
        (127, "", f"maven unavailable: {MAVEN}"),
        (1, "The JAVA_HOME environment variable is not defined correctly", f"maven cannot find a JDK: {JDK}"),
        (1, "JAVA_HOME is fine", None),
        (1, "not defined correctly", None),
        (0, "", None),
    ],
)
def test_maven_hint(code: int, output: str, hint: str | None) -> None:
    assert java.maven_hint(code, output) == hint


@pytest.mark.parametrize(
    ("code", "exists", "output", "expected"),
    [
        (0, True, "", None),
        (1, True, " boom \n", "cannot: boom"),
        (0, False, "quiet", "cannot: quiet"),
        (127, False, "", f"maven unavailable: {MAVEN}"),
    ],
)
def test_maven_failure(tmp_path: Path, code: int, exists: bool, output: str, expected: str | None) -> None:
    product = tmp_path / "out.txt"
    if exists:
        product.write_text("x")
    assert java.maven_failure(code, output, product, "cannot") == expected


@pytest.mark.parametrize(
    ("code", "exists", "expected"),
    [(0, True, None), (127, True, f"javac not found: {JDK}"), (2, True, "broke: log"), (0, False, "broke: log")],
)
def test_jdk_failure(tmp_path: Path, code: int, exists: bool, expected: str | None) -> None:
    product = tmp_path / "Scan.class"
    if exists:
        product.write_text("x")
    assert java.jdk_failure(code, " log ", product, "javac", "broke") == expected


def test_require_pom(tmp_path: Path) -> None:
    ctx = ctx_at(tmp_path, root="svc")
    assert java.pom(ctx) == tmp_path / "svc" / "pom.xml"
    assert java.require_pom(ctx) == ("no pom.xml in svc; the java gates drive Maven, so set [java] root to the module that holds pom.xml")
    write(tmp_path / "svc" / "pom.xml", "<project/>")
    assert java.require_pom(ctx) is None


def test_build_dir(tmp_path: Path) -> None:
    assert java.build_dir(ctx_at(tmp_path)) == tmp_path / "target"
    assert java.build_dir(ctx_at(tmp_path, root="svc", build_dir="out")) == tmp_path / "svc" / "out"


def pom_with(properties: str) -> str:
    return f'<project xmlns="{NS}"><modelVersion>4.0.0</modelVersion>{properties}</project>'


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", None),
        ("<properties><maven.compiler.release>21</maven.compiler.release></properties>", "21"),
        ("<properties><maven.compiler.release> 17 </maven.compiler.release></properties>", "17"),
        ("<properties><jdk>11</jdk><maven.compiler.release>${jdk}</maven.compiler.release></properties>", "11"),
        ("<properties><maven.compiler.release>${missing}</maven.compiler.release></properties>", None),
        ("<properties><maven.compiler.release>${jdk</maven.compiler.release></properties>", None),
        ("<properties><maven.compiler.release></maven.compiler.release></properties>", None),
        ("<properties><other>21</other></properties>", None),
    ],
)
def test_release_from_pom(tmp_path: Path, body: str, expected: str | None) -> None:
    write(tmp_path / "pom.xml", pom_with(body))
    assert java.release(ctx_at(tmp_path)) == expected


def test_release_configured_or_missing_pom(tmp_path: Path) -> None:
    assert java.release(ctx_at(tmp_path)) is None
    assert java.release(ctx_at(tmp_path, release=17)) == "17"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"k": "21"}, "21"),
        ({"k": "${v}", "v": "8"}, "8"),
        ({"k": "${v"}, "${v"),
        ({"k": "v}"}, "v}"),
        ({}, ""),
    ],
)
def test_resolve_property(values: dict[str, str], expected: str) -> None:
    assert java.resolve_property(values, "k") == expected


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("src/main/java/App.java", False),
        ("target/App.java", True),
        ("src/node_modules/App.java", True),
        ("perf/Bench.java", True),
        ("App.java", False),
        ("src/target.java", False),
    ],
)
def test_skipped(tmp_path: Path, relative: str, expected: bool) -> None:
    assert java.skipped(ctx_at(tmp_path), tmp_path / relative) is expected


def tree(root: Path) -> dict[str, Path]:
    paths = {
        "app": write(root / "src/main/java/app/App.java"),
        "inner": write(root / "src/main/java/app/core/Core.java"),
        "test": write(root / "src/test/java/app/AppTest.java"),
    }
    write(root / "src/main/java/app/target/Gen.java")
    write(root / "src/main/java/app/notes.txt")
    return paths


def test_collect_sources_and_tests(tmp_path: Path) -> None:
    paths = tree(tmp_path)
    ctx = ctx_at(tmp_path)
    assert java.source_roots(ctx) == [tmp_path / "src/main/java"]
    assert java.test_roots(ctx) == [tmp_path / "src/test/java"]
    assert java.sources(ctx) == [paths["app"], paths["inner"]]
    assert java.tests(ctx) == [paths["test"]]
    assert java.files(ctx) == [paths["app"], paths["inner"], paths["test"]]


def test_collect_configured_roots(tmp_path: Path) -> None:
    lib = write(tmp_path / "svc/lib/L.java")
    ctx = ctx_at(tmp_path, root="svc", sources=["lib", "missing"], tests="checks")
    assert java.source_roots(ctx) == [tmp_path / "svc/lib", tmp_path / "svc/missing"]
    assert java.test_roots(ctx) == [tmp_path / "svc/checks"]
    assert java.sources(ctx) == [lib]
    assert java.tests(ctx) == []
    assert java.collect(ctx, [tmp_path / "svc/lib", tmp_path / "svc/lib"]) == [lib]


def test_in_scope(tmp_path: Path) -> None:
    paths = tree(tmp_path)
    everything = [paths["app"], paths["inner"]]
    assert java.in_scope(ctx_at(tmp_path), everything) == everything
    scoped = make_context(tmp_path, {}, scope_changed=True, changed={"src/main/java/app/core/Core.java"})
    assert java.in_scope(scoped, everything) == [paths["inner"]]


def test_class_name_and_locate(tmp_path: Path) -> None:
    paths = tree(tmp_path)
    ctx = ctx_at(tmp_path)
    assert java.class_name(ctx, paths["inner"]) == "app.core.Core"
    assert java.class_name(ctx, paths["test"]) == "app.AppTest"
    assert java.class_name(ctx, tmp_path / "other" / "X.java") is None
    assert java.locate(ctx, "app", "AppTest.java") == paths["test"]
    assert java.locate(ctx, "app", "AppTest.java", java.source_roots(ctx)) is None
    assert java.locate(ctx, "app/core", "Core.java", java.source_roots(ctx)) == paths["inner"]
    assert java.locate(ctx, "app", "core") is None


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("gen/A.java", True),
        ("gen", True),
        ("generated/A.java", False),
        ("src/Main.java", True),
        ("src/Main2.java", False),
        ("x/Dto.java", True),
        ("x/Service.java", False),
    ],
)
def test_coverage_excluded(tmp_path: Path, relative: str, expected: bool) -> None:
    ctx = ctx_at(tmp_path, coverage_exclude=["/gen/", "src/Main.java", "*/Dto.java"])
    assert java.coverage_excluded(ctx, relative) is expected


def test_excluded_under_nested_root(tmp_path: Path) -> None:
    ctx = ctx_at(tmp_path, root="svc", coverage_exclude="gen")
    assert java.coverage_excluded(ctx, "svc/gen/A.java")
    assert not java.coverage_excluded(ctx, "gen/A.java")
    assert not java.coverage_excluded(ctx_at(tmp_path), "gen/A.java")


def test_mutation_excluded(tmp_path: Path) -> None:
    fallback = ctx_at(tmp_path, coverage_exclude=["gen"])
    assert java.mutation_excluded(fallback, "gen/A.java")
    own = ctx_at(tmp_path, coverage_exclude=["gen"], mutation_exclude=["dto"])
    assert not java.mutation_excluded(own, "gen/A.java")
    assert java.mutation_excluded(own, "dto/A.java")
    assert not java.mutation_excluded(ctx_at(tmp_path, coverage_exclude=["gen"], mutation_exclude=[]), "gen/A.java")


def test_load_coverage(tmp_path: Path) -> None:
    ctx = ctx_at(tmp_path)
    assert java.load_coverage(ctx) is None
    (tmp_path / ".marestail" / "java-coverage.json").write_text('{"files": {"a": 1}}')
    assert java.load_coverage(ctx) == {"files": {"a": 1}}


def test_frozen_java_project_files_live_in_the_package() -> None:
    assert java.JVM_DIR == java.PACKAGE / "jvm"
    assert java.TOOLS_POM == java.JVM_DIR / "tools" / "pom.xml"
    assert java.PMD_RULESET == java.JVM_DIR / "pmd-ruleset.xml"
    assert java.SCAN_SOURCE == java.JVM_DIR / "Scan.java"
    assert java.TOOLS_POM.is_file()
    assert java.PMD_RULESET.is_file()
    assert java.SCAN_SOURCE.is_file()


def test_stamp_matches(tmp_path: Path) -> None:
    stamp = tmp_path / "stamp"
    assert not java.stamp_matches(stamp, "abc")
    stamp.write_text("abc")
    assert java.stamp_matches(stamp, "abc")
    assert not java.stamp_matches(stamp, "abd")
    write(tmp_path / "f", "hello")
    assert java.digest_of(tmp_path / "f") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def compile_scanner(command: list[str]) -> tuple[int, str]:
    (Path(flag(command, "-d")) / "Scan.class").write_bytes(b"\xca\xfe")
    return 0, ""


def test_build_scanner_compiles_once(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(java, compile_scanner)
    ctx = ctx_at(tmp_path)
    out = tmp_path / ".marestail" / "java-scan"
    assert java.build_scanner(ctx) is None
    assert java.build_scanner(ctx) is None
    assert fake.calls == [["javac", "--release", "21", "-d", str(out), str(java.SCAN_SOURCE)]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 300}]
    assert (out / "stamp").read_text() == java.digest_of(java.SCAN_SOURCE)


def test_build_scanner_rebuilds_stale_stamp(tmp_path: Path, fake_run: Any) -> None:
    out = tmp_path / ".marestail" / "java-scan"
    write(out / "Scan.class", "old")
    write(out / "stamp", "stale")
    write(out / "Leftover.class", "old")
    fake = fake_run(java, compile_scanner)
    assert java.build_scanner(ctx_at(tmp_path)) is None
    assert len(fake.calls) == 1
    assert not (out / "Leftover.class").exists()


def test_build_scanner_rebuilds_without_class(tmp_path: Path, fake_run: Any) -> None:
    write(tmp_path / ".marestail" / "java-scan" / "stamp", java.digest_of(java.SCAN_SOURCE))
    fake = fake_run(java, compile_scanner)
    assert java.build_scanner(ctx_at(tmp_path)) is None
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((127, ""), f"javac not found: {JDK}"),
        ((1, "error: bad\n"), "java scanner build failed (needs JDK 21+): error: bad"),
        ((0, "ok"), "java scanner build failed (needs JDK 21+): ok"),
    ],
)
def test_build_scanner_failures(tmp_path: Path, fake_run: Any, reply: tuple[int, str], expected: str) -> None:
    fake_run(java, [reply])
    assert java.build_scanner(ctx_at(tmp_path)) == expected
    assert not (tmp_path / ".marestail" / "java-scan" / "stamp").exists()


def ready_scanner(root: Path) -> None:
    write(root / ".marestail" / "java-scan" / "Scan.class", "x")
    write(root / ".marestail" / "java-scan" / "stamp", java.digest_of(java.SCAN_SOURCE))


def scanned(payload: Any) -> Any:
    def reply(command: list[str]) -> tuple[int, str]:
        Path(flag(command, "--out")).write_text(json.dumps(payload))
        return 0, ""

    return reply


@pytest.mark.parametrize(("mode", "empty"), [("deps", {"files": [], "edges": []}), ("complexity", []), ("lint", [])])
def test_scan_nothing(tmp_path: Path, fake_run: Any, mode: str, empty: Any) -> None:
    fake = fake_run(java)
    assert java.scan(ctx_at(tmp_path), mode, []) == (empty, None)
    assert fake.calls == []


def test_scan_runs_scanner(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAVA_HOME", "/jdk")
    ready_scanner(tmp_path)
    work = tmp_path / ".marestail"
    write(work / "java-lint.json", "stale")
    fake = fake_run(java, scanned([{"kind": "ERROR"}]))
    paths = [tmp_path / "A.java", tmp_path / "B.java"]
    assert java.scan(ctx_at(tmp_path), "lint", paths, ["--release", "17"]) == ([{"kind": "ERROR"}], None)
    assert fake.calls == [
        [
            "/jdk/bin/java",
            "-cp",
            str(work / "java-scan"),
            "Scan",
            "lint",
            "--root",
            str(tmp_path),
            "--out",
            str(work / "java-lint.json"),
            "--release",
            "17",
            f"@{work / 'java-lint.txt'}",
        ]
    ]
    assert fake.options == [{"cwd": tmp_path, "timeout": 1800}]
    assert (work / "java-lint.txt").read_text() == f"{paths[0]}\n{paths[1]}\n"


def test_scan_without_extra(tmp_path: Path, fake_run: Any) -> None:
    ready_scanner(tmp_path)
    fake = fake_run(java, scanned({"files": [], "edges": [1]}))
    assert java.scan(ctx_at(tmp_path), "deps", [tmp_path / "A.java"]) == ({"files": [], "edges": [1]}, None)
    assert fake.calls[0][-2:] == [str(tmp_path / ".marestail" / "java-deps.json"), f"@{tmp_path / '.marestail' / 'java-deps.txt'}"]


def test_scan_reports_build_error(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(java, [(127, "")])
    assert java.scan(ctx_at(tmp_path), "deps", [tmp_path / "A.java"]) == (None, f"javac not found: {JDK}")
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((127, ""), f"java not found: {JDK}"),
        ((1, "Exception\n"), "java scanner failed (dead): Exception"),
        ((0, ""), "java scanner failed (dead): "),
    ],
)
def test_scan_failures(tmp_path: Path, fake_run: Any, reply: tuple[int, str], expected: str) -> None:
    ready_scanner(tmp_path)
    write(tmp_path / ".marestail" / "java-dead.json", "stale")
    fake_run(java, [reply])
    assert java.scan(ctx_at(tmp_path), "dead", [tmp_path / "A.java"]) == (None, expected)


def classpath_reply(text: str) -> Any:
    def reply(command: list[str]) -> tuple[int, str]:
        Path(option(command, "-Dmdep.outputFile=")).write_text(text)
        return 0, ""

    return reply


def test_classpath(tmp_path: Path, fake_run: Any) -> None:
    out = tmp_path / ".marestail" / "java-classpath.txt"
    fake = fake_run(java, classpath_reply("/m2/a.jar:/m2/b.jar"))
    assert java.classpath(ctx_at(tmp_path)) == (out, None)
    assert fake.calls == [
        ["mvn", "-B", "-ntp", f"{java.DEPENDENCY_PLUGIN}:build-classpath", f"-Dmdep.outputFile={out}", "-Dmdep.includeScope=test"]
    ]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((127, ""), f"maven unavailable: {MAVEN}"),
        ((1, "BUILD FAILURE\n"), "maven could not resolve the test classpath: BUILD FAILURE"),
    ],
)
def test_classpath_failures(tmp_path: Path, fake_run: Any, reply: tuple[int, str], expected: str) -> None:
    write(tmp_path / ".marestail" / "java-classpath.txt", "stale")
    fake_run(java, [reply])
    assert java.classpath(ctx_at(tmp_path)) == (None, expected)


def test_pmd_classpath_fetches_then_caches(tmp_path: Path, fake_run: Any) -> None:
    folder = tmp_path / ".marestail" / "java-tools"
    fake = fake_run(java, classpath_reply(" /m2/pmd.jar \n"))
    ctx = ctx_at(tmp_path)
    assert java.pmd_classpath(ctx) == ("/m2/pmd.jar", None)
    assert java.pmd_classpath(ctx) == ("/m2/pmd.jar", None)
    assert fake.calls == [
        [
            "mvn",
            "-B",
            "-ntp",
            "-f",
            str(java.TOOLS_POM),
            f"{java.DEPENDENCY_PLUGIN}:build-classpath",
            f"-Dmdep.outputFile={folder / 'pmd.classpath'}",
        ]
    ]
    assert (folder / "stamp").read_text() == java.digest_of(java.TOOLS_POM)


def test_pmd_classpath_refetches_stale(tmp_path: Path, fake_run: Any) -> None:
    folder = tmp_path / ".marestail" / "java-tools"
    write(folder / "pmd.classpath", "/old.jar")
    write(folder / "stamp", "old")
    fake = fake_run(java, classpath_reply("/new.jar"))
    assert java.pmd_classpath(ctx_at(tmp_path)) == ("/new.jar", None)
    assert len(fake.calls) == 1


def test_pmd_classpath_failure(tmp_path: Path, fake_run: Any) -> None:
    fake_run(java, [(1, "offline\n")])
    assert java.pmd_classpath(ctx_at(tmp_path)) == (None, "maven could not fetch PMD: offline")
    assert not (tmp_path / ".marestail" / "java-tools" / "stamp").exists()
