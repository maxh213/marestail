import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result

ROUTE_PATTERNS = [r"""@\w+\.route\(\s*["']([^"']+)["']""", r"""\.(?:get|post|put|delete|patch)\(\s*["'](/[^"']*)["']""", r"""(?:get|post|put|patch|delete)\s+["'](/[^"']+)["']"""]
ENV_PATTERNS = [r"""os\.getenv\(\s*["']([A-Z][A-Z0-9_]+)["']""", r"""os\.environ(?:\.get\(|\[)\s*["']([A-Z][A-Z0-9_]+)["']""", r"""process\.env\.([A-Z][A-Z0-9_]+)""", r"""ENV(?:\[|\.fetch\(\s*)["']([A-Z][A-Z0-9_]+)["']"""]
IGNORED_ENV = ["K_REVISION", "K_SERVICE", "PORT", "HOME", "PATH"]
LEDGER_ROW = re.compile(r"^\|\s*`(/[^`]*)`\s*\|\s*(\w+)\s*\|", re.MULTILINE)
DOC_PATH = re.compile(r"`((?:[\w.-]+/)+[\w.-]+)`")
GONE = {"retired", "removed", "gone"}
SKIP_DIRS = {"node_modules", ".venv", "mutants", "dist", ".git", "tests", "test", "__pycache__", ".marestail"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.config.section("docs") is None:
        return Result.skipped("docs", "no [docs] section")
    docs = doc_text(ctx)
    findings = route_findings(ctx) + env_findings(ctx, docs) + path_findings(ctx)
    summary = "docs match the code" if not findings else f"{len(findings)} drift findings"
    return Result("docs", not findings, summary, findings, time.time() - started)


def doc_files(ctx: Context) -> list[Path]:
    patterns = ctx.config.get("docs", "files", ["README.md"])
    return sorted({path for pattern in patterns for path in ctx.root.glob(pattern) if path.is_file()})


def doc_text(ctx: Context) -> str:
    return "\n".join(path.read_text() for path in doc_files(ctx))


def source_files(ctx: Context) -> list[Path]:
    folders = ctx.config.get("docs", "sources", ["."])
    suffixes = (".py", ".ts", ".tsx", ".js", ".mjs", ".rb")
    files = []
    for folder in folders:
        for path in (ctx.root / folder).rglob("*"):
            if path.suffix in suffixes and not any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts):
                files.append(path)
    return sorted(files)


def found(ctx: Context, patterns: list[str]) -> dict[str, str]:
    hits: dict[str, str] = {}
    for path in source_files(ctx):
        text = path.read_text()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                hits.setdefault(match.group(1), f"{path.relative_to(ctx.root)}:{text.count(chr(10), 0, match.start()) + 1}")
    return hits


def route_findings(ctx: Context) -> list[str]:
    ledger_name = ctx.config.get("docs", "routes_file")
    if not ledger_name:
        return []
    ledger_path = ctx.root / ledger_name
    if not ledger_path.exists():
        return [f"{ledger_name} is missing; it must list every route with a status"]
    ledger = {route: status.lower() for route, status in LEDGER_ROW.findall(ledger_path.read_text())}
    in_code = found(ctx, ctx.config.get("docs", "route_patterns", ROUTE_PATTERNS))
    findings = [f"{where} route {route} is not in {ledger_name}" for route, where in in_code.items() if route not in ledger]
    findings += [f"{where} route {route} is marked {ledger[route]} in {ledger_name} but still exists" for route, where in in_code.items() if ledger.get(route) in GONE]
    findings += [f"{ledger_name} lists {route} as {status} but no code serves it" for route, status in ledger.items() if status not in GONE and route not in in_code]
    return findings


def env_findings(ctx: Context, docs: str) -> list[str]:
    ignored = set(IGNORED_ENV) | set(ctx.config.get("docs", "ignore_env", []))
    in_code = found(ctx, ctx.config.get("docs", "env_patterns", ENV_PATTERNS))
    return [f"{where} environment variable {name} is not documented" for name, where in in_code.items() if name not in ignored and name not in docs]


def path_findings(ctx: Context) -> list[str]:
    prefixes = tuple(ctx.config.get("docs", "path_prefixes", []))
    findings = []
    for doc in doc_files(ctx):
        for number, line in enumerate(doc.read_text().splitlines(), start=1):
            for token in DOC_PATH.findall(line):
                if prefixes and token.startswith(prefixes) and "*" not in token and not (ctx.root / token).exists():
                    findings.append(f"{doc.relative_to(ctx.root)}:{number} mentions {token}, which does not exist")
    return findings
