import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marestail import dotnet, erlang, java, rust
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import run, tail
from marestail.sonar.client import Client, credentials

GATE = "sonar"
SECTION = "sonar"
SCANNER_IMAGE = "sonarsource/sonar-scanner-cli"
POLL_SECONDS = 5
POLL_LIMIT = 120
FAILED_TAIL = 15
DOTNET_SCANNER = "dotnet-sonarscanner"
DOTNET_SCANNER_VERSION = "11.3.0"
DOTNET_REPORT_TASK = Path(".sonarqube") / "out" / ".sonar" / "report-task.txt"
PROPERTIES_FILE = "sonar-project.properties"
PROPERTIES_CONFLICT = "delete sonar-project.properties: the SonarScanner for .NET refuses to run beside it, and [sonar] in marestail.toml configures this gate"
MEASURES = "api/measures/component"
COVERAGE = "coverage"
DUPLICATION = "duplicated_lines_density"
LANGUAGES = "ncloc_language_distribution"
PAGE = 500
FINISHED = {"SUCCESS": None, "FAILED": "analysis FAILED", "CANCELED": "analysis CANCELED"}
BENCHMARKS = "perf/**"
EQUALS = "="
COLON = ":"
SLASH = "/"
COMMA = ","
LINE = "line"
COMPONENT = "component"
PATH_KEY = "path"
KEY = "key"
EMPTY = ""
EMPTY_LIST: list[str] = []
DOTNET_EXCLUSIONS = [
    "**/node_modules/**",
    "**/.next/**",
    "**/bin/**",
    "**/obj/**",
    "**/dist/**",
    "**/.venv/**",
    "**/mutants/**",
    "**/StrykerOutput/**",
    "**/coverage/**",
    ".sonarqube/**",
    ".marestail/**",
    ".scannerwork/**",
    BENCHMARKS,
]
Credentials = dict[str, str]


@dataclass(frozen=True)
class LanguageCheck:
    section: str
    marker: str
    label: str
    where: Callable[[Context], str]
    advice: str
    coverage: str


def run_gate(ctx: Context) -> Result:
    started = time.time()
    creds = credentials()
    if creds is None:
        return Result(GATE, False, "not set up", ["run: marestail sonar setup"], 0.0)
    return analyse(ctx, creds, started)


def analyse(ctx: Context, creds: Credentials, started: float) -> Result:
    client = Client(creds["url"], creds["token"])
    key = ctx.config.get(SECTION, "project_key")
    code, output, task_file = scan(ctx, creds, key)
    if code != 0:
        return Result(GATE, False, "scanner failed", tail(output), elapsed(started))
    error = wait_for_analysis(client, task_file)
    if error:
        return Result(GATE, False, "analysis did not complete", [error, *tail(output, FAILED_TAIL)], elapsed(started))
    findings, status = collect(ctx, client, key)
    findings += language_findings(ctx, client, key)
    return Result(GATE, not findings, summarize(ctx, findings, status), findings, elapsed(started))


def scan(ctx: Context, creds: Credentials, key: str) -> tuple[int, str, Path]:
    if ctx.config.section("dotnet") is not None:
        code, output = dotnet_scan(ctx, creds, key)
        return code, output, ctx.root / DOTNET_REPORT_TASK
    code, output = run(scanner_command(ctx, creds, key), cwd=ctx.root, timeout=1800)
    return code, output, ctx.work / "scannerwork" / "report-task.txt"


def summarize(ctx: Context, findings: list[str], status: str) -> str:
    base = "sonar clean" if not findings else f"{len(findings)} sonar findings"
    if not ctx.scoped:
        return base
    return f"{base} in scope (global quality gate {status}; scope: {ctx.scope_summary()})"


def scanner_command(ctx: Context, creds: Credentials, key: str) -> list[str]:
    return [*docker_arguments(ctx, creds), SCANNER_IMAGE, *scanner_properties(ctx, key)]


def docker_arguments(ctx: Context, creds: Credentials) -> list[str]:
    root = str(ctx.root)
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-u",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        f"SONAR_HOST_URL={creds['url']}",
        "-e",
        f"SONAR_TOKEN={creds['token']}",
        "-e",
        f"SONAR_USER_HOME={root}/.marestail/sonar-cache",
        "-v",
        f"{root}:{root}",
        *git_mounts(ctx),
        "-w",
        root,
    ]


