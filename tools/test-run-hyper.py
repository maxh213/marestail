#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLI = ROOT / "marestail" / "cli.py"
STUB = HERE / "stub-claude"
HYPER_ROLES = ["specifier", "critic", "coder", "architect", "blast", "hardener", "qa"]
ALL_ROLES = ["specifier", "critic", "coder", "cleaner", "architect", "design", "practices", "perf", "hardener", "qa"]
HYPER_PLAN = ["specify", "judge PASS", "code", "architect", "judge PASS", "judge PASS", "worker qa"]
HARD_PLAN = ["specify", "judge PASS", "code", "worker cleaner", "worker architect", "judge PASS", "worker qa"]
TOML = '[git]\nbase = "main"\n\n[design]\nenabled = false\n\n[practices]\nenabled = false\n\n[perf]\nenabled = false\n'
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
    "Tests must run with a command the repository already supports, in the style its existing tests use; find that out first. "
    "If the repository has a runner, use it. If it has tests but no runner, write the same kind of script. If it has no tests "
    "at all, write dependency-free tests for the language's standard runtime and say so in your handoff. A test that loads "
    "code with vm must pass process into the sandbox, so mutation testing can switch mutants. Write as many as you need.",
    "architect": "Apply the boy scout rule to the code this change touches, and only that code. If the function the fix "
    "lands in is long, split it. If the changed condition is hard to read, give it a name. Do not reshape, move or rename "
    "anything the change does not touch. Leave the dependency contracts as they are unless the change itself adds a "
    "dependency.",
    "hardener": "Judge the changed lines and their tests. Do not ask for clean-up, renames, or coverage of lines that did not change.",
}
FULL_LINE = "Run `marestail gate --tier full --scope hyper` and keep working until it prints GATE PASSED."
UNKNOWN_CLEANER = "unknown role cleaner; choose from " + ", ".join(HYPER_ROLES)
UNKNOWN_BLAST = "unknown role blast; choose from " + ", ".join(ALL_ROLES)
UTIL = "def untouched(value):\n    if value:\n        return 1\n    return 0\n"
HUNKS_SENTENCE = "Add a `## Hunks` section"
HUNKS_INSTRUCTION = (
    "Add a `## Hunks` section to your handoff: one line per hunk outside the tests, `path:start-end — why`, where why is "
    "`the fix needs it` or `boy scout: <what got better> in <the touched function>`."
)
GROW = "Do not bounce for a reason that would grow the diff beyond the fix; if you believe the fix is wrong, bounce to the specifier."
BLAST_FIRST = "You are the blast judge. You judge the diff; you never edit it."
BLAST_PARAGRAPH = (
    "Judge whether this change stays inside the code the fix touches. For each hunk outside the tests, decide whether the fix "
    "needs it, or whether it makes the touched code better: a named condition, a long function split where the fix lands. Both "
    "are welcome. Bounce a hunk in code the fix never needed to enter, a rename or move, a tidy-up next door, a change that "
    "reaches into a module the task did not mention, a change in behaviour the task did not ask for, or a stated reason that "
    "does not hold against the code. Do not bounce for the number of lines. Tests are not limited: do not bounce for the number "
    "or size of tests, only for tests that exercise code the change does not touch in a way that would force later edits there. "
    "Say which hunk and what the smaller change is."
)
NO_MOVES = "under hyper no file may be renamed, moved or deleted"
RENAMED = f"util.py -> helpers.py: renamed or moved; {NO_MOVES}"
DELETED = f"util.py: deleted; {NO_MOVES}"
WHITESPACE = "whitespace or formatting only"
REINDENTED = f"util.py:2-4: {WHITESPACE}; under hyper leave code the fix does not need as it is"
UNLISTED = "not listed under ## Hunks"
HUNK_ROWS = [
    ("code rename", RENAMED, UNLISTED),
    ("code rename", RENAMED, WHITESPACE),
    ("code delete", DELETED, "util.py:0-0"),
    ("code delete", DELETED, UNLISTED),
    ("code reindent", REINDENTED, UNLISTED),
    ("code miss-util", f"util.py:3-3: {UNLISTED}", WHITESPACE),
]
ROUTES = [
    (["architect", "judge PASS", "judge PASS"], ["architect", "blast", "hardener"]),
    (["architect", "judge BOUNCE coder", "code", "judge PASS", "judge PASS"], ["architect", "blast", "coder", "blast", "hardener"]),
    (
        ["architect", "judge BOUNCE architect", "architect", "judge PASS", "judge PASS"],
        ["architect", "blast", "architect", "blast", "hardener"],
    ),
    (
        ["architect", "judge BOUNCE specifier", "specify", "judge PASS", "judge PASS"],
        ["architect", "blast", "specifier", "blast", "hardener"],
    ),
    (["architect", "judge BOUNCE", "code", "judge PASS", "judge PASS"], ["architect", "blast", "coder", "blast", "hardener"]),
]
CLASSES = {
    "package.json": "frozen",
    "package-lock.json": "frozen",
    ".gitignore": "frozen",
    "README.md": "frozen",
    ".github/workflows/ci.yml": "frozen",
    "note.txt": "frozen",
    "lib/extra.py": "source",
    "src/app.ts": "source",
    "tests/test_x.py": "test",
    "src/app.test.ts": "test",
}


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
        "util.py": UTIL,
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
    expect("hyper-prompts", len(prompts), 7)
    expect("hyper-plan", plan_left(env), "")
    for role, prompt in zip(HYPER_ROLES, prompts, strict=True):
        check_hyper_prompt(role, prompt)
    for prompt in prompts[2:4]:
        expect_true("full-tier", FULL_LINE in prompt and "--tier fast" not in prompt and "--tier sonar" not in prompt)
    expect_true("architect-boy-scout", "Do not reshape, move or rename anything the change does not touch" in prompts[3])
    blast_prompt(prompts[4])
    expect("hunks-instruction", [HUNKS_INSTRUCTION in prompt for prompt in prompts], [False, False, True, True, False, False, False])
    expect("hunks-sentence", [HUNKS_SENTENCE in prompt for prompt in prompts], [False, False, True, True, False, False, False])
    expect("grow-sentence", [GROW in prompt for prompt in prompts], [False] * 5 + [True, False])
    return root, env


