#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.config import Config
from marestail.context import Context
from marestail.gates import sonar

CLI = Path(__file__).resolve().parent.parent / "marestail" / "cli.py"
PY_FILE = "app/legacy.py"
TS_FILE = "src/legacy.ts"
SCOPE = "--scope"
HYPER = "hyper"
FULL = ("--tier", "full")
ONLY = "--only"
PASSED = "GATE PASSED"
FOCUS_ERROR = "--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else"
PY_TOOLS = ["pytest", "pytest-cov", "radon", "ruff", "mypy", "vulture", "import-linter", "mutmut"]
TS_TOOLS = [
    "vitest@3",
    "@vitest/coverage-v8@3",
    "typescript@5",
    "eslint@9",
    "@stryker-mutator/core",
    "@stryker-mutator/vitest-runner",
    "dependency-cruiser",
    "knip",
]
PY_LEGACY = """import os
# legacy note


def double(price):
    return price * 2


def label(name):
    return "item " + name


def code(n):
    return n+1


def relay(n):
    return code(n)
"""
PY_TEST = """from app.legacy import code, double, label, relay


def test_double():
    assert double(2) == 4
    assert double(3) == 6


def test_calls():
    label("x")
    code(1)
    relay(1)
"""
PY_TOML = """[git]
base = "base"

[python]
root = "."
sources = ["app"]
"""
PY_PROJECT = """[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]

[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I"]

[tool.mypy]
files = ["app", "tests"]

[tool.mutmut]
source_paths = ["app"]

[tool.importlinter]
root_packages = ["app"]

[[tool.importlinter.contracts]]
name = "app layers"
type = "layers"
layers = ["app.legacy"]
"""
TS_LEGACY = """const unused = 1;
// legacy note


export function double(price) {
  return price * 2;
}

export function label(name) {
  return "item " + name;
}

export function code(n) {
  return n+1;
}
"""
TS_TEST = """import { expect, test } from "vitest";
import { code, double, label } from "./legacy";

test("double", () => {
  expect(double(2)).toBe(4);
  expect(double(3)).toBe(6);
  label("x");
  code(1);
});
"""
TS_FILES = {
    "package.json": '{"name": "hyper-ts-fixture", "private": true, "type": "module", "scripts": {"test": "vitest run"}}\n',
    "tsconfig.app.json": json.dumps(
        {
            "compilerOptions": {
                "target": "es2022",
                "module": "esnext",
                "moduleResolution": "bundler",
                "strict": False,
                "noEmit": True,
                "types": [],
            },
            "include": ["src"],
        }
    ),
    "eslint.config.js": 'export default [{files: ["src/**/*.ts"], rules: {"no-unused-vars": "error", "eqeqeq": "error"}}];\n',
    "stryker.config.json": '{"testRunner": "vitest", "coverageAnalysis": "perTest"}\n',
    ".dependency-cruiser.cjs": 'module.exports = {forbidden: [{name: "no-circular", severity: "error", from: {}, to: {circular: true}}]};\n',
    "vitest.config.ts": (
        'import { defineConfig } from "vitest/config";\n\n'
        'export default defineConfig({test: {coverage: {provider: "v8", include: ["src/**/*.ts"], exclude: ["src/**/*.test.ts"]}}});\n'
    ),
    "marestail.toml": '[git]\nbase = "base"\n\n[ts]\nroot = "."\n',
    ".gitignore": "node_modules/\n.marestail/\nreports/\n.stryker-tmp/\ncoverage/\n",
    TS_FILE: TS_LEGACY,
    "src/legacy.test.ts": TS_TEST,
}


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def contains(name: str, text: str, *needles: str) -> None:
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise SystemExit(f"{name}: missing {missing!r} in:\n{text}")


def lacks(name: str, text: str, *needles: str) -> None:
    found = [needle for needle in needles if needle in text]
    if found:
        raise SystemExit(f"{name}: unexpected {found!r} in:\n{text}")


def shell(folder: Path, *command: str) -> None:
    subprocess.run(list(command), cwd=folder, check=True, capture_output=True)


