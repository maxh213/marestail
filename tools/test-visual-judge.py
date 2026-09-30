#!/usr/bin/env python3
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLI = ROOT / "marestail" / "cli.py"
STUB = HERE / "stub-claude"


def load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VISUAL_TEST = load("visual_test", "test-visual.py")
TIMELINE_TEST = load("timeline_test", "test-timeline.py")
expect, expect_true, git, write = VISUAL_TEST.expect, VISUAL_TEST.expect_true, VISUAL_TEST.git, VISUAL_TEST.write

VISUAL = VISUAL_TEST.changed(viewports='{ desktop = "1440x900", phone = "390x844@2 touch" }')
BLOCK = {**VISUAL_TEST.BLOCK, "symptom": "the widget is 440px wide"}
FEATURE = "Feature: t\n  Scenario: Adds one\n    Given x\n  Scenario Outline: Rejects bad input\n    Given y\n"
PICTURES = [
    f"- .marestail/runs/t/visual/{tree}/{view}/{shot}.png"
    for view in ("desktop", "phone")
    for tree in ("base", "head")
    for shot in ("element", "viewport")
]
GEOMETRY_ROWS = [
    "| desktop | x | 434 | 434 |",
    "| desktop | width | 440 | 440 |",
    "| desktop | scrollWidth | 1440 | 1440 |",
    "| desktop | overlaps | none | none |",
    "| phone | x | 0 | 0 |",
    "| phone | scrollWidth | 572 | 572 |",
]
BLIND_LINE = "The pictures could not be shown: kilo cannot read images. Judge from the geometry table alone."
WRAPPER = """#!/bin/sh
nn=$(printf %02d $(( $(ls "$ARGS" | wc -l) + 1 )))
echo "$(basename "$0") $*" > "$ARGS/$nn.txt"
cat > "$PROMPTS/$nn.txt"
"$STUB" < "$PROMPTS/$nn.txt"
"""
ROLE_LINE = re.compile(r"^You are (?:the |an? )?(\w+)")
FULL_RUN = ("--from", "hardener", "--to", "qa", "--auto", "--retries", "2")


def toml(visual: dict[str, str] | None) -> str:
    text = '[git]\nbase = "main"\n\n[qa]\ncmd = "true"\n\n[practices]\nenabled = false\n\n[perf]\nenabled = false\n'
    if visual is None:
        return text
    return text + "\n[visual]\n" + "".join(f"{key} = {value}\n" for key, value in visual.items())


class Bed:
    def __init__(self, folder: Path, visual: dict[str, str] | None = VISUAL) -> None:
        self.folder = folder
        self.root = VISUAL_TEST.fixture(folder, visual=visual, block=BLOCK)
        write(self.root, "marestail.toml", toml(visual))
        write(self.root, "tasks/t.md", "# Change the page text\n")
        write(self.root, "features/t.feature", FEATURE)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "task")
        self.args = folder / "args"
        self.prompts = folder / "prompts"
        self.args.mkdir()
        self.prompts.mkdir()
        self.bins = self.wrappers()
        self.out = ""
        self.code = -1

    def wrappers(self) -> dict[str, Path]:
        wrapper = write(self.folder, "bin/capture", WRAPPER)
        wrapper.chmod(0o755)
        names = {"MARESTAIL_CLAUDE": "claude", "MARESTAIL_CURSOR": "cursor-agent", "MARESTAIL_KILO": "kilo"}
        links = {key: self.folder / "bin" / name for key, name in names.items()}
        for link in links.values():
            link.symlink_to(wrapper)
        return links

    def run(self, plan: list[str], *args: str, env: dict[str, str] | None = None) -> str:
        plan_path = write(self.folder, "plan.txt", "".join(line + "\n" for line in plan))
        merged = {**os.environ, **{key: str(path) for key, path in self.bins.items()}}
        merged.update(
            {
                "STUB": str(STUB),
                "STUB_PLAN": str(plan_path),
                "ARGS": str(self.args),
                "PROMPTS": str(self.prompts),
                "MARESTAIL_LIMIT_WAIT_SECONDS": "0",
                "PATH": f"{ROOT / 'bin'}:{os.environ['PATH']}",
                **(env or {}),
            }
        )
        for key in ("MARESTAIL_TASK", "MARESTAIL_AGENT"):
            merged.pop(key, None)
        completed = subprocess.run(
            [sys.executable, str(CLI), "run", "tasks/t.md", *args], cwd=self.root, capture_output=True, text=True, check=False, env=merged
        )
        self.out, self.code = completed.stdout, completed.returncode
        self.err = completed.stderr
        return self.out

    def arg(self, number: int) -> str:
        return (self.args / f"{number:02d}.txt").read_text()

    def prompt(self, number: int) -> str:
        return (self.prompts / f"{number:02d}.txt").read_text()

    def invocations(self) -> list[str]:
        return [role_of(path.read_text()) for path in sorted(self.prompts.glob("*.txt"))]

    def headers(self) -> list[str]:
        return re.findall(r"^== (\w+) \(", self.out, re.MULTILINE)

    def subjects(self, needle: str) -> list[str]:
        return [line for line in git(self.root, "log", "--format=%s").splitlines() if needle in line]

    def last_line(self) -> str:
        return self.out.strip().splitlines()[-1]