def scanner_properties(ctx: Context, key: str) -> list[str]:
    root = str(ctx.root)
    return [
        f"-Dsonar.projectKey={key}",
        f"-Dsonar.projectBaseDir={root}",
        f"-Dsonar.working.directory={root}/.marestail/scannerwork",
        f"-Dsonar.exclusions={scanner_exclusions(ctx)}",
        *java_properties(ctx),
    ]


def git_mounts(ctx: Context) -> list[str]:
    code, output = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ctx.root)
    lines = output.strip().splitlines()
    if code != 0 or not lines:
        return []
    common = Path(lines[-1]).resolve()
    if common.is_relative_to(ctx.root.resolve()):
        return []
    return ["-v", f"{common}:{common}:ro"]


def java_properties(ctx: Context) -> list[str]:
    if ctx.config.section("java") is None:
        return []
    declared = declared_properties(ctx)
    values = {
        "sonar.java.binaries": java.build_dir(ctx) / "classes",
        "sonar.coverage.jacoco.xmlReportPaths": ctx.work / "java-jacoco.xml",
    }
    return [f"-D{name}={value}" for name, value in values.items() if name not in declared]


def declared_properties(ctx: Context) -> set[str]:
    return {key for key, _ in project_properties(ctx)}


def scanner_exclusions(ctx: Context) -> str:
    patterns = project_exclusions(ctx)
    return ",".join(patterns if BENCHMARKS in patterns else [*patterns, BENCHMARKS])


def project_exclusions(ctx: Context) -> list[str]:
    values = [value for key, value in project_properties(ctx) if key == "sonar.exclusions"]
    return split_patterns(values[0]) if values else []


def split_patterns(value: str) -> list[str]:
    return [pattern.strip() for pattern in value.split(",") if pattern.strip()]


def project_properties(ctx: Context) -> list[tuple[str, str]]:
    path = ctx.root / PROPERTIES_FILE
    return properties(path.read_text()) if path.exists() else []


def properties(text: str) -> list[tuple[str, str]]:
    entries = (property_entry(line.strip()) for line in text.replace("\\\n", "").splitlines())
    return [entry for entry in entries if entry is not None]


def property_entry(line: str) -> tuple[str, str] | None:
    if line.startswith(("#", "!")):
        return None
    separator = separator_index(line)
    if separator <= 0:
        return None
    return line[:separator].strip(), line[separator + 1 :].strip()


def separator_index(line: str) -> int:
    return min((index for index in (line.find(EQUALS), line.find(COLON)) if index >= 0), default=-1)


def dotnet_scan(ctx: Context, creds: Credentials, key: str) -> tuple[int, str]:
    if (ctx.root / PROPERTIES_FILE).exists():
        return 1, PROPERTIES_CONFLICT
    product, _, error = dotnet.projects(ctx)
    if error:
        return 1, error
    tools = ctx.work / "dotnet-tools"
    code, output = install_scanner(ctx, tools)
    if code != 0:
        return code, output
    script = write_scan_script(ctx, creds, key, tools, build_target(ctx, product))
    return dotnet.dotnet(ctx, [str(script)], cwd=ctx.root, timeout=1800, network=True, extra={"SONAR_TOKEN": creds["token"]}, program="sh")


def install_scanner(ctx: Context, tools: Path) -> tuple[int, str]:
    if (tools / DOTNET_SCANNER).exists():
        return 0, ""
    code, output = dotnet.dotnet(
        ctx,
        ["tool", "install", DOTNET_SCANNER, "--version", DOTNET_SCANNER_VERSION, "--tool-path", str(tools)],
        cwd=ctx.root,
        network=True,
    )
    return code, dotnet.hint(code, output) or output


def build_target(ctx: Context, product: Path | None) -> Path | None:
    solutions = sorted(ctx.dotnet_root().glob("*.sln"))
    return solutions[0] if len(solutions) == 1 else product


def write_scan_script(ctx: Context, creds: Credentials, key: str, tools: Path, build: Path | None) -> Path:
    name = ctx.config.get(SECTION, "project_name", key)
    script = ctx.work / "sonar-dotnet.sh"
    script.write_text(
        "\n".join(
            [
                "set -e",
                f'export PATH="$PATH:{tools}"',
                f'cd "{ctx.root}" && rm -rf .sonarqube',
                f'{DOTNET_SCANNER} begin /k:"{key}" /n:"{name}" /s:"{write_settings(ctx)}" /d:sonar.host.url="{creds["url"]}" /d:sonar.token="$SONAR_TOKEN" /d:sonar.projectBaseDir="{ctx.root}"',
                f'dotnet build "{build}" -t:Rebuild --nologo',
                f'{DOTNET_SCANNER} end /d:sonar.token="$SONAR_TOKEN"',
                "",
            ]
        )
    )
    return script