def git(repo: Path, *args: str) -> None:
    shell(repo, "git", *args)


def cli(repo: Path, *args: str, stdin: str = "", env: dict[str, str] | None = None) -> tuple[int, str, str]:
    merged = {key: value for key, value in os.environ.items() if not key.startswith("MARESTAIL_")}
    completed = subprocess.run(
        [sys.executable, str(CLI), *args], cwd=repo, input=stdin, capture_output=True, text=True, env={**merged, **(env or {})}, check=False
    )
    return completed.returncode, completed.stdout, completed.stderr


def gate(repo: Path, *args: str) -> tuple[int, str]:
    code, out, err = cli(repo, "gate", *FULL, *args)
    return code, out + err


def write_all(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)


def set_line(path: Path, number: int, text: str) -> None:
    lines = path.read_text().splitlines()
    lines[number - 1] = text
    path.write_text("\n".join(lines) + "\n")


def commit_base(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "tag", "base")
    git(repo, "checkout", "-qb", "work")


def new_repo(folder: Path, files: dict[str, str]) -> Path:
    folder.mkdir()
    git(folder, "init", "-q", "-b", "main")
    git(folder, "config", "user.email", "test@marestail")
    git(folder, "config", "user.name", "test")
    write_all(folder, files)
    return folder


def python_fixture(folder: Path) -> Path:
    files = {
        "marestail.toml": PY_TOML,
        "pyproject.toml": PY_PROJECT,
        ".gitignore": ".venv/\n.marestail/\n.mypy_cache/\n.ruff_cache/\n.coverage\n__pycache__/\nmutants/\n",
        "app/__init__.py": "",
        PY_FILE: PY_LEGACY,
        "tests/test_legacy.py": PY_TEST,
    }
    repo = new_repo(folder / "py", files)
    shell(repo, "uv", "venv", ".venv", "--quiet")
    shell(repo, "uv", "pip", "install", "--python", ".venv/bin/python", "--quiet", *PY_TOOLS)
    commit_base(repo)
    return repo


def ts_fixture(folder: Path) -> Path:
    repo = new_repo(folder / "ts", TS_FILES)
    shell(repo, "npm", "install", "--silent", "--no-audit", "--no-fund", "-D", *TS_TOOLS)
    commit_base(repo)
    return repo


def restore(repo: Path, *paths: str) -> None:
    git(repo, "checkout", "--", *paths)


def results(output: str, gate_name: str) -> list[str]:
    lines = output.splitlines()
    start = next(index for index, line in enumerate(lines) if f"] {gate_name} " in line)
    block = [line.strip() for line in lines[start + 1 :]]
    return block[: next((index for index, line in enumerate(block) if not line or line.startswith("[")), len(block))]


def header(output: str, gate_name: str) -> str:
    return next(line for line in output.splitlines() if f"] {gate_name} " in line)


def python_happy_path(repo: Path) -> None:
    set_line(repo / PY_FILE, 6, "    return 2 * price")
    code, output = gate(repo, SCOPE, HYPER)
    expect(
        "py-hyper-passes", (code, output.splitlines()[0], output.splitlines()[-1]), (0, "scope: hyper: 1 changed lines in 1 files", PASSED)
    )
    lacks("py-hyper-old-lines", output, f"{PY_FILE}:1", f"{PY_FILE}:2", f"{PY_FILE}:14", f"{PY_FILE}:17")
    code, out, _ = cli(repo, "gate", *FULL, SCOPE, HYPER, "--json")
    expect("py-hyper-json", (json.loads(out)["scope"], json.loads(out)["focus"]), (HYPER, []))
    code, output = gate(repo, SCOPE, "changed")
    expect("py-changed-fails", (code, output.splitlines()[0]), (1, "scope: changed (1 files, 1 lines)"))
    contains(
        "py-changed-old",
        output,
        f"{PY_FILE}:2 comment: # legacy note",
        f"{PY_FILE}:17 relay only forwards its arguments",
        "x_code__mutmut_",
    )
    code, output = gate(repo, SCOPE, "hard", "--focus", PY_FILE)
    expect("py-hard-fails", code, 1)
    contains("py-hard-old", output, f"{PY_FILE}:2 comment: # legacy note")