def part(prompt: str, title: str) -> str:
    body = prompt.split(f"\n\n# {title}\n", 1)[1]
    return re.split(r"\n\n# (?=[A-Z])", body, maxsplit=1)[0]


def has_part(prompt: str, title: str) -> bool:
    return f"\n\n# {title}\n" in prompt


def blast_prompt(prompt: str) -> None:
    expect_true("blast-first", prompt.startswith("You are the blast judge."))
    expect_true("blast-task", "# Add one" in part(prompt, "Task"))
    stat = part(prompt, "Diff stat")
    expect_true("blast-stat", "src.py" in stat and "features/t.feature" not in stat and "qa/t.md" not in stat)
    expect_true("blast-diff", "+def add_one(x):" in part(prompt, "Diff"))
    expect_true("blast-hunks", "src.py:1-2 — the fix needs it" in part(prompt, "Hunks"))
    expect_true("blast-no-gate", not has_part(prompt, "Gate report"))
    expect("blast-scope", scope_section(prompt), "# Scope\n" + HYPER_ALL)


def prepared(source: Path, folder: Path) -> tuple[Path, dict[str, str]]:
    root = folder / "repo"
    shutil.copytree(source, root, symlinks=True)
    return root, environment(folder)


def hyper(root: Path, env: dict[str, str], plan: list[str], *args: str) -> subprocess.CompletedProcess[str]:
    return run(root, env, plan, "--scope", "hyper", "--auto", *args)


def blast_since_start(source: Path, folder: Path) -> None:
    root, env = prepared(source, folder)
    result = hyper(
        root,
        env,
        ["code five-tests", "architect util", "judge PASS", "judge PASS"],
        "--from",
        "coder",
        "--to",
        "hardener",
        "--retries",
        "1",
    )
    check_complete("since-start", result)
    prompt = saved(env)[2]
    diff, stat, listed = part(prompt, "Diff"), part(prompt, "Diff stat"), part(prompt, "Hunks")
    expect_true("since-diff", "+        return 2" in diff and "+def add_one(x):" not in diff)
    expect_true("since-stat", "util.py" in stat and "tests/test_a.py" in stat and "src.py |" not in stat)
    line = "- util.py:3-3 — the fix needs it"
    expect("since-hunks", listed, f"## 01-coder\n{line}\n## 02-architect\n{line}")


