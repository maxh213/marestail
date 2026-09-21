import json
import re
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

from marestail.context import Context
from marestail.gates._coverage import finding_file
from marestail.report import Result, elapsed
from marestail.shell import run

VULTURE_HEAD = re.compile(r":(\d+): (unused \w+|unreachable code) (.+)")
VULTURE_CONFIDENCE = re.compile(r"\d+% confidence\)")
PYTHON_KINDS = ["unused function", "unused method", "unused class", "unused import", "unused property", "unreachable code"]

PYTHON_DECORATORS = [
    "@*.route",
    "@*.before_request",
    "@*.after_request",
    "@*.errorhandler",
    "@*.teardown_appcontext",
    "@*.cli.command",
    "@*.command",
]
PYTHON_EXCLUDES = ["*/tests/*", "*/test/*", "*/mutants/*", "*/.venv/*", "*/__pycache__/*", "perf/*"]
TS_KINDS = ["files", "exports", "types"]
ELIXIR_FAILED = "elixir dead code analysis failed"
ERLANG_FAILED = "erlang dead code analysis failed"
MISSING_CONFIDENCE = ""
COLON = ":"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    findings = collected(ctx)
    summary = "nothing unreachable" if not findings else f"{len(findings)} dead definitions"
    return Result("deadcode", not findings, summary, findings, elapsed(started))


def collected(ctx: Context) -> list[str]:
    return [finding for scanner in SCANNERS for finding in scanner(ctx) if ctx.in_scope(finding_file(finding))]


def failed(label: str, output: str) -> str:
    return f"{label}: {output.strip()[-200:]}"


def python_findings(ctx: Context) -> list[str]:
    if ctx.config.section("python") is None:
        return []
    root = ctx.python_root()
    kinds = set(ctx.config.get("deadcode", "python_kinds", PYTHON_KINDS))
    code, output = run(vulture_command(ctx), cwd=root, timeout=600)
    if code == 127:
        return ["vulture is not installed: uv pip install --python .venv/bin/python vulture"]
    return vetted(code, output, vulture_findings(output, kinds, root, ctx))


def vetted(code: int, output: str, findings: list[str]) -> list[str]:
    if code not in (0, 3) and not findings:
        return [failed("vulture failed", output)]
    return findings


def vulture_findings(output: str, kinds: set[str], root: Path, ctx: Context) -> list[str]:
    return [vulture_finding(entry, root, ctx) for entry in vulture_entries(output) if entry[2] in kinds]


def vulture_entries(output: str) -> list[tuple[str, str, str, str]]:
    return [entry for entry in (vulture_entry(line.strip()) for line in output.splitlines()) if entry]


def vulture_entry(line: str) -> tuple[str, str, str, str] | None:
    body = without_confidence(line)
    match = next(filter(None, (VULTURE_HEAD.fullmatch(body, at) for at in colons_from_left(body))), None)
    return None if match is None else (body[: match.start()], match.group(1), match.group(2), match.group(3))


def without_confidence(line: str) -> str:
    body, _, tail = line.rpartition(" (")
    return body if VULTURE_CONFIDENCE.fullmatch(tail) else MISSING_CONFIDENCE


def colons_from_left(text: str) -> list[int]:
    return [at for at, char in enumerate(text) if at > 0 and char == COLON]


def vulture_finding(entry: tuple[str, str, str, str], root: Path, ctx: Context) -> str:
    path_text, line, kind, name = entry
    path = (root / path_text).resolve().relative_to(ctx.root)
    return f"{path}:{line} {kind} {name}"


def vulture_command(ctx: Context) -> list[str]:
    get = ctx.config.get
    return [
        ctx.python_bin("vulture"),
        *ctx.python("sources", ["."]),
        "--min-confidence",
        str(get("deadcode", "min_confidence", 60)),
        "--exclude",
        ",".join(get("deadcode", "python_exclude", PYTHON_EXCLUDES)),
        "--ignore-decorators",
        ",".join(get("deadcode", "python_decorators", PYTHON_DECORATORS)),
        *ignore_names(get("deadcode", "python_ignore_names")),
    ]


def ignore_names(names: Any) -> list[str]:
    return ["--ignore-names", ",".join(names)] if names else []


def ts_findings(ctx: Context) -> list[str]:
    if ctx.config.section("ts") is None:
        return []
    ts_root = ctx.ts_root()
    kinds = ctx.config.get("deadcode", "ts_kinds", TS_KINDS)
    _, output = run(["npx", "--yes", "knip", "--reporter", "json", "--no-progress"], cwd=ts_root, timeout=900)
    start = output.find("{")
    if start < 0:
        return [failed("knip produced no report", output)]
    report, _ = json.JSONDecoder().raw_decode(output[start:])
    return knip_findings(report, kinds, ts_root.relative_to(ctx.root))


def report_items(report: dict[str, Any], key: str) -> list[Any]:
    value = report.get(key)
    return [] if value is None else list(value)


def knip_findings(report: dict[str, Any], kinds: list[str], prefix: Path) -> list[str]:
    return unused_files(report, kinds, prefix) + [
        finding for issue in report_items(report, "issues") for finding in issue_findings(issue, kinds, prefix)
    ]


def unused_files(report: dict[str, Any], kinds: list[str], prefix: Path) -> list[str]:
    return [f"{prefix / file} unused file" for file in report_items(report, "files") if "files" in kinds]


