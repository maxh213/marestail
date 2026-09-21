import fnmatch
import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from marestail.context import Context, live, under_benchmarks
from marestail.shell import ensure_dir, run

PACKAGE = Path(__file__).resolve().parent
JVM_DIR = PACKAGE / "jvm"
SCAN_SOURCE = JVM_DIR / "Scan.java"
TOOLS_POM = JVM_DIR / "tools" / "pom.xml"
PMD_RULESET = JVM_DIR / "pmd-ruleset.xml"
SCAN_RELEASE = "21"
DEPENDENCY_PLUGIN = "org.apache.maven.plugins:maven-dependency-plugin:3.8.1"
BUILD_CLASSPATH = f"{DEPENDENCY_PLUGIN}:build-classpath"
SKIP_DIRS = {"target", "build", ".marestail", ".git", ".mvn", ".gradle", ".idea", "node_modules"}
INSTALL = {
    "jdk": "install a JDK 21 or newer (java and javac on PATH, JAVA_HOME, or [java] java_home)",
    "maven": "install Maven 3.9+ (mvn on PATH), commit the Maven wrapper (./mvnw), or set [java] mvn",
}
MISSING_TOOL = 127
STAMP = "stamp"
NS_CLOSE = "}"
EMPTY = ""
OUTPUT_TAIL = 300
MISSING = -1
EMPTY_PATTERNS: list[str] = []
SLASH = "/"


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def configured_list(value: Any) -> list[str]:
    if value is None:
        raise TypeError("list")
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def rel(ctx: Context, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.root / candidate
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def root_prefix(ctx: Context) -> str:
    prefix = rel(ctx, ctx.java_root())
    return "" if prefix == "." else prefix + "/"


def package_dir(owner: str) -> str:
    return "/".join(owner.split(".")[:-1])


def output_tail(output: str) -> str:
    return output.strip()[-OUTPUT_TAIL:]


def tool(ctx: Context, name: str) -> str:
    home = ctx.java("java_home") or os.environ.get("JAVA_HOME")
    return str(Path(str(home)) / "bin" / name) if home else name


def mvn_command(ctx: Context) -> list[str]:
    configured = listify(ctx.java("mvn"))
    if configured:
        return configured
    wrapper = ctx.java_root() / "mvnw"
    return [str(wrapper)] if wrapper.exists() else ["mvn"]


def mvn(ctx: Context, args: list[str], timeout: int = 1800, pom: Path | None = None) -> tuple[int, str]:
    target = ["-f", str(pom)] if pom else []
    return run([*mvn_command(ctx), "-B", "-ntp", *target, *args], cwd=ctx.java_root(), timeout=timeout)


def maven_hint(code: int, output: str) -> str | None:
    if code == MISSING_TOOL:
        return f"maven unavailable: {INSTALL['maven']}"
    if "JAVA_HOME" in output and "not defined correctly" in output:
        return f"maven cannot find a JDK: {INSTALL['jdk']}"
    return None


def maven_failure(code: int, output: str, product: Path, failed: str) -> str | None:
    if code != 0 or not product.exists():
        return maven_hint(code, output) or f"{failed}: {output_tail(output)}"
    return None


def jdk_failure(code: int, output: str, product: Path, program: str, failed: str) -> str | None:
    if code == MISSING_TOOL:
        return f"{program} not found: {INSTALL['jdk']}"
    if code != 0 or not product.exists():
        return f"{failed}: {output_tail(output)}"
    return None


def pom(ctx: Context) -> Path:
    return ctx.java_root() / "pom.xml"


def require_pom(ctx: Context) -> str | None:
    if pom(ctx).exists():
        return None
    return f"no pom.xml in {rel(ctx, ctx.java_root())}; the java gates drive Maven, so set [java] root to the module that holds pom.xml"


def build_dir(ctx: Context) -> Path:
    return ctx.java_root() / str(ctx.java("build_dir", "target"))


def release(ctx: Context) -> str | None:
    configured = ctx.java("release")
    if configured:
        return str(configured)
    return pom_release(pom(ctx)) if pom(ctx).exists() else None


def pom_release(path: Path) -> str | None:
    properties = ET.parse(path).getroot().find("{*}properties")
    if properties is None:
        return None
    value = resolve_property(pom_properties(properties), "maven.compiler.release")
    return value if value.isdigit() else None


def pom_properties(properties: ET.Element) -> dict[str, str]:
    return {local_tag(element.tag): (element.text or EMPTY).strip() for element in properties}


def local_tag(tag: str) -> str:
    prefix, sep, name = tag.partition(NS_CLOSE)
    return name if sep else prefix


def resolve_property(values: dict[str, str], key: str) -> str:
    value = lookup(values, key)
    if value.startswith("${") and value.endswith("}"):
        return lookup(values, value[2:-1])
    return value


def lookup(values: dict[str, str], key: str) -> str:
    if key not in values:
        return EMPTY
    return values[key]


def skipped(ctx: Context, path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts[:-1]) or under_benchmarks(ctx.root, path)


def source_roots(ctx: Context) -> list[Path]:
    return [ctx.java_root() / folder for folder in listify(ctx.java("sources", ["src/main/java"]))]


def test_roots(ctx: Context) -> list[Path]:
    return [ctx.java_root() / folder for folder in listify(ctx.java("tests", ["src/test/java"]))]


def collect(ctx: Context, folders: list[Path]) -> list[Path]:
    return sorted({path for folder in folders for path in java_files_in(ctx, folder)})


def java_files_in(ctx: Context, folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [path for path in folder.rglob("*.java") if not skipped(ctx, path)]


def sources(ctx: Context) -> list[Path]:
    ctx = live(ctx)
    return collect(ctx, source_roots(ctx))


def tests(ctx: Context) -> list[Path]:
    return collect(ctx, test_roots(ctx))


def files(ctx: Context) -> list[Path]:
    return sorted(set(sources(ctx)) | set(tests(ctx)))


def in_scope(ctx: Context, paths: list[Path]) -> list[Path]:
    if not ctx.scoped:
        return paths
    return [path for path in paths if ctx.in_scope(rel(ctx, path))]


def class_name(ctx: Context, path: Path) -> str | None:
    for folder in source_roots(ctx) + test_roots(ctx):
        if path.is_relative_to(folder):
            return ".".join(path.relative_to(folder).with_suffix("").parts)
    return None


def locate(ctx: Context, package: str, file_name: str, folders: list[Path] | None = None) -> Path | None:
    for folder in folders if folders is not None else source_roots(ctx) + test_roots(ctx):
        candidate = folder / package / file_name
        if candidate.is_file():
            return candidate
    return None


def excluded(ctx: Context, relative: str, key: str) -> bool:
    prefix = root_prefix(ctx)
    return any(matches(relative, prefix + trim_slash(pattern)) for pattern in configured_list(ctx.java(key, EMPTY_PATTERNS)))


def trim_slash(pattern: str) -> str:
    while pattern.startswith(SLASH):
        pattern = pattern[1:]
    while pattern.endswith(SLASH):
        pattern = pattern[:-1]
    return pattern


def matches(relative: str, pattern: str) -> bool:
    return relative == pattern or relative.startswith(pattern + "/") or fnmatch.fnmatch(relative, pattern)


def coverage_excluded(ctx: Context, relative: str) -> bool:
    return excluded(ctx, relative, "coverage_exclude")


def mutation_excluded(ctx: Context, relative: str) -> bool:
    key = "mutation_exclude" if ctx.java("mutation_exclude") is not None else "coverage_exclude"
    return excluded(ctx, relative, key)


def load_coverage(ctx: Context) -> dict[str, Any] | None:
    path = ctx.work / "java-coverage.json"
    return json.loads(path.read_text()) if path.exists() else None


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp_matches(stamp: Path, digest: str) -> bool:
    return stamp.exists() and stamp.read_text() == digest


def build_scanner(ctx: Context) -> str | None:
    out = ctx.work / "java-scan"
    digest = digest_of(SCAN_SOURCE)
    if (out / "Scan.class").exists() and stamp_matches(out / STAMP, digest):
        return None
    shutil.rmtree(out, ignore_errors=True)
    ensure_dir(out)
    code, output = run([tool(ctx, "javac"), "--release", SCAN_RELEASE, "-d", str(out), str(SCAN_SOURCE)], cwd=ctx.root, timeout=300)
    error = jdk_failure(code, output, out / "Scan.class", "javac", f"java scanner build failed (needs JDK {SCAN_RELEASE}+)")
    if error is None:
        (out / STAMP).write_text(digest)
    return error


def empty_scan(mode: str) -> list[Any] | dict[str, Any]:
    return {"files": [], "edges": []} if mode == "deps" else []


def scan(ctx: Context, mode: str, paths: list[Path], extra: list[str] | None = None) -> tuple[Any, str | None]:
    ctx = live(ctx)
    if not paths:
        return empty_scan(mode), None
    error = build_scanner(ctx)
    if error:
        return None, error
    return run_scanner(ctx, mode, paths, extra or [])


def run_scanner(ctx: Context, mode: str, paths: list[Path], extra: list[str]) -> tuple[Any, str | None]:
    listing = ctx.work / f"java-{mode}.txt"
    listing.write_text("".join(f"{path}\n" for path in paths))
    out = ctx.work / f"java-{mode}.json"
    out.unlink(missing_ok=True)
    command = [tool(ctx, "java"), "-cp", str(ctx.work / "java-scan"), "Scan", mode, "--root", str(ctx.root), "--out", str(out)]
    code, output = run([*command, *extra, f"@{listing}"], cwd=ctx.root, timeout=1800)
    error = jdk_failure(code, output, out, "java", f"java scanner failed ({mode})")
    if error:
        return None, error
    return json.loads(out.read_text()), None


def classpath(ctx: Context) -> tuple[Path | None, str | None]:
    out = ctx.work / "java-classpath.txt"
    out.unlink(missing_ok=True)
    code, output = mvn(ctx, [BUILD_CLASSPATH, f"-Dmdep.outputFile={out}", "-Dmdep.includeScope=test"])
    error = maven_failure(code, output, out, "maven could not resolve the test classpath")
    return (None, error) if error else (out, None)


def pmd_classpath(ctx: Context) -> tuple[str | None, str | None]:
    folder = ctx.work / "java-tools"
    out, stamp = folder / "pmd.classpath", folder / STAMP
    digest = digest_of(TOOLS_POM)
    if out.exists() and stamp_matches(stamp, digest):
        return out.read_text().strip(), None
    ensure_dir(folder)
    out.unlink(missing_ok=True)
    code, output = mvn(ctx, [BUILD_CLASSPATH, f"-Dmdep.outputFile={out}"], pom=TOOLS_POM)
    error = maven_failure(code, output, out, "maven could not fetch PMD")
    if error:
        return None, error
    stamp.write_text(digest)
    return out.read_text().strip(), None