def blast_none(source: Path, folder: Path) -> None:
    root, env = prepared(source, folder)
    result = hyper(root, env, ["architect no-hunks", "judge PASS"], "--from", "architect", "--to", "blast", "--retries", "1")
    expect("none-exit", result.returncode, 0)
    prompt = saved(env)[1]
    expect("none-sections", [part(prompt, title) for title in ("Diff stat", "Diff", "Hunks")], ["none", "none", "none"])


def blast_role_file() -> None:
    expect("blast-role", (ROOT / "roles" / "blast.md").read_text().strip(), f"{BLAST_FIRST}\n\n{BLAST_PARAGRAPH}")


def blast_routes(source: Path, folder: Path) -> None:
    for number, (plan, roles) in enumerate(ROUTES):
        root, env = prepared(source, folder / f"route-{number}")
        result = hyper(root, env, plan, "--from", "architect", "--to", "hardener", "--retries", "2")
        check_complete(f"route-{number}", result)
        expect(f"route-{number}-roles", headed(result.stdout), roles)
    root, env = prepared(source, folder / "route-hardener")
    result = hyper(root, env, ["architect", "judge BOUNCE hardener"], "--from", "architect", "--to", "hardener", "--retries", "2")
    expect(
        "route-hardener",
        (headed(result.stdout), "pipeline stopped at blast" in result.stdout, result.returncode),
        (["architect", "blast"], True, 1),
    )


def coder_only(source: Path, folder: Path, plan: list[str], retries: str = "1") -> subprocess.CompletedProcess[str]:
    root, env = prepared(source, folder)
    return hyper(root, env, plan, "--from", "coder", "--to", "coder", "--retries", retries)


def hunk_findings(source: Path, folder: Path) -> None:
    for number, (action, finding, absent) in enumerate(HUNK_ROWS):
        result = coder_only(source, folder / f"hunk-{number}", [action])
        expect_true(f"hunk-{number}-finding", finding in result.stdout)
        expect_true(f"hunk-{number}-absent", absent not in result.stdout)
        expect(f"hunk-{number}-stop", ("pipeline stopped at coder" in result.stdout, result.returncode), (True, 1))


def hunks_pass(source: Path, folder: Path) -> None:
    for action in ("code miss-util bare", "code miss-util single", "code reindent-fix", "code five-tests"):
        result = coder_only(source, folder / action.replace(" ", "-"), [action])
        check_complete(action, result)
        expect_true(f"{action}-listed", UNLISTED not in result.stdout and WHITESPACE not in result.stdout)


def architect_unlisted(source: Path, folder: Path) -> None:
    root, env = prepared(source, folder)
    result = hyper(root, env, ["code five-tests", "architect no-hunks"], "--from", "coder", "--to", "architect", "--retries", "1")
    expect("architect-unlisted-roles", headed(result.stdout), ["coder", "architect"])
    expect_true("architect-unlisted", f"util.py:3-3: {UNLISTED}" in result.stdout)
    expect("architect-unlisted-stop", ("pipeline stopped at architect" in result.stdout, result.returncode), (True, 1))


