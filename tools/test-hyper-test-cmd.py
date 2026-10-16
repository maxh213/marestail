#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "marestail" / "cli.py"
TOOLING = ".marestail/tooling"
TEST_CMD = 'for f in client/embed/*.test.js; do node \\"$f\\" || exit 1; done'
CONFIG = f'[git]\nbase = "main"\n\n[ts]\nroot = "."\ntooling = "{TOOLING}"\n'
HYPER = f'\n[hyper]\ntest_cmd = "{TEST_CMD}"\n'
POP_UP = """function popUpUrl(base, id) {
  var url = base + "/embed/" + id;
  return url;
}

function isOpen(state) {
  return state === "open" || state === "shown";
}

module.exports = { popUpUrl: popUpUrl, isOpen: isOpen };
"""
SIZE = """function sizeOf(kind) {
  if (kind === "wide") return 800;
  if (kind === "tall") return 600;
  var fallback = 400;
  return fallback;
}

module.exports = { sizeOf: sizeOf };
"""
LOADER = """const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");
function load(file) {
  const sandbox = SANDBOX;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, file), "utf8"), sandbox);
  return sandbox.module.exports;
}
function it(name, fn) {
  fn();
  console.log("ok - " + name);
}
"""
WITH_PROCESS = "{ module: { exports: {} }, process }"
WITHOUT_PROCESS = "{ module: { exports: {} } }"
IS_OPEN_CASES = """const { isOpen } = load("popUp.js");
it("opening is open", () => assert.strictEqual(isOpen("opening"), OPENING));
it("open is open", () => assert.strictEqual(isOpen("open"), true));
it("closed is closed", () => assert.strictEqual(isOpen("closed"), false));
"""
UNASSERTED_CASES = """const { isOpen } = load("popUp.js");
it("opening is open", () => isOpen("opening"));
it("open is open", () => isOpen("open"));
it("closed is closed", () => isOpen("closed"));
"""
UNPROVABLE_CASES = """it("url", () => assert.strictEqual(load("popUp.js").popUpUrl("https://x", "a"), "https://x/embed/a"));
it("size", () => assert.strictEqual(load("size.js").sizeOf("square"), 400));
"""
FAKE_VITEST = "#!/bin/sh\necho vitest ran\nexit 1\n"
GATES = "ts.tests,ts.crap,ts.mutation"
ADVISORY = "[ok  ] ts.tests       test_cmd exited 0; coverage advisory: not measured with [hyper] test_cmd"
PROOF = "proof: mutation via [hyper] test_cmd; coverage not measured"
NO_RUNNER = "[FAIL] ts.tests       hyper: set [hyper] test_cmd to the command that runs this repository's tests"
HINT = (
    "hint: no mutant was killed; a test that loads code with vm must pass process into the sandbox, "
    "or no assertion depends on the changed lines"
)


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def install_tooling(folder: Path) -> None:
    folder.mkdir(parents=True)
    completed = subprocess.run(
        ["npm", "install", "--silent", "--no-audit", "--no-fund", "--prefix", str(folder), "@stryker-mutator/core@9", "typescript@5"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"npm install failed: {completed.stdout}{completed.stderr}")


def test_file(cases: str, sandbox: str = WITH_PROCESS) -> str:
    return LOADER.replace("SANDBOX", sandbox) + cases


def asserting(opening: str = "true", sandbox: str = WITH_PROCESS) -> str:
    return test_file(IS_OPEN_CASES.replace("OPENING", opening), sandbox)


class Sample:
    def __init__(self, tmp: Path, shared: Path, name: str) -> None:
        self.root = tmp / name
        (self.root / "client" / "embed").mkdir(parents=True)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "q@a")
        git(self.root, "config", "user.name", "qa")
        git(self.root, "config", "commit.gpgsign", "false")
        (self.root / ".git" / "info" / "exclude").write_text(".marestail/\nmarestail.toml\n")
        (self.root / "marestail.toml").write_text(CONFIG + HYPER)
        self.write("client/embed/popUp.js", POP_UP)
        self.write("client/embed/size.js", SIZE)
        git(self.root, "add", "client")
        git(self.root, "commit", "-qm", "seed")
        git(self.root, "checkout", "-qb", "fix")
        (self.root / ".marestail").mkdir()
        (self.root / TOOLING).symlink_to(shared)

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_text(text)

    def edit(self, name: str, line: int, text: str) -> None:
        lines = (self.root / name).read_text().split("\n")
        lines[line - 1] = text
        self.write(name, "\n".join(lines))

    def open_fix(self, test: str) -> None:
        self.edit("client/embed/popUp.js", 7, '  return state === "open" || state === "opening";')
        self.write("client/embed/popUp.test.js", test)

    def gate(self, *args: str) -> tuple[int, list[str]]:
        env = {key: value for key, value in os.environ.items() if key != "MARESTAIL_GATE_ACTIVE"}
        completed = subprocess.run(
            [sys.executable, str(CLI), "gate", "--scope", "hyper", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        return completed.returncode, completed.stdout.splitlines()


def line_of(lines: list[str], start: str) -> str:
    return next((line for line in lines if line.startswith(start)), "")


def findings_of(lines: list[str], gate: str) -> list[str]:
    head = next(index for index, line in enumerate(lines) if line[7:].startswith(gate + " "))
    found = []
    for line in lines[head + 1 :]:
        if not line.startswith("       "):
            break
        found.append(line.strip())
    return found


def summary_of(lines: list[str], gate: str) -> str:
    line = next(line for line in lines if line[7:].startswith(gate + " "))
    return line[:6] + " " + line[7:].split("  (")[0][15:]


def asserted_change_passes(sample: Sample) -> None:
    sample.open_fix(asserting())
    code, lines = sample.gate("--tier", "full", "--only", GATES)
    scope = next(index for index, line in enumerate(lines) if line.startswith("scope: hyper: "))
    expect("pass-proof-line", lines[scope + 1 : scope + 3], [PROOF, ""])
    expect("pass-tests", line_of(lines, ADVISORY) != "", True)
    expect("pass-mutation", summary_of(lines, "ts.mutation"), "[ok  ] all mutants killed (proof: mutation via [hyper] test_cmd)")
    expect("pass-crap", summary_of(lines, "ts.crap"), "[ok  ] 1 innermost changed functions, 0 above CRAP 4, 0 of them no worse than base")
    expect("pass-verdict", (lines[-1], code), ("GATE PASSED", 0))
    status = sorted(line[3:] for line in git(sample.root, "status", "--porcelain").splitlines())
    expect("pass-status", status, ["client/embed/popUp.js", "client/embed/popUp.test.js"])
    leftovers = [name for name in ("package.json", "reports", ".stryker-tmp") if (sample.root / name).exists()]
    expect("pass-leftovers", leftovers, [])


def unasserted_change_fails(sample: Sample) -> None:
    sample.open_fix(test_file(UNASSERTED_CASES))
    code, lines = sample.gate("--tier", "full", "--only", GATES)
    expect("unasserted-tests", line_of(lines, ADVISORY) != "", True)
    summary = summary_of(lines, "ts.mutation")
    expect(
        "unasserted-mutation",
        (summary[:7], summary.endswith(" surviving mutants (proof: mutation via [hyper] test_cmd)")),
        ("[FAIL] ", True),
    )
    findings = findings_of(lines, "ts.mutation")
    expect("unasserted-hint", findings[0], HINT)
    expect("unasserted-lines", [finding.startswith("client/embed/popUp.js:7 ") for finding in findings[1:]], [True] * (len(findings) - 1))
    expect("unasserted-unchanged", [line for line in lines if "popUp.js:2" in line or "popUp.js:3" in line], [])
    crap = ["client/embed/popUp.js:6 isOpen crap=6.0 (cc=2, coverage=0%); changed lines not covered: 7"]
    expect("unasserted-crap", (summary_of(lines, "ts.crap")[:6], findings_of(lines, "ts.crap")), ("[FAIL]", crap))
    expect("unasserted-verdict", (lines[-1], code), ("GATE FAILED: ts.crap, ts.mutation", 1))


def vm_without_process_kills_nothing(sample: Sample) -> None:
    sample.open_fix(asserting(sandbox=WITHOUT_PROCESS))
    code, lines = sample.gate("--tier", "full", "--only", "ts.tests,ts.mutation")
    expect("vm-tests", line_of(lines, ADVISORY) != "", True)
    expect("vm-mutation", summary_of(lines, "ts.mutation").endswith(" surviving mutants (proof: mutation via [hyper] test_cmd)"), True)
    expect("vm-hint", (findings_of(lines, "ts.mutation")[0], code), (HINT, 1))


def unprovable_lines_are_listed(sample: Sample) -> None:
    sample.edit("client/embed/popUp.js", 3, "  return String(url);")
    sample.edit("client/embed/popUp.js", 4, "};")
    sample.edit("client/embed/popUp.js", 9, "var loaded = 1;")
    sample.edit("client/embed/size.js", 5, "  return Number(fallback);")
    sample.write("client/embed/popUp.test.js", test_file(UNPROVABLE_CASES))
    _, lines = sample.gate("--tier", "full", "--only", GATES)
    expect("unprovable-mutation", summary_of(lines, "ts.mutation"), "[ok  ] no mutants on changed lines")
    summary = "0 innermost changed functions, 0 above CRAP 4, 0 of them no worse than base; 2 changed lines not provable by mutation"
    expect("unprovable-crap", summary_of(lines, "ts.crap"), "[ok  ] " + summary)
    wanted = ["client/embed/popUp.js:3 not provable by mutation", "client/embed/size.js:5 not provable by mutation"]
    expect("unprovable-findings", findings_of(lines, "ts.crap"), wanted)
    expect("unprovable-verdict", lines[-1], "GATE PASSED")


def only_a_test_skips_stryker(sample: Sample) -> None:
    sample.write("client/embed/popUp.test.js", asserting())
    _, lines = sample.gate("--tier", "full", "--only", GATES)
    expect("skip-mutation", summary_of(lines, "ts.mutation"), "[ok  ] skipped: no changed typescript sources")
    expect("skip-crap", summary_of(lines, "ts.crap"), "[ok  ] skipped: no files in scope")


def failing_test_cmd_fails_tests(sample: Sample) -> None:
    sample.open_fix(asserting(opening="false"))
    code, lines = sample.gate("--tier", "full", "--only", "ts.tests")
    expect("failing-summary", summary_of(lines, "ts.tests"), "[FAIL] test_cmd exited 1")
    expect("failing-assertion", any("AssertionError" in finding for finding in findings_of(lines, "ts.tests")), True)
    expect("failing-exit", code, 1)


def no_test_cmd_and_no_runner_fails(sample: Sample) -> None:
    (sample.root / "marestail.toml").write_text(CONFIG)
    code, lines = sample.gate("--tier", "fast", "--only", "ts.tests")
    expect("no-runner", (line_of(lines, NO_RUNNER) != "", any(line.startswith("proof:") for line in lines), code), (True, False, 1))


def tooling_runner_does_not_count(sample: Sample) -> None:
    (sample.root / "marestail.toml").write_text(CONFIG)
    (sample.root / TOOLING).unlink()
    binaries = sample.root / TOOLING / "node_modules" / ".bin"
    binaries.mkdir(parents=True)
    (binaries / "vitest").write_text(FAKE_VITEST)
    (binaries / "vitest").chmod(0o755)
    (sample.root / TOOLING / "vitest.config.ts").write_text("export default {};\n")
    code, lines = sample.gate("--tier", "fast", "--only", "ts.tests")
    expect("tooling-hyper", (line_of(lines, NO_RUNNER) != "", "vitest ran" in "\n".join(lines), code), (True, False, 1))
    completed = subprocess.run(
        [sys.executable, str(CLI), "gate", "--scope", "changed", "--tier", "fast", "--only", "ts.tests"],
        cwd=sample.root,
        capture_output=True,
        text=True,
        check=False,
    )
    expect("tooling-diff", ("vitest ran" in completed.stdout, "hyper: set [hyper] test_cmd" in completed.stdout), (True, False))


def missing_stryker_fails(sample: Sample) -> None:
    sample.open_fix(asserting())
    (sample.root / TOOLING).unlink()
    (sample.root / TOOLING).mkdir()
    code, lines = sample.gate("--tier", "full", "--only", "ts.mutation")
    expect("missing-stryker", (summary_of(lines, "ts.mutation"), code), ("[FAIL] stryker produced no report (exit 127)", 1))


def readme_documents_test_cmd() -> None:
    text = (ROOT / "README.md").read_text()
    phrases = [
        "[hyper]\ntest_cmd = ",
        "run with a command the repository already supports",
        "mutation stands in for coverage",
        "pass process into the sandbox",
        "a runner installed only in the tooling folder does not count",
    ]
    expect("readme", [phrase for phrase in phrases if phrase not in text], [])


CASES = [
    asserted_change_passes,
    unasserted_change_fails,
    vm_without_process_kills_nothing,
    unprovable_lines_are_listed,
    only_a_test_skips_stryker,
    failing_test_cmd_fails_tests,
    no_test_cmd_and_no_runner_fails,
    tooling_runner_does_not_count,
    missing_stryker_fails,
]


def main() -> int:
    if shutil.which("npm") is None or shutil.which("node") is None:
        raise SystemExit("node and npm must be on PATH")
    readme_documents_test_cmd()
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        shared = tmp / "tooling"
        install_tooling(shared)
        for case in CASES:
            case(Sample(tmp, shared, case.__name__))
            print(f"{case.__name__}: ok")
    print("hyper test_cmd ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