def python_changed_line_findings(repo: Path) -> None:
    set_line(repo / PY_FILE, 6, "    return 2 * price  # doubled")
    code, output = gate(repo, SCOPE, HYPER, ONLY, "comments")
    expect("py-comment", (code, results(output, "comments")), (1, [f"{PY_FILE}:6 comment: # doubled"]))
    set_line(repo / PY_FILE, 6, "    return 2 * price if price != None else 0")
    code, output = gate(repo, SCOPE, HYPER, ONLY, "py.lint")
    e711 = f"ruff: {PY_FILE}:6:34: E711 Comparison to `None` should be `cond is not None`"
    expect("py-lint", (code, results(output, "py.lint")), (1, [e711]))
    contains("py-lint-summary", header(output, "py.lint"), " 1 problems ")
    restore(repo, PY_FILE)
    with (repo / PY_FILE).open("a") as source:
        source.write("\n\ndef forward(n):\n    return label(n)\n")
    code, output = gate(repo, SCOPE, HYPER, ONLY, "depth,deadcode")
    expect("py-depth", results(output, "depth"), [f"{PY_FILE}:21 forward only forwards its arguments"])
    expect("py-deadcode", (code, results(output, "deadcode")), (1, [f"{PY_FILE}:21 unused function 'forward'"]))
    _, output = gate(repo, SCOPE, "changed", ONLY, "depth,deadcode")
    contains("py-depth-changed", output, f"{PY_FILE}:17 relay only forwards its arguments", f"{PY_FILE}:5 unused function 'double'")
    restore(repo, PY_FILE)


def python_mutation(repo: Path) -> None:
    set_line(repo / PY_FILE, 10, '    return "item: " + name')
    code, output = gate(repo, SCOPE, HYPER, ONLY, "py.mutation")
    survivors = ["app.legacy.x_label__mutmut_2: survived", "app.legacy.x_label__mutmut_3: survived"]
    expect("py-mutation", (code, results(output, "py.mutation")), (1, survivors))
    contains("py-mutation-summary", header(output, "py.mutation"), " 2 of 3 mutants not killed ")
    _, output = gate(repo, SCOPE, "changed", ONLY, "py.mutation")
    contains("py-mutation-changed", output, "4 of 8 mutants not killed", "app.legacy.x_code__mutmut_1: survived")
    restore(repo, PY_FILE)


def python_fail_closed(repo: Path) -> None:
    set_line(repo / PY_FILE, 6, "    return 2 * price")
    ruff = repo / ".venv" / "bin" / "ruff"
    hidden = repo / "ruff.hidden"
    ruff.rename(hidden)
    try:
        code, output = gate(repo, SCOPE, HYPER, ONLY, "py.lint")
    finally:
        hidden.rename(ruff)
    findings = results(output, "py.lint")
    expect("py-missing-ruff", (code, len(findings), [line.split(":")[0] for line in findings]), (1, 2, ["ruff", "format"]))
    contains("py-missing-ruff-text", output, f"{ruff}: not found", " 2 problems ")
    lacks("py-missing-ruff-file-level", output, "file-level")
    restore(repo, PY_FILE)


def python_hooks_and_runs(repo: Path, folder: Path) -> None:
    set_line(repo / PY_FILE, 6, "    return 2 * price  # doubled")
    code, out, err = cli(repo, "gate", "--hook", stdin="{}", env={"MARESTAIL_SCOPE": HYPER, "MARESTAIL_FOCUS": ""})
    expect("hook-blocks", code, 2)
    contains("hook-hyper", out + err, f"{PY_FILE}:6 comment: # doubled")
    lacks("hook-old-line", out + err, f"{PY_FILE}:2")
    restore(repo, PY_FILE)
    agent = folder / "envagent"
    seen = folder / "env.txt"
    agent.write_text(f"#!/bin/sh\nenv | grep ^MARESTAIL_ > {seen}\n")
    agent.chmod(0o755)
    task = folder / "t.md"
    task.write_text("Say hello.\n")
    cli(repo, "run", SCOPE, HYPER, "--to", "specifier", str(task), env={"MARESTAIL_CLAUDE": str(agent)})
    exported = seen.read_text().splitlines()
    expect("run-env", ("MARESTAIL_SCOPE=hyper" in exported, "MARESTAIL_FOCUS=" in exported), (True, True))


