import os
import time
from pathlib import Path

from marestail import dotnet, erlang, rust
from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail
from marestail.sonar.client import Client, credentials

SCANNER_IMAGE = "sonarsource/sonar-scanner-cli"
POLL_SECONDS = 5
POLL_LIMIT = 120
DOTNET_SCANNER = "dotnet-sonarscanner"
DOTNET_SCANNER_VERSION = "11.3.0"
DOTNET_REPORT_TASK = Path(".sonarqube") / "out" / ".sonar" / "report-task.txt"
DOTNET_EXCLUSIONS = [
    "**/node_modules/**", "**/.next/**", "**/bin/**", "**/obj/**", "**/dist/**", "**/.venv/**",
    "**/mutants/**", "**/StrykerOutput/**", "**/coverage/**", ".sonarqube/**", ".marestail/**", ".scannerwork/**",
]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    creds = credentials()
    if creds is None:
        return Result("sonar", False, "not set up", ["run: marestail sonar setup"], 0.0)
    client = Client(creds["url"], creds["token"])
    key = ctx.config.get("sonar", "project_key")
    is_dotnet = ctx.config.section("dotnet") is not None
    if is_dotnet:
        code, output = dotnet_scan(ctx, creds, key)
        task_file = ctx.root / DOTNET_REPORT_TASK
    else:
        code, output = run(scanner_command(ctx, creds, key), cwd=ctx.root, timeout=1800)
        task_file = ctx.work / "scannerwork" / "report-task.txt"
    if code != 0:
        return Result("sonar", False, "scanner failed", tail(output), time.time() - started)
    error = wait_for_analysis(client, task_file)
    if error:
        return Result("sonar", False, "analysis did not complete", [error, *tail(output, 15)], time.time() - started)
    findings, status = collect(ctx, client, key)
    findings += dotnet_findings(ctx, client, key) if is_dotnet else []
    if ctx.config.section("erlang") is not None:
        findings += erlang_findings(ctx, client, key)
    if ctx.config.section("rust") is not None:
        findings += rust_findings(ctx, client, key)
    return Result("sonar", not findings, summarize(ctx, findings, status), findings, time.time() - started)


def summarize(ctx: Context, findings: list[str], status: str) -> str:
    base = "sonar clean" if not findings else f"{len(findings)} sonar findings"
    if not ctx.scoped:
        return base
    return f"{base} in scope (global quality gate {status}; scope: {ctx.scope_summary()})"


def scanner_command(ctx: Context, creds: dict, key: str) -> list[str]:
    root = str(ctx.root)
    return [
        "docker", "run", "--rm", "--network", "host",
        "-u", f"{os.getuid()}:{os.getgid()}",
        "-e", f"SONAR_HOST_URL={creds['url']}",
        "-e", f"SONAR_TOKEN={creds['token']}",
        "-e", f"SONAR_USER_HOME={root}/.marestail/sonar-cache",
        "-v", f"{root}:{root}",
        "-w", root,
        SCANNER_IMAGE,
        f"-Dsonar.projectKey={key}",
        f"-Dsonar.projectBaseDir={root}",
        f"-Dsonar.working.directory={root}/.marestail/scannerwork",
    ]


