import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail
from marestail.sonar.client import Client, credentials

SCANNER_IMAGE = "sonarsource/sonar-scanner-cli"
POLL_SECONDS = 5
POLL_LIMIT = 120


def run_gate(ctx: Context) -> Result:
    started = time.time()
    creds = credentials()
    if creds is None:
        return Result("sonar", False, "not set up", ["run: marestail sonar setup"], 0.0)
    client = Client(creds["url"], creds["token"])
    key = ctx.config.get("sonar", "project_key")
    code, output = run(scanner_command(ctx, creds, key), cwd=ctx.root, timeout=1800)
    if code != 0:
        return Result("sonar", False, "scanner failed", tail(output), time.time() - started)
    error = wait_for_analysis(client, ctx)
    if error:
        return Result("sonar", False, "analysis did not complete", [error, *tail(output, 15)], time.time() - started)
    findings = collect(client, key)
    summary = "sonar clean" if not findings else f"{len(findings)} sonar findings"
    return Result("sonar", not findings, summary, findings, time.time() - started)


def scanner_command(ctx: Context, creds: dict, key: str) -> list[str]:
    root = str(ctx.root)
    return [
        "docker", "run", "--rm", "--network", "host",
        "-u", f"{uid()}:{gid()}",
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


def uid() -> int:
    import os

    return os.getuid()


def gid() -> int:
    import os

    return os.getgid()


def wait_for_analysis(client: Client, ctx: Context) -> str | None:
    task_id = report_task(ctx).get("ceTaskId")
    if not task_id:
        return "no ceTaskId in .scannerwork/report-task.txt"
    for _ in range(POLL_LIMIT):
        status = client.get("api/ce/task", id=task_id)["task"]["status"]
        if status == "SUCCESS":
            return None
        if status in ("FAILED", "CANCELED"):
            return f"analysis {status}"
        time.sleep(POLL_SECONDS)
    return "analysis timed out"


def report_task(ctx: Context) -> dict[str, str]:
    path = ctx.work / "scannerwork" / "report-task.txt"
    if not path.exists():
        return {}
    pairs = (line.partition("=") for line in path.read_text().splitlines() if "=" in line)
    return {key: value for key, _, value in pairs}


def collect(client: Client, key: str) -> list[str]:
    findings = [f"quality gate {status}" for status in [gate_status(client, key)] if status != "OK"]
    findings += issues(client, key)
    findings += hotspots(client, key)
    findings += measures(client, key)
    return findings


def gate_status(client: Client, key: str) -> str:
    return client.get("api/qualitygates/project_status", projectKey=key)["projectStatus"]["status"]


def issues(client: Client, key: str) -> list[str]:
    data = client.get("api/issues/search", componentKeys=key, resolved="false", ps=500)
    return [
        f"{issue.get('component', '').split(':', 1)[-1]}:{issue.get('line', 0)} {issue['severity']} {issue['rule']}: {issue['message']}"
        for issue in data.get("issues", [])
    ]


def hotspots(client: Client, key: str) -> list[str]:
    data = client.get("api/hotspots/search", project=key, status="TO_REVIEW", ps=500)
    return [f"{h.get('component', '').split(':', 1)[-1]}:{h.get('line', 0)} hotspot: {h['message']}" for h in data.get("hotspots", [])]


def measures(client: Client, key: str) -> list[str]:
    data = client.get("api/measures/component", component=key, metricKeys="coverage,duplicated_lines_density")
    values = {m["metric"]: float(m.get("value", 0)) for m in data["component"].get("measures", [])}
    findings = []
    if values.get("coverage", 100.0) < 100.0:
        findings.append(f"sonar coverage {values['coverage']:.1f}% (need 100)")
    if values.get("duplicated_lines_density", 0.0) > 0.0:
        findings.append(f"sonar duplication {values['duplicated_lines_density']:.1f}% (need 0)")
    return findings


