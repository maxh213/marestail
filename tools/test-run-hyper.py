#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLI = ROOT / "marestail" / "cli.py"
STUB = HERE / "stub-claude"
HYPER_ROLES = ["specifier", "critic", "coder", "architect", "hardener", "qa"]
ALL_ROLES = ["specifier", "critic", "coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"]
HYPER_PLAN = ["specify", "judge PASS", "code", "worker architect", "judge PASS", "worker qa"]
HARD_PLAN = ["specify", "judge PASS", "code", "worker cleaner", "worker architect", "judge PASS", "worker qa"]
TOML = '[git]\nbase = "main"\n\n[practices]\nenabled = false\n\n[perf]\nenabled = false\n'
CAPTURE = '#!/bin/sh\nn=$(printf \'%02d\' $(( $(ls "$PROMPTS" | wc -l) + 1 )))\ntee "$PROMPTS/$n.txt" | "{stub}"\n'
HARD_TEXT = "This run has a hard scope"
HYPER_ALL = (
    "This run is hyper-scoped. Make the smallest change that does what the task asks. Leave the code you touch a little "
    "better than you found it. Leave code the change does not touch exactly as it is, including code you would like to "
    "improve. The gates measure only the lines that change."
)
SENTENCES = {
    "specifier": "Write one scenario for the behaviour the task asks for, and regression scenarios only for behaviour the "
    "changed lines can reach.",
    "critic": "Bounce a scenario that would force a change outside the fix.",
    "coder": "Change as few lines as the fix needs. Prefer a small, well-named function over a longer inline condition. "
    "Write the tests the repository can already run, in the style it already uses. Write as many as you need.",
    "architect": "Apply the boy scout rule to the code this change touches, and only that code. If the function the fix "
    "lands in is long, split it. If the changed condition is hard to read, give it a name. Do not reshape, move or rename "
    "anything the change does not touch. Leave the dependency contracts as they are unless the change itself adds a "
    "dependency.",
    "hardener": "Judge the changed lines and their tests. Do not ask for clean-up, renames, or coverage of lines that did not change.",
}
FULL_LINE = "Run `marestail gate --tier full --scope hyper` and keep working until it prints GATE PASSED."
UNKNOWN_CLEANER = "unknown role cleaner; choose from " + ", ".join(HYPER_ROLES)


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_true(name: str, value: object) -> None:
    if not value:
        raise SystemExit(f"{name}: {value!r}")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def fixture(folder: Path) -> Path:
    root = folder / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    for relative, text in {
        "marestail.toml": TOML,
        ".gitignore": ".marestail/\n",
        "tasks/t.md": "# Add one\n",
        "src.py": "original\n",
    }.items():
        write(root, relative, text)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def environment(folder: Path) -> dict[str, str]:
    capture = folder / "capture"
    capture.write_text(CAPTURE.format(stub=STUB))
    capture.chmod(0o755)
    env = {**os.environ, "MARESTAIL_CLAUDE": str(capture), "STUB_PLAN": str(folder / "plan.txt"), "PROMPTS": str(folder / "prompts")}
    env["PATH"] = f"{ROOT / 'bin'}{os.pathsep}{env['PATH']}"
    for key in ("MARESTAIL_AGENT", "MARESTAIL_DANDELION", "MARESTAIL_SCOPE", "MARESTAIL_FOCUS"):
        env.pop(key, None)
    return env


def reset(env: dict[str, str], plan: list[str]) -> None:
    prompts = Path(env["PROMPTS"])
    subprocess.run(["rm", "-rf", str(prompts)], check=True)
    prompts.mkdir()
    Path(env["STUB_PLAN"]).write_text("".join(line + "\n" for line in plan))


def run(root: Path, env: dict[str, str], plan: list[str], *args: str) -> subprocess.CompletedProcess[str]:
    reset(env, plan)
    command = [sys.executable, str(CLI), "run", "tasks/t.md", *args]
    return subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=600)


def headed(stdout: str) -> list[str]:
    return [line.split()[1] for line in stdout.splitlines() if line.startswith("== ")]


def visited(stdout: str) -> list[str]:
    names = []
    for line in stdout.splitlines():
        if line.startswith("== ") or line.endswith("disabled in marestail.toml; skipping"):
            names.append(line.removeprefix("== ").split()[0])
    return names


def last_line(stdout: str) -> str:
    return [line for line in stdout.splitlines() if line.strip()][-1]


def saved(env: dict[str, str]) -> list[str]:
    return [path.read_text() for path in sorted(Path(env["PROMPTS"]).glob("*.txt"))]


def plan_left(env: dict[str, str]) -> str:
    return Path(env["STUB_PLAN"]).read_text()


def check_complete(name: str, result: subprocess.CompletedProcess[str]) -> None:
    expect(f"{name}-exit", (result.returncode, result.stderr[-400:]), (0, result.stderr[-400:]))
    expect(f"{name}-last", last_line(result.stdout), "pipeline complete")


def scope_section(prompt: str) -> str:
    return next(part for part in prompt.split("\n\n") if part.startswith("# Scope\n"))


def check_hyper_prompt(role: str, prompt: str) -> None:
    section = scope_section(prompt)
    expect_true(f"{role}-all-roles", HYPER_ALL in section)
    expect_true(f"{role}-own", SENTENCES.get(role, HYPER_ALL) in section)
    expect(f"{role}-others", [name for name, sentence in SENTENCES.items() if name != role and sentence in prompt], [])
    expect_true(f"{role}-no-hard", HARD_TEXT not in prompt)