def write_settings(ctx: Context) -> Path:
    lines = [
        '<?xml version="1.0" encoding="utf-8" ?>',
        '<SonarQubeAnalysisProperties xmlns="http://www.sonarsource.com/msbuild/integration/2015/1">',
        *(f'  <Property Name="{name}">{value}</Property>' for name, value in settings(ctx).items() if value),
        "</SonarQubeAnalysisProperties>",
    ]
    path = ctx.work / "sonar-dotnet.xml"
    path.write_text("\n".join(lines) + "\n")
    return path


def settings(ctx: Context) -> dict[str, str]:
    values = {
        "sonar.exclusions": ",".join(DOTNET_EXCLUSIONS + dotnet.listify(ctx.config.get(SECTION, "exclusions", []))),
        "sonar.coverage.exclusions": coverage_exclusions(ctx),
        "sonar.cs.opencover.reportsPaths": f"{ctx.work}/cs-tests/**/coverage.opencover.xml",
        "sonar.scm.disabled": "true",
        "sonar.sourceEncoding": "UTF-8",
    }
    return values | report_paths(ctx)


def coverage_exclusions(ctx: Context) -> str:
    prefix = dotnet.rel(ctx, ctx.dotnet_root())
    prefix = "" if prefix == "." else prefix + "/"
    return COMMA.join(prefix + pattern.strip(SLASH) for pattern in dotnet.configured_list(ctx.dotnet("coverage_exclude", EMPTY_LIST)))


def report_paths(ctx: Context) -> dict[str, str]:
    optional = {
        "ts": ("sonar.javascript.lcov.reportPaths", ctx.work / "ts-coverage" / "lcov.info"),
        "python": ("sonar.python.coverage.reportPaths", ctx.work / "py-coverage.xml"),
    }
    return {name: str(path) for section, (name, path) in optional.items() if ctx.config.section(section) is not None}


def dotnet_where(ctx: Context) -> str:
    return dotnet.rel(ctx, ctx.dotnet_root())


def erlang_where(ctx: Context) -> str:
    return erlang.rel(ctx, ctx.erlang_root())


def rust_where(ctx: Context) -> str:
    return rust.rel(ctx, ctx.rust_root())


def java_where(ctx: Context) -> str:
    return java.rel(ctx, ctx.java_root())


LANGUAGE_CHECKS = (
    LanguageCheck(
        "dotnet",
        "cs=",
        "C#",
        dotnet_where,
        "",
        "marestail.toml:1 SonarQube imported no coverage; run cs.tests first so its OpenCover report exists",
    ),
    LanguageCheck(
        "erlang",
        "erlang=",
        "Erlang",
        erlang_where,
        "; run: marestail sonar setup",
        "marestail.toml:1 SonarQube imported no erlang coverage; run er.tests first so .marestail/eunit.coverdata exists",
    ),
    LanguageCheck(
        "rust",
        "rust=",
        "Rust",
        rust_where,
        "; put the crate's src in sonar.sources",
        "sonar-project.properties:1 SonarQube imported no rust coverage; run rs.tests first and set sonar.rust.lcov.reportPaths=.marestail/rs-lcov.info",
    ),
    LanguageCheck(
        "java",
        "java=",
        "Java",
        java_where,
        "; put the sources in sonar.sources",
        "marestail.toml:1 SonarQube imported no java coverage; run java.tests first so .marestail/java-jacoco.xml exists",
    ),
)


def language_findings(ctx: Context, client: Client, key: str) -> list[str]:
    findings: list[str] = []
    for check in LANGUAGE_CHECKS:
        if ctx.config.section(check.section) is not None:
            findings += checked_language(ctx, client, key, check)
    return findings


def checked_language(ctx: Context, client: Client, key: str, check: LanguageCheck) -> list[str]:
    values = language_values(client, key)
    languages = values.get(LANGUAGES, "")
    findings = []
    if check.marker not in languages:
        findings.append(f"{check.where(ctx)}:1 SonarQube received no {check.label} lines (languages: {languages or 'none'}){check.advice}")
    if COVERAGE not in values:
        findings.append(check.coverage)
    return findings