def dotnet_scan(ctx: Context, creds: dict, key: str) -> tuple[int, str]:
    if (ctx.root / "sonar-project.properties").exists():
        return 1, "delete sonar-project.properties: the SonarScanner for .NET refuses to run beside it, and [sonar] in marestail.toml configures this gate"
    product, tests, error = dotnet.projects(ctx)
    if error:
        return 1, error
    tools = ctx.work / "dotnet-tools"
    if not (tools / DOTNET_SCANNER).exists():
        code, output = dotnet.dotnet(ctx, ["tool", "install", DOTNET_SCANNER, "--version", DOTNET_SCANNER_VERSION, "--tool-path", str(tools)], cwd=ctx.root, network=True)
        if code != 0:
            return code, dotnet.hint(code, output) or output
    solutions = sorted(ctx.dotnet_root().glob("*.sln"))
    build = solutions[0] if len(solutions) == 1 else product
    name = ctx.config.get("sonar", "project_name", key)
    script = ctx.work / "sonar-dotnet.sh"
    script.write_text("\n".join([
        "set -e",
        f'export PATH="$PATH:{tools}"',
        f'cd "{ctx.root}" && rm -rf .sonarqube',
        f'{DOTNET_SCANNER} begin /k:"{key}" /n:"{name}" /s:"{write_settings(ctx)}" /d:sonar.host.url="{creds["url"]}" /d:sonar.token="$SONAR_TOKEN" /d:sonar.projectBaseDir="{ctx.root}"',
        f'dotnet build "{build}" -t:Rebuild --nologo',
        f'{DOTNET_SCANNER} end /d:sonar.token="$SONAR_TOKEN"',
        "",
    ]))
    return dotnet.dotnet(ctx, [str(script)], cwd=ctx.root, timeout=1800, network=True, extra={"SONAR_TOKEN": creds["token"]}, program="sh")


def write_settings(ctx: Context) -> Path:
    prefix = dotnet.rel(ctx, ctx.dotnet_root())
    prefix = "" if prefix == "." else prefix + "/"
    values = {
        "sonar.exclusions": ",".join(DOTNET_EXCLUSIONS + dotnet.listify(ctx.config.get("sonar", "exclusions", []))),
        "sonar.coverage.exclusions": ",".join(prefix + pattern.strip("/") for pattern in dotnet.listify(ctx.dotnet("coverage_exclude", []))),
        "sonar.cs.opencover.reportsPaths": f"{ctx.work}/cs-tests/**/coverage.opencover.xml",
        "sonar.scm.disabled": "true",
        "sonar.sourceEncoding": "UTF-8",
    }
    if ctx.config.section("ts") is not None:
        values["sonar.javascript.lcov.reportPaths"] = str(ctx.work / "ts-coverage" / "lcov.info")
    if ctx.config.section("python") is not None:
        values["sonar.python.coverage.reportPaths"] = str(ctx.work / "py-coverage.xml")
    lines = ['<?xml version="1.0" encoding="utf-8" ?>', '<SonarQubeAnalysisProperties xmlns="http://www.sonarsource.com/msbuild/integration/2015/1">']
    lines += [f'  <Property Name="{name}">{value}</Property>' for name, value in values.items() if value]
    lines.append("</SonarQubeAnalysisProperties>")
    path = ctx.work / "sonar-dotnet.xml"
    path.write_text("\n".join(lines) + "\n")
    return path