def issue_findings(issue: dict[str, Any], kinds: list[str], prefix: Path) -> list[str]:
    file = prefix / issue.get("file", "")
    return [describe(file, kind, item) for kind in kinds if kind != "files" for item in report_items(issue, kind)]


def unused_kind(kind: str) -> str:
    return kind[:-1] if kind.endswith("s") else kind


def describe(file: Path, kind: str, item: Any) -> str:
    name = item.get("name", "") if isinstance(item, dict) else str(item)
    line = item.get("line", 0) if isinstance(item, dict) else 0
    return f"{file}:{line} unused {unused_kind(kind)} '{name}'"


def ruby_findings(ctx: Context) -> list[str]:
    if ctx.config.section("ruby") is None:
        return []
    from marestail.ruby import scan, sources

    files = sources(ctx)
    if not files:
        return []
    code, output = scan(ctx, "dead", files)
    return ruby_report(code, output)


def ruby_report(code: int, output: str) -> list[str]:
    if code != 0:
        return [failed("ruby deadcode scanner failed", output)]
    return list(json.loads(output or "[]"))


def structured(ctx: Context, module: ModuleType, failure: str, relabel: Callable[[Any], str] = str, **options: Any) -> list[str]:
    files = module.sources(ctx)
    if not files:
        return []
    data, error = module.scan(ctx, "dead", files, **options)
    if error:
        return [f"{failure}: {error}"]
    return [f"{relabel(e['file'])}:{e['line']} unused {e['kind']} '{e['name']}'" for e in data]


def dotnet_findings(ctx: Context) -> list[str]:
    if ctx.config.section("dotnet") is None:
        return []
    from marestail import dotnet

    return structured(ctx, dotnet, "C# deadcode scanner failed")


def rust_findings(ctx: Context) -> list[str]:
    if ctx.config.section("rust") is None:
        return []
    from marestail import rust

    return structured(ctx, rust, "rust deadcode scanner failed", partial(rust.rel, ctx), uses=rust.use_files(ctx))


def java_findings(ctx: Context) -> list[str]:
    if ctx.config.section("java") is None:
        return []
    from marestail import java

    return structured(ctx, java, "java deadcode scanner failed")


def unused_function(label: str, entry: dict[str, Any]) -> str:
    return f"{label}:{entry['line']} unused function {entry['module']}.{entry['function']}/{entry['arity']}"


def elixir_findings(ctx: Context) -> list[str]:
    from marestail import elixir

    if ctx.config.section("elixir") is None:
        return []
    root = ctx.elixir_root()
    out = ctx.work / "ex-deadcode.json"
    out.unlink(missing_ok=True)
    code, output = run(elixir.deadcode_command(ctx, out), cwd=root, timeout=900)
    if code != 0 or not out.exists():
        return [failed(ELIXIR_FAILED, output)]
    return elixir_entries(json.loads(out.read_text()), root, ctx)


def elixir_entries(entries: list[dict[str, Any]], root: Path, ctx: Context) -> list[str]:
    return [unused_function(elixir_label(entry["file"], root, ctx), entry) for entry in entries]


def elixir_label(name: str, root: Path, ctx: Context) -> str:
    file = (root / name).resolve()
    return str(file.relative_to(ctx.root)) if file.is_relative_to(ctx.root) else name


def erlang_findings(ctx: Context) -> list[str]:
    if ctx.config.section("erlang") is None:
        return []
    from marestail import erlang

    sources = erlang.source_files(ctx)
    if not sources:
        return []
    ebin = erlang.fresh_dir(ctx.work / "er-deadcode-ebin")
    code, output = erlang.erlc(ctx, ["+debug_info", "-o", str(ebin), *map(str, sources)], timeout=900)
    problem = compile_problem(code, output)
    return [problem] if problem else xref_findings(ctx, sources, ebin)


def compile_problem(code: int, output: str) -> str | None:
    from marestail import erlang

    return erlang.hint(code, output) or (failed(ERLANG_FAILED, output) if code != 0 else None)


def xref_findings(ctx: Context, sources: list[Path], ebin: Path) -> list[str]:
    from marestail import erlang

    code, output = erlang.escript(ctx, "deadcode.escript", xref_args(ctx, ebin), timeout=600)
    if code != 0:
        return [erlang.hint(code, output) or failed(ERLANG_FAILED, output)]
    return xref_entries(ctx, sources, json.loads(output))


def xref_entries(ctx: Context, sources: list[Path], entries: list[dict[str, Any]]) -> list[str]:
    from marestail import erlang

    labels = {path.stem: erlang.rel(ctx, path) for path in sources}
    return [unused_function(labels.get(entry["module"], entry["module"]), entry) for entry in entries]


def as_name_list(value: Any) -> list[str]:
    return [str(part) for part in value] if isinstance(value, list) else []


def xref_args(ctx: Context, ebin: Path) -> list[str]:
    beams = sorted(str(beam) for beam in ebin.glob("*.beam"))
    ignore = as_name_list(ctx.erlang("deadcode_ignore"))
    return (["--ignore", ",".join(ignore)] if ignore else []) + beams


SCANNERS = [
    python_findings,
    ts_findings,
    elixir_findings,
    erlang_findings,
    ruby_findings,
    dotnet_findings,
    rust_findings,
    java_findings,
]