def language_values(client: Client, key: str) -> dict[str, str]:
    data = client.get(MEASURES, component=key, metricKeys=f"{COVERAGE},{LANGUAGES}")
    return {m["metric"]: m.get("value", "") for m in data.get("component", {}).get("measures", [])}


def wait_for_analysis(client: Client, task_file: Path) -> str | None:
    task_id = report_task(task_file).get("ceTaskId")
    if not task_id:
        return f"no ceTaskId in {task_file}"
    return poll(client, task_id)


def poll(client: Client, task_id: str) -> str | None:
    for _ in range(POLL_LIMIT):
        status = client.get("api/ce/task", id=task_id)["task"]["status"]
        if status in FINISHED:
            return FINISHED[status]
        time.sleep(POLL_SECONDS)
    return "analysis timed out"


def report_task(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return report_pairs(path.read_text())


def report_pairs(text: str) -> dict[str, str]:
    pairs = (line.partition("=") for line in text.splitlines() if "=" in line)
    return {key: value for key, _, value in pairs}


def collect(ctx: Context, client: Client, key: str) -> tuple[list[str], str]:
    status = gate_status(client, key)
    findings = reopened(ctx, client, key)
    if not ctx.scoped and status != "OK":
        findings.append(f"quality gate {status}")
    findings += issues(ctx, client, key)
    findings += hotspots(ctx, client, key)
    findings += measures(ctx, client, key)
    return findings, status


def gate_status(client: Client, key: str) -> str:
    status: str = client.get("api/qualitygates/project_status", projectKey=key)["projectStatus"]["status"]
    return status


def issue_path(component: dict[str, Any]) -> str:
    path: str = component.get(COMPONENT, EMPTY).split(COLON, 1)[-1]
    return path


def reopened(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/issues/search", componentKeys=key, issueStatuses="ACCEPTED,FALSE_POSITIVE", ps=PAGE)
    in_scope = [issue for issue in data.get("issues", []) if ctx.in_scope(issue_path(issue))]
    return [reopen(client, issue) for issue in in_scope]


def reopen(client: Client, issue: dict[str, Any]) -> str:
    client.post("api/issues/do_transition", issue=issue["key"], transition="reopen")
    where = f"{issue_path(issue)}:{issue.get(LINE, 0)}"
    return f"{where} {issue['rule']} was marked {issue.get('issueStatus')} in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties"


def issues(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/issues/search", componentKeys=key, resolved="false", ps=PAGE)
    return [
        f"{issue_path(issue)}:{issue.get(LINE, 0)} {issue['severity']} {issue['rule']}: {issue['message']}"
        for issue in data.get("issues", [])
        if ctx.in_scope(issue_path(issue))
    ]


def hotspots(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/hotspots/search", project=key, status="TO_REVIEW", ps=PAGE)
    return [
        f"{issue_path(hotspot)}:{hotspot.get(LINE, 0)} hotspot: {hotspot['message']}"
        for hotspot in data.get("hotspots", [])
        if ctx.in_scope(issue_path(hotspot))
    ]


def measures(ctx: Context, client: Client, key: str) -> list[str]:
    if ctx.scoped:
        return scoped_duplication(ctx, client, key)
    data = client.get(MEASURES, component=key, metricKeys=f"{COVERAGE},{DUPLICATION}")
    return measure_findings(metric_values(data["component"]))


def metric_values(holder: dict[str, Any]) -> dict[str, float]:
    return {m["metric"]: float(m.get("value", 0)) for m in holder.get("measures", [])}


def measure_findings(values: dict[str, float]) -> list[str]:
    findings = []
    if values.get(COVERAGE, 100.0) < 100.0:
        findings.append(f"sonar coverage {values[COVERAGE]:.1f}% (need 100)")
    if values.get(DUPLICATION, 0.0) > 0.0:
        findings.append(duplication(values[DUPLICATION]))
    return findings


def duplication(density: float) -> str:
    return f"sonar duplication {density:.1f}% (need 0)"


def scoped_duplication(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/measures/component_tree", component=key, metricKeys=DUPLICATION, qualifiers="FIL", ps=PAGE)
    findings = []
    for component in data.get("components", []):
        path = component_path(component)
        density = metric_values(component).get(DUPLICATION, 0.0)
        if ctx.in_scope(path) and density > 0.0:
            findings.append(f"{path}:1 {duplication(density)}")
    return findings


def component_path(component: dict[str, Any]) -> str:
    path: str = component.get(PATH_KEY) or component.get(KEY, EMPTY).split(COLON, 1)[-1]
    return path