def role_of(prompt: str) -> str:
    matched = ROLE_LINE.match(prompt)
    return matched.group(1).lower() if matched else ""


def verdict_files(bed: Bed, subject: str) -> str:
    sha = git(bed.root, "log", "--format=%H", f"--grep={subject}", "-F", "-1")
    return str(git(bed.root, "show", "--format=", "--name-only", sha))


def case_between_hardener_and_qa(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(["judge PASS", "judge PASS", "worker qa"], *FULL_RUN)
    expect("order", bed.headers(), ["hardener", "visual", "qa"])
    expect_true("judge-model", "--model claude-fable-5-1" in bed.arg(2) and "--effort" not in bed.arg(2))
    lines = bed.prompt(2).splitlines()
    expect_true("visual-heading", lines.index("# Visual") < lines.index("Symptom: the widget is 440px wide"))
    expect("pictures", [line for line in lines if line.startswith("- .marestail/runs/t/visual/")], PICTURES)
    expect_true("pictures-exist", all((bed.root / line[2:]).is_file() for line in PICTURES))
    expect("geometry", [row for row in GEOMETRY_ROWS if row not in lines], [])
    gate_lines = [line for line in lines if re.match(r"^\[(ok  |FAIL)\]", line)]
    expect("gate-lines", [line.split("  (")[0].split()[:3] for line in gate_lines], [["[ok", "]", "visual"]])
    expect("verdict-subject", bed.subjects("visual verdict"), ["[claude-fable-5-1] visual verdict: PASS"])
    expect("verdict-files", verdict_files(bed, "visual verdict"), "")
    expect("ending", (bed.code, bed.last_line()), (0, "pipeline complete"))


def case_skips(folder: Path) -> None:
    cases: list[tuple[str, Callable[[Path], object], str]] = [
        ("no-block", lambda root: VISUAL_TEST.set_block(root, None), "visual: no block in qa/t.md; skipping"),
        ("no-qa", lambda root: (root / "qa" / "t.md").unlink(), "visual: no qa/t.md; skipping"),
        (
            "disabled",
            lambda root: write(root, "marestail.toml", toml({**VISUAL, "enabled": "false"})),
            "visual disabled in marestail.toml; skipping",
        ),
        ("no-section", lambda root: write(root, "marestail.toml", toml(None)), ""),
    ]
    for name, change, line in cases:
        bed = Bed(folder / name)
        change(bed.root)
        git(bed.root, "add", "-A")
        git(bed.root, "commit", "-qm", name)
        bed.run(["judge PASS", "worker qa"], *FULL_RUN)
        visual_lines = [text for text in bed.out.splitlines() if "visual" in text]
        expect(f"{name}-shows", visual_lines, [line] if line else [])
        expect(f"{name}-order", bed.headers(), ["hardener", "qa"])
        expect(f"{name}-prompts", (len(bed.invocations()), bed.code), (2, 0))


def case_role_list_without_section(folder: Path) -> None:
    bed = Bed(folder, visual=None)
    bed.run([], "--from", "visual", "--auto")
    wanted = "unknown role visual; choose from specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa"
    expect_true("unknown-role", bed.code == 1 and wanted in bed.err)


def case_verdicts(folder: Path) -> None:
    for verdict, step, role in (
        ("BOUNCE coder", "code", "coder"),
        ("BOUNCE specifier", "worker specifier", "specifier"),
        ("BOUNCE hardener", "code", "coder"),
    ):
        bed = Bed(folder / role / verdict.split()[1])
        bed.run(["judge PASS", f"judge {verdict}", step, "judge PASS", "worker qa"], *FULL_RUN)
        expect(f"{verdict}-invocations", bed.invocations(), ["hardener", "visual", role, "visual", "qa"])
        expect_true(f"{verdict}-judge-model", "--model claude-fable-5-1" in bed.arg(4))
        expect(f"{verdict}-exit", bed.code, 0)


def case_failing_gate(folder: Path) -> None:
    bed = Bed(folder)
    VISUAL_TEST.edit(bed.root, VISUAL_TEST.widget("width:1408px"))
    write(bed.root, ".marestail/runs/t/start-commit", git(bed.root, "rev-parse", "main") + "\n")
    bed.run(["judge PASS", "code", "judge PASS", "worker coder"], "--from", "visual", "--to", "visual", "--auto", "--retries", "1")
    first = bed.out.index("== visual (")
    expect_true("forced-bounce", bed.out.index("   verdict BOUNCE", first) > first)
    expect("gate-invocations", bed.invocations(), ["visual", "coder", "visual", "coder"])
    expect_true("coder-sees-gate", "[FAIL] visual" in bed.prompt(2))
    expect_true("coder-sees-finding", "desktop: #widget is 1408px wide, .col is 572px (base: 440px)" in bed.prompt(2))
    expect_true("no-stall", "repeated the same findings" not in bed.out)
    expect("gate-ending", (bed.last_line(), bed.code), ("pipeline stopped at visual", 1))


def case_other_backend(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(["judge PASS", "judge PASS", "worker qa"], *FULL_RUN, "--agent", "cursor", "--model", "gpt-9")
    for number in (1, 3):
        expect_true(f"cursor-{number}", bed.arg(number).startswith("cursor-agent") and "--model gpt-9" in bed.arg(number))
    expect_true("claude-judge", bed.arg(2).startswith("claude") and "--model claude-fable-5-1" in bed.arg(2))
    expect("visual-stamp", bed.subjects("visual verdict"), ["[claude-fable-5-1] visual verdict: PASS"])
    expect("hardener-stamp", bed.subjects("hardener verdict"), ["[cursor/gpt-9] hardener verdict: PASS"])


def case_configured_judge_model(folder: Path) -> None:
    bed = Bed(folder, visual={**VISUAL, "judge_model": '"claude-opus-5-5"'})
    bed.run(["judge PASS"], "--from", "visual", "--to", "visual", "--auto")
    expect_true("configured-model", "--model claude-opus-5-5" in bed.arg(1))


def visual_step(bed: Bed) -> dict[str, Any]:
    steps = json.loads((bed.root / ".marestail" / "runs" / "t" / "timeline.json").read_text())["steps"]
    return next(step for step in steps if step["role"] == "visual")


def case_out_of_usage(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(["limit", "judge PASS"], "--from", "visual", "--to", "visual", "--agent", "cursor", "--model", "gpt-9", "--auto")
    expect_true("fallback-line", "visual: claude-fable-5-1 is out of usage; judging with cursor gpt-9" in bed.out)
    expect_true("no-wait", "rate limited; waiting" not in bed.out)
    expect_true("fallback-backend", bed.arg(2).startswith("cursor-agent") and "--model gpt-9" in bed.arg(2))
    expect("fallback-pictures", [line for line in bed.prompt(2).splitlines() if line.startswith("- .marestail")], PICTURES)
    step = visual_step(bed)
    expect("fallback-timeline", (step["waits"], step["agent"]["backend"], step["agent"]["model"]), ([], "cursor", "cursor/gpt-9"))
    expect("fallback-stamp", (bed.subjects("visual verdict"), bed.code), (["[cursor/gpt-9] visual verdict: PASS"], 0))


def case_blind_fallback(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(
        ["limit", "judge PASS", "worker qa"],
        "--from",
        "visual",
        "--to",
        "qa",
        "--agent",
        "kilo",
        "--model",
        "kilo/x",
        "--auto",
        "--retries",
        "2",
    )
    expect_true("blind-line", "visual: claude-fable-5-1 is out of usage; judging with kilo kilo/x" in bed.out)
    prompt = bed.prompt(2)
    expect_true("blind-prompt", BLIND_LINE in prompt and "| desktop | x | 434 | 434 |" in prompt and ".png" not in prompt)
    expect_true("blind-verdict", "   verdict PASS (geometry only, pictures not seen)" in bed.out)
    expect("blind-stamp", bed.subjects("visual verdict"), ["[kilo/x] visual verdict: PASS (geometry only, pictures not seen)"])
    expect("blind-ending", (bed.last_line(), bed.code), ("pipeline complete, NOT verified by eye", 3))


def case_dandelion(folder: Path) -> None:
    bed = Bed(folder)
    binary = TIMELINE_TEST.stub_dandelion(folder)
    plan = write(folder, "dandelion-plan", "0 gpt-9 cursor\n0 gpt-9 cursor\n")
    calls = write(folder, "calls", "")
    env = {"MARESTAIL_DANDELION": str(binary), "PLAN": str(plan), "CALLS": str(calls)}
    bed.run(
        ["limit", "judge PASS", "worker qa"],
        "--from",
        "visual",
        "--to",
        "qa",
        "--model",
        "dandelion/route",
        "--auto",
        "--retries",
        "2",
        env=env,
    )
    expect_true("route-first", bed.arg(1).startswith("claude") and "--model claude-fable-5-1" in bed.arg(1))
    fallback = bed.out.index("visual: claude-fable-5-1 is out of usage; judging with dandelion/route")
    expect_true("route-order", fallback < bed.out.index("   dandelion/route: gpt-9 cursor"))
    for number in (2, 3):
        expect_true(f"route-{number}", bed.arg(number).startswith("cursor-agent") and "claude-fable-5-1" not in bed.arg(number))
    expect_true("route-model", "--model gpt-9" in bed.arg(2))
    expect("route-calls", len(calls.read_text().splitlines()), 2)
    expect("route-stamp", (bed.subjects("visual verdict"), bed.code), (["[cursor/gpt-9] visual verdict: PASS"], 0))


def case_other_waits(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(["limit", "judge PASS"], "--from", "hardener", "--to", "hardener", "--auto")
    expect_true("hardener-waits", "rate limited; waiting 0 min before retrying" in bed.out and "out of usage" not in bed.out)


def case_roles_and_readme(_folder: Path) -> None:
    role = (ROOT / "roles" / "visual.md").read_text()
    expect_true("role", role.startswith("You are the visual judge.\n\nLook at the before and after pictures for each viewport."))
    readme = (ROOT / "README.md").read_text()
    lines = readme.splitlines()
    row = next(index for index, line in enumerate(lines) if line.startswith("| visual | judge | visual | visual |"))
    expect_true("readme-row", lines[row - 1].startswith("| hardener |") and lines[row + 1].startswith("| qa |"))
    paragraph = next(line for line in lines if "`judge_model`" in line)
    for needle in ("claude-fable-5-1", "at once", "NOT verified by eye"):
        expect_true(f"readme-{needle}", needle in paragraph)


CASES = [
    case_roles_and_readme,
    case_role_list_without_section,
    case_between_hardener_and_qa,
    case_skips,
    case_verdicts,
    case_failing_gate,
    case_other_backend,
    case_configured_judge_model,
    case_out_of_usage,
    case_blind_fallback,
    case_dandelion,
    case_other_waits,
]


def main() -> None:
    wanted = set(sys.argv[1:])
    for case in CASES:
        if wanted and case.__name__ not in wanted:
            continue
        with tempfile.TemporaryDirectory(prefix="marestail-visual-judge-test-") as folder:
            started = time.time()
            case(Path(folder))
            print(f"{case.__name__} ok ({time.time() - started:.0f}s)", flush=True)
    print("visual judge ok")


if __name__ == "__main__":
    main()
