#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.config import Config
from marestail.context import Context
from marestail.gates import ts_tests
from marestail.gates.ts_tests import run_gate


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def ctx_for(root: Path, runner: str = "playwright", sources=None) -> Context:
    raw = {"ts": {"root": ".", "runner": runner, "source": "src"}}
    if sources is not None:
        raw["ts"]["sources"] = sources
    return Context(config=Config(root=root, raw=raw))


def test_unknown_runner():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        result = run_gate(ctx_for(root, runner="mocha"))
        expect("unknown ok", result.ok, False)
        expect("unknown summary", "unknown [ts] runner" in result.summary, True)
        expect("unknown mentions playwright", "playwright" in result.summary, True)


def test_missing_playwright():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        result = run_gate(ctx_for(root))
        expect("missing playwright ok", result.ok, False)
        expect("missing playwright summary", result.summary, "playwright is not installed at node_modules/.bin/playwright; add @playwright/test")


def test_missing_c8():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        binary = root / "node_modules" / ".bin" / "playwright"
        binary.parent.mkdir(parents=True)
        binary.write_text("")
        result = run_gate(ctx_for(root))
        expect("missing c8 ok", result.ok, False)
        expect("missing c8 summary", result.summary, "c8 is not installed at node_modules/.bin/c8; add c8 so playwright coverage can be collected")


def test_playwright_command():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        ctx = ctx_for(root, sources=["components", "lib"])
        command = ts_tests.playwright_command(ctx)
        expect("c8 bin", command[0], str(root / "node_modules" / ".bin" / "c8"))
        expect("playwright bin", command[-4], str(root / "node_modules" / ".bin" / "playwright"))
        expect("playwright test", command[-3:], ["test", "--reporter=line", "--reporter=json"])
        expect("include components", "--include=components/**" in command, True)
        expect("include lib", "--include=lib/**" in command, True)
        expect("all", "--all" in command, True)
        expect("coverage dir", f"--reports-dir={root / '.marestail' / 'ts-coverage'}" in command, True)
        expect("coverage mkdir", (root / ".marestail" / "ts-coverage").is_dir(), True)
        expect("env", ts_tests.playwright_env(ctx), {ts_tests.PLAYWRIGHT_JSON: str(root / ".marestail" / "ts-tests.json")})


def test_playwright_failures():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        ctx = ctx_for(root)
        work = root / ".marestail"
        work.mkdir()
        spec_file = str(root / "tests" / "box.spec.ts")
        report = {
            "stats": {"expected": 0, "unexpected": 1},
            "errors": [{"message": "config boom\nmore", "location": {"file": str(root / "playwright.config.ts"), "line": 2}}],
            "suites": [
                {
                    "specs": [
                        {
                            "title": "small",
                            "ok": False,
                            "file": "box.spec.ts",
                            "line": 4,
                            "tests": [
                                {
                                    "status": "unexpected",
                                    "results": [
                                        {
                                            "status": "failed",
                                            "error": {
                                                "message": "Error: expect(received).toBe(expected)\n    at fail",
                                                "location": {"file": spec_file, "line": 5},
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    "suites": [
                        {
                            "specs": [
                                {
                                    "title": "nested",
                                    "ok": False,
                                    "file": "nested.spec.ts",
                                    "line": 8,
                                    "tests": [{"status": "unexpected", "results": [{"status": "timedOut"}]}],
                                }
                            ]
                        }
                    ],
                }
            ],
        }
        (work / "ts-tests.json").write_text(json.dumps(report))
        findings = ts_tests.playwright_failures(ctx)
        expect(
            "failures",
            findings,
            [
                "tests/box.spec.ts:5 small failed: Error: expect(received).toBe(expected)",
                "nested.spec.ts:8 nested failed: timedOut",
                "playwright.config.ts:2 config boom",
            ],
        )
        expect("count from json", ts_tests.count_tests("", ctx, "playwright"), "0")


def test_playwright_invalid_json():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        ctx = ctx_for(root)
        work = root / ".marestail"
        work.mkdir()
        (work / "ts-tests.json").write_text("{nope")
        expect(
            "invalid json",
            ts_tests.playwright_failures(ctx),
            ["ts-tests.json:1 playwright JSON report is not valid JSON"],
        )


def test_count_playwright_line():
    expect("line count", ts_tests.count_tests("  3 passed (666ms)"), "3")


if __name__ == "__main__":
    test_unknown_runner()
    test_missing_playwright()
    test_missing_c8()
    test_playwright_command()
    test_playwright_failures()
    test_playwright_invalid_json()
    test_count_playwright_line()
    print("ts playwright unit assertions OK")