def python_edges(repo: Path) -> None:
    git(repo, "reset", "-q", "--hard", "base")
    git(repo, "clean", "-qfd", "-e", ".venv")
    code, output = gate(repo, SCOPE, HYPER)
    lines = output.splitlines()
    expect("empty-diff", (code, lines[0], lines[-1]), (0, "scope: hyper: 0 changed lines in 0 files", PASSED))
    contains("empty-diff-skips", output, "skipped: no changed python files", "skipped: no changed python sources")
    code, _, err = cli(repo, "gate", SCOPE, HYPER, "--focus", PY_FILE)
    expect("focus-error", (code, err), (2, FOCUS_ERROR + "\n"))
    _, out, _ = cli(repo, "gate", "--help")
    contains("help-lists-hyper", out, "{all,changed,hard,hyper}")


def typescript_checks(repo: Path) -> None:
    source = repo / TS_FILE
    set_line(source, 6, "  return 2 * price;")
    code, output = gate(repo, SCOPE, HYPER)
    expect(
        "ts-hyper-passes", (code, output.splitlines()[0], output.splitlines()[-1]), (0, "scope: hyper: 1 changed lines in 1 files", PASSED)
    )
    lacks("ts-hyper-old-lines", output, f"{TS_FILE}:1 ", f"{TS_FILE}:2 ", f"{TS_FILE}:14 ")
    code, output = gate(repo, SCOPE, "changed")
    expect("ts-changed-fails", code, 1)
    contains("ts-changed-old", output, f"{TS_FILE}:2 comment: // legacy note")
    code, output = gate(repo, SCOPE, "hard", "--focus", TS_FILE)
    expect("ts-hard-fails", code, 1)
    set_line(source, 6, "  return price == null ? 0 : 2 * price;")
    code, output = gate(repo, SCOPE, HYPER, ONLY, "ts.lint")
    expect("ts-lint", (code, results(output, "ts.lint")), (1, [f"{TS_FILE}:6 eqeqeq: Expected '===' and instead saw '=='."]))
    restore(repo, TS_FILE)
    set_line(source, 10, '  return "item: " + name;')
    code, output = gate(repo, SCOPE, HYPER, ONLY, "ts.mutation")
    expect("ts-mutation", (code, results(output, "ts.mutation")), (1, [f'{TS_FILE}:10 StringLiteral Survived: ""']))
    contains("ts-mutation-summary", header(output, "ts.mutation"), " 1 surviving mutants ")
    report = json.loads((repo / "reports" / "mutation" / "mutation.json").read_text())
    lines = {mutant["location"]["start"]["line"] for data in report["files"].values() for mutant in data["mutants"]}
    expect("ts-mutation-range", lines, {10})
    restore(repo, TS_FILE)


def typescript_fail_closed(repo: Path) -> None:
    set_line(repo / TS_FILE, 6, "  return 2 * price;")
    (repo / "eslint.config.js").write_text('export default [{rules: {"eqeqeq": }}];\n')
    code, output = gate(repo, SCOPE, HYPER, ONLY, "ts.lint")
    findings = results(output, "ts.lint")
    silent = "marestail.toml:1 eslint exited 2 without a message"
    expect("eslint-crash", (code, bool(findings), all(line.startswith("eslint: ") or line == silent for line in findings)), (1, True, True))
    restore(repo, "eslint.config.js")
    with (repo / "marestail.toml").open("a") as toml:
        toml.write('tsconfig = "missing.json"\n')
    code, output = gate(repo, SCOPE, HYPER, ONLY, "ts.lint")
    expect(
        "tsconfig-diagnostic",
        (code, results(output, "ts.lint")),
        (1, ["marestail.toml:1 [ts] tsconfig = 'missing.json' does not exist under ."]),
    )
    restore(repo, "marestail.toml", TS_FILE)