def git_out(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def package_proposal(source: Path, folder: Path) -> None:
    root, env = prepared(source, folder)
    result = hyper(root, env, ["code package explain", "code"], "--from", "coder", "--to", "coder", "--retries", "2")
    check_complete("package", result)
    expect_true("package-reverted", "package.json: frozen, reverted. Your reason was recorded as" in result.stdout)
    log = git_out(root, "log", "--format=%B").stdout
    expect_true("package-revert-line", re.search(r"Revert change to frozen files by \d+-coder, recorded as a proposal$", log, re.MULTILINE))
    expect_true("package-proposed-line", re.search(r"^Proposed by \d+-coder: package\.json$", log, re.MULTILINE))
    expect("package-gone", git_out(root, "cat-file", "-e", "HEAD:package.json").returncode != 0, True)


def classified(config: Any, path: str) -> str:
    from marestail import freeze

    if freeze.frozen_paths(config, "coder", [path], True):
        return "frozen"
    return "test" if freeze.is_test(path) else "source"


def classes(root: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from marestail import config as config_module

    config = config_module.load(root)
    expect("classes", {path: classified(config, path) for path in CLASSES}, CLASSES)


def extract_passes(source: Path, folder: Path) -> None:
    root, env = prepared(source, folder)
    result = hyper(root, env, ["code", "architect extract"], "--from", "coder", "--to", "architect", "--retries", "1")
    check_complete("extract", result)
    expect("extract-line", git_out(root, "show", "HEAD:src.py").stdout.splitlines()[4], "def increment(x):")


def repeat_limit(source: Path, folder: Path) -> None:
    result = coder_only(source, folder, ["code reindent"] * 3, "5")
    expect_true(
        "repeat-text",
        "coder got the same problems back 3 times in a row; the worker is not making progress, stopping for a human" in result.stdout,
    )
    expect("repeat-exit", result.returncode, 1)


def blast_outside_hyper(folder: Path) -> None:
    root, env = fixture(folder), environment(folder)
    result = run(root, env, ["code"], "--from", "blast", "--auto")
    expect("outside-blast", (result.returncode, UNKNOWN_BLAST in result.stderr, saved(env)), (1, True, []))


def outside_hyper(source: Path, folder: Path) -> None:
    for number, scope in enumerate(([], ["--scope", "changed"], ["--scope", "hard", "--focus", "src.py"])):
        root, env = prepared(source, folder / f"outside-{number}")
        result = run(root, env, ["code no-hunks package reindent"], *scope, "--from", "coder", "--to", "coder", "--auto", "--retries", "1")
        check_complete(f"outside-{number}", result)
        expect(f"outside-{number}-package", git_out(root, "show", "HEAD:package.json").stdout, "{}\n")
        expect_true(f"outside-{number}-quiet", not any(text in result.stdout for text in (UNLISTED, WHITESPACE, "is frozen")))


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
    for start, stop, plan in (("coder", "architect", ["code", "architect"]), ("hardener", "qa", ["judge PASS", "worker qa"])):
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
    expect("hard-no-blast", ("== blast (" in result.stdout, plan_left(env)), (False, ""))
    prompts = saved(env)
    expect_true("hard-no-hyper", not any("This run is hyper-scoped" in prompt for prompt in prompts))
    expect_true("hard-no-hunks", not any(HUNKS_SENTENCE in prompt or GROW in prompt for prompt in prompts))


def readme_documents() -> None:
    text = (ROOT / "README.md").read_text()
    table = text[text.index("## Pipeline") : text.index("The Gate column")]
    rows = [line.split(" | ") for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    expect("readme-header", rows[0][:4], ["| Step", "Kind", "Gate", "hyper"])
    expect(
        "readme-hyper", [row[3] for row in rows[1:]], ["none", "none", "full", "—", "full", "—", "—", "—", "none", "full", "visual", "qa"]
    )
    expect("readme-blast", [row[0] for row in rows[8:11]], ["| perf", "| blast", "| hardener"])
    expect("readme-blast-row", rows[9][:4], ["| blast", "judge", "—", "none"])
    paragraph = text[text.index("The Gate column is") :].split("\n\n", 1)[0]
    for phrase in (
        "that pipeline is specifier, critic, coder, architect, blast, hardener, qa",
        "in the Gate column `—` means the step runs only under hyper",
        "renamed, moved or deleted",
        "whitespace or formatting only",
        "neither a source file nor a test file",
        "missing from `## Hunks`",
    ):
        expect_true(f"readme-{phrase}", phrase in paragraph)
    sentences = re.split(r"(?<=\.) ", paragraph)
    expect("readme-sentences", [sum(word in sentence for sentence in sentences) for word in ("boy scout", "no line budget")], [1, 1])


def run_hyper_diagnostic_passes() -> None:
    readme_documents()
    with tempfile.TemporaryDirectory(prefix="marestail-run-hyper-") as temp:
        base = Path(temp)
        root, env = hyper_run(base / "hyper")
        source = base / "prepared"
        shutil.copytree(root, source, symlinks=True)
        blast_role_file()
        classes(source)
        blast_since_start(source, base / "since")
        blast_none(source, base / "none")
        blast_routes(source, base / "routes")
        hunk_findings(source, base / "hunks")
        hunks_pass(source, base / "pass")
        architect_unlisted(source, base / "architect")
        package_proposal(source, base / "package")
        extract_passes(source, base / "extract")
        repeat_limit(source, base / "repeat")
        outside_hyper(source, base / "outside")
        blast_outside_hyper(base / "outside-blast")
        missing_roles(root, env)
        partial_runs(root, env)
        refused_bounce(root, env)
        overnight(root, env)
        bogus_roles(base / "bogus")
        hard_run(base / "hard")


if __name__ == "__main__":
    run_hyper_diagnostic_passes()
    print("run hyper ok")