def dotnet_findings(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/measures/component", component=key, metricKeys="coverage,ncloc_language_distribution")
    values = {m["metric"]: m.get("value", "") for m in data.get("component", {}).get("measures", [])}
    findings = []
    if "cs=" not in values.get("ncloc_language_distribution", ""):
        findings.append(f"{dotnet.rel(ctx, ctx.dotnet_root())}:1 SonarQube received no C# lines (languages: {values.get('ncloc_language_distribution') or 'none'})")
    if "coverage" not in values:
        findings.append("marestail.toml:1 SonarQube imported no coverage; run cs.tests first so its OpenCover report exists")
    return findings


def erlang_findings(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/measures/component", component=key, metricKeys="coverage,ncloc_language_distribution")
    values = {m["metric"]: m.get("value", "") for m in data.get("component", {}).get("measures", [])}
    findings = []
    if "erlang=" not in values.get("ncloc_language_distribution", ""):
        findings.append(f"{erlang.rel(ctx, ctx.erlang_root())}:1 SonarQube received no Erlang lines (languages: {values.get('ncloc_language_distribution') or 'none'}); run: marestail sonar setup")
    if "coverage" not in values:
        findings.append("marestail.toml:1 SonarQube imported no erlang coverage; run er.tests first so .marestail/eunit.coverdata exists")
    return findings


def rust_findings(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/measures/component", component=key, metricKeys="coverage,ncloc_language_distribution")
    values = {m["metric"]: m.get("value", "") for m in data.get("component", {}).get("measures", [])}
    findings = []
    if "rust=" not in values.get("ncloc_language_distribution", ""):
        findings.append(f"{rust.rel(ctx, ctx.rust_root())}:1 SonarQube received no Rust lines (languages: {values.get('ncloc_language_distribution') or 'none'}); put the crate's src in sonar.sources")
    if "coverage" not in values:
        findings.append("sonar-project.properties:1 SonarQube imported no rust coverage; run rs.tests first and set sonar.rust.lcov.reportPaths=.marestail/rs-lcov.info")
    return findings


def wait_for_analysis(client: Client, task_file: Path) -> str | None:
    task_id = report_task(task_file).get("ceTaskId")
    if not task_id:
        return f"no ceTaskId in {task_file}"
    for _ in range(POLL_LIMIT):
        status = client.get("api/ce/task", id=task_id)["task"]["status"]
        if status == "SUCCESS":
            return None
        if status in ("FAILED", "CANCELED"):
            return f"analysis {status}"
        time.sleep(POLL_SECONDS)
    return "analysis timed out"


def report_task(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = (line.partition("=") for line in path.read_text().splitlines() if "=" in line)
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
    return client.get("api/qualitygates/project_status", projectKey=key)["projectStatus"]["status"]


def issue_path(component: dict) -> str:
    return component.get("component", "").split(":", 1)[-1]


def reopened(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/issues/search", componentKeys=key, issueStatuses="ACCEPTED,FALSE_POSITIVE", ps=500)
    findings = []
    for issue in data.get("issues", []):
        if not ctx.in_scope(issue_path(issue)):
            continue
        client.post("api/issues/do_transition", issue=issue["key"], transition="reopen")
        where = f"{issue_path(issue)}:{issue.get('line', 0)}"
        findings.append(f"{where} {issue['rule']} was marked {issue.get('issueStatus')} in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties")
    return findings


def issues(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/issues/search", componentKeys=key, resolved="false", ps=500)
    return [
        f"{issue_path(issue)}:{issue.get('line', 0)} {issue['severity']} {issue['rule']}: {issue['message']}"
        for issue in data.get("issues", [])
        if ctx.in_scope(issue_path(issue))
    ]


def hotspots(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/hotspots/search", project=key, status="TO_REVIEW", ps=500)
    return [
        f"{issue_path(hotspot)}:{hotspot.get('line', 0)} hotspot: {hotspot['message']}"
        for hotspot in data.get("hotspots", [])
        if ctx.in_scope(issue_path(hotspot))
    ]


def measures(ctx: Context, client: Client, key: str) -> list[str]:
    if ctx.scoped:
        return scoped_duplication(ctx, client, key)
    data = client.get("api/measures/component", component=key, metricKeys="coverage,duplicated_lines_density")
    values = {m["metric"]: float(m.get("value", 0)) for m in data["component"].get("measures", [])}
    findings = []
    if values.get("coverage", 100.0) < 100.0:
        findings.append(f"sonar coverage {values['coverage']:.1f}% (need 100)")
    if values.get("duplicated_lines_density", 0.0) > 0.0:
        findings.append(f"sonar duplication {values['duplicated_lines_density']:.1f}% (need 0)")
    return findings


def scoped_duplication(ctx: Context, client: Client, key: str) -> list[str]:
    data = client.get("api/measures/component_tree", component=key, metricKeys="duplicated_lines_density", qualifiers="FIL", ps=500)
    findings = []
    for component in data.get("components", []):
        path = component.get("path") or component.get("key", "").split(":", 1)[-1]
        values = {m["metric"]: float(m.get("value", 0)) for m in component.get("measures", [])}
        if ctx.in_scope(path) and values.get("duplicated_lines_density", 0.0) > 0.0:
            findings.append(f"{path}:1 sonar duplication {values['duplicated_lines_density']:.1f}% (need 0)")
    return findings