def hyper_run(folder: Path) -> tuple[Path, dict[str, str]]:
    root, env = fixture(folder), environment(folder)
    result = run(root, env, HYPER_PLAN, "--scope", "hyper", "--auto", "--retries", "2")
    check_complete("hyper", result)
    expect("hyper-roles", headed(result.stdout), HYPER_ROLES)
    expect("hyper-visited", visited(result.stdout), HYPER_ROLES)
    prompts = saved(env)
    expect("hyper-prompts", len(prompts), 6)
    expect("hyper-plan", plan_left(env), "")
    for role, prompt in zip(HYPER_ROLES, prompts, strict=True):
        check_hyper_prompt(role, prompt)
    for prompt in prompts[2:4]:
        expect_true("full-tier", FULL_LINE in prompt and "--tier fast" not in prompt and "--tier sonar" not in prompt)
    expect_true("architect-boy-scout", "Do not reshape, move or rename anything the change does not touch" in prompts[3])
    return root, env


def missing_roles(root: Path, env: dict[str, str]) -> None:
    for flag in ("--from", "--to"):
        result = run(root, env, ["code"], "--scope", "hyper", flag, "cleaner", "--auto")
        expect(f"hyper{flag}-exit", result.returncode, 1)
        expect_true(f"hyper{flag}-error", UNKNOWN_CLEANER in result.stderr)
        expect(f"hyper{flag}-prompts", (saved(env), plan_left(env)), ([], "code\n"))


def bogus_roles(folder: Path) -> None:
    root, env = fixture(folder), environment(folder)
    for flag in ("--from", "--to"):
        result = run(root, env, ["code"], flag, "bogus", "--auto")
        expect(f"bogus{flag}-exit", result.returncode, 1)
        expect_true(f"bogus{flag}-error", "unknown role bogus; choose from " + ", ".join(ALL_ROLES) in result.stderr)
        expect_true(f"bogus{flag}-incomplete", "pipeline complete" not in result.stdout)
        expect(f"bogus{flag}-prompts", (saved(env), plan_left(env)), ([], "code\n"))


def partial_runs(root: Path, env: dict[str, str]) -> None:
    for start, stop, plan in (("coder", "architect", ["code", "worker architect"]), ("hardener", "qa", ["judge PASS", "worker qa"])):
        result = run(root, env, plan, "--scope", "hyper", "--from", start, "--to", stop, "--auto", "--retries", "2")
        expect(f"partial-{start}", (headed(result.stdout), result.returncode), ([start, stop], 0))


def refused_bounce(root: Path, env: dict[str, str]) -> None:
    plan = ["judge BOUNCE cleaner", "code", "judge PASS"]
    result = run(root, env, plan, "--scope", "hyper", "--from", "hardener", "--to", "hardener", "--auto", "--retries", "2")
    check_complete("bounce", result)
    expect("bounce-roles", headed(result.stdout), ["hardener", "coder", "hardener"])
    expect_true("bounce-coder-full", FULL_LINE in saved(env)[1])


def overnight(root: Path, env: dict[str, str]) -> None:
    reset(env, ["code"])
    night = {**env, "SCOPE": "hyper", "START_FROM": "coder", "STOP_AT": "coder"}
    result = subprocess.run([str(HERE / "overnight.sh"), "tasks/t.md"], cwd=root, env=night, capture_output=True, text=True, timeout=600)
    expect("overnight-exit", result.returncode, 0)
    summary = sorted((root / ".marestail" / "runs").glob("overnight-*.md"))[-1].read_text()
    expect_true("overnight-summary", "- exit 0" in summary)
    prompts = saved(env)
    expect("overnight-prompts", len(prompts), 1)
    expect_true("overnight-coder", prompts[0].startswith("You are the coder."))
    expect_true("overnight-hyper", "This run is hyper-scoped." in prompts[0] and "--tier full --scope hyper" in prompts[0])


def hard_run(folder: Path) -> None:
    root, env = fixture(folder), environment(folder)
    result = run(root, env, HARD_PLAN, "--scope", "hard", "--focus", "src.py", "--auto", "--retries", "2")
    check_complete("hard", result)
    expect("hard-visited", visited(result.stdout), ALL_ROLES)
    expect_true("hard-no-hyper", not any("This run is hyper-scoped" in prompt for prompt in saved(env)))


def readme_documents() -> None:
    text = (ROOT / "README.md").read_text()
    table = text[text.index("## Pipeline") : text.index("The Gate column")]
    rows = [line.split(" | ") for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    expect("readme-header", rows[0][:4], ["| Step", "Kind", "Gate", "hyper"])
    expect("readme-hyper", [row[3] for row in rows[1:]], ["none", "none", "full", "—", "full", "—", "—", "full", "qa"])
    expect_true("readme-paragraph", "The hyper column is the tier each step runs under `--scope hyper`, and `—` means" in text)


def run_hyper_diagnostic_passes() -> None:
    readme_documents()
    with tempfile.TemporaryDirectory(prefix="marestail-run-hyper-") as temp:
        base = Path(temp)
        root, env = hyper_run(base / "hyper")
        missing_roles(root, env)
        partial_runs(root, env)
        refused_bounce(root, env)
        overnight(root, env)
        bogus_roles(base / "bogus")
        hard_run(base / "hard")


if __name__ == "__main__":
    run_hyper_diagnostic_passes()
    print("run hyper ok")