class StubClient:
    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.posts: list[dict[str, Any]] = []

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        if path == "api/issues/search":
            return {"issues": self.replies["accepted" if "issueStatuses" in params else "open"]}
        if path == "api/measures/component":
            return {"component": {"measures": self.replies["measures"]}}
        return {
            "api/qualitygates/project_status": {"projectStatus": {"status": "ERROR"}},
            "api/hotspots/search": {"hotspots": self.replies["hotspots"]},
        }[path]

    def post(self, path: str, **params: Any) -> dict[str, Any]:
        self.posts.append(params)
        return {}


def sonar_run(folder: Path, replies: dict[str, Any], raw: dict[str, Any]) -> tuple[Any, StubClient]:
    client = StubClient(replies)
    sonar.credentials = lambda: {"url": "http://stub", "token": "t"}
    sonar.Client = lambda url, token: client
    sonar.scan = lambda *args: (0, "", folder / "task")
    sonar.wait_for_analysis = lambda *args: None
    config = Config(root=folder, raw={"sonar": {"project_key": "proj"}, **raw})
    ctx = Context(config=config, scope_changed=True, hyper=True, changed={PY_FILE}, changed_lines_map={PY_FILE: {1, 6}})
    return sonar.run_gate(ctx), client


def sonar_checks(folder: Path) -> None:
    component = f"proj:{PY_FILE}"
    replies = {
        "accepted": [
            {"key": "A1", "component": component, "line": 6, "rule": "python:S3", "issueStatus": "ACCEPTED"},
            {"key": "A2", "component": component, "line": 9, "rule": "python:S4", "issueStatus": "FALSE_POSITIVE"},
        ],
        "open": [
            {"component": component, "line": 2, "severity": "MAJOR", "rule": "python:S1481", "message": "old"},
            {"component": component, "line": 6, "severity": "MAJOR", "rule": "python:S1481", "message": "new"},
            {"component": component, "severity": "MINOR", "rule": "python:S1451", "message": "Add a header"},
        ],
        "hotspots": [
            {"component": component, "line": 1, "message": "check import"},
            {"component": component, "line": 9, "message": "old hotspot"},
        ],
        "measures": [],
    }
    result, client = sonar_run(folder, replies, {})
    expect("sonar-reopen", client.posts, [{"issue": "A1", "transition": "reopen"}])
    expect(
        "sonar-findings",
        result.findings,
        [
            f"{PY_FILE}:6 python:S3 was marked ACCEPTED in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties",
            f"{PY_FILE}:6 MAJOR python:S1481: new",
            f"{PY_FILE}:1 hotspot: check import",
        ],
    )
    summary = (
        "3 sonar findings in scope (global quality gate ERROR; scope: hyper: 2 changed lines in 1 files; "
        "1 file-level findings not gated under hyper; duplication not gated under hyper)"
    )
    expect("sonar-summary", result.summary, summary)
    empty = {"accepted": [], "open": [], "hotspots": [], "measures": [{"metric": "ncloc_language_distribution", "value": "java=40;py=12"}]}
    result, _ = sonar_run(folder, empty, {"java": {}})
    coverage = "marestail.toml:1 SonarQube imported no java coverage; run java.tests first so .marestail/java-jacoco.xml exists"
    expect("sonar-diagnostic", (result.ok, result.findings, "file-level" in result.summary), (False, [coverage], False))


if __name__ == "__main__":
    for tool in ("uv", "npm", "git"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is needed to build the fixtures")
    with tempfile.TemporaryDirectory(prefix="marestail-hyper-scope-") as temp:
        folder = Path(temp)
        sonar_checks(folder)
        python = python_fixture(folder)
        python_happy_path(python)
        restore(python, PY_FILE)
        python_changed_line_findings(python)
        python_mutation(python)
        python_fail_closed(python)
        python_hooks_and_runs(python, folder)
        python_edges(python)
        typescript = ts_fixture(folder)
        typescript_checks(typescript)
        typescript_fail_closed(typescript)
    print("hyper scope ok")
